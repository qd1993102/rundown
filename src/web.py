"""Web Chat 路由模块 — 页面 + API 端点。

所有路由通过 FastMCP custom_route 注册，和 MCP Server 共用一个 uvicorn 进程。
依赖：Starlette（FastMCP 内建），不引入 Flask/FastAPI。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .auth import AuthManager, cleanup_expired_mfa_states, get_mfa_state
from .coach import chat_stream
from .config import Config, UserConfig
from .main import _do_daily_sync
from .memory import MemoryStore, build_memory_file
from .storage import Storage
from .users import UserManager

logger = logging.getLogger(__name__)

_COOKIE_NAME = "rundown_key"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 年
_TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"


def _get_api_key(request: Request) -> str | None:
    """从 Cookie 提取 API Key。"""
    return request.cookies.get(_COOKIE_NAME)


def _read_template(name: str) -> str:
    """读取 HTML 模板文件。"""
    path = _TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _user_context(memory_store: MemoryStore, storage: Storage) -> str:
    """构建用户上下文文本（注入 AI 对话）。"""
    parts = []

    # 最新日报
    try:
        latest = memory_store.get_latest("daily_report")
        if latest:
            fm = latest.front_matter or {}
            ya = fm.get("yesterday_activities", {})
            sl = fm.get("last_night_sleep", {})
            mo = fm.get("this_morning", {})
            ld = fm.get("training_load", {})
            rec = fm.get("recovery", {})
            ai = fm.get("ai_insight", {})

            lines = ["## 今日数据"]
            if ya.get("is_rest_day"):
                lines.append("- 昨天: 休息日")
            else:
                lines.append(
                    f"- 昨天训练: {ya.get('total_duration_min', 0)}min "
                    f"{ya.get('total_distance_km', 0):.1f}km "
                    f"负荷 {ya.get('total_training_load', 0)}"
                )
            lines.append(
                f"- 昨晚睡眠: {sl.get('total_hours', '?')}h "
                f"评分 {sl.get('sleep_score', '?')}"
            )
            lines.append(
                f"- 今晨: RHR {mo.get('resting_hr', '?')} | "
                f"HRV {mo.get('hrv_ms', '?')}ms ({mo.get('hrv_status', '?')}) | "
                f"电量 {mo.get('body_battery_morning', '?')}"
            )
            lines.append(
                f"- 负荷: ACWR {ld.get('acwr', '?')} "
                f"({ld.get('acwr_status', '?')}) | "
                f"恢复 {rec.get('overall_score', '?')}/100"
            )
            if ai.get("conclusion"):
                lines.append(f"- AI 评估: {ai['conclusion']}")
            parts.append("\n".join(lines))
    except Exception:
        pass

    # 活跃目标
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if goals:
            goal_lines = ["## 活跃目标"]
            for g in goals[:3]:
                fm = g.front_matter or {}
                goal_lines.append(
                    f"- {fm.get('title', g.id)}"
                    f"（目标日期: {fm.get('target_date', '?')}）"
                )
            parts.append("\n".join(goal_lines))
    except Exception:
        pass

    # 竞技档案
    try:
        profile = memory_store.get("fitness-assessment")
        if profile and profile.body:
            parts.append(f"## 运动员档案\n{profile.body[:800]}")
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "暂无数据，请先同步。"


def _dashboard_data(memory_store: MemoryStore, target_date: date | None = None) -> dict[str, Any]:
    """构建 Dashboard JSON 数据，供前端渲染日报卡片。

    Args:
        memory_store: 记忆存储实例。
        target_date: 指定日期，None 则返回最新日报。
    """
    try:
        if target_date:
            report = memory_store.get(str(target_date))
        else:
            report = memory_store.get_latest("daily_report")

        if report and report.front_matter:
            fm = report.front_matter
            return {
                "has_data": True,
                "report_date": str(fm.get("report_date", "")),
                "yesterday_activities": fm.get("yesterday_activities", {}),
                "last_night_sleep": fm.get("last_night_sleep", {}),
                "this_morning": fm.get("this_morning", {}),
                "training_load": fm.get("training_load", {}),
                "recovery": fm.get("recovery", {}),
                "ai_insight": fm.get("ai_insight", {}),
            }
    except Exception:
        pass
    return {"has_data": False}


# ── 路由注册入口 ────────────────────────────────


def register_web_routes(server, user_manager: UserManager, config: Config):
    """在 FastMCP server 上注册所有 Web 路由。"""

    # ═══ 页面路由 ═══

    @server.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        """首页 — 已绑定用户进聊天，未绑定跳转设置页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user and user.token_status == "active":
            html = _read_template("chat.html")
            return HTMLResponse(html or _chat_fallback())

        return _redirect("/setup")

    @server.custom_route("/setup", methods=["GET"])
    async def setup_page(request: Request) -> Response:
        """Garmin 绑定页面。已绑定用户跳回首页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user and user.token_status == "active":
            return _redirect("/")

        html = _read_template("setup.html")
        return HTMLResponse(html or _setup_fallback())

    @server.custom_route("/reports", methods=["GET"])
    async def reports_page(request: Request) -> Response:
        """日报列表页面。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return _redirect("/setup")

        html = _read_template("reports.html")
        return HTMLResponse(html or _reports_fallback())

    # ═══ API 路由 ═══

    @server.custom_route("/api/setup", methods=["POST"])
    async def api_setup(request: Request) -> Response:
        """启动数据源绑定流程（Garmin / Coros / Huawei）。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        provider = body.get("provider", "garmin").strip()
        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        domain = body.get("domain", "garmin.com").strip()

        # ── Huawei: GROUP_PALS_TOKEN 认证 ──
        if provider == "huawei":
            group_token = body.get("group_pals_token", "").strip()
            if not group_token:
                return JSONResponse({"status": "error", "message": "请输入 GROUP_PALS_TOKEN"}, status_code=400)

            api_key = _get_api_key(request)
            user = user_manager.get(api_key) if api_key else None
            if user is None:
                user = user_manager.register(provider="huawei")
                api_key = user.api_key

            user_cfg = config.for_user(api_key)
            user_manager.ensure_dirs(api_key)

            # 设置 Huawei 专属配置
            user_cfg.group_pals_token = group_token  # type: ignore[attr-defined]
            # 确保 token 目录存在
            token_dir = Path(user_cfg.huawei_token_dir)
            token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

            try:
                from .providers.huawei import HuaweiProvider
                hw = HuaweiProvider(user_cfg)
                hw.authenticate()
                user_manager.update(api_key, provider="huawei", token_status="active")
                resp = JSONResponse({"status": "ok"})
                resp.set_cookie(_COOKIE_NAME, api_key, max_age=_COOKIE_MAX_AGE,
                               httponly=True, samesite="lax")
                return resp
            except Exception as exc:
                logger.error("Huawei 认证失败: %s", exc)
                return JSONResponse({"status": "error", "message": f"Huawei 认证失败: {exc}"}, status_code=400)

        # ── Coros: 邮箱/手机号 + 密码 ──
        if provider == "coros":
            if not email or not password:
                return JSONResponse({"status": "error", "message": "请输入账号和密码"}, status_code=400)

            api_key = _get_api_key(request)
            user = user_manager.get(api_key) if api_key else None
            if user is None:
                user = user_manager.register(garmin_email=email, provider="coros")
                api_key = user.api_key

            user_cfg = config.for_user(api_key)
            user_manager.ensure_dirs(api_key)

            try:
                from .providers.coros import CorosProvider
                # 临时设置 email/password 到 user_cfg
                user_cfg.email = email  # type: ignore[attr-defined]
                user_cfg.password = password  # type: ignore[attr-defined]
                cp = CorosProvider(user_cfg)
                if cp.authenticate():
                    user_manager.update(api_key, garmin_email=email,
                                       provider="coros", token_status="active")
                    resp = JSONResponse({"status": "ok"})
                    resp.set_cookie(_COOKIE_NAME, api_key, max_age=_COOKIE_MAX_AGE,
                                   httponly=True, samesite="lax")
                    return resp
                else:
                    return JSONResponse({"status": "error", "message": "Coros 登录失败，请检查账号密码"}, status_code=400)
            except Exception as exc:
                logger.error("Coros 登录失败: %s", exc)
                return JSONResponse({"status": "error", "message": f"登录失败: {exc}"}, status_code=400)

        # ── Garmin: 邮箱 + 密码 + 可选 MFA ──
        if not email or not password:
            return JSONResponse({"status": "error", "message": "请输入邮箱和密码"}, status_code=400)

        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user is None:
            user = user_manager.register(garmin_email=email, garmin_domain=domain, provider="garmin")
            api_key = user.api_key

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        user_cfg.email = email
        user_cfg._domain = domain

        auth = AuthManager(user_cfg)

        try:
            session_key = secrets.token_hex(16)
            result = auth.start_login(email, password, session_key)

            if result == "needs_mfa":
                resp = JSONResponse({"status": "needs_mfa", "session": session_key})
            else:
                user_manager.update(api_key, garmin_email=email,
                                    garmin_domain=domain, token_status="active")
                resp = JSONResponse({"status": "ok"})

            resp.set_cookie(_COOKIE_NAME, api_key, max_age=_COOKIE_MAX_AGE,
                           httponly=True, samesite="lax")
            return resp

        except Exception as exc:
            logger.error("Garmin 登录失败: %s", exc)
            return JSONResponse({"status": "error", "message": f"登录失败: {exc}"}, status_code=400)

    @server.custom_route("/api/mfa", methods=["POST"])
    async def api_mfa(request: Request) -> Response:
        """提交 MFA 验证码完成登录。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        mfa_code = body.get("code", "").strip()
        session_key = body.get("session", "").strip()

        if not mfa_code or not session_key:
            return JSONResponse({"status": "error", "message": "缺少验证码或会话"}, status_code=400)

        api_key = _get_api_key(request)
        if not api_key:
            return JSONResponse({"status": "error", "message": "请先发起绑定"}, status_code=400)

        user_cfg = config.for_user(api_key)
        auth = AuthManager(user_cfg)

        try:
            auth.complete_mfa(session_key, mfa_code)
            user_manager.update(api_key, token_status="active")
            return JSONResponse({"status": "ok"})
        except ValueError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        except Exception as exc:
            logger.error("MFA 验证失败: %s", exc)
            return JSONResponse({"status": "error", "message": f"验证失败: {exc}"}, status_code=400)

    @server.custom_route("/api/chat/stream", methods=["POST"])
    async def api_chat_stream(request: Request) -> Response:
        """流式 AI 对话（SSE）。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定 Garmin"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        messages = body.get("messages", [])

        # 构建用户上下文
        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        memory_store = MemoryStore(user_cfg.memory_dir,
                                   db_getter=lambda: storage.db)
        context = _user_context(memory_store, storage)

        async def generate():
            async for token in chat_stream(messages, context=context):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @server.custom_route("/api/sync", methods=["POST"])
    async def api_sync(request: Request) -> Response:
        """触发数据同步 + 生成日报（直接复用 CLI 的 _do_daily_sync）。

        Request body (JSON):
            date: 可选，YYYY-MM-DD，不传则默认今天。
        """
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        # 注入用户凭证供 _do_daily_sync 使用
        user_cfg.email = user.garmin_email
        user_cfg._domain = user.garmin_domain

        # 解析目标日期
        target = date.today()
        try:
            body = await request.json()
            date_str = body.get("date", "").strip() if body else ""
            if date_str:
                target = date.fromisoformat(date_str)
        except Exception:
            pass  # GET 请求或无 body 则默认今天

        try:
            mem, _provider, storage, _ms, _uid = _do_daily_sync(
                config=user_cfg, target=target,
                sync_days=config.sync_days, quiet=True,
            )

            # 备份数据库
            storage.backup_to(user_manager.get_backup_path(api_key))
            user_manager.update(api_key, last_sync=str(date.today()))

            return JSONResponse({
                "status": "ok",
                "message": f"同步完成，已生成 {target} 日报",
                "date": str(target),
            })

        except Exception as exc:
            logger.error("同步失败: %s", exc)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @server.custom_route("/api/status", methods=["GET"])
    async def api_status(request: Request) -> Response:
        """查询用户状态。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if not user:
            return JSONResponse({"bound": False})

        return JSONResponse({
            "bound": True,
            "token_status": user.token_status,
            "garmin_email": user.garmin_email,
            "last_sync": user.last_sync,
        })

    @server.custom_route("/api/logout", methods=["POST"])
    async def api_logout(request: Request) -> Response:
        """退出登录。"""
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(_COOKIE_NAME)
        return resp

    @server.custom_route("/api/dashboard", methods=["GET"])
    async def api_dashboard(request: Request) -> Response:
        """获取 Dashboard 数据（日报摘要 JSON）。

        Query params:
            date: 可选，YYYY-MM-DD 格式，不传则返回最新日报。
        """
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        memory_store = MemoryStore(user_cfg.memory_dir,
                                   db_getter=lambda: storage.db)

        # 支持 ?date=YYYY-MM-DD 查看历史日报
        target_date = None
        date_param = request.query_params.get("date", "").strip()
        if date_param:
            try:
                target_date = date.fromisoformat(date_param)
            except ValueError:
                pass

        data = _dashboard_data(memory_store, target_date)
        return JSONResponse(data)

    @server.custom_route("/api/reports", methods=["GET"])
    async def api_reports(request: Request) -> Response:
        """获取所有日报摘要列表（按日期倒序）。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        memory_store = MemoryStore(user_cfg.memory_dir,
                                   db_getter=lambda: storage.db)

        reports = memory_store.list_by_type("daily_report")
        summaries = []
        for mem in reports:
            fm = mem.front_matter
            ya = fm.get("yesterday_activities", {})
            sl = fm.get("last_night_sleep", {})
            rc = fm.get("recovery", {})
            ai = fm.get("ai_insight", {})

            # 训练摘要
            train_parts = []
            if ya.get("is_rest_day"):
                train_parts.append("🧘 休息日")
            else:
                sessions = ya.get("sessions", [])
                if sessions:
                    types = list(dict.fromkeys(s.get("type", "") for s in sessions if s.get("type")))
                    train_parts.append(", ".join(types))
                dur = ya.get("total_duration_min", 0)
                dist = ya.get("total_distance_km", 0)
                if dur:
                    train_parts.append(f"{dur}min")
                if dist:
                    train_parts.append(f"{dist:.1f}km")

            report_date = str(fm.get("report_date") or mem.id)
            summaries.append({
                "id": mem.id,
                "date": report_date,
                "training": " · ".join(train_parts) or "—",
                "sleep_score": sl.get("sleep_score"),
                "recovery_score": rc.get("overall_score"),
                "ai_conclusion": ai.get("conclusion", "")[:80] if ai.get("conclusion") else "",
            })

        summaries.sort(key=lambda x: x["date"], reverse=True)
        return JSONResponse(summaries)

    @server.custom_route("/api/profile", methods=["POST"])
    async def api_profile(request: Request) -> Response:
        """保存个人资料与最佳成绩。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        memory_store = MemoryStore(user_cfg.memory_dir,
                                   db_getter=lambda: storage.db)

        now = datetime.now().isoformat(timespec="seconds")
        today = str(date.today())

        # Build profile front matter
        fm: dict[str, Any] = {
            "type": "fitness_profile",
            "profile_type": "assessment",
            "updated": now,
            "personal_info": {
                "height_cm": _int_or_none(body.get("height_cm")),
                "weight_kg": _int_or_none(body.get("weight_kg")),
                "age": _int_or_none(body.get("age")),
                "gender": body.get("gender", "male"),
                "location": body.get("location", ""),
            },
            "personal_bests": {},
            "tags": ["fitness-profile", today.split("-")[0]],
        }

        pbs = body.get("pbs", {})
        for dist_key, dist_label in [("5k", "5k"), ("10k", "10k"),
                                       ("half_marathon", "half_marathon"),
                                       ("marathon", "marathon")]:
            if pbs.get(dist_key):
                fm["personal_bests"][dist_label] = {"time": pbs[dist_key]}
        if pbs.get("vo2max"):
            try:
                fm["personal_bests"]["vo2max_estimate"] = float(pbs["vo2max"])
            except (TypeError, ValueError):
                pass

        # Build markdown body
        pi = fm["personal_info"]
        body_lines = [
            "# 竞技档案",
            "",
            "## 基本信息",
            f"- 身高: {pi['height_cm'] or '—'} cm",
            f"- 体重: {pi['weight_kg'] or '—'} kg",
            f"- 年龄: {pi['age'] or '—'}",
            f"- 性别: {pi['gender']}",
            f"- 地点: {pi['location'] or '未设置'}",
            "",
            "## 个人最佳",
        ]
        for dist, data in fm["personal_bests"].items():
            if isinstance(data, dict) and "time" in data:
                body_lines.append(f"- **{dist}**: {data['time']}")

        profile_path = Path(user_cfg.memory_dir) / "profile" / "fitness-assessment.md"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(build_memory_file(fm, "\n".join(body_lines)), encoding="utf-8")

        return JSONResponse({"status": "ok"})

    @server.custom_route("/api/goals", methods=["POST"])
    async def api_goals(request: Request) -> Response:
        """保存训练目标。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        goal_name = body.get("name", "").strip()
        if not goal_name:
            return JSONResponse({"status": "error", "message": "目标名称不能为空"}, status_code=400)

        goal_dist = body.get("distance", "marathon")
        goal_id = f"goal-{date.today().year}-{goal_dist}"

        fm: dict[str, Any] = {
            "type": "goal",
            "id": goal_id,
            "goal_type": "time_based",
            "category": "running",
            "status": "active",
            "priority": "high",
            "created": str(date.today()),
            "target_date": body.get("target_date") or str(date.today().replace(year=date.today().year + 1)),
            "review_cycle": "weekly",
            "metrics": {
                f"target_{goal_dist}": body.get("target_time") or "",
                "weekly_mileage_km": _int_or_none(body.get("weekly_km")) or 50,
            },
            "tags": [goal_dist, str(date.today().year), "active"],
        }

        goal_body = f"""# {goal_name}

