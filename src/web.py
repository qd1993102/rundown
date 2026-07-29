"""Web Chat 路由模块 — 页面 + API 端点。

所有路由通过 FastMCP custom_route 注册，和 MCP Server 共用一个 uvicorn 进程。
依赖：Starlette（FastMCP 内建），不引入 Flask/FastAPI。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from .auth import AuthManager, cleanup_expired_mfa_states, get_mfa_state
from .coach import chat_stream
from .config import Config, UserConfig
from .invitations import InvitationError, InvitationStore
from .local_files import LocalPersistenceError, atomic_write_private, ensure_private_dir
from .main import ProviderAuthenticationError, _do_daily_sync, _do_data_sync
from .memory import Memory, MemoryStore, build_memory_file
from .resource_lifecycle import close_runtime_resources
from .storage import Storage
from .sync_coordinator import (
    SyncCapacityExceededError,
    SyncCoordinator,
    SyncInProgressError,
)
from .sync_tasks import ACTIVE_STATUSES, SyncTaskStore
from .users import UserExistsError, UserManager, UserRecord

logger = logging.getLogger(__name__)

_COOKIE_NAME = "neurun_key"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 固定 30 天
_TEMPLATE_DIR = Path(__file__).parent.parent / "web" / "templates"
_ASSET_DIR = Path(__file__).parent.parent / "web" / "assets"
_CONTACT_QR_FILENAME = "contact-wechat.jpg"
_CONTACT_WIDGET_MARKER = 'data-neurun-contact-widget=""'
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
_INVITE_UNAVAILABLE = "邀请码无效、已停用或已经使用"
_ALIYUN_ARMS_RUM_SDK = "https://sdk.rum.aliyuncs.com/v2/browser-sdk.js"
_ALIYUN_ARMS_RUM_ENDPOINT = (
    "https://proj-xtrace-331c87d116484cdd1fe18f4f5e917845-cn-hangzhou."
    "cn-hangzhou.log.aliyuncs.com/rum/web/v2?"
    "workspace=default-cms-1561822425896437-cn-hangzhou&"
    "service_id=fdmbbfbh11@80570cae83e7211869362"
)


@dataclass(frozen=True)
class SyncRequest:
    """标准化后的 Web 同步请求。"""

    mode: str
    target: date
    start: date
    end: date
    sync_days: int
    full: bool
    force: bool


def _get_api_key(request: Request) -> str | None:
    """从 Cookie 提取 API Key。"""
    return request.cookies.get(_COOKIE_NAME)


def _read_template(name: str) -> str:
    """读取 HTML 模板文件。"""
    path = _TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _rum_account_name(user: UserRecord) -> str:
    """生成可归因但不泄露账号凭证或个人信息的 RUM 账户名。"""
    digest = hashlib.sha256(user.api_key.encode("utf-8")).hexdigest()[:24]
    return f"account_{digest}"


def _arms_rum_script(user: UserRecord | None = None) -> str:
    """构建阿里云 ARMS Browser RUM v2 引导脚本。"""
    user_config = ""
    if user is not None:
        user_config = (
            f"\n      user: {{ name: {json.dumps(_rum_account_name(user))} }},"
        )
    return f"""<script>
    !(function(c,b,d,a){{c[a]||(c[a]={{}});c[a]=
    {{
      endpoint: '{_ALIYUN_ARMS_RUM_ENDPOINT}',
      env: 'prod',
      spaMode: 'history',{user_config}
      collectors: {{
        perf: true,
        webVitals: true,
        api: true,
        staticResource: true,
        jsError: true,
        consoleError: true,
        action: true,
      }},
      tracing: false,
    }}
    with(b)with(body)with(insertBefore(createElement("script"),firstChild))setAttribute("crossorigin","",src=d)
}})(window, document, "{_ALIYUN_ARMS_RUM_SDK}", "__rum");
</script>"""


def _contact_widget() -> str:
    """构建登录后页面统一使用的联系入口。"""
    return """<style data-neurun-contact-widget-style>
