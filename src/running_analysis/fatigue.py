"""S6 — 疲劳代偿模式识别。

识别疲劳时身体"硬撑"的方式，区分肌肉耐力瓶颈（A）、心肺输出瓶颈（B）
和神经控制衰减（C）三种模式，并定位衰减起点。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any, Iterable

from ._shared import (
    CADENCE_DROP_PCT,
    CleanedSegment,
    CV_CHANGE,
    DECAY_ONSET_PACE_PCT,
    DECAY_PACE_PCT,
    HR_RISE_PCT,
    STRIDE_DROP_PCT,
    _is_running_segment,
    cv_pct,
    segment_weighted_mean,
)

# 至少需要 4 个跑步主体段才能做前后对比
MIN_RUNNING_SEGMENTS = 4


@dataclass(frozen=True)
class FatigueCompensationResult:
    """疲劳代偿分析结果。"""

    status: str  # ok | insufficient_data
    # 前后变化
    pace_change_pct: float | None = None
    hr_change_pct: float | None = None
    cadence_change_pct: float | None = None
    stride_change_pct: float | None = None
    cadence_cv_change: float | None = None
    stride_cv_change: float | None = None
    # 模式判定
    pattern: str | None = None  # A | B | C | mixed | no_significant_fatigue
    pattern_label: str | None = None
    # 衰减起点
    decay_onset_segment: int | None = None
    decay_onset_distance_km: float | None = None
    decay_onset_time_minutes: float | None = None
    # 建议
    training_suggestion: str | None = None
    note: str | None = None


def identify_fatigue_compensation(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: Any = None,
    baseline: Any = None,
) -> FatigueCompensationResult:
    """识别疲劳代偿模式。

    需要 ≥4 个跑步主体段；不足时返回 insufficient_data。
    模式判定是确定性规则，不是 AI 推断。

    Args:
        cleaned_segments: 清洗后的 CleanedSegment 序列。
        summary: SessionSummaryFacts（可选，当前未使用，保留扩展）。
        baseline: AthleteBaseline（可选，当前未使用，保留扩展）。

    Returns:
        FatigueCompensationResult 包含模式判定、衰减起点和训练建议。
    """
    segments = list(cleaned_segments)
    if not segments:
        return FatigueCompensationResult(
            status="insufficient_data",
            note="无分段数据",
        )

    # ── 1. 筛选跑步主体段 ──
    running = [
        s for s in segments
        if _is_running_segment(s.split_type)
    ]
    if not running:
        running = segments  # 无类型标记时全部视为跑步段

    if len(running) < MIN_RUNNING_SEGMENTS:
        return FatigueCompensationResult(
            status="insufficient_data",
            note=f"跑步主体段不足（{len(running)} < {MIN_RUNNING_SEGMENTS}）",
        )

    # ── 2. 按时间三等分：前 1/3 vs 后 1/3 ──
    third = max(1, len(running) // 3)
    first_third = running[:third]
    last_third = running[-third:] if len(running) >= third * 2 else running[third:]

    # ── 3. 计算各指标前后变化 ──
    first_pace = segment_weighted_mean(first_third, "pace_sec_per_km")
    last_pace = segment_weighted_mean(last_third, "pace_sec_per_km")
    first_hr = segment_weighted_mean(first_third, "avg_hr")
    last_hr = segment_weighted_mean(last_third, "avg_hr")
    first_cadence = segment_weighted_mean(first_third, "avg_cadence")
    last_cadence = segment_weighted_mean(last_third, "avg_cadence")
    first_stride = segment_weighted_mean(first_third, "stride_length_cm")
    last_stride = segment_weighted_mean(last_third, "stride_length_cm")

    # 计算各指标变化百分比
    pace_change = _pct_change(last_pace, first_pace)
    hr_change = _pct_change(last_hr, first_hr)
    cadence_change = _pct_change(last_cadence, first_cadence)
    stride_change = _pct_change(last_stride, first_stride)

    # ── 4. CV 变化（步频/步幅稳定性） ──
    first_cadence_cv = _segment_cv(first_third, "avg_cadence")
    last_cadence_cv = _segment_cv(last_third, "avg_cadence")
    first_stride_cv = _segment_cv(first_third, "stride_length_cm")
    last_stride_cv = _segment_cv(last_third, "stride_length_cm")

    cadence_cv_change = _diff(last_cadence_cv, first_cadence_cv)
    stride_cv_change = _diff(last_stride_cv, first_stride_cv)

    # ── 5. 模式判定 ──
    pattern, pattern_label, suggestion = _classify_pattern(
        pace_change, hr_change, cadence_change, stride_change,
        cadence_cv_change, stride_cv_change,
    )

    # ── 6. 衰减起点定位 ──
    decay_onset = None
    decay_distance = None
    decay_time = None

    if pattern in ("A", "B", "C", "mixed"):
        decay_onset, decay_distance, decay_time = _find_decay_onset(
            running, first_pace
        )

    # ── 7. 构建结果 ──
    note_parts = []
    if pattern == "A":
        note_parts.append("步幅明显缩短但步频维持，提示肌肉耐力不足")
    elif pattern == "B":
        note_parts.append("心率高位运行伴随步频步幅同步下降，提示心肺输出受限")
    elif pattern == "C":
        note_parts.append("配速勉强维持但步态稳定性下降，提示神经控制衰减")
    elif pattern == "mixed":
        note_parts.append("多种代偿信号共存，需综合判断")

    return FatigueCompensationResult(
        status="ok",
        pace_change_pct=round(pace_change, 1) if pace_change is not None else None,
        hr_change_pct=round(hr_change, 1) if hr_change is not None else None,
        cadence_change_pct=round(cadence_change, 1) if cadence_change is not None else None,
        stride_change_pct=round(stride_change, 1) if stride_change is not None else None,
        cadence_cv_change=round(cadence_cv_change, 2) if cadence_cv_change is not None else None,
        stride_cv_change=round(stride_cv_change, 2) if stride_cv_change is not None else None,
        pattern=pattern,
        pattern_label=pattern_label,
        decay_onset_segment=decay_onset,
        decay_onset_distance_km=round(decay_distance, 1) if decay_distance else None,
        decay_onset_time_minutes=round(decay_time, 1) if decay_time else None,
        training_suggestion=suggestion,
        note="；".join(note_parts) if note_parts else None,
    )


def _pct_change(
    later: float | None,
    earlier: float | None,
) -> float | None:
    """计算变化百分比：(later - earlier) / earlier × 100。"""
    if later is None or earlier is None or earlier == 0:
        return None
    return (later - earlier) / earlier * 100


def _diff(
    later: float | None,
    earlier: float | None,
) -> float | None:
    """计算绝对差值。"""
    if later is None or earlier is None:
        return None
    return later - earlier


def _segment_cv(
    segments: list[CleanedSegment],
    attr: str,
) -> float | None:
    """计算 segment 序列指定属性的 CV。"""
    values = [
        getattr(s, attr)
        for s in segments
        if getattr(s, attr) is not None
    ]
    if len(values) < 2:
        return None
    mu = mean(values)
    if mu == 0:
        return None
    return pstdev(values) / mu * 100


def _classify_pattern(
    pace_change: float | None,
    hr_change: float | None,
    cadence_change: float | None,
    stride_change: float | None,
    cadence_cv_change: float | None,
    stride_cv_change: float | None,
) -> tuple[str | None, str | None, str | None]:
    """按规则树判定代偿模式。

    Returns:
        (pattern, pattern_label, training_suggestion)
    """
    # 缺关键指标时无法判定
    if pace_change is None:
        return "no_significant_fatigue", "无明显疲劳", None

    # 明显掉速（配速变慢 ≥3%）
    if pace_change >= DECAY_PACE_PCT:
        # 模式 A：步幅显著缩短 + 步频基本维持 + 心率稳定 → 肌肉耐力瓶颈
        if (
            stride_change is not None
            and stride_change <= STRIDE_DROP_PCT
            and cadence_change is not None
            and cadence_change >= CADENCE_DROP_PCT
            and (hr_change is None or hr_change <= HR_RISE_PCT)
        ):
            return (
                "A", "肌肉耐力瓶颈",
                "建议加强臀中肌和髋部稳定性力量训练，增加长距离后程专项练习",
            )

        # 模式 B：心率高位 + 步频步幅同步下降 → 心肺输出瓶颈
        if (
            hr_change is not None
            and hr_change >= HR_RISE_PCT
            and cadence_change is not None
            and cadence_change <= CADENCE_DROP_PCT
            and stride_change is not None
            and stride_change <= STRIDE_DROP_PCT
        ):
            return (
                "B", "心肺输出瓶颈",
                "建议增加阈值跑和节奏跑训练，提升乳酸清除能力和心肺耐力",
            )

        # 掉速但不满足 A/B 条件 → 混合模式
        return (
            "mixed", "混合代偿",
            "多种代偿信号共存，建议结合具体数据综合判断训练方向",
        )

    # 配速基本维持（掉速 <3%）
    # 模式 C：配速维持但步态稳定性下降 → 神经控制衰减
    if (
        cadence_cv_change is not None
        and stride_cv_change is not None
        and (cadence_cv_change >= CV_CHANGE or stride_cv_change >= CV_CHANGE)
    ):
        return (
            "C", "神经控制衰减",
            "建议增加短距离间歇训练和跑步技术练习，提升神经肌肉协调性",
        )

    return "no_significant_fatigue", "无明显疲劳", None


def _find_decay_onset(
    running: list[CleanedSegment],
    first_third_mean_pace: float | None,
) -> tuple[int | None, float | None, float | None]:
    """定位衰减起点：第一个比前两段均值慢 ≥5% 且后续持续低于前 1/3 均值的段。

    Returns:
        (segment_index, cumulative_distance_km, cumulative_time_minutes)
    """
    if first_third_mean_pace is None or len(running) < 3:
        return None, None, None

    cumulative_distance = 0.0
    cumulative_time = 0.0

    for i in range(2, len(running)):
        seg = running[i]
        prev1 = running[i - 1]
        prev2 = running[i - 2]

        seg_pace = seg.pace_sec_per_km
        prev1_pace = prev1.pace_sec_per_km
        prev2_pace = prev2.pace_sec_per_km

        if seg_pace is None or prev1_pace is None or prev2_pace is None:
            continue

        prev_mean = (prev1_pace + prev2_pace) / 2
        if prev_mean == 0:
            continue

        # 该段比前两段均值慢 ≥5%
        if (seg_pace - prev_mean) / prev_mean * 100 >= DECAY_ONSET_PACE_PCT:
            # 检查后续段是否持续低于前 1/3 均值
            subsequent = running[i:]
            if not subsequent:
                return None, None, None
            all_below = all(
                s.pace_sec_per_km is not None
                and s.pace_sec_per_km >= first_third_mean_pace
                for s in subsequent
                if s.pace_sec_per_km is not None
            )
            if all_below:
                # 计算累计距离和时间
                for j in range(i):
                    cumulative_distance += running[j].distance_m or 0
                    cumulative_time += running[j].duration_s or 0
                return (
                    i + 1,  # 1-based
                    cumulative_distance / 1000 if cumulative_distance > 0 else None,
                    cumulative_time / 60 if cumulative_time > 0 else None,
                )

    return None, None, None
