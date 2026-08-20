"""S13 — 伤病风险综合评估。

多因子加权打分系统，综合负荷、恢复、代偿和步态稳定性评估伤病风险。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .acwr import AcwrResult
from .hrv_baseline import HrvBaselineResult
from .fatigue import FatigueCompensationResult
from .economy import EconomyResult

# 风险因子权重
WEIGHT_ACWR = 0.30         # 训练负荷
WEIGHT_RECOVERY = 0.25     # 连续恢复不足
WEIGHT_FATIGUE = 0.20      # 步态代偿持续
WEIGHT_STRIDE = 0.15       # 步幅不对称/下降
WEIGHT_CADENCE = 0.10      # 步频稳定性

# 风险阈值
RISK_SCORE_LOW = 25
RISK_SCORE_MODERATE = 50
RISK_SCORE_HIGH = 75

# ACWR 风险映射
ACWR_RISK_THRESHOLD = 1.4  # ACWR > 1.4 → 风险

# 恢复风险：连续恢复指数 < 30 的天数
RECOVERY_POOR_THRESHOLD = 30
RECOVERY_CONSECUTIVE_DAYS = 3


@dataclass(frozen=True)
class InjuryRiskResult:
    """伤病风险综合评估结果。"""

    status: str  # ok | insufficient_data
    # 总分
    risk_score: float | None = None  # 0-100
    risk_level: str | None = None  # 低风险 | 注意 | 警告 | 必须休息
    risk_label: str | None = None
    # 各因子分数
    acwr_score: float | None = None
    recovery_score: float | None = None
    fatigue_score: float | None = None
    stride_score: float | None = None
    cadence_score: float | None = None
    # 主要风险来源
    primary_risk_source: str | None = None
    # 行动建议
    action: str | None = None
    action_urgency: str | None = None  # immediate | today | this_week
    note: str | None = None


def assess_injury_risk(
    acwr: AcwrResult | None = None,
    hrv: HrvBaselineResult | None = None,
    hrv_history: list[dict[str, Any]] | None = None,
    fatigue: FatigueCompensationResult | None = None,
    fatigue_history: list[FatigueCompensationResult] | None = None,
    economy: EconomyResult | None = None,
    *,
    recent_fatigue_patterns: list[str] | None = None,
) -> InjuryRiskResult:
    """多因子加权伤病风险评估。

    Args:
        acwr: S11 ACWR 分析结果。
        hrv: S9 当日 HRV 恢复状态。
        hrv_history: 近期 HRV 恢复指数序列（用于连续检查）：
            [{"recovery_index": 75}, {"recovery_index": 28}, ...]
        fatigue: S6 当日疲劳代偿结果。
        fatigue_history: 近期疲劳代偿模式序列（用于检查持续性）。
        economy: S5 经济性评估（步态稳定性）。
        recent_fatigue_patterns: 预计算的近期代偿模式标签列表。

    Returns:
        InjuryRiskResult 包含风险总分、等级和行动建议。
    """
    scores: dict[str, float] = {}
    available_factors = 0

    # ── 因子 1：ACWR 负荷风险（0-30） ──
    if acwr is not None and acwr.status == "ok" and acwr.acwr is not None:
        if acwr.acwr > ACWR_RISK_THRESHOLD:
            # ACWR > 1.4：线性映射到 0-30
            excess = min(acwr.acwr - ACWR_RISK_THRESHOLD, 0.6)  # 最多超出 0.6
            acwr_score = min(30.0, excess / 0.6 * 30.0)
        else:
            acwr_score = 0.0
        scores["acwr"] = acwr_score
        available_factors += 1
    else:
        acwr_score = None

    # ── 因子 2：连续恢复不足（0-25） ──
    if hrv is not None and hrv.status == "ok" and hrv.recovery_index is not None:
        # 当日恢复指数
        if hrv.recovery_index < RECOVERY_POOR_THRESHOLD:
            recovery_score = 15.0  # 单日恢复差
        else:
            recovery_score = 0.0

        # 检查连续天数
        if hrv_history:
            consecutive_poor = _count_consecutive_poor(hrv_history, RECOVERY_POOR_THRESHOLD)
            if consecutive_poor >= 3:
                recovery_score = min(25.0, 15.0 + (consecutive_poor - 2) * 5.0)
        scores["recovery"] = recovery_score
        available_factors += 1
    else:
        recovery_score = None

    # ── 因子 3：步态代偿持续性（0-20） ──
    if fatigue is not None and fatigue.status == "ok":
        if fatigue.pattern and fatigue.pattern != "no_significant_fatigue":
            fatigue_score = 10.0  # 单次代偿

            # 检查持续性
            patterns = recent_fatigue_patterns or []
            if fatigue_history:
                patterns = [f.pattern for f in fatigue_history if f.pattern]

            if patterns:
                # 最近 2 次也出现代偿
                recent_non_normal = sum(
                    1 for p in patterns[-3:]
                    if p and p != "no_significant_fatigue"
                )
                if recent_non_normal >= 2:
                    fatigue_score = min(20.0, 10.0 + (recent_non_normal - 1) * 5.0)
        else:
            fatigue_score = 0.0
        scores["fatigue"] = fatigue_score
        available_factors += 1
    else:
        fatigue_score = None

    # ── 因子 4：步幅不对称/下降（0-15） ──
    if fatigue is not None and fatigue.status == "ok":
        if fatigue.stride_change_pct is not None and fatigue.stride_change_pct < -5.0:
            # 步幅下降 >5%
            stride_score = min(15.0, abs(fatigue.stride_change_pct) / 5.0 * 5.0)
        elif fatigue.stride_change_pct is not None and fatigue.stride_change_pct < -3.0:
            stride_score = 5.0
        else:
            stride_score = 0.0
        scores["stride"] = stride_score
        available_factors += 1
    else:
        stride_score = None

    # ── 因子 5：步频稳定性（0-10） ──
    if fatigue is not None and fatigue.status == "ok":
        if fatigue.cadence_cv_change is not None and fatigue.cadence_cv_change > 1.0:
            cadence_score = min(10.0, fatigue.cadence_cv_change / 1.0 * 5.0)
        else:
            cadence_score = 0.0
        scores["cadence"] = cadence_score
        available_factors += 1
    else:
        cadence_score = None

    # ── 总分归一化 ──
    if available_factors == 0:
        return InjuryRiskResult(
            status="insufficient_data",
            note="无足够数据因子（需要 ACWR、HRV、疲劳代偿或经济性中的至少一项）",
        )

    total_score = sum(s for s in scores.values() if s is not None)

    # ── 分级 ──
    if total_score < RISK_SCORE_LOW:
        risk_level = "低风险"
        risk_label = f"🟢 低风险（{total_score:.0f}/100）"
        action = "正常训练，保持当前节奏"
        urgency = "this_week"
    elif total_score < RISK_SCORE_MODERATE:
        risk_level = "注意"
        risk_label = f"🟡 注意（{total_score:.0f}/100）"
        action = "建议降低训练强度或时长，增加恢复日"
        urgency = "this_week"
    elif total_score < RISK_SCORE_HIGH:
        risk_level = "警告"
        risk_label = f"🟠 警告（{total_score:.0f}/100）"
        action = "强烈建议减少训练量，优先休息恢复，检查是否有疼痛或不适"
        urgency = "today"
    else:
        risk_level = "必须休息"
        risk_label = f"🔴 必须休息（{total_score:.0f}/100）"
        action = "立即停止训练，充分休息，如持续不适请咨询医生"
        urgency = "immediate"

    # ── 主要风险来源 ──
    primary = _primary_source(scores)

    # ── 备注 ──
    note_parts = []
    if acwr_score is not None and acwr_score > 0:
        note_parts.append(f"负荷风险 +{acwr_score:.0f}（ACWR={acwr.acwr:.2f}）")
    if recovery_score is not None and recovery_score > 0:
        note_parts.append(f"恢复不足 +{recovery_score:.0f}")
    if fatigue_score is not None and fatigue_score > 0:
        note_parts.append(f"代偿持续 +{fatigue_score:.0f}")

    return InjuryRiskResult(
        status="ok",
        risk_score=round(total_score, 1),
        risk_level=risk_level,
        risk_label=risk_label,
        acwr_score=round(acwr_score, 1) if acwr_score is not None else None,
        recovery_score=round(recovery_score, 1) if recovery_score is not None else None,
        fatigue_score=round(fatigue_score, 1) if fatigue_score is not None else None,
        stride_score=round(stride_score, 1) if stride_score is not None else None,
        cadence_score=round(cadence_score, 1) if cadence_score is not None else None,
        primary_risk_source=primary,
        action=action,
        action_urgency=urgency,
        note="；".join(note_parts) if note_parts else None,
    )


def _count_consecutive_poor(
    history: list[dict[str, Any]],
    threshold: float,
) -> int:
    """从最新往后数连续低于阈值的记录数。"""
    count = 0
    for entry in reversed(history):
        ri = entry.get("recovery_index")
        if ri is not None and float(ri) < threshold:
            count += 1
        else:
            break
    return count


def _primary_source(scores: dict[str, float]) -> str | None:
    """找出分数最高的风险来源。"""
    if not scores:
        return None
    max_source = max(scores, key=lambda k: scores[k])
    if scores[max_source] == 0:
        return "无明显风险来源"
    labels = {
        "acwr": "训练负荷过高",
        "recovery": "连续恢复不足",
        "fatigue": "步态代偿持续",
        "stride": "步幅下降",
        "cadence": "步频稳定性下降",
    }
    return labels.get(max_source, max_source)
