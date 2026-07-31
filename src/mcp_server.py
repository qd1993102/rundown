"""MCP Server — 将 neurun 数据暴露为 MCP Resources 和 Tools。

基于 fastmcp 框架，供 OpenClaw / Claude Desktop 连接。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .local_files import atomic_write_private, ensure_private_dir, restrict_private_file
from .training_analysis import ActivityDayState, natural_week_bounds

logger = logging.getLogger(__name__)


def create_server(
    config,
    provider,
    storage,
    memory_store,
    user_id: int,
    *,
    enable_admin_tools: bool = False,
):
    """创建并配置 FastMCP 服务器。

    暴露 Resources（只读上下文）和 Tools（AI 可调用的查询/分析函数）。
    """
    from fastmcp import FastMCP

    mcp = FastMCP(
        name="neurun",
        instructions="""你已接入 neurun——一个 运动数据 + AI 跑步教练系统。

## 核心能力
- **每日综合报告**：包含昨日训练详情（分段配速、步频、功率、心率、触地时间）、昨夜睡眠质量、今晨恢复状态（HRV、静息心率、身体电量）、训练负荷（ACWR）、7日趋势、异常检测、今日训练建议。
- **训练细节分析**：每项活动的分段数据，包括配速变化、步频、功率、触地时间、步幅、垂直振幅、爬升等。
- **历史查询**：前30天的活动列表、健康指标趋势、任意日期的日报。
- **静态HTML报告**：可生成三主题（运动/清新/暗黑）完整HTML日报，包含趋势图，浏览器直接打开。

## 何时主动触发
- 用户提到"今天状态"、"昨天训练"、"睡眠"、"恢复"、"HRV"、"跑步数据"→ 读取 `neurun://daily/latest`
- 用户问"最近一周"、"趋势"、"负荷"、"训练量" → 读取 `neurun://context/full`
- 用户问"活动详情"、"配速"、"步频"、"功率"、"分段" → 调用 `get_activity_detail`
- 用户说"生成报告"、"日报"、"HTML" → 调用 `generate_report` 或 `generate_html_report`
- 用户说"截图"、"生成图片"、"分享"、"导出图片"、"打卡" → 调用 `generate_image`（可选 theme: fresh/sport/dark）
- 用户问"目标"、"5K"、"备赛"、"PB" → 读取 `neurun://goals/active`
- 用户要"更新资料"、"设置目标"、"输入身高体重" → 调用 `update_profile` 或 `set_goal`

## 典型对话示例
- 用户："早上好，今天状态怎么样？" → 你读取 daily/latest，用自然语言总结状态并给出训练建议
- 用户："帮我看看昨天那场跑步的技术数据" → 你先 query_activities 找到活动ID，再 get_activity_detail 获取分段
- 用户："这周跑量够不够？离目标还差多少？" → 你读取 goals/active + query_current_week_progress，按自然周计算对比
- 用户："生成今天的HTML日报" → 你调用 generate_html_report

