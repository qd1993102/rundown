"""训练服务装配与日报能力背景构造。

Web 草稿/训练页、CLI 日报、MCP 报告共用同一套活动加载器与上下文装配，
避免三入口维护两套口径。日报侧通过 ``build_capacity_athlete_context``
只读调用训练域 ``capacity_profile(D)`` 并投影 ``athlete_context``，
不重复实现能力计算，也不向训练域写入任何内容。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .memory import MemoryStore, MemoryType, load_platform_thresholds
from .storage import Storage
from .training import TrainingService


def _running_distances(
    items: list[dict], start: date, end: date,
) -> list[float]:
    """返回窗口内跑步活动距离（km）。长距离能力指标与周量共用，
    避免把单周窗口当成个人长距离能力。"""
    result: list[float] = []
    for item in items:
        raw_date = item.get("activity_date") or item.get("date")
        try:
            activity_date = date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            continue
        if not (start <= activity_date <= end):
            continue
        if (
            "run" in str(item.get("activity_type") or "").lower()
            or "跑" in str(item.get("activity_name") or "")
        ):
            result.append(float(item.get("distance_meters") or 0) / 1000)
    return result


def _load_platform_thresholds_for_config(config: Any, storage_factory: Any) -> Callable[[date], dict[str, Any]]:
    """平台自算乳酸阈值加载器：按目标日期读 daily_health_metrics 的 lthr/ltsp。"""
    def load(target: date) -> dict[str, Any]:
        if not Path(config.db_path).exists():
            return {}
        storage = storage_factory(config)
        try:
            user_id = storage.get_local_user_id()
            if user_id is None:
                return {}
            return load_platform_thresholds(storage.db, user_id, target)
        finally:
            storage.close()
    return load


def build_training_service(
    config: Any,
    *,
    scheme_planner: Any = None,
    storage_factory: Any = None,
) -> TrainingService:
    """构建只访问当前配置目录的训练服务（与草稿流程同口径）。

    活动加载与上下文装配从 Web 路由中固化为共享实现；``scheme_planner``
    仅 Web 训练页需要，日报/报告只读场景可省略。``storage_factory`` 供
    Web 传入自身 ``Storage`` 引用（保持既有测试注入点与行为一致）。
    """
    storage_factory = storage_factory or Storage

    def load_week(start: date, end: date) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not Path(config.db_path).exists():
            return [], {}
        storage = storage_factory(config)
        try:
            user_id = storage.get_local_user_id()
            if user_id is None:
                return [], {}
            activities = storage.get_activities_range(user_id, start, end)
            fixed_week_activities: list[dict[str, Any]] = []
            for item in activities:
                raw_date = item.get("activity_date") or item.get("date")
                try:
                    activity_date = date.fromisoformat(str(raw_date)[:10])
                except (TypeError, ValueError):
                    continue
                if start <= activity_date <= end:
                    fixed_week_activities.append(item)
            running_activities = [
                item for item in fixed_week_activities
                if "run" in str(item.get("activity_type") or "").lower()
                or "跑" in str(item.get("activity_name") or "")
            ]
            history = MemoryStore(
                config.memory_dir, db_getter=lambda: storage.db,
            ).get_training_history_entries(
                user_id=user_id, days=(end - start).days + 1, end_date=end,
            )
            from .training_analysis import (
                display_name,
                get_latest_training_analysis,
            )
            from .activity import get_activity_summary_facts
            for activity in activities:
                analysis = get_latest_training_analysis(
                    storage.db, user_id, str(activity.get("activity_id") or ""),
                )
                if analysis:
                    activity["training_analysis"] = {
                        **analysis,
                        "display_name": display_name(
                            analysis["primary_type"], analysis["terrain"],
                        ),
                    }
                # 统一来源：草稿/周报/训练首页与日报消费同一份 activity_summary_facts
                # （session-summary），由 TrainingDaySummaryBuilder 透传；不重复计算。
                try:
                    summary_facts = get_activity_summary_facts(
                        storage.db, str(activity.get("activity_id") or ""),
                    )
                except Exception:
                    summary_facts = None
                if summary_facts:
                    activity["session_summary"] = summary_facts
            states = {
                str(item["date"]): str(item.get("activity_state") or "unknown")
                for item in history
            }
            return activities, states
        finally:
            storage.close()

    def load_setup(target: date | None = None) -> dict[str, Any]:
        baseline = {
            "coverage": "unknown", "window_days": 28,
            "activity_count": 0, "distance_km": 0,
            "longest_distance_km": 0,
        }
        known_constraints: dict[str, Any] = {}
        memory_store = MemoryStore(config.memory_dir)
        athlete_profile: dict[str, Any] = {}
        recovery_snapshot: dict[str, Any] = {}
        profile = memory_store.get("fitness-assessment")
        if profile:
            athlete_profile = dict(profile.front_matter)
        reports = memory_store.list_by_type(MemoryType.DAILY_REPORT)
        eligible_reports = [
            report for report in reports
            if target is None or (
                report.created_date is not None
                and report.created_date <= target
            )
        ]
        latest_report = max(
            eligible_reports,
            key=lambda report: report.created_date or date.min,
            default=None,
        )
        if latest_report:
            latest_fm = latest_report.front_matter
            recovery_snapshot = {
                "date": latest_fm.get("date"),
                "recovery": latest_fm.get("recovery") or {},
                "morning": latest_fm.get("morning") or {},
                "sleep": latest_fm.get("sleep") or {},
                "training_load": latest_fm.get("training_load") or {},
            }

        def result() -> dict[str, Any]:
            return {
                "baseline": baseline,
                "known_constraints": known_constraints,
                "athlete_profile": athlete_profile,
                "recovery_snapshot": recovery_snapshot,
            }

        preferences = memory_store.get("preferences")
        if preferences:
            training_preferences = (
                preferences.front_matter.get("training_preferences") or {}
            )
            preferred_terrain = training_preferences.get("preferred_terrain") or []
            if isinstance(preferred_terrain, list) and preferred_terrain:
                known_constraints["preferred_terrain"] = preferred_terrain[0]
            injuries = preferences.front_matter.get("injury_history") or []
            if injuries:
                known_constraints["medical_limitations"] = str(
                    injuries[0].get("description") or ""
                )
        if not Path(config.db_path).exists():
            return result()
        storage = storage_factory(config)
        try:
            user_id = storage.get_local_user_id()
            if user_id is None:
                return result()
            # 草稿基线只读取上一完整自然周作为起始周量；当前周/最近 7 天属于
            # 动态执行事实，不能抬高新草稿的起始周量。长距离能力指标则取近 28 天
            # 窗口（与 AthleteBaselineBuilder 一致），避免把单周窗口当成个人
            # 长距离能力——例如本周刚完成的 30km 长距离不能被上周窗口漏掉。
            as_of = target or date.today()
            current_monday = as_of - timedelta(days=as_of.weekday())
            end = current_monday - timedelta(days=1)
            start = end - timedelta(days=6)
            long_start = as_of - timedelta(days=28)
            activities = storage.get_activities_range(user_id, long_start, as_of)
            calendar = storage.get_sync_calendar(user_id, start, end, today=end)
            calendar_counts = calendar.get("summary") or calendar.get("counts") or {}
            synced_days = int(calendar_counts.get("synced") or 0)
            coverage = (
                "sufficient" if synced_days >= 5
                else "partial" if synced_days else "unknown"
            )
            week_distances = _running_distances(activities, start, end)
            long_distances = _running_distances(activities, long_start, as_of)
            previous_week_km = round(sum(week_distances), 1)
            baseline = {
                "coverage": coverage, "window_days": 7,
                "reference_window_kind": "previous_completed_natural_week",
                "reference_window_start": str(start),
                "reference_window_end": str(end),
                "activity_count": len(week_distances),
                "distance_km": previous_week_km,
                # 长距离能力：近 28 天最长跑步距离（非上一周）
                "longest_distance_km": round(max(long_distances, default=0), 1),
                "previous_week_km": previous_week_km,
                # 兼容旧读模型字段，但值与上一完整自然周一致，不代表滚动 7 天。
                "average_weekly_km": previous_week_km,
                "recent_7d_km": 0,
            }
            return result()
        finally:
            storage.close()

    return TrainingService(
        config.memory_dir,
        activity_loader=load_week,
        setup_context_loader=load_setup,
        scheme_planner=scheme_planner,
        platform_threshold_loader=_load_platform_thresholds_for_config(
            config, storage_factory,
        ),
    )


def project_capacity_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """把训练域能力画像投影为日报 Front Matter 的稳定字段。

    0 值/空值转为 None，避免把“没有历史事实”展示成能力数值。
    """
    current = profile.get("current_sustainable_capacity") or {}
    return {
        "as_of": profile.get("as_of"),
        "facts_cutoff": profile.get("facts_cutoff"),
        "sync_coverage": profile.get("sync_coverage"),
        "confidence": profile.get("confidence"),
        "current_sustainable_capacity": {
            "weekly_km": current.get("weekly_km") or None,
            "long_run_km": current.get("long_run_km") or None,
            "recent_running_pace_sec_per_km": current.get(
                "recent_running_pace_sec_per_km"
            ),
            "pace_sample_count": current.get("pace_sample_count") or 0,
            "observed_weeks": current.get("observed_weeks") or 0,
        },
        "historical_proven_capacity": (
            profile.get("historical_proven_capacity") or {}
        ),
        "entry_load_envelope": profile.get("entry_load_envelope") or {},
        "current_readiness": profile.get("current_readiness") or {},
    }


def build_capacity_athlete_context(
    config: Any,
    target: date,
    *,
    omitted_sections: Any = (),
) -> dict[str, Any]:
    """日报侧只读调用训练域能力画像并投影为 ``athlete_context``。

    门禁：``training_load`` 被省略的受限版不读取训练域；配置缺失或读取失败
    返回显式 ``unavailable``，不阻断日报生成，也不编造能力数值。
    """
    source = "training_domain_capacity_profile"
    omitted = {str(item) for item in (omitted_sections or ())}
    if "training_load" in omitted:
        return {
            "status": "unavailable",
            "reason": "training_load_omitted",
            "source": source,
        }
    if not getattr(config, "memory_dir", None) or not getattr(config, "db_path", None):
        return {"status": "unavailable", "reason": "not_loaded", "source": source}
    try:
        profile = build_training_service(config).capacity_profile(target=target)
    except Exception:
        return {
            "status": "unavailable",
            "reason": "capacity_load_error",
            "source": source,
        }
    projected = project_capacity_profile(profile)
    return {
        "status": "available",
        "source": source,
        "facts_cutoff": projected.get("facts_cutoff"),
        "capacity_profile": projected,
    }
