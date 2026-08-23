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
from .memory import get_daily_activities
from .report_readiness import (
    DailyReportReadinessError,
    DailyReportReadinessService,
    enforce_report_readiness,
)
from .training import TrainingError, TrainingService
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
- **每日综合报告**：包含报告日训练详情（分段配速、步频、功率、心率、触地时间）、昨夜睡眠质量、今晨恢复状态（HRV、静息心率、身体电量）、训练负荷（ACWR）、7日趋势、异常检测、当日训练建议。
- **训练细节分析**：每项活动的分段数据，包括配速变化、步频、功率、触地时间、步幅、垂直振幅、爬升等。
- **历史查询**：前30天的活动列表、健康指标趋势、任意日期的日报。
- **静态HTML报告**：可生成三主题（运动/清新/暗黑）完整HTML日报，包含趋势图，浏览器直接打开。

## 何时主动触发
- 用户提到"今天状态"、"当日训练"、"睡眠"、"恢复"、"HRV"、"跑步数据"→ 读取 `neurun://daily/latest`
- 用户问"最近一周"、"趋势"、"负荷"、"训练量" → 读取 `neurun://context/full`
- 用户问"活动详情"、"配速"、"步频"、"功率"、"分段" → 调用 `get_activity_detail`
- 用户说"生成报告"、"日报"、"HTML" → 调用 `generate_report` 或 `generate_html_report`
- 用户说"生成 HTML"、"导出报告" → 调用 `generate_html_report`
- 用户问"目标"、"5K"、"备赛"、"PB" → 读取 `neurun://goals/active`
- 用户要"更新资料"、"输入身高体重" → 调用 `update_profile`；训练目标由训练方案建立向导统一管理
- 用户问"今天练什么"、"本周安排"、"训练方案" → 调用 `get_training_home`
- 用户反馈时间、疲劳、疼痛或日程约束 → 先调用 `submit_training_feedback` 和 `propose_training_adjustment`；只有用户明确确认后才调用 `approve_training_adjustment`

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

    def generate_local_report(d: date, mode: str = "complete"):
        readiness = DailyReportReadinessService(
            storage, provider_type=config.provider_type,
        ).check(user_id, d)
        enforce_report_readiness(readiness, mode)
        from .training_service_factory import build_capacity_athlete_context

        athlete_context = build_capacity_athlete_context(
            config, d, omitted_sections=readiness.omitted_sections,
        )
        return memory_store.generate_daily_report(
            str(user_id),
            d,
            ai_insight={} if readiness.omitted_sections else None,
            readiness=readiness.to_dict(),
            athlete_context=athlete_context,
        )

    # ═══════════════════════════════════════════════════════
    # Resources: 只读数据，自动注入 AI 上下文
    # ═══════════════════════════════════════════════════════

    @mcp.resource("neurun://daily/latest")
    def get_latest_daily() -> str:
        """【最常用】最新每日综合报告。包含：报告日训练详情（类型/时长/距离/配速/心率/负荷）、
        昨夜睡眠（时长/质量/深睡占比）、今晨恢复状态（HRV/静息心率/身体电量/训练准备）、
        训练负荷ACWR、运动员能力背景参考（可持续周跑量/长距离/参考配速）、7日趋势、异常提醒、
        报告日训练建议、AI教练洞察。"""
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

    def current_training_service() -> TrainingService:
        memory_dir = getattr(config, "memory_dir", None)
        if not memory_dir:
            memory_dir = getattr(getattr(memory_store, "reader", None), "_root", "memory")
        return TrainingService(memory_dir)

    def training_json(action) -> str:
        try:
            return json.dumps({"status": "ok", "data": action()}, ensure_ascii=False)
        except TrainingError as exc:
            return json.dumps({
                "status": "error", "code": exc.code, "message": str(exc),
                "suggestion": "刷新训练方案后，按提示补全信息或重新生成提案。",
            }, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return json.dumps({
                "status": "error", "code": "invalid_input", "message": str(exc),
            }, ensure_ascii=False)
        except Exception as exc:
            logger.exception("训练工具执行失败")
            return json.dumps({
                "status": "error", "code": "training_internal_error",
                "message": "训练服务暂时不可用，请稍后重试。",
            }, ensure_ascii=False)

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
    def get_training_home() -> str:
        """读取实时训练首页：当前方案、今日/本周逐段处方（组数、工作/恢复段和分段目标）、个人配速依据、执行状态和待确认提案。"""
        return training_json(lambda: current_training_service().home())

    @mcp.tool()
    def get_training_plan(target_date: str = "") -> str:
        """读取实时方案；提供日期时返回当时生效版本及含 Workout Steps 的具体课次。"""
        target = date.fromisoformat(target_date) if target_date else None
        return training_json(
            lambda: current_training_service().plan(target_date=target)
        )

    @mcp.tool()
    def get_training_session_brief(target_date: str = "") -> str:
        """读取指定日期训练前说明；提供组数、快慢段顺序、分段配速/体感、降级和停止条件，不修改方案。"""
        target = date.fromisoformat(target_date) if target_date else date.today()
        return training_json(
            lambda: current_training_service().session_brief(target=target)
        )

    @mcp.tool()
    def get_athlete_capacity_profile(target_date: str = "") -> str:
        """读取能力档案：近期可持续能力、用户确认历史能力、同步覆盖与首周负荷边界。历史高峰不会直接作为首周跑量。"""
        target = date.fromisoformat(target_date) if target_date else date.today()
        return training_json(lambda: current_training_service().capacity_profile(target=target))

    @mcp.tool()
    def preview_training_activation(
        plan_id: str,
        start_mode: str = "today",
        effective_from: str = "",
        entry_strategy: str = "recommended",
    ) -> str:
        """只读预览草稿的启用日期、衔接周、同步事实截点和入门策略；不会激活方案或转换教练模式。"""
        payload = {"start_mode": start_mode, "entry_strategy": entry_strategy}
        if effective_from:
            payload["effective_from"] = effective_from
        return training_json(lambda: current_training_service().activation_preview(plan_id, payload))

    @mcp.tool()
    def cancel_training_activation(plan_id: str) -> str:
        """取消尚未生效的排期，并将同一方案退回预览草稿；不会创建新方案。"""
        return training_json(
            lambda: current_training_service().cancel_scheduled_activation(plan_id)
        )

    @mcp.tool()
    def preview_goal_rescheduling(target_date: str, target_time: str = "") -> str:
        """生成不生效的目标改期预览与替代草稿；不会改写当前目标或方案。"""
        return training_json(lambda: current_training_service().preview_goal_rescheduling({
            "target_date": target_date,
            "target_time": target_time or None,
        }))

    @mcp.tool()
    def confirm_goal_rescheduling(preview_id: str, idempotency_key: str) -> str:
        """确认仍有效的改期预览，废弃旧方案并返回同一 plan_id 的新草稿。"""
        return training_json(lambda: current_training_service().confirm_goal_rescheduling(
            preview_id, idempotency_key=idempotency_key,
        ))

    @mcp.tool()
    def prepare_race_strategy(
        course: str = "", weather: str = "", fueling_experience: str = "",
    ) -> str:
        """在已确认赛事方案的赛前 21 天内生成比赛策略；缺失事实会显式保留为不确定性。"""
        return training_json(lambda: current_training_service().race_strategy({
            "course": course,
            "weather": weather,
            "fueling_experience": fueling_experience,
        }))

    @mcp.tool()
    def submit_training_feedback(
        feedback_type: str,
        target_date: str = "",
        note: str = "",
        available_minutes: int = 0,
        affected_dates: list[str] | None = None,
        new_available_days: list[int] | None = None,
        planned_workout_id: str = "",
        completion_status: str = "",
        idempotency_key: str = "",
        pain_location: str = "",
        pain_severity: int = 0,
        pain_affects_daily_life: bool = False,
    ) -> str:
        """记录训练反馈但不修改方案。feedback_type 使用 constraint_change、fatigue、pain、post_workout；长期星期变化填写 new_available_days。疼痛必须提供部位和 1–10 严重程度；训练后只有明确 skipped 才能写入。"""
        payload = {
            "feedback_type": feedback_type,
            "target_date": target_date or str(date.today()),
            "note": note,
            "available_minutes": available_minutes or None,
            "affected_dates": affected_dates,
            "new_available_days": new_available_days,
            "planned_workout_id": planned_workout_id or None,
            "completion_status": completion_status or None,
            "idempotency_key": idempotency_key or None,
            "pain": {
                "location": pain_location,
                "severity": pain_severity or None,
                "affects_daily_life": pain_affects_daily_life,
            } if feedback_type == "pain" else {},
        }
        return training_json(
            lambda: current_training_service().submit_feedback(payload)
        )

    @mcp.tool()
    def propose_training_adjustment(feedback_id: str) -> str:
        """根据一条已记录反馈生成结构化待确认提案；不会改写当前训练方案。"""
        return training_json(
            lambda: current_training_service().propose(feedback_id)
        )

    @mcp.tool()
    def propose_training_adjustment_from_report(
        week_id: str, effective_from: str = "",
    ) -> str:
        """将已归档周复盘的加速/降载/重规划建议转成训练域待确认方案提案；不会直接生效。"""
        return training_json(
            lambda: current_training_service().propose_from_weekly_report(
                week_id,
                effective_from=(date.fromisoformat(effective_from) if effective_from else None),
            )
        )

    @mcp.tool()
    def propose_training_scheme_revision(
        reason: str,
        trigger: str = "execution_deviation",
        available_days: list[int] | None = None,
        max_session_minutes: int = 0,
    ) -> str:
        """为连续偏离、长期停训、目标或固定日程变化生成完整方案重规划提案；不会直接生效。"""
        constraints = {}
        if available_days is not None:
            constraints["available_days"] = available_days
        if max_session_minutes:
            constraints["max_session_minutes"] = max_session_minutes
        return training_json(
            lambda: current_training_service().propose_scheme_revision({
                "reason": reason,
                "trigger": trigger,
                "constraints": constraints,
            })
        )

    @mcp.tool()
    def approve_training_adjustment(
        proposal_id: str,
        base_version: int,
        idempotency_key: str,
    ) -> str:
        """仅在用户明确确认后，按 base_version 批准提案并生成新方案版本；禁止自动调用。"""
        return training_json(lambda: current_training_service().approve(
            proposal_id,
            base_version=base_version,
            idempotency_key=idempotency_key,
        ))

    @mcp.tool()
    def reject_training_adjustment(proposal_id: str, reason: str = "") -> str:
        """拒绝待确认提案并保持当前训练方案不变。"""
        return training_json(
            lambda: current_training_service().reject(proposal_id, reason=reason)
        )

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
    def generate_report(target_date: str = "", mode: str = "complete") -> str:
        """生成指定日期的运动日报（默认今天）。用户说"生成日报"、"帮我看看今天的报告"、
        "分析指定日期的训练"时调用。mode 默认 complete；只有用户明确接受数据缺口时才用 limited。
        成功后返回训练/睡眠/恢复/HRV的摘要数据。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        try:
            mem = generate_local_report(d, mode)
        except DailyReportReadinessError as exc:
            raise RuntimeError(json.dumps(exc.to_dict(), ensure_ascii=False)) from exc
        fm = mem.front_matter
        ya = get_daily_activities(fm)
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
    def generate_html_report(
        target_date: str = "", mode: str = "complete",
    ) -> str:
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
            try:
                mem = generate_local_report(d, mode)
            except DailyReportReadinessError as exc:
                raise RuntimeError(json.dumps(exc.to_dict(), ensure_ascii=False)) from exc

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
    ya = get_daily_activities(fm)
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
