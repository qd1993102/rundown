"""S11 — 急慢性负荷比（ACWR）本地计算。

从活动训练负荷数据计算 7 天急性负荷、28 天慢性负荷和 ACWR。
不依赖 Garmin 平台计算的 ACWR 值。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable

# ACWR 阈值
ACWR_SAFE_LOW = 0.8
ACWR_SAFE_HIGH = 1.3
ACWR_CAUTION = 1.5
ACWR_HIGH_RISK = 1.5  # same as caution for label, different for action

# 窗口天数
ACUTE_DAYS = 7
CHRONIC_DAYS = 28


@dataclass(frozen=True)
class AcwrResult:
    """ACWR 分析结果。"""

    status: str  # ok | insufficient_data
    # 负荷值
    acute_load: float | None = None  # 7 天日均
    chronic_load: float | None = None  # 28 天日均
    acwr: float | None = None
    # 分级
    risk_level: str | None = None  # 减量区 | 安全区 | 警戒区 | 高风险区
    risk_label: str | None = None
    # 数据质量
    acute_days: int = 0
    chronic_days: int = 0
    # 建议
    load_adjustment_pct: float | None = None  # 建议未来 3 天训练量调整幅度
    suggestion: str | None = None
    note: str | None = None


def calculate_acwr(
    daily_loads: Iterable[dict[str, Any]],
    *,
    acute_days: int = ACUTE_DAYS,
    chronic_days: int = CHRONIC_DAYS,
) -> AcwrResult:
    """计算急慢性负荷比。

    Args:
        daily_loads: 每日训练负荷 dict 列表，每个 dict 包含：
            date: str (YYYY-MM-DD), training_load: float.
            按日期升序排列。
        acute_days: 急性窗口天数，默认 7。
        chronic_days: 慢性窗口天数，默认 28。

    Returns:
        AcwrResult 包含 ACWR 数值、风险等级和训练量调整建议。
    """
    loads = list(daily_loads)
    if not loads:
        return AcwrResult(
            status="insufficient_data",
            note="无训练负荷数据",
        )

    # ── 提取每日负荷 ──
    daily_values: list[float] = []
    for entry in loads:
        val = _float(entry.get("training_load") or entry.get("load") or entry.get("total_training_load"))
        if val is not None:
            daily_values.append(val)

    if not daily_values:
        return AcwrResult(
            status="insufficient_data",
            note="无有效训练负荷数值",
        )

    # ── 计算急性负荷（最近 7 天日均） ──
    acute_window = daily_values[-min(len(daily_values), acute_days):]
    acute_load = mean(acute_window) if acute_window else None

    # ── 计算慢性负荷（最近 28 天日均） ──
    chronic_window = daily_values[-min(len(daily_values), chronic_days):]
    chronic_load = mean(chronic_window) if chronic_window else None

    acute_n = len(acute_window)
    chronic_n = len(chronic_window)

    if acute_load is None or chronic_load is None or chronic_load == 0:
        return AcwrResult(
            status="insufficient_data",
            acute_load=round(acute_load, 1) if acute_load else None,
            chronic_load=round(chronic_load, 1) if chronic_load else None,
            acute_days=acute_n,
            chronic_days=chronic_n,
            note="负荷数据不足，无法计算 ACWR",
        )

    acwr = round(acute_load / chronic_load, 2)

    # ── 分级 ──
    risk_level, risk_label, suggestion, adjustment = _classify_acwr(acwr, acute_n, chronic_n)

    # ── 备注 ──
    note_parts = []
    if chronic_n < chronic_days:
        note_parts.append(f"慢性负荷数据不足 {chronic_days} 天（仅 {chronic_n} 天），ACWR 仅供参考")
    if acute_n < acute_days:
        note_parts.append(f"急性负荷数据不足 {acute_days} 天（仅 {acute_n} 天）")

    return AcwrResult(
        status="ok",
        acute_load=round(acute_load, 1),
        chronic_load=round(chronic_load, 1),
        acwr=acwr,
        risk_level=risk_level,
        risk_label=risk_label,
        acute_days=acute_n,
        chronic_days=chronic_n,
        load_adjustment_pct=round(adjustment, 1) if adjustment else None,
        suggestion=suggestion,
        note="；".join(note_parts) if note_parts else None,
    )


def _classify_acwr(
    acwr: float,
    acute_days: int,
    chronic_days: int,
) -> tuple[str, str, str | None, float | None]:
    """按 ACWR 分级并给出调整建议。

    Returns:
        (risk_level, risk_label, suggestion, adjustment_pct)
    """
    if acwr < ACWR_SAFE_LOW:
        risk_level = "减量区"
        risk_label = f"🟢 减量区（ACWR={acwr:.2f}）"
        suggestion = "当前训练量偏低，如果身体状态良好可适当增加训练量"
        adjustment = 10.0  # 建议增加 10%
    elif acwr <= ACWR_SAFE_HIGH:
        risk_level = "安全区"
        risk_label = f"🟢 安全区（ACWR={acwr:.2f}）"
        suggestion = "训练负荷在安全范围内，可维持当前量"
        adjustment = 0.0
    elif acwr <= ACWR_CAUTION:
        risk_level = "警戒区"
        risk_label = f"🟠 警戒区（ACWR={acwr:.2f}）"
        suggestion = "训练负荷偏高，建议未来 3 天降低训练量 10-20%，增加恢复"
        adjustment = -15.0
    else:
        risk_level = "高风险区"
        risk_label = f"🔴 高风险区（ACWR={acwr:.2f}）"
        suggestion = "训练负荷显著过高，强烈建议减少训练量 20-30%，优先休息恢复"
        adjustment = -25.0

    # 数据不足时降低置信度
    if chronic_days < CHRONIC_DAYS:
        risk_label += "（数据不足，仅供参考）"

    return risk_level, risk_label, suggestion, adjustment


def _float(value: Any) -> float | None:
    """安全转换为 float。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
