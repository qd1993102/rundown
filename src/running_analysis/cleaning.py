"""S1 — 数据清洗与异常修复。

对原始分段序列做 GPS 漂移检测、HR 跳变检测、步频/步幅异常值剔除，
输出 CleaningReport 供下游分析模块消费。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from ._shared import (
    CADENCE_MAX,
    CADENCE_MIN,
    CleanedSegment,
    CleaningReport,
    HR_MAX,
    HR_MIN,
    HR_SPIKE_DELTA_BPM,
    PACE_CONSISTENCY_RATIO,
    PACE_MIN_SEC_PER_KM,
    STRIDE_MAX_CM,
    STRIDE_MIN_CM,
)


def clean_segment_sequence(
    segment_sequence: Iterable[dict[str, Any]],
    *,
    data_quality: dict[str, Any] | None = None,
) -> CleaningReport:
    """清洗分段序列，返回 CleaningReport。

    不修改输入，不清洗 provider 原始数据。
    缺失数据直接继承为 None，不编造。

    Args:
        segment_sequence: 原始分段 dict 序列，每个 dict 包含
            pace_sec_per_km, avg_hr, avg_cadence, stride_length_cm,
            duration_s, distance_m, split_type 等字段。
        data_quality: 已有质量标记（可选，用于上下文）。

    Returns:
        CleaningReport 包含清洗后的分段和整体质量评级。
    """
    segments = list(segment_sequence)
    if not segments:
        return CleaningReport(
            total_segments=0,
            cleaned_segments=0,
            overall_quality="good",
            segments=(),
        )

    # ── 第一步：提取原始值 ──
    raw: list[dict[str, Any]] = []
    for index, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        raw.append({
            "index": seg.get("index", index),
            "pace_sec_per_km": seg.get("pace_sec_per_km"),
            "avg_hr": seg.get("avg_hr"),
            "avg_cadence": seg.get("avg_cadence"),
            "stride_length_cm": seg.get("stride_length_cm"),
            "duration_s": seg.get("duration_s"),
            "distance_m": seg.get("distance_m"),
            "split_type": seg.get("split_type"),
        })

    if not raw:
        return CleaningReport(
            total_segments=0,
            cleaned_segments=0,
            overall_quality="good",
            segments=(),
        )

    # ── 第二步：清洗 ──
    cleaned: list[CleanedSegment] = []
    flags_summary: dict[str, int] = {}

    for i, seg in enumerate(raw):
        flags: list[str] = []
        pace = seg["pace_sec_per_km"]
        hr = seg["avg_hr"]
        cadence = seg["avg_cadence"]
        stride = seg["stride_length_cm"]
        duration = seg["duration_s"]
        distance = seg["distance_m"]

        original_pace = pace
        original_hr = hr
        original_cadence = cadence
        original_stride = stride

        # ── GPS 漂移检测 ──
        if pace is not None and pace < PACE_MIN_SEC_PER_KM:
            flags.append("gps_drift")
            # 用相邻段插值
            pace = _interpolate_pace(raw, i, "pace_sec_per_km")

        # 自洽性检查：距离/时长计算出的配速 vs 声明的配速
        if (
            pace is not None
            and distance is not None
            and duration is not None
            and distance > 0
            and duration > 0
        ):
            computed = duration / (distance / 1000)
            if computed > 0:
                deviation = abs(computed - pace) / computed
                if deviation > PACE_CONSISTENCY_RATIO:
                    if "gps_drift" not in flags:
                        flags.append("gps_inconsistent")
                    pace = computed

        # ── 心率异常检测 ──
        if hr is not None:
            if hr < HR_MIN or hr > HR_MAX:
                flags.append("hr_outlier")
                hr = None
            elif i > 0 and raw[i - 1].get("avg_hr") is not None:
                delta = abs(hr - raw[i - 1]["avg_hr"])
                if delta > HR_SPIKE_DELTA_BPM:
                    flags.append("hr_spike")
                    hr = _interpolate_hr(raw, i, "avg_hr")

        # ── 步频异常 ──
        if cadence is not None and (cadence < CADENCE_MIN or cadence > CADENCE_MAX):
            flags.append("cadence_outlier")
            cadence = None

        # ── 步幅异常 ──
        if stride is not None and (stride < STRIDE_MIN_CM or stride > STRIDE_MAX_CM):
            flags.append("stride_outlier")
            stride = None

        for flag in flags:
            flags_summary[flag] = flags_summary.get(flag, 0) + 1

        cleaned.append(CleanedSegment(
            index=seg["index"],
            pace_sec_per_km=pace,
            avg_hr=hr,
            avg_cadence=cadence,
            stride_length_cm=stride,
            duration_s=duration,
            distance_m=distance,
            split_type=seg["split_type"],
            quality_flags=tuple(flags),
            original_pace=original_pace if original_pace != pace else None,
            original_hr=original_hr if original_hr != hr else None,
            original_cadence=original_cadence if original_cadence != cadence else None,
            original_stride=original_stride if original_stride != stride else None,
        ))

    # ── 第三步：整体质量评级 ──
    cleaned_count = sum(flags_summary.values())
    total = len(cleaned)
    if cleaned_count == 0:
        quality = "good"
    elif cleaned_count <= total * 0.2:
        quality = "degraded"
    else:
        quality = "poor"

    return CleaningReport(
        total_segments=total,
        cleaned_segments=cleaned_count,
        flags_summary=flags_summary,
        overall_quality=quality,
        segments=tuple(cleaned),
    )


def _interpolate_pace(
    raw: list[dict[str, Any]],
    index: int,
    key: str,
) -> float | None:
    """用相邻段插值替换异常段。"""
    prev_val = None
    next_val = None
    if index > 0:
        prev_val = raw[index - 1].get(key)
    if index < len(raw) - 1:
        next_val = raw[index + 1].get(key)
    if prev_val is not None and next_val is not None:
        return (prev_val + next_val) / 2
    if prev_val is not None:
        return prev_val
    if next_val is not None:
        return next_val
    return None


def _interpolate_hr(
    raw: list[dict[str, Any]],
    index: int,
    key: str,
) -> float | None:
    """用前后两段均值插值替换 HR 跳变段。"""
    prev_val = None
    next_val = None
    if index > 0:
        prev_val = raw[index - 1].get(key)
    if index < len(raw) - 1:
        next_val = raw[index + 1].get(key)
    if prev_val is not None and next_val is not None:
        return (prev_val + next_val) / 2
    if prev_val is not None:
        return prev_val
    if next_val is not None:
        return next_val
    return None