## 数据时效
- 健康数据每日更新（需先运行 `neurun daily` 或 `neurun sync`）
- 日报每天早上自动生成
- 活动详情随时可查""",
    )

    # ═══════════════════════════════════════════════════════
    # Resources: 只读数据，自动注入 AI 上下文
    # ═══════════════════════════════════════════════════════

    @mcp.resource("neurun://daily/latest")
    def get_latest_daily() -> str:
        """【最常用】最新每日综合报告。包含：昨日训练详情（类型/时长/距离/配速/心率/负荷）、
        昨夜睡眠（时长/质量/深睡占比）、今晨恢复状态（HRV/静息心率/身体电量/训练准备）、
        训练负荷ACWR、7日趋势、异常提醒、今日训练建议、AI教练洞察。"""
        mem = memory_store.get_latest("daily_report")
        if mem is None:
            return "暂无日报，请先运行 neurun sync"
        return _format_memory(mem)

    @mcp.resource("neurun://daily/{target_date}")
    def get_daily_by_date(target_date: str) -> str:
        """指定日期的日报。"""
        mem = memory_store.get(target_date)
        if mem is None:
            return f"未找到 {target_date} 的日报"
        return _format_memory(mem)

    @mcp.resource("neurun://context/full")
    def get_full_context() -> str:
        """【全面分析时用】完整训练上下文包。包含：最新日报全文 + 前7天恢复/睡眠/训练趋势 +
        活跃训练目标 + 个人竞技档案。当用户问"最近一周"、"整体状态"、"趋势如何"时使用。"""
        parts = []

        # 最新日报
        mem = memory_store.get_latest("daily_report")
        if mem:
            parts.append(f"# 最新日报 ({mem.id})\n{_format_memory(mem)}")

        # 前 7 天摘要直接读取 SQLite，不依赖是否生成过历史日报。
        parts.append("\n# 前 7 天数据")
        entries = memory_store.get_training_history_entries(
            user_id=user_id, days=7,
        )
        for entry in entries:
            state = entry.get("activity_state")
            if state == ActivityDayState.CONFIRMED_REST:
                types = "已确认休息"
            elif state == ActivityDayState.UNKNOWN:
                types = "运动数据未同步"
            else:
                types = "、".join(entry.get("training_types", [])) or "训练"
            parts.append(
                f"- {entry['date']}: {types} "
                f"{entry.get('duration', 0)}min {entry.get('distance', 0):.1f}km "
                f"| 恢复 {entry.get('recovery') or '?'} "
                f"| 睡眠 {entry.get('sleep_h', 0)}h"
            )

        # 活跃目标
        goals = memory_store.list_by_type("goal", status="active")
        if goals:
            parts.append("\n# 活跃目标")
            for g in goals:
                fm = g.front_matter
                parts.append(
                    f"- **{g.id}**: {fm.get('metrics', {})}"
                )

        # 个人资料
        profile = memory_store.get("fitness-assessment")
        if profile:
            parts.append(f"\n# 个人资料\n{profile.body[:500]}")

        return "\n".join(parts)

    @mcp.resource("neurun://goals/active")
    def get_active_goals() -> str:
        """进行中的训练目标。"""
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return "暂无活跃目标"
        return "\n\n".join(
            f"# {g.id}\n{g.body[:300]}" for g in goals
        )

    @mcp.resource("neurun://profile")
    def get_profile() -> str:
        """个人竞技档案。"""
        mem = memory_store.get("fitness-assessment")
        if mem is None:
            return "暂无档案，请运行 neurun setup"
        return _format_memory(mem)

    @mcp.resource("neurun://preferences")
    def get_preferences() -> str:
        """训练偏好。"""
        mem = memory_store.get("preferences")
        if mem is None:
            return "暂无偏好设置"
        return _format_memory(mem)

    # ═══════════════════════════════════════════════════════
    # Tools: AI 可调用的查询/分析函数
    # ═══════════════════════════════════════════════════════

    if enable_admin_tools:
        from .invitations import InvitationStore

        invitation_store = InvitationStore(config.invite_codes_path)

        @mcp.tool()
        def invite_create(count: int = 1) -> str:
            """【仅本地管理员】生成 6 位随机一次性邀请码；返回 ID 和掩码，完整邀请码须用本地 CLI 查看。"""
            records = [item.to_admin_dict() for item in invitation_store.create(count)]
            return json.dumps(records, ensure_ascii=False)

        @mcp.tool()
        def invite_list() -> str:
            """【仅本地管理员】列出邀请码状态；不会返回完整邀请码。"""
            records = [item.to_admin_dict() for item in invitation_store.list_all()]
            return json.dumps(records, ensure_ascii=False)

        @mcp.tool()
        def invite_show(invitation_id: str) -> str:
            """【仅本地管理员】查看单个邀请码的掩码与状态；不会返回完整邀请码。"""
            record = invitation_store.get(invitation_id).to_admin_dict()
            return json.dumps(record, ensure_ascii=False)

        @mcp.tool()
        def invite_revoke(invitation_id: str) -> str:
            """【仅本地管理员】停用一个邀请码。"""
            record = invitation_store.revoke(invitation_id).to_admin_dict()
            return json.dumps(record, ensure_ascii=False)

    @mcp.tool()
    def authenticate_provider() -> str:
        """认证当前运动数据 Provider；Huawei 使用每用户的 GROUP_PALS_TOKEN 获取 AT。"""
        try:
            return "认证成功" if provider.authenticate() else "认证失败，请检查账号配置"
        except Exception as exc:
            return f"认证失败: {exc}"

    @mcp.tool()
    def query_activities(days: int = 7) -> str:
        """查询最近 N 天的活动列表（含距离、时长、心率、负荷）。"""
        activities = storage.get_recent_activities(user_id, days)
        if not activities:
            return "暂无活动数据"
        lines = [f"最近 {days} 天活动 ({len(activities)} 条):"]
        for a in activities:
            dur = (a.get("duration_seconds", 0) or 0) / 60
            dist = (a.get("distance_meters", 0) or 0) / 1000
            lines.append(
                f"- {a.get('activity_date', '?')}: {a.get('activity_name', '?')} "
                f"{dur:.0f}min {dist:.1f}km HR{a.get('avg_heart_rate', '?')} "
                f"load{a.get('training_load', 0)}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def query_current_week_progress(target_date: str = "") -> str:
        """查询目标日期所在自然周（周一至周日）截至当日的实际训练进度。"""
        try:
            target = (
                date.fromisoformat(target_date)
                if target_date
                else date.today()
            )
        except ValueError:
            return "日期格式错误，请使用 YYYY-MM-DD"
        monday, sunday = natural_week_bounds(target)
        entries = memory_store.get_training_history_entries(
            user_id=user_id,
            days=(target - monday).days + 1,
            end_date=target,
        )
        training = [
            entry for entry in entries
            if entry.get("activity_state") == ActivityDayState.TRAINING
        ]
        unknown = sum(
            entry.get("activity_state") == ActivityDayState.UNKNOWN
            for entry in entries
        )
        total_km = sum(float(entry.get("distance", 0) or 0) for entry in training)
        total_minutes = sum(
            float(entry.get("duration", 0) or 0) for entry in training
        )
        lines = [
            f"自然周 {monday} 至 {sunday}，截至 {target}：",
            f"- 已完成 {len(training)} 次，{total_km:.1f}km，{total_minutes:.0f}min",
        ]
        if unknown:
            lines.append(f"- {unknown} 天运动数据状态未知，不计为休息日")
        return "\n".join(lines)

    @mcp.tool()
    def query_health_metrics(days: int = 7) -> str:
        """查询最近 N 天的健康指标（睡眠、HRV、心率、身体电量等）。"""
        metrics = storage.get_health_metrics_range(user_id, days)
        if not metrics:
            return "暂无健康数据"
        lines = [f"最近 {days} 天健康指标:"]
        for m in metrics:
            lines.append(
                f"- {m.get('metric_date', '?')}: "
                f"睡眠 {m.get('sleep_duration_hours', '?')}h "
                f"RHR {m.get('resting_heart_rate', '?')} "
                f"HRV {m.get('hrv_last_night_avg', '?')}ms "
                f"电量 {m.get('body_battery_high', '?')} "
                f"准备 {m.get('training_readiness_score', '?')}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def get_activity_detail(activity_id: str) -> str:
        """获取单条活动的详细技术数据。包含：每公里/分段配速、步频变化、功率输出（平均/标准化）、
        触地时间(ms)、步幅(cm)、垂直振幅(mm)、心率区间、爬升、训练效果标签。
        用户问"配速"、"步频"、"功率"、"技术分析"、"分段数据"时调用。
        需要先通过 query_activities 获取 activity_id。"""
        from .activity import get_activity_splits, get_activity_detail
        from .training_analysis import get_latest_training_analysis, display_name
        detail = get_activity_detail(storage, activity_id)
        splits = get_activity_splits(storage, activity_id)
        if not detail:
            return f"未找到活动 {activity_id}"
        lines = [
            f"活动: {detail.get('activityName', '?')}",
            f"距离: {(detail.get('summaryDTO', {}).get('distance', 0) or 0)/1000:.1f}km",
            f"时长: {(detail.get('summaryDTO', {}).get('duration', 0) or 0)/60:.0f}min",
        ]
        analysis = get_latest_training_analysis(storage, user_id, activity_id)
        if analysis:
            lines.extend([
                f"训练内容: {display_name(analysis['primary_type'], analysis['terrain'])}",
                f"识别置信度: {analysis['confidence']:.0%} "
                f"({analysis['algorithm_version']})",
            ])
            lines.extend(f"识别依据: {item}" for item in analysis["evidence"])
            lines.extend(
                f"后续影响: {item}"
                for item in analysis["training_implications"]
            )
        for s in splits:
            dist = (s.get("distance_m") or 0) / 1000
            pace = s.get("pace_per_km")
            pace_str = f"{int(pace//60)}:{int(pace%60):02d}/km" if pace else "—"
            lines.append(
                f"  {dist:.1f}km {pace_str} "
                f"HR{s.get('avg_hr', '?')} cad{s.get('avg_cadence', '?')}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def search_memories(keyword: str) -> str:
        """全文搜索记忆库。"""
        results = memory_store.search(keyword)
        if not results:
            return f"未找到包含 '{keyword}' 的记忆"
        lines = [f"搜索 '{keyword}' 找到 {len(results)} 条:"]
        for m in results[:10]:
            lines.append(f"- [{m.type.value}] {m.id}: {m.body[:100]}...")
        return "\n".join(lines)

    @mcp.tool()
    def generate_image(target_date: str = "", theme: str = "fresh") -> str:
        """将指定日期的 HTML 日报截图导出为 PNG 图片。路径 output/{date}.png。
        theme: fresh(清新默认) | sport(运动橙) | dark(暗黑)。
        用户说"生成图片"、"截图"、"分享"、"导出图片"时调用。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        mem = memory_store.get(str(d))
        if mem is None:
            mem = memory_store.generate_daily_report(str(user_id), d)

        from .image import render_daily_image
        out = f"output/{d}.png"
        ensure_private_dir(Path(out).parent)
        png_path = render_daily_image(mem, output_path=out, theme=theme)
        restrict_private_file(png_path)
        return f"✅ PNG 已生成: {png_path} (theme={theme})"

    @mcp.tool()
    def get_training_advice() -> str:
        """基于最新日报生成训练建议。"""
        mem = memory_store.get_latest("daily_report")
        if mem is None:
            return "暂无日报数据"
        fm = mem.front_matter
        rec = fm.get("recommendation", {})
        ai = fm.get("ai_insight", {})
        lines = [
            f"训练建议 ({mem.id}):",
            f"强度: {rec.get('intensity', '?')}",
            f"建议: {rec.get('training_advice', '?')}",
        ]
        if ai:
            lines.append(f"\nAI 洞察: {ai.get('conclusion', '')}")
            for obs in ai.get("observations", []):
                lines.append(f"  • {obs}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════
    # Write Tools: AI 可以写入和触发操作
    # ═══════════════════════════════════════════════════════

    @mcp.tool()
    def update_profile(
        height_cm: int = 0,
        weight_kg: int = 0,
        age: int = 0,
        gender: str = "",
        location: str = "",
        pb_5k: str = "",
        pb_10k: str = "",
        pb_hm: str = "",
        pb_marathon: str = "",
    ) -> str:
        """更新个人基本资料和最佳成绩。留空的字段保持原值不变。"""
        from datetime import datetime
        from .memory import build_memory_file

        # 读取现有资料
        existing = memory_store.get("fitness-assessment")
        existing_fm = existing.front_matter if existing else {}
        existing_info = existing_fm.get("personal_info", {})
        existing_pb = existing_fm.get("personal_bests", {})

        # 合并
        info = {
            "height_cm": height_cm or existing_info.get("height_cm"),
            "weight_kg": weight_kg or existing_info.get("weight_kg"),
            "age": age or existing_info.get("age"),
            "gender": gender or existing_info.get("gender", ""),
            "location": location or existing_info.get("location", ""),
        }
        pbs = dict(existing_pb)
        if pb_5k: pbs["5k"] = {"time": pb_5k}
        if pb_10k: pbs["10k"] = {"time": pb_10k}
        if pb_hm: pbs["half_marathon"] = {"time": pb_hm}
        if pb_marathon: pbs["marathon"] = {"time": pb_marathon}

        fm = {
            "type": "fitness_profile",
            "profile_type": "assessment",
            "updated": datetime.now().isoformat(timespec="seconds"),
            "personal_info": info,
            "personal_bests": pbs,
            "tags": ["fitness-profile", str(date.today().year)],
        }

        body_parts = [
            "# 竞技档案",
            f"\n## 基本信息",
            f"- 身高: {info['height_cm'] or '?'} cm",
            f"- 体重: {info['weight_kg'] or '?'} kg",
            f"- 年龄: {info['age'] or '?'}",
        ]
        if info.get("gender"): body_parts.append(f"- 性别: {info['gender']}")
        if info.get("location"): body_parts.append(f"- 地点: {info['location']}")

        body_parts.append("\n## 个人最佳")
        for dist, data in pbs.items():
            if isinstance(data, dict) and data.get("time"):
                body_parts.append(f"- **{dist}**: {data['time']}")

        body = "\n".join(body_parts)
        path = Path(config.memory_dir) / "profile" / "fitness-assessment.md"
        atomic_write_private(path, build_memory_file(fm, body))
        logger.info("Profile updated via MCP")
        return f"✅ 个人资料已更新。身高 {info['height_cm']}cm 体重 {info['weight_kg']}kg，最佳: {list(pbs.keys())}"

    @mcp.tool()
    def set_goal(
        name: str,
        distance: str,
        target_time: str,
        target_date: str = "",
        weekly_km: int = 50,
    ) -> str:
        """创建或更新训练目标。"""
        from datetime import datetime
        from .memory import build_memory_file

        if not target_date:
            target_date = str(date.today().replace(year=date.today().year + 1))

        goal_id = f"goal-{date.today().year}-{distance}"
        fm = {
            "type": "goal",
            "id": goal_id,
            "goal_type": "time_based",
            "category": "running",
            "status": "active",
            "priority": "high",
            "created": str(date.today()),
            "target_date": target_date,
            "review_cycle": "weekly",
            "metrics": {
                f"target_{distance}": target_time,
                "weekly_mileage_km": weekly_km,
            },
            "tags": [distance, str(date.today().year), "active"],
        }
        body = f"""# {name}

## 目标
- 距离: {distance}
- 目标成绩: {target_time}
- 截止日期: {target_date}
- 周跑量: {weekly_km} km

## 进度
创建于 {date.today()}。
"""

        path = Path(config.memory_dir) / "goals" / "active" / f"{goal_id}.md"
        atomic_write_private(path, build_memory_file(fm, body))
        logger.info("Goal created via MCP: %s", goal_id)
        return f"✅ 目标已创建: {name} — {distance} {target_time} (截止 {target_date})"

    @mcp.tool()
    def generate_report(target_date: str = "") -> str:
        """生成指定日期的运动日报（默认今天）。用户说"生成日报"、"帮我看看今天的报告"、
        "分析一下昨天的训练"时调用。成功后返回训练/睡眠/恢复/HRV的摘要数据。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        mem = memory_store.generate_daily_report(str(user_id), d)
        fm = mem.front_matter
        ya = fm.get("yesterday_activities", {})
        sleep = fm.get("last_night_sleep", {})
        rec = fm.get("recovery", {})

        return (
            f"✅ 日报已生成: {d}\n"
            f"训练: {ya.get('day_type', 'rest')} "
            f"{ya.get('total_duration_min', 0)}min "
            f"{ya.get('total_distance_km', 0):.1f}km\n"
            f"睡眠: {sleep.get('total_hours', '?')}h {sleep.get('quality', '?')}\n"
            f"恢复: {rec.get('overall_score', '?')}/100 {rec.get('level', '?')}\n"
            f"HRV: {fm.get('this_morning', {}).get('hrv_ms', '?')}ms "
            f"RHR: {fm.get('this_morning', {}).get('resting_hr', '?')}"
        )

    @mcp.tool()
    def generate_html_report(target_date: str = "") -> str:
        """生成静态 HTML 运动日报到 output/ 目录。绿黑色潮流风格，包含状态面板、
        训练卡片、ACWR可视化、SVG趋势图、AI洞察。浏览器直接打开，无需服务器。
        用户说"生成HTML"、"导出日报"、"给我一个网页版"时调用。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        # 确保日报存在
        mem = memory_store.get(str(d))
        if mem is None:
            mem = memory_store.generate_daily_report(str(user_id), d)

        from .render import render_daily_html
        output_dir = Path("output")
        ensure_private_dir(output_dir)
        output_path = str(output_dir / f"{d}.html")
        render_daily_html(mem, output_path)
        return f"✅ HTML 日报已生成: {output_path}"

    return mcp


def _format_memory(mem) -> str:
    """格式化 Memory 为文本。"""
    fm = mem.front_matter
    parts = [mem.body]

    # 附加关键 Front Matter 数据
    ya = fm.get("yesterday_activities", {})
    if ya.get("activity_state") == ActivityDayState.UNKNOWN:
        parts.append("\n运动数据: 未同步，训练/休息状态未知")
    elif ya and not ya.get("is_rest_day"):
        parts.append(
            f"\n数据: {ya.get('total_duration_min', 0)}min "
            f"{ya.get('total_distance_km', 0):.1f}km "
            f"负荷 {ya.get('total_training_load', 0)}"
        )

    rec = fm.get("recovery", {})
    if rec:
        parts.append(f"恢复: {rec.get('overall_score', '?')}/100 {rec.get('level', '?')}")

    return "\n".join(parts)
