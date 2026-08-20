"""S10 — 状态-表现一致性校验。

对比昨晚恢复状态与今日实际运动表现，识别异常：
- 恢复差 → 表现却好：肾上腺素代偿，透支未来
- 恢复好 → 表现却差：潜在伤病/营养/心理因素
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hrv_baseline import HrvBaselineResult
from .drift import AerobicDriftResult
from .economy import EconomyResult


@dataclass(frozen=True)
class ConsistencyResult:
    """状态-表现一致性校验结果。"""

    status: str  # ok | insufficient_data
    # 输入
    recovery_index: float | None = None
    recovery_label: str | None = None
    drift_grade: str | None = None
    economy_trend_pct: float | None = None
    # 判定
    consistent: bool | None = None  # True=一致, False=不一致
    pattern: str | None = None  # normal | adrenal_compensation | hidden_fatigue | unclear
    pattern_label: str | None = None
    # 解释
    explanation: str | None = None
    training_suggestion: str | None = None
    note: str | None = None


def check_recovery_performance_consistency(
    hrv: HrvBaselineResult | None = None,
    drift: AerobicDriftResult | None = None,
    economy: EconomyResult | None = None,
    *,
    performance_grade: str | None = None,
) -> ConsistencyResult:
    """校验恢复状态与运动表现的一致性。

    Args:
        hrv: S9 HRV 基线分析结果。
        drift: S4 有氧漂移分析结果。
        economy: S5 经济性评估结果。
        performance_grade: 外部传入的表现评级（可选），
            如 "good" / "normal" / "poor"。

    Returns:
        ConsistencyResult 包含一致性判定和训练建议。
    """
    if hrv is None or hrv.status != "ok":
        return ConsistencyResult(
            status="insufficient_data",
            note="HRV 恢复数据不可用，无法校验一致性",
        )

    recovery_index = hrv.recovery_index
    if recovery_index is None:
        return ConsistencyResult(
            status="insufficient_data",
            note="恢复指数不可得",
        )

    # ── 判断表现好坏 ──
    perf_good = _is_performance_good(drift, economy, performance_grade)
    perf_poor = _is_performance_poor(drift, economy, performance_grade)

    # ── 一致性判定 ──
    recovery_good = recovery_index >= 70
    recovery_poor = recovery_index < 50

    if recovery_good and perf_good:
        pattern = "normal"
        pattern_label = "正常一致"
        consistent = True
        explanation = "恢复状态良好，运动表现匹配，训练-恢复平衡健康"
        suggestion = "保持当前训练节奏"
    elif recovery_poor and perf_poor:
        pattern = "normal"
        pattern_label = "正常一致"
        consistent = True
        explanation = "恢复状态不佳，运动表现相应下降，身体在正常反应"
        suggestion = "优先保证睡眠和恢复，降低训练强度"
    elif recovery_poor and perf_good:
        pattern = "adrenal_compensation"
        pattern_label = "肾上腺素代偿"
        consistent = False
        explanation = (
            "虽然恢复状态不佳，但运动表现却异常好——"
            "这通常是肾上腺素和皮质醇代偿的结果，"
            "身体在透支未来的恢复储备"
        )
        suggestion = (
            "警惕：今日训练强度不宜过高，建议缩短时长。"
            "连续出现此模式需立即降低训练负荷"
        )
    elif recovery_good and perf_poor:
        pattern = "hidden_fatigue"
        pattern_label = "隐性疲劳"
        consistent = False
        explanation = (
            "恢复指标看起来正常，但运动表现却明显下降——"
            "可能提示潜在伤病、营养不足、心理疲劳或数据质量问题"
        )
        suggestion = (
            "建议检查：1) 是否有未察觉的疼痛或不适；"
            "2) 近期营养摄入是否充足；"
            "3) 是否长期处于高压状态"
        )
    else:
        pattern = "unclear"
        pattern_label = "边界状态"
        consistent = None
        explanation = "恢复与表现均处于中间状态，暂无法明确判断一致性"
        suggestion = "继续观察，保持当前训练节奏"

    # ── 备注 ──
    note_parts = []
    if recovery_index is not None:
        note_parts.append(f"恢复指数 {recovery_index:.0f}")
    if drift is not None and drift.grade:
        note_parts.append(f"漂移等级 {drift.grade}")
    if economy is not None and economy.trend_pct is not None:
        direction = "改善" if economy.trend_pct > 0 else "下降"
        note_parts.append(f"经济性趋势 {direction} {abs(economy.trend_pct):.1f}%")

    return ConsistencyResult(
        status="ok",
        recovery_index=recovery_index,
        recovery_label=hrv.recovery_label,
        drift_grade=drift.grade if drift else None,
        economy_trend_pct=economy.trend_pct if economy else None,
        consistent=consistent,
        pattern=pattern,
        pattern_label=pattern_label,
        explanation=explanation,
        training_suggestion=suggestion,
        note="；".join(note_parts) if note_parts else None,
    )


def _is_performance_good(
    drift: AerobicDriftResult | None,
    economy: EconomyResult | None,
    performance_grade: str | None,
) -> bool:
    """判断表现是否好。"""
    # 外部传入优先
    if performance_grade == "good":
        return True
    if performance_grade == "poor":
        return False

    signals = 0
    total = 0

    if drift is not None and drift.grade:
        total += 1
        if drift.grade in ("excellent", "normal"):
            signals += 1

    if economy is not None and economy.trend_pct is not None:
        total += 1
        if economy.trend_pct > 0:
            signals += 1

    if total == 0:
        return False  # 默认不判为好

    return signals >= total / 2  # 半数以上信号为好


def _is_performance_poor(
    drift: AerobicDriftResult | None,
    economy: EconomyResult | None,
    performance_grade: str | None,
) -> bool:
    """判断表现是否差。"""
    if performance_grade == "poor":
        return True
    if performance_grade == "good":
        return False

    signals = 0
    total = 0

    if drift is not None and drift.grade:
        total += 1
        if drift.grade in ("elevated", "high"):
            signals += 1

    if economy is not None and economy.trend_pct is not None:
        total += 1
        if economy.trend_pct < 0:
            signals += 1

    if total == 0:
        return False

    return signals >= total / 2
