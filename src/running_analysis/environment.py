"""S7 — 环境补偿与归一化。

消除坡度对配速和心率的干扰，还原"等效平地配速/等效平地心率"。
天气数据（温度/湿度/风力）当前不可用，预留接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Naismith 规则参数
# 每 100m 爬升增加的时间（秒/km）
NAISMITH_UPHILL_SECONDS_PER_100M_GAIN = 45  # 约 45s/km 每 100m 爬升
# 每 100m 下降减少的时间（秒/km），但下降收益递减
NAISMITH_DOWNHILL_SECONDS_PER_100M_DESCENT = 15  # 约 15s/km 每 100m 下降

# 坡度分级阈值（gain_per_km: m/km）
GRADE_FLAT = 10       # 0-10m/km = 平路
GRADE_ROLLING = 30    # 10-30m/km = 起伏
GRADE_HILLY = 60      # 30-60m/km = 丘陵
GRADE_MOUNTAIN = 100  # 60-100m/km = 山地
# >100m/km = 陡峭山地


@dataclass(frozen=True)
class EnvironmentCompensationResult:
    """环境补偿分析结果。"""

    status: str  # ok | insufficient_data | flat_terrain
    # 原始值
    original_pace_sec_per_km: float | None = None
    original_hr: float | None = None
    # 地形数据
    total_ascent_m: float | None = None
    total_descent_m: float | None = None
    gain_per_km: float | None = None
    terrain_class: str | None = None  # flat | rolling | hilly | mountain | steep_mountain
    # 修正后
    normalized_pace_sec_per_km: float | None = None
    normalized_hr: float | None = None
    # 补偿量
    pace_compensation_sec_per_km: float | None = None  # 修正量
    elevation_contribution_pct: float | None = None  # 坡度对配速的贡献百分比
    # 天气（当前不可用）
    weather_available: bool = False
    weather_note: str | None = None
    note: str | None = None


def compensate_environment(
    avg_pace_sec_per_km: float | None,
    avg_hr: float | None,
    *,
    total_ascent_m: float | None = None,
    total_descent_m: float | None = None,
    distance_m: float | None = None,
    gain_per_km: float | None = None,
    grade_profile: dict[str, float] | None = None,
) -> EnvironmentCompensationResult:
    """消除坡度对配速和心率的影响。

    基于 Naismith 规则将实际配速归一化为等效平地配速。
    天气补偿不可用（缺少温度/湿度/风力数据源）。

    Args:
        avg_pace_sec_per_km: 原始平均配速（sec/km）。
        avg_hr: 原始平均心率。
        total_ascent_m: 总爬升（米）。
        total_descent_m: 总下降（米）。
        distance_m: 总距离（米）。
        gain_per_km: 每公里爬升（m/km），优先使用。
        grade_profile: 坡度分布（来自 TerrainFacts）。

    Returns:
        EnvironmentCompensationResult 包含等效平地配速和坡度贡献度。
    """
    if avg_pace_sec_per_km is None:
        return EnvironmentCompensationResult(
            status="insufficient_data",
            note="缺少配速数据",
        )

    # ── 计算 gain_per_km ──
    if gain_per_km is None and total_ascent_m is not None and distance_m is not None and distance_m > 0:
        gain_per_km = total_ascent_m / (distance_m / 1000)

    # 爬升可忽略 → 无需补偿
    if gain_per_km is None or gain_per_km < 5:
        return EnvironmentCompensationResult(
            status="flat_terrain",
            original_pace_sec_per_km=round(avg_pace_sec_per_km, 1),
            original_hr=round(avg_hr, 1) if avg_hr else None,
            total_ascent_m=round(total_ascent_m, 1) if total_ascent_m else None,
            total_descent_m=round(total_descent_m, 1) if total_descent_m else None,
            gain_per_km=round(gain_per_km, 1) if gain_per_km else None,
            terrain_class="flat",
            normalized_pace_sec_per_km=round(avg_pace_sec_per_km, 1),
            normalized_hr=round(avg_hr, 1) if avg_hr else None,
            pace_compensation_sec_per_km=0.0,
            elevation_contribution_pct=0.0,
            weather_available=False,
            weather_note="天气数据不可用（缺少温度/湿度/风力数据源）",
            note="平路地形，无需坡度补偿",
        )

    # ── 地形分类 ──
    terrain_class = _classify_terrain(gain_per_km)

    # ── 坡度补偿（Naismith 规则） ──
    # 爬升补偿：每 100m 爬升/km 增加约 45s/km
    uphill_compensation = gain_per_km / 100 * NAISMITH_UPHILL_SECONDS_PER_100M_GAIN

    # 下降补偿：每 100m 下降/km 减少约 15s/km（有上限）
    total_descent = total_descent_m or 0
    if distance_m and distance_m > 0:
        descent_per_km = total_descent / (distance_m / 1000)
    else:
        descent_per_km = gain_per_km * 0.8  # 粗略估计：下降 ≈ 爬升的 80%

    downhill_compensation = min(
        descent_per_km / 100 * NAISMITH_DOWNHILL_SECONDS_PER_100M_DESCENT,
        30,  # 下降收益上限 30s/km（陡坡下降不会无限加速）
    )

    # 净补偿：爬升减速 - 下降加速
    net_compensation = uphill_compensation - downhill_compensation

    # 等效平地配速 = 实际配速 - 净补偿（爬升让配速变慢，补偿后变快）
    normalized_pace = avg_pace_sec_per_km - net_compensation

    # 配速修正百分比
    if avg_pace_sec_per_km > 0:
        contribution_pct = round(net_compensation / avg_pace_sec_per_km * 100, 1)
    else:
        contribution_pct = None

    # 心率修正（粗略：坡度对心率的影响约为配速影响的 60%）
    normalized_hr = None
    if avg_hr is not None and avg_pace_sec_per_km > 0:
        hr_compensation = net_compensation / avg_pace_sec_per_km * avg_hr * 0.6
        normalized_hr = round(avg_hr - hr_compensation, 1)

    # ── 备注 ──
    note_parts = []
    note_parts.append(f"地形 {terrain_class}（{gain_per_km:.0f}m/km）")
    if net_compensation > 0:
        note_parts.append(
            f"坡度使配速减慢约 {net_compensation:.0f}s/km，"
            f"等效平地配速 {normalized_pace:.0f}s/km"
        )
    note_parts.append("天气补偿不可用（缺少温度/湿度/风力数据源）")

    return EnvironmentCompensationResult(
        status="ok",
        original_pace_sec_per_km=round(avg_pace_sec_per_km, 1),
        original_hr=round(avg_hr, 1) if avg_hr else None,
        total_ascent_m=round(total_ascent_m, 1) if total_ascent_m else None,
        total_descent_m=round(total_descent_m, 1) if total_descent_m else None,
        gain_per_km=round(gain_per_km, 1),
        terrain_class=terrain_class,
        normalized_pace_sec_per_km=round(normalized_pace, 1),
        normalized_hr=normalized_hr,
        pace_compensation_sec_per_km=round(net_compensation, 1),
        elevation_contribution_pct=contribution_pct,
        weather_available=False,
        weather_note="天气数据不可用（缺少温度/湿度/风力数据源）",
        note="；".join(note_parts),
    )


def _classify_terrain(gain_per_km: float) -> str:
    """按每公里爬升分类地形。"""
    if gain_per_km < GRADE_FLAT:
        return "flat"
    if gain_per_km < GRADE_ROLLING:
        return "rolling"
    if gain_per_km < GRADE_HILLY:
        return "hilly"
    if gain_per_km < GRADE_MOUNTAIN:
        return "mountain"
    return "steep_mountain"