## 目标
- 距离: {goal_dist}
- 目标成绩: {body.get('target_time') or '—'}
- 截止日期: {fm['target_date']}
- 周跑量目标: {fm['metrics']['weekly_mileage_km']} km

## 进度
创建于 {date.today()}，定期更新。
"""
        goal_path = Path(user_cfg.memory_dir) / "goals" / "active" / f"{goal_id}.md"
        goal_path.parent.mkdir(parents=True, exist_ok=True)
        goal_path.write_text(build_memory_file(fm, goal_body), encoding="utf-8")

        return JSONResponse({"status": "ok"})

    @server.custom_route("/api/preferences", methods=["POST"])
    async def api_preferences(request: Request) -> Response:
        """保存训练偏好。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        workouts_raw = body.get("preferred_workouts", "间歇,节奏,长距离")
        preferred_workouts = ([w.strip() for w in workouts_raw.split(",")]
                              if isinstance(workouts_raw, str) else workouts_raw)
        terrain_raw = body.get("preferred_terrain", "公路")
        preferred_terrain = [terrain_raw] if isinstance(terrain_raw, str) else terrain_raw

        fm: dict[str, Any] = {
            "type": "coaching_preference",
            "updated": datetime.now().isoformat(timespec="seconds"),
            "training_preferences": {
                "preferred_workouts": preferred_workouts,
                "preferred_time": body.get("preferred_time", "evening"),
                "preferred_terrain": preferred_terrain,
            },
            "injury_history": [],
            "training_philosophy": body.get("training_philosophy", "数据驱动，极化训练，重视恢复"),
            "tags": ["preferences", "coaching-style"],
        }

        injury = body.get("injury_history", "").strip()
        if injury:
            fm["injury_history"].append({
                "description": injury,
                "date": str(date.today()),
            })

        pref_body = f"""# 训练偏好

## 训练习惯
- 偏好时间: {fm['training_preferences']['preferred_time']}
- 偏好类型: {', '.join(fm['training_preferences']['preferred_workouts'])}
- 偏好地形: {', '.join(fm['training_preferences']['preferred_terrain'])}

## 训练哲学
{fm['training_philosophy']}

## 伤病史
{injury or '无'}
"""
        pref_path = Path(user_cfg.memory_dir) / "coaching" / "preferences.md"
        pref_path.parent.mkdir(parents=True, exist_ok=True)
        pref_path.write_text(build_memory_file(fm, pref_body), encoding="utf-8")

        return JSONResponse({"status": "ok"})


