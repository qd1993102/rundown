"""跑步分析引擎共享常量和工具函数。

本模块不依赖项目其他模块，可以独立使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any, Iterable

# ═══════════════════════════════════════════════════════════════════════════════
# S1 — 数据清洗阈值
# ═══════════════════════════════════════════════════════════════════════════════

PACE_MIN_SEC_PER_KM = 120       # 2:00/km（世界纪录级，业余不可达）
PACE_CONSISTENCY_RATIO = 0.3    # 自洽性偏差 30%
HR_SPIKE_DELTA_BPM = 40         # 相邻段跳变阈值
HR_MIN = 60
HR_MAX = 220
CADENCE_MIN = 120
CADENCE_MAX = 220
STRIDE_MIN_CM = 50
STRIDE_MAX_CM = 200

# ═══════════════════════════════════════════════════════════════════════════════
# S4 — 有氧漂移阈值
# ═══════════════════════════════════════════════════════════════════════════════

STEADY_MIN_SEGMENTS = 3
STEADY_MIN_DURATION_MINUTES = 20
STEADY_PACE_SIGMA = 2.0          # 配速在 ±2σ 内视为稳态
DRIFT_EXCELLENT = 3.0
DRIFT_NORMAL = 5.0
DRIFT_ELEVATED = 8.0
PACE_HR_CORRECTION = 1.0        # 配速每快 1s/km，心率修正 1bpm

# ═══════════════════════════════════════════════════════════════════════════════
# S5 — 经济性阈值
# ═══════════════════════════════════════════════════════════════════════════════

ECONOMY_SCALE = 1000
HIGH_CADENCE = 180
LOW_CADENCE = 170
LONG_STRIDE_CM = 115
EFFICIENT_STRIDE_CM = 110

# ═══════════════════════════════════════════════════════════════════════════════
# S6 — 疲劳代偿阈值
# ═══════════════════════════════════════════════════════════════════════════════

DECAY_PACE_PCT = 3.0            # 掉速 ≥3% 视为明显
STRIDE_DROP_PCT = -3.0
CADENCE_DROP_PCT = -2.0
HR_RISE_PCT = 2.0
CV_CHANGE = 0.5
DECAY_ONSET_PACE_PCT = 5.0      # 单段比前两段均值慢 ≥5%

# ═══════════════════════════════════════════════════════════════════════════════
# 非跑步段标记
# ═══════════════════════════════════════════════════════════════════════════════

NON_RUNNING_SPLIT_MARKERS = (
    "walk", "stand", "warmup", "cooldown", "cool",
    "rest", "recovery", "jog", "transition", "stop",
)


def _is_running_segment(split_type: str | None) -> bool:
    if not split_type:
        return True  # 未知类型默认视为跑步段
    return not any(
        marker in split_type.lower()
        for marker in NON_RUNNING_SPLIT_MARKERS
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 共享数据类
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CleanedSegment:
    """清洗后的单段数据，保留原始值和清洗标记。"""

    index: int
    pace_sec_per_km: float | None
    avg_hr: float | None
    avg_cadence: float | None
    stride_length_cm: float | None
    duration_s: float | None
    distance_m: float | None
    split_type: str | None
    # 清洗标记
    quality_flags: tuple[str, ...] = ()
    # 原始值引用（清洗前）
    original_pace: float | None = None
    original_hr: float | None = None
    original_cadence: float | None = None
    original_stride: float | None = None


@dataclass(frozen=True)
class CleaningReport:
    """清洗报告：整体质量评估。"""

    total_segments: int
    cleaned_segments: int
    flags_summary: dict[str, int] = field(default_factory=dict)
    overall_quality: str = "good"  # good | degraded | poor
    segments: tuple[CleanedSegment, ...] = ()


# ═══════════════════════════════════════════════════════════════════════════════
# 共享工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def weighted_mean(
    pairs: Iterable[tuple[float, float]],
) -> float | None:
    """时长加权平均。

    Args:
        pairs: (value, weight) 的迭代器，weight 通常为 duration_s。

    Returns:
        加权均值，无有效值时返回 None。
    """
    total_weight = 0.0
    total_value = 0.0
    for value, weight in pairs:
        if value is not None and weight > 0:
            total_value += value * weight
            total_weight += weight
    return total_value / total_weight if total_weight > 0 else None


def segment_weighted_mean(
    segments: Iterable[CleanedSegment],
    attr: str,
) -> float | None:
    """对 CleanedSegment 序列按 duration_s 加权计算指定属性的均值。

    Args:
        segments: CleanedSegment 序列。
        attr: 属性名，如 "pace_sec_per_km"、"avg_hr"。

    Returns:
        加权均值，无有效值时返回 None。
    """
    pairs = [
        (getattr(s, attr), s.duration_s or 0)
        for s in segments
        if getattr(s, attr) is not None
    ]
    return weighted_mean(pairs)


def cv_pct(values: Iterable[float]) -> float | None:
    """计算变异系数（百分比）。

    Args:
        values: 数值序列。

    Returns:
        CV = σ / μ × 100，序列不足 2 个时返回 None。
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mu = mean(vals)
    if mu == 0:
        return None
    return pstdev(vals) / mu * 100


def _mid(values: list[float]) -> float | None:
    """返回中位数（与研究提取保持一致）。"""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2