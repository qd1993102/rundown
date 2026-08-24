#!/usr/bin/env python3
"""
Pace Zones Computer — 个人配速区间计算引擎。

实现需求 1-6：PB 清洗与 VDOT 校验、心率区间（Karvonen）、
配速基线（VDOT 映射）、近期训练反馈校准、环境疲劳补偿、异常降级。

用法:
    python3 compute_zones.py --pb-json '<json>' --hr-rest 51 --hr-max 190 \
        --age 34 --activities-json '<json>' [--temp 28 --humidity 75]

输出: JSON 格式的 Z1-Z5 配速与心率区间。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


# ============================================================================
# 常量
# ============================================================================

VDOT_A = 0.182258
VDOT_B = 0.000104
VDOT_C = -4.60

HR_ZONE_PCTS = {
    "z1": (0.50, 0.60),
    "z2": (0.60, 0.70),
    "z3": (0.70, 0.80),
    "z4": (0.80, 0.90),
    "z5": (0.90, 1.00),
}

DISTANCE_MAP = {
    "5k": 5000,
    "10k": 10000,
    "half_marathon": 21097.5,
    "marathon": 42195,
}


# ============================================================================
# 数据类型
# ============================================================================

@dataclass
class PBRecord:
    distance: str
    time_seconds: float
    updated_days_ago: float
    weight: float = 1.0
    vdot: float = 0.0
    reason: str = ""


@dataclass
class ZoneResult:
    zone: str
    hr_min: int
    hr_max: int
    pace_min_sec_per_km: float | None
    pace_max_sec_per_km: float | None
    pace_display: str
    source: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComputeResult:
    zones: list[ZoneResult]
    effective_pb: dict[str, Any]
    hr_rest: int
    hr_max: int
    hr_max_source: str
    meta: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# 需求 1：PB 清洗与 VDOT 校验
# ============================================================================

def _compute_vdot(distance_m: float, time_seconds: float) -> float:
    """Jack Daniels VDOT 近似计算。"""
    if time_seconds <= 0 or distance_m <= 0:
        return 0.0
    v = (distance_m / time_seconds) * 60
    vo2 = VDOT_C + VDOT_A * v + VDOT_B * v * v
    if distance_m >= 42195:
        pct = 0.979
    elif distance_m >= 21097:
        pct = 0.985
    elif distance_m >= 10000:
        pct = 0.990
    elif distance_m >= 5000:
        pct = 0.995
    elif distance_m >= 3000:
        pct = 0.998
    else:
        pct = 1.0
    return round(vo2 / pct, 1)


def _parse_time(time_str: str) -> float:
    """解析 '1:56:47' 或 '24:18' 为秒数，兼容全角冒号。"""
    normalized = str(time_str).strip().replace("：", ":")
    parts = normalized.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    else:
        return float(normalized)


def clean_pb_data(
    personal_bests: dict[str, Any],
    today: date | None = None,
) -> tuple[PBRecord | None, list[PBRecord], list[str]]:
    """需求 1：PB 数据清洗与置信度判定。

    时效性衰减：>90天权重50%，>180天权重20%。
    VDOT 校验：半马 VDOT 比 5K 低 >= 2.0 则权重归零。
    5K 与 10K VDOT 差值 <= 1.5 则合并为速度基准锚点。
    最终取通过校验且权重最高的短距离 PB。
    """
    today = today or date.today()
    warnings: list[str] = []
    records: list[PBRecord] = []

    for dist_key, dist_m in DISTANCE_MAP.items():
        raw = personal_bests.get(dist_key)
        if not raw:
            continue
        time_str = raw.get("time") if isinstance(raw, dict) else str(raw)
        if not time_str:
            continue
        try:
            time_sec = _parse_time(time_str)
        except (TypeError, ValueError):
            warnings.append(f"忽略无法解析的 {dist_key} PB 时间")
            continue
        if not math.isfinite(time_sec) or time_sec <= 0:
            warnings.append(f"忽略无效的 {dist_key} PB 时间")
            continue

        updated_str = raw.get("updated") if isinstance(raw, dict) else ""
        if updated_str:
            try:
                updated_date = datetime.fromisoformat(updated_str).date()
                days_ago = (today - updated_date).days
            except (ValueError, TypeError):
                days_ago = 365
        else:
            days_ago = 365

        weight = 1.0
        reason = ""
        if days_ago > 180:
            weight = 0.2
            reason = f"{dist_key} PB 超过 180 天未更新，权重衰减至 20%"
        elif days_ago > 90:
            weight = 0.5
            reason = f"{dist_key} PB 超过 90 天未更新，权重衰减至 50%"

        vdot = _compute_vdot(dist_m, time_sec)
        records.append(PBRecord(
            distance=dist_key, time_seconds=time_sec,
            updated_days_ago=days_ago, weight=weight, vdot=vdot, reason=reason,
        ))

    if not records:
        return None, [], ["无任何有效 PB 数据"]

    hm = next((r for r in records if r.distance == "half_marathon"), None)
    five_k = next((r for r in records if r.distance == "5k"), None)
    ten_k = next((r for r in records if r.distance == "10k"), None)

    if hm and five_k and (five_k.vdot - hm.vdot) >= 2.0:
        hm.weight = 0.0
        hm.reason = (
            f"半马 VDOT({hm.vdot}) 比 5K VDOT({five_k.vdot}) 低 >= 2.0，"
            f"判定为耐力不足，配速权重归零"
        )
        warnings.append(hm.reason)

    if five_k and ten_k and abs(five_k.vdot - ten_k.vdot) <= 1.5:
        five_k.reason = "5K 与 10K VDOT 高度一致，合并为速度基准锚点（权重最高）"
        five_k.weight = max(five_k.weight, 1.0)

    candidates = [r for r in records if r.weight > 0]
    if not candidates:
        return None, records, ["所有 PB 均未通过校验"]

    priority = {"5k": 0, "10k": 1, "half_marathon": 2, "marathon": 3}
    candidates.sort(key=lambda r: (priority.get(r.distance, 99), -r.weight))
    effective = candidates[0]

    return effective, records, warnings


# ============================================================================
# 需求 2：心率区间（Karvonen）
# ============================================================================

def compute_hr_zones(
    hr_rest: int, hr_max: int, age: int | None = None,
) -> tuple[dict[str, tuple[int, int]], str, list[str]]:
    """需求 2：Karvonen 储备心率法。心率区间不可被配速或天气覆盖。"""
    warnings: list[str] = []
    hr_max_source = "provided"

    if hr_max <= 0:
        if age and age > 0:
            hr_max = int(207 - 0.7 * age)
            hr_max_source = "estimated"
            warnings.append(f"最大心率缺失，使用估算公式 207 - 0.7 * {age} = {hr_max} bpm")
        else:
            hr_max = 180
            hr_max_source = "default"
            warnings.append("最大心率缺失且无年龄，使用默认值 180 bpm")

    if hr_rest <= 0:
        hr_rest = 65
        warnings.append("静息心率缺失，使用默认值 65 bpm，建议测量晨脉")

    reserve = hr_max - hr_rest
    zones: dict[str, tuple[int, int]] = {}
    for zone, (lo_pct, hi_pct) in HR_ZONE_PCTS.items():
        lo = round(hr_rest + reserve * lo_pct)
        hi = round(hr_rest + reserve * hi_pct)
        zones[zone] = (lo, hi)

    return zones, hr_max_source, warnings


# ============================================================================
# 需求 3：配速基线推算（VDOT 映射）
# ============================================================================

def compute_pace_baseline(effective_pb: PBRecord) -> dict[str, tuple[float, float]]:
    """需求 3：基于有效 PB 推算 Z1-Z5 理论配速区间。"""
    dist_m = DISTANCE_MAP.get(effective_pb.distance, 5000)
    race_pace = effective_pb.time_seconds / (dist_m / 1000)

    z2_min = round(race_pace * 1.20)
    z2_max = round(race_pace * 1.25)
    z4_min = round(race_pace * 1.00)
    z4_max = round(race_pace * 1.02)

    return {
        "z1": (round(z2_max * 1.10), round(z2_max * 1.15)),
        "z2": (z2_min, z2_max),
        "z3": (round(((z2_max + z4_min) / 2) * 0.97), round(((z2_max + z4_min) / 2) * 1.03)),
        "z4": (z4_min, z4_max),
        "z5": (round(race_pace * 0.92), round(race_pace * 0.95)),
    }


# ============================================================================
# 需求 4：近期训练反馈校准
# ============================================================================

def _activity_in_hr_zone(avg_hr: float | None, hr_zone: tuple[int, int]) -> bool:
    return avg_hr is not None and hr_zone[0] <= avg_hr <= hr_zone[1]


def calibrate_from_recent_activities(
    baseline: dict[str, tuple[float, float]],
    hr_zones: dict[str, tuple[int, int]],
    activities: list[dict[str, Any]],
) -> tuple[dict[str, tuple[float, float]], dict[str, Any], list[str]]:
    """需求 4：近期训练反馈校准。"""
    warnings: list[str] = []
    adjusted = dict(baseline)
    meta: dict[str, Any] = {
        "calibrated": False, "method": "none", "samples_used": 0,
        "z2_activities_found": 0, "slow_count": 0, "hr_high_count": 0,
        "force_slow_today": False, "slow_seconds": 0,
    }

    if len(activities) < 3:
        warnings.append("近期跑步记录少于 3 条，跳过近期反馈校准")
        return adjusted, meta, warnings

    z2_hr = hr_zones["z2"]
    z2_pace = baseline["z2"]

    z2_activities = [a for a in activities
                     if _activity_in_hr_zone(a.get("avg_heart_rate"), z2_hr)]
    meta["z2_activities_found"] = len(z2_activities)

    # 场景 B：心率偏高（按近期全部跑步评估，不依赖 Z2 心率命中数）
    all_recent = sorted(
        activities, key=lambda a: str(a.get("activity_date", "")), reverse=True,
    )[:5]
    hr_high_count = 0
    for act in all_recent:
        avg_hr = act.get("avg_heart_rate")
        d = act.get("distance_meters", 0)
        t = act.get("duration_seconds", 0)
        if d > 0 and t > 0 and avg_hr:
            if t / (d / 1000) <= z2_pace[1] and avg_hr > z2_hr[1]:
                hr_high_count += 1
    meta["hr_high_count"] = hr_high_count
    if hr_high_count >= 2:
        meta["force_slow_today"] = True
        meta["slow_seconds"] = 10
        warnings.append(
            f"近期 {hr_high_count} 次 Z2 配速心率偏高，今日强制降速 10-15s/km"
        )

    if len(z2_activities) < 3:
        warnings.append(
            f"落在 Z2 心率区间内的有效记录不足 3 条"
            f"（实际 {len(z2_activities)}），跳过反馈校准"
        )
        return adjusted, meta, warnings

    z2_activities.sort(key=lambda a: str(a.get("activity_date", "")), reverse=True)
    recent = z2_activities[:5]

    actual_paces: list[float] = []
    for act in recent:
        d = act.get("distance_meters", 0)
        t = act.get("duration_seconds", 0)
        if d > 0 and t > 0:
            actual_paces.append(t / (d / 1000))

    meta["samples_used"] = len(actual_paces)
    if len(actual_paces) < 3:
        return adjusted, meta, warnings

    # 场景 A：配速偏慢
    slow_count = sum(1 for p in actual_paces if p > z2_pace[1] + 10)
    meta["slow_count"] = slow_count

    if slow_count >= 3:
        shift = round(sum(actual_paces) / len(actual_paces) - z2_pace[1])
        adjusted["z2"] = (round(z2_pace[0] + shift), round(z2_pace[1] + shift))
        meta["calibrated"] = True
        meta["method"] = "pace_shift"
        meta["shift_seconds"] = shift
        warnings.append(
            f"连续 {slow_count} 次 Z2 跑配速偏慢 >= 10s/km，"
            f"判定为状态偏移，Z2 配速整体平移 {shift}s/km"
        )

    return adjusted, meta, warnings


# ============================================================================
# 需求 5：环境与疲劳非线性补偿
# ============================================================================

def apply_environment_compensation(
    paces: dict[str, tuple[float, float]],
    temp_c: float | None = None,
    humidity: float | None = None,
    recent_3day_run_count: int = 0,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any], list[str]]:
    """需求 5：环境与疲劳补偿。只修改配速，不修改心率。"""
    warnings: list[str] = []
    adjusted = dict(paces)
    meta: dict[str, Any] = {
        "hr_priority_mode": False, "temp_compensation_sec": 0,
        "fatigue_compensation": False,
    }

    z2 = adjusted.get("z2", (0, 0))
    z4 = adjusted.get("z4", (0, 0))

    # 高温补偿
    if temp_c is not None and humidity is not None:
        if temp_c > 30:
            meta["hr_priority_mode"] = True
            meta["temp_compensation_sec"] = 30
            warnings.append(
                f"气温 {temp_c}C > 30C，激活心率优先模式，"
                f"配速建议仅作参考，允许比理论值慢 30s 以上"
            )
            z2_lo, z2_hi = z2
            adjusted["z2"] = (z2_lo, z2_hi + 30)
        elif temp_c > 25 and humidity > 70:
            bonus = min(round(10 + (temp_c - 25) / 5 * 10), 20)
            meta["temp_compensation_sec"] = bonus
            warnings.append(
                f"气温 {temp_c}C 湿度 {humidity}%，Z2 配速向后平移 {bonus}s/km"
            )
            z2_lo, z2_hi = z2
            adjusted["z2"] = (z2_lo + bonus, z2_hi + bonus)

    # 疲劳累积
    if recent_3day_run_count >= 2:
        meta["fatigue_compensation"] = True
        warnings.append(
            f"最近 3 天有 {recent_3day_run_count} 天跑步记录，"
            f"判定疲劳累积，取 Z2 慢端"
        )
        z2_lo, z2_hi = adjusted.get("z2", (0, 0))
        adjusted["z2"] = (z2_hi - 5, z2_hi)

    # 边界保护：Z2 快端不得快于 Z4 慢端
    z2_lo, z2_hi = adjusted.get("z2", (0, 0))
    z4_lo, z4_hi = z4
    if z2_lo > 0 and z4_lo > 0 and z2_lo < z4_lo:
        warnings.append(
            f"修正后 Z2 快端 ({z2_lo}s/km) 快于 Z4 慢端 ({z4_lo}s/km)，"
            f"封顶至 Z4 慢端"
        )
        adjusted["z2"] = (z4_lo, max(z2_hi, z4_lo + 5))

    return adjusted, meta, warnings


# ============================================================================
# 需求 6：异常与缺失值降级
# ============================================================================

def fallback_from_recent_avg(
    activities: list[dict[str, Any]],
) -> tuple[float | None, str]:
    """需求 6：PB 全部过期时，用最近 3 次跑步平均配速反推 Z2。"""
    if not activities:
        return None, "无任何跑步记录"

    def finite_number(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    running = [
        a for a in activities
        if finite_number(a.get("distance_meters")) >= 3000
        and finite_number(a.get("duration_seconds")) > 0
    ]
    running.sort(key=lambda a: str(a.get("activity_date", "")), reverse=True)
    recent = running[:3]

    if len(recent) < 3:
        return None, f"近期跑步记录不足 3 条（实际 {len(recent)}），无法反推"

    paces = []
    for act in recent:
        d = finite_number(act.get("distance_meters"))
        t = finite_number(act.get("duration_seconds"))
        if d > 0 and t > 0:
            paces.append(t / (d / 1000))

    if not paces:
        return None, "近期记录无法计算有效配速"

    return round(sum(paces) / len(paces)), "基于最近 3 次跑步平均配速反推"


# ============================================================================
# 格式化
# ============================================================================

def _format_pace(sec_per_km: float) -> str:
    if sec_per_km <= 0:
        return "--:--"
    m = int(sec_per_km) // 60
    s = int(sec_per_km) % 60
    return f"{m}:{s:02d}"


# ============================================================================
# 主入口
# ============================================================================

def compute_all_zones(
    personal_bests: dict[str, Any],
    hr_rest: int,
    hr_max: int,
    age: int | None = None,
    activities: list[dict[str, Any]] | None = None,
    temp_c: float | None = None,
    humidity: float | None = None,
    today: date | None = None,
) -> ComputeResult:
    """一站式计算 Z1-Z5 配速与心率区间。"""
    activities = activities or []
    today = today or date.today()
    all_warnings: list[str] = []

    # 需求 1：PB 清洗
    effective_pb, pb_records, pb_warnings = clean_pb_data(personal_bests, today)
    all_warnings.extend(pb_warnings)

    # 需求 2：心率区间
    hr_zones, hr_max_source, hr_warnings = compute_hr_zones(hr_rest, hr_max, age)
    all_warnings.extend(hr_warnings)

    # 需求 6：所有 PB 超 180 天 → 走 fallback
    all_pb_expired = (
        len(pb_records) > 0
        and all(r.updated_days_ago > 180 for r in pb_records)
    )

    pace_baseline: dict[str, tuple[float, float]] | None = None
    pace_source = ""

    if effective_pb is None or all_pb_expired:
        if all_pb_expired:
            all_warnings.append("所有 PB 均超过 180 天未更新，请重测 PB！")
        fallback_pace, fallback_reason = fallback_from_recent_avg(activities)
        if fallback_pace:
            all_warnings.append(f"配速基线: {fallback_reason}")
            z2_hi = fallback_pace
            pace_baseline = compute_pace_baseline(PBRecord(
                distance="fallback", time_seconds=z2_hi * 5,
                updated_days_ago=0, vdot=0,
            ))
            # Override with actual fallback values
            z2_lo = round(z2_hi * 0.95)
            pace_baseline = {
                "z1": (round(z2_hi * 1.10), round(z2_hi * 1.15)),
                "z2": (z2_lo, z2_hi),
                "z3": (round(z2_hi * 0.92), round(z2_hi * 0.98)),
                "z4": (round(z2_hi * 0.85), round(z2_hi * 0.90)),
                "z5": (round(z2_hi * 0.78), round(z2_hi * 0.83)),
            }
            pace_source = f"fallback_recent_avg ({fallback_reason})"
        else:
            all_warnings.append("无有效 PB 且无近期跑步记录，无法计算配速区间")
    else:
        # 需求 3：配速基线
        pace_baseline = compute_pace_baseline(effective_pb)
        pace_source = f"VDOT 映射 (based on {effective_pb.distance} PB)"

    # 需求 4：近期反馈校准
    calibration_meta: dict[str, Any] = {"calibrated": False}
    if pace_baseline and len(activities) >= 3:
        pace_baseline, calibration_meta, cal_warnings = (
            calibrate_from_recent_activities(pace_baseline, hr_zones, activities)
        )
        all_warnings.extend(cal_warnings)
    elif pace_baseline:
        all_warnings.append(
            "近期跑步记录不足 3 条，跳过需求 4（近期反馈校准），置信度偏低"
        )

    # 需求 5：环境与疲劳补偿
    env_meta: dict[str, Any] = {"hr_priority_mode": False}
    if pace_baseline:
        recent_3day = 0
        cutoff = today - timedelta(days=3)
        for act in activities:
            act_date_str = str(act.get("activity_date", ""))
            try:
                act_date = date.fromisoformat(act_date_str[:10])
                if act_date >= cutoff:
                    a_type = str(act.get("activity_type", "")).lower()
                    a_name = str(act.get("activity_name", "")).lower()
                    if "run" in a_type or "跑" in a_name or "跑" in a_type:
                        recent_3day += 1
            except (ValueError, TypeError):
                pass

        pace_baseline, env_meta, env_warnings = apply_environment_compensation(
            pace_baseline, temp_c=temp_c, humidity=humidity,
            recent_3day_run_count=recent_3day,
        )
        all_warnings.extend(env_warnings)

    # 组装输出
    zones: list[ZoneResult] = []
    for zone_name in ["z1", "z2", "z3", "z4", "z5"]:
        hr = hr_zones.get(zone_name, (0, 0))
        pace = pace_baseline.get(zone_name) if pace_baseline else None
        if pace:
            zones.append(ZoneResult(
                zone=zone_name, hr_min=hr[0], hr_max=hr[1],
                pace_min_sec_per_km=pace[0], pace_max_sec_per_km=pace[1],
                pace_display=f"{_format_pace(pace[0])}-{_format_pace(pace[1])}/km",
                source=pace_source,
            ))
        else:
            zones.append(ZoneResult(
                zone=zone_name, hr_min=hr[0], hr_max=hr[1],
                pace_min_sec_per_km=None, pace_max_sec_per_km=None,
                pace_display="不可用", source=pace_source,
            ))

    effective_pb_info: dict[str, Any] = {}
    if effective_pb:
        effective_pb_info = {
            "distance": effective_pb.distance,
            "time_seconds": effective_pb.time_seconds,
            "vdot": effective_pb.vdot,
            "weight": effective_pb.weight,
            "updated_days_ago": effective_pb.updated_days_ago,
        }

    return ComputeResult(
        zones=zones, effective_pb=effective_pb_info,
        hr_rest=hr_rest if hr_rest > 0 else 65,
        hr_max=hr_max if hr_max > 0 else (207 - int(0.7 * (age or 30))),
        hr_max_source=hr_max_source,
        meta={
            "calibration": calibration_meta,
            "environment": env_meta,
            "pb_records": [
                {
                    "distance": r.distance, "time_seconds": r.time_seconds,
                    "vdot": r.vdot, "weight": r.weight, "reason": r.reason,
                }
                for r in pb_records
            ],
        },
        warnings=all_warnings,
    )





# ============================================================================
# 持久化：读写 fitness-assessment.md 中的 pace_zones
# ============================================================================

def _pace_zones_path(memory_dir: str) -> str:
    import os
    return os.path.join(memory_dir, "profile", "fitness-assessment.md")


def load_cached_pace_zones(memory_dir: str) -> dict[str, Any] | None:
    """从 fitness-assessment.md 读取缓存的 pace_zones。"""
    import os, re
    path = _pace_zones_path(memory_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            text = f.read()
        # 解析 YAML front matter
        m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
        if not m:
            return None
        import yaml
        try:
            fm = yaml.safe_load(m.group(1))
        except Exception:
            # 如果没有 yaml 库，用简单解析
            fm = _parse_simple_yaml(m.group(1))
        return fm.get("pace_zones") if isinstance(fm, dict) else None
    except Exception:
        return None


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """简陋 YAML 解析，用于没有 pyyaml 的环境。"""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_dict: dict[str, Any] | None = None
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not line.startswith(' ') and not line.startswith('\t'):
            if ':' in stripped:
                k, _, v = stripped.partition(':')
                k = k.strip()
                v = v.strip().strip("'\"")
                if v == '' or v == '{}':
                    result[k] = {}
                    current_key = k
                    current_dict = result[k]
                elif v in ('true', 'false'):
                    result[k] = v == 'true'
                elif v == 'null':
                    result[k] = None
                else:
                    try:
                        result[k] = float(v) if '.' in v else int(v)
                    except ValueError:
                        result[k] = v
        elif current_key and current_dict is not None:
            if ':' in stripped:
                k, _, v = stripped.partition(':')
                k = k.strip()
                v = v.strip().strip("'\"")
                if v == '' or v == '{}':
                    current_dict[k] = {}
                elif v in ('true', 'false'):
                    current_dict[k] = v == 'true'
                elif v == 'null':
                    current_dict[k] = None
                else:
                    try:
                        current_dict[k] = float(v) if '.' in v else int(v)
                    except ValueError:
                        current_dict[k] = v
    return result


def _simple_yaml_dump(data: dict[str, Any], indent: int = 0) -> str:
    """简陋 YAML 序列化。"""
    lines = []
    prefix = "  " * indent
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(_simple_yaml_dump(v, indent + 1))
        elif isinstance(v, bool):
            lines.append(f"{prefix}{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{prefix}{k}: null")
        elif isinstance(v, str):
            lines.append(f"{prefix}{k}: '{v}'")
        else:
            lines.append(f"{prefix}{k}: {v}")
    return '\n'.join(lines)


def save_pace_zones_to_profile(
    memory_dir: str,
    zones_result: ComputeResult,
    hr_rest: int,
    hr_max: int,
    age: int | None = None,
) -> bool:
    """将 Z1-Z5 计算结果写入 fitness-assessment.md 的 front matter。

    只更新 pace_zones 字段，不修改 personal_info / personal_bests 等已有字段。
    同时更新 body 中的 pace zones 表格。
    """
    import os, re, json as _json
    from datetime import datetime

    path = _pace_zones_path(memory_dir)
    if not os.path.exists(path):
        return False

    try:
        with open(path, 'r') as f:
            original = f.read()
    except Exception:
        return False

    # 解析现有 front matter 和 body
    m = re.match(r'^(---\s*\n.*?\n---)\s*\n(.*)$', original, re.DOTALL)
    if not m:
        return False

    fm_block = m.group(1)
    body = m.group(2)

    # 构建 pace_zones 数据
    pace_zones_data: dict[str, Any] = {
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "hr_rest": hr_rest,
        "hr_max": hr_max,
    }
    if age:
        pace_zones_data["age"] = age
    for z in zones_result.zones:
        pace_zones_data[z.zone] = {
            "hr": f"{z.hr_min}-{z.hr_max}",
            "pace": z.pace_display,
            "pace_min_sec": z.pace_min_sec_per_km,
            "pace_max_sec": z.pace_max_sec_per_km,
            "source": z.source,
        }
    if zones_result.effective_pb:
        pace_zones_data["based_on"] = (
            f"{zones_result.effective_pb.get('distance', 'unknown')} PB "
            f"(VDOT {zones_result.effective_pb.get('vdot', '?')})"
        )

    pace_zones_yaml = _simple_yaml_dump({"pace_zones": pace_zones_data})

    # 在 front matter 中追加 pace_zones（如果已存在则替换）
    fm_inner = fm_block[4:-4]  # 去掉 --- 标记
    if '\npace_zones:' in fm_inner:
        # 替换已有的 pace_zones 块
        fm_inner = re.sub(
            r'\npace_zones:.*?(?=\n(?:[a-z_]|$))',
            '\n' + pace_zones_yaml,
            fm_inner,
            flags=re.DOTALL,
        )
    else:
        fm_inner = fm_inner.rstrip() + '\n' + pace_zones_yaml

    new_fm = f"---\n{fm_inner}\n---"

    # 更新 body 中的 pace zones 表格
    pace_table = _build_pace_zones_table(zones_result)
    body = _replace_or_append_section(body, "## 训练配速区间", pace_table)

    new_content = new_fm + "\n" + body

    try:
        from .local_files import atomic_write_private
        atomic_write_private(path, new_content)
    except ImportError:
        import os as _os
        tmp = path + ".tmp"
        with open(tmp, 'w') as f:
            f.write(new_content)
        _os.replace(tmp, path)

    return True


def _build_pace_zones_table(result: ComputeResult) -> str:
    """构建 pace zones Markdown 表格。"""
    lines = [
        "## 训练配速区间",
        "",
        "> 基于最新个人数据与 pace-zones skill 自动计算，每周同步后更新。",
        "",
        "| 区间 | 心率 (bpm) | 配速 | 用途 |",
        "|------|-----------|------|------|",
    ]
    labels = {
        "z1": "恢复跑", "z2": "有氧跑", "z3": "马拉松配速",
        "z4": "阈值跑", "z5": "间歇跑",
    }
    for z in result.zones:
        hr = f"{z.hr_min}–{z.hr_max}"
        pace = z.pace_display
        label = labels.get(z.zone, z.zone)
        lines.append(f"| {z.zone.upper()} {label} | {hr} | {pace} | {z.source} |")

    if result.warnings:
        lines.append("")
        lines.append("### 注意事项")
        for w in result.warnings[:5]:
            lines.append(f"- ⚠️ {w}")

    return '\n'.join(lines)


def _replace_or_append_section(body: str, section_title: str, new_content: str) -> str:
    """替换或追加 Markdown 章节。"""
    import re
    pattern = re.compile(
        rf'^{re.escape(section_title)}\n.*?(?=\n## |\Z)',
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(body):
        return pattern.sub(new_content, body)
    else:
        return body.rstrip() + '\n\n' + new_content + '\n'


def refresh_pace_zones_if_stale(
    memory_dir: str,
    personal_bests: dict[str, Any],
    hr_rest: int,
    hr_max: int,
    age: int | None = None,
    activities: list[dict[str, Any]] | None = None,
    max_age_days: int = 7,
    force: bool = False,
) -> dict[str, Any] | None:
    """如果 pace_zones 缓存不存在或超过 max_age_days，重新计算并写入 profile。

    返回更新后的 pace_zones dict，如果未更新则返回 None。
    """
    from datetime import datetime, timedelta

    if not force:
        cached = load_cached_pace_zones(memory_dir)
        if cached:
            updated_str = cached.get("updated", "")
            try:
                updated = datetime.fromisoformat(updated_str)
                if (datetime.now() - updated).days < max_age_days:
                    return cached
            except (ValueError, TypeError):
                pass

    result = compute_all_zones(
        personal_bests=personal_bests,
        hr_rest=hr_rest,
        hr_max=hr_max,
        age=age,
        activities=activities or [],
        today=date.today(),
    )

    ok = save_pace_zones_to_profile(
        memory_dir, result, hr_rest, hr_max, age,
    )

    if not ok:
        return None

    # 返回写入的数据
    zones_data: dict[str, Any] = {
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "hr_rest": hr_rest, "hr_max": hr_max,
    }
    for z in result.zones:
        zones_data[z.zone] = {
            "hr": f"{z.hr_min}-{z.hr_max}",
            "pace": z.pace_display,
            "pace_min_sec": z.pace_min_sec_per_km,
            "pace_max_sec": z.pace_max_sec_per_km,
            "source": z.source,
        }
    return zones_data

# ============================================================================
# 格式适配：转换为 training_pace.py 兼容格式
# ============================================================================

def to_legacy_profile(result: ComputeResult) -> dict[str, Any]:
    """将 ComputeResult 转换为 PaceCalibrationProfileBuilder.build() 兼容的 dict。

    下游 DailyPaceAdjustmentEngine 期望:
        profile["zones"]["z2"]["min_sec_per_km"] 等字段。
    """
    import hashlib, json as _json

    zones: dict[str, Any] = {}
    for z in result.zones:
        pace_min = z.pace_min_sec_per_km
        pace_max = z.pace_max_sec_per_km
        available = pace_min is not None and pace_max is not None
        mid = round((pace_min + pace_max) / 2) if available else None
        zones[z.zone] = {
            "status": "available" if available else "unavailable",
            "source": z.source if available else "insufficient_data",
            "min_sec_per_km": pace_min,
            "max_sec_per_km": pace_max,
            "display_range": z.pace_display if available else None,
            "applies_to": "work_interval" if z.zone == "z5" else "continuous_main",
            "basis_refs": [result.effective_pb.get("distance", "unknown")],
            "sample_count": result.meta.get("calibration", {}).get("samples_used", 0),
            "confidence": "medium" if available else "low",
            "recent_median_sec_per_km": mid,
            "baseline_median_sec_per_km": mid,
        }

    version_source = _json.dumps(
        {"zones": zones, "warnings": result.warnings}, sort_keys=True, ensure_ascii=False,
    )
    return {
        "type": "pace_calibration_profile",
        "version": hashlib.sha256(version_source.encode("utf-8")).hexdigest()[:12],
        "policy_version": "pace-zones-v1",
        "as_of": str(date.today()),
        "facts_cutoff": str(date.today()),
        "zones": zones,
        "data_quality": {
            "status": "sufficient" if any(z.pace_min_sec_per_km for z in result.zones) else "insufficient",
            "available_zone_count": sum(1 for z in result.zones if z.pace_min_sec_per_km),
            "excluded_reasons": {},
            "weather_normalized": result.meta.get("environment", {}).get("hr_priority_mode", False),
        },
        "warnings": result.warnings,
    }

# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="个人配速区间计算引擎")
    parser.add_argument("--pb-json", type=str, required=True, help="PB 数据 JSON")
    parser.add_argument("--hr-rest", type=int, required=True, help="静息心率")
    parser.add_argument("--hr-max", type=int, required=True, help="最大心率")
    parser.add_argument("--age", type=int, default=None, help="年龄")
    parser.add_argument("--activities-json", type=str, default="[]", help="近期活动列表 JSON")
    parser.add_argument("--temp", type=float, default=None, help="当前气温 C")
    parser.add_argument("--humidity", type=float, default=None, help="当前湿度百分比")
    parser.add_argument("--today", type=str, default=None, help="日期 YYYY-MM-DD")

    args = parser.parse_args()

    try:
        pb = json.loads(args.pb_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"PB JSON 解析失败: {e}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        activities = json.loads(args.activities_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Activities JSON 解析失败: {e}"}, ensure_ascii=False))
        sys.exit(1)

    today = date.fromisoformat(args.today) if args.today else date.today()

    result = compute_all_zones(
        personal_bests=pb, hr_rest=args.hr_rest, hr_max=args.hr_max,
        age=args.age, activities=activities,
        temp_c=args.temp, humidity=args.humidity, today=today,
    )

    output = {
        "zones": [
            {
                "zone": z.zone, "hr_min": z.hr_min, "hr_max": z.hr_max,
                "pace_min_sec_per_km": z.pace_min_sec_per_km,
                "pace_max_sec_per_km": z.pace_max_sec_per_km,
                "pace_display": z.pace_display, "source": z.source,
                "warnings": z.warnings,
            }
            for z in result.zones
        ],
        "effective_pb": result.effective_pb,
        "hr_rest": result.hr_rest, "hr_max": result.hr_max,
        "hr_max_source": result.hr_max_source,
        "meta": result.meta, "warnings": result.warnings,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
