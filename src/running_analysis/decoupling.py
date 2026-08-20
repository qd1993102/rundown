"""S8 — 心肺-肌肉解耦检测。

判断一次训练的"失败"或"掉速"主要归因于心肺还是肌肉。
通过滚动窗口配速-心率相关性 + 步幅衰减曲线来分析。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Iterable

from ._shared import (
    CleanedSegment,
    _is_running_segment,
    segment_weighted_mean,
)

# 滚动窗口大小（段数）
ROLLING_WINDOW = 3
# 最小段数要求
MIN_SEGMENTS = 6
# 相关性阈值：弱相关 = 心肺-肌肉开始解耦
CORRELATION_WEAK = 0.3


@dataclass(frozen=True)
class DecouplingResult:
    """心肺-肌肉解耦分析结果。"""

    status: str  # ok | insufficient_data | no_hr
    decoupling_type: str | None = None  # cardiac | muscular | none | mixed
    decoupling_label: str | None = None
    # 解耦发生时间/距离
    decoupling_onset_segment: int | None = None
    decoupling_onset_distance_km: float | None = None
    decoupling_onset_time_minutes: float | None = None
    # 整体相关性
    overall_pace_hr_correlation: float | None = None
    # 后半程分析
    second_half_pace_change_pct: float | None = None
    second_half_hr_change_pct: float | None = None
    second_half_stride_change_pct: float | None = None
    # 归因
    attribution: str | None = None
    note: str | None = None


def analyze_cardiac_muscle_decoupling(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: Any = None,
    baseline: Any = None,
) -> DecouplingResult:
    """分析心肺-肌肉解耦。

    通过滚动窗口配速-心率相关性和步幅衰减判断：
    - 后半程配速降但心率不降 → 心肺已到极限（cardiac）
    - 配速降且心率同步降 → 肌肉/能量系统先衰竭（muscular）
    - 配速稳定但相关性越来越弱 → 解耦开始（mixed）

    Args:
        cleaned_segments: 清洗后的 CleanedSegment 序列。
        summary: SessionSummaryFacts（可选）。
        baseline: AthleteBaseline（可选）。

    Returns:
        DecouplingResult 包含解耦类型、起始点和归因。
    """
    segments = list(cleaned_segments)
    if not segments:
        return DecouplingResult(status="insufficient_data", note="无分段数据")

    # ── 筛选跑步主体段 ──
    running = [s for s in segments if _is_running_segment(s.split_type)]
    if not running:
        running = segments

    # 需要配速和心率
    valid = [s for s in running if s.pace_sec_per_km is not None and s.avg_hr is not None]
    if len(valid) < MIN_SEGMENTS:
        return DecouplingResult(
            status="insufficient_data",
            note=f"有效段不足（{len(valid)} < {MIN_SEGMENTS}），需要配速和心率数据",
        )

    if len(valid) < ROLLING_WINDOW:
        return DecouplingResult(
            status="insufficient_data",
            note=f"段数不足滚动窗口（{len(valid)} < {ROLLING_WINDOW}）",
        )

    # ── 整体配速-心率相关性（皮尔逊） ──
    paces = [s.pace_sec_per_km for s in valid if s.pace_sec_per_km is not None]
    hrs = [s.avg_hr for s in valid if s.avg_hr is not None]
    # 对齐长度
    n = min(len(paces), len(hrs))
    paces = paces[:n]
    hrs = hrs[:n]

    overall_corr = _pearson(paces, hrs)

    # ── 滚动窗口相关性 ──
    rolling_corrs = []
    for i in range(len(valid) - ROLLING_WINDOW + 1):
        window = valid[i:i + ROLLING_WINDOW]
        wp = [s.pace_sec_per_km for s in window if s.pace_sec_per_km is not None]
        wh = [s.avg_hr for s in window if s.avg_hr is not None]
        if len(wp) >= 2 and len(wh) >= 2:
            m = min(len(wp), len(wh))
            rolling_corrs.append(_pearson(wp[:m], wh[:m]))
        else:
            rolling_corrs.append(None)

    # ── 后半程变化 ──
    half = len(valid) // 2
    first_half = valid[:half]
    second_half = valid[half:]

    first_pace = segment_weighted_mean(first_half, "pace_sec_per_km")
    second_pace = segment_weighted_mean(second_half, "pace_sec_per_km")
    first_hr = segment_weighted_mean(first_half, "avg_hr")
    second_hr = segment_weighted_mean(second_half, "avg_hr")
    first_stride = segment_weighted_mean(first_half, "stride_length_cm")
    second_stride = segment_weighted_mean(second_half, "stride_length_cm")

    pace_change = _pct(second_pace, first_pace)
    hr_change = _pct(second_hr, first_hr)
    stride_change = _pct(second_stride, first_stride)

    # ── 解耦判定 ──
    decoupling_type, decoupling_label, attribution = _classify_decoupling(
        overall_corr, rolling_corrs,
        pace_change, hr_change, stride_change,
    )

    # ── 解耦起始点定位 ──
    onset_seg, onset_dist, onset_time = _find_decoupling_onset(
        valid, rolling_corrs, ROLLING_WINDOW,
    )

    # ── 构建备注 ──
    note_parts = []
    if overall_corr is not None:
        if overall_corr < 0.3:
            note_parts.append(f"配速-心率整体相关性弱（r={overall_corr:.2f}），提示耦合异常")
        elif overall_corr > 0.7:
            note_parts.append(f"配速-心率整体相关性强（r={overall_corr:.2f}），耦合正常")
    if decoupling_type == "cardiac":
        note_parts.append("心率未随配速下降，提示心肺输出受限")
    elif decoupling_type == "muscular":
        note_parts.append("心率随配速同步下降，提示肌肉/能量系统先衰竭")

    return DecouplingResult(
        status="ok",
        decoupling_type=decoupling_type,
        decoupling_label=decoupling_label,
        decoupling_onset_segment=onset_seg,
        decoupling_onset_distance_km=round(onset_dist, 1) if onset_dist else None,
        decoupling_onset_time_minutes=round(onset_time, 1) if onset_time else None,
        overall_pace_hr_correlation=round(overall_corr, 2) if overall_corr is not None else None,
        second_half_pace_change_pct=round(pace_change, 1) if pace_change is not None else None,
        second_half_hr_change_pct=round(hr_change, 1) if hr_change is not None else None,
        second_half_stride_change_pct=round(stride_change, 1) if stride_change is not None else None,
        attribution=attribution,
        note="；".join(note_parts) if note_parts else None,
    )


def _pearson(x: list[float], y: list[float]) -> float | None:
    """计算皮尔逊相关系数。"""
    n = len(x)
    if n < 2 or len(y) != n:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)


def _pct(later: float | None, earlier: float | None) -> float | None:
    """变化百分比。"""
    if later is None or earlier is None or earlier == 0:
        return None
    return (later - earlier) / earlier * 100


def _classify_decoupling(
    overall_corr: float | None,
    rolling_corrs: list[float | None],
    pace_change: float | None,
    hr_change: float | None,
    stride_change: float | None,
) -> tuple[str | None, str | None, str | None]:
    """按规则判定解耦类型。

    Returns:
        (decoupling_type, decoupling_label, attribution)
    """
    if overall_corr is None:
        return "none", "无法判定", "数据不足"

    # 相关性高 → 耦合正常
    if overall_corr >= 0.5:
        return "none", "耦合正常", "配速与心率保持良好耦合，心肺-肌肉协同正常"

    # 相关性中等 → 检查后半程
    if pace_change is not None and pace_change >= 3.0:
        # 明显掉速
        if hr_change is not None and hr_change > -2.0:
            # 心率基本稳定或上升 → 心肺到极限
            return (
                "cardiac", "心肺限制",
                "后半程掉速但心率未同步下降，提示心肺输出已达上限，建议加强阈值训练",
            )
        elif hr_change is not None and hr_change <= -2.0:
            # 心率也降了 → 肌肉/能量先衰竭
            return (
                "muscular", "肌肉/能量限制",
                "心率随配速同步下降，提示肌肉耐力或能量系统先衰竭，建议加强长距离后程专项",
            )

    # 滚动相关性下降趋势
    valid_corrs = [c for c in rolling_corrs if c is not None]
    if len(valid_corrs) >= 3:
        first_half_corrs = valid_corrs[:len(valid_corrs)//2]
        second_half_corrs = valid_corrs[len(valid_corrs)//2:]
        if first_half_corrs and second_half_corrs:
            first_mean = sum(first_half_corrs) / len(first_half_corrs)
            second_mean = sum(second_half_corrs) / len(second_half_corrs)
            if second_mean < first_mean - 0.2:
                return (
                    "mixed", "渐进解耦",
                    "配速-心率相关性逐渐减弱，提示肌肉疲劳累积导致心肺耦合效率下降",
                )

    return "none", "耦合正常", "未检测到明显解耦信号"


def _find_decoupling_onset(
    segments: list[CleanedSegment],
    rolling_corrs: list[float | None],
    window: int,
) -> tuple[int | None, float | None, float | None]:
    """找第一个滚动相关性跌破弱相关阈值的窗口起始段。

    Returns:
        (segment_index_1based, distance_km, time_minutes)
    """
    if not rolling_corrs:
        return None, None, None

    for i, corr in enumerate(rolling_corrs):
        if corr is not None and corr < CORRELATION_WEAK:
            seg_index = i  # 窗口起始段（0-based）
            cumulative_dist = 0.0
            cumulative_time = 0.0
            for j in range(seg_index):
                cumulative_dist += segments[j].distance_m or 0
                cumulative_time += segments[j].duration_s or 0
            return (
                seg_index + 1,  # 1-based
                cumulative_dist / 1000 if cumulative_dist > 0 else None,
                cumulative_time / 60 if cumulative_time > 0 else None,
            )

    return None, None, None
