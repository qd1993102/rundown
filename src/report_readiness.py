"""日报生成前的数据完整性门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal


CoverageStatus = Literal[
    "complete",
    "unsupported",
    "missing",
    "syncing",
    "failed",
    "insufficient_history",
]
ReadinessStatus = Literal["ready", "limited", "blocked"]
ReportFinality = Literal["provisional", "final"]
ReportMode = Literal["complete", "limited"]


@dataclass(frozen=True)
class CoverageDimension:
    """一个报告数据维度的覆盖证据。"""

    status: CoverageStatus
    observed_days: int
    expected_days: int
    last_synced_at: str | None = None
    reason: str | None = None
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observed_days": self.observed_days,
            "expected_days": self.expected_days,
            "last_synced_at": self.last_synced_at,
            "reason": self.reason,
            "action": self.action,
        }


@dataclass(frozen=True)
class DailyReportReadiness:
    """指定日期生成日报前的完整性结论。"""

    status: ReadinessStatus
    finality: ReportFinality
    data_as_of: str | None
    dimensions: dict[str, CoverageDimension]
    blockers: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    suggested_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "finality": self.finality,
            "data_as_of": self.data_as_of,
            "dimensions": {
                key: value.to_dict() for key, value in self.dimensions.items()
            },
            "blockers": list(self.blockers),
            "omitted_sections": list(self.omitted_sections),
            "suggested_actions": list(self.suggested_actions),
        }


class DailyReportReadinessError(RuntimeError):
    """数据未达到调用方所请求的报告模式。"""

    code = "report_data_incomplete"

    def __init__(self, readiness: DailyReportReadiness, mode: str):
        self.readiness = readiness
        self.mode = mode
        if readiness.status == "blocked":
            message = "报告核心数据尚未完整，请先完成同步后再生成日报"
        else:
            message = "当前数据只能生成受限版日报，请先补齐数据或明确选择受限版"
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "message": str(self),
            "readiness": self.readiness.to_dict(),
        }


class DailyReportReadinessService:
    """基于逐日同步证据和 Provider 能力评估日报是否可生成。"""

    _HEALTH_UNSUPPORTED_PROVIDERS = frozenset({"huawei"})

    def __init__(
        self,
        storage: Any,
        *,
        provider_type: str,
        today: date | None = None,
    ) -> None:
        self._storage = storage
        self._provider_type = (provider_type or "").strip().lower()
        self._today = today or date.today()

    def check(
        self,
        user_id: int | None,
        target_date: date,
    ) -> DailyReportReadiness:
        start = target_date - timedelta(days=27)
        if user_id is None:
            return self._no_local_user_readiness(target_date)

        facts = self._storage.get_report_readiness_facts(
            user_id, start, target_date,
        )
        day_facts = {str(item["date"]): item for item in facts.get("days", [])}
        target = day_facts.get(str(target_date), {})
        marker_status = str(target.get("marker_status") or "").lower()
        marker_synced_at = target.get("marker_synced_at")

        if target_date > self._today:
            activity = CoverageDimension(
                "missing", 0, 1, reason="未来日期尚无完整数据", action="choose_past_date",
            )
        elif marker_status == "completed":
            activity = CoverageDimension(
                "complete", 1, 1, marker_synced_at,
                reason="报告日活动同步已完整结束",
            )
        elif marker_status == "pending":
            activity = CoverageDimension(
                "syncing", 0, 1, marker_synced_at,
                reason="报告日数据仍在同步", action="wait_for_sync",
            )
        elif marker_status == "failed":
            activity = CoverageDimension(
                "failed", 0, 1, marker_synced_at,
                reason=str(target.get("marker_error") or "报告日同步失败"),
                action="retry_day_sync",
            )
        else:
            activity = CoverageDimension(
                "missing", 0, 1, marker_synced_at,
                reason="缺少报告日活动同步完成证据", action="sync_day",
            )

        dimensions: dict[str, CoverageDimension] = {"activity": activity}
        omitted: set[str] = set()
        auxiliary_incomplete = False
        health_supported = self._provider_type not in self._HEALTH_UNSUPPORTED_PROVIDERS

        if not health_supported:
            dimensions["sleep"] = CoverageDimension(
                "unsupported", 0, 0,
                reason=f"{self._provider_type or '当前'} 数据源不提供睡眠数据",
            )
            dimensions["recovery"] = CoverageDimension(
                "unsupported", 0, 0,
                reason=f"{self._provider_type or '当前'} 数据源不提供恢复指标",
            )
            dimensions["trends_7d"] = CoverageDimension(
                "unsupported", 0, 0,
                reason=f"{self._provider_type or '当前'} 数据源不提供健康趋势",
            )
            omitted.update({"sleep", "recovery", "trends_7d", "anomalies", "recommendation"})
        else:
            health = target.get("health") or {}
            sleep_complete = float(health.get("sleep_duration_hours") or 0) > 0
            recovery_complete = any(
                health.get(key) is not None
                for key in (
                    "resting_heart_rate",
                    "hrv_last_night_avg",
                    "avg_stress_level",
                    "body_battery_high",
                    "training_readiness_score",
                )
            )
            dimensions["sleep"] = self._target_health_dimension(
                sleep_complete, marker_status, marker_synced_at,
                missing_reason="报告日没有可用睡眠数据",
                missing_action="authorize_or_sync_sleep",
            )
            dimensions["recovery"] = self._target_health_dimension(
                recovery_complete, marker_status, marker_synced_at,
                missing_reason="报告日没有可用恢复指标",
                missing_action="sync_health",
            )
            if dimensions["sleep"].status != "complete":
                auxiliary_incomplete = True
                omitted.add("sleep")
            if dimensions["recovery"].status != "complete":
                auxiliary_incomplete = True
                omitted.update({"recovery", "recommendation"})

            trend_days = [
                item for item in day_facts.values()
                if target_date - timedelta(days=6) <= date.fromisoformat(str(item["date"])) <= target_date
                and self._has_health_trend(item.get("health") or {})
            ]
            if len(trend_days) == 7:
                dimensions["trends_7d"] = CoverageDimension(
                    "complete", 7, 7, self._latest_sync(trend_days),
                )
            else:
                dimensions["trends_7d"] = CoverageDimension(
                    "insufficient_history", len(trend_days), 7,
                    self._latest_sync(trend_days),
                    reason=f"近 7 天只有 {len(trend_days)} 天可用健康数据",
                    action="sync_7d",
                )
                auxiliary_incomplete = True
                omitted.update({"trends_7d", "anomalies"})

        seven_day_facts = [
            item for item in day_facts.values()
            if target_date - timedelta(days=6) <= date.fromisoformat(str(item["date"])) <= target_date
        ]
        load_7d = self._coverage_window_dimension(seven_day_facts, 7, "sync_7d")
        all_days = list(day_facts.values())
        load_28d = self._coverage_window_dimension(all_days, 28, "sync_28d")
        dimensions["load_7d"] = load_7d
        dimensions["load_28d"] = load_28d
        if load_7d.status != "complete" or load_28d.status != "complete":
            auxiliary_incomplete = True
            omitted.update({"training_load", "recommendation"})

        blockers = ("activity",) if activity.status != "complete" else ()
        if blockers:
            status: ReadinessStatus = "blocked"
        elif auxiliary_incomplete:
            status = "limited"
        else:
            status = "ready"

        finality: ReportFinality = (
            "final" if status == "ready" and target_date < self._today else "provisional"
        )
        actions = tuple(dict.fromkeys(
            dimension.action
            for dimension in dimensions.values()
            if dimension.action
        ))
        return DailyReportReadiness(
            status=status,
            finality=finality,
            data_as_of=marker_synced_at,
            dimensions=dimensions,
            blockers=blockers,
            omitted_sections=tuple(sorted(omitted)),
            suggested_actions=actions,
        )

    def _no_local_user_readiness(self, target_date: date) -> DailyReportReadiness:
        activity = CoverageDimension(
            "missing", 0, 1,
            reason="本地没有可用于确认日报归属的同步数据",
            action="sync_day",
        )
        return DailyReportReadiness(
            status="blocked",
            finality="provisional",
            data_as_of=None,
            dimensions={"activity": activity},
            blockers=("activity",),
            omitted_sections=(),
            suggested_actions=("sync_day",),
        )

    @staticmethod
    def _target_health_dimension(
        is_complete: bool,
        marker_status: str,
        synced_at: str | None,
        *,
        missing_reason: str,
        missing_action: str,
    ) -> CoverageDimension:
        if is_complete:
            return CoverageDimension("complete", 1, 1, synced_at)
        if marker_status == "pending":
            return CoverageDimension(
                "syncing", 0, 1, synced_at,
                reason="健康数据仍在同步", action="wait_for_sync",
            )
        if marker_status == "failed":
            return CoverageDimension(
                "failed", 0, 1, synced_at,
                reason="健康数据同步失败", action="retry_day_sync",
            )
        return CoverageDimension(
            "missing", 0, 1, synced_at,
            reason=missing_reason, action=missing_action,
        )

    @staticmethod
    def _has_health_trend(health: dict[str, Any]) -> bool:
        has_sleep = float(health.get("sleep_duration_hours") or 0) > 0
        has_recovery = any(
            health.get(key) is not None
            for key in (
                "resting_heart_rate",
                "hrv_last_night_avg",
                "avg_stress_level",
            )
        )
        return has_sleep or has_recovery

    @staticmethod
    def _coverage_window_dimension(
        day_facts: list[dict[str, Any]], expected_days: int, action: str,
    ) -> CoverageDimension:
        completed = [
            item for item in day_facts
            if str(item.get("marker_status") or "").lower() == "completed"
        ]
        status: CoverageStatus = (
            "complete" if len(completed) == expected_days else "insufficient_history"
        )
        return CoverageDimension(
            status,
            len(completed),
            expected_days,
            DailyReportReadinessService._latest_sync(completed),
            reason=None if status == "complete" else (
                f"近 {expected_days} 天只有 {len(completed)} 天具备活动同步完成证据"
            ),
            action=None if status == "complete" else action,
        )

    @staticmethod
    def _latest_sync(day_facts: list[dict[str, Any]]) -> str | None:
        values = [
            str(item["marker_synced_at"])
            for item in day_facts
            if item.get("marker_synced_at")
        ]
        return max(values) if values else None


def enforce_report_readiness(
    readiness: DailyReportReadiness,
    mode: str = "complete",
) -> DailyReportReadiness:
    """校验调用方请求的生成模式，失败时返回结构化门禁错误。"""

    if mode not in {"complete", "limited"}:
        raise ValueError("report mode 必须是 complete 或 limited")
    if readiness.status == "blocked":
        raise DailyReportReadinessError(readiness, mode)
    if readiness.status == "limited" and mode != "limited":
        raise DailyReportReadinessError(readiness, mode)
    return readiness