# ── 辅助 ────────────────────────────────────────


def _redirect(url: str) -> Response:
    return Response(status_code=302, headers={"Location": url})


def _int_or_none(value: Any) -> int | None:
    """安全转整数，失败返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── 内联 HTML 兜底（模板文件不存在时使用）─────────


def _setup_fallback() -> str:
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rundown — 绑定 Garmin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f0f0f;color:#e0e0e0;
  display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px}
.card{background:#1a1a1a;border-radius:16px;padding:32px 24px;max-width:400px;width:100%}
h1{font-size:20px;margin-bottom:8px;color:#fff}
p.sub{font-size:14px;color:#888;margin-bottom:24px}
label{display:block;font-size:13px;color:#aaa;margin-bottom:4px;margin-top:16px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #333;background:#0f0f0f;
  color:#fff;font-size:16px;outline:none}
input:focus{border-color:#4caf50}
button{width:100%;padding:14px;border-radius:10px;border:none;background:#4caf50;color:#fff;
  font-size:16px;font-weight:600;margin-top:24px;cursor:pointer}
button:disabled{opacity:.5}
#mfa-section{display:none}
#error{color:#f44336;font-size:14px;margin-top:12px;display:none}
#success{color:#4caf50;font-size:14px;margin-top:12px;display:none}
</style>
</head>
<body>
<div class="card">
<h1>🔗 绑定 Garmin</h1>
<p class="sub">连接你的 Garmin 账号，AI 教练需要你的运动数据</p>
<div id="setup-section">
  <label>Garmin 邮箱</label>
  <input id="email" type="email" placeholder="your@email.com" autocomplete="email">
  <label>Garmin 密码</label>
  <input id="password" type="password" placeholder="Garmin 密码" autocomplete="current-password">
  <button id="setup-btn" onclick="startSetup()">开始绑定</button>
</div>
<div id="mfa-section">
  <p>请输入 Garmin 发送的二次验证码</p>
  <label>MFA 验证码</label>
  <input id="mfa-code" type="text" placeholder="6 位数字" inputmode="numeric">
  <button id="mfa-btn" onclick="submitMfa()">验证</button>
</div>
<div id="error"></div>
<div id="success"></div>
</div>
<script>
let mfaSession = '';
function showError(msg){const e=document.getElementById('error');e.textContent=msg;e.style.display='block'}
function hideError(){document.getElementById('error').style.display='none'}
async function startSetup(){
  hideError();
  const email=document.getElementById('email').value.trim();
  const password=document.getElementById('password').value.trim();
  if(!email||!password){showError('请输入邮箱和密码');return}
  const btn=document.getElementById('setup-btn');btn.disabled=true;btn.textContent='登录中...';
  try{
    const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email,password})});
    const d=await r.json();
    if(d.status==='needs_mfa'){
      mfaSession=d.session;
      document.getElementById('setup-section').style.display='none';
      document.getElementById('mfa-section').style.display='block';
    }else if(d.status==='ok'){
      document.getElementById('success').textContent='绑定成功！即将跳转...';
      document.getElementById('success').style.display='block';
      setTimeout(()=>location.href='/',1500);
    }else{
      showError(d.message||'绑定失败');
    }
  }catch(e){showError('网络错误: '+e.message)}
  btn.disabled=false;btn.textContent='开始绑定';
}
async function submitMfa(){
  hideError();
  const code=document.getElementById('mfa-code').value.trim();
  if(!code){showError('请输入验证码');return}
  const btn=document.getElementById('mfa-btn');btn.disabled=true;btn.textContent='验证中...';
  try{
    const r=await fetch('/api/mfa',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,session:mfaSession})});
    const d=await r.json();
    if(d.status==='ok'){
      document.getElementById('success').textContent='绑定成功！即将跳转...';
      document.getElementById('success').style.display='block';
      setTimeout(()=>location.href='/',1500);
    }else{
      showError(d.message||'MFA 验证失败');
    }
  }catch(e){showError('网络错误: '+e.message)}
  btn.disabled=false;btn.textContent='验证';
}
</script>
</body>
</html>"""


