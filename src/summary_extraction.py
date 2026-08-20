"""Deterministic, provider-tolerant extraction of per-session summary facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from statistics import mean, pstdev
from typing import Any, Iterable

# Schema 演进原则（统一收拢）：
# - session-summary 是单一统一 schema，维度演进（如训练深度分析字段）直接并入，
#   不产生 v1/v2 并行版本；旧记录缺失字段按 dataclass 默认值兼容读取。
# - 重算判断以 activity_summary_facts.detail_hash（detail 规范化 SHA-256）为准；
#   字段级粒度由 granularity 自描述，不做版本标记。
# - 所有消费方（日报/草稿/周报/训练首页）统一按本 schema 处理。

SESSION_SUMMARY_SCHEMA_VERSION = "session-summary"
SESSION_SUMMARY_RULE_VERSION = "2026-08-14"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(item: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(item.get(key))
        if value is not None:
            return value
    return None


def _string(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _mapping(detail: dict[str, Any], *keys: str) -> dict[str, Any]:
    queue: list[dict[str, Any]] = [detail]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        for key in keys:
            value = current.get(key)
            if isinstance(value, dict):
                return value
        queue.extend(value for value in current.values() if isinstance(value, dict))
    return {}


def _flatten_lap_list(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Coros lapList 展开为有顺序的分段序列。

    Coros 的 lap 本身是汇总，其内 `lapItemList` 才是逐段子项；
    优先展开子项（保留 lap 内顺序），无子项时退回 lap 汇总本身。
    """
    flat: list[dict[str, Any]] = []
    for lap in laps:
        items = lap.get("lapItemList") or []
        if isinstance(items, list) and items and all(
            isinstance(item, dict) for item in items
        ):
            flat.extend(items)
        else:
            flat.append(lap)
    return flat


