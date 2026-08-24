"""Coach Skill 输出的轻量结构化校验。"""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Callable


class SkillSchemaError(ValueError):
    """模型输出不满足 Skill 合同。"""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillSchemaError(f"{field} 必须是对象")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise SkillSchemaError(f"{field} 必须是数组")
    return value


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


_SHARE_CARD_FORBIDDEN_TEXT = re.compile(
    r"睡眠|恢复|hrv|acwr|风险|警告|异常|建议",
    re.IGNORECASE,
)


def _share_card_text(value: Any, limit: int) -> str:
    text = _bounded_text(value, limit)
    return "" if _SHARE_CARD_FORBIDDEN_TEXT.search(text) else text


def _validate_share_card(value: Any) -> dict[str, Any]:
    """Normalize the optional AI-generated share-card summary contract."""
    if value is None:
        value = {}
    item = _mapping(value, "share_card")
    sessions: list[dict[str, Any]] = []
    session_indexes: set[int] = set()
    for index, raw_session in enumerate(_list(item.get("sessions", []), "share_card.sessions")):
        if len(sessions) >= 4:
            break
        session = _mapping(raw_session, f"share_card.sessions[{index}]")
        raw_session_index = session.get("session_index")
        if isinstance(raw_session_index, bool):
            raise SkillSchemaError(
                f"share_card.sessions[{index}].session_index 无效"
            )
        if isinstance(raw_session_index, float) and not raw_session_index.is_integer():
            raise SkillSchemaError(
                f"share_card.sessions[{index}].session_index 必须为整数"
            )
        try:
            session_index = int(raw_session_index)
        except (TypeError, ValueError) as exc:
            raise SkillSchemaError(
                f"share_card.sessions[{index}].session_index 无效"
            ) from exc
        if session_index <= 0:
            raise SkillSchemaError(
                f"share_card.sessions[{index}].session_index 必须为正整数"
            )
        if session_index in session_indexes:
            raise SkillSchemaError(
                f"share_card.sessions[{index}].session_index 不能重复"
            )
        session_indexes.add(session_index)
        sessions.append({
            "session_index": session_index,
            "text": _share_card_text(session.get("text"), 70),
        })
    return {
        "headline": _share_card_text(item.get("headline"), 90),
        "sessions": sessions,
        "takeaway": _share_card_text(item.get("takeaway"), 90),
        "conclusion": _share_card_text(item.get("conclusion"), 90),
    }


_WEEKDAY_NAMES = {
    "monday": 0, "mon": 0, "周一": 0, "星期一": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "周二": 1, "星期二": 1,
    "wednesday": 2, "wed": 2, "周三": 2, "星期三": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "周四": 3, "星期四": 3,
    "friday": 4, "fri": 4, "周五": 4, "星期五": 4,
    "saturday": 5, "sat": 5, "周六": 5, "星期六": 5,
    "sunday": 6, "sun": 6, "周日": 6, "星期日": 6, "星期天": 6,
}


def _normalize_weekday(value: Any, field: str) -> int:
    """将模型常见的标准星期名称收敛为合同使用的 0–6。"""
    if isinstance(value, bool):
        raise SkillSchemaError(f"{field} 无效: {value!r}")
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _WEEKDAY_NAMES:
            return _WEEKDAY_NAMES[normalized]
        if len(normalized) == 10 and normalized[4] == "-" and normalized[7] == "-":
            try:
                # A schedule may use an ISO date for a concrete calendar slot.
                # Accept only YYYY-MM-DD and retain the internal weekday
                # contract used by the downstream planner.
                return date.fromisoformat(normalized).weekday()
            except ValueError:
                pass
        value = normalized
    try:
        weekday = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillSchemaError(f"{field} 无效: {value!r}") from exc
    if not 0 <= weekday <= 6:
        raise SkillSchemaError(f"{field} 必须在 0 到 6: {value!r}")
    return weekday