def _chat_fallback() -> str:
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rundown Coach</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f0f0f;color:#e0e0e0;
  display:flex;flex-direction:column;height:100vh}
header{background:#1a1a1a;padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
h1{font-size:18px;color:#4caf50}
#msg-list{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.msg{padding:12px 16px;border-radius:12px;max-width:85%;line-height:1.6;font-size:15px}
.msg.user{align-self:flex-end;background:#4caf50;color:#fff}
.msg.assistant{align-self:flex-start;background:#1a1a1a}
.msg .time{font-size:11px;opacity:.5;margin-top:4px}
#input-row{display:flex;padding:12px 16px;gap:8px;background:#1a1a1a}
#input-row input{flex:1;padding:12px;border-radius:10px;border:1px solid #333;
  background:#0f0f0f;color:#fff;font-size:16px;outline:none}
#input-row input:focus{border-color:#4caf50}
#input-row button{padding:12px 20px;border-radius:10px;border:none;background:#4caf50;
  color:#fff;font-weight:600;cursor:pointer;font-size:15px}
#input-row button:disabled{opacity:.5}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #4caf50;
  border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <h1>🏃 Rundown Coach</h1>
  <span style="font-size:12px;color:#888">AI 跑步教练</span>
</header>
<div id="msg-list"></div>
<div id="input-row">
  <input id="user-input" type="text" placeholder="输入消息..." autocomplete="off">
  <button id="send-btn" onclick="send()">发送</button>
</div>
<script>
const msgList=document.getElementById('msg-list');
const input=document.getElementById('user-input');
const messages=[];
function addMsg(role,text){
  const div=document.createElement('div');
  div.className='msg '+role;
  const now=new Date();
  div.innerHTML=text+'<div class="time">'+now.toLocaleTimeString('zh',{hour:'2-digit',minute:'2-digit'})+'</div>';
  msgList.appendChild(div);
  msgList.scrollTop=msgList.scrollHeight;
  return div;
}
async function send(){
  const text=input.value.trim();
  if(!text)return;
  input.value='';input.disabled=true;
  document.getElementById('send-btn').disabled=true;
  messages.push({role:'user',content:text});
  addMsg('user',text);
  const aiDiv=addMsg('assistant','<span class="spinner"></span>');
  try{
    const r=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({messages})});
    const reader=r.body.getReader();
    const decoder=new TextDecoder();
    let full='';
    aiDiv.innerHTML='';
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      const chunk=decoder.decode(value,{stream:true});
      for(const line of chunk.split('\\n')){
        if(line.startsWith('data: ')){
          const d=line.slice(6);
          if(d==='[DONE]')continue;
          try{const{j}=JSON.parse(d);full+=j.token||'';aiDiv.innerHTML=full.replace(/\\n/g,'<br>')}catch(e){}
        }
      }
      msgList.scrollTop=msgList.scrollHeight;
    }
    messages.push({role:'assistant',content:full});
  }catch(e){
    aiDiv.innerHTML='错误: '+e.message;
  }
  input.disabled=false;
  document.getElementById('send-btn').disabled=false;
  input.focus();
}
input.addEventListener('keydown',e=>{if(e.key==='Enter')send()});
</script>
</body>
</html>"""


# ── 启动入口 ────────────────────────────────────


def create_web_server(config: Config, user_manager: UserManager):
    """创建带了 Web 路由的 FastMCP server，由 cmd_serve 调用。"""
    from .mcp_server import create_server

    # 用占位 provider（Web 模式下每个用户独立认证）
    from .providers.garmin import GarminProvider
    provider = GarminProvider(config)

    storage = Storage(config)
    memory_store = MemoryStore(config.memory_dir, db_getter=lambda: storage.db)

    server = create_server(config, provider, storage, memory_store, 0)
    register_web_routes(server, user_manager, config)

    return server