def _list(detail: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = [detail]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        for key in keys:
            value = current.get(key)
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                if key == "lapList":
                    return _flatten_lap_list(value)
                return value
        queue.extend(value for value in current.values() if isinstance(value, dict))
    return []


@dataclass(frozen=True)
class VolumeFacts:
    duration_s: float = 0.0
    distance_m: float = 0.0
    calories: float | None = None
    load: float | None = None
    elapsed_duration_s: float | None = None  # 总用时（含暂停），用于无暂停判定
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StructureFacts:
    """跑步动力学画像：各维度均值、变异系数、前后半程差。

    配速相关的 CV/前后半程差由 PaceProfileFacts 独立管理，不在本类重复。
    """
    n_splits: int
    avg_cadence: float | None = None
    max_cadence: float | None = None
    cadence_cv_pct: float | None = None
    cadence_half_diff: float | None = None
    avg_stride: float | None = None
    stride_cv_pct: float | None = None
    stride_half_diff: float | None = None
    avg_gct: float | None = None
    gct_cv_pct: float | None = None
    gct_half_diff: float | None = None
    avg_vo: float | None = None
    vo_cv_pct: float | None = None
    vo_half_diff: float | None = None
    avg_vertical_ratio: float | None = None
    hr_cv_pct: float | None = None
    hr_half_diff: float | None = None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class IntensityFacts:
    pace_bands_pct: dict[str, float]
    hr_bands_pct: dict[str, float]
    basis: str
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TerrainFacts:
    ascent_m: float | None
    gain_per_km: float | None
    grade_profile: dict[str, float] | None = None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EffectFacts:
    """训练效果：Provider 原始 TE 优先；本地估算必须带 estimated 标记。"""

    aerobic_training_effect: float | None = None
    anaerobic_training_effect: float | None = None
    label: str | None = None
    message: str | None = None
    moderate_intensity_minutes: float | None = None
    vigorous_intensity_minutes: float | None = None
    estimated: bool = False
    estimate_method: str | None = None
    estimate_basis: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ElevationProfileFacts:
    """地形全貌：爬升/下降/海拔范围。"""

    ascent_m: float | None = None
    descent_m: float | None = None
    max_elevation_m: float | None = None
    min_elevation_m: float | None = None
    gain_per_km: float | None = None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PaceProfileFacts:
    """配速画像（分段粒度）：最快配速、分位数、CV、前后半程。"""

    avg_pace_sec_per_km: float | None = None
    fastest_pace_sec_per_km: float | None = None
    p5: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p95: float | None = None
    cv_pct: float | None = None
    half_pace_diff_s: float | None = None
    positive_split: bool | None = None
    split_count: int = 0
    basis: str = "split"
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StructureProfileFacts:
    """复合训练结构：splitType 序列、work/recovery、结构性课型。"""

    split_types: tuple[str, ...] = ()
    work_blocks: int = 0
    recovery_blocks: int = 0
    composite_type: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SessionSummaryFacts:
    schema_version: str
    fact_version: str
    granularity: str
    volume: VolumeFacts
    structure: StructureFacts | None = None
    intensity: IntensityFacts | None = None
    terrain: TerrainFacts | None = None
    effect: EffectFacts | None = None
    elevation_profile: ElevationProfileFacts | None = None
    pace_profile: PaceProfileFacts | None = None
    structure_profile: StructureProfileFacts | None = None
    segment_sequence: tuple[dict[str, Any], ...] = ()
    quantity_gate: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _weighted(values: Iterable[tuple[float, float]]) -> float | None:
    pairs = [(v, w) for v, w in values if v is not None and w > 0]
    return sum(v * w for v, w in pairs) / sum(w for _, w in pairs) if pairs else None


def _bands(values: list[tuple[float, float]], boundaries: tuple[float, ...]) -> dict[str, float]:
    total = sum(weight for _, weight in values)
    if not total:
        return {}
    result: dict[str, float] = {}
    edges = (float("-inf"),) + boundaries + (float("inf"),)
    for index in range(len(edges) - 1):
        name = f"{edges[index]:g}-{edges[index + 1]:g}"
        result[name] = round(sum(w for value, w in values if edges[index] <= value < edges[index + 1]) / total * 100, 2)
    return result


def _weighted_quantile(pairs: list[tuple[float, float]], fraction: float) -> float | None:
    """按权重（时长）线性插值分位数；pairs = [(value, weight), ...]。"""
    items = [(v, w) for v, w in pairs if v is not None and w > 0]
    if not items:
        return None
    if len(items) == 1:
        return round(items[0][0], 1)
    items.sort(key=lambda pair: pair[0])
    total = sum(w for _, w in items)
    target = total * fraction
    cumulative = 0.0
    prev_value = items[0][0]
    prev_cum = 0.0
    for value, weight in items:
        if target <= cumulative:
            if cumulative > prev_cum:
                frac = (target - prev_cum) / (cumulative - prev_cum)
                return round(prev_value + (value - prev_value) * frac, 1)
            return round(value, 1)
        prev_value, prev_cum = value, cumulative
        cumulative += weight
    return round(items[-1][0], 1)


def _half_metric_diff(pairs: list[tuple[float, float]]) -> dict[str, float | None]:
    """按累计权重切半，返回前后半段加权平均差（正=后程增大）。"""
    items = [(p, d) for p, d in pairs if p and d > 0]
    if len(items) < 2:
        return {"first": None, "second": None, "diff": None}
    total = sum(d for _, d in items)
    half = total / 2
    first: list[tuple[float, float]] = []
    second: list[tuple[float, float]] = []
    cumulative = 0.0
    for pace, duration in items:
        if cumulative < half:
            take = min(duration, half - cumulative)
            first.append((pace, take))
            cumulative += take
            if take < duration:
                second.append((pace, duration - take))
        else:
            second.append((pace, duration))
    first_avg = _weighted(first)
    second_avg = _weighted(second)
    if first_avg is None or second_avg is None:
        return {"first": None, "second": None, "diff": None}
    return {
        "first": round(first_avg, 1),
        "second": round(second_avg, 1),
        "diff": round(second_avg - first_avg, 1),
    }


def _cv_pct(pairs: list[tuple[float, float]]) -> float | None:
    """时长加权变异系数（%），不足 2 对返回 None。"""
    values = [v for v, _ in pairs if v is not None]
    if len(values) < 2:
        return None
    return round((pstdev(values) / (mean(values) or 1)) * 100, 2)


_WORK_TOKENS = (
    "interval", "work", "main", "speed", "repeat", "stride", "sprint",
)
_RECOVERY_TOKENS = (
    "recovery", "jog", "easy", "walk", "cool", "cooldown", "rest",
)


def _work_recovery_blocks(splits: list[dict[str, Any]]) -> tuple[int, int]:
    """从 splitType 标注或配速交替检测 work/recovery 段数。

    热身/冷却/步行/站立段不计入工作段，避免设备自动 lap 把整节轻松跑
    误判为间歇结构；配速交替检测只在前缀标注缺失时启用。
    """
    typed = [
        str(_string(s, "splitType", "split_type", "type", "lapType", "stepType") or "").lower()
        for s in splits
    ]
    work = 0
    recovery = 0
    for token in typed:
        if any(marker in token for marker in ("warmup", "cooldown", "walk", "stand", "rest")):
            continue
        if any(marker in token for marker in _RECOVERY_TOKENS):
            recovery += 1
            continue
        if any(marker in token for marker in _WORK_TOKENS):
            work += 1
    if work or recovery:
        return work, recovery
    pace_values = [_first(s, "pace", "pace_per_km", "pace_sec_per_km") for s in splits]
    for index in range(1, len(pace_values) - 1):
        before, current, after = (
            pace_values[index - 1], pace_values[index], pace_values[index + 1],
        )
        if before and current and after:
            # 配速秒/km 越小越快：current 明显快于前段且后段明显回落 → 一组 work/recovery
            if current < before * 0.88 and after > current * 1.15:
                work += 1
                recovery += 1
    return work, recovery


def _composite_type(
    work: int, recovery: int, activity_name: str, split_types: tuple[str, ...],
) -> str | None:
    """结构性课型：只做结构识别；强度课型（tempo 等）由 TrainingSessionAnalyzer 提供。"""
    lowered = (activity_name or "").lower()
    if "fartlek" in lowered or "变速" in activity_name:
        return "fartlek"
    if work >= 2 and recovery >= 1:
        return "interval"
    if any(token not in ("", "unknown") for token in split_types):
        return "structured"
    return "steady"


def _evidence(field: str, source: str, value: Any, *, confidence: float | None = 1.0) -> dict[str, Any]:
    """Keep source evidence machine-readable even when observation time is absent."""
    return {"kind": "fact", "source": source, "field": field,
            "observed_at": None, "value": value, "confidence": confidence}


def build_session_summary(detail: dict[str, Any]) -> SessionSummaryFacts:
    """Build one replayable summary from a provider detail payload.

    The function never raises for malformed optional provider fields; missing data
    is represented in ``data_quality`` and the finest available granularity wins.
    """
    from .providers.normalization import normalize_activity_detail

    detail = normalize_activity_detail(detail)
    summary = _mapping(detail, "summaryDTO", "summary", "activitySummary")
    source = summary or detail
    duration = _first(source, "duration", "durationSeconds", "duration_seconds") or 0.0
    distance = _first(source, "distance", "distanceMeters", "distance_meters") or 0.0
    calories = _first(source, "calories", "totalCalories", "caloriesBurned")
    load = _first(source, "trainingLoad", "training_load", "activityTrainingLoad")
    elapsed_duration = _first(source, "elapsedDuration", "elapsed_duration", "elapsed_duration_s")
    volume_evidence = (_evidence("volume.duration_s", "summaryDTO.duration", duration),
                       _evidence("volume.distance_m", "summaryDTO.distance", distance))
    # lapDTOs 是 Garmin 官方可信分段（距离/时长合计与 summary 一致），
    # 优先于 splitSummaries（存在 ×2 等异常）；其他 Provider 仍按各自字段探测。
    splits = _list(detail, "lapDTOs", "splitSummaries", "splits", "laps", "intervals", "segments", "lapList")
    split_values: list[tuple[float, float]] = []
    hrs: list[tuple[float, float]] = []
    cadence: list[tuple[float, float]] = []
    stride: list[tuple[float, float]] = []
    gct: list[tuple[float, float]] = []
    vo: list[tuple[float, float]] = []
    ascent = _first(source, "elevationGain", "elevation_gain", "ascent")
    split_ascent = 0.0
    split_duration = 0.0
    split_durations: list[float] = []
    for index, item in enumerate(splits):
        dist = _first(item, "distance", "distance_m", "distanceMeters") or 0.0
        dur = _first(item, "duration", "duration_sec", "durationSeconds") or 0.0
        pace = _first(item, "pace", "pace_per_km", "pace_sec_per_km")
        if pace is None and dist > 0 and dur > 0:
            pace = dur / (dist / 1000)
        if pace and dur > 0:
            split_values.append((pace, dur))
        hr = _first(item, "averageHR", "avgHr", "avg_hr")
        if hr and dur > 0:
            hrs.append((hr, dur))
        for target, keys in ((cadence, ("averageRunCadence", "avgCadence", "avg_cadence")), (stride, ("strideLength", "stride_length_cm")), (gct, ("groundContactTime", "ground_contact_ms")), (vo, ("verticalOscillation", "vertical_osc_mm"))):
            value = _first(item, *keys)
            if value is not None and dur > 0:
                target.append((value, dur))
        split_ascent += _first(item, "elevationGain", "ascent", "elevation_gain") or 0.0
        split_duration += dur
        split_durations.append(dur)

    metrics = _list(detail, "activity_detail_metrics", "activityDetailMetrics", "detailMetrics", "metrics")
    metric_pace: list[tuple[float, float]] = []
    metric_hr: list[tuple[float, float]] = []
    metric_grade: list[tuple[float, float]] = []
    metric_cadence: list[tuple[float, float]] = []
    for item in metrics:
        weight = _first(item, "duration", "duration_s", "elapsedSeconds") or 1.0
        pace = _first(item, "pace", "pace_sec_per_km", "speedPace")
        hr = _first(item, "heartRate", "heart_rate", "hr")
        grade = _first(item, "grade", "grade_pct", "slope")
        cadence_value = _first(item, "cadence", "avgCadence", "averageRunCadence")
        if pace and pace > 0: metric_pace.append((pace, weight))
        if hr and hr > 0: metric_hr.append((hr, weight))
        if grade is not None: metric_grade.append((grade, weight))
        if cadence_value and cadence_value > 0: metric_cadence.append((cadence_value, weight))
    pace_values = metric_pace or split_values
    hr_values = metric_hr or hrs
    granularity = "L2" if metrics else ("L1" if splits else "L0")
    basis = "hr" if hr_values else ("pace" if pace_values else None)
    intensity = None
    if pace_values or hr_values:
        intensity = IntensityFacts(
            pace_bands_pct=_bands(pace_values, (240, 300, 360, 420)),
            hr_bands_pct=_bands(hr_values, (130, 145, 160, 175)),
            basis=basis or "pace",
            evidence=(_evidence("intensity.basis", "heart_rate" if basis == "hr" else "pace_proxy", basis),),
        )
    structure = None
    terrain = None
    if splits:
        # ── 跑步动力学画像：各维度均值、CV、前后半程差 ──
        _avg_cad = _weighted(metric_cadence or cadence)
        _avg_st = _weighted(stride)
        _avg_gct = _weighted(gct)
        _avg_vo = _weighted(vo)
        _avg_vr = round((_avg_vo / _avg_st * 100), 1) if _avg_vo and _avg_st else None
        # 心率来源：优先 metrics 序列，否则分段 hr
        _hr_pairs = metric_hr or hrs
        structure = StructureFacts(
            n_splits=len(splits),
            avg_cadence=_avg_cad,
            max_cadence=_first(source, "maxRunCadence", "max_run_cadence", "maxCadence"),
            cadence_cv_pct=_cv_pct(metric_cadence or cadence),
            cadence_half_diff=_half_metric_diff(metric_cadence or cadence)["diff"] if (metric_cadence or cadence) else None,
            avg_stride=_avg_st,
            stride_cv_pct=_cv_pct(stride),
            stride_half_diff=_half_metric_diff(stride)["diff"] if stride else None,
            avg_gct=_avg_gct,
            gct_cv_pct=_cv_pct(gct),
            gct_half_diff=_half_metric_diff(gct)["diff"] if gct else None,
            avg_vo=_avg_vo,
            vo_cv_pct=_cv_pct(vo),
            vo_half_diff=_half_metric_diff(vo)["diff"] if vo else None,
            avg_vertical_ratio=_avg_vr,
            hr_cv_pct=_cv_pct(_hr_pairs),
            hr_half_diff=_half_metric_diff(_hr_pairs)["diff"] if _hr_pairs else None,
            evidence=(_evidence("structure.n_splits", "splitSummaries", len(splits)),),
        )
        total_ascent = ascent if ascent is not None else (split_ascent or None)
        terrain = TerrainFacts(
            ascent_m=round(total_ascent, 2) if total_ascent is not None else None,
            gain_per_km=round(total_ascent / (distance / 1000), 2) if total_ascent is not None and distance > 0 else None,
            grade_profile=_bands(metric_grade, (-5, 0, 5, 10)) if metric_grade else None,
            evidence=(_evidence("terrain.ascent_m", "summaryDTO/elevationGain or splits", total_ascent),),
        )
    missing = []
    if not duration: missing.append("duration")
    if not distance: missing.append("distance")
    if not splits: missing.append("splits")
    if not hr_values: missing.append("heart_rate")

    # ── v2: 训练效果（Provider 原始 TE，估算在分析层） ──
    te = _first(source, "trainingEffect", "aerobicTrainingEffect")
    ate = _first(source, "anaerobicTrainingEffect")
    te_label = _string(source, "trainingEffectLabel")
    effect = None
    if te is not None or ate is not None or te_label:
        effect = EffectFacts(
            aerobic_training_effect=te,
            anaerobic_training_effect=ate,
            label=te_label,
            message=_string(source, "aerobicTrainingEffectMessage"),
            moderate_intensity_minutes=_first(
                source, "moderateIntensityMinutes", "moderate_intensity_minutes",
            ),
            vigorous_intensity_minutes=_first(
                source, "vigorousIntensityMinutes", "vigorous_intensity_minutes",
            ),
            evidence=(_evidence("effect.source", "summaryDTO", "provider_raw"),),
        )

    # ── v2: 地形全貌（爬升/下降/海拔范围） ──
    descent = _first(source, "elevationLoss", "elevation_loss", "descent")
    max_elevation = _first(source, "maxElevation", "max_elevation")
    min_elevation = _first(source, "minElevation", "min_elevation")
    elevation_profile = None
    if (
        ascent is not None or descent is not None
        or max_elevation is not None or min_elevation is not None
    ):
        elevation_profile = ElevationProfileFacts(
            ascent_m=ascent,
            descent_m=descent,
            max_elevation_m=max_elevation,
            min_elevation_m=min_elevation,
            gain_per_km=(
                round(ascent / (distance / 1000), 2)
                if ascent is not None and distance > 0 else None
            ),
            evidence=(_evidence(
                "elevation_profile.source", "summaryDTO",
                {"ascent": ascent, "descent": descent},
            ),),
        )

    # ── v2: 配速画像（分段粒度） ──
    pace_pairs = [(p, d) for p, d in split_values if p and d > 0]
    fastest_pace = _first(source, "fastestPace")
    max_speed = _first(source, "maxSpeed")
    if max_speed and max_speed > 0:
        fastest_pace = round(1000.0 / max_speed, 1)
    if not fastest_pace and pace_pairs:
        fastest_pace = round(min(p for p, _ in pace_pairs), 1)
    pace_profile = None
    if pace_pairs:
        half = _half_metric_diff(pace_pairs)
        overall_pace = (
            duration / (distance / 1000)
            if duration > 0 and distance > 0
            else _weighted(pace_pairs)
        )
        pace_profile = PaceProfileFacts(
            avg_pace_sec_per_km=round(overall_pace, 1) if overall_pace else None,
            fastest_pace_sec_per_km=fastest_pace,
            p5=_weighted_quantile(pace_pairs, 0.05),
            p25=_weighted_quantile(pace_pairs, 0.25),
            p50=_weighted_quantile(pace_pairs, 0.50),
            p75=_weighted_quantile(pace_pairs, 0.75),
            p95=_weighted_quantile(pace_pairs, 0.95),
            cv_pct=(
                round((pstdev([p for p, _ in pace_pairs]) / (mean([p for p, _ in pace_pairs]) or 1)) * 100, 2)
                if len(pace_pairs) > 1 else None
            ),
            half_pace_diff_s=half["diff"],
            positive_split=(
                (half["diff"] or 0) > 0 if half["diff"] is not None else None
            ),
            split_count=len(pace_pairs),
            basis="split",
            evidence=(_evidence(
                "pace_profile.basis", "splitSummaries", len(pace_pairs),
            ),),
        )

    # ── v2: 复合训练结构（splitType / work-recovery），仅分段数据存在时 ──
    structure_profile = None
    if splits:
        split_types = tuple(
            str(_string(s, "splitType", "split_type", "type", "lapType", "stepType") or "unknown")
            for s in splits
        )
        work_blocks, recovery_blocks = _work_recovery_blocks(splits)
        structure_profile = StructureProfileFacts(
            split_types=split_types,
            work_blocks=work_blocks,
            recovery_blocks=recovery_blocks,
            composite_type=_composite_type(
                work_blocks, recovery_blocks,
                str(source.get("activityName") or detail.get("activityName") or ""),
                split_types,
            ),
            evidence=(_evidence(
                "structure_profile.split_types", "splitSummaries", len(split_types),
            ),),
        )
    # ── v: 逐段强度/效率序列（配速=比值抵消后真实；距离/时长可能不可信） ──
    segment_sequence = []
    for s in splits:
        pace = _first(s, "pace", "pace_per_km", "pace_sec_per_km")
        if pace is None:
            # Garmin splitSummaries 用 averageSpeed（m/s）；speed 与 duration 同源，
            # ×2 异常下比值抵消仍真实
            speed = _first(s, "averageSpeed", "averageMovingSpeed", "speed")
            if speed and speed > 0:
                pace = 1000.0 / speed
        segment_sequence.append({
            "pace_sec_per_km": round(pace, 1) if pace else None,
            "duration_s": _first(s, "duration", "duration_sec", "durationSeconds"),
            "distance_m": _first(s, "distance", "distance_m", "distanceMeters"),
            "avg_hr": _first(s, "averageHR", "avgHr", "avg_hr"),
            "avg_cadence": _first(s, "averageRunCadence", "avgCadence", "avg_cadence"),
            "stride_length_cm": _first(s, "strideLength", "stride_length_cm", "avgStepLength"),
            "split_type": _string(s, "intensityType", "splitType", "split_type", "type", "lapType", "stepType"),
        })

    # ── v: 量特征门禁（分段距离合计 vs summary 距离） ──
    quantity_gate = {}
    if distance and splits:
        splits_distance = sum(_num(s.get("distance")) or 0 for s in splits)
        ratio = splits_distance / distance if distance else 0.0
        quantity_gate = {
            "ratio": round(ratio, 2),
            "quantity_reliable": 0.85 <= ratio <= 1.15,
        }

    source_hash = hashlib.sha256(
        json.dumps(detail, sort_keys=True, default=str).encode()
    ).hexdigest()
    fact_digest = hashlib.sha256(
        f"{SESSION_SUMMARY_SCHEMA_VERSION}:{SESSION_SUMMARY_RULE_VERSION}:{source_hash}".encode()
    ).hexdigest()[:12]
    return SessionSummaryFacts(
        schema_version=SESSION_SUMMARY_SCHEMA_VERSION,
        fact_version=f"{SESSION_SUMMARY_SCHEMA_VERSION}:{fact_digest}",
        granularity=granularity,
        volume=VolumeFacts(duration_s=duration, distance_m=distance, calories=calories, load=load,
                           elapsed_duration_s=elapsed_duration, evidence=volume_evidence),
        structure=structure, intensity=intensity, terrain=terrain,
        effect=effect,
        elevation_profile=elevation_profile,
        pace_profile=pace_profile,
        structure_profile=structure_profile,
        segment_sequence=tuple(segment_sequence),
        quantity_gate=quantity_gate,
        data_quality={"missing_fields": missing, "intensity_basis": basis, "source_hash": source_hash},
    )


def estimate_training_effect(
    splits: Iterable[dict[str, Any]],
    *,
    threshold_heart_rate: float | None = None,
    threshold_pace_sec_per_km: float | None = None,
) -> dict[str, Any] | None:
    """训练效果缺失时的本地估算（必须显式标记 estimated）。

    用个人阈值把逐段强度换算为 zone 分钟加权刺激分，映射为 0–5 的近似 TE。
    心率优先；心率缺失时用配速代理（``estimate_basis="pace"``）。
    阈值与两种强度依据都不可用时返回 None（“不可得”），不伪造数值。
    """
    rows: list[tuple[float, float | None, float | None]] = []
    for split in splits:
        duration_min = (
            _first(split, "duration_sec", "duration", "durationSeconds") or 0
        ) / 60
        if duration_min <= 0:
            continue
        hr = _first(split, "avg_hr", "averageHR", "avgHr")
        pace = _first(split, "pace_per_km", "pace", "pace_sec_per_km")
        rows.append((duration_min, hr, pace))
    if not rows:
        return None

    if threshold_heart_rate:
        used = [(d, hr) for d, hr, _ in rows if hr and hr > 0]
        basis = "heart_rate"
    elif threshold_pace_sec_per_km:
        used = [(d, pace) for d, _, pace in rows if pace and pace > 0]
        basis = "pace"
    else:
        return None
    if not used:
        return None

    total_stimulus = 0.0
    for duration_min, value in used:
        if basis == "heart_rate":
            intensity = value / threshold_heart_rate
        else:
            # 配速 sec/km 越小越快：强度比 = 阈值 / 实际
            intensity = threshold_pace_sec_per_km / value
        if intensity < 0.75:
            weight = 0.3
        elif intensity < 0.88:
            weight = 0.6
        elif intensity < 1.0:
            weight = 1.0
        elif intensity < 1.1:
            weight = 1.6
        else:
            weight = 2.2
        total_stimulus += duration_min * weight

    # 刺激分 → 0–5 近似 TE（校准：60min Z3 约 2.5，40min 恢复课约 1.3）
    estimated = round(min(5.0, max(1.0, 1.0 + total_stimulus / 40.0)), 1)
    return {
        "aerobic_training_effect": estimated,
        "anaerobic_training_effect": None,
        "label": None,
        "message": None,
        "moderate_intensity_minutes": None,
        "vigorous_intensity_minutes": None,
        "estimated": True,
        "estimate_method": "zone_minutes_weighted",
        "estimate_basis": basis,
        "evidence": [{
            "kind": "estimate",
            "source": "activity_splits",
            "field": f"effect.aerobic_training_effect",
            "value": estimated,
            "confidence": None,
        }],
    }


def compact_session_summary_for_planning(summary: dict[str, Any]) -> dict[str, Any]:
    """草稿/周报消费侧投影：从统一 session-summary 压缩出规划/复盘需要的概要。

    数据源仍是同一份 ``activity_summary_facts``；这里只选择子集字段以减小
    事实包载荷，不产生第二套结构。日报保持全量消费。
    """
    if not isinstance(summary, dict):
        return {}
    volume = summary.get("volume") or {}
    effect = summary.get("effect") or {}
    intensity = summary.get("intensity") or {}
    pace_profile = summary.get("pace_profile") or {}
    structure_profile = summary.get("structure_profile") or {}
    structure = summary.get("structure") or {}
    return {
        "schema_version": summary.get("schema_version"),
        "fact_version": summary.get("fact_version"),
        "granularity": summary.get("granularity"),
        "volume": {
            "duration_s": volume.get("duration_s"),
            "distance_m": volume.get("distance_m"),
        },
        "effect": {
            key: effect.get(key)
            for key in (
                "aerobic_training_effect", "anaerobic_training_effect",
                "label", "estimated", "estimate_basis",
            )
        },
        "intensity": {"basis": intensity.get("basis")},
        "pace_profile": {
            key: pace_profile.get(key)
            for key in (
                "avg_pace_sec_per_km", "cv_pct",
                "half_pace_diff_s", "positive_split",
            )
        },
        "structure": {
            key: structure.get(key)
            for key in (
                "avg_cadence", "max_cadence",
                "cadence_cv_pct", "cadence_half_diff",
                "avg_stride", "stride_cv_pct", "stride_half_diff",
                "avg_gct", "gct_cv_pct", "gct_half_diff",
                "avg_vo", "vo_cv_pct", "vo_half_diff",
                "avg_vertical_ratio",
                "hr_cv_pct", "hr_half_diff",
            )
        },
        "structure_profile": {
            key: structure_profile.get(key)
            for key in ("composite_type", "work_blocks", "recovery_blocks")
        },
        # 逐段序列：供草稿/周报在分析层做结构判定（配速/心率/步频/步幅）
        "segment_sequence": summary.get("segment_sequence"),
        "quantity_gate": {
            key: (summary.get("quantity_gate") or {}).get(key)
            for key in ("ratio", "quantity_reliable")
        },
    }


# 训练结构判定权重（配速/心率主证据，步频/步幅辅助）
_WEIGHT_PACE = 0.40
_WEIGHT_HR = 0.35
_WEIGHT_CADENCE = 0.15
_WEIGHT_STRIDE = 0.10
_FAST_PACE_RATIO = 0.88      # 快段：配速 ≤ 主体 × 0.88（快 ≥12%）
_SLOW_PACE_RATIO = 1.15      # 慢段：配速 ≥ 主体 × 1.15（慢 ≥15%）
_HR_RISE_RATIO = 1.08        # 心率抬升 ≥8% 确认


def _median(values: Iterable[float]) -> float | None:
    ordered = sorted(float(v) for v in values if v is not None and v > 0)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def classify_training_structure(
    segment_sequence: Iterable[dict[str, Any]],
    *,
    threshold_heart_rate: float | None = None,
    threshold_pace_sec_per_km: float | None = None,
    quantity_reliable: bool = True,
    activity_name: str = "",
) -> dict[str, Any]:
    """确定性六类训练结构判定（加权证据，不用 AI）。

    权重：配速 0.40 / 心率 0.35 / 步频 0.15 / 步幅 0.10。
    快段 = 配速比主体快 ≥12%（心率抬升 ≥8% 确认）；交替组数 ≥2 → 变速/间歇；
    无交替按主体强度带分节奏（Z3）/有氧（Z2）；主证据不足 → unknown。
    四维度独立降级，缺失只降置信度与成分精度。
    """
    segments = [s for s in segment_sequence if isinstance(s, dict)]
    if not segments:
        return {"structure_type": "unknown", "label": "未知",
                "confidence": 0.0, "reason": "无分段数据"}

    paces = [s.get("pace_sec_per_km") for s in segments if s.get("pace_sec_per_km")]
    hrs = [s.get("avg_hr") for s in segments if s.get("avg_hr")]
    if not paces:
        return {"structure_type": "unknown", "label": "未知",
                "confidence": 0.0, "reason": "无配速序列"}
    has_pace = bool(paces)
    has_hr = bool(hrs)

    body_pace = _median(paces)
    if not body_pace:
        return {"structure_type": "unknown", "label": "未知",
                "confidence": 0.0, "reason": "主体配速不可得"}

    # ── 快/慢段标记（配速相对主体；心率、步频、步幅辅助） ──
    roles: list[str] = []
    alternations = 0
    cadence_responses = 0
    stride_responses = 0
    fast_hr_confirmed = 0
    for index, segment in enumerate(segments):
        pace = segment.get("pace_sec_per_km")
        role = "body"
        if pace and pace <= body_pace * _FAST_PACE_RATIO:
            role = "fast"
        elif pace and pace >= body_pace * _SLOW_PACE_RATIO:
            role = "slow"
        roles.append(role)
        if role == "fast":
            hr = segment.get("avg_hr")
            prev_hr = segments[index - 1].get("avg_hr") if index > 0 else None
            if hr and prev_hr and hr >= prev_hr * _HR_RISE_RATIO:
                fast_hr_confirmed += 1
            if segment.get("avg_cadence") and segments[index - 1].get("avg_cadence"):
                if segment["avg_cadence"] > segments[index - 1]["avg_cadence"]:
                    cadence_responses += 1
            if segment.get("stride_length_cm") and segments[index - 1].get("stride_length_cm"):
                if segment["stride_length_cm"] > segments[index - 1]["stride_length_cm"]:
                    stride_responses += 1
    for index in range(1, len(roles)):
        if roles[index] == "fast" and roles[index - 1] in ("body", "slow"):
            alternations += 1

    fast_count = roles.count("fast")
    slow_count = roles.count("slow")

    # ── 每组 work/recovery 明细（快段配速 → 恢复段配速，供展示与引用） ──
    work_recovery_groups = []
    for index in range(len(segments)):
        if roles[index] != "fast":
            continue
        work = segments[index]
        work_pace = work.get("pace_sec_per_km")
        recovery_index = None
        if work_pace:
            for offset in range(index + 1, min(index + 4, len(segments))):
                candidate = segments[offset]
                candidate_pace = candidate.get("pace_sec_per_km")
                if not candidate_pace:
                    continue
                # 恢复段：比快段慢 ≥20%；排除微型段（时长 <20s，如中断/起点段）
                duration_s = candidate.get("duration_s")
                if duration_s is not None and float(duration_s) < 20:
                    continue
                if candidate_pace >= work_pace * 1.2:
                    recovery_index = offset
                    break
        recovery = segments[recovery_index] if recovery_index is not None else None
        work_recovery_groups.append({
            "group": len(work_recovery_groups) + 1,
            "work_pace_sec_per_km": work_pace,
            "work_distance_m": work.get("distance_m"),
            "work_duration_s": work.get("duration_s"),
            "work_avg_hr": work.get("avg_hr"),
            "work_cadence": work.get("avg_cadence"),
            "work_stride_cm": work.get("stride_length_cm"),
            "recovery_pace_sec_per_km": recovery.get("pace_sec_per_km") if recovery else None,
            "recovery_distance_m": recovery.get("distance_m") if recovery else None,
            "recovery_duration_s": recovery.get("duration_s") if recovery else None,
            "recovery_avg_hr": recovery.get("avg_hr") if recovery else None,
        })

    # ── 主体强度带（个人阈值，心率优先；配速代理） ──
    body_intensity = None
    body_hr = _median(hrs) if hrs else None
    if body_hr and threshold_heart_rate:
        body_intensity = body_hr / threshold_heart_rate
        intensity_basis = "heart_rate"
    elif body_pace and threshold_pace_sec_per_km:
        body_intensity = threshold_pace_sec_per_km / body_pace
        intensity_basis = "pace"
    else:
        intensity_basis = "unavailable"

    # ── 加权置信度 ──
    confidence = 0.0
    missing: list[str] = []
    if has_pace:
        confidence += _WEIGHT_PACE
    else:
        missing.append("配速")
    if has_hr:
        confidence += _WEIGHT_HR
    else:
        missing.append("心率")
    if any(s.get("avg_cadence") for s in segments):
        confidence += _WEIGHT_CADENCE
    else:
        missing.append("步频")
    if any(s.get("stride_length_cm") for s in segments):
        confidence += _WEIGHT_STRIDE
    else:
        missing.append("步幅")

    # ── 六类判定 ──
    structure_type = "unknown"
    label = "未知"
    composition: list[dict[str, Any]] = []
    # ── 结构组成占比：距离可信时按距离口径（快段占比 = 快段距离 / 总距离），
    #    否则回退按段数口径；basis 标记来源供下游解释。 ──
    role_order = lambda r: ("fast", "slow", "body").index(r) if r in ("fast", "slow", "body") else 9
    dist_by_role: dict[str, float] = {}
    for role, segment in zip(roles, segments):
        dist = _num(segment.get("distance_m")) or 0.0
        dist_by_role[role] = dist_by_role.get(role, 0.0) + dist
    total_dist = sum(dist_by_role.values())
    use_distance = bool(total_dist > 0 and quantity_reliable)
    if use_distance:
        role_pcts = {
            role: round(dist_by_role[role] / total_dist * 100, 1)
            for role in sorted(set(roles), key=role_order)
        }
        basis = "distance"
    else:
        role_pcts = {
            role: round(roles.count(role) / len(roles) * 100, 1)
            for role in sorted(set(roles), key=role_order)
        }
        basis = "segment_count"
    composition = [
        {"role": role, "pct": pct, "basis": basis}
        for role, pct in role_pcts.items()
    ]

    if alternations >= 2:
        # 间歇 vs 变速：设备间歇标记（INTERVAL_*）优先；其次快段达阈值强度
        has_interval_markers = any(
            "interval" in str(segment.get("split_type") or "").lower()
            and "warmup" not in str(segment.get("split_type") or "").lower()
            for segment in segments
        )
        fast_paces = [s.get("pace_sec_per_km") for s in segments if s.get("pace_sec_per_km")]
        fast_intensity_high = False
        if fast_paces and threshold_pace_sec_per_km:
            # 快段配速严格快于阈值配速才算达阈值强度（等于不算）
            fast_intensity_high = (
                min(fast_paces) < threshold_pace_sec_per_km
            )
        if threshold_heart_rate and hrs:
            fast_hrs = [segments[i].get("avg_hr") for i in range(len(segments)) if roles[i] == "fast" and segments[i].get("avg_hr")]
            if fast_hrs and max(fast_hrs) >= threshold_heart_rate:
                fast_intensity_high = True
        if has_interval_markers or fast_intensity_high:
            structure_type = "interval"
            label = "间歇"
        else:
            structure_type = "fartlek"
            label = "变速"
    elif fast_count >= 1:
        structure_type = "mixed"
        label = "混合"
    elif body_intensity is not None and 0.88 <= body_intensity < 1.12:
        structure_type = "tempo"
        label = "节奏"
    elif body_intensity is not None and 0.75 <= body_intensity < 0.88:
        structure_type = "aerobic"
        label = "有氧"
    elif body_intensity is None:
        structure_type = "unknown"
        label = "未知"
        missing.append("个人阈值")
    else:
        structure_type = "aerobic"
        label = "有氧"

    # ── 疲劳复合信号（心率漂移 + 步幅下降） ──
    fatigue = _fatigue_signal(segments)

    # ── 步频一致性（效率特征） ──
    cadence_values = [
        s.get("avg_cadence") for s in segments
        if s.get("avg_cadence") and s["avg_cadence"] >= 100
    ]
    cadence_consistency = None
    if len(cadence_values) > 1:
        mean_cadence = sum(cadence_values) / len(cadence_values)
        if mean_cadence:
            cadence_consistency = {
                "avg": round(mean_cadence, 1),
                "cv_pct": round(
                    (sum((c - mean_cadence) ** 2 for c in cadence_values) / len(cadence_values)) ** 0.5 / mean_cadence * 100, 2,
                ),
            }

    return {
        "structure_type": structure_type,
        "label": label,
        "confidence": round(min(1.0, confidence), 2),
        "missing_evidence": missing,
        "alternations": alternations,
        "fast_segments": fast_count,
        "slow_segments": slow_count,
        "work_recovery_groups": work_recovery_groups,
        "composition": composition,
        "body_pace_sec_per_km": round(body_pace, 1),
        "body_intensity": round(body_intensity, 3) if body_intensity is not None else None,
        "intensity_basis": intensity_basis,
        "quantity_reliable": quantity_reliable,
        "cadence_consistency": cadence_consistency,
        "fatigue_signal": fatigue,
        "evidence": [
            {"kind": "fact", "source": "segment_sequence", "field": "alternations", "value": alternations},
            {"kind": "fact", "source": "segment_sequence", "field": "fast_segments", "value": fast_count},
            {"kind": "fact", "source": "segment_sequence", "field": "confidence", "value": round(min(1.0, confidence), 2)},
        ],
    }


_NON_RUNNING_SPLIT_MARKERS = ("walk", "stand", "warmup", "cooldown", "rest")


def _fatigue_signal(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """疲劳复合信号：心率漂移（后半 vs 前半）+ 步幅后半下降 + 配速维持。

    只比较跑步主体段（排除 walk/stand/warmup/cooldown），避免热身心率
    拉低前半基线导致漂移误报。
    """
    running = [
        s for s in segments
        if not any(
            marker in str(s.get("split_type") or "").lower()
            for marker in _NON_RUNNING_SPLIT_MARKERS
        )
    ]
    if len(running) < 3:
        return {"detected": False, "note": "主体分段过少，无法判断疲劳"}
    half = len(running) // 2
    first = running[:half]
    second = running[half:]

    def _half_mean(block: list[dict[str, Any]], key: str) -> float | None:
        values = [s.get(key) for s in block if s.get(key)]
        return sum(values) / len(values) if values else None

    first_hr = _half_mean(first, "avg_hr")
    second_hr = _half_mean(second, "avg_hr")
    first_stride = _half_mean(first, "stride_length_cm")
    second_stride = _half_mean(second, "stride_length_cm")
    first_pace = _half_mean(first, "pace_sec_per_km")
    second_pace = _half_mean(second, "pace_sec_per_km")

    drift = None
    stride_drop = None
    if first_hr and second_hr:
        drift = round(second_hr - first_hr, 1)
    if first_stride and second_stride:
        stride_drop = round((second_stride - first_stride) / first_stride * 100, 1)

    pace_held = bool(first_pace and second_pace and second_pace <= first_pace * 1.05)
    detected = bool(
        drift is not None and drift >= 5
        and stride_drop is not None and stride_drop <= -2
    )
    if not detected and drift is not None and drift >= 5 and pace_held:
        detected = True
    note_parts = []
    if drift is not None and drift >= 5:
        note_parts.append(f"心率漂移 +{drift:g}bpm")
    if stride_drop is not None and stride_drop <= -2:
        note_parts.append(f"步幅后半程下降 {abs(stride_drop):g}%")
    if not detected:
        return {"detected": False, "note": "未检出明显疲劳信号"}
    return {
        "detected": True,
        "heart_rate_drift_bpm": drift,
        "stride_drop_pct": stride_drop,
        "note": "、".join(note_parts) or "心率漂移与步幅下降组合",
    }
