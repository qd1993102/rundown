"""S4 — 有氧漂移分析。

量化长时间稳态运动中同等配速下心率随时间上升的幅度。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any, Iterable

from ._shared import (
    CleanedSegment,
    DRIFT_ELEVATED,
    DRIFT_EXCELLENT,
    DRIFT_NORMAL,
    PACE_HR_CORRECTION,
    STEADY_MIN_DURATION_MINUTES,
    STEADY_MIN_SEGMENTS,
    STEADY_PACE_SIGMA,
    _is_running_segment,
    segment_weighted_mean,
)


@dataclass(frozen=True)
class AerobicDriftResult:
    """有氧漂移分析结果。"""

    status: str  # ok | insufficient_data | no_steady_segment
    drift_rate_pct: float | None = None
    grade: str | None = None  # excellent | normal | elevated | high
    first_half_hr: float | None = None
    second_half_hr: float | None = None
    first_half_pace: float | None = None
    second_half_pace: float | None = None
    steady_segment_count: int = 0
    steady_duration_minutes: float = 0.0
    pace_correction_applied: bool = False
    note: str | None = None


def analyze_aerobic_drift(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: Any = None,
) -> AerobicDriftResult:
    """分析有氧漂移。

    只在存在连续稳态段（≥3 段，≥20min）时计算漂移率。
    配速有明显变化时做配速归一化修正。

    Args:
        cleaned_segments: 清洗后的 CleanedSegment 序列。
        summary: SessionSummaryFacts（可选，当前未使用，保留扩展）。

    Returns:
        AerobicDriftResult 包含漂移率、分级和前后半程详情。
    """
    # ── 1. 筛选跑步主体段 ──
    running = [
        s for s in cleaned_segments
        if _is_running_segment(s.split_type)
    ]
    if not running:
        return AerobicDriftResult(
            status="insufficient_data",
            note="无跑步主体段",
        )

    # ── 2. 只保留有配速且有 HR 的段 ──
    valid = [
        s for s in running
        if s.pace_sec_per_km is not None and s.avg_hr is not None
    ]
    if len(valid) < STEADY_MIN_SEGMENTS:
        return AerobicDriftResult(
            status="insufficient_data",
            note=f"有效段不足（{len(valid)} < {STEADY_MIN_SEGMENTS}）",
        )

    # ── 3. 筛选稳态段（配速在 ±2σ 内） ──
    paces = [s.pace_sec_per_km for s in valid if s.pace_sec_per_km is not None]
    if len(paces) < 2:
        return AerobicDriftResult(
            status="insufficient_data",
            note="配速采样不足",
        )

    mu = mean(paces)
    sigma = pstdev(paces)
    if sigma == 0:
        # 所有配速一致，全部视为稳态
        steady = valid
    else:
        steady = [
            s for s in valid
            if s.pace_sec_per_km is not None
            and abs(s.pace_sec_per_km - mu) <= STEADY_PACE_SIGMA * sigma
        ]

    if len(steady) < STEADY_MIN_SEGMENTS:
        return AerobicDriftResult(
            status="no_steady_segment",
            note=f"稳态段不足（{len(steady)} < {STEADY_MIN_SEGMENTS}），配速波动过大",
        )

    # ── 4. 取最大连续稳态块 ──
    steady_block = _longest_contiguous(valid, steady)

    # 计算稳态段总时长
    total_duration_s = sum(
        (s.duration_s or 0) for s in steady_block
    )
    total_duration_min = total_duration_s / 60

    if len(steady_block) < STEADY_MIN_SEGMENTS:
        return AerobicDriftResult(
            status="no_steady_segment",
            note=f"最大连续稳态段只有 {len(steady_block)} 段",
        )

    if total_duration_min < STEADY_MIN_DURATION_MINUTES:
        return AerobicDriftResult(
            status="insufficient_data",
            note=f"稳态段总时长 {total_duration_min:.0f}min < {STEADY_MIN_DURATION_MINUTES}min",
            steady_segment_count=len(steady_block),
            steady_duration_minutes=round(total_duration_min, 1),
        )

    # ── 5. 分前后半程计算漂移 ──
    half = len(steady_block) // 2
    first_half = list(steady_block[:half])
    second_half = list(steady_block[half:])

    first_half_hr = segment_weighted_mean(first_half, "avg_hr")
    second_half_hr = segment_weighted_mean(second_half, "avg_hr")
    first_half_pace = segment_weighted_mean(first_half, "pace_sec_per_km")
    second_half_pace = segment_weighted_mean(second_half, "pace_sec_per_km")

    if (
        first_half_hr is None
        or second_half_hr is None
        or first_half_hr == 0
    ):
        return AerobicDriftResult(
            status="insufficient_data",
            note="前后半程心率不可得",
            steady_segment_count=len(steady_block),
            steady_duration_minutes=round(total_duration_min, 1),
        )

    # ── 6. 配速归一化修正 ──
    pace_correction = 0.0
    pace_correction_applied = False
    if (
        first_half_pace is not None
        and second_half_pace is not None
    ):
        # 配速每快 1s/km，心率修正 1bpm
        pace_correction = (second_half_pace - first_half_pace) * PACE_HR_CORRECTION
        if abs(pace_correction) > 0.5:
            pace_correction_applied = True

    corrected_drift = (second_half_hr - first_half_hr) - pace_correction
    drift_rate_pct = round(corrected_drift / first_half_hr * 100, 1)

    # ── 7. 分级 ──
    abs_drift = abs(drift_rate_pct)
    if abs_drift < DRIFT_EXCELLENT:
        grade = "excellent"
    elif abs_drift < DRIFT_NORMAL:
        grade = "normal"
    elif abs_drift < DRIFT_ELEVATED:
        grade = "elevated"
    else:
        grade = "high"

    # ── 8. 构建结果 ──
    note_parts = []
    if pace_correction_applied:
        direction = "变快" if pace_correction > 0 else "变慢"
        note_parts.append(
            f"配速修正：后半程{direction} {abs(second_half_pace - first_half_pace):.0f}s/km，"
            f"心率漂移原始值 {second_half_hr - first_half_hr:+.1f}bpm"
        )
    if grade == "high":
        note_parts.append("漂移偏高，可能提示脱水、过热或过度疲劳")
    elif grade == "elevated":
        note_parts.append("漂移偏高，注意补水降温和有氧基础")

    return AerobicDriftResult(
        status="ok",
        drift_rate_pct=drift_rate_pct,
        grade=grade,
        first_half_hr=round(first_half_hr, 1),
        second_half_hr=round(second_half_hr, 1),
        first_half_pace=round(first_half_pace, 1) if first_half_pace else None,
        second_half_pace=round(second_half_pace, 1) if second_half_pace else None,
        steady_segment_count=len(steady_block),
        steady_duration_minutes=round(total_duration_min, 1),
        pace_correction_applied=pace_correction_applied,
        note="；".join(note_parts) if note_parts else None,
    )


def _longest_contiguous(
    all_segments: list[CleanedSegment],
    steady_segments: list[CleanedSegment],
) -> list[CleanedSegment]:
    """在 all_segments 中找属于 steady_segments 的最大连续块。

    保持原始顺序，返回 steady_segments 中在 all_segments 里连续的最大子序列。
    """
    steady_set = set(id(s) for s in steady_segments)
    best: list[CleanedSegment] = []
    current: list[CleanedSegment] = []

    for s in all_segments:
        if id(s) in steady_set:
            current.append(s)
        else:
            if len(current) > len(best):
                best = current
            current = []

    if len(current) > len(best):
        best = current

    return best
