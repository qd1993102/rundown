"""专业赛事方案的事实包、负荷边界、Skill 生成与确定性校验。"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import time
from datetime import date, timedelta
from typing import Any

from .coach_runtime import (
    CoachRunContext,
    CoachSkillRunner,
    OpenAICompatibleSkillModel,
    SkillRegistry,
)
from .coach_runtime.schemas import validate_output

logger = logging.getLogger(__name__)


def _stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _failure_type(message: str) -> str:
    text = str(message)
    if "超时" in text:
        return "provider_timeout"
    if "未配置" in text:
        return "provider_unavailable"
    if "JSON" in text or "结构" in text or "输出无效" in text:
        return "schema_invalid"
    if "请求失败" in text or "服务" in text:
        return "provider_request_failed"
    return "stage_failed"


def _positive_int(value: Any, default: int = 0) -> int:
    """安全读取分钟数，避免展示层因旧方案字段失效。"""
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


_WEEKDAY_TOKENS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _parse_fixed_unavailable(text: str) -> set[int]:
    """把“固定不可训练时间”自由文本解析为排除的星期几（周一=0…周日=6）。

    如“周三晚”→{2}、“每周二、周四”→{1, 3}、“周末”→{5, 6}；
    无星期关键词返回空集。
    """
    if not text:
        return set()
    if "周末" in text:
        return {5, 6}
    return {day for ch in text if ch in _WEEKDAY_TOKENS for day in (_WEEKDAY_TOKENS[ch],)}


def _effective_available_days(constraints: dict[str, Any]) -> list[int]:
    """可训练日扣除固定不可训练日后的有效排课日。"""
    days = sorted(int(day) for day in constraints.get("available_days") or [])
    blocked = {int(day) for day in constraints.get("fixed_unavailable_days") or []}
    effective = [day for day in days if day not in blocked]
    return effective or days


def _personal_pace_reference(value: Any) -> float | None:
    """读取可用于训练处方的近期跑步配速事实（秒/公里）。"""
    try:
        pace = float(value)
    except (TypeError, ValueError):
        return None
    return pace if 180 <= pace <= 720 else None


def _number(value: Any) -> float | None:
    """正数解析；非正/非法返回 None。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


# 定时跑（只有时长无距离）按个人 Z2 配速与强度区间的保守比例估算距离；
# 折扣覆盖热身/放松/恢复段占整课的比例，避免把主段配速套用到整课。
_DURATION_ZONE_PACE_RATIO = {1: 1.10, 2: 1.00, 3: 0.93, 4: 0.85, 5: 0.78}
_DURATION_TYPE_DISCOUNT = {
    "interval": 0.85, "intervals": 0.85, "repetition": 0.85, "reps": 0.85,
    "fartlek": 0.85, "tempo": 0.90, "threshold": 0.90,
}


def _estimate_duration_distance(
    workout: dict[str, Any], pace_reference_sec_per_km: float,
) -> float:
    """定时跑按个人配速 + 强度折扣估算距离，标记为估算值参与周量对齐。"""
    duration = _positive_int(workout.get("duration_minutes"))
    if not duration:
        return 0.0
    try:
        zone = int(
            workout.get("intensity_zone")
            or (workout.get("intensity_intent") or {}).get("zone")
            or 2
        )
    except (TypeError, ValueError):
        zone = 2
    if zone not in _DURATION_ZONE_PACE_RATIO:
        zone = 2
    zone_pace = pace_reference_sec_per_km * _DURATION_ZONE_PACE_RATIO[zone]
    work_type = str(workout.get("type") or "").lower()
    discount = _DURATION_TYPE_DISCOUNT.get(work_type, 1.0)
    return round(duration / (zone_pace / 60) * discount, 1)


# ── 审计确定性复核：AI 审计的 critical 数值规则用确定性代码复核，误判降级为 warning ──
_QUALITY_INTENTS = {"interval", "repetition", "tempo", "threshold", "race_pace", "marathon_pace"}
_QUALITY_TYPES = {"interval", "intervals", "tempo", "threshold", "quality", "fartlek"}
_WEEKDAY_LONG_SESSION_MINUTES = 120.0


def _audit_workouts(result: dict[str, Any]) -> list[dict[str, Any]]:
    workouts: list[dict[str, Any]] = []
    for week in result.get("first_two_weeks") or result.get("first_four_weeks") or []:
        if not isinstance(week, dict):
            continue
        for w in week.get("workouts") or []:
            if isinstance(w, dict):
                workouts.append(w)
    return workouts


def _is_quality_workout(w: dict[str, Any]) -> bool:
    intent = str(w.get("pace_intent") or "").lower()
    wtype = str(w.get("type") or "").lower()
    if intent in _QUALITY_INTENTS or wtype in _QUALITY_TYPES:
        return True
    title = str(w.get("title") or "")
    return bool(w.get("is_key")) and any(
        token in title for token in ("间歇", "变速", "节奏", "阈值")
    )


def _workout_duration_minutes(w: dict[str, Any], pace_reference: float | None) -> float | None:
    if w.get("duration_minutes"):
        try:
            return float(w["duration_minutes"])
        except (TypeError, ValueError):
            pass
    distance = _number(w.get("distance_km"))
    if distance and pace_reference:
        return round(distance * pace_reference / 60, 1)
    return None


def _confirm_long_run_exceed(result: dict[str, Any], facts: dict[str, Any]) -> bool:
    """长距离是否真的超过阈值：min(个人历史×1.15, 全马×0.75)；基础期再加全马×0.5 上限。"""
    longest_history = _number((facts.get("baseline") or {}).get("longest_distance_km")) or 0.0
    marathon_km = 42.195
    cap = marathon_km * 0.75
    if longest_history:
        cap = min(cap, longest_history * 1.15)
    phases = result.get("periodization") or []
    first_phase = str((phases[0] if phases else {}).get("name") or "")
    if any(token in first_phase for token in ("基础", "适应", "有氧")):
        cap = min(cap, marathon_km * 0.5)
    for w in _audit_workouts(result):
        wtype = str(w.get("type") or "").lower()
        intent = str(w.get("pace_intent") or "").lower()
        if wtype in {"long_run", "long"} or intent == "long_run":
            distance = _number(w.get("distance_km"))
            if distance and distance > cap:
                return True
    return False


def _confirm_weekday_too_long(result: dict[str, Any], facts: dict[str, Any]) -> bool:
    """周中（周一至周五）课程按个人配速推算是否真的超 120 分钟；补充信息明确允许时豁免。"""
    supplement = str((facts.get("supplement") or {}).get("additional_context") or "")
    if any(token in supplement for token in ("周中可", "工作日可", "晚上可跑", "时间充裕", "周中也能", "加班后也能")):
        return False
    pace = _personal_pace_reference(
        (facts.get("athlete_profile") or {}).get("recent_running_pace_sec_per_km"),
    )
    for w in _audit_workouts(result):
        weekday = _number(w.get("weekday"))
        if weekday is None or weekday > 4:
            continue
        if str(w.get("type") or "").lower() == "rest":
            continue
        duration = _workout_duration_minutes(w, pace)
        if duration and duration > _WEEKDAY_LONG_SESSION_MINUTES:
            return True
    return False


def _confirm_quality_back_to_back(result: dict[str, Any], facts: dict[str, Any]) -> bool:
    """质量课是否真的连续两天排，或长距离/质量课后次日是强度课或大距离（轻松跑 >12km）。"""
    by_day: dict[int, list[dict[str, Any]]] = {}
    for w in _audit_workouts(result):
        weekday = _number(w.get("weekday"))
        if weekday is not None and 0 <= weekday <= 6:
            by_day.setdefault(int(weekday), []).append(w)
    days = sorted(by_day)
    for i in range(len(days) - 1):
        if days[i + 1] - days[i] != 1:
            continue
        # 规则 1：质量课连续两天
        if any(_is_quality_workout(w) for w in by_day[days[i]]) and any(
            _is_quality_workout(w) for w in by_day[days[i + 1]]
        ):
            return True
        # 规则 2：长距离/质量课后次日是强度课或大距离轻松跑
        prev_hard = any(
            _is_quality_workout(w)
            or str(w.get("pace_intent") or "").lower() == "long_run"
            or str(w.get("type") or "").lower() in {"long_run", "long"}
            for w in by_day[days[i]]
        )
        if not prev_hard:
            continue
        for w in by_day[days[i + 1]]:
            if _is_quality_workout(w):
                return True
            distance = _number(w.get("distance_km"))
            if distance and distance > 12:
                return True
    return False


def _confirm_weekday_rest_streak(result: dict[str, Any], facts: dict[str, Any]) -> bool:
    """周中（周一至周五）连续两天及以上无训练，且这些天在可训练日内 → 结构失衡提示。"""
    effective = _effective_available_days(facts.get("constraints") or {})
    by_day: set[int] = set()
    for w in _audit_workouts(result):
        weekday = _number(w.get("weekday"))
        if weekday is not None and 0 <= weekday <= 6 and str(w.get("type") or "").lower() != "rest":
            by_day.add(int(weekday))
    weekdays = [day for day in range(5) if day in effective]
    streak = 0
    for day in weekdays:
        if day not in by_day:
            streak += 1
            if streak >= 2:
                return True
        else:
            streak = 0
    return False


def _format_pace(seconds_per_km: float) -> str:
    seconds = max(1, round(seconds_per_km))
    return f"{seconds // 60}:{seconds % 60:02d}/km"


def _default_stimuli(workout: dict[str, Any]) -> list[str]:
    """为旧方案补足刺激语义；这不是用户可见的课程库枚举。"""
    existing = workout.get("stimuli")
    if isinstance(existing, list) and existing:
        return [str(item) for item in existing if item]
    kind = str(workout.get("type") or "").lower()
    title = str(workout.get("title") or "").lower()
    if kind == "long" or "长距离" in title:
        return ["aerobic_endurance", "durability"]
    if any(token in title for token in ("间歇", "interval")) or kind == "interval":
        return ["high_intensity_tolerance", "running_economy"]
    if any(token in title for token in ("节奏", "阈值", "tempo")) or kind in {"tempo", "race_pace"}:
        return ["threshold_development"]
    if any(token in title for token in ("变速", "法特莱克", "fartlek")):
        return ["aerobic_power", "pace_change"]
    if kind == "quality":
        return ["threshold_development"]
    if kind in {"rest", "recovery"}:
        return ["recovery"]
    return ["aerobic_endurance"]


def _default_intensity_zone(stimuli: list[str], workout: dict[str, Any]) -> int:
    """为处方主体补全五级相对强度，不从固定配速或心率反推。"""
    kind = str(workout.get("type") or "").lower()
    if kind == "recovery" or "recovery" in stimuli:
        return 1
    if any(item in {"high_intensity_tolerance", "aerobic_power"} for item in stimuli):
        return 5
    if "threshold_development" in stimuli:
        return 4
    if "race_specific_pace" in stimuli or kind in {"race_pace", "steady"}:
        return 3
    return 2


def _feel_for_zone(zone: int) -> dict[str, str]:
    return {
        1: {"label": "轻松自在，结束后感觉更舒展", "rpe": "1–2"},
        2: {"label": "能完整对话，结束仍有余量", "rpe": "2–3"},
        3: {"label": "呼吸稳定但只能短句交流", "rpe": "4–5"},
        4: {"label": "呼吸明显有压力，但节奏可持续", "rpe": "6–7"},
        5: {"label": "短工作段高强度，恢复段必须完全放松", "rpe": "8–9"},
    }[zone]


def _dose(metric: str, value: int | float, unit: str) -> dict[str, Any]:
    return {"metric": metric, "value": value, "unit": unit}


def _work_step(
    step_id: str,
    order: int,
    role: str,
    *,
    minutes: int,
    zone: int,
    feel: str,
    preferred_metric: str = "feel",
) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "zone": zone,
        "preferred_metric": preferred_metric,
        "fallback_feel": feel,
    }
    if preferred_metric == "pace":
        intent["calibration_context"] = {
            "segment_kind": "work" if role == "work" else "continuous_main",
            "duration_seconds": minutes * 60,
        }
    return {
        "step_id": step_id,
        "order": order,
        "kind": "recovery" if role == "recovery" else "work",
        "role": role,
        "dose": _dose("duration", minutes, "minute"),
        "intensity_intent": intent,
        "transition": (
            {"type": "dose_and_readiness", "readiness": "呼吸恢复并能说完整短句"}
            if role == "recovery" else {"type": "dose_complete"}
        ),
    }


