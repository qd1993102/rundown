"""S9 — HRV 基线对比与状态判定。

将昨晚的 HRV 和睡眠数据转化为"今日可用状态评估"。
支持 7/30 天滚动基线对比和恢复指数计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any, Iterable

# 恢复指数权重
WEIGHT_HRV = 0.50          # HRV 偏离权重
WEIGHT_SLEEP = 0.30        # 睡眠时长权重
WEIGHT_RHR = 0.20          # 静息心率偏离权重

# 睡眠时长基准（小时）
SLEEP_BASELINE_HOURS = 7.5
# 恢复指数分级
RECOVERY_GOOD = 70
RECOVERY_FAIR = 50
RECOVERY_POOR = 30


@dataclass(frozen=True)
class HrvBaselineResult:
    """HRV 基线对比与状态判定结果。"""

    status: str  # ok | insufficient_data
    # 当前值
    hrv_last_night: float | None = None
    hrv_weekly_avg: float | None = None
    resting_hr: float | None = None
    sleep_hours: float | None = None
    body_battery_high: float | None = None
    training_readiness: float | None = None
    # 基线（7天/30天）
    hrv_baseline_7d: float | None = None
    hrv_baseline_30d: float | None = None
    hrv_baseline_sample_count: int = 0
    # 偏离
    hrv_deviation_stdev: float | None = None  # 偏离基线的标准差倍数
    rhr_deviation_stdev: float | None = None
    # 恢复指数
    recovery_index: float | None = None  # 0-100
    recovery_label: str | None = None  # 充分恢复 | 轻微欠恢复 | 明显欠恢复 | 严重疲劳
    # 今日训练风险
    training_risk: str | None = None
    risk_note: str | None = None
    note: str | None = None


def analyze_hrv_baseline(
    today: dict[str, Any],
    *,
    historical: Iterable[dict[str, Any]] = (),
    baseline_days: int = 7,
) -> HrvBaselineResult:
    """分析 HRV 基线对比与恢复状态。

    Args:
        today: 当日 DailyHealth dict，包含：
            hrv_last_night_avg, hrv_weekly_avg, resting_heart_rate,
            sleep_duration_hours, body_battery_high, training_readiness_score.
        historical: 历史 DailyHealth dict 列表（最近 7/30 天）。
        baseline_days: 基线窗口天数，默认 7 天。

    Returns:
        HrvBaselineResult 包含恢复指数、状态标签和训练风险。
    """
    # ── 提取当日数据 ──
    hrv_night = _float(today.get("hrv_last_night_avg"))
    hrv_weekly = _float(today.get("hrv_weekly_avg"))
    rhr = _float(today.get("resting_heart_rate"))
    sleep_hours = _float(today.get("sleep_duration_hours"))
    battery = _float(today.get("body_battery_high"))
    readiness = _float(today.get("training_readiness_score"))

    # ── 构建历史基线 ──
    history = list(historical)
    hrv_values = []
    rhr_values = []
    sleep_values = []
    for h in history:
        v = _float(h.get("hrv_last_night_avg"))
        if v is not None:
            hrv_values.append(v)
        v = _float(h.get("resting_heart_rate"))
        if v is not None:
            rhr_values.append(v)
        v = _float(h.get("sleep_duration_hours"))
        if v is not None:
            sleep_values.append(v)

    # 取最近 baseline_days 天
    if len(hrv_values) > baseline_days:
        hrv_values = hrv_values[-baseline_days:]

    if len(hrv_values) < 3:
        # 数据不足，尝试扩展窗口
        all_hrv = []
        for h in history:
            v = _float(h.get("hrv_last_night_avg"))
            if v is not None:
                all_hrv.append(v)
        if len(all_hrv) >= 3:
            hrv_values = all_hrv[-min(len(all_hrv), 30):]
        else:
            return HrvBaselineResult(
                status="insufficient_data",
                hrv_last_night=hrv_night,
                hrv_weekly_avg=hrv_weekly,
                resting_hr=rhr,
                sleep_hours=sleep_hours,
                body_battery_high=battery,
                training_readiness=readiness,
                hrv_baseline_sample_count=len(all_hrv),
                note=f"HRV 历史数据不足（{len(all_hrv)} < 3），无法建立基线",
            )

    # ── 基线统计 ──
    hrv_baseline = mean(hrv_values)
    hrv_stdev = pstdev(hrv_values) if len(hrv_values) >= 2 else 0.0

    rhr_baseline = mean(rhr_values) if rhr_values else None
    rhr_stdev = pstdev(rhr_values) if len(rhr_values) >= 2 else 0.0

    sleep_baseline = mean(sleep_values) if sleep_values else SLEEP_BASELINE_HOURS

    # ── 偏离计算 ──
    hrv_deviation = None
    if hrv_night is not None and hrv_stdev > 0:
        hrv_deviation = (hrv_night - hrv_baseline) / hrv_stdev

    rhr_deviation = None
    if rhr is not None and rhr_baseline is not None and rhr_stdev and rhr_stdev > 0:
        rhr_deviation = (rhr - rhr_baseline) / rhr_stdev

    # ── 恢复指数（0-100） ──
    # HRV 分量：偏离基线在 ±1σ 内为满分，超过 ±2σ 为 0 分
    hrv_score = 50.0
    if hrv_deviation is not None:
        if hrv_deviation >= 0:
            # HRV 高于基线 = 好
            hrv_score = min(100.0, 50.0 + hrv_deviation * 25.0)
        else:
            # HRV 低于基线 = 差
            hrv_score = max(0.0, 50.0 + hrv_deviation * 25.0)

    # 睡眠分量
    sleep_score = 50.0
    if sleep_hours is not None:
        sleep_ratio = sleep_hours / SLEEP_BASELINE_HOURS
        if sleep_ratio >= 1.0:
            sleep_score = min(100.0, 50.0 + (sleep_ratio - 1.0) * 50.0)
        else:
            sleep_score = max(0.0, sleep_ratio * 50.0)

    # RHR 分量：静息心率升高 = 差
    rhr_score = 50.0
    if rhr_deviation is not None:
        if rhr_deviation <= 0:
            # RHR 低于基线 = 好
            rhr_score = min(100.0, 50.0 - rhr_deviation * 25.0)
        else:
            # RHR 高于基线 = 差
            rhr_score = max(0.0, 50.0 - rhr_deviation * 25.0)

    recovery_index = round(
        WEIGHT_HRV * hrv_score
        + WEIGHT_SLEEP * sleep_score
        + WEIGHT_RHR * rhr_score,
        1,
    )

    # ── 分级 ──
    if recovery_index >= RECOVERY_GOOD:
        recovery_label = "充分恢复"
        training_risk = "低风险"
        risk_note = "今日可以进行正常训练"
    elif recovery_index >= RECOVERY_FAIR:
        recovery_label = "轻微欠恢复"
        training_risk = "注意"
        risk_note = "建议降低强度或缩短时长，优先保证睡眠"
    elif recovery_index >= RECOVERY_POOR:
        recovery_label = "明显欠恢复"
        training_risk = "警告"
        risk_note = "建议改为轻松恢复跑或完全休息，检查睡眠和压力"
    else:
        recovery_label = "严重疲劳"
        training_risk = "高风险"
        risk_note = "强烈建议今日完全休息，连续疲劳需评估训练负荷"

    # ── 备注 ──
    note_parts = []
    if hrv_deviation is not None and hrv_deviation < -1.0:
        note_parts.append(f"HRV 低于基线 {abs(hrv_deviation):.1f}σ")
    if rhr_deviation is not None and rhr_deviation > 1.0:
        note_parts.append(f"静息心率高于基线 {rhr_deviation:.1f}σ")
    if sleep_hours is not None and sleep_hours < 6.0:
        note_parts.append(f"睡眠不足（{sleep_hours:.1f}h）")

    return HrvBaselineResult(
        status="ok",
        hrv_last_night=round(hrv_night, 1) if hrv_night else None,
        hrv_weekly_avg=round(hrv_weekly, 1) if hrv_weekly else None,
        resting_hr=round(rhr, 1) if rhr else None,
        sleep_hours=round(sleep_hours, 1) if sleep_hours else None,
        body_battery_high=round(battery, 1) if battery else None,
        training_readiness=round(readiness, 1) if readiness else None,
        hrv_baseline_7d=round(hrv_baseline, 1),
        hrv_baseline_30d=None,  # 30 天基线需要更长历史
        hrv_baseline_sample_count=len(hrv_values),
        hrv_deviation_stdev=round(hrv_deviation, 2) if hrv_deviation is not None else None,
        rhr_deviation_stdev=round(rhr_deviation, 2) if rhr_deviation is not None else None,
        recovery_index=recovery_index,
        recovery_label=recovery_label,
        training_risk=training_risk,
        risk_note=risk_note,
        note="；".join(note_parts) if note_parts else None,
    )


def _float(value: Any) -> float | None:
    """安全转换为 float。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