def _repair_intensity_intent(workout: dict[str, Any]) -> dict[str, Any]:
    """从已有课程类型和体感文本补齐最小、无配速/心率的强度意图。"""
    workout_type = str(workout.get("type") or "").strip().casefold()
    if workout_type in {"interval", "intervals", "repetition", "reps"}:
        zone = 5
    elif workout_type in {"quality", "tempo", "threshold", "fartlek", "race_specific"}:
        zone = 4
    elif workout_type in {"steady", "progression", "moderate"}:
        zone = 3
    else:
        zone = 2
    feel = str(workout.get("intensity") or "按体感保持可控").strip()[:120]
    return {
        "zone": zone,
        "preferred_metric": "feel",
        "fallback_feel": feel,
        "basis": "deterministic_type_intensity_repair",
    }
    return weekday


def _validate_workout_steps(value: Any, field: str) -> None:
    steps = _list(value, field)
    if not steps:
        raise SkillSchemaError(f"{field} 不能为空")
    for index, raw_step in enumerate(steps):
        step = _mapping(raw_step, f"{field}[{index}]")
        if not str(step.get("step_id") or ""):
            raise SkillSchemaError(f"{field}[{index}].step_id 不能为空")
        try:
            order = int(step.get("order"))
        except (TypeError, ValueError) as exc:
            raise SkillSchemaError(f"{field}[{index}].order 无效") from exc
        if order <= 0:
            raise SkillSchemaError(f"{field}[{index}].order 必须为正整数")
        kind = str(step.get("kind") or "")
        if kind == "repeat":
            try:
                repeat_count = int(step.get("repeat_count"))
            except (TypeError, ValueError) as exc:
                raise SkillSchemaError(f"{field}[{index}].repeat_count 无效") from exc
            if repeat_count <= 0:
                raise SkillSchemaError(f"{field}[{index}].repeat_count 必须为正整数")
            children = _list(step.get("children"), f"{field}[{index}].children")
            roles = {
                str(child.get("role") or "") for child in children
                if isinstance(child, dict)
            }
            if not {"work", "recovery"}.issubset(roles):
                raise SkillSchemaError(f"{field}[{index}] 必须包含工作段和恢复段")
            _validate_workout_steps(children, f"{field}[{index}].children")
            continue
        if kind not in {"work", "recovery"}:
            raise SkillSchemaError(f"{field}[{index}].kind 无效")
        _mapping(step.get("dose"), f"{field}[{index}].dose")
        _mapping(step.get("intensity_intent"), f"{field}[{index}].intensity_intent")
        _mapping(step.get("transition"), f"{field}[{index}].transition")


