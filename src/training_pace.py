"""基于个人训练事实的配速校准与当天保守执行覆盖。"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable

from .training_planning import iter_leaf_workout_steps


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _activity_date(activity: dict[str, Any]) -> date | None:
    raw = activity.get("activity_date") or activity.get("date") or activity.get("start_time")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _is_running(activity: dict[str, Any]) -> bool:
    value = f"{activity.get('activity_type') or ''} {activity.get('activity_name') or ''}".lower()
    return any(token in value for token in ("run", "跑", "越野", "trail"))


def _activity_pace(activity: dict[str, Any]) -> float | None:
    direct = _number(
        activity.get("avg_pace_sec_per_km")
        or activity.get("pace_sec_per_km")
        or activity.get("averagePaceInSecondsPerKilometer")
    )
    if direct is not None:
        return direct if 180 <= direct <= 720 else None
    duration = _number(activity.get("duration_seconds") or activity.get("duration"))
    distance_m = _number(activity.get("distance_meters") or activity.get("distance"))
    if duration is None or distance_m is None or distance_m < 3000:
        return None
    pace = duration / (distance_m / 1000)
    return pace if 180 <= pace <= 720 else None


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def format_pace_range(min_sec_per_km: int, max_sec_per_km: int) -> str:
    def one(value: int) -> str:
        return f"{value // 60}:{value % 60:02d}"

    return f"{one(min_sec_per_km)}–{one(max_sec_per_km)}/km"


@dataclass(frozen=True)
class PaceCalibrationPolicy:
    """可版本化的 v1 配速证据门禁。"""

    version: str = "pace-calibration-v1"
    primary_window_days: int = 28
    extended_window_days: int = 42
    recent_window_days: int = 14
    min_comparable_samples: int = 3
    min_analysis_confidence: float = 0.55
    threshold_fast_ratio: float = 0.98
    threshold_slow_ratio: float = 1.03


@dataclass(frozen=True)
class _PaceSample:
    zone: str
    pace_sec_per_km: float
    activity_date: date
    basis_ref: str
    confidence: float
    work_duration_seconds: int | None = None


class PaceCalibrationProfileBuilder:
    """从已分析的同类训练与个人锚点建立配速校准档案。"""

    def __init__(self, policy: PaceCalibrationPolicy | None = None):
        self.policy = policy or PaceCalibrationPolicy()

    def build(
        self,
        activities: list[dict[str, Any]],
        *,
        target: date,
        athlete_profile: dict[str, Any] | None = None,
        personal_bests: dict[str, Any] | None = None,
        target_time: Any = None,
        weather_context: str = "",
    ) -> dict[str, Any]:
        athlete_profile = athlete_profile or {}
        samples: dict[str, list[_PaceSample]] = {
            f"z{zone}": [] for zone in range(1, 6)
        }
        excluded = {
            "invalid_or_non_running": 0,
            "outside_window": 0,
            "terrain_mismatch": 0,
            "low_confidence": 0,
            "missing_comparable_pace": 0,
        }
        cutoff = target - timedelta(days=self.policy.extended_window_days)
        latest_fact_date: date | None = None

        for activity in activities:
            activity_day = _activity_date(activity)
            if not _is_running(activity) or activity_day is None:
                excluded["invalid_or_non_running"] += 1
                continue
            if activity_day < cutoff or activity_day > target:
                excluded["outside_window"] += 1
                continue
            analysis = activity.get("training_analysis")
            analysis = analysis if isinstance(analysis, dict) else {}
            terrain = str(analysis.get("terrain") or "unknown").lower()
            if terrain in {"hilly", "trail", "mountain"}:
                excluded["terrain_mismatch"] += 1
                continue
            confidence = _number(analysis.get("confidence")) or 0.0
            if confidence < self.policy.min_analysis_confidence:
                excluded["low_confidence"] += 1
                continue
            primary_type = str(analysis.get("primary_type") or "unknown").lower()
            activity_id = str(activity.get("activity_id") or activity_day)
            basis_ref = str(analysis.get("analysis_id") or f"activity:{activity_id}")
            latest_fact_date = max(latest_fact_date or activity_day, activity_day)

            if primary_type == "interval":
                work_samples = self._interval_work_samples(
                    activity, analysis, activity_day, basis_ref, confidence,
                )
                if work_samples:
                    samples["z5"].extend(work_samples)
                else:
                    excluded["missing_comparable_pace"] += 1
                continue

            pace = _activity_pace(activity)
            zone = self._zone_for_activity(activity, primary_type)
            if pace is None or zone is None:
                excluded["missing_comparable_pace"] += 1
                continue
            # 天气归一化：高温/低温天气下的配速折算回标准温度
            normalized_pace = _weather_adjusted_pace(pace, activity_day, weather_context)
            samples[zone].append(_PaceSample(
                zone=zone,
                pace_sec_per_km=normalized_pace,
                activity_date=activity_day,
                basis_ref=basis_ref,
                confidence=confidence,
            ))

        zones = {
            zone: self._zone_from_samples(zone, values, target)
            for zone, values in samples.items()
        }
        threshold = _number(
            athlete_profile.get("threshold_pace_sec_per_km")
            or athlete_profile.get("lactate_threshold_pace")
            or athlete_profile.get("critical_speed_pace_sec_per_km")
        )
        if threshold is not None and 180 <= threshold <= 720:
            minimum = round(threshold * self.policy.threshold_fast_ratio)
            maximum = round(threshold * self.policy.threshold_slow_ratio)
            zones["z4"] = {
                "status": "available",
                "source": "user_confirmed_threshold",
                "min_sec_per_km": minimum,
                "max_sec_per_km": maximum,
                "display_range": format_pace_range(minimum, maximum),
                "applies_to": "continuous_main",
                "basis_refs": ["athlete_profile:threshold_pace"],
                "sample_count": 1,
                "confidence": "high",
                "recent_median_sec_per_km": round(threshold),
                "baseline_median_sec_per_km": round(threshold),
            }

        available_count = sum(
            1 for value in zones.values() if value["status"] == "available"
        )
        # PB 交叉验证：Z4 上限封顶、样本不足兜底、能力差距标记
        threshold_pace = _derive_threshold_pace(personal_bests)
        recent_median_by_zone = {
            zone: round(median([s.pace_sec_per_km for s in values]))
            for zone, values in samples.items() if values
        }
        zones, state_gap = _cap_and_fallback_by_pb(
            zones, threshold_pace, recent_median_by_zone,
        )
        available_count = sum(
            1 for value in zones.values() if value["status"] == "available"
        )
        version_source = json.dumps(
            {"policy": self.policy.version, "zones": zones, "cutoff": str(latest_fact_date)},
            sort_keys=True,
            ensure_ascii=False,
        )
        profile_version = hashlib.sha256(version_source.encode("utf-8")).hexdigest()[:12]
        result = {
            "type": "pace_calibration_profile",
            "version": profile_version,
            "policy_version": self.policy.version,
            "as_of": str(target),
            "facts_cutoff": str(latest_fact_date) if latest_fact_date else None,
            "zones": zones,
            "data_quality": {
                "status": "sufficient" if available_count else "insufficient",
                "available_zone_count": available_count,
                "excluded_reasons": excluded,
                "weather_normalized": bool(weather_context),
            },
        }
        if state_gap:
            result["state_gap"] = state_gap
        if threshold_pace:
            result["pb_threshold_pace_sec_per_km"] = threshold_pace
        return result

    @staticmethod
    def _zone_for_activity(
        activity: dict[str, Any], primary_type: str,
    ) -> str | None:
        if primary_type == "aerobic":
            return "z2"
        if primary_type in {"steady", "race_pace"}:
            return "z3"
        if primary_type == "tempo":
            return "z4"
        name = str(activity.get("activity_name") or "").lower()
        if any(token in name for token in ("稳态", "马拉松配速", "race pace", "steady")):
            return "z3"
        if any(token in name for token in ("测试", "比赛", "race", "time trial")):
            return "z4"
        return None

    @staticmethod
    def _interval_work_samples(
        activity: dict[str, Any],
        analysis: dict[str, Any],
        activity_day: date,
        basis_ref: str,
        confidence: float,
    ) -> list[_PaceSample]:
        features = analysis.get("features")
        features = features if isinstance(features, dict) else {}
        splits = features.get("splits") or activity.get("splits") or []
        result: list[_PaceSample] = []
        for split in splits if isinstance(splits, list) else []:
            if not isinstance(split, dict):
                continue
            split_type = str(split.get("split_type") or split.get("type") or "").upper()
            if not any(token in split_type for token in ("INTERVAL_ACTIVE", "WORK")):
                continue
            pace = _number(split.get("pace_sec_per_km") or split.get("pace_per_km"))
            duration = _number(split.get("duration_sec") or split.get("duration_seconds"))
            if pace is None or not 120 <= pace <= 720 or duration is None:
                continue
            result.append(_PaceSample(
                zone="z5",
                pace_sec_per_km=pace,
                activity_date=activity_day,
                basis_ref=f"{basis_ref}:split:{split.get('index', len(result))}",
                confidence=confidence,
                work_duration_seconds=round(duration),
            ))
        return result

    def _zone_from_samples(
        self, zone: str, values: list[_PaceSample], target: date,
    ) -> dict[str, Any]:
        primary_cutoff = target - timedelta(days=self.policy.primary_window_days)
        primary = [sample for sample in values if sample.activity_date >= primary_cutoff]
        eligible = primary if len(primary) >= self.policy.min_comparable_samples else values
        if len(eligible) < self.policy.min_comparable_samples:
            return {
                "status": "unavailable",
                "source": "insufficient_comparable_sessions",
                "min_sec_per_km": None,
                "max_sec_per_km": None,
                "display_range": None,
                "applies_to": "work_interval" if zone == "z5" else "continuous_main",
                "basis_refs": [],
                "sample_count": len(eligible),
                "confidence": "low",
            }

        paces = [sample.pace_sec_per_km for sample in eligible]
        minimum = round(_percentile(paces, 0.25) - 3)
        maximum = round(_percentile(paces, 0.75) + 3)
        if maximum - minimum < 10:
            center = round(median(paces))
            minimum, maximum = center - 5, center + 5
        recent_cutoff = target - timedelta(days=self.policy.recent_window_days)
        recent = [sample.pace_sec_per_km for sample in eligible if sample.activity_date >= recent_cutoff]
        older = [sample.pace_sec_per_km for sample in eligible if sample.activity_date < recent_cutoff]
        result: dict[str, Any] = {
            "status": "available",
            "source": "comparable_training_sessions",
            "min_sec_per_km": minimum,
            "max_sec_per_km": maximum,
            "display_range": format_pace_range(minimum, maximum),
            "applies_to": "work_interval" if zone == "z5" else "continuous_main",
            "basis_refs": list(dict.fromkeys(sample.basis_ref for sample in eligible)),
            "sample_count": len(eligible),
            "confidence": "medium",
            "recent_median_sec_per_km": round(median(recent)) if recent else None,
            "baseline_median_sec_per_km": round(median(older or paces)),
        }
        if zone == "z5":
            durations = [
                sample.work_duration_seconds for sample in eligible
                if sample.work_duration_seconds is not None
            ]
            result["work_duration_seconds"] = {
                "min": min(durations), "max": max(durations),
            }
        return result


class StepTargetResolver:
    """把课程意图解析为每个叶子步骤的个人目标，不改写步骤结构。"""

    def apply(
        self,
        prescription: dict[str, Any],
        *,
        profile: dict[str, Any],
        course_guidance: dict[str, Any],
    ) -> dict[str, Any]:
        result = copy.deepcopy(prescription)
        if result.get("structure_version") != 2:
            return result
        force_feel_only = (
            (course_guidance.get("today_target") or {}).get("status") == "feel_only"
        )
        course_zone = str(result.get("intensity_zone") or "")
        for step in iter_leaf_workout_steps(result.get("steps") or []):
            intent = step.get("intensity_intent")
            intent = intent if isinstance(intent, dict) else {}
            fallback_feel = str(intent.get("fallback_feel") or "以可控体感完成")
            try:
                zone_number = int(intent.get("zone"))
            except (TypeError, ValueError):
                zone_number = 0
            preferred = str(intent.get("preferred_metric") or "feel")
            zone = (profile.get("zones") or {}).get(f"z{zone_number}") or {}
            if force_feel_only or preferred != "pace" or zone.get("status") != "available":
                step["resolved_target"] = {
                    "status": "feel_only",
                    "metric": "feel",
                    "label": fallback_feel,
                    "zone": zone_number or None,
                    "reason": (
                        str(course_guidance.get("safety_action") or "")
                        if force_feel_only else "该步骤缺少匹配的个人数字强度事实"
                    ),
                    "facts_cutoff": profile.get("facts_cutoff"),
                    "confidence": str(zone.get("confidence") or "low"),
                }
                continue
            context = intent.get("calibration_context")
            context = context if isinstance(context, dict) else {}
            observed_duration = zone.get("work_duration_seconds")
            intended_duration = _number(context.get("duration_seconds"))
            if zone_number == 5 and isinstance(observed_duration, dict) and intended_duration:
                minimum = _number(observed_duration.get("min")) or 0
                maximum = _number(observed_duration.get("max")) or 0
                if not minimum <= intended_duration <= maximum:
                    step["resolved_target"] = {
                        "status": "feel_only",
                        "metric": "feel",
                        "label": fallback_feel,
                        "zone": zone_number,
                        "reason": "现有间歇样本与本工作段时长不可比",
                        "facts_cutoff": profile.get("facts_cutoff"),
                        "confidence": "low",
                    }
                    continue
            target = zone
            if str(zone_number) == course_zone:
                course_target = course_guidance.get("today_target") or {}
                if course_target.get("min_sec_per_km") and course_target.get("max_sec_per_km"):
                    target = course_target
            minimum = int(target["min_sec_per_km"])
            maximum = int(target["max_sec_per_km"])
            if str(step.get("role") or "").lower() == "warmup" and maximum > minimum + 15:
                # 热身段从偏慢端渐进到主体：用区间慢半段，避免与主体配速完全一致
                slower_min = round((minimum + maximum) / 2)
                minimum = slower_min
            step["resolved_target"] = {
                "status": "available",
                "metric": "pace",
                "min_sec_per_km": minimum,
                "max_sec_per_km": maximum,
                "display_range": format_pace_range(minimum, maximum),
                "zone": zone_number,
                "basis_refs": list(zone.get("basis_refs") or []),
                "profile_version": profile.get("version"),
                "facts_cutoff": profile.get("facts_cutoff"),
                "confidence": str(zone.get("confidence") or "low"),
            }
        return result


_EXTREME_WEATHER_TOKENS = (
    "高温", "炎热", "酷暑", "闷热", "桑拿", "暴晒", "热浪",
    "台风", "暴雨", "大雨", "暴雪", "严寒", "酷寒", "冰冻",
)
_MODERATE_WEATHER_TOKENS = ("大风", "阵雨", "雨雪", "雾霾", "湿冷", "沙尘")


def _weather_slow_ratio(text: str) -> float:
    """极端天气关键词 → 配速保守放慢比例；无输入或非极端天气不调整。"""
    if not text:
        return 0.0
    if any(token in text for token in _EXTREME_WEATHER_TOKENS):
        return 0.05
    if any(token in text for token in _MODERATE_WEATHER_TOKENS):
        return 0.03
    return 0.0


# ── 校准增强：天气归一化 + PB 交叉验证 ──
# 按活动月份估算温度，把配速折算回标准温度（约 15°C）。
# 系数 <1 表示高温下跑得慢，折算后配速变快（去掉高温影响）。
_SEASON_PACE_FACTOR = {
    7: 0.94, 8: 0.94,       # 夏季（高温）
    4: 0.99, 5: 0.99, 9: 0.99, 10: 0.99,  # 春秋
    12: 1.03, 1: 1.03, 2: 1.03,  # 冬季
    3: 1.00, 6: 1.00, 11: 1.00,  # 温和
}


def _weather_adjusted_pace(
    pace_sec_per_km: float, activity_date: date | None,
    weather_hint: str = "",
) -> float:
    """高温/低温天气下跑的活动配速折算回标准温度。"""
    factor = 1.0
    if activity_date is not None:
        factor = _SEASON_PACE_FACTOR.get(activity_date.month, 1.0)
    hint = weather_hint or ""
    if any(token in hint for token in ("高温", "炎热", "酷暑", "热浪")):
        factor = min(factor, 0.92)
    elif any(token in hint for token in ("凉快", "低温", "冷")):
        factor = max(factor, 1.05)
    return round(pace_sec_per_km * factor)


def _derive_threshold_pace(personal_bests: dict[str, Any] | None) -> float | None:
    """从 PB 推算个人阈值配速 T（s/km）：半马配速最接近阈值；其他距离按经验差折算。"""
    pb = personal_bests or {}
    candidates: list[float] = []

    def pace_seconds(value: Any) -> float | None:
        if isinstance(value, dict):
            raw = value.get("time") or value.get("pace_sec_per_km")
        else:
            raw = value
        try:
            text = str(raw or "").strip()
            if "=" in text:
                text = text.split("=")[-1].strip()
            parts = [part for part in text.split(":") if part]
            if len(parts) == 3:  # 时:分:秒 或 分:秒:百分秒
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            return float(text)
        except (TypeError, ValueError):
            return None

    def km_pace(duration_seconds: float | None, distance_km: float) -> float | None:
        if not duration_seconds or duration_seconds <= 0:
            return None
        return round(duration_seconds / distance_km)

    half_raw = pb.get("half_marathon") or pb.get("hm")
    ten_raw = pb.get("10k") or pb.get("10km")
    five_raw = pb.get("5k") or pb.get("5km")
    if isinstance(half_raw, dict):
        t = pace_seconds(half_raw.get("time")) or pace_seconds(half_raw)
    else:
        t = pace_seconds(half_raw)
    half_pace = km_pace(t, 21.0975)
    if half_pace:
        candidates.append(half_pace)
    ten_time = pace_seconds(ten_raw.get("time") if isinstance(ten_raw, dict) else ten_raw)
    ten_pace = km_pace(ten_time, 10.0)
    if ten_pace:
        candidates.append(ten_pace + 10)
    five_time = pace_seconds(five_raw.get("time") if isinstance(five_raw, dict) else five_raw)
    five_pace = km_pace(five_time, 5.0)
    if five_pace:
        candidates.append(five_pace + 20)
    if not candidates:
        return None
    return round(min(candidates))


def _cap_and_fallback_by_pb(
    zones: dict[str, dict[str, Any]],
    threshold_pace: float | None,
    recent_median_by_zone: dict[str, float],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """PB 交叉验证：Z4 上限封顶；样本不足用 PB 兜底；差距过大标记 state_gap。"""
    if threshold_pace is None:
        return zones, None
    state_gap: str | None = None
    # 样本不足兜底（z2/z3/z4）
    for zone, fallback_range in (("z2", (round(threshold_pace * 1.28), round(threshold_pace * 1.38))),
                                  ("z3", (round(threshold_pace * 1.02), round(threshold_pace * 1.12))),
                                  ("z4", (round(threshold_pace * 0.95), round(threshold_pace * 1.05))),
                                  ("z5", (round(threshold_pace * 0.88), round(threshold_pace * 0.96)))):
        current = zones.get(zone) or {}
        if current.get("status") == "available":
            continue
        minimum, maximum = fallback_range
        zones[zone] = {
            "status": "available",
            "source": "pb_derived",
            "min_sec_per_km": minimum,
            "max_sec_per_km": maximum,
            "display_range": format_pace_range(minimum, maximum),
            "applies_to": "continuous_main",
            "basis_refs": ["personal_bests:derived"],
            "sample_count": 0,
            "confidence": "medium",
            "recent_median_sec_per_km": round(median([minimum, maximum])),
            "baseline_median_sec_per_km": round(median([minimum, maximum])),
        }
    # Z4 上限封顶：近期样本比 PB 阈值快 → 封顶（配速数值不能小于 threshold）
    z4 = zones.get("z4") or {}
    if z4.get("status") == "available" and z4.get("source") != "pb_derived":
        max_pace = z4.get("max_sec_per_km")
        if max_pace and max_pace < threshold_pace:
            z4["max_sec_per_km"] = threshold_pace
            z4["display_range"] = format_pace_range(z4.get("min_sec_per_km") or threshold_pace, threshold_pace)
            z4["capped_by_pb"] = True
    # 能力差距标记：近期中位数显著慢于 PB 推算
    recent = recent_median_by_zone.get("z4") or recent_median_by_zone.get("z2")
    if recent and recent > threshold_pace * 1.12:
        state_gap = "current_below_pb"
    return zones, state_gap


class DailyPaceAdjustmentEngine:
    """按能力/状态、目标推进和安全信号生成当天配速覆盖。"""

    goal_progression_ratio: float = 0.01

    def __init__(self, step_target_resolver: StepTargetResolver | None = None):
        self.step_target_resolver = step_target_resolver or StepTargetResolver()

    def apply(
        self,
        workout: dict[str, Any],
        *,
        profile: dict[str, Any],
        recovery_snapshot: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        goal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = copy.deepcopy(workout)
        if str(item.get("type") or "").lower() == "rest":
            return item
        prescription = item.get("training_prescription")
        prescription = copy.deepcopy(prescription) if isinstance(prescription, dict) else {}
        targets = prescription.get("targets")
        targets = copy.deepcopy(targets) if isinstance(targets, dict) else {}
        pacing_guard = prescription.get("pacing_guard")
        if isinstance(pacing_guard, dict) and pacing_guard.get("status") == "requires_review":
            return self._feel_only(
                item,
                prescription,
                targets,
                profile,
                reason="课程距离与时长隐含配速不一致，需先重规划",
                safety_action="本次不设数字配速硬目标，只按可对话体感和单一时长完成",
            )
        try:
            zone_name = f"z{int(prescription.get('intensity_zone'))}"
        except (TypeError, ValueError):
            zone_name = ""
        zone = (profile.get("zones") or {}).get(zone_name) or {}
        constraints = constraints or {}
        recovery_snapshot = recovery_snapshot or {}
        goal_context = goal_context or {}

        if zone.get("status") != "available":
            return self._feel_only(
                item,
                prescription,
                targets,
                profile,
                reason="缺少足够的近期同类训练或个人强度锚点",
                safety_action="配速暂不作为硬目标，按体感执行",
            )

        base_target = {
            key: copy.deepcopy(zone.get(key))
            for key in (
                "min_sec_per_km", "max_sec_per_km", "display_range",
                "applies_to", "work_duration_seconds", "confidence",
                "sample_count", "source",
            )
            if zone.get(key) is not None
        }
        base_target.update({
            "status": "available",
            "profile_version": profile.get("version"),
        })
        reasons: list[str] = []
        recovery = recovery_snapshot.get("recovery")
        recovery = recovery if isinstance(recovery, dict) else recovery_snapshot
        score = _number(
            recovery.get("overall_score")
            or recovery.get("score")
            or recovery.get("training_readiness_score")
        )
        load = recovery_snapshot.get("training_load")
        load = load if isinstance(load, dict) else {}
        load_status = str(load.get("acwr_status") or load.get("status") or "").lower()
        medical_limitations = str(constraints.get("medical_limitations") or "").strip()

        if medical_limitations:
            return self._feel_only(
                item,
                prescription,
                targets,
                profile,
                reason="当前存在疼痛、伤病或其他训练限制",
                safety_action="今天不设数字配速硬目标，按无痛和可控体感完成",
                base_target=base_target,
                basis_refs=zone.get("basis_refs") or [],
                sample_count=zone.get("sample_count"),
                confidence=str(zone.get("confidence") or "low"),
            )
        if score is not None and score < 45:
            return self._feel_only(
                item,
                prescription,
                targets,
                profile,
                reason="今天恢复状态不足以支持数字配速硬目标",
                safety_action="今天不设数字配速硬目标，按可对话体感完成",
                base_target=base_target,
                basis_refs=zone.get("basis_refs") or [],
                sample_count=zone.get("sample_count"),
                confidence=str(zone.get("confidence") or "low"),
            )

        slow_ratio = 0.0
        progression_ratio = 0.0
        if score is not None and score < 60:
            slow_ratio += 0.03
            reasons.append("今晨恢复偏低，主体配速采用更保守区间")
        recent = _number(zone.get("recent_median_sec_per_km"))
        baseline = _number(zone.get("baseline_median_sec_per_km"))
        performance_declining = (
            recent is not None
            and baseline is not None
            and recent > baseline * 1.03
        )
        if performance_declining:
            slow_ratio += 0.02
            reasons.append("近期同类训练完成配速慢于较长窗口基线")
        load_risk = load_status in {"high", "high_risk", "elevated"}
        if load_risk and (score is not None and score < 60 or performance_declining):
            slow_ratio += 0.01
            reasons.append("近期负荷偏高且状态已有保守信号，进一步收紧执行")
        elif load_risk:
            reasons.append("近期负荷偏高，但当前能力与状态稳定，仅作风险提示")
        weather_slow = _weather_slow_ratio(str(constraints.get("weather") or ""))
        if weather_slow:
            slow_ratio += weather_slow
            reasons.append("极端天气（高温/大风/雨雪等），主体配速进一步保守")
        slow_ratio = min(slow_ratio, 0.08)

        try:
            zone_number = int(prescription.get("intensity_zone"))
        except (TypeError, ValueError):
            zone_number = 0
        goal_ready = bool(
            goal_context.get("target_date")
            and zone_number in {2, 3}
            and score is not None
            and score >= 60
            and recent is not None
            and baseline is not None
            and not performance_declining
            and not slow_ratio
        )
        if goal_ready:
            progression_ratio = self.goal_progression_ratio
            reasons.append("当前能力与近期状态稳定，按目标做小幅推进")

        minimum = int(base_target["min_sec_per_km"])
        maximum = int(base_target["max_sec_per_km"])
        if slow_ratio:
            minimum = round(minimum * (1 + slow_ratio))
            maximum = round(maximum * (1 + slow_ratio))
            status = "slower"
            summary = "今天采用更保守的主体配速"
        elif progression_ratio:
            minimum = round(minimum * (1 - progression_ratio))
            maximum = round(maximum * (1 - progression_ratio))
            status = "progressed"
            summary = "当前状态稳定，按目标小幅推进主体配速"
        else:
            status = "unchanged"
            summary = "今天按当前个人基础配速区间执行"
            if not load_risk:
                reasons.append("当前恢复与负荷未触发配速降级")
        today_target = {
            "status": status,
            "min_sec_per_km": minimum,
            "max_sec_per_km": maximum,
            "display_range": format_pace_range(minimum, maximum),
            "applies_to": base_target.get("applies_to"),
        }
        if base_target.get("work_duration_seconds"):
            today_target["work_duration_seconds"] = copy.deepcopy(
                base_target["work_duration_seconds"]
            )
        guidance = {
            "base_target": base_target,
            "today_target": today_target,
            "goal_progression": {
                "applied": bool(progression_ratio),
                "ratio": progression_ratio,
            },
            "explanation": self._explanation(
                summary=summary,
                reasons=reasons,
                basis_refs=zone.get("basis_refs") or [],
                sample_count=zone.get("sample_count"),
                confidence=str(zone.get("confidence") or "low"),
                facts_cutoff=profile.get("facts_cutoff"),
            ),
            "facts_cutoff": profile.get("facts_cutoff"),
        }
        targets["pace"] = {
            "status": "available",
            "range": today_target["display_range"],
            "basis": f"个人配速校准档案 {profile.get('version')}",
        }
        prescription["targets"] = targets
        prescription["pace_guidance"] = guidance
        prescription = self.step_target_resolver.apply(
            prescription, profile=profile, course_guidance=guidance,
        )
        item["training_prescription"] = prescription
        return item

    def _feel_only(
        self,
        item: dict[str, Any],
        prescription: dict[str, Any],
        targets: dict[str, Any],
        profile: dict[str, Any],
        *,
        reason: str,
        safety_action: str,
        base_target: dict[str, Any] | None = None,
        basis_refs: list[str] | None = None,
        sample_count: Any = None,
        confidence: str = "low",
    ) -> dict[str, Any]:
        targets["pace"] = {
            "status": "unavailable", "range": None, "basis": reason,
        }
        guidance = {
            "base_target": base_target or {"status": "unavailable"},
            "today_target": {"status": "feel_only"},
            "safety_action": safety_action,
            "explanation": self._explanation(
                summary=safety_action,
                reasons=[reason],
                basis_refs=basis_refs or [],
                sample_count=sample_count,
                confidence=confidence,
                facts_cutoff=profile.get("facts_cutoff"),
            ),
            "facts_cutoff": profile.get("facts_cutoff"),
        }
        prescription["targets"] = targets
        prescription["pace_guidance"] = guidance
        prescription = self.step_target_resolver.apply(
            prescription, profile=profile, course_guidance=guidance,
        )
        item["training_prescription"] = prescription
        return item

    @staticmethod
    def _explanation(
        *,
        summary: str,
        reasons: list[str],
        basis_refs: list[str],
        sample_count: Any,
        confidence: str,
        facts_cutoff: Any,
    ) -> dict[str, Any]:
        return {
            "presentation": "collapsed",
            "trigger_label": "为什么这样建议",
            "summary": summary,
            "reasons": reasons,
            "basis_refs": list(basis_refs),
            "sample_count": sample_count,
            "facts_cutoff": facts_cutoff,
            "confidence": confidence,
        }