.neurun-contact{position:fixed;z-index:70;right:12px;bottom:calc(92px + env(safe-area-inset-bottom));font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,sans-serif;color:var(--text,#17211b)}
.neurun-contact *{box-sizing:border-box}
.neurun-contact [hidden]{display:none!important}
.neurun-contact-trigger{display:flex;align-items:center;justify-content:center;gap:7px;min-height:44px;padding:9px 14px;border:1px solid color-mix(in srgb,var(--accent,#2d9d6f) 78%,#fff);border-radius:999px;background:var(--accent,#2d9d6f);color:#fff;box-shadow:0 10px 30px rgba(0,0,0,.2);font:inherit;font-size:13px;font-weight:750;line-height:1;cursor:pointer;-webkit-tap-highlight-color:transparent;touch-action:manipulation;transition:transform .18s ease,box-shadow .18s ease,opacity .18s ease}
.neurun-contact-trigger svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.neurun-contact-trigger:focus-visible,.neurun-contact-close:focus-visible,.neurun-contact-save:focus-visible{outline:3px solid color-mix(in srgb,var(--accent,#2d9d6f) 34%,transparent);outline-offset:3px}
.neurun-contact-panel{position:absolute;right:0;bottom:calc(100% + 10px);width:min(320px,calc(100vw - 24px));max-height:calc(100vh - 160px - env(safe-area-inset-bottom));overflow:auto;padding:18px;border:1px solid var(--border-subtle,#dbe5df);border-radius:20px;background:var(--bg-card,#fff);color:var(--text,#17211b);box-shadow:0 18px 54px rgba(0,0,0,.24);overscroll-behavior:contain}
.neurun-contact-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}
.neurun-contact-eyebrow{margin:0 0 3px;color:var(--accent,#2d9d6f);font-size:10px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase}
.neurun-contact-title{margin:0;font-size:17px;line-height:1.35;letter-spacing:-.2px}
.neurun-contact-close{display:grid;place-items:center;flex:0 0 36px;width:36px;height:36px;margin:-7px -7px 0 0;padding:0;border:0;border-radius:50%;background:transparent;color:var(--text-muted,#718078);font:inherit;font-size:24px;line-height:1;cursor:pointer}
.neurun-contact-qr{display:block;width:100%;height:auto;aspect-ratio:1;border:1px solid var(--border-subtle,#dbe5df);border-radius:14px;background:#fff;object-fit:contain}
.neurun-contact-help{margin:11px 0 0;color:var(--text-secondary,#53665c);font-size:12px;line-height:1.6}
.neurun-contact-status{margin:12px 0;padding:18px 12px;border-radius:12px;background:var(--bg-subtle,#f3f6f4);color:var(--text-secondary,#53665c);font-size:13px;text-align:center}
.neurun-contact-save{display:flex;align-items:center;justify-content:center;min-height:44px;margin-top:12px;padding:9px 14px;border:1px solid var(--accent,#2d9d6f);border-radius:12px;background:transparent;color:var(--accent,#2d9d6f);font-size:13px;font-weight:750;text-decoration:none}
@media(min-width:641px){.neurun-contact{right:24px;bottom:24px}.neurun-contact-panel{max-height:calc(100vh - 92px)}}
@media(hover:hover) and (pointer:fine){.neurun-contact-trigger:hover{transform:translateY(-2px);box-shadow:0 14px 38px rgba(0,0,0,.24)}.neurun-contact-close:hover{background:var(--bg-subtle,#f3f6f4);color:var(--text,#17211b)}.neurun-contact-save:hover{background:var(--accent,#2d9d6f);color:#fff}}
@media(prefers-reduced-motion:reduce){.neurun-contact-trigger{transition:none}}
</style>
<aside class="neurun-contact" data-neurun-contact-widget="">
  <button class="neurun-contact-trigger" type="button" aria-expanded="false" aria-controls="neurunContactPanel" aria-haspopup="dialog">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 9.6 9.6 0 0 1-4-.9L3 21l1.8-4.6A8.2 8.2 0 0 1 3 11.5a8.4 8.4 0 0 1 9-8.5 8.4 8.4 0 0 1 9 8.5Z"/><path d="M8 11.5h.01M12 11.5h.01M16 11.5h.01"/></svg>
    <span>联系我们</span>
  </button>
  <section class="neurun-contact-panel" id="neurunContactPanel" role="dialog" aria-modal="false" aria-labelledby="neurunContactTitle" hidden>
    <div class="neurun-contact-head">
      <div><p class="neurun-contact-eyebrow">WeChat Community</p><h2 class="neurun-contact-title" id="neurunContactTitle">加入 NeuRun 内测交流群</h2></div>
      <button class="neurun-contact-close" type="button" aria-label="关闭联系我们">×</button>
    </div>
    <img class="neurun-contact-qr" src="/contact/qr" alt="NeuRun 内测交流群二维码" decoding="async" draggable="false">
    <p class="neurun-contact-status" role="status" hidden>二维码暂不可用，请稍后重试</p>
    <p class="neurun-contact-help">电脑端可直接使用微信扫码；手机端请长按或保存图片，再从微信“扫一扫”的相册中识别。二维码会定期更新，识别失败时请刷新页面。</p>
    <a class="neurun-contact-save" href="/contact/qr" download="neurun-wechat-group.jpg">保存二维码</a>
  </section>
</aside>
<script data-neurun-contact-widget-script>
(() => {
  const root = document.querySelector('[data-neurun-contact-widget]');
  if (!root || root.dataset.ready === 'true') return;
  root.dataset.ready = 'true';
  const trigger = root.querySelector('.neurun-contact-trigger');
  const panel = root.querySelector('.neurun-contact-panel');
  const close = root.querySelector('.neurun-contact-close');
  const image = root.querySelector('.neurun-contact-qr');
  const status = root.querySelector('.neurun-contact-status');
  const save = root.querySelector('.neurun-contact-save');
  const finePointer = window.matchMedia('(hover:hover) and (pointer:fine)');
  let preview = false;
  let pinned = false;
  let suppressFocusPreview = false;

  function render() {
    const open = preview || pinned;
    panel.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
  }

  function dismiss(returnFocus = false) {
    preview = false;
    pinned = false;
    render();
    if (returnFocus) {
      suppressFocusPreview = true;
      trigger.focus({ preventScroll: true });
      window.setTimeout(() => { suppressFocusPreview = false; }, 0);
    }
  }

  trigger.addEventListener('click', () => {
    pinned = !pinned;
    preview = false;
    render();
  });
  close.addEventListener('click', () => dismiss(true));
  root.addEventListener('pointerenter', () => {
    if (finePointer.matches) { preview = true; render(); }
  });
  root.addEventListener('pointerleave', () => {
    if (finePointer.matches) { preview = false; render(); }
  });
  root.addEventListener('focusin', () => {
    if (!suppressFocusPreview) { preview = true; render(); }
  });
  root.addEventListener('focusout', () => {
    window.setTimeout(() => {
      if (!root.contains(document.activeElement)) { preview = false; render(); }
    }, 0);
  });
  document.addEventListener('pointerdown', event => {
    if (!root.contains(event.target)) dismiss(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) dismiss(true);
  });
  image.addEventListener('error', () => {
    image.hidden = true;
    save.hidden = true;
    status.hidden = false;
  });
  image.addEventListener('load', () => {
    image.hidden = false;
    save.hidden = false;
    status.hidden = true;
  });
})();
</script>"""


def _contact_qr_path(config: Config) -> Path | None:
    """返回持久化覆盖图片或随版本发布的默认二维码。"""
    candidates = (
        Path(config.data_dir) / _CONTACT_QR_FILENAME,
        _ASSET_DIR / _CONTACT_QR_FILENAME,
    )
    return next((path for path in candidates if path.is_file()), None)


def _html_response(html: str, user: UserRecord | None = None) -> HTMLResponse:
    """返回统一注入联系入口与 ARMS RUM 的 HTML 页面响应。"""
    injections = []
    if user is not None and _CONTACT_WIDGET_MARKER not in html:
        injections.append(_contact_widget())
    if _ALIYUN_ARMS_RUM_SDK not in html:
        injections.append(_arms_rum_script(user))
    if not injections:
        return HTMLResponse(html)
    injected = "\n".join(injections)
    body_end = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    if body_end is None:
        logger.warning("HTML 页面缺少 </body>，平台注入内容追加到文末")
        return HTMLResponse(f"{html}\n{injected}")
    monitored_html = f"{html[:body_end.start()]}{injected}\n{html[body_end.start():]}"
    return HTMLResponse(monitored_html)


def _registration_error(nickname: str, email: str, password: str) -> str | None:
    """校验应用账号注册字段，返回面向用户的错误信息。"""
    if not 2 <= len(nickname.strip()) <= 32:
        return "昵称长度须为 2–32 个字符"
    if not _EMAIL_PATTERN.fullmatch(email.strip()):
        return "请输入有效的邮箱地址"
    if len(password) < 8:
        return "密码至少需要 8 个字符"
    if len(password) > 128:
        return "密码不能超过 128 个字符"
    return None


def _calendar_month_range(value: str | None) -> tuple[str, date, date]:
    """解析 YYYY-MM，并返回规范月份及包含首尾的日期范围。"""
    month = value or date.today().strftime("%Y-%m")
    if not _MONTH_PATTERN.fullmatch(month):
        raise ValueError("月份格式必须为 YYYY-MM")
    try:
        start = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise ValueError("月份格式必须为有效的 YYYY-MM") from exc
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return month, start, next_month - timedelta(days=1)


def _user_page(user: UserRecord | None) -> str:
    """返回当前会话应进入的页面。"""
    if user is None:
        return "/login"
    return "/" if user.token_status == "active" else "/setup"


def _set_session_cookie(response: Response, request: Request, api_key: str) -> None:
    """写入 HttpOnly 应用会话 Cookie。"""
    response.set_cookie(
        _COOKIE_NAME,
        api_key,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _parse_sync_request(body: dict[str, Any], default_days: int) -> SyncRequest:
    """解析单日、批量及旧版 Web 同步参数。"""
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON object")

    mode = str(body.get("mode") or "legacy").strip().lower()
    force = bool(body.get("force"))

    def parse_date(value: Any, field_name: str, *, fallback: date | None = None) -> date:
        raw = str(value or "").strip()
        if not raw:
            if fallback is not None:
                return fallback
            raise ValueError(f"缺少 {field_name}，格式应为 YYYY-MM-DD")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field_name} 格式无效，应为 YYYY-MM-DD") from exc

    if mode == "single":
        target = parse_date(body.get("date"), "date", fallback=date.today())
        return SyncRequest("single", target, target, target, 0, False, force)

    if mode == "batch":
        start = parse_date(body.get("from_date"), "from_date")
        end = parse_date(body.get("to_date"), "to_date")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        sync_days = (end - start).days
        if sync_days > 365 * 3:
            raise ValueError("批量同步范围不能超过 3 年")
        return SyncRequest("batch", end, start, end, sync_days, False, force)

    if mode != "legacy":
        raise ValueError("mode 必须是 single 或 batch")

    target = parse_date(body.get("date"), "date", fallback=date.today())
    full = bool(body.get("full"))
    try:
        sync_days = int(body.get("sync_days", default_days))
    except (TypeError, ValueError) as exc:
        raise ValueError("sync_days 必须是非负整数") from exc
    if sync_days < 0:
        raise ValueError("sync_days 必须是非负整数")
    start = target - timedelta(days=365 * 3 if full else sync_days)
    return SyncRequest("legacy", target, start, target, sync_days, full, force)


def _migrate_legacy_coros_auth(user_manager, user, user_cfg: UserConfig) -> bool:
    """仅在 token 归属唯一时迁移旧版 coros-mcp 全局认证。"""
    if user.provider != "coros":
        return False
    token_path = Path(user_cfg.token_dir) / "coros-auth.json"
    if token_path.exists():
        return False

    active_coros_users = [
        record for record in user_manager.list_all()
        if record.provider == "coros" and record.token_status == "active"
    ]
    if (len(active_coros_users) != 1
            or active_coros_users[0].api_key != user.api_key):
        logger.warning("Coros 旧版全局 token 归属不唯一，跳过自动迁移")
        return False

    from .providers.coros import CorosAuth
    return CorosAuth(user_cfg.token_dir).migrate_legacy_token()


def _is_coros_auth_error(exc: Exception) -> bool:
    """识别 Coros API 的 token 失效响应。"""
    message = str(exc).lower()
    return "result=1019" in message or "access token is invalid" in message


def _is_provider_auth_error(exc: Exception, provider_type: str) -> bool:
    """统一识别三平台认证失效，避免把重新绑定场景返回为 500。"""
    if isinstance(exc, ProviderAuthenticationError):
        return True
    if provider_type == "coros" and _is_coros_auth_error(exc):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        response = getattr(getattr(exc, "error", None), "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in (
        "not authenticated",
        "please login first",
        "unauthorized",
        "forbidden",
        "token expired",
        "token is invalid",
    ))


def _sync_task_error(exc: Exception, provider_type: str) -> dict[str, Any]:
    """将后台异常转换为不含第三方原始响应的稳定任务错误。"""
    if _is_provider_auth_error(exc, provider_type):
        return {
            "code": "provider_authentication_failed",
            "message": str(ProviderAuthenticationError(provider_type)),
            "retryable": False,
            "action": "reauthorize",
        }
    if isinstance(exc, LocalPersistenceError):
        return {
            "code": "local_persistence_failed",
            "message": str(exc),
            "retryable": True,
            "action": "contact_support",
        }
    if isinstance(exc, TimeoutError):
        return {
            "code": "provider_timeout",
            "message": "数据源请求超时，请稍后重试",
            "retryable": True,
            "action": "retry",
        }
    return {
        "code": "sync_failed",
        "message": "数据源同步失败，请稍后重试",
        "retryable": True,
        "action": "retry",
    }


def _user_context(memory_store: MemoryStore) -> str:
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
                "plan_context": fm.get("plan_context", {}),
            }
    except Exception:
        pass
    return {"has_data": False}


# ── 路由注册入口 ────────────────────────────────


def register_web_routes(server, user_manager: UserManager, config: Config):
    """在 FastMCP server 上注册所有 Web 路由。"""

    invitations = InvitationStore(config.invite_codes_path)
    sync_coordinator = SyncCoordinator(
        max_concurrency=config.sync_max_concurrency,
        max_pending=config.sync_max_pending,
    )
    runner_instance_id = secrets.token_hex(16)
    sync_task_stores: dict[str, SyncTaskStore] = {}

    def sync_task_store(api_key: str) -> SyncTaskStore:
        store = sync_task_stores.get(api_key)
        if store is None:
            store = SyncTaskStore(
                user_manager.get_sync_tasks_path(api_key),
                runner_instance_id=runner_instance_id,
            )
            sync_task_stores[api_key] = store
        return store

    # ═══ 基础设施路由 ═══

    @server.custom_route("/healthz", methods=["GET", "HEAD"])
    async def healthz(request: Request) -> Response:
        """负载均衡存活检查：不依赖会话、用户数据或外部服务。"""
        headers = {"Cache-Control": "no-store"}
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return JSONResponse({"status": "ok"}, headers=headers)

    @server.custom_route("/contact/qr", methods=["GET"])
    async def contact_qr(request: Request) -> Response:
        """返回登录用户可读取、可由运维覆盖的微信群二维码。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user is None:
            return Response(status_code=401, headers={"Cache-Control": "no-store"})
        path = _contact_qr_path(config)
        if path is None:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": (
                    'inline; filename="neurun-wechat-group.jpg"'
                ),
            },
        )

    # ═══ 页面路由 ═══

    @server.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        """首页 — 已绑定用户进聊天，未绑定跳转设置页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user and user.token_status == "active":
            html = _read_template("chat.html")
            return _html_response(html or _chat_fallback(), user)
        return _redirect(_user_page(user))

    @server.custom_route("/login", methods=["GET"])
    async def login_page(request: Request) -> Response:
        """应用账号登录页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user:
            return _redirect(_user_page(user))
        html = _read_template("auth.html")
        return _html_response(html or _auth_fallback("login"))

    @server.custom_route("/register", methods=["GET"])
    async def register_page(request: Request) -> Response:
        """邀请码注册页。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user:
            return _redirect(_user_page(user))
        html = _read_template("auth.html")
        return _html_response(html or _auth_fallback("register"))

    @server.custom_route("/setup", methods=["GET"])
    async def setup_page(request: Request) -> Response:
        """数据源绑定页面；已绑定 Coros 用户可进入睡眠重新授权。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if user is None:
            return _redirect("/login")
        is_coros_rebind = (
            user.token_status == "active"
            and user.provider == "coros"
            and request.query_params.get("rebind") == "coros"
        )
        if user.token_status == "active" and not is_coros_rebind:
            return _redirect("/")

        html = _read_template("setup.html")
        return _html_response(html or _setup_fallback(), user)

    @server.custom_route("/reports", methods=["GET"])
    async def reports_page(request: Request) -> Response:
        """日报列表页面。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return _redirect(_user_page(user))

        html = _read_template("reports.html")
        return _html_response(html or _reports_fallback(), user)

    @server.custom_route("/sync", methods=["GET"])
    async def sync_page(request: Request) -> Response:
        """同步管理页面。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return _redirect(_user_page(user))

        html = _read_template("sync.html")
        return _html_response(html or _sync_fallback(), user)

    @server.custom_route("/profile", methods=["GET"])
    async def profile_page(request: Request) -> Response:
        """个人信息页面。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return _redirect(_user_page(user))

        html = _read_template("profile.html")
        return _html_response(html or _profile_fallback(), user)

    # ═══ API 路由 ═══

    @server.custom_route("/api/invitations/validate", methods=["POST"])
    async def api_invitation_validate(request: Request) -> Response:
        """检查邀请码是否可用；不在此阶段预占名额。"""
        try:
            body = await request.json()
            code = str(body.get("invite_code", "")).strip()
        except Exception:
            return JSONResponse({"status": "error", "message": "请求体必须是有效 JSON"}, status_code=400)
        try:
            valid = invitations.validate(code)
        except InvitationError as exc:
            logger.error("邀请码配置错误: %s", exc)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=503)
        if not valid:
            return JSONResponse({"status": "error", "message": _INVITE_UNAVAILABLE}, status_code=400)
        return JSONResponse({"status": "ok", "valid": True})

    @server.custom_route("/api/register", methods=["POST"])
    async def api_register(request: Request) -> Response:
        """使用邀请码创建昵称/邮箱/密码应用账号。"""
        try:
            body = await request.json()
            invite_code = str(body.get("invite_code", "")).strip()
            nickname = str(body.get("nickname", "")).strip()
            email = str(body.get("email", "")).strip()
            password = str(body.get("password", ""))
        except Exception:
            return JSONResponse({"status": "error", "message": "请求体必须是有效 JSON"}, status_code=400)

        field_error = _registration_error(nickname, email, password)
        if field_error:
            return JSONResponse({"status": "error", "message": field_error}, status_code=400)
        try:
            if not invitations.validate(invite_code):
                raise InvitationError(_INVITE_UNAVAILABLE)
            user = user_manager.register_account(nickname, email, password)
            try:
                invitations.consume(invite_code, user.email)
            except Exception:
                user_manager.delete_account(user.api_key)
                raise
        except UserExistsError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=409)
        except InvitationError as exc:
            status_code = 400 if str(exc) == _INVITE_UNAVAILABLE else 503
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=status_code)

        user_manager.ensure_dirs(user.api_key)
        response = JSONResponse({
            "status": "ok",
            "user": {"nickname": user.nickname, "email": user.email},
            "next": "/setup",
        }, status_code=201)
        _set_session_cookie(response, request, user.api_key)
        return response

    @server.custom_route("/api/login", methods=["POST"])
    async def api_login(request: Request) -> Response:
        """使用应用邮箱和密码建立会话。"""
        try:
            body = await request.json()
            email = str(body.get("email", "")).strip()
            password = str(body.get("password", ""))
        except Exception:
            return JSONResponse({"status": "error", "message": "请求体必须是有效 JSON"}, status_code=400)
        user = user_manager.authenticate(email, password)
        if user is None:
            return JSONResponse({"status": "error", "message": "邮箱或密码错误"}, status_code=401)
        next_url = "/" if user.token_status == "active" else "/setup"
        response = JSONResponse({
            "status": "ok",
            "user": {"nickname": user.nickname, "email": user.email},
            "next": next_url,
        })
        _set_session_cookie(response, request, user.api_key)
        return response

    @server.custom_route("/api/setup", methods=["POST"])
    async def api_setup(request: Request) -> Response:
        """启动数据源绑定流程（Garmin / Coros / Huawei）。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user is None:
            return JSONResponse({"status": "error", "message": "请先登录应用账号"}, status_code=401)
        provider = body.get("provider", "garmin").strip()
        rebind = body.get("rebind") is True
        is_coros_rebind = (
            rebind and user.token_status == "active"
            and user.provider == "coros" and provider == "coros"
        )
        if user.token_status == "active" and not is_coros_rebind:
            return JSONResponse(
                {"status": "error", "message": "当前账号已绑定运动平台，MVP 暂不支持换绑"},
                status_code=409,
            )

        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        domain = body.get("domain", "garmin.com").strip()
        if user.token_status == "expired" and provider != user.provider:
            return JSONResponse(
                {"status": "error", "message": "连接失效后只能重新绑定原平台，MVP 暂不支持换绑"},
                status_code=409,
            )

        # ── Huawei: GROUP_PALS_TOKEN 认证 ──
        if provider == "huawei":
            group_token = body.get("group_pals_token", "").strip()
            if not group_token:
                return JSONResponse({"status": "error", "message": "请输入 GROUP_PALS_TOKEN"}, status_code=400)

            user_cfg = config.for_user(api_key)
            user_manager.ensure_dirs(api_key)

            try:
                from .providers.huawei import HuaweiProvider
                # 先仅注入本次认证；认证成功后再持久化用户级 CrewPals 凭证。
                user_cfg.set_group_pals_token(group_token, persist=False)
                hw = HuaweiProvider(user_cfg)
                if not hw.authenticate():
                    raise ProviderAuthenticationError("huawei")
                user_cfg.set_group_pals_token(group_token)
                user_manager.update(api_key, provider="huawei", token_status="active")
                return JSONResponse({"status": "ok"})
            except Exception as exc:
                logger.error("Huawei 认证失败: %s", exc)
                return JSONResponse({"status": "error", "message": f"Huawei 认证失败: {exc}"}, status_code=400)

        # ── Coros: 邮箱/手机号 + 密码 ──
        if provider == "coros":
            if not email or not password:
                return JSONResponse({"status": "error", "message": "请输入账号和密码"}, status_code=400)

            user_cfg = config.for_user(api_key)
            user_manager.ensure_dirs(api_key)

            try:
                from .providers.coros import CorosAuth
                auth = CorosAuth(
                    user_cfg.token_dir,
                    credential_key=user_cfg.coros_credential_key,
                )
                region = str(body.get("region") or "cn").strip().lower()
                auto_refresh = body.get(
                    "auto_refresh", body.get("coros_auto_relogin", True),
                ) is True
                if auth.login_training(
                    email, password, region, auto_refresh=auto_refresh,
                ):
                    user_manager.update(api_key, garmin_email=email,
                                       provider="coros", token_status="active")
                    enabled = auth.auto_relogin_enabled
                    message = "Coros 运动数据授权成功"
                    if enabled:
                        message += "；Training Hub 自动鉴权已启用"
                    elif auth.auto_relogin_warning:
                        message += f"；自动鉴权未启用：{auth.auto_relogin_warning}"
                    return JSONResponse({
                        "status": "ok",
                        "scope": "training",
                        "training_auth_status": "active",
                        "training_auto_refresh_enabled": enabled,
                        "coros_auto_relogin_enabled": enabled,
                        "message": message,
                    })
                else:
                    return JSONResponse({"status": "error", "message": "Coros Training Hub 登录失败，请检查账号、密码和区域"}, status_code=400)
            except Exception as exc:
                logger.error("Coros 登录失败: %s", exc)
                return JSONResponse({"status": "error", "message": f"登录失败: {exc}"}, status_code=400)

        # ── Garmin: 邮箱 + 密码 + 可选 MFA ──
        if not email or not password:
            return JSONResponse({"status": "error", "message": "请输入邮箱和密码"}, status_code=400)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        user_cfg.email = email
        user_cfg._domain = domain

        auth = AuthManager(user_cfg)
        keep_mfa_session = False

        try:
            session_key = secrets.token_hex(16)
            result = auth.start_login(email, password, session_key)

            if result == "needs_mfa":
                keep_mfa_session = True
                resp = JSONResponse({"status": "needs_mfa", "session": session_key})
            else:
                user_manager.update(api_key, garmin_email=email,
                                    garmin_domain=domain, token_status="active")
                resp = JSONResponse({"status": "ok"})

            return resp

        except Exception as exc:
            logger.error("Garmin 登录失败: %s", exc)
            return JSONResponse({"status": "error", "message": f"登录失败: {exc}"}, status_code=400)
        finally:
            if not keep_mfa_session:
                auth.close()

    @server.custom_route("/api/coros/auth/training", methods=["POST"])
    async def api_coros_training_auth(request: Request) -> Response:
        """重新认证 Coros Training Hub 运动数据域。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse(
                {"status": "error", "message": "请先登录应用账号"},
                status_code=401,
            )
        if user.token_status == "active" and user.provider != "coros":
            return JSONResponse(
                {"status": "error", "message": "当前账号已绑定其他运动平台"},
                status_code=409,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "error", "message": "无效请求"}, status_code=400,
            )
        account = str(body.get("account") or body.get("email") or "").strip()
        password = str(body.get("password") or "")
        region = str(body.get("region") or "cn").strip().lower()
        if not account or not password or region not in {"eu", "us", "cn", "asia"}:
            return JSONResponse({
                "status": "error",
                "message": "请输入 Coros Training Hub 账号、密码并选择账号区域",
            }, status_code=400)
        from .providers.coros import CorosAuth
        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        auth = CorosAuth(
            user_cfg.token_dir,
            credential_key=user_cfg.coros_credential_key,
        )
        if not auth.login_training(
            account, password, region,
            auto_refresh=body.get("auto_refresh", True) is True,
        ):
            return JSONResponse({
                "status": "error",
                "message": "Coros Training Hub 登录失败，请检查账号、密码和区域",
            }, status_code=400)
        user_manager.update(
            api_key, garmin_email=account,
            provider="coros", token_status="active",
        )
        message = "Coros 运动数据授权成功"
        if auth.auto_relogin_enabled:
            message += "；Training Hub 自动鉴权已启用"
        elif auth.auto_relogin_warning:
            message += f"；自动鉴权未启用：{auth.auto_relogin_warning}"
        return JSONResponse({
            "status": "ok",
            "scope": "training",
            "training_auth_status": "active",
            "training_auto_refresh_enabled": auth.auto_relogin_enabled,
            "message": message,
        })

    @server.custom_route("/api/coros/auth/sleep", methods=["POST"])
    async def api_coros_sleep_auth(request: Request) -> Response:
        """认证 Coros App Mobile 睡眠域，不改动 Training Hub Token。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse(
                {"status": "error", "message": "请先登录应用账号"},
                status_code=401,
            )
        if user.provider != "coros" or user.token_status != "active":
            return JSONResponse(
                {"status": "error", "message": "请先完成 Coros 运动数据认证"},
                status_code=409,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "error", "message": "无效请求"}, status_code=400,
            )
        email = str(body.get("account") or body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if "@" not in email or not password:
            return JSONResponse({
                "status": "error",
                "message": "请输入可登录 Coros App 的邮箱和密码；睡眠认证不支持手机号",
            }, status_code=400)
        from .providers.coros import CorosAuth
        user_cfg = config.for_user(api_key)
        auth = CorosAuth(
            user_cfg.token_dir,
            credential_key=user_cfg.coros_credential_key,
        )
        if not auth.login_sleep(
            email, password,
            auto_refresh=body.get("auto_refresh", True) is True,
        ):
            return JSONResponse({
                "status": "error",
                "message": auth.sleep_auth_error or "Coros App 睡眠认证失败",
            }, status_code=400)
        message = "Coros 睡眠数据授权成功"
        if auth.sleep_auto_refresh_enabled:
            message += "；睡眠自动鉴权已启用"
        elif auth.sleep_auto_refresh_warning:
            message += f"；自动鉴权未启用：{auth.sleep_auto_refresh_warning}"
        return JSONResponse({
            "status": "ok",
            "scope": "sleep",
            "sleep_auth_status": "active",
            "sleep_auto_refresh_enabled": auth.sleep_auto_refresh_enabled,
            "message": message,
        })

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
        finally:
            auth.close()

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
        memory_store = MemoryStore(user_cfg.memory_dir)
        context = _user_context(memory_store)

        async def generate():
            async for token in chat_stream(messages, context=context):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @server.custom_route("/api/sync", methods=["POST"])
    async def api_sync(request: Request) -> Response:
        """只同步并持久化数据，不生成或覆盖日报。

        Request body (JSON):
            单日: {"mode": "single", "date": "YYYY-MM-DD", "force": false}
            批量: {"mode": "batch", "from_date": "YYYY-MM-DD",
                   "to_date": "YYYY-MM-DD", "force": false}
            未提供 mode 时兼容旧版 date/sync_days/full 参数。
        """
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)

        # 注入用户凭证供 _do_daily_sync 使用
        user_cfg.provider = user.provider
        user_cfg.email = user.garmin_email
        user_cfg._domain = user.garmin_domain
        _migrate_legacy_coros_auth(user_manager, user, user_cfg)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "error", "message": "请求体必须是有效 JSON"},
                status_code=400,
            )

        try:
            sync_request = _parse_sync_request(body or {}, config.sync_days)
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=400
            )

        task_id = f"st_{secrets.token_hex(16)}"
        task_store = sync_task_store(api_key)

        def report_progress(
            stage: str, current: int, total: int, label: str,
            items: dict[str, Any] | None = None,
        ) -> None:
            try:
                task_store.update_progress(
                    task_id,
                    stage=stage,
                    current=current,
                    total=total,
                    label=label,
                    items=items,
                )
            except Exception as exc:
                logger.warning(
                    "无法更新同步任务阶段: task_id=%s error_type=%s",
                    task_id,
                    type(exc).__name__,
                )

        def run_sync() -> dict[str, Any]:
            provider = None
            storage = None
            try:
                provider, storage, _ms, _uid = _do_data_sync(
                    config=user_cfg, target=sync_request.target,
                    sync_days=sync_request.sync_days, full_sync=sync_request.full,
                    force_sync=sync_request.force, quiet=True,
                    progress_callback=report_progress,
                )
                report_progress("backing_up", 4, 4, "正在备份本地数据")
                storage.backup_to(user_manager.get_backup_path(api_key))
                return {
                    "message": (
                        f"单日同步完成（{sync_request.target}）"
                        if sync_request.mode == "single"
                        else f"批量同步完成（{sync_request.start} ~ {sync_request.end}）"
                        if sync_request.mode == "batch"
                        else f"同步完成（{sync_request.start} ~ {sync_request.end}）"
                    ),
                    "mode": sync_request.mode,
                    "from_date": str(sync_request.start),
                    "to_date": str(sync_request.end),
                }
            finally:
                close_runtime_resources(provider)
                if storage is not None:
                    storage.close()

        def on_accept() -> None:
            task_store.create(
                task_id=task_id,
                mode=sync_request.mode,
                from_date=str(sync_request.start),
                to_date=str(sync_request.end),
                force=sync_request.force,
            )

        def on_started() -> None:
            task_store.mark_running(
                task_id,
                stage="authenticating",
                current=1,
                total=4,
                label="正在验证数据源",
            )

        def on_succeeded(result: dict[str, Any]) -> None:
            try:
                user_manager.update(api_key, last_sync=str(date.today()))
            except Exception as exc:
                logger.warning(
                    "无法更新用户最后同步日期: error_type=%s",
                    type(exc).__name__,
                )
            task_store.mark_succeeded(task_id, result=result)

        def on_failed(exc: Exception) -> None:
            error = _sync_task_error(exc, user.provider)
            if error["code"] == "provider_authentication_failed":
                try:
                    user_manager.update(api_key, token_status="expired")
                except Exception as update_exc:
                    logger.warning(
                        "无法更新数据源失效状态: error_type=%s",
                        type(update_exc).__name__,
                    )
            logger.error(
                "同步任务失败: task_id=%s provider=%s error_type=%s code=%s",
                task_id,
                user.provider,
                type(exc).__name__,
                error["code"],
            )
            task_store.mark_failed(task_id, error=error)

        try:
            await sync_coordinator.submit(
                api_key,
                task_id,
                run_sync,
                on_accept=on_accept,
                on_started=on_started,
                on_succeeded=on_succeeded,
                on_failed=on_failed,
            )
            location = f"/api/sync/tasks/{task_id}"
            return JSONResponse({
                "status": "queued",
                "task_id": task_id,
                "mode": sync_request.mode,
                "from_date": str(sync_request.start),
                "to_date": str(sync_request.end),
                "links": {"self": location},
            }, status_code=202, headers={
                "Location": location,
                "Cache-Control": "no-store",
            })

        except SyncInProgressError as exc:
            existing_task_id = exc.task_id
            location = (
                f"/api/sync/tasks/{existing_task_id}"
                if existing_task_id else None
            )
            return JSONResponse(
                {
                    "status": "error",
                    "code": "sync_in_progress",
                    "message": str(exc),
                    "task_id": existing_task_id,
                    "links": {"self": location} if location else {},
                },
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        except SyncCapacityExceededError as exc:
            return JSONResponse(
                {
                    "status": "error",
                    "code": "sync_capacity_exceeded",
                    "message": str(exc),
                },
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "5"},
            )
        except Exception as exc:
            logger.error(
                "同步任务接纳失败: error_type=%s", type(exc).__name__,
            )
            return JSONResponse(
                {
                    "status": "error",
                    "code": "sync_task_submit_failed",
                    "message": "无法创建同步任务，请稍后重试",
                },
                status_code=500,
                headers={"Cache-Control": "no-store"},
            )

    @server.custom_route("/api/sync/tasks/{task_id}", methods=["GET"])
    async def api_sync_task(request: Request) -> Response:
        """查询当前登录用户自己的异步同步任务。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if user is None:
            return JSONResponse(
                {"status": "error", "message": "请先登录"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )

        task_id = str(request.path_params.get("task_id") or "")
        try:
            task = sync_task_store(api_key).get(task_id)
        except LocalPersistenceError:
            logger.error("无法读取同步任务: task_id=%s", task_id)
            return JSONResponse(
                {
                    "status": "error",
                    "code": "sync_task_read_failed",
                    "message": "无法读取同步任务，请联系管理员检查数据目录权限",
                },
                status_code=500,
                headers={"Cache-Control": "no-store"},
            )

        if task is None:
            return JSONResponse(
                {
                    "status": "error",
                    "code": "sync_task_not_found",
                    "message": "同步任务不存在",
                },
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )

        headers = {"Cache-Control": "no-store"}
        if task.status in ACTIVE_STATUSES:
            headers["Retry-After"] = "1"
        return JSONResponse(task.to_api_dict(), headers=headers)

    @server.custom_route("/api/sync/calendar", methods=["GET"])
    async def api_sync_calendar(request: Request) -> Response:
        """读取当前用户本地 SQLite，返回指定月份的逐日同步状态。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse(
                {"status": "error", "message": "请先绑定数据源"}, status_code=401,
            )

        try:
            month, start, end = _calendar_month_range(
                request.query_params.get("month")
            )
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=400,
            )

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        storage = Storage(user_cfg)
        try:
            user_id = storage.get_local_user_id()
            calendar_data = storage.get_sync_calendar(user_id, start, end)
            return JSONResponse(
                {"month": month, **calendar_data},
                headers={"Cache-Control": "no-store"},
            )
        finally:
            storage.close()

    @server.custom_route("/api/status", methods=["GET"])
    async def api_status(request: Request) -> Response:
        """查询用户状态。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None

        if not user:
            return JSONResponse({"bound": False})

        return JSONResponse({
            "bound": True,
            "nickname": user.nickname,
            "email": user.email,
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
        memory_store = MemoryStore(user_cfg.memory_dir)

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
        memory_store = MemoryStore(user_cfg.memory_dir)

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

    @server.custom_route("/api/reports", methods=["POST"])
    async def api_report_generate(request: Request) -> Response:
        """基于本地 SQLite 显式生成指定日期日报。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user or user.token_status != "active":
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        try:
            body = await request.json()
            raw_date = str(body.get("date", "")).strip()
            target = date.fromisoformat(raw_date)
        except (AttributeError, TypeError, ValueError):
            return JSONResponse(
                {"status": "error", "message": "date 格式无效，应为 YYYY-MM-DD"},
                status_code=400,
            )

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        result = None
        try:
            result = await asyncio.to_thread(
                _do_daily_sync,
                config=user_cfg,
                target=target,
                skip_sync=True,
                quiet=True,
            )
        except Exception as exc:
            logger.error("日报生成失败: %s", exc)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
        finally:
            if result is not None:
                _mem, provider, storage, _memory_store, _user_id = result
                close_runtime_resources(provider, storage)

        return JSONResponse({
            "status": "ok",
            "message": f"{target} 日报已生成",
            "date": str(target),
        })

    @server.custom_route("/api/profile", methods=["GET"])
    async def api_profile_get(request: Request) -> Response:
        """读取个人资料、最佳成绩、目标、偏好。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        user_cfg = config.for_user(api_key)
        user_manager.ensure_dirs(api_key)
        memory_store = MemoryStore(user_cfg.memory_dir)

        result = {
            "nickname": user.nickname,
            "account_email": user.email,
            "provider": user.provider,
            "email": user.garmin_email,
            "token_status": user.token_status,
            "last_sync": user.last_sync,
            "sleep_available": None,
            "coros_auto_relogin_enabled": False,
            "training_auth_status": None,
            "sleep_auth_status": None,
            "training_auto_refresh_enabled": False,
            "sleep_auto_refresh_enabled": False,
            "coros_secure_credential_storage": bool(
                user_cfg.coros_credential_key
            ),
            "profile": None,
            "goals": [],
            "preferences": None,
        }

        if user.provider == "coros":
            try:
                from .providers.coros import CorosAuth
                coros_auth = CorosAuth(
                    user_cfg.token_dir,
                    credential_key=user_cfg.coros_credential_key,
                )
                result["sleep_available"] = coros_auth.has_sleep_access()
                result["coros_auto_relogin_enabled"] = (
                    coros_auth.auto_relogin_enabled
                )
                result["training_auth_status"] = (
                    "active" if coros_auth.is_authenticated() else "expired"
                )
                result["sleep_auth_status"] = (
                    "active" if coros_auth.has_sleep_access() else "missing"
                )
                result["training_auto_refresh_enabled"] = (
                    coros_auth.auto_relogin_enabled
                )
                result["sleep_auto_refresh_enabled"] = (
                    coros_auth.sleep_auto_refresh_enabled
                )
            except Exception as exc:
                logger.warning("Coros 睡眠授权状态读取失败: %s", exc)
                result["sleep_available"] = False

        # 竞技档案
        profile = memory_store.get("fitness-assessment")
        if profile:
            result["profile"] = profile.front_matter

        # 活跃目标
        goals = memory_store.list_by_type("goal", status="active")
        result["goals"] = [g.front_matter for g in goals]

        # 训练偏好
        prefs = memory_store.get("preferences")
        if prefs:
            result["preferences"] = prefs.front_matter

        return JSONResponse(result)

    @server.custom_route(
        "/api/coros/relogin-credential", methods=["DELETE"],
    )
    async def api_coros_relogin_credential_delete(
        request: Request,
    ) -> Response:
        """当前用户关闭 Coros 自动续期并删除加密重登凭据。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse(
                {"status": "error", "message": "请先登录应用账号"},
                status_code=401,
            )
        if user.provider != "coros":
            return JSONResponse(
                {"status": "error", "message": "当前账号未绑定 Coros"},
                status_code=409,
            )
        from .providers.coros_credentials import CorosReloginCredentialStore
        try:
            CorosReloginCredentialStore(
                config.for_user(api_key).token_dir,
                config.coros_credential_key,
            ).delete()
        except LocalPersistenceError as exc:
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=500,
            )
        return JSONResponse({
            "status": "ok", "coros_auto_relogin_enabled": False,
        })

    @server.custom_route(
        "/api/coros/auth/{scope}/refresh-credential", methods=["DELETE"],
    )
    async def api_coros_refresh_credential_delete(
        request: Request,
    ) -> Response:
        """按认证域关闭 Coros 自动鉴权并删除对应加密凭据。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse(
                {"status": "error", "message": "请先登录应用账号"},
                status_code=401,
            )
        if user.provider != "coros":
            return JSONResponse(
                {"status": "error", "message": "当前账号未绑定 Coros"},
                status_code=409,
            )
        scope = request.path_params.get("scope")
        if scope not in {"training", "sleep"}:
            return JSONResponse(
                {"status": "error", "message": "未知的 Coros 认证域"},
                status_code=404,
            )
        from .providers.coros_credentials import (
            CorosMobileCredentialStore, CorosReloginCredentialStore,
        )
        user_cfg = config.for_user(api_key)
        store_class = (
            CorosReloginCredentialStore
            if scope == "training" else CorosMobileCredentialStore
        )
        try:
            store_class(
                user_cfg.token_dir, user_cfg.coros_credential_key,
            ).delete()
        except LocalPersistenceError as exc:
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=500,
            )
        return JSONResponse({
            "status": "ok", "scope": scope, "auto_refresh_enabled": False,
        })

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
        atomic_write_private(
            profile_path,
            build_memory_file(fm, "\n".join(body_lines)),
        )

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
        atomic_write_private(goal_path, build_memory_file(fm, goal_body))

        return JSONResponse({"status": "ok", "id": goal_id})

    @server.custom_route("/api/goals/{goal_id}", methods=["PUT"])
    async def api_goals_update(request: Request) -> Response:
        """更新训练目标。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        goal_id = request.path_params.get("goal_id", "")
        if not goal_id:
            return JSONResponse({"status": "error", "message": "缺少目标 ID"}, status_code=400)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "无效请求"}, status_code=400)

        user_cfg = config.for_user(api_key)
        goal_path = Path(user_cfg.memory_dir) / "goals" / "active" / f"{goal_id}.md"

        if not goal_path.exists():
            return JSONResponse({"status": "error", "message": "目标不存在"}, status_code=404)

        # 加载已有目标
        existing = Memory.from_file(goal_path)
        if existing is None:
            return JSONResponse({"status": "error", "message": "无法读取目标文件"}, status_code=500)

        fm = existing.front_matter
        goal_dist = body.get("distance") or list(fm.get("metrics", {}).keys())[0].replace("target_", "") or "marathon"

        # 更新字段
        if body.get("name"):
            fm["title"] = body["name"]
        fm["target_date"] = body.get("target_date") or fm.get("target_date", "")
        fm["metrics"] = {
            f"target_{goal_dist}": body.get("target_time") or "",
            "weekly_mileage_km": _int_or_none(body.get("weekly_km")) or _int_or_none(fm.get("metrics", {}).get("weekly_mileage_km")) or 50,
        }
        fm["status"] = body.get("status") or fm.get("status", "active")
        fm["updated"] = datetime.now().isoformat(timespec="seconds")

        goal_name = body.get("name") or existing.body.split("\n")[0].replace("# ", "")
        existing.body = f"""# {goal_name}

## 目标
- 距离: {goal_dist}
- 目标成绩: {body.get('target_time') or '—'}
- 截止日期: {fm['target_date']}
- 周跑量目标: {fm['metrics']['weekly_mileage_km']} km

## 进度
更新于 {date.today()}。"""
        existing.front_matter = fm
        existing.save()

        return JSONResponse({"status": "ok"})

    @server.custom_route("/api/goals/{goal_id}", methods=["DELETE"])
    async def api_goals_delete(request: Request) -> Response:
        """删除训练目标（归档而非物理删除）。"""
        api_key = _get_api_key(request)
        user = user_manager.get(api_key) if api_key else None
        if not user:
            return JSONResponse({"status": "error", "message": "请先绑定数据源"}, status_code=401)

        goal_id = request.path_params.get("goal_id", "")
        if not goal_id:
            return JSONResponse({"status": "error", "message": "缺少目标 ID"}, status_code=400)

        user_cfg = config.for_user(api_key)
        goal_path = Path(user_cfg.memory_dir) / "goals" / "active" / f"{goal_id}.md"

        if not goal_path.exists():
            return JSONResponse({"status": "error", "message": "目标不存在"}, status_code=404)

        # 归档：移到 archived 目录，改状态
        existing = Memory.from_file(goal_path)
        if existing:
            existing.front_matter["status"] = "archived"
            existing.save()

        archive_dir = Path(user_cfg.memory_dir) / "goals" / "archived"
        ensure_private_dir(archive_dir)
        goal_path.rename(archive_dir / f"{goal_id}.md")

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
        atomic_write_private(pref_path, build_memory_file(fm, pref_body))

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


def _auth_fallback(mode: str) -> str:
    """认证模板缺失时提供可操作的最小页面。"""
    is_register = mode == "register"
    title = "邀请码注册" if is_register else "登录"
    fields = (
        '<input id="invite" placeholder="6 位邀请码（旧长码仍可用）">'
        '<input id="nickname" placeholder="昵称">'
        '<input id="email" type="email" placeholder="邮箱">'
        '<input id="password" type="password" placeholder="密码（至少 8 位）">'
        if is_register else
        '<input id="email" type="email" placeholder="邮箱">'
        '<input id="password" type="password" placeholder="密码">'
    )
    endpoint = "/api/register" if is_register else "/api/login"
    payload = (
        "{invite_code:v('invite'),nickname:v('nickname'),email:v('email'),password:v('password')}"
        if is_register else "{email:v('email'),password:v('password')}"
    )
    alternate = (
        '<a href="/login">已有账号，去登录</a>'
        if is_register else '<a href="/register">使用邀请码注册</a>'
    )
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>neurun — {title}</title>
<style>body{{font-family:sans-serif;background:#111;color:#eee;display:grid;place-items:center;min-height:100vh}}
main{{width:min(380px,90vw)}}input,button{{box-sizing:border-box;width:100%;padding:12px;margin:6px 0}}
button{{background:#4caf50;color:white;border:0}}a{{color:#6c7}}#msg{{color:#f77}}</style></head>
<body><main><h1>{title}</h1>{fields}<button onclick="submitForm()">{title}</button>
<p id="msg"></p>{alternate}</main><script>function v(id){{return document.getElementById(id).value}}
async function submitForm(){{let r=await fetch('{endpoint}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({payload})}});let d=await r.json();if(r.ok)location.href=d.next||'/';else document.getElementById('msg').textContent=d.message||'请求失败'}}
</script></body></html>"""


def _setup_fallback() -> str:
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>neurun — 绑定 Garmin</title>
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
<title>neurun Coach</title>
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
  <h1>🏃 neurun Coach</h1>
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