def validate_skill_finding(value: Any) -> dict[str, Any]:
    item = _mapping(value, "finding")
    status = str(item.get("status") or "")
    if status not in {"ok", "insufficient_data", "blocked"}:
        raise SkillSchemaError("finding.status 无效")
    return {
        "skill": str(item.get("skill") or ""),
        "status": status,
        "conclusion": str(item.get("conclusion") or ""),
        "evidence": [str(part) for part in _list(item.get("evidence", []), "evidence")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
        "risk_flags": [str(part) for part in _list(item.get("risk_flags", []), "risk_flags")],
        "recommendations": [str(part) for part in _list(item.get("recommendations", []), "recommendations")],
    }


_PLAN_REVIEW_DECISIONS = {"hold", "advance", "adjust", "deload", "replan"}
_PLAN_REVIEW_SCOPES = {"entry_phase", "next_week", "phase", "scheme"}


def _normalize_handoff(*, applied: bool = False) -> dict[str, Any]:
    return {
        "status": "applied_to_candidate" if applied else "review_pending",
        "requires_user_confirmation": True,
    }


def validate_training_plan_review(value: Any) -> dict[str, Any]:
    item = _mapping(value, "TrainingPlanReview")
    status = str(item.get("status") or "")
    if status not in {"ok", "insufficient_data", "blocked"}:
        raise SkillSchemaError("TrainingPlanReview.status 无效")
    decision = str(item.get("decision") or "")
    if decision not in _PLAN_REVIEW_DECISIONS:
        raise SkillSchemaError("TrainingPlanReview.decision 无效")
    if status != "ok":
        decision = "hold"
    scope = str(item.get("scope") or "next_week")
    if scope not in _PLAN_REVIEW_SCOPES:
        raise SkillSchemaError("TrainingPlanReview.scope 无效")
    confidence = float(item.get("confidence") or 0)
    if not 0 <= confidence <= 1:
        raise SkillSchemaError("TrainingPlanReview.confidence 必须在 0 到 1")
    stage = str(item.get("review_stage") or "execution_review")
    if stage not in {"entry_review", "execution_review"}:
        raise SkillSchemaError("TrainingPlanReview.review_stage 无效")
    return {
        "skill": "review-training-plan",
        "status": status,
        "conclusion": str(item.get("conclusion") or ""),
        "evidence": [str(part) for part in _list(item.get("evidence", []), "evidence")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
        "risk_flags": [str(part) for part in _list(item.get("risk_flags", []), "risk_flags")],
        "recommendations": [str(part) for part in _list(item.get("recommendations", []), "recommendations")],
        "review_stage": stage,
        "decision": decision,
        "scope": scope,
        "recommended_entry_phase": item.get("recommended_entry_phase"),
        "confidence": confidence,
        "adjustments": [str(part) for part in _list(item.get("adjustments", []), "adjustments")],
        "safety_hold": item.get("safety_hold") if isinstance(item.get("safety_hold"), dict) else None,
        "user_explanation": str(item.get("user_explanation") or ""),
        "handoff": _normalize_handoff(),
    }


def validate_coach_insight(value: Any) -> dict[str, Any]:
    item = _mapping(value, "CoachInsight")
    execution = _mapping(item.get("plan_execution", {}), "plan_execution")
    comparison_status = str(execution.get("comparison_status") or "not_applicable")
    if comparison_status not in {"applicable", "not_applicable"}:
        raise SkillSchemaError("plan_execution.comparison_status 无效")
    on_track = execution.get("on_track")
    if on_track is not None and not isinstance(on_track, bool):
        raise SkillSchemaError("plan_execution.on_track 必须是布尔值或 null")
    return {
        "plan_execution": {
            "today_planned": str(execution.get("today_planned") or ""),
            "today_actual": str(execution.get("today_actual") or ""),
            "today_match": str(execution.get("today_match") or ""),
            "comparison_status": comparison_status,
            "week_completion": str(execution.get("week_completion") or ""),
            "on_track": on_track,
            "deviation_note": str(execution.get("deviation_note") or ""),
        },
        "conclusion": str(item.get("conclusion") or ""),
        "observations": [str(part) for part in _list(item.get("observations", []), "observations")],
        "recommendations": [str(part) for part in _list(item.get("recommendations", []), "recommendations")],
        "warnings": [str(part) for part in _list(item.get("warnings", []), "warnings")],
        "share_card": _validate_share_card(item.get("share_card")),
        "plan_adjusted": False,
        "session_summary": str(item.get("session_summary") or ""),
        "session_characteristics": [
            part for part in _list(item.get("session_characteristics", []), "session_characteristics")
            if isinstance(part, dict)
        ],
        "training_effect": str(item.get("training_effect") or ""),
        "recovery_response": str(item.get("recovery_response") or ""),
        "capability_signals": [str(part) for part in _list(item.get("capability_signals", []), "capability_signals")],
        "next_day_constraints": [str(part) for part in _list(item.get("next_day_constraints", []), "next_day_constraints")],
        "evidence": [part for part in _list(item.get("evidence", []), "evidence") if isinstance(part, dict)],
        "gaps": [part for part in _list(item.get("gaps", []), "gaps") if isinstance(part, dict)],
        "confidence": min(1.0, max(0.0, float(item.get("confidence") or 0))),
    }


def validate_training_day_semantic_summary(value: Any) -> dict[str, Any]:
    """校验单日运动特点摘要；证据数值必须来自输入事实。"""
    item = _mapping(value, "TrainingDaySemanticSummary")
    status = str(item.get("status") or "")
    if status not in {"ok", "insufficient_data"}:
        raise SkillSchemaError("TrainingDaySemanticSummary.status 无效")
    features: list[dict[str, Any]] = []
    for index, raw_feature in enumerate(_list(item.get("observed_features", []), "observed_features")):
        feature = _mapping(raw_feature, f"observed_features[{index}]")
        confidence = float(feature.get("confidence") or 0)
        if not 0 <= confidence <= 1:
            raise SkillSchemaError(f"observed_features[{index}].confidence 必须在 0 到 1")
        evidence = []
        for evidence_index, raw_evidence in enumerate(_list(feature.get("evidence", []), f"observed_features[{index}].evidence")):
            evidence.append(_mapping(raw_evidence, f"observed_features[{index}].evidence[{evidence_index}]"))
        features.append({
            "feature_code": str(feature.get("feature_code") or "other_observation"),
            "label": str(feature.get("label") or "观察到的训练特点"),
            "confidence": confidence,
            "evidence": evidence,
        })
    return {
        "skill": "summarize-training-day",
        "status": status,
        "summary": str(item.get("summary") or ""),
        "observed_features": features,
        "training_implications": [str(part) for part in _list(item.get("training_implications", []), "training_implications")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
    }


def _validate_entry_review(raw: Any, *, field: str = "entry_review") -> dict[str, Any]:
    item = _mapping(raw or {}, field)
    decision = str(item.get("decision") or "hold")
    if decision not in _PLAN_REVIEW_DECISIONS:
        raise SkillSchemaError(f"{field}.decision 无效")
    try:
        confidence = float(item.get("confidence") or 0)
    except (TypeError, ValueError) as exc:
        raise SkillSchemaError(f"{field}.confidence 无效") from exc
    if not 0 <= confidence <= 1:
        raise SkillSchemaError(f"{field}.confidence 必须在 0 到 1")
    applied = bool(item.get("applied")) and decision != "replan"
    return {
        "recommended_entry_phase": item.get("recommended_entry_phase"),
        "decision": decision,
        "confidence": confidence,
        "evidence": [str(part) for part in _list(item.get("evidence", []), f"{field}.evidence")],
        "adjustments": [str(part) for part in _list(item.get("adjustments", []), f"{field}.adjustments")],
        "applied": applied,
        "applied_adjustments": [str(part) for part in _list(item.get("applied_adjustments", []), f"{field}.applied_adjustments")],
        "handoff": _normalize_handoff(applied=applied),
    }


def validate_training_framework(value: Any) -> dict[str, Any]:
    item = _mapping(value, "TrainingFramework")
    feasibility = _mapping(item.get("feasibility"), "feasibility")
    level = str(feasibility.get("level") or "")
    if level not in {"feasible", "challenging", "not_recommended", "insufficient_data"}:
        raise SkillSchemaError("TrainingFramework.feasibility.level 无效")
    try:
        confidence = float(feasibility.get("confidence") or 0)
    except (TypeError, ValueError) as exc:
        raise SkillSchemaError("TrainingFramework.feasibility.confidence 无效") from exc
    if not 0 <= confidence <= 1:
        raise SkillSchemaError("TrainingFramework.feasibility.confidence 必须在 0 到 1")
    raw_periodization = _list(item.get("periodization"), "periodization")
    if not raw_periodization:
        raise SkillSchemaError("TrainingFramework.periodization 不能为空")
    periodization: list[dict[str, Any]] = []
    for index, raw_phase in enumerate(raw_periodization):
        phase = _mapping(raw_phase, f"TrainingFramework.periodization[{index}]")
        name = str(phase.get("name") or "").strip()
        purpose = str(phase.get("purpose") or "").strip()
        try:
            weeks = int(phase.get("weeks") or 0)
        except (TypeError, ValueError) as exc:
            raise SkillSchemaError(
                f"TrainingFramework.periodization[{index}].weeks 无效"
            ) from exc
        if not name:
            raise SkillSchemaError(
                f"TrainingFramework.periodization[{index}].name 不能为空"
            )
        if weeks <= 0:
            raise SkillSchemaError(
                f"TrainingFramework.periodization[{index}].weeks 必须大于 0"
            )
        if not purpose:
            raise SkillSchemaError(
                f"TrainingFramework.periodization[{index}].purpose 不能为空"
            )
        periodization.append({**phase, "name": name, "weeks": weeks, "purpose": purpose})
    progression = _list(item.get("load_progression"), "load_progression")
    if not progression:
        raise SkillSchemaError("TrainingFramework.load_progression 不能为空")
    return {
        "feasibility": {
            "level": level,
            "confidence": confidence,
            "summary": str(feasibility.get("summary") or ""),
            "evidence": [str(part) for part in _list(feasibility.get("evidence", []), "feasibility.evidence")],
            "gaps": [str(part) for part in _list(feasibility.get("gaps", []), "feasibility.gaps")],
        },
        "ability_summary": item.get("ability_summary") if isinstance(item.get("ability_summary"), dict) else {},
        "goal_demand_summary": item.get("goal_demand_summary") if isinstance(item.get("goal_demand_summary"), dict) else {},
        "ability_gap": [str(part) for part in _list(item.get("ability_gap", []), "ability_gap")],
        "recommended_entry_phase": str(item.get("recommended_entry_phase") or ""),
        "entry_rationale": str(item.get("entry_rationale") or ""),
        "periodization": periodization,
        "load_progression": progression,
        "weekly_principles": item.get("weekly_principles") if isinstance(item.get("weekly_principles"), dict) else {},
        "method_basis": [str(part) for part in _list(item.get("method_basis", []), "method_basis")],
        "assumptions": [str(part) for part in _list(item.get("assumptions", []), "assumptions")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
        "risk_flags": [str(part) for part in _list(item.get("risk_flags", []), "risk_flags")],
        "user_explanation": str(item.get("user_explanation") or ""),
    }


def validate_near_term_schedule(value: Any) -> dict[str, Any]:
    item = _mapping(value, "NearTermSchedule")
    raw_weeks = _list(item.get("first_two_weeks"), "first_two_weeks")
    if len(raw_weeks) != 2:
        raise SkillSchemaError("NearTermSchedule.first_two_weeks 必须恰好两项")
    weeks: list[dict[str, Any]] = []
    for index, raw_week in enumerate(raw_weeks):
        week = _mapping(raw_week, f"first_two_weeks[{index}]")
        workouts = _list(week.get("workouts"), f"first_two_weeks[{index}].workouts")
        if not workouts:
            raise SkillSchemaError(f"first_two_weeks[{index}].workouts 不能为空")
        for workout_index, raw_workout in enumerate(workouts):
            workout = _mapping(raw_workout, f"first_two_weeks[{index}].workouts[{workout_index}]")
            weekday = _normalize_weekday(
                workout.get("weekday", -1),
                f"NearTermSchedule.first_two_weeks[{index}].workouts[{workout_index}].weekday",
            )
            workout["weekday"] = weekday
            pace_intent = workout.get("pace_intent")
            if pace_intent is not None:
                workout["pace_intent"] = (
                    str(pace_intent).strip()[:40] if str(pace_intent).strip() else None
                )
            if str(workout.get("type") or "") != "rest":
                if not (workout.get("duration_minutes") or workout.get("distance_km")):
                    raise SkillSchemaError(
                        f"NearTermSchedule.first_two_weeks[{index}].workouts[{workout_index}] 课程缺少时长或距离"
                    )
                if not isinstance(workout.get("intensity_intent"), dict):
                    workout["intensity_intent"] = _repair_intensity_intent(workout)
        weeks.append(week)
    review = item.get("review") if isinstance(item.get("review"), dict) else {"status": "not_provided", "items": [], "safety_hold": None}
    return {
        "first_two_weeks": weeks,
        "review": {
            "status": str(review.get("status") or "not_provided"),
            "items": [part for part in _list(review.get("items", []), "review.items") if isinstance(part, dict)],
            "safety_hold": review.get("safety_hold") if isinstance(review.get("safety_hold"), dict) else None,
        },
        "entry_review": _validate_entry_review(item.get("entry_review")),
        "assumptions": [str(part) for part in _list(item.get("assumptions", []), "assumptions")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
        "risk_flags": [str(part) for part in _list(item.get("risk_flags", []), "risk_flags")],
        "data_gaps": [str(part) for part in _list(item.get("data_gaps", []), "data_gaps")],
        "user_explanation": str(item.get("user_explanation") or ""),
    }


def validate_training_scheme_candidate(value: Any) -> dict[str, Any]:
    item = _mapping(value, "TrainingSchemeCandidate")
    feasibility = _mapping(item.get("feasibility"), "feasibility")
    level = str(feasibility.get("level") or "")
    if level not in {"feasible", "challenging", "not_recommended", "insufficient_data"}:
        raise SkillSchemaError("feasibility.level 无效")
    confidence = float(feasibility.get("confidence") or 0)
    if not 0 <= confidence <= 1:
        raise SkillSchemaError("feasibility.confidence 必须在 0 到 1")
    periodization = _list(item.get("periodization"), "periodization")
    raw_weeks = item.get("first_two_weeks")
    week_field = "first_two_weeks"
    if raw_weeks is None:
        raw_weeks = item.get("first_four_weeks")
        week_field = "first_four_weeks"
    first_four_weeks = _list(raw_weeks, week_field)
    if not periodization:
        raise SkillSchemaError("periodization 不能为空")
    if not first_four_weeks:
        raise SkillSchemaError("first_four_weeks 不能为空")
    if len(first_four_weeks) not in {2, 4}:
        raise SkillSchemaError("first_two_weeks 必须恰好两项（历史完整候选可为四项）")
    for week_index, raw_week in enumerate(first_four_weeks):
        week = _mapping(raw_week, f"first_four_weeks[{week_index}]")
        workouts = _list(
            week.get("workouts"), f"first_four_weeks[{week_index}].workouts",
        )
        if not workouts:
            raise SkillSchemaError(f"first_four_weeks[{week_index}].workouts 不能为空")
        for workout_index, raw_workout in enumerate(workouts):
            workout = _mapping(
                raw_workout,
                f"first_four_weeks[{week_index}].workouts[{workout_index}]",
            )
            if str(workout.get("type") or "") == "rest":
                continue
            # AI 只需返回课程骨架和强度意图；完整 v2 处方由
            # TrainingSchemeCandidateNormalizer 的确定性层补全。若模型仍返回
            # 完整处方，继续按旧合同校验，保证向后兼容。
            raw_prescription = workout.get("training_prescription")
            if raw_prescription is not None:
                prescription = _mapping(
                    raw_prescription,
                    f"first_four_weeks[{week_index}].workouts[{workout_index}].training_prescription",
                )
                if prescription.get("structure_version") != 2:
                    raise SkillSchemaError("training_prescription.structure_version 必须为 2")
                _validate_workout_steps(
                    prescription.get("steps"),
                    f"first_four_weeks[{week_index}].workouts[{workout_index}].training_prescription.steps",
                )
    raw_review = item.get("review")
    review: dict[str, Any]
    if isinstance(raw_review, dict):
        raw_items = raw_review.get("items", [])
        review = {
            "status": str(raw_review.get("status") or "not_provided"),
            "items": [part for part in raw_items if isinstance(part, dict)],
            "safety_hold": raw_review.get("safety_hold") if isinstance(raw_review.get("safety_hold"), dict) else None,
        }
    else:
        # 审阅是非阻断的 AI 建议；旧 Skill 输出没有该字段时保持兼容。
        review = {"status": "not_provided", "items": [], "safety_hold": None}
    raw_entry_review = item.get("entry_review")
    if isinstance(raw_entry_review, dict):
        entry_decision = str(raw_entry_review.get("decision") or "hold")
        if entry_decision not in _PLAN_REVIEW_DECISIONS:
            entry_decision = "hold"
        applied = bool(raw_entry_review.get("applied"))
        if entry_decision == "replan" or review.get("safety_hold") is not None:
            applied = False
        entry_review = {
            "recommended_entry_phase": raw_entry_review.get("recommended_entry_phase"),
            "decision": entry_decision,
            "confidence": min(1.0, max(0.0, float(raw_entry_review.get("confidence") or 0))),
            "evidence": [str(part) for part in _list(raw_entry_review.get("evidence", []), "entry_review.evidence")],
            "adjustments": [str(part) for part in _list(raw_entry_review.get("adjustments", []), "entry_review.adjustments")],
            "applied": applied,
            "applied_adjustments": [str(part) for part in _list(raw_entry_review.get("applied_adjustments", []), "entry_review.applied_adjustments")],
            "handoff": _normalize_handoff(applied=applied),
        }
    else:
        entry_review = {
        "recommended_entry_phase": None,
        "decision": "hold",
        "confidence": 0.0,
        "evidence": [],
        "adjustments": [],
        "applied": False,
        "applied_adjustments": [],
        "handoff": _normalize_handoff(),
        }
    return {
        "feasibility": {
            "level": level,
            "confidence": confidence,
            "summary": str(feasibility.get("summary") or ""),
            "evidence": [str(part) for part in _list(feasibility.get("evidence", []), "feasibility.evidence")],
            "gaps": [str(part) for part in _list(feasibility.get("gaps", []), "feasibility.gaps")],
            "routes": _list(feasibility.get("routes", []), "feasibility.routes"),
        },
        "periodization": periodization,
        "load_progression": _list(item.get("load_progression", []), "load_progression"),
        # Normalizer 会把紧凑两周候选延展为 canonical first_four_weeks。
        "first_four_weeks": first_four_weeks,
        "assumptions": [str(part) for part in _list(item.get("assumptions", []), "assumptions")],
        "uncertainties": [str(part) for part in _list(item.get("uncertainties", []), "uncertainties")],
        "risk_flags": [str(part) for part in _list(item.get("risk_flags", []), "risk_flags")],
        "user_explanation": str(item.get("user_explanation") or ""),
        "review": review,
        "entry_review": entry_review,
        "data_gaps": [str(part) for part in _list(item.get("data_gaps", []), "data_gaps")],
        "framework_id": str(item.get("framework_id") or ""),
        "framework_summary": item.get("framework_summary") if isinstance(item.get("framework_summary"), dict) else {},
        "goal_demand_summary": item.get("goal_demand_summary") if isinstance(item.get("goal_demand_summary"), dict) else {},
        "ability_gap": [str(part) for part in _list(item.get("ability_gap", []), "ability_gap")],
        "method_basis": [str(part) for part in _list(item.get("method_basis", []), "method_basis")],
    }


def validate_scheme_audit(value: Any) -> dict[str, Any]:
    """可执行性审计结论：pass/fail + 结构化 issues。"""
    item = _mapping(value, "SchemeAudit")
    verdict = str(item.get("verdict") or "").strip()
    if verdict not in {"pass", "fail"}:
        raise SkillSchemaError("SchemeAudit.verdict 必须是 pass 或 fail")
    raw_issues = _list(item.get("issues", []), "issues")
    issues: list[dict[str, Any]] = []
    for index, raw_issue in enumerate(raw_issues):
        issue = _mapping(raw_issue, f"issues[{index}]")
        severity = str(issue.get("severity") or "").strip()
        if severity not in {"critical", "warning"}:
            severity = "warning"
        issues.append({
            "code": str(issue.get("code") or "unclassified").strip()[:60],
            "severity": severity,
            "message": str(issue.get("message") or "").strip()[:300],
            "recommendation": str(issue.get("recommendation") or "").strip()[:300],
        })
    has_critical = any(issue["severity"] == "critical" for issue in issues)
    return {
        "verdict": "fail" if has_critical else "pass",
        "summary": str(item.get("summary") or "").strip()[:200],
        "issues": issues,
    }


VALIDATORS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "CoachInsight": validate_coach_insight,
    "TrainingDaySemanticSummary": validate_training_day_semantic_summary,
    "SkillFinding": validate_skill_finding,
    "TrainingPlanReview": validate_training_plan_review,
    "TrainingSchemeCandidate": validate_training_scheme_candidate,
    "TrainingFramework": validate_training_framework,
    "NearTermSchedule": validate_near_term_schedule,
    "SchemeAudit": validate_scheme_audit,
}


def validate_output(model_name: str, value: Any) -> dict[str, Any]:
    validator = VALIDATORS.get(model_name)
    if validator is None:
        return _mapping(value, model_name)
    return validator(value)
