"""S5 — 跑步经济性评估。

量化同等配速下的"心脏成本"，支持历史基线对比和步态模式标签。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable

from ._shared import (
    CleanedSegment,
    ECONOMY_SCALE,
    EFFICIENT_STRIDE_CM,
    HIGH_CADENCE,
    LONG_STRIDE_CM,
    LOW_CADENCE,
    _is_running_segment,
    _mid,
    segment_weighted_mean,
)


@dataclass(frozen=True)
class EconomyResult:
    """跑步经济性评估结果。"""

    status: str  # ok | no_hr | insufficient_data
    economy_index: float | None = None
    economy_index_unit: str = "(m/s)/bpm × 1000"
    avg_pace_sec_per_km: float | None = None
    avg_hr: float | None = None
    avg_cadence: float | None = None
    avg_stride_cm: float | None = None
    gait_label: str | None = None
    # 历史对比
    baseline_available: bool = False
    baseline_economy: float | None = None
    baseline_sample_count: int = 0
    trend_pct: float | None = None
    trend_days: int = 0
    note: str | None = None


def assess_running_economy(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: Any = None,
    baseline: Any = None,
    historical_economies: Iterable[EconomyResult] = (),
) -> EconomyResult:
    """评估跑步经济性。

    需要心率数据；无心率时返回 no_hr。
    历史基线从近期同类活动中取中位数。

    Args:
        cleaned_segments: 清洗后的 CleanedSegment 序列。
        summary: SessionSummaryFacts（可选，当前未使用，保留扩展）。
        baseline: AthleteBaseline（可选，当前未使用，保留扩展）。
        historical_economies: 近期同类活动的 EconomyResult 序列，
            用于计算历史基线。

    Returns:
        EconomyResult 包含经济性指数、步态标签和历史趋势。
    """
    segments = list(cleaned_segments)
    if not segments:
        return EconomyResult(status="insufficient_data", note="无分段数据")

    # ── 筛选跑步主体段 ──
    running = [s for s in segments if _is_running_segment(s.split_type)]
    if not running:
        running = segments  # 全部视为跑步段

    # ── 计算整体均速和心率 ──
    avg_pace = segment_weighted_mean(running, "pace_sec_per_km")
    avg_hr = segment_weighted_mean(running, "avg_hr")

    if avg_hr is None or avg_hr == 0:
        return EconomyResult(
            status="no_hr",
            avg_pace_sec_per_km=round(avg_pace, 1) if avg_pace else None,
            note="无心率数据，无法计算经济性",
        )

    if avg_pace is None or avg_pace == 0:
        return EconomyResult(
            status="insufficient_data",
            avg_hr=round(avg_hr, 1),
            note="无配速数据，无法计算经济性",
        )

    # ── 经济性指数 = 配速(m/s) / 心率(bpm) × 1000 ──
    pace_ms = 1000 / avg_pace
    economy_index = round(pace_ms / avg_hr * ECONOMY_SCALE, 1)

    # ── 步频和步幅 ──
    avg_cadence = segment_weighted_mean(running, "avg_cadence")
    avg_stride = segment_weighted_mean(running, "stride_length_cm")

    # ── 步态模式标签 ──
    gait_label = _classify_gait(avg_cadence, avg_stride)

    # ── 历史基线对比 ──
    historical = list(historical_economies)
    baseline_available = False
    baseline_economy = None
    baseline_count = 0
    trend_pct = None
    trend_days = 0

    if historical:
        # 只取同类型、配速相近（±15%）的历史记录
        similar = [
            h for h in historical
            if h.status == "ok"
            and h.economy_index is not None
            and h.avg_pace_sec_per_km is not None
            and abs(h.avg_pace_sec_per_km - avg_pace) / avg_pace <= 0.15
        ]
        if similar:
            baseline_values = [h.economy_index for h in similar if h.economy_index is not None]
            baseline_economy = _mid(baseline_values)
            baseline_count = len(similar)
            baseline_available = True
            if baseline_economy and baseline_economy > 0:
                trend_pct = round(
                    (economy_index - baseline_economy) / baseline_economy * 100, 1
                )
            trend_days = 30  # 默认 30 天窗口

    # ── 构建备注 ──
    note_parts = []
    if baseline_available and trend_pct is not None:
        direction = "改善" if trend_pct > 0 else "下降"
        note_parts.append(
            f"经济性{trend_days}天趋势：{direction} {abs(trend_pct)}%"
        )
    if gait_label == "低效型":
        note_parts.append("步态效率偏低，建议关注步频提升")
    elif gait_label == "大步幅低步频型":
        note_parts.append("大步幅低步频可能增加冲击风险")

    return EconomyResult(
        status="ok",
        economy_index=economy_index,
        avg_pace_sec_per_km=round(avg_pace, 1),
        avg_hr=round(avg_hr, 1),
        avg_cadence=round(avg_cadence, 1) if avg_cadence else None,
        avg_stride_cm=round(avg_stride, 1) if avg_stride else None,
        gait_label=gait_label,
        baseline_available=baseline_available,
        baseline_economy=round(baseline_economy, 1) if baseline_economy else None,
        baseline_sample_count=baseline_count,
        trend_pct=trend_pct,
        trend_days=trend_days if baseline_available else 0,
        note="；".join(note_parts) if note_parts else None,
    )


def _classify_gait(
    avg_cadence: float | None,
    avg_stride: float | None,
) -> str | None:
    """按步频和步幅分类步态模式。

    需要两者都可用；缺失任一返回 unknown。
    """
    if avg_cadence is None or avg_stride is None:
        return "unknown"

    if avg_cadence >= HIGH_CADENCE and avg_stride < EFFICIENT_STRIDE_CM:
        return "高步频省力型"
    if avg_cadence < LOW_CADENCE and avg_stride >= LONG_STRIDE_CM:
        return "大步幅低步频型"
    if avg_cadence >= HIGH_CADENCE - 5 and avg_stride >= EFFICIENT_STRIDE_CM:
        return "均衡高效型"
    return "低效型"