def build_workout_steps_v2(
    workout: dict[str, Any],
    *,
    duration_minutes: int,
    intensity_zone: int,
    stimuli: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """为确定性兜底构建完整逐段课程；不用于反向猜测历史 v1 方案。"""
    duration = max(10, duration_minutes)
    title = str(workout.get("title") or "").lower()
    kind = str(workout.get("type") or "").lower()
    is_quality = bool(
        workout.get("is_key") and intensity_zone >= 4
        or kind in {"quality", "interval", "tempo", "race_pace"}
        or any(item in {
            "threshold_development", "high_intensity_tolerance",
            "aerobic_power", "pace_change",
        } for item in stimuli)
    )
    warmup = min(15, max(3, duration // 4))
    cooldown = min(10, max(2, duration // 5))
    main = max(1, duration - warmup - cooldown)
    steps: list[dict[str, Any]] = [
        _work_step(
            "warmup", 1, "warmup", minutes=warmup, zone=2,
            feel="轻松跑并完成动态活动，能完整对话", preferred_metric="pace",
        ),
    ]
    if not is_quality:
        main_zone = max(1, min(3, intensity_zone))
        steps.append(_work_step(
            "main", 2, "main", minutes=main, zone=main_zone,
            feel=_feel_for_zone(main_zone)["label"], preferred_metric="pace",
        ))
        minimum_repetitions = None
    else:
        # 课型优先决定工作段强度：节奏/阈值=Z4 分段，间歇/重复=Z5 分段
        if kind in {"tempo", "threshold"} or "threshold_development" in stimuli or "阈值" in title:
            work_minutes, recovery_minutes = 5, 2
            work_zone = 4
        elif "high_intensity_tolerance" in stimuli or "间歇" in title or kind == "interval":
            work_minutes, recovery_minutes = 3, 2
            work_zone = 5
        else:
            work_minutes, recovery_minutes = 2, 2
            work_zone = max(4, min(5, intensity_zone))
        cycle = work_minutes + recovery_minutes
        repeat_count = max(2, min(8, main // cycle))
        while repeat_count > 2 and repeat_count * cycle > main:
            repeat_count -= 1
        if repeat_count * cycle > main:
            work_minutes = max(1, main // 4)
            recovery_minutes = max(1, main // 4)
            cycle = work_minutes + recovery_minutes
            repeat_count = max(2, main // cycle)
        steps.append({
            "step_id": "main-repeat",
            "order": 2,
            "kind": "repeat",
            "role": "main",
            "repeat_count": repeat_count,
            "children": [
                _work_step(
                    "work", 1, "work", minutes=work_minutes, zone=work_zone,
                    feel=_feel_for_zone(work_zone)["label"], preferred_metric="pace",
                ),
                _work_step(
                    "recovery", 2, "recovery", minutes=recovery_minutes,
                    zone=1, feel="轻松慢跑，呼吸恢复后再开始下一组",
                ),
            ],
        })
        used = repeat_count * cycle
        if main > used:
            steps.append(_work_step(
                "easy-extension", 3, "main", minutes=main - used, zone=2,
                feel="轻松连续跑，能完整对话", preferred_metric="pace",
            ))
        minimum_repetitions = max(2, repeat_count - 1)
    steps.append(_work_step(
        "cooldown", len(steps) + 1, "cooldown", minutes=cooldown, zone=1,
        feel="慢跑或步行，逐步放松到呼吸自然",
    ))
    completion = {
        "minimum_completed_repetitions": minimum_repetitions,
        "quality_rule": "工作段动作质量稳定，后程不以冲刺弥补",
        "classification": ["completed", "partially_completed", "stopped"],
    }
    return steps, completion


def iter_leaf_workout_steps(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按执行顺序返回处方叶子步骤；返回原对象引用以便目标解析。"""
    result: list[dict[str, Any]] = []
    for step in sorted(
        (item for item in steps if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    ):
        if step.get("kind") == "repeat":
            result.extend(iter_leaf_workout_steps(step.get("children") or []))
        else:
            result.append(step)
    return result


def _dose_text(dose: dict[str, Any]) -> str:
    value = dose.get("value")
    unit = {"minute": "分钟", "second": "秒", "km": "公里", "meter": "米"}.get(
        str(dose.get("unit") or ""), str(dose.get("unit") or ""),
    )
    return f"{value:g} {unit}" if isinstance(value, (int, float)) else f"{value} {unit}".strip()


_DEFAULT_COMPLETION_CLASSIFICATION = [
    "completed",
    "partially_completed",
    "stopped",
]


def _default_completion_criteria(
    steps: list[dict[str, Any]] | None,
    *,
    duration_minutes: int,
    distance_km: float | None,
    primary_completion: str,
) -> dict[str, Any]:
    """Build the smallest executable completion contract from local facts.

    The model is allowed to omit this low-level field.  Completion criteria are
    deliberately derived from the already validated step structure and course
    dose; no training-quality decision is made here.
    """
    minimum_repetitions = None
    for step in steps or []:
        if not isinstance(step, dict) or step.get("kind") != "repeat":
            continue
        try:
            repeat_count = int(step.get("repeat_count") or 0)
        except (TypeError, ValueError):
            repeat_count = 0
        if repeat_count > 0:
            minimum_repetitions = max(1, repeat_count - 1)
            break
    target = (
        f"{distance_km:g} km"
        if primary_completion == "distance" and distance_km
        else f"{duration_minutes} 分钟"
    )
    return {
        "minimum_completed_repetitions": minimum_repetitions,
        "quality_rule": f"优先完成 {target}；动作质量稳定，后程不以冲刺弥补",
        "classification": list(_DEFAULT_COMPLETION_CLASSIFICATION),
    }


def _normalize_v2_completion_criteria(
    completion: Any,
    *,
    steps: list[dict[str, Any]],
    duration_minutes: int,
    distance_km: float | None,
    primary_completion: str,
) -> tuple[dict[str, Any], bool]:
    """Normalize a v2 completion field and report whether it was synthesized."""
    defaults = _default_completion_criteria(
        steps,
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        primary_completion=primary_completion,
    )
    changed = not isinstance(completion, dict)
    normalized = copy.deepcopy(completion) if isinstance(completion, dict) else {}

    minimum = normalized.get("minimum_completed_repetitions")
    if minimum in (None, ""):
        normalized["minimum_completed_repetitions"] = defaults[
            "minimum_completed_repetitions"
        ]
        changed = True
    else:
        try:
            normalized["minimum_completed_repetitions"] = max(0, int(minimum))
        except (TypeError, ValueError):
            normalized["minimum_completed_repetitions"] = defaults[
                "minimum_completed_repetitions"
            ]
            changed = True

    quality_rule = normalized.get("quality_rule")
    if not isinstance(quality_rule, str) or not quality_rule.strip():
        normalized["quality_rule"] = defaults["quality_rule"]
        changed = True

    classifications = normalized.get("classification")
    if not isinstance(classifications, list):
        classifications = []
        changed = True
    classifications = [str(item).strip() for item in classifications if str(item).strip()]
    for status in _DEFAULT_COMPLETION_CLASSIFICATION:
        if status not in classifications:
            classifications.append(status)
            changed = True
    normalized["classification"] = classifications
    return normalized, changed


def project_steps_to_blocks(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 v2 步骤投影为旧客户端可读的 blocks；不支持反向猜测。"""
    blocks: list[dict[str, Any]] = []
    for step in sorted(
        (item for item in steps if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    ):
        if step.get("kind") == "repeat":
            children = sorted(
                (item for item in step.get("children") or [] if isinstance(item, dict)),
                key=lambda item: int(item.get("order") or 0),
            )
            work = next((item for item in children if item.get("role") == "work"), None)
            recovery = next((item for item in children if item.get("role") == "recovery"), None)
            instruction = f"{int(step.get('repeat_count') or 0)} 组"
            if work:
                instruction += f"：快段 {_dose_text(work.get('dose') or {})}"
            if recovery:
                instruction += f" / 慢段 {_dose_text(recovery.get('dose') or {})}"
            duration = sum(
                _step_duration_minutes(item) for item in children
            ) * int(step.get("repeat_count") or 0)
            blocks.append({
                "role": "main", "duration_minutes": round(duration),
                "instruction": instruction,
            })
            continue
        dose = step.get("dose") if isinstance(step.get("dose"), dict) else {}
        intent = step.get("intensity_intent") if isinstance(step.get("intensity_intent"), dict) else {}
        blocks.append({
            "role": str(step.get("role") or "main"),
            "duration_minutes": round(_step_duration_minutes(step)),
            "instruction": str(intent.get("fallback_feel") or "按处方完成"),
        })
    return blocks


def _step_duration_minutes(step: dict[str, Any]) -> float:
    if step.get("kind") == "repeat":
        return int(step.get("repeat_count") or 0) * sum(
            _step_duration_minutes(item)
            for item in step.get("children") or [] if isinstance(item, dict)
        )
    dose = step.get("dose") if isinstance(step.get("dose"), dict) else {}
    if dose.get("metric") != "duration":
        return 0.0
    try:
        value = float(dose.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0
    return value / 60 if dose.get("unit") == "second" else value


def workout_steps_summary(prescription: dict[str, Any]) -> str:
    """生成页面和简报可共用的逐段结构摘要。"""
    parts: list[str] = []
    for step in sorted(
        (item for item in prescription.get("steps") or [] if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    ):
        if step.get("kind") == "repeat":
            children = step.get("children") or []
            work = next((item for item in children if item.get("role") == "work"), None)
            recovery = next((item for item in children if item.get("role") == "recovery"), None)
            detail = f"{int(step.get('repeat_count') or 0)} 组"
            if work:
                detail += f"〔快 {_dose_text(work.get('dose') or {})}"
            if recovery:
                detail += f" / 慢 {_dose_text(recovery.get('dose') or {})}〕"
            parts.append(detail)
        else:
            label = {"warmup": "热身", "main": "主体", "cooldown": "放松", "recovery": "恢复"}.get(
                str(step.get("role") or ""), str(step.get("role") or "训练"),
            )
            parts.append(f"{label} {_dose_text(step.get('dose') or {})}")
    return " → ".join(parts)


def ensure_training_prescription(
    workout: dict[str, Any], *, pace_reference_sec_per_km: float | None = None,
    synthesize_steps: bool = False,
) -> dict[str, Any]:
    """补全可展示、可比较的训练处方，同时兼容历史方案。

    课程名称可以自由变化；这里只补全稳定的训练块、完成标准和强度数据可用性。
    """
    item = copy.deepcopy(workout)
    if str(item.get("type") or "").lower() == "rest":
        item.pop("training_prescription", None)
        return item
    reference_pace = _personal_pace_reference(pace_reference_sec_per_km)
    duration = _positive_int(item.get("duration_minutes"), 40)
    distance = item.get("distance_km")
    try:
        distance_value = round(float(distance), 1) if distance not in (None, "") else None
    except (TypeError, ValueError):
        distance_value = None
    if not item.get("duration_minutes") and distance_value and reference_pace:
        # 只有距离没有时长的课，按个人配速参考推算时长，避免默认 40 分钟
        # 把轻松跑大距离误判成隐含配速过快而触发强度-距离矛盾降级。
        duration = max(20, round(distance_value * (reference_pace / 60)))
    raw = item.get("training_prescription")
    prescription = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    compact_intent = item.get("intensity_intent")
    compact_intent = compact_intent if isinstance(compact_intent, dict) else {}
    stimuli = (
        prescription.get("stimuli")
        or item.get("stimuli")
        or compact_intent.get("stimuli")
        or _default_stimuli(item)
    )
    if not isinstance(stimuli, list):
        stimuli = [str(stimuli)] if stimuli else _default_stimuli(item)
    try:
        intensity_zone = int(
            prescription.get("intensity_zone")
            or item.get("intensity_zone")
            or compact_intent.get("zone")
        )
    except (TypeError, ValueError):
        intensity_zone = _default_intensity_zone(stimuli, item)
    if intensity_zone not in {1, 2, 3, 4, 5}:
        intensity_zone = _default_intensity_zone(stimuli, item)
    primary = str(
        prescription.get("primary_completion")
        or item.get("primary_completion")
        or ("distance" if distance_value else "time")
    )
    if primary not in {"distance", "time"}:
        primary = "distance" if distance_value else "time"
    if synthesize_steps and not prescription.get("steps"):
        steps, step_completion = build_workout_steps_v2(
            item,
            duration_minutes=duration,
            intensity_zone=intensity_zone,
            stimuli=[str(stimulus) for stimulus in stimuli],
        )
        prescription.update({
            "structure_version": 2,
            "steps": steps,
            "completion_criteria": step_completion,
        })
    steps = prescription.get("steps")
    if prescription.get("structure_version") == 2 and isinstance(steps, list) and steps:
        blocks = project_steps_to_blocks(steps)
    else:
        blocks = prescription.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        main_minutes = max(10, duration - 20)
        title = str(item.get("title") or "")
        if any(token in title for token in ("间歇", "interval", "变速", "法特莱克", "fartlek")):
            blocks = [
                {"role": "warmup", "duration_minutes": 15, "instruction": "轻松跑并完成动态活动"},
                {"role": "main", "duration_minutes": max(12, duration - 25), "instruction": "按课程工作段与恢复段交替；工作段始终留有余量"},
                {"role": "cooldown", "duration_minutes": 10, "instruction": "慢跑或步行放松"},
            ]
        else:
            blocks = [
                {"role": "warmup", "duration_minutes": 10, "instruction": "轻松跑并完成动态活动"},
                {"role": "main", "duration_minutes": main_minutes, "instruction": "以可对话强度连续跑；热身和放松均计入本次总距离/时长，不要求主体单独完成全程目标"},
                {"role": "cooldown", "duration_minutes": 10, "instruction": "慢跑或步行放松"},
            ]
    targets = prescription.get("targets") if isinstance(prescription.get("targets"), dict) else {}
    feel = targets.get("feel") if isinstance(targets.get("feel"), dict) else {}
    if not feel:
        feel = _feel_for_zone(intensity_zone)
    def unavailable_target(name: str) -> dict[str, Any]:
        value = targets.get(name)
        if isinstance(value, dict) and value.get("status") == "available" and value.get("range"):
            return {"status": "available", "range": str(value["range"]), "basis": str(value.get("basis") or "个人训练事实")}
        return {"status": "unavailable", "range": None, "basis": "缺少可靠个人阈值或近期同类训练事实"}
    implied_pace = (duration * 60 / distance_value) if duration and distance_value else None
    easy_session = intensity_zone in {1, 2} or str(item.get("type") or "").lower() in {"easy", "recovery"}
    stored_guard = prescription.get("pacing_guard")
    guard: dict[str, Any] = {
        "status": "not_applicable",
        "implied_pace_sec_per_km": round(implied_pace) if implied_pace else None,
        "reference_pace_sec_per_km": round(reference_pace) if reference_pace else None,
        "message": None,
    }
    if implied_pace and easy_session:
        if reference_pace and implied_pace < reference_pace * 0.92:
            guard.update({
                "status": "requires_review",
                "message": (
                    f"原距离与时长组合需要约 {_format_pace(implied_pace)}，"
                    f"快于近期跑步配速参照 {_format_pace(reference_pace)}；"
                    "不能把两者同时作为轻松跑的硬目标。"
                ),
            })
        elif reference_pace:
            guard["status"] = "consistent"
        else:
            guard.update({
                "status": "unverified",
                "message": "缺少近期个人配速参照；距离和时长不能同时作为轻松跑的硬目标。",
            })
    if (
        reference_pace is None
        and isinstance(stored_guard, dict)
        and stored_guard.get("status") == "requires_review"
    ):
        guard = copy.deepcopy(stored_guard)
    completion = prescription.get("completion_criteria")
    is_v2 = prescription.get("structure_version") == 2 and isinstance(steps, list) and bool(steps)
    if is_v2:
        normalized_completion, _ = _normalize_v2_completion_criteria(
            completion,
            steps=steps,
            duration_minutes=duration,
            distance_km=distance_value,
            primary_completion=primary,
        )
    elif not isinstance(completion, list) or not completion:
        target_label = f"{distance_value:g} km" if primary == "distance" and distance_value else f"{duration} 分钟"
        normalized_completion = [
            f"优先完成 {target_label}，不为凑距离额外加量",
            "主体强度始终以可控为上限；不适时先降级而不是硬撑",
        ]
    else:
        normalized_completion = [str(value) for value in completion if value]
    if guard["status"] == "requires_review":
        review_rule = (
            f"本次先按 {duration} 分钟可对话强度完成，距离只记录结果，"
            f"不同时追逐 {distance_value:g} km"
        )
        if is_v2:
            normalized_completion["quality_rule"] = review_rule
        else:
            normalized_completion = [
                review_rule,
                "请在下一次调整中修正距离、时长或个人配速依据后再恢复双目标表达",
            ]
    adjustments = prescription.get("adjustment_rules")
    if not isinstance(adjustments, list) or not adjustments:
        adjustments = [str(value) for value in item.get("adjustment_triggers") or [] if value]
        if not adjustments:
            adjustments = ["明显疲劳时缩短主体；出现疼痛时停止跑步并反馈"]
    method_basis = prescription.get("method_basis") if isinstance(prescription.get("method_basis"), dict) else {}
    prescription.update({
        "primary_completion": primary,
        "intensity_zone": intensity_zone,
        "stimuli": [str(stimulus) for stimulus in stimuli],
        "blocks": blocks,
        "targets": {
            "pace": unavailable_target("pace"),
            "heart_rate": unavailable_target("heart_rate"),
            "feel": feel,
        },
        "completion_criteria": normalized_completion,
        "adjustment_rules": [str(value) for value in adjustments if value],
        "pacing_guard": guard,
        "method_basis": {
            "principle": str(method_basis.get("principle") or "先完成当前阶段所需刺激，再依据恢复决定是否进阶"),
            "stage_relation": str(method_basis.get("stage_relation") or "服务本周训练重点，不单独决定阶段推进"),
        },
    })
    if prescription.get("structure_version") == 2 and isinstance(steps, list):
        prescription["steps"] = steps
    item["training_prescription"] = prescription
    return item


class TrainingSchemeValidationError(ValueError):
    """方案候选违反确定性训练或数据合同。"""


class TrainingSchemeCandidateUnavailable(RuntimeError):
    """AI 候选不可用；不得用另一套完整方案静默兜底。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "training_framework",
        failure_type: str = "provider_or_schema",
        retryable: bool = True,
        completed: list[str] | None = None,
        framework_id: str | None = None,
        diagnostic_reason: str | None = None,
    ):
        super().__init__(message)
        self.stage = stage
        self.failure_type = failure_type
        self.retryable = retryable
        self.completed = list(completed or [])
        self.framework_id = framework_id
        self.diagnostic_reason = diagnostic_reason


def _validate_workout_steps_v2(
    prescription: dict[str, Any],
    *,
    course_duration_minutes: int,
) -> None:
    if prescription.get("structure_version") != 2:
        raise TrainingSchemeValidationError("新方案课程必须使用 Workout Steps v2")
    steps = prescription.get("steps")
    if not isinstance(steps, list) or not steps:
        raise TrainingSchemeValidationError("Workout Steps v2 缺少执行步骤")
    seen_ids: set[str] = set()
    has_repeat = False
    has_high_intensity = False
    all_duration_steps = True

    def validate_siblings(items: list[Any], path: str) -> None:
        nonlocal has_repeat, has_high_intensity, all_duration_steps
        orders: set[int] = set()
        for raw_step in items:
            if not isinstance(raw_step, dict):
                raise TrainingSchemeValidationError("Workout Step 必须是对象")
            step_id = str(raw_step.get("step_id") or "").strip()
            if not step_id or step_id in seen_ids:
                raise TrainingSchemeValidationError("Workout Step ID 缺失或重复")
            seen_ids.add(step_id)
            try:
                order = int(raw_step.get("order"))
            except (TypeError, ValueError) as exc:
                raise TrainingSchemeValidationError("Workout Step 顺序无效") from exc
            if order <= 0 or order in orders:
                raise TrainingSchemeValidationError("Workout Step 同级顺序缺失或重复")
            orders.add(order)
            kind = str(raw_step.get("kind") or "")
            if kind == "repeat":
                has_repeat = True
                try:
                    repeat_count = int(raw_step.get("repeat_count"))
                except (TypeError, ValueError) as exc:
                    raise TrainingSchemeValidationError("重复组数必须是正整数") from exc
                if not 1 <= repeat_count <= 30:
                    raise TrainingSchemeValidationError("重复组数必须在 1 到 30 之间")
                children = raw_step.get("children")
                if not isinstance(children, list) or not children:
                    raise TrainingSchemeValidationError("重复步骤缺少工作和恢复子步骤")
                roles = {str(item.get("role") or "") for item in children if isinstance(item, dict)}
                if not {"work", "recovery"}.issubset(roles):
                    raise TrainingSchemeValidationError("重复步骤必须同时包含工作段和恢复段")
                validate_siblings(children, f"{path}/{step_id}")
                continue
            if kind not in {"work", "recovery"}:
                raise TrainingSchemeValidationError("Workout Step kind 无效")
            dose = raw_step.get("dose")
            if not isinstance(dose, dict):
                raise TrainingSchemeValidationError("Workout Step 缺少剂量")
            metric = str(dose.get("metric") or "")
            unit = str(dose.get("unit") or "")
            try:
                value = float(dose.get("value"))
            except (TypeError, ValueError) as exc:
                raise TrainingSchemeValidationError("Workout Step 剂量无效") from exc
            if value <= 0 or metric not in {"duration", "distance"}:
                raise TrainingSchemeValidationError("Workout Step 剂量必须为正数时长或距离")
            if metric == "duration" and unit not in {"minute", "second"}:
                raise TrainingSchemeValidationError("时长步骤单位必须是 minute 或 second")
            if metric == "distance" and unit not in {"km", "meter"}:
                raise TrainingSchemeValidationError("距离步骤单位必须是 km 或 meter")
            all_duration_steps = all_duration_steps and metric == "duration"
            intent = raw_step.get("intensity_intent")
            if not isinstance(intent, dict) or not str(intent.get("fallback_feel") or "").strip():
                raise TrainingSchemeValidationError("Workout Step 缺少强度意图或体感降级")
            try:
                zone = int(intent.get("zone"))
            except (TypeError, ValueError) as exc:
                raise TrainingSchemeValidationError("Workout Step 缺少 Z1–Z5 强度") from exc
            if zone not in {1, 2, 3, 4, 5}:
                raise TrainingSchemeValidationError("Workout Step 强度必须在 Z1–Z5")
            has_high_intensity = has_high_intensity or zone >= 4
            duration = _step_duration_minutes(raw_step)
            if zone == 5 and duration > 15:
                raise TrainingSchemeValidationError("Z5 工作段时长缺少安全边界")
            if zone == 4 and duration > 40:
                raise TrainingSchemeValidationError("Z4 工作段时长缺少安全边界")
            transition = raw_step.get("transition")
            if not isinstance(transition, dict) or not str(transition.get("type") or ""):
                raise TrainingSchemeValidationError("Workout Step 缺少转场条件")

    validate_siblings(steps, "steps")
    if has_high_intensity and any(
        int((step.get("intensity_intent") or {}).get("zone") or 0) == 5
        for step in iter_leaf_workout_steps(steps)
    ) and not has_repeat:
        raise TrainingSchemeValidationError("Z5 课程必须明确重复组和恢复段")
    total_duration = sum(_step_duration_minutes(step) for step in steps)
    if all_duration_steps and course_duration_minutes > 0:
        tolerance = max(2.0, course_duration_minutes * 0.05)
        if abs(total_duration - course_duration_minutes) > tolerance:
            raise TrainingSchemeValidationError("Workout Steps 合计时长与课程总时长不一致")
    completion = prescription.get("completion_criteria")
    if not isinstance(completion, dict):
        raise TrainingSchemeValidationError("Workout Steps v2 缺少结构化完成标准")
    classifications = completion.get("classification")
    if not isinstance(classifications, list) or not {
        "completed", "partially_completed", "stopped",
    }.issubset({str(item) for item in classifications}):
        raise TrainingSchemeValidationError("Workout Steps v2 完成分类不完整")


class PlanningFactPackBuilder:
    """把目标、固定周训练事实与约束归一化为方案 Skill 的唯一输入。

    动态当前周不进入草稿的周量基线；它只通过恢复/执行反馈影响审阅。
    """

    @staticmethod
    def build(
        *,
        goal: dict[str, Any],
        baseline: dict[str, Any],
        constraints: dict[str, Any],
        athlete_profile: dict[str, Any] | None = None,
        recovery_snapshot: dict[str, Any] | None = None,
        short_term_training: dict[str, Any] | None = None,
        training_summary: dict[str, Any] | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        as_of = as_of or date.today()
        target_date = None
        if goal.get("target_date"):
            try:
                target_date = date.fromisoformat(str(goal["target_date"]))
            except ValueError:
                target_date = None
        days_remaining = (target_date - as_of).days if target_date else None
        weeks_remaining = max(0, (days_remaining + 6) // 7) if days_remaining is not None else None
        coverage = str(baseline.get("coverage") or "unknown")
        uncertainties: list[str] = []
        if coverage != "sufficient":
            uncertainties.append("活动覆盖不完整")
        if not target_date:
            uncertainties.append("缺少有效目标赛事日期")
        if not goal.get("target_time"):
            uncertainties.append("未设置目标成绩，将按完赛或能力提升路线规划")
        if not (athlete_profile or {}).get("personal_bests"):
            uncertainties.append("缺少结构化近期成绩或个人最佳")
        if not recovery_snapshot:
            uncertainties.append("缺少当前恢复快照")

        weekly_volume = {
            "reported_weekly_mileage": baseline.get("reported_weekly_mileage"),
            "previous_week_km": float(
                baseline.get("previous_week_km")
                or baseline.get("average_weekly_km")
                or 0
            ),
            "average_weekly_km": float(baseline.get("average_weekly_km") or 0),
            "observed_28d_km": float(baseline.get("distance_km") or 0),
            "reference_window_kind": str(
                baseline.get("reference_window_kind")
                or "previous_completed_natural_week"
            ),
            "reference_window_start": baseline.get("reference_window_start"),
            "reference_window_end": baseline.get("reference_window_end"),
        }
        return {
            "coaching_mode": "race_preparation" if target_date else "continuous_running",
            "as_of": str(as_of),
            "goal": copy.deepcopy(goal),
            "race_timeline": {
                "target_date": str(target_date) if target_date else None,
                "days_remaining": days_remaining,
                "weeks_remaining": weeks_remaining,
            },
            "activity_coverage": {
                "status": coverage,
                "window_days": int(baseline.get("window_days") or 28),
                "activity_count": int(baseline.get("activity_count") or 0),
                "reference_window_kind": weekly_volume["reference_window_kind"],
                "reference_window_start": weekly_volume["reference_window_start"],
                "reference_window_end": weekly_volume["reference_window_end"],
            },
            "athlete_baseline": {
                "weekly_volume": weekly_volume,
                "frequency": {"activities_reference_week": int(baseline.get("activity_count") or 0)},
                "long_run": {"longest_reference_week_km": float(baseline.get("longest_distance_km") or 0)},
                "quality_sessions": copy.deepcopy(baseline.get("quality_sessions") or []),
                "recent_performances": copy.deepcopy((athlete_profile or {}).get("personal_bests") or []),
                "training_consistency": copy.deepcopy(baseline.get("training_consistency") or {}),
            },
            "athlete_profile": copy.deepcopy(athlete_profile or {}),
            "recovery_snapshot": copy.deepcopy(recovery_snapshot or {}),
            # 只用于课程前后衔接，不参与上一完整自然周的周量基线。
            "short_term_training": copy.deepcopy(short_term_training or {}),
            # 草稿可读取完整历史周聚合与最近两日摘要；不读取日报自然语言。
            "training_summary": copy.deepcopy(training_summary or {}),
            # 用户本次生成前填写的短文本，只是未验证自述；不得覆盖确定性事实、
            # 目标或安全底线，也不应被当作长期能力字段。
            "constraints": copy.deepcopy(constraints),
            "uncertainties": uncertainties,
        }


class TrainingLoadEnvelope:
    """按上一完整自然周基线计算起始容量和安全边界。"""

    @staticmethod
    def calculate(facts: dict[str, Any]) -> dict[str, Any]:
        volume = facts["athlete_baseline"]["weekly_volume"]
        candidates = [
            volume.get("reported_weekly_mileage"),
            volume.get("previous_week_km"),
            volume.get("average_weekly_km"),
        ]
        base = next((float(item) for item in candidates if item not in (None, "") and float(item) > 0), 0.0)
        if base <= 0:
            # 没有上一完整自然周的可靠基线时，不用动态窗口反推周量。
            base = 20.0
        constraints = facts.get("constraints") or {}
        available_days = constraints.get("available_days") or []
        max_minutes = int(constraints.get("max_session_minutes") or 90)
        pace_reference = _personal_pace_reference(
            (facts.get("athlete_profile") or {}).get("recent_running_pace_sec_per_km"),
        )
        # 时间约束不能借用统一 5 分/km 换算。只有近期个人跑步事实足够时，才把
        # 可训练时间折算成距离容量；否则近期观察跑量是唯一可量化容量边界。
        capacity_km = (
            round(len(available_days) * max_minutes * 60 / pace_reference, 1)
            if pace_reference else round(base, 1)
        )
        initial_target = round(min(base, capacity_km), 1)
        return {
            "observed_weekly_km": round(base, 1),
            "initial_target_km": max(1.0, initial_target),
            "capacity_km": max(1.0, capacity_km),
            "max_weekly_growth_ratio": 0.10,
            "max_long_run_ratio": 0.38,
            "min_hard_session_spacing_days": 2,
            "recovery_week_interval": 4,
            "minimum_taper_weeks": 2,
            "pace_reference_sec_per_km": round(pace_reference) if pace_reference else None,
        }


class TrainingSchemeCandidateNormalizer:
    """延展模型候选并补全可执行处方，不改写 AI 的训练质量判断。"""

    @classmethod
    def normalize(
        cls,
        candidate: dict[str, Any],
        facts: dict[str, Any],
        envelope: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = copy.deepcopy(candidate)
        pace_reference = _personal_pace_reference(
            (facts.get("athlete_profile") or {}).get("recent_running_pace_sec_per_km"),
        )
        adjustments: list[str] = []
        adjusted_periodization = False
        cls._expand_ai_horizon(result, envelope, adjustments)
        cls._apply_short_term_scheduling_guard(result, facts, adjustments)
        cls._record_periodization_order_review(result)
        adjusted_weeks: list[int] = []
        for fallback_week, week in enumerate(
            result.get("first_four_weeks") or [], start=1,
        ):
            if not isinstance(week, dict):
                continue
            try:
                proposed_target = float(week.get("target_km") or 0)
            except (TypeError, ValueError):
                continue
            target = round(proposed_target, 1)
            week["target_km"] = target
            workouts = week.get("workouts")
            if not isinstance(workouts, list) or not workouts:
                continue
            for workout in workouts:
                raw_prescription = workout.get("training_prescription")
                raw_steps = (
                    raw_prescription.get("steps")
                    if isinstance(raw_prescription, dict)
                    else None
                )
                raw_completion = (
                    raw_prescription.get("completion_criteria")
                    if isinstance(raw_prescription, dict)
                    else None
                )
                completion_needs_normalization = bool(
                    isinstance(raw_prescription, dict)
                    and raw_prescription.get("structure_version") == 2
                    and isinstance(raw_steps, list)
                    and raw_steps
                    and not (
                        isinstance(raw_completion, dict)
                        and isinstance(raw_completion.get("classification"), list)
                        and {
                            "completed", "partially_completed", "stopped",
                        }.issubset({str(item) for item in raw_completion["classification"]})
                    )
                )
                workout.update(ensure_training_prescription(
                    workout, pace_reference_sec_per_km=pace_reference,
                    synthesize_steps=True,
                ))
                if completion_needs_normalization:
                    adjustments.append(
                        f"第 {week.get('week') or fallback_week} 周「{workout.get('title') or '训练课程'}」"
                        "已确定性补齐完成标准（completion_criteria 已补齐）"
                    )

        normalized_weeks = {
            int(week.get("week") or 0): week
            for week in result.get("first_four_weeks") or []
            if isinstance(week, dict)
        }
        for item in result.get("load_progression") or []:
            if not isinstance(item, dict):
                continue
            normalized_week = normalized_weeks.get(int(item.get("week") or 0))
            if normalized_week:
                item["target_km"] = normalized_week.get("target_km")
                item["target_load"] = normalized_week.get("target_load")
                item["recovery_week"] = bool(normalized_week.get("recovery_week"))

        reconciliation = TrainingPlanReconciler.reconcile(result, facts)
        result["reconciliation"] = reconciliation
        adjustments.extend(reconciliation.get("adjustments") or [])
        normalized_weeks = {
            int(week.get("week") or 0): week
            for week in result.get("first_four_weeks") or []
            if isinstance(week, dict)
        }
        for item in result.get("load_progression") or []:
            if not isinstance(item, dict):
                continue
            normalized_week = normalized_weeks.get(int(item.get("week") or 0))
            if normalized_week:
                item["target_km"] = normalized_week.get("target_km")
                item["target_load"] = normalized_week.get("target_load")

        return result, {
            "stage": "deterministic_normalization",
            "status": "completed",
            "adjusted_periodization": adjusted_periodization,
            "adjusted_weeks": adjusted_weeks,
            "adjustments": adjustments,
            "reconciliation": reconciliation,
        }

    @staticmethod
    def _apply_short_term_scheduling_guard(
        result: dict[str, Any], facts: dict[str, Any], adjustments: list[str],
    ) -> None:
        """避免近期长跑/质量课后紧接高强度课程。"""
        short_term = facts.get("short_term_training") or {}
        blocked_dates: set[date] = set()
        for activity in short_term.get("activities") or []:
            if not isinstance(activity, dict) or not (
                activity.get("is_long") or activity.get("is_quality")
            ):
                continue
            try:
                activity_date = date.fromisoformat(str(activity.get("date"))[:10])
            except (TypeError, ValueError):
                continue
            blocked_dates.add(activity_date + timedelta(days=1))
        if not blocked_dates:
            return
        try:
            as_of = date.fromisoformat(str(facts.get("as_of"))[:10])
        except (TypeError, ValueError):
            as_of = date.today()
        monday = as_of - timedelta(days=as_of.weekday())
        for fallback_week, raw_week in enumerate(result.get("first_four_weeks") or [], start=1):
            if not isinstance(raw_week, dict):
                continue
            try:
                week_number = int(raw_week.get("week") or fallback_week)
            except (TypeError, ValueError):
                week_number = fallback_week
            week_start = monday + timedelta(days=(week_number - 1) * 7)
            for workout in raw_week.get("workouts") or []:
                if not isinstance(workout, dict) or str(workout.get("type") or "") == "rest":
                    continue
                try:
                    weekday = int(workout.get("weekday", -1))
                except (TypeError, ValueError):
                    continue
                if not 0 <= weekday <= 6:
                    continue
                workout_date = week_start + timedelta(days=weekday)
                if workout_date < as_of or workout_date not in blocked_dates:
                    continue
                prescription = workout.get("training_prescription")
                try:
                    zone = int(
                        (prescription or {}).get("intensity_zone")
                        or workout.get("intensity_zone")
                        or (workout.get("intensity_intent") or {}).get("zone")
                        or 0
                    )
                except (TypeError, ValueError):
                    zone = 0
                is_quality = str(workout.get("type") or "") in {
                    "quality", "interval", "tempo", "race_pace",
                } or zone >= 4
                if not is_quality:
                    continue
                original_title = str(workout.get("title") or "强度训练")
                workout.update({
                    "title": "衔接恢复跑",
                    "type": "easy",
                    "purpose": "承接近期高负荷训练，先恢复再安排强度",
                    "intensity": "能完整对话，结束仍有余量",
                    "stimuli": ["aerobic_endurance", "recovery"],
                    "intensity_intent": {
                        "zone": 2,
                        "preferred_metric": "feel",
                        "fallback_feel": "能完整对话，结束仍有余量",
                    },
                    "is_key": False,
                })
                workout.pop("training_prescription", None)
                adjustments.append(
                    f"{workout_date} 原「{original_title}」改为衔接恢复跑：近期长跑或质量课后次日不安排强度"
                )

    @staticmethod
    def _record_periodization_order_review(result: dict[str, Any]) -> None:
        """对明显倒置的阶段顺序给出非阻断审阅，不静默重排模型方案。"""
        ranks: list[int] = []
        for phase in result.get("periodization") or []:
            name = str((phase or {}).get("name") or "").lower()
            if any(token in name for token in ("减量", "taper", "恢复周")):
                rank = 4
            elif any(token in name for token in ("高峰", "峰值", "peak")):
                rank = 3
            elif any(token in name for token in ("专项", "强度", "提升", "threshold", "build")):
                rank = 2
            elif any(token in name for token in ("基础", "适应", "准备", "base")):
                rank = 1
            else:
                rank = 0
            ranks.append(rank)
        known = [rank for rank in ranks if rank > 0]
        if len(known) < 2 or all(left <= right for left, right in zip(known, known[1:])):
            return
        review = result.get("review")
        if not isinstance(review, dict):
            review = {"status": "attention", "items": [], "safety_hold": None}
            result["review"] = review
        items = review.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            review["items"] = items
        if not any(str(item.get("code") or "") == "PERIODIZATION_ORDER" for item in items if isinstance(item, dict)):
            items.append({
                "code": "PERIODIZATION_ORDER",
                "severity": "attention",
                "reason": "训练阶段顺序出现倒置，基础/适应应先于专项强度，高峰后才进入减量",
                "suggestion": "请重新生成或确认前检查阶段时间线",
            })
        review["status"] = "attention"

    @staticmethod
    def _expand_ai_horizon(
        result: dict[str, Any],
        envelope: dict[str, Any],
        adjustments: list[str],
    ) -> None:
        """把 AI 当前周/下一周骨架延展为现有读模型所需的首四周。"""
        weeks = result.get("first_four_weeks")
        if not isinstance(weeks, list) or len(weeks) == 0:
            weeks = result.get("first_two_weeks")
        if not isinstance(weeks, list) or len(weeks) != 2:
            return
        first, second = copy.deepcopy(weeks[0]), copy.deepcopy(weeks[1])
        try:
            second_target = float(second.get("target_km") or 0)
        except (TypeError, ValueError):
            second_target = float(envelope["initial_target_km"])
        third_target = round(second_target, 1)
        third = copy.deepcopy(second)
        third.update({
            "week": 3,
            "target_km": third_target,
            "recovery_week": False,
            "focus": str(second.get("focus") or "") or "延续当前训练重点",
        })
        fourth = copy.deepcopy(third)
        fourth.update({
            "week": 4,
            "target_km": third_target,
            "recovery_week": bool(third.get("recovery_week")),
            "focus": str(third.get("focus") or "延续当前训练重点"),
        })
        first["week"] = 1
        second["week"] = 2
        result["first_four_weeks"] = [first, second, third, fourth]
        adjustments.append("AI 仅生成当前周和下一周，后两周已按第二周骨架确定性延展")


class TrainingPlanReconciler:
    """让周目标与课程累计结果自洽；不判断训练是否适合当前运动者。"""

    @staticmethod
    def _workout_load(workout: dict[str, Any]) -> float:
        prescription = workout.get("training_prescription") or {}
        try:
            duration = float(workout.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            zone = int(prescription.get("intensity_zone") or workout.get("intensity_zone") or 1)
        except (TypeError, ValueError):
            zone = 1
        # 计划负荷只是内部一致性单位，不是 ACWR，也不代表 Provider 的生理负荷。
        return round(max(0.0, duration) * max(1, min(5, zone)), 1)

    @classmethod
    def reconcile(cls, candidate: dict[str, Any], facts: dict[str, Any] | None = None) -> dict[str, Any]:
        adjustments: list[str] = []
        weeks: list[dict[str, Any]] = []
        pace_reference = _personal_pace_reference(
            (facts or {}).get("athlete_profile", {}).get("recent_running_pace_sec_per_km"),
        )
        for raw_week in candidate.get("first_four_weeks") or []:
            if not isinstance(raw_week, dict):
                continue
            workouts = [
                workout for workout in raw_week.get("workouts") or []
                if isinstance(workout, dict) and str(workout.get("type") or "") != "rest"
            ]
            try:
                target_km = float(raw_week.get("target_km") or 0)
            except (TypeError, ValueError):
                target_km = 0.0
            week_adjustments: list[str] = []
            distances = []
            for workout in workouts:
                try:
                    distance = max(0.0, float(workout.get("distance_km") or 0))
                except (TypeError, ValueError):
                    distance = 0.0
                if not distance and pace_reference:
                    estimated = _estimate_duration_distance(workout, pace_reference)
                    if estimated:
                        workout["distance_km"] = round(estimated, 1)
                        workout["distance_estimated"] = True
                        distance = round(estimated, 1)
                        week_adjustments.append(
                            f"定时跑 {int(workout.get('duration_minutes') or 0)} 分钟按个人配速估算约 "
                            f"{distance:g} km"
                        )
                distances.append(max(0.0, distance))
            current_km = sum(distances)
            if target_km > 0 and current_km > 0 and abs(current_km - target_km) > 0.05:
                factor = target_km / current_km
                scaled = [round(distance * factor, 1) for distance in distances]
                rounding_delta = round(target_km - sum(scaled), 1)
                if scaled and abs(rounding_delta) >= 0.1:
                    scaled[scaled.index(max(scaled))] = round(
                        scaled[scaled.index(max(scaled))] + rounding_delta, 1,
                    )
                for workout, distance in zip(workouts, scaled):
                    workout["distance_km"] = max(0.0, distance)
                current_km = round(sum(scaled), 1)
                week_adjustments.append("课程累计跑量已对齐周规划目标")
            total_load = round(sum(cls._workout_load(workout) for workout in workouts), 1)
            requested_load = raw_week.get("target_load")
            try:
                requested_load_value = float(requested_load) if requested_load not in (None, "") else total_load
            except (TypeError, ValueError):
                requested_load_value = total_load
            if abs(requested_load_value - total_load) > 0.1:
                week_adjustments.append("周规划负荷已按课程累计负荷结算")
            raw_week["target_km"] = round(current_km, 1) if current_km > 0 else round(target_km, 1)
            raw_week["target_load"] = total_load
            for workout in workouts:
                workout["planned_load"] = cls._workout_load(workout)
            raw_week["reconciliation"] = {
                "status": "adjusted" if week_adjustments else "consistent",
                "planned_km": raw_week["target_km"],
                "course_km": round(sum(float(item.get("distance_km") or 0) for item in workouts), 1),
                "planned_load": total_load,
                "course_load": total_load,
                "adjustments": week_adjustments,
            }
            if week_adjustments:
                adjustments.extend([
                    f"第 {raw_week.get('week') or '?'} 周：{item}" for item in week_adjustments
                ])
            weeks.append(raw_week["reconciliation"])
        return {"status": "adjusted" if adjustments else "consistent", "weeks": weeks, "adjustments": adjustments}

class TrainingSchemeValidator:
    """只校验候选能否被系统执行；训练质量由同次 AI 审阅给出建议。"""

    @classmethod
    def validate(
        cls,
        candidate: dict[str, Any],
        facts: dict[str, Any],
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(candidate.get("periodization"), list) or not candidate["periodization"]:
            raise TrainingSchemeValidationError("方案缺少周期化阶段")
        weeks = candidate.get("first_four_weeks")
        if not isinstance(weeks, list) or len(weeks) != 4:
            raise TrainingSchemeValidationError("方案必须提供首四周")
        for index, week in enumerate(weeks, start=1):
            target = float(week.get("target_km") or 0)
            if target <= 0:
                raise TrainingSchemeValidationError(f"第 {index} 周跑量无效")
            workouts = week.get("workouts")
            if not isinstance(workouts, list) or not workouts:
                raise TrainingSchemeValidationError(f"第 {index} 周缺少课程")
            for workout in workouts:
                try:
                    weekday = int(workout.get("weekday", -1))
                except (TypeError, ValueError) as exc:
                    raise TrainingSchemeValidationError("课程星期无效") from exc
                if not 0 <= weekday <= 6:
                    raise TrainingSchemeValidationError("课程星期必须在周一到周日")
                if str(workout.get("type") or "") != "rest":
                    prescription = workout.get("training_prescription")
                    if not isinstance(prescription, dict):
                        raise TrainingSchemeValidationError("课程缺少结构化训练处方")
                    targets = prescription.get("targets") or {}
                    feel = targets.get("feel") if isinstance(targets, dict) else None
                    if not isinstance(feel, dict) or not str(feel.get("label") or "").strip():
                        raise TrainingSchemeValidationError("课程缺少通俗强度说明")
                    try:
                        intensity_zone = int(prescription.get("intensity_zone"))
                    except (TypeError, ValueError) as exc:
                        raise TrainingSchemeValidationError("课程缺少五级主体强度") from exc
                    if intensity_zone not in {1, 2, 3, 4, 5}:
                        raise TrainingSchemeValidationError("课程主体强度必须在 Z1–Z5")
                    _validate_workout_steps_v2(
                        prescription,
                        course_duration_minutes=int(workout.get("duration_minutes") or 0),
                    )
        # 周期、负荷、配速、恢复和训练间隔属于 AI review 的非阻断建议，
        # 不在这里重复硬失败。若 AI 未返回 review，保留兼容的空审阅状态。
        review = candidate.get("review")
        if not isinstance(review, dict):
            candidate["review"] = {"status": "not_provided", "items": [], "safety_hold": None}
        entry_review = candidate.get("entry_review")
        if not isinstance(entry_review, dict):
            entry_review = {
                "recommended_entry_phase": None,
                "decision": "hold",
                "confidence": 0.0,
                "evidence": [],
                "adjustments": [],
                "applied": False,
                "applied_adjustments": [],
                "handoff": {
                    "status": "review_pending",
                    "requires_user_confirmation": True,
                },
            }
        else:
            entry_review = copy.deepcopy(entry_review)
            if str(entry_review.get("decision") or "hold") not in {
                "hold", "advance", "adjust", "deload", "replan",
            }:
                entry_review["decision"] = "hold"
            try:
                entry_review["confidence"] = min(
                    1.0, max(0.0, float(entry_review.get("confidence") or 0)),
                )
            except (TypeError, ValueError):
                entry_review["confidence"] = 0.0
            applied = bool(entry_review.get("applied"))
            if entry_review.get("decision") == "replan" or (candidate.get("review") or {}).get("safety_hold"):
                applied = False
            entry_review["applied"] = applied
            entry_review["applied_adjustments"] = [
                str(item) for item in entry_review.get("applied_adjustments") or []
            ]
            entry_review["handoff"] = {
                "status": "applied_to_candidate" if applied else "review_pending",
                "requires_user_confirmation": True,
            }
        candidate["entry_review"] = entry_review
        return candidate


class ProfessionalSchemePlanner:
    """运行分阶段方案 Skill；真实候选失败时不生成第二套完整方案。"""

    def __init__(self, runner: CoachSkillRunner | None = None):
        self.runner = runner
        self._framework_cache: dict[str, dict[str, Any]] = {}
        self._schedule_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _cache_put(cache: dict[str, dict[str, Any]], key: str, value: dict[str, Any]) -> None:
        cache[key] = copy.deepcopy(value)
        while len(cache) > 32:
            cache.pop(next(iter(cache)))

    def plan(
        self, facts: dict[str, Any], envelope: dict[str, Any],
    ) -> dict[str, Any]:
        if self.runner is None:
            return self._fallback(facts, envelope, reason="AI 方案 Skill 不可用")
        # 旧的测试/扩展 runner 仍提供 draft-training-scheme 单入口；生产默认
        # CoachSkillRunner 走新的 framework -> near-term DAG。这样兼容旧集成，
        # 不把兼容路径当作生产成功率或第二套完整方案。
        if not isinstance(self.runner, CoachSkillRunner):
            return self._plan_legacy(facts, envelope)
        try:
            return self._plan_dag(facts, envelope)
        except TrainingSchemeCandidateUnavailable as exc:
            # 本地开发或未配置 Provider 时保留既有可用的确定性降级；真实
            # Provider/Schema/Validator 失败仍携带阶段信息交给上层展示。
            if "未配置 NEURUN_AI_API_KEY" in str(exc):
                return self._fallback(facts, envelope, reason="AI 方案 Skill 不可用")
            raise

    def _plan_legacy(
        self, facts: dict[str, Any], envelope: dict[str, Any],
    ) -> dict[str, Any]:
        context = CoachRunContext(
            request={"intent": "draft_training_scheme"},
            facts={
                "planning_fact_pack": facts,
                "training_load_envelope": envelope,
                "recovery_snapshot": facts.get("recovery_snapshot") or {},
            },
            uncertainties=tuple(facts.get("uncertainties") or []),
            policy={"coaching_mode": facts.get("coaching_mode")},
        )
        try:
            started = time.monotonic()
            candidate, final_context = self.runner.run(
                "draft-training-scheme", context,
            )
            logger.info(
                "training scheme timing: skill=draft-training-scheme phase=skill_complete elapsed=%.2fs",
                time.monotonic() - started,
            )
            candidate = copy.deepcopy(candidate)
            normalizer_started = time.monotonic()
            candidate, normalization_trace = TrainingSchemeCandidateNormalizer.normalize(
                candidate, facts, envelope,
            )
            logger.info(
                "training scheme timing: skill=draft-training-scheme phase=normalizer_complete elapsed=%.2fs",
                time.monotonic() - normalizer_started,
            )
            candidate["generation_mode"] = "skill"
            candidate["fallback_reason"] = None
            candidate["planning_trace"] = [*final_context.trace, normalization_trace]
            self._attach_result_metadata(candidate, normalization_trace)
            validator_started = time.monotonic()
            result = TrainingSchemeValidator.validate(candidate, facts, envelope)
            logger.info(
                "training scheme timing: skill=draft-training-scheme phase=validator_complete elapsed=%.2fs",
                time.monotonic() - validator_started,
            )
            return result
        except Exception as exc:
            logger.warning("专业方案 Skill 不可用: %s", exc)
            # 本地/未配置 Provider 时保持现有首次建方案可用；真实 Provider、
            # Schema 或 Validator 失败则交给上层返回 scheme_candidate_unavailable。
            if "未配置 NEURUN_AI_API_KEY" in str(exc):
                return self._fallback(facts, envelope, reason=str(exc))
            raise TrainingSchemeCandidateUnavailable(str(exc)) from exc

    @staticmethod
    def _compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """只保留框架/配速需要的能力字段，避免把档案正文送进 AI。"""
        if not isinstance(profile, dict):
            return {}
        result = {}
        for key in (
            "personal_info", "personal_bests", "recent_running_pace_sec_per_km",
            "current_readiness", "current_sustainable_capacity",
            "historical_proven_capacity", "entry_load_envelope",
        ):
            if key in profile and profile[key] not in (None, "", {}, []):
                result[key] = copy.deepcopy(profile[key])
        return result

    @staticmethod
    def _compact_recovery(snapshot: dict[str, Any]) -> dict[str, Any]:
        """保留恢复判断所需的数值，不复制整份日报 front matter。"""
        if not isinstance(snapshot, dict):
            return {}
        result = {}
        for key in ("date", "status", "score", "hrv", "resting_hr", "fatigue", "pain"):
            if key in snapshot and snapshot[key] not in (None, "", {}, []):
                result[key] = copy.deepcopy(snapshot[key])
        nested_fields = {
            "recovery": ("score", "status", "hrv", "resting_hr", "fatigue", "pain", "confidence"),
            "morning": ("status", "energy", "readiness", "hrv", "resting_hr", "sleep_score"),
            "sleep": ("duration_minutes", "duration", "score", "quality", "deep_minutes", "rem_minutes"),
            "training_load": ("load", "acute", "chronic", "acwr", "status", "risk"),
        }
        for group, fields in nested_fields.items():
            value = snapshot.get(group)
            if not isinstance(value, dict):
                continue
            compact = {
                key: copy.deepcopy(value[key])
                for key in fields
                if key in value and value[key] not in (None, "", {}, [])
            }
            if compact:
                result[group] = compact
        return result

    @staticmethod
    def _compact_recent_activity(activity: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(activity, dict):
            return {}
        result = {}
        for key in (
            "activity_id", "activity_date", "date", "start_time", "activity_name",
            "activity_type", "distance_meters", "duration_seconds", "training_load",
            "average_hr", "avg_heart_rate", "max_hr", "avg_pace_sec_per_km",
        ):
            if key in activity and activity[key] not in (None, "", {}, []):
                result[key] = copy.deepcopy(activity[key])
        analysis = activity.get("training_analysis")
        if isinstance(analysis, dict):
            result["training_analysis"] = {
                key: copy.deepcopy(analysis[key])
                for key in (
                    "primary_type", "display_name", "terrain", "confidence",
                    "specialties", "training_effect", "recovery_response",
                )
                if key in analysis and analysis[key] not in (None, "", {}, [])
            }
        return result

    @classmethod
    def _compact_short_term(cls, short_term: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(short_term, dict):
            return {}
        result = {
            key: copy.deepcopy(short_term[key])
            for key in ("status", "window_start", "window_end", "constraints")
            if key in short_term
        }
        result["activities"] = [
            item for item in (
                cls._compact_recent_activity(activity)
                for activity in short_term.get("activities") or []
            ) if item
        ]
        return result

    @classmethod
    def _compact_schedule_current_state(cls, current: dict[str, Any]) -> dict[str, Any]:
        """Keep only the near-term evidence needed to place the next workout.

        ``recent_days`` already represents the activity window used by the
        scheduler.  Repeating the same activities under ``short_term_training``
        consumes context without improving the decision.  Preserve a short,
        session-aware tail for quality/long-run spacing plus the recovery
        snapshot and window metadata for data-quality interpretation.
        """
        recent_days = current.get("recent_days") or []
        compact_days: list[dict[str, Any]] = []
        for raw_day in recent_days[-2:]:
            if not isinstance(raw_day, dict):
                continue
            day = {
                key: copy.deepcopy(raw_day[key])
                for key in (
                    "date", "status", "total_distance_km",
                    "total_duration_minutes", "primary_type", "training_load",
                    "gaps",
                )
                if key in raw_day
            }
            sessions = []
            for raw_session in raw_day.get("sessions") or []:
                if not isinstance(raw_session, dict):
                    continue
                session = {
                    key: copy.deepcopy(raw_session[key])
                    for key in (
                        "session_role", "distance_km", "duration_minutes",
                        "training_load", "metrics", "analysis",
                    )
                    if key in raw_session
                }
                if isinstance(raw_session.get("session_summary"), dict):
                    session["session_summary"] = copy.deepcopy(
                        raw_session["session_summary"]
                    )
                if session:
                    sessions.append(session)
            if sessions:
                day["sessions"] = sessions
            compact_days.append(day)

        short_term = current.get("short_term_training") or {}
        recent_window = {
            key: copy.deepcopy(short_term[key])
            for key in ("status", "window_start", "window_end", "constraints")
            if key in short_term
        }
        return {
            "recent_days": compact_days,
            "recent_training_window": recent_window,
            "recovery": copy.deepcopy(current.get("recovery") or {}),
            "uncertainties": list(current.get("uncertainties") or []),
        }

    @staticmethod
    def _compact_day(summary: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {}
        result = {
            key: copy.deepcopy(summary[key])
            for key in (
                "date", "data_as_of", "status", "finality", "total_distance_km",
                "total_duration_minutes", "primary_type", "training_load",
                "plan_context", "gaps", "summary_version",
            ) if key in summary
        }
        result["sessions"] = [
            {
                key: copy.deepcopy(session[key])
                for key in (
                    "activity_id", "name", "session_role", "distance_km",
                    "duration_minutes", "training_load", "metrics", "analysis",
                    "session_summary",
                ) if key in session
            }
            for session in summary.get("sessions") or []
            if isinstance(session, dict)
        ]
        result["observed_features"] = [
            {
                key: copy.deepcopy(feature[key])
                for key in ("feature_code", "label", "confidence", "evidence")
                if key in feature
            }
            for feature in (summary.get("observed_features") or [])[:8]
            if isinstance(feature, dict)
        ]
        return result

    @staticmethod
    def _dedup_gaps(gaps: Any) -> list[dict[str, Any]]:
        """按 (field, reason, affects) 去重缺失原因；gaps 语义上是一组事实缺口。

        同一缺口在完整自然周内按天聚合会重复出现（4 种原因 × N 天），
        去重只保留首个条目，不改变模型可读的信息量，但显著压缩 AI 载荷。
        """
        seen: set[tuple[str, str, str]] = set()
        result: list[dict[str, Any]] = []
        for gap in gaps or []:
            if not isinstance(gap, dict):
                continue
            key = (
                str(gap.get("field") or ""),
                str(gap.get("reason") or ""),
                str(gap.get("affects") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(copy.deepcopy(gap))
        return result

    @classmethod
    def _compact_reference_week(cls, week: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: copy.deepcopy(week[key])
            for key in (
                "window_start", "window_end", "day_count", "training_days",
                "running_distance_km", "running_duration_minutes",
                "primary_type_counts", "feature_counts", "data_quality",
            ) if key in week
        }
        if "gaps" in week:
            compact["gaps"] = cls._dedup_gaps(week.get("gaps"))
        return compact

    @classmethod
    def _compact_training_summary(cls, summary: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {}
        return {
            "schema_version": summary.get("schema_version"),
            "status": summary.get("status"),
            "reference_window": copy.deepcopy(summary.get("reference_window") or {}),
            "reference_weeks": [
                cls._compact_reference_week(week)
                for week in (summary.get("reference_weeks") or [])
                if isinstance(week, dict)
            ],
            "recent_days": [
                item for item in (
                    cls._compact_day(day)
                    for day in (summary.get("recent_days") or [])
                ) if item
            ],
        }

    @classmethod
    def _supporting_summaries(cls, facts: dict[str, Any]) -> dict[str, Any]:
        """把长事实压缩成可缓存的阶段输入，避免重复发送原始活动正文。"""
        baseline = facts.get("athlete_baseline") or {}
        volume = baseline.get("weekly_volume") or {}
        summary = facts.get("training_summary") or {}
        recent = facts.get("short_term_training") or {}
        recovery = facts.get("recovery_snapshot") or {}
        capability = {
            "reference_window": copy.deepcopy(summary.get("reference_window") or {}),
            "weekly_volume": copy.deepcopy(volume),
            "frequency": copy.deepcopy(baseline.get("frequency") or {}),
            "long_run": copy.deepcopy(baseline.get("long_run") or {}),
            "quality_sessions": copy.deepcopy(baseline.get("quality_sessions") or []),
            "training_consistency": copy.deepcopy(baseline.get("training_consistency") or {}),
            "reference_weeks": copy.deepcopy(cls._compact_training_summary(summary).get("reference_weeks") or []),
            "stable_interpretation": "长期能力由多个完整自然周判断，不由单周或 PB 直接替代",
        }
        current = {
            "recent_days": copy.deepcopy(cls._compact_training_summary(summary).get("recent_days") or []),
            "short_term_training": cls._compact_short_term(recent),
            "recovery": cls._compact_recovery(recovery),
            "uncertainties": list(facts.get("uncertainties") or []),
        }
        goal = facts.get("goal") or {}
        goal_demand = {
            "distance": goal.get("distance"),
            "target_time": goal.get("target_time"),
            "target_date": goal.get("target_date"),
            "weeks_remaining": (facts.get("race_timeline") or {}).get("weeks_remaining"),
            "demand_summary": "需要同时覆盖基础耐力、专项配速、长距离耐受与减量",
        }
        return {
            "athlete_capability_summary": capability,
            "athlete_current_state": current,
            "goal_demand_model": goal_demand,
            "athlete_profile": cls._compact_profile(facts.get("athlete_profile") or {}),
            "training_summary": cls._compact_training_summary(summary),
            "recovery_summary": cls._compact_recovery(recovery),
        }

    @staticmethod
    def _framework_to_candidate(framework: dict[str, Any]) -> dict[str, Any]:
        return {
            "feasibility": copy.deepcopy(framework.get("feasibility") or {}),
            "periodization": copy.deepcopy(framework.get("periodization") or []),
            "load_progression": copy.deepcopy(framework.get("load_progression") or []),
            "assumptions": copy.deepcopy(framework.get("assumptions") or []),
            "uncertainties": copy.deepcopy(framework.get("uncertainties") or []),
            "risk_flags": copy.deepcopy(framework.get("risk_flags") or []),
            "user_explanation": str(framework.get("user_explanation") or ""),
            "framework_summary": copy.deepcopy(framework.get("ability_summary") or {}),
            "goal_demand_summary": copy.deepcopy(framework.get("goal_demand_summary") or {}),
            "ability_gap": copy.deepcopy(framework.get("ability_gap") or []),
            "method_basis": copy.deepcopy(framework.get("method_basis") or []),
            "recommended_entry_phase": framework.get("recommended_entry_phase"),
            "entry_rationale": framework.get("entry_rationale"),
        }

    @staticmethod
    def _compact_schedule_for_audit(result: dict[str, Any]) -> dict[str, Any]:
        """课表紧凑摘要：只含审计所需的课程字段，控制审计调用 token。"""
        weeks = []
        for week in result.get("first_two_weeks") or result.get("first_four_weeks") or []:
            if not isinstance(week, dict):
                continue
            workouts = []
            for w in week.get("workouts") or []:
                if not isinstance(w, dict):
                    continue
                workouts.append({
                    "weekday": w.get("weekday"),
                    "type": w.get("type"),
                    "distance_km": w.get("distance_km"),
                    "duration_minutes": w.get("duration_minutes"),
                    "pace_intent": w.get("pace_intent"),
                    "is_key": bool(w.get("is_key")),
                })
            weeks.append({
                "week": week.get("week"),
                "target_km": week.get("target_km"),
                "workouts": workouts,
            })
        return {
            "periodization": [
                {"name": p.get("name"), "weeks": p.get("weeks")}
                for p in result.get("periodization") or []
                if isinstance(p, dict)
            ],
            "first_two_weeks": weeks[:2],
        }

    def _audit_scheme(
        self,
        facts: dict[str, Any],
        result: dict[str, Any],
        generation_id: str,
    ) -> dict[str, Any]:
        """独立 AI 审计草稿可执行性；调用失败时降级放行，不阻断草稿。"""
        audit_context = CoachRunContext(
            request={"intent": "audit_training_scheme", "generation_id": generation_id, "ai_call_index": 3},
            facts={
                "scheme_candidate": self._compact_schedule_for_audit(result),
                "audit_context": {
                    "goal": copy.deepcopy(facts.get("goal") or {}),
                    "constraints": copy.deepcopy(facts.get("constraints") or {}),
                    "pace_reference_sec_per_km": (
                        (facts.get("athlete_profile") or {}).get("recent_running_pace_sec_per_km")
                    ),
                    "supplement": copy.deepcopy(facts.get("supplement") or {}),
                    "longest_reference_week_km": (facts.get("baseline") or {}).get("longest_distance_km"),
                    "marathon_distance_km": 42.195,
                },
            },
            uncertainties=facts.get("uncertainties") or [],
            policy={"coaching_mode": facts.get("coaching_mode")},
        )
        try:
            started = time.monotonic()
            logger.info("AI draft execution: generation_id=%s step=5/5 audit_training_scheme", generation_id)
            raw, _ = self.runner.run("audit-training-scheme", audit_context)
            audit = validate_output("SchemeAudit", raw)
            logger.info(
                "training scheme audit: generation_id=%s verdict=%s elapsed=%.2fs",
                generation_id, audit.get("verdict"), time.monotonic() - started,
            )
            return {
                "status": "passed" if audit["verdict"] == "pass" else "failed",
                **audit,
            }
        except Exception as exc:
            logger.warning("training scheme audit unavailable: %s", str(exc)[:240])
            return {
                "status": "unavailable", "verdict": None,
                "summary": "可执行性审计暂不可用，草稿按确定性校验放行",
                "issues": [],
            }

    def _verify_audit_critical(
        self,
        facts: dict[str, Any],
        result: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        """确定性把关：① 主动补抓 AI 审计漏报的 critical；② 复核 AI 报的 critical（误判降级 warning）。

        确定性规则是审计的超集——AI 报的按确定性复核，AI 漏的由确定性规则补上。
        """
        issues = audit.get("issues") or []
        reviewed: list[dict[str, Any]] = []
        downgraded = 0
        for issue in issues:
            if issue.get("severity") != "critical":
                reviewed.append(issue)
                continue
            code = issue.get("code")
            verified = True
            if code == "long_run_exceeds_limit":
                verified = _confirm_long_run_exceed(result, facts)
            elif code == "weekday_session_too_long":
                verified = _confirm_weekday_too_long(result, facts)
            elif code == "quality_back_to_back":
                verified = _confirm_quality_back_to_back(result, facts)
            if verified:
                reviewed.append(issue)
            else:
                downgraded += 1
                reviewed.append({
                    **issue,
                    "severity": "warning",
                    "message": f"{issue.get('message')}（确定性复核未确认，降级为提示）",
                })
        # 确定性主动补抓（AI 可能漏报）
        deterministic_issues = self._deterministic_audit_issues(facts, result)
        existing_codes = {issue.get("code") for issue in reviewed}
        for issue in deterministic_issues:
            if issue["code"] not in existing_codes:
                reviewed.append(issue)
        if downgraded:
            logger.info(
                "scheme audit deterministic recheck: downgraded %s critical issue(s) to warning",
                downgraded,
            )
        return {
            "issues": reviewed,
            "summary": (
                f"确定性复核将 {downgraded} 条未确认的 critical 降级为提示。"
                if downgraded else audit.get("summary")
            ),
        }

    def _deterministic_audit_issues(
        self,
        facts: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """确定性规则独立审计课表，补抓 AI 可能漏报的可执行性问题。"""
        issues: list[dict[str, Any]] = []
        if _confirm_long_run_exceed(result, facts):
            issues.append({
                "code": "long_run_exceeds_limit", "severity": "critical",
                "message": "长距离超过确定性阈值（个人历史×1.15 或全马×0.75；基础期全马×0.5）",
                "recommendation": "收敛长距离距离",
            })
        if _confirm_weekday_too_long(result, facts):
            issues.append({
                "code": "weekday_session_too_long", "severity": "critical",
                "message": "周中课程按个人配速推算超过 120 分钟，且补充信息未明确允许",
                "recommendation": "缩短周中课程时长",
            })
        if _confirm_quality_back_to_back(result, facts):
            issues.append({
                "code": "quality_back_to_back", "severity": "critical",
                "message": "质量课连续两天排，或长距离/质量课后次日恢复不足",
                "recommendation": "错开质量课间隔并保证恢复日",
            })
        if _confirm_weekday_rest_streak(result, facts):
            issues.append({
                "code": "weekday_rest_streak", "severity": "warning",
                "message": "周中可训练日内连续两天及以上没有训练，训练日过度集中到周末",
                "recommendation": "将部分训练分散到周中可训练日",
            })
        return issues

    def _build_schedule_candidate(
        self,
        facts: dict[str, Any],
        envelope: dict[str, Any],
        supporting: dict[str, Any],
        uncertainties: tuple[Any, ...],
        framework: dict[str, Any],
        framework_id: str,
        generation_id: str,
        schedule_context: CoachRunContext,
        *,
        audit_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """near_term → 归一化 → 本地校验；可带上轮审计意见打回重排课表。"""
        if audit_feedback:
            near_term_context = schedule_context.facts.get("near_term_context")
            near_term_context = copy.deepcopy(near_term_context) if isinstance(near_term_context, dict) else {}
            near_term_context["audit_feedback"] = (
                audit_feedback.get("issues") or []
            )
            schedule_context = CoachRunContext(
                request={**schedule_context.request, "intent": "build_near_term_schedule_after_audit"},
                facts={**schedule_context.facts, "near_term_context": near_term_context},
                uncertainties=schedule_context.uncertainties,
                policy=schedule_context.policy,
                trace=schedule_context.trace,
            )
        schedule_cache_key = _stable_fingerprint({
            "framework_id": framework_id,
            "near_term_context": schedule_context.facts.get("near_term_context"),
        })
        try:
            schedule_started = time.monotonic()
            logger.info(
                "AI draft execution: generation_id=%s step=2/4 near_term_schedule%s",
                generation_id, " (audit rebuild)" if audit_feedback else "",
            )
            cached_schedule = self._schedule_cache.get(schedule_cache_key)
            if cached_schedule is not None:
                schedule = copy.deepcopy(cached_schedule)
                schedule_context = schedule_context.with_trace({
                    "skill": "build-near-term-schedule", "status": "cache_hit",
                    "cache_key": schedule_cache_key,
                })
            else:
                schedule, schedule_context = self.runner.run(
                    "build-near-term-schedule", schedule_context,
                )
                self._cache_put(self._schedule_cache, schedule_cache_key, schedule)
            logger.info(
                "training scheme timing: generation_id=%s stage=near_term_schedule cache=%s elapsed=%.2fs",
                generation_id, bool(cached_schedule), time.monotonic() - schedule_started,
            )
        except Exception as exc:
            logger.warning(
                "training scheme stage failed: stage=near_term_schedule failure_type=%s reason=%s",
                _failure_type(str(exc)), str(exc)[:240],
            )
            raise TrainingSchemeCandidateUnavailable(
                f"近期课表阶段失败：{exc}", stage="near_term_schedule",
                failure_type=_failure_type(str(exc)),
                completed=["training_framework"],
                framework_id=framework_id,
                diagnostic_reason=str(exc)[:240],
            ) from exc
        candidate = self._framework_to_candidate(framework)
        candidate.update({
            "first_two_weeks": copy.deepcopy(schedule.get("first_two_weeks") or []),
            "review": copy.deepcopy(schedule.get("review") or {}),
            "entry_review": copy.deepcopy(schedule.get("entry_review") or {}),
            "assumptions": [*candidate.get("assumptions", []), *(schedule.get("assumptions") or [])],
            "uncertainties": [*candidate.get("uncertainties", []), *(schedule.get("uncertainties") or [])],
            "risk_flags": [*candidate.get("risk_flags", []), *(schedule.get("risk_flags") or [])],
            "data_gaps": copy.deepcopy(schedule.get("data_gaps") or []),
            "user_explanation": str(schedule.get("user_explanation") or candidate.get("user_explanation") or ""),
            "framework_id": framework_id,
            "framework_summary": copy.deepcopy(framework.get("ability_summary") or {}),
            "goal_demand_summary": copy.deepcopy(framework.get("goal_demand_summary") or {}),
            "ability_gap": copy.deepcopy(framework.get("ability_gap") or []),
            "method_basis": copy.deepcopy(framework.get("method_basis") or []),
        })
        candidate, normalization_trace = TrainingSchemeCandidateNormalizer.normalize(
            candidate, facts, envelope,
        )
        logger.info("AI draft execution: generation_id=%s step=3/4 deterministic_normalization", generation_id)
        candidate["generation_mode"] = "skill"
        candidate["fallback_reason"] = None
        candidate["planning_trace"] = [
            *schedule_context.trace,
            {"stage": "training_framework", "status": "completed", "framework_id": framework_id},
            normalization_trace,
        ]
        self._attach_result_metadata(candidate, normalization_trace)
        try:
            logger.info("AI draft execution: generation_id=%s step=4/4 local_validation", generation_id)
            result = TrainingSchemeValidator.validate(candidate, facts, envelope)
        except Exception as exc:
            raise TrainingSchemeCandidateUnavailable(
                f"本地结构校验失败：{exc}", stage="local_validation",
                failure_type="validator", completed=["training_framework", "near_term_schedule"],
                framework_id=framework_id,
            ) from exc
        result["framework_id"] = framework_id
        result["degraded"] = bool(facts.get("uncertainties") or result.get("data_gaps"))
        result["data_gaps"] = [*facts.get("uncertainties", []), *(result.get("data_gaps") or [])]
        return result

    def _plan_dag(
        self, facts: dict[str, Any], envelope: dict[str, Any],
    ) -> dict[str, Any]:
        supporting = self._supporting_summaries(facts)
        generation_id = str(facts.get("generation_id") or "none")[:80]
        uncertainties = tuple(facts.get("uncertainties") or [])
        policy = {"coaching_mode": facts.get("coaching_mode")}
        framework_context_facts = {
            "goal": copy.deepcopy(facts.get("goal") or {}),
            "race_timeline": copy.deepcopy(facts.get("race_timeline") or {}),
            "athlete_capability_summary": copy.deepcopy(supporting["athlete_capability_summary"]),
            "athlete_current_state": copy.deepcopy(supporting["athlete_current_state"]),
            "goal_demand_model": copy.deepcopy(supporting["goal_demand_model"]),
            "athlete_profile": copy.deepcopy(supporting["athlete_profile"]),
            "data_gaps": list(uncertainties),
        }
        framework_facts = {
            "framework_context": framework_context_facts,
            "training_load_envelope": copy.deepcopy(envelope),
        }
        framework_cache_key = _stable_fingerprint({
            "goal": facts.get("goal"),
            "race_timeline": facts.get("race_timeline"),
            "capability": supporting["athlete_capability_summary"],
            "goal_demand": supporting["goal_demand_model"],
            "envelope": envelope,
        })
        framework_context = CoachRunContext(
            request={"intent": "build_training_framework", "generation_id": generation_id, "ai_call_index": 1},
            facts=framework_facts,
            uncertainties=uncertainties,
            policy=policy,
        )
        try:
            framework_started = time.monotonic()
            logger.info("AI draft execution: generation_id=%s step=1/4 training_framework", generation_id)
            cached_framework = self._framework_cache.get(framework_cache_key)
            if cached_framework is not None:
                framework = copy.deepcopy(cached_framework)
                framework_context = framework_context.with_trace({
                    "skill": "build-training-framework", "status": "cache_hit",
                    "cache_key": framework_cache_key,
                })
            else:
                framework, framework_context = self.runner.run(
                    "build-training-framework", framework_context,
                )
                self._cache_put(self._framework_cache, framework_cache_key, framework)
            logger.info(
                "training scheme timing: generation_id=%s stage=training_framework cache=%s elapsed=%.2fs",
                generation_id, bool(cached_framework), time.monotonic() - framework_started,
            )
        except Exception as exc:
            logger.warning(
                "training scheme stage failed: stage=training_framework failure_type=%s reason=%s",
                _failure_type(str(exc)), str(exc)[:240],
            )
            raise TrainingSchemeCandidateUnavailable(
                f"训练框架阶段失败：{exc}", stage="training_framework",
                failure_type=_failure_type(str(exc)),
                completed=[],
                diagnostic_reason=str(exc)[:240],
            ) from exc
        framework_id = _stable_fingerprint({"facts": facts, "framework": framework})
        schedule_context = CoachRunContext(
            request={"intent": "build_near_term_schedule", "generation_id": generation_id, "framework_id": framework_id, "ai_call_index": 2},
            facts={
                "training_framework": framework,
                "near_term_context": {
                    "goal": copy.deepcopy(facts.get("goal") or {}),
                    "race_timeline": copy.deepcopy(facts.get("race_timeline") or {}),
                    "current_state": self._compact_schedule_current_state(
                        supporting["athlete_current_state"],
                    ),
                    "constraints": copy.deepcopy(facts.get("constraints") or {}),
                    "pace_reference": copy.deepcopy(
                        (supporting.get("athlete_profile") or {}).get("recent_running_pace_sec_per_km")
                    ),
                    "data_gaps": list(uncertainties),
                },
                "framework_id": framework_id,
                # The near-term skill declares the load envelope as a
                # required deterministic fact. Keep the same envelope used
                # for framework generation in the downstream context.
                "training_load_envelope": copy.deepcopy(envelope),
                "input_contract": "near_term_context_v1",
            },
            uncertainties=uncertainties,
            policy=policy,
            trace=framework_context.trace,
        )
        result = self._build_schedule_candidate(
            facts, envelope, supporting, uncertainties, framework,
            framework_id, generation_id, schedule_context,
        )
        # 第 5 阶段：可执行性审计（独立 AI 调用）；fail 打回重生成 ≤2 轮
        for audit_round in range(3):
            audit = self._audit_scheme(facts, result, generation_id)
            if audit["status"] == "unavailable":
                result["audit"] = audit
                break
            # 确定性复核：AI 判定的 critical 数值规则用确定性代码复核，误判降级为 warning
            verified = self._verify_audit_critical(facts, result, audit)
            if verified["issues"]:
                audit = {**audit, "issues": verified["issues"], "summary": verified.get("summary") or audit.get("summary")}
            if not any(issue["severity"] == "critical" for issue in audit["issues"]):
                audit["verdict"] = "pass"
            if audit["verdict"] == "pass":
                result["audit"] = audit
                break
            if audit_round < 2:
                logger.info(
                    "AI draft execution: generation_id=%s audit round=%s verdict=fail, rebuilding near-term",
                    generation_id, audit_round + 1,
                )
                result = self._build_schedule_candidate(
                    facts, envelope, supporting, uncertainties, framework,
                    framework_id, generation_id, schedule_context,
                    audit_feedback=audit,
                )
            else:
                result["audit"] = {**audit, "status": "failed"}
                break
        return result

    def revise(
        self,
        facts: dict[str, Any],
        envelope: dict[str, Any],
        *,
        execution_summary: dict[str, Any],
        active_scheme: dict[str, Any],
        recovery_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """用独立重规划 Skill 生成候选；失败时显式使用同一安全兜底。"""
        if self.runner is None:
            return self._fallback(facts, envelope, reason="AI 重规划 Skill 不可用")
        context = CoachRunContext(
            request={"intent": "revise_training_scheme"},
            facts={
                "planning_fact_pack": facts,
                "training_load_envelope": envelope,
                "execution_summary": execution_summary,
                "natural_week_progress": execution_summary,
                "active_scheme": active_scheme,
                "recovery_snapshot": recovery_snapshot,
            },
            uncertainties=tuple(facts.get("uncertainties") or []),
            policy={"coaching_mode": facts.get("coaching_mode")},
        )
        try:
            started = time.monotonic()
            candidate, final_context = self.runner.run(
                "revise-training-scheme",
                context,
            )
            logger.info(
                "training scheme timing: skill=revise-training-scheme phase=skill_complete elapsed=%.2fs",
                time.monotonic() - started,
            )
            candidate = copy.deepcopy(candidate)
            normalizer_started = time.monotonic()
            candidate, normalization_trace = TrainingSchemeCandidateNormalizer.normalize(
                candidate, facts, envelope,
            )
            logger.info(
                "training scheme timing: skill=revise-training-scheme phase=normalizer_complete elapsed=%.2fs",
                time.monotonic() - normalizer_started,
            )
            candidate["generation_mode"] = "skill"
            candidate["fallback_reason"] = None
            candidate["planning_trace"] = [*final_context.trace, normalization_trace]
            self._attach_result_metadata(candidate, normalization_trace)
            validator_started = time.monotonic()
            result = TrainingSchemeValidator.validate(candidate, facts, envelope)
            logger.info(
                "training scheme timing: skill=revise-training-scheme phase=validator_complete elapsed=%.2fs",
                time.monotonic() - validator_started,
            )
            return result
        except Exception as exc:
            logger.warning("专业重规划 Skill 不可用: %s", exc)
            if "未配置 NEURUN_AI_API_KEY" in str(exc):
                return self._fallback(facts, envelope, reason=str(exc))
            raise TrainingSchemeCandidateUnavailable(str(exc)) from exc

    def _fallback(
        self,
        facts: dict[str, Any],
        envelope: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        weeks_remaining = int(facts.get("race_timeline", {}).get("weeks_remaining") or 0)
        feasibility = self._fallback_feasibility(facts, weeks_remaining)
        phases = self._periodization(max(4, weeks_remaining))
        first_four_weeks: list[dict[str, Any]] = []
        target = float(envelope["initial_target_km"])
        for week_number in range(1, 5):
            recovery = week_number % int(envelope["recovery_week_interval"]) == 0
            if week_number > 1:
                target = target * (0.90 if recovery else 1.05)
            target = round(min(target, float(envelope["capacity_km"])), 1)
            first_four_weeks.append({
                "week": week_number,
                "target_km": target,
                "focus": "恢复与吸收" if recovery else "建立稳定有氧和备赛连续性",
                "recovery_week": recovery,
                "workouts": self._week_workouts(
                    facts, envelope, target, recovery, week_number=week_number,
                ),
            })
        candidate = {
            "feasibility": feasibility,
            "periodization": phases,
            "load_progression": [
                {"week": item["week"], "target_km": item["target_km"], "recovery_week": item["recovery_week"]}
                for item in first_four_weeks
            ],
            "first_four_weeks": first_four_weeks,
            "assumptions": ["未使用在线 AI 教练推理，采用保守周期化规则"],
            "uncertainties": list(facts.get("uncertainties") or []),
            "risk_flags": ["medical_limitations"] if facts["constraints"].get("medical_limitations") else [],
            "user_explanation": "这是专业方案 Skill 不可用时的保守兜底，重点保证训练连续性和安全边界。",
            "generation_mode": "deterministic_fallback",
            "inference_source": "deterministic",
            "validation_status": "fallback",
            "recommendation_status": str(feasibility.get("level") or "insufficient_data"),
            "decision_status": "deterministic_fallback",
            "adjustments": [],
            "fallback_reason": reason,
            "planning_trace": [{"stage": "deterministic_fallback", "reason": reason}],
        }
        return TrainingSchemeValidator.validate(candidate, facts, envelope)

    @staticmethod
    def _attach_result_metadata(
        candidate: dict[str, Any],
        normalization_trace: dict[str, Any],
    ) -> None:
        adjustments = list(normalization_trace.get("adjustments") or [])
        recommendation = str(
            candidate.get("feasibility", {}).get("level") or "insufficient_data"
        )
        validation = "repaired" if adjustments else "passed"
        if recommendation in {"challenging", "not_recommended", "insufficient_data"}:
            decision = "ai_risk_advisory"
        elif validation == "repaired":
            decision = "ai_repaired"
        else:
            decision = "ai_validated"
        candidate.update({
            "inference_source": "ai",
            "validation_status": validation,
            "recommendation_status": recommendation,
            "decision_status": decision,
            "adjustments": adjustments,
        })

    @staticmethod
    def _fallback_feasibility(
        facts: dict[str, Any], weeks_remaining: int,
    ) -> dict[str, Any]:
        if weeks_remaining <= 0:
            level, confidence, summary = "insufficient_data", 0.2, "缺少有效的未来赛事日期"
        elif weeks_remaining < 6:
            level, confidence, summary = "not_recommended", 0.55, "剩余时间过短，不建议承诺完整能力提升周期"
        elif weeks_remaining < 10:
            level, confidence, summary = "challenging", 0.55, "备赛周期偏短，需要保守设定目标"
        else:
            level, confidence, summary = "challenging", 0.62, "时间允许建立周期，但仍需近期成绩验证目标"
        return {
            "level": level,
            "confidence": confidence,
            "summary": summary,
            "evidence": [f"距离赛事约 {weeks_remaining} 周"],
            "gaps": list(facts.get("uncertainties") or []),
            "routes": [
                {"id": "conservative", "label": "保守完成路线"},
                {"id": "balanced", "label": "平衡提升路线"},
                {"id": "adjust_goal", "label": "调整成绩或日期"},
            ],
        }

    @staticmethod
    def _periodization(total_weeks: int) -> list[dict[str, Any]]:
        if total_weeks <= 4:
            return [
                {"name": "基础与适应期", "weeks": 1, "purpose": "建立连续性并确认当前状态"},
                {"name": "专项建设期", "weeks": 1, "purpose": "在有限时间内保守维持专项刺激"},
                {"name": "减量期", "weeks": 2, "purpose": "降低疲劳并保持比赛准备度"},
            ]
        if total_weeks < 8:
            base, build, peak, taper = 1, max(1, total_weeks - 4), 1, 2
        else:
            taper = 2
            peak = max(1, round(total_weeks * 0.15))
            base = max(2, round(total_weeks * 0.35))
            build = max(1, total_weeks - base - peak - taper)
        return [
            {"name": "基础期", "weeks": base, "purpose": "建立有氧容量、训练连续性和基础力量"},
            {"name": "专项建设期", "weeks": build, "purpose": "逐步增加专项耐力和目标强度训练"},
            {"name": "高峰期", "weeks": peak, "purpose": "完成关键长距离和专项模拟"},
            {"name": "减量期", "weeks": taper, "purpose": "降低疲劳并保持比赛准备度"},
        ]

    @staticmethod
    def _week_workouts(
        facts: dict[str, Any],
        envelope: dict[str, Any],
        target_km: float,
        recovery: bool,
        *,
        week_number: int,
    ) -> list[dict[str, Any]]:
        constraints = facts["constraints"]
        days = _effective_available_days(constraints)
        max_minutes = int(constraints.get("max_session_minutes") or 90)
        medical = bool(str(constraints.get("medical_limitations") or "").strip())
        # 长距离优先落在周末；只有用户没有周末可训练日时才退化到最后一个可训日。
        weekend_days = [day for day in days if day in {5, 6}]
        long_day = weekend_days[-1] if weekend_days else days[-1]
        quality_day = days[max(0, len(days) // 2 - 1)] if len(days) >= 4 else None
        quality_allowed = quality_day is not None and not recovery and not medical
        # 最近两天的长跑/质量课只影响当前周衔接，不改变固定周基线。
        short_term = facts.get("short_term_training") or {}
        blocked_weekdays: set[int] = set()
        if week_number == 1:
            as_of_raw = str(facts.get("as_of") or "")[:10]
            try:
                as_of = date.fromisoformat(as_of_raw)
            except ValueError:
                as_of = date.today()
            monday = as_of - timedelta(days=as_of.weekday())
            for activity in short_term.get("activities") or []:
                if not isinstance(activity, dict) or not (
                    activity.get("is_long") or activity.get("is_quality")
                ):
                    continue
                try:
                    activity_date = date.fromisoformat(str(activity.get("date"))[:10])
                except (TypeError, ValueError):
                    continue
                next_day = activity_date + timedelta(days=1)
                if next_day >= as_of and monday <= next_day <= monday + timedelta(days=6):
                    blocked_weekdays.add(next_day.weekday())
            if quality_day in blocked_weekdays:
                quality_allowed = False
                quality_day = None
        long_share = 0.30 if recovery else min(0.35, float(envelope["max_long_run_ratio"]))
        quality_share = 0.20 if quality_allowed else 0.0
        easy_days = [day for day in days if day != long_day and day != quality_day]
        if quality_day is not None and not quality_allowed:
            easy_days.append(quality_day)
            easy_days.sort()
        easy_share = max(0.0, 1 - long_share - quality_share) / max(1, len(easy_days))
        allocations: list[tuple[int, str, float]] = []
        allocations.extend((day, "easy", easy_share) for day in easy_days)
        if quality_allowed and quality_day is not None:
            allocations.append((quality_day, "quality", quality_share))
        # 只有一个可训练日时，整周距离必然集中到这一天，不能再用多日训练的
        # 长距离占比规则否决方案；将其定义为受控有氧课，仍受单次时长上限保护。
        allocations.append((long_day, "easy" if len(days) == 1 else "long", long_share))
        allocations.sort()
        workouts: list[dict[str, Any]] = []
        remaining = target_km
        for index, (weekday, kind, share) in enumerate(allocations):
            distance = round(remaining if index == len(allocations) - 1 else target_km * share, 1)
            remaining = round(remaining - distance, 1)
            quality_variants = (
                ("变速耐力跑", ["aerobic_power", "pace_change"], "用短工作段建立节奏变化能力，不追求力竭"),
                ("阈值分段跑", ["threshold_development"], "以可控阈值刺激提升持续输出能力"),
                ("间歇耐受跑", ["high_intensity_tolerance", "running_economy"], "用工作—恢复结构练习高质量跑姿与耐受"),
            )
            variant = quality_variants[(week_number - 1) % len(quality_variants)]
            title = {
                "easy": "轻松有氧跑", "quality": variant[0], "long": "长距离有氧跑",
            }[kind]
            pace_reference = _personal_pace_reference(envelope.get("pace_reference_sec_per_km"))
            duration = (
                min(max_minutes, max(20, round(distance * pace_reference / 60)))
                if pace_reference else max_minutes
            )
            workout = {
                "weekday": weekday,
                "title": title,
                "type": kind,
                "purpose": {
                    "easy": "积累有氧并促进恢复",
                    "quality": variant[2],
                    "long": "提升耐力、补给和长时间运动适应",
                }[kind],
                "duration_minutes": duration,
                "distance_km": distance,
                "intensity": "呼吸有压力但始终可控" if kind == "quality" else "能完整对话，结束仍有余量",
                "alternatives": ["状态不佳时降低 20% 总量", "疼痛时停止跑步并记录反馈"],
                "adjustment_triggers": ["疼痛", "异常疲劳", "可用时间变化"],
                "is_key": kind in {"quality", "long"},
                "stimuli": (
                    variant[1] if kind == "quality"
                    else (["aerobic_endurance", "durability"] if kind == "long" else ["aerobic_endurance"])
                ),
                "training_prescription": {
                    "primary_completion": "time" if kind == "quality" else "distance",
                    "blocks": (
                        [
                            {"role": "warmup", "duration_minutes": 15, "instruction": "轻松跑并完成动态活动"},
                            {"role": "main", "duration_minutes": max(12, duration - 25), "instruction": "工作段与恢复段交替，始终保留一次可控余量"},
                            {"role": "cooldown", "duration_minutes": 10, "instruction": "慢跑或步行放松"},
                        ] if kind == "quality" else []
                    ),
                    "targets": {"feel": {"label": "呼吸有压力但始终可控" if kind == "quality" else "能完整对话，结束仍有余量", "rpe": "6–7" if kind == "quality" else "2–3"}},
                    "method_basis": {"principle": variant[2] if kind == "quality" else "以有氧连续性为基础，避免把单次训练做成力竭测试", "stage_relation": "本周关键刺激" if kind == "quality" else "为本周耐力与恢复服务"},
                },
            }
            workouts.append(ensure_training_prescription(
                workout, pace_reference_sec_per_km=pace_reference,
                synthesize_steps=True,
            ))
        return workouts


def create_default_professional_planner(
    project_root: str | None = None,
) -> ProfessionalSchemePlanner:
    """创建生产使用的 AI Skill planner。"""
    registry = SkillRegistry.default(project_root)
    root = registry.get("draft-training-scheme").prompt_path.parents[2]
    core_path = root / "coach-core.md"
    core_prompt = core_path.read_text(encoding="utf-8") if core_path.exists() else ""
    runner = CoachSkillRunner(
        registry,
        OpenAICompatibleSkillModel(),
        core_prompt=core_prompt,
    )
    return ProfessionalSchemePlanner(runner=runner)
