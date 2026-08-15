"""专业赛事方案事实包、生成和安全校验测试。"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta

import pytest

from src.training_planning import (
    PlanningFactPackBuilder,
    ProfessionalSchemePlanner,
    TrainingLoadEnvelope,
    TrainingSchemeCandidateNormalizer,
    TrainingSchemeValidationError,
    TrainingSchemeValidator,
    ensure_training_prescription,
)
from src.coach_runtime import CoachSkillRunner


def _facts(**overrides):
    goal = {
        "goal_id": "goal-marathon",
        "name": "秋季全马",
        "distance": "marathon",
        "target_time": "03:30:00",
        "target_date": str(date.today() + timedelta(weeks=16)),
    }
    baseline = {
        "coverage": "sufficient", "window_days": 28,
        "activity_count": 16, "distance_km": 160,
        "previous_week_km": 40, "average_weekly_km": 40, "recent_7d_km": 120,
        "longest_distance_km": 24,
    }
    constraints = {
        "available_days": [0, 2, 4, 6], "max_session_minutes": 150,
        "preferred_terrain": "公路", "medical_limitations": "",
        "cross_training": True,
    }
    goal.update(overrides.pop("goal", {}))
    baseline.update(overrides.pop("baseline", {}))
    constraints.update(overrides.pop("constraints", {}))
    short_term_training = overrides.pop("short_term_training", None)
    return PlanningFactPackBuilder.build(
        goal=goal, baseline=baseline, constraints=constraints,
        athlete_profile=overrides.pop("athlete_profile", {}),
        recovery_snapshot=overrides.pop("recovery_snapshot", {}),
        short_term_training=short_term_training,
        as_of=date.today(),
    )


def test_fact_pack_calculates_race_timeline_and_preserves_uncertainty():
    facts = _facts(baseline={"coverage": "partial"})

    assert facts["coaching_mode"] == "race_preparation"
    assert facts["race_timeline"]["weeks_remaining"] == 16
    assert facts["athlete_baseline"]["weekly_volume"]["previous_week_km"] == 40
    assert "recent_7d_km" not in facts["athlete_baseline"]["weekly_volume"]
    assert "活动覆盖不完整" in facts["uncertainties"]


def test_fact_pack_keeps_optional_user_supplement_separate_from_verified_facts():
    facts = _facts(constraints={"additional_context": "本周出差两天，周末再安排长距离"})

    assert facts["constraints"]["additional_context"] == "本周出差两天，周末再安排长距离"
    assert facts["athlete_baseline"]["weekly_volume"]["previous_week_km"] == 40


def test_case_fixture_preserves_marathon_230_inputs_for_framework_stage():
    facts = _facts(
        goal={
            "name": "10 月 18 日全马破 230",
            "target_time": "02:30:00",
            "target_date": "2026-10-18",
        },
        baseline={"previous_week_km": 101, "average_weekly_km": 105, "distance_km": 211},
        athlete_profile={
            "personal_bests": [
                {"distance": "marathon", "time": "02:32:49"},
                {"distance": "half_marathon", "time": "01:12:48"},
            ],
        },
        recovery_snapshot={"status": "poor", "hrv": "low", "neural_profile": "难激活且难压制"},
    )
    facts["training_summary"] = {
        "schema_version": "training-day-summary-v1",
        "reference_weeks": [
            {"running_distance_km": 110, "primary_type_counts": {"tempo": 1, "long": 1}, "recovery": {"status": "poor"}},
            {"running_distance_km": 101, "primary_type_counts": {"fartlek": 1, "long": 1}, "recovery": {"status": "poor"}},
        ],
        "recent_days": [{"date": "2026-08-08", "primary_type": "long", "total_distance_km": 30}, {"date": "2026-08-09", "status": "confirmed_rest"}],
    }

    supporting = ProfessionalSchemePlanner._supporting_summaries(facts)
    assert facts["goal"]["target_time"] == "02:30:00"
    assert supporting["athlete_capability_summary"]["reference_weeks"][0]["running_distance_km"] == 110
    assert supporting["athlete_capability_summary"]["quality_sessions"] == []
    assert supporting["athlete_current_state"]["recent_days"][0]["primary_type"] == "long"
    assert supporting["goal_demand_model"]["weeks_remaining"] == facts["race_timeline"]["weeks_remaining"]


def test_dynamic_current_week_volume_cannot_raise_draft_baseline():
    facts = _facts(baseline={"previous_week_km": 40, "average_weekly_km": 40, "recent_7d_km": 120})
    envelope = TrainingLoadEnvelope.calculate(facts)

    assert envelope["observed_weekly_km"] == 40
    assert envelope["initial_target_km"] == 40


def test_deterministic_fallback_is_periodized_and_explicitly_labeled():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    result = ProfessionalSchemePlanner().plan(facts, envelope)

    assert result["generation_mode"] == "deterministic_fallback"
    assert result["fallback_reason"] == "AI 方案 Skill 不可用"
    assert result["inference_source"] == "deterministic"
    assert result["validation_status"] == "fallback"
    assert result["decision_status"] == "deterministic_fallback"
    assert result["adjustments"] == []
    assert len(result["periodization"]) >= 3
    assert len(result["first_four_weeks"]) == 4
    assert result["first_four_weeks"][3]["recovery_week"] is True
    assert result["first_four_weeks"][0]["workouts"]
    assert result["planning_trace"][0]["stage"] == "deterministic_fallback"


def test_fallback_builds_structured_prescriptions_and_varied_key_stimuli():
    facts = _facts()
    result = ProfessionalSchemePlanner().plan(facts, TrainingLoadEnvelope.calculate(facts))
    workouts = [
        workout for week in result["first_four_weeks"] for workout in week["workouts"]
        if workout["type"] != "rest"
    ]

    assert all(workout["training_prescription"]["blocks"] for workout in workouts)
    assert all(
        workout["training_prescription"]["targets"]["feel"]["label"]
        for workout in workouts
    )
    assert all(
        workout["training_prescription"]["targets"]["pace"]["status"] == "unavailable"
        for workout in workouts
    )
    assert all(
        workout["training_prescription"]["intensity_zone"] in {1, 2, 3, 4, 5}
        for workout in workouts
    )
    key_titles = {
        workout["title"] for workout in workouts if workout.get("is_key") and workout["type"] == "quality"
    }
    assert {"变速耐力跑", "阈值分段跑", "间歇耐受跑"}.issubset(key_titles)


def test_fallback_prefers_long_run_on_available_weekend_day():
    facts = _facts(constraints={"available_days": [0, 2, 4, 5]})
    result = ProfessionalSchemePlanner().plan(facts, TrainingLoadEnvelope.calculate(facts))

    long_workout = next(
        workout for workout in result["first_four_weeks"][0]["workouts"]
        if workout["type"] == "long"
    )
    assert long_workout["weekday"] == 5


def test_normalizer_moves_quality_session_after_recent_heavy_training():
    facts = _facts(short_term_training={
        "status": "sufficient",
        "activities": [{
            "date": "2026-08-04", "distance_km": 30,
            "is_long": True, "is_quality": False,
        }],
        "constraints": {"avoid_quality_after_heavy": True},
    })
    # Use a Wednesday as the plan reference date so the generated quality slot
    # (weekday=2) is exactly the day after the recent long run.
    facts["as_of"] = "2026-08-05"
    candidate = ProfessionalSchemePlanner().plan(facts, TrainingLoadEnvelope.calculate(facts))
    envelope = TrainingLoadEnvelope.calculate(facts)
    quality = next(
        item for item in candidate["first_four_weeks"][0]["workouts"]
        if item["weekday"] == 2
    )
    quality.update({"type": "quality", "title": "间歇跑", "intensity_zone": 4})
    quality["training_prescription"]["intensity_zone"] = 4

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(candidate, facts, envelope)
    workout = next(
        item for item in normalized["first_four_weeks"][0]["workouts"]
        if item["weekday"] == 2
    )

    assert workout["type"] == "easy"
    assert workout["title"] == "衔接恢复跑"
    assert workout["intensity_intent"]["zone"] == 2
    assert any("长跑或质量课后次日不安排强度" in item for item in trace["adjustments"])


def test_periodization_order_is_reviewed_without_blocking_candidate():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["periodization"] = [
        {"name": "专项强度期", "weeks": 2},
        {"name": "基础期", "weeks": 2},
        {"name": "高峰期", "weeks": 1},
        {"name": "减量期", "weeks": 1},
    ]

    normalized, _ = TrainingSchemeCandidateNormalizer.normalize(candidate, facts, envelope)

    assert normalized["review"]["status"] == "attention"
    assert any(
        item.get("code") == "PERIODIZATION_ORDER"
        for item in normalized["review"]["items"]
    )
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_fallback_key_sessions_have_executable_repeat_steps():
    facts = _facts()
    result = ProfessionalSchemePlanner().plan(facts, TrainingLoadEnvelope.calculate(facts))
    quality = next(
        workout
        for week in result["first_four_weeks"]
        for workout in week["workouts"]
        if workout["type"] == "quality"
    )
    prescription = quality["training_prescription"]
    repeat = next(step for step in prescription["steps"] if step["kind"] == "repeat")
    children = {step["role"]: step for step in repeat["children"]}

    assert prescription["structure_version"] == 2
    assert repeat["repeat_count"] >= 2
    assert children["work"]["dose"]["value"] > 0
    assert children["recovery"]["dose"]["value"] > 0
    assert children["work"]["intensity_intent"]["zone"] in {4, 5}
    assert children["recovery"]["intensity_intent"]["zone"] == 1
    assert "快段" in next(
        block["instruction"] for block in prescription["blocks"]
        if block["role"] == "main"
    )
    assert "交替" not in str(prescription["blocks"])


def test_validator_rejects_v1_or_incomplete_repeat_in_new_candidate():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = next(
        item for item in candidate["first_four_weeks"][0]["workouts"]
        if item["type"] == "quality"
    )
    workout["training_prescription"].pop("steps")
    workout["training_prescription"]["structure_version"] = 1

    with pytest.raises(TrainingSchemeValidationError, match="Workout Steps v2"):
        TrainingSchemeValidator.validate(candidate, facts, envelope)

    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = next(
        item for item in candidate["first_four_weeks"][0]["workouts"]
        if item["type"] == "quality"
    )
    repeat = next(
        step for step in workout["training_prescription"]["steps"]
        if step["kind"] == "repeat"
    )
    repeat["children"] = [
        child for child in repeat["children"] if child["role"] != "recovery"
    ]

    with pytest.raises(TrainingSchemeValidationError, match="工作段和恢复段"):
        TrainingSchemeValidator.validate(candidate, facts, envelope)


def test_normalizer_materializes_missing_ai_workout_steps():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    prescription = candidate["first_four_weeks"][0]["workouts"][0]["training_prescription"]
    prescription.pop("steps")
    prescription.pop("structure_version")

    normalized, _ = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )

    prescription = normalized["first_four_weeks"][0]["workouts"][0]["training_prescription"]
    assert prescription["structure_version"] == 2
    assert prescription["steps"]
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_normalizer_materializes_missing_v2_completion_criteria_without_second_ai_call():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = candidate["first_four_weeks"][0]["workouts"][0]
    prescription = workout["training_prescription"]
    assert prescription["structure_version"] == 2
    prescription.pop("completion_criteria")

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )

    completion = normalized["first_four_weeks"][0]["workouts"][0][
        "training_prescription"
    ]["completion_criteria"]
    assert completion["classification"] == [
        "completed", "partially_completed", "stopped",
    ]
    assert completion["quality_rule"]
    assert any("补齐完成标准" in item for item in trace["adjustments"])
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_normalizer_converts_legacy_completion_list_for_existing_v2_steps():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = candidate["first_four_weeks"][0]["workouts"][0]
    workout["training_prescription"]["completion_criteria"] = ["完成主体即可"]

    normalized, _ = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )
    completion = normalized["first_four_weeks"][0]["workouts"][0][
        "training_prescription"
    ]["completion_criteria"]
    assert isinstance(completion, dict)
    assert "completed" in completion["classification"]
    assert "partially_completed" in completion["classification"]
    assert "stopped" in completion["classification"]


def test_normalizer_accepts_compact_workout_intent_and_uses_it_for_steps():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = candidate["first_four_weeks"][0]["workouts"][0]
    workout.pop("training_prescription", None)
    workout.update({
        "type": "quality",
        "title": "间歇跑",
        "stimuli": ["high_intensity_tolerance"],
        "intensity_zone": 5,
        "intensity_intent": {"zone": 5, "preferred_metric": "pace"},
    })

    normalized, _ = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )
    prescription = normalized["first_four_weeks"][0]["workouts"][0]["training_prescription"]
    assert prescription["intensity_zone"] == 5
    assert any(step.get("kind") == "repeat" for step in prescription["steps"])


def test_normalizer_expands_two_week_ai_candidate_to_four_week_read_model():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["first_four_weeks"] = candidate["first_four_weeks"][:2]
    candidate["first_four_weeks"][0].pop("training_prescription", None)
    candidate["first_four_weeks"][1].pop("training_prescription", None)

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )

    weeks = normalized["first_four_weeks"]
    assert [week["week"] for week in weeks] == [1, 2, 3, 4]
    assert weeks[3]["recovery_week"] is weeks[1]["recovery_week"]
    assert any("AI 仅生成当前周和下一周" in item for item in trace["adjustments"])
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_validator_rejects_invalid_five_level_intensity_zone():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["first_four_weeks"][0]["workouts"][0]["training_prescription"]["intensity_zone"] = 6

    with pytest.raises(TrainingSchemeValidationError, match="Z1–Z5"):
        TrainingSchemeValidator.validate(candidate, facts, envelope)


def test_validator_allows_precise_pace_for_ai_review_without_personal_intensity_facts():
    facts = _facts()
    candidate = ProfessionalSchemePlanner().plan(facts, TrainingLoadEnvelope.calculate(facts))
    candidate["first_four_weeks"][0]["workouts"][0]["training_prescription"]["targets"]["pace"] = {
        "status": "available", "range": "4:30–4:40 /km", "basis": "未验证",
    }

    assert TrainingSchemeValidator.validate(
        candidate, facts, TrainingLoadEnvelope.calculate(facts),
    )["first_four_weeks"]


def test_easy_prescription_requires_review_when_distance_time_imply_too_fast_a_pace():
    prescription = ensure_training_prescription({
        "type": "easy", "title": "Easy Run", "distance_km": 13.7,
        "duration_minutes": 60,
    }, pace_reference_sec_per_km=322)["training_prescription"]

    assert prescription["pacing_guard"]["status"] == "requires_review"
    assert prescription["pacing_guard"]["implied_pace_sec_per_km"] == 263
    assert "4:23/km" in prescription["pacing_guard"]["message"]


def test_validator_keeps_easy_course_pace_mismatch_as_non_blocking_review():
    facts = _facts(athlete_profile={"recent_running_pace_sec_per_km": 322})
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    workout = next(
        item for item in candidate["first_four_weeks"][0]["workouts"]
        if item["type"] == "easy"
    )
    workout["training_prescription"]["pacing_guard"] = {
        "status": "requires_review",
        "message": "轻松跑配速需要复核",
    }

    result = TrainingSchemeValidator.validate(candidate, facts, envelope)
    assert result["first_four_weeks"]


def test_validator_allows_unsafe_weekly_growth_for_ai_review():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    unsafe = copy.deepcopy(candidate)
    unsafe["first_four_weeks"][1]["target_km"] = round(
        unsafe["first_four_weeks"][0]["target_km"] * 1.5, 1,
    )

    result = TrainingSchemeValidator.validate(unsafe, facts, envelope)
    assert result["first_four_weeks"][1]["target_km"] > (
        result["first_four_weeks"][0]["target_km"] * 1.4
    )


def test_normalizer_reconciles_weekly_totals_without_blocking_quality_review():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    week = candidate["first_four_weeks"][0]
    target = float(week["target_km"])
    original_semantics = [
        (item["weekday"], item["type"], item["title"])
        for item in week["workouts"]
    ]
    for workout in week["workouts"]:
        workout["distance_km"] = round(target * 0.15, 1)
        if workout["type"] == "long":
            workout["distance_km"] = round(target * 0.50, 1)
    candidate["first_four_weeks"][1]["target_km"] = round(target * 1.35, 1)

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )
    normalized_week = normalized["first_four_weeks"][0]
    normalized_long = next(item for item in normalized_week["workouts"] if item["type"] == "long")

    assert sum(item["distance_km"] for item in normalized_week["workouts"]) == pytest.approx(target)
    assert normalized_long["distance_km"] > target * 0.38
    assert [
        (item["weekday"], item["type"], item["title"])
        for item in normalized_week["workouts"]
    ] == original_semantics
    assert trace["stage"] == "deterministic_normalization"
    assert trace["adjusted_weeks"] == []
    assert normalized_week["reconciliation"]["status"] == "adjusted"
    assert normalized_week["target_load"] == pytest.approx(
        sum(item["planned_load"] for item in normalized_week["workouts"] if item["type"] != "rest"),
    )
    assert normalized["first_four_weeks"][1]["target_km"] == pytest.approx(target * 1.35, abs=0.1)
    assert normalized["load_progression"][1]["target_km"] == (
        normalized["first_four_weeks"][1]["target_km"]
    )
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_normalizer_preserves_ai_periodization_for_review():
    facts = _facts(goal={"target_date": str(date.today() + timedelta(weeks=11))})
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["periodization"] = [
        {"name": "基础期", "weeks": 5, "purpose": "建立基础"},
        {"name": "专项期", "weeks": 4, "purpose": "专项适应"},
        {"name": "高峰期", "weeks": 2, "purpose": "关键模拟"},
        {"name": "减量期", "weeks": 2, "purpose": "降低疲劳"},
    ]
    original_names = [phase["name"] for phase in candidate["periodization"]]

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(
        candidate, facts, envelope,
    )

    assert sum(phase["weeks"] for phase in normalized["periodization"]) == 13
    assert [phase["name"] for phase in normalized["periodization"]] == original_names
    assert normalized["periodization"][-1]["weeks"] == 2
    assert trace["adjusted_periodization"] is False
    assert not any("阶段总周期" in item for item in trace["adjustments"])
    TrainingSchemeValidator.validate(normalized, facts, envelope)


def test_validator_allows_periodization_mismatch_for_ai_review():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["periodization"][0]["weeks"] += 1

    assert TrainingSchemeValidator.validate(candidate, facts, envelope)["periodization"]


def test_validator_allows_quality_session_with_medical_context_for_safety_review():
    facts = _facts(constraints={"medical_limitations": "近期右膝疼痛"})
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["first_four_weeks"][0]["workouts"][0]["type"] = "quality"

    assert TrainingSchemeValidator.validate(candidate, facts, envelope)["first_four_weeks"]


def test_validator_preserves_ai_review_and_safety_hold_without_blocking():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    candidate["review"] = {
        "status": "attention",
        "items": [{"code": "TAPER_SHORT", "severity": "medium", "reason": "减量期偏短", "suggestion": "最后一周降低训练量"}],
        "safety_hold": {"code": "PAIN_REPORTED", "reason": "需要确认是否降低强度"},
    }

    result = TrainingSchemeValidator.validate(candidate, facts, envelope)

    assert result["review"]["status"] == "attention"
    assert result["review"]["items"][0]["code"] == "TAPER_SHORT"
    assert result["review"]["safety_hold"]["code"] == "PAIN_REPORTED"


def test_medical_limitations_remove_quality_sessions_from_fallback():
    facts = _facts(constraints={"medical_limitations": "近期右膝疼痛"})
    result = ProfessionalSchemePlanner().plan(
        facts, TrainingLoadEnvelope.calculate(facts),
    )

    types = {
        workout["type"]
        for week in result["first_four_weeks"]
        for workout in week["workouts"]
    }
    assert "quality" not in types
    assert "medical_limitations" in result["risk_flags"]


class FakeRunner:
    def run(self, skill_name, context):
        fallback = ProfessionalSchemePlanner()._fallback(
            context.facts["planning_fact_pack"],
            context.facts["training_load_envelope"],
            reason="fixture",
        )
        fallback.pop("generation_mode", None)
        fallback.pop("fallback_reason", None)
        fallback.pop("planning_trace", None)
        return fallback, context.with_trace({"skill": skill_name, "version": "1.0.0"})


def test_skill_candidate_passes_validator_and_records_trace():
    facts = _facts()
    result = ProfessionalSchemePlanner(runner=FakeRunner()).plan(
        facts, TrainingLoadEnvelope.calculate(facts),
    )

    assert result["generation_mode"] == "skill"
    assert result["fallback_reason"] is None
    assert result["inference_source"] == "ai"
    assert result["validation_status"] == "passed"
    assert result["recommendation_status"] == "challenging"
    assert result["decision_status"] == "ai_risk_advisory"
    assert result["adjustments"] == []
    assert result["planning_trace"][-2]["skill"] == "draft-training-scheme"
    assert result["planning_trace"][-1]["stage"] == "deterministic_normalization"


def test_case_driven_dag_calls_framework_then_near_term_and_reuses_framework():
    captured = []
    facts = _facts(recovery_snapshot={})
    envelope = TrainingLoadEnvelope.calculate(facts)

    class DAGRunnner(CoachSkillRunner):
        def __init__(self):
            pass

        def run(self, skill_name, context):
            captured.append((skill_name, context.to_payload()))
            base = ProfessionalSchemePlanner()._fallback(
                facts,
                envelope,
                reason="fixture",
            )
            if skill_name == "build-training-framework":
                return {
                    "feasibility": base["feasibility"],
                    "ability_summary": {"weekly_pattern": "近四周稳定"},
                    "goal_demand_summary": {"marathon": "需要专项耐力"},
                    "ability_gap": ["后程耐力"],
                    "recommended_entry_phase": "基础期",
                    "entry_rationale": "先吸收训练",
                    "periodization": base["periodization"],
                    "load_progression": base["load_progression"],
                    "weekly_principles": {"long_run": "周末优先"},
                    "method_basis": ["五级强度模型"],
                    "assumptions": [], "uncertainties": [], "risk_flags": [],
                    "user_explanation": "先建立专项耐力。",
                }, context.with_trace({"skill": skill_name, "stage": "training_framework"})
            return {
                "first_two_weeks": [copy.deepcopy(week) for week in base["first_four_weeks"][:2]],
                "review": {"status": "ok", "items": [], "safety_hold": None},
                "entry_review": {
                    "recommended_entry_phase": "基础期", "decision": "hold", "confidence": 0.8,
                    "evidence": ["恢复数据不足"], "adjustments": [], "applied": False,
                    "applied_adjustments": [], "handoff": {"status": "review_pending", "requires_user_confirmation": True},
                },
                "assumptions": [], "uncertainties": [], "risk_flags": [], "data_gaps": ["缺少睡眠"],
                "user_explanation": "先执行并观察恢复。",
            }, context.with_trace({"skill": skill_name, "stage": "near_term_schedule"})

    result = ProfessionalSchemePlanner(runner=DAGRunnner()).plan(
        facts, envelope,
    )

    assert result["generation_mode"] == "skill"
    assert result["framework_id"]
    assert result["degraded"] is True
    assert "缺少睡眠" in result["data_gaps"]
    assert len(result["first_four_weeks"]) == 4
    assert [item["skill"] for item in result["planning_trace"][:2]] == [
        "build-training-framework", "build-near-term-schedule",
    ]
    assert "framework_context" in captured[0][1]["facts"]
    assert "planning_fact_pack" not in captured[0][1]["facts"]
    assert "near_term_context" in captured[1][1]["facts"]
    assert captured[1][1]["facts"]["training_load_envelope"] == envelope
    assert "planning_fact_pack" not in captured[1][1]["facts"]
    # 近期活动仅由 compact recent_days 传入；短期窗口不重复附带活动列表。
    near_term = captured[1][1]["facts"]["near_term_context"]
    assert "short_term_training" not in near_term["current_state"]
    assert "recent_training_window" in near_term["current_state"]
    assert "activities" not in near_term["current_state"]["recent_training_window"]


def test_supporting_summaries_drop_redundant_recent_two_days():
    facts = _facts(short_term_training={
        "status": "sufficient",
        "window_start": "2026-08-08",
        "window_end": "2026-08-09",
        "activities": [{"date": "2026-08-09", "distance_meters": 26000}],
    })
    supporting = ProfessionalSchemePlanner._supporting_summaries(facts)

    assert "recent_two_days" not in supporting
    current = supporting["athlete_current_state"]
    assert current["short_term_training"]["activities"] == [
        {"date": "2026-08-09", "distance_meters": 26000},
    ]


def test_schedule_current_state_keeps_recent_sessions_without_duplicate_activities():
    current = {
        "recent_days": [
            {"date": "2026-08-07", "sessions": [{"session_role": "easy", "metrics": {"ignored": 1}}]},
            {"date": "2026-08-08", "sessions": [{"session_role": "quality", "analysis": {"primary_type": "interval"}}]},
            {"date": "2026-08-09", "sessions": [{"session_role": "long", "distance_km": 28}]},
            {"date": "2026-08-10", "sessions": [{"session_role": "recovery", "distance_km": 8}]},
        ],
        "short_term_training": {
            "status": "sufficient", "window_start": "2026-08-09", "window_end": "2026-08-10",
            "activities": [{"date": "2026-08-10", "distance_meters": 8000}],
        },
        "recovery": {"score": 81}, "uncertainties": ["sleep_missing"],
    }

    compact = ProfessionalSchemePlanner._compact_schedule_current_state(current)

    assert [item["date"] for item in compact["recent_days"]] == ["2026-08-09", "2026-08-10"]
    assert compact["recent_days"][0]["sessions"] == [
        {"session_role": "long", "distance_km": 28},
    ]
    assert compact["recent_training_window"] == {
        "status": "sufficient", "window_start": "2026-08-09", "window_end": "2026-08-10",
    }
    assert compact["recovery"] == {"score": 81}


def test_compact_training_summary_dedups_repeated_week_gaps():
    def gap(field):
        return {"field": field, "reason": "missing", "affects": f"无法使用{field}"}

    summary = {
        "schema_version": "training-day-summary-v1",
        "status": "sufficient",
        "reference_window": {"kind": "previous_complete_natural_weeks", "week_count": 1},
        "reference_weeks": [{
            "window_start": "2026-08-03", "window_end": "2026-08-09",
            "day_count": 7, "training_days": 5, "running_distance_km": 80,
            "primary_type_counts": {"tempo": 2}, "feature_counts": {"session_tempo": 2},
            "data_quality": {"status": "sufficient"},
            # 4 种缺口 × 7 天聚合 → 28 条，实际只有 4 种唯一缺口
            "gaps": [gap(f) for _ in range(7) for f in
                     ("activity_coverage", "sleep", "recovery", "training_load")],
        }],
        "recent_days": [],
    }
    compact = ProfessionalSchemePlanner._compact_training_summary(summary)
    week = compact["reference_weeks"][0]

    assert [item["field"] for item in week["gaps"]] == [
        "activity_coverage", "sleep", "recovery", "training_load",
    ]
    assert len(week["gaps"]) == 4
    # 其余周聚合字段不受影响
    assert week["running_distance_km"] == 80
    assert week["primary_type_counts"] == {"tempo": 2}


def test_compact_supporting_summaries_keep_recent_metrics_without_raw_activity_payload():
    facts = _facts()
    facts["athlete_profile"] = {
        "personal_info": {"height_cm": 175, "weight_kg": 55},
        "personal_bests": {"marathon": "2:32:49"},
        "private_notes": "x" * 12000,
    }
    facts["short_term_training"] = {
        "status": "sufficient",
        "window_start": "2026-08-08",
        "window_end": "2026-08-09",
        "activities": [{
            "activity_id": "a1", "activity_date": "2026-08-09",
            "activity_name": "长距离跑", "distance_meters": 26000,
            "duration_seconds": 9000, "avg_heart_rate": 150,
            "raw_splits": "x" * 12000,
            "training_analysis": {"primary_type": "long", "confidence": 0.9},
        }],
    }
    supporting = ProfessionalSchemePlanner._supporting_summaries(facts)

    serialized = json.dumps(supporting, ensure_ascii=False)
    assert "private_notes" not in serialized
    assert "raw_splits" not in serialized
    assert "长距离跑" in serialized
    assert "avg_heart_rate" in serialized
    assert len(serialized.encode("utf-8")) < 12000


class FakeRevisionRunner:
    def run(self, skill_name, context):
        assert skill_name == "revise-training-scheme"
        fallback = ProfessionalSchemePlanner()._fallback(
            context.facts["planning_fact_pack"],
            context.facts["training_load_envelope"],
            reason="fixture",
        )
        fallback.pop("generation_mode", None)
        fallback.pop("fallback_reason", None)
        fallback.pop("planning_trace", None)
        return fallback, context.with_trace({"skill": skill_name, "version": "1.0.0"})


def test_scheme_revision_uses_dedicated_skill_and_validator():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)

    result = ProfessionalSchemePlanner(runner=FakeRevisionRunner()).revise(
        facts,
        envelope,
        execution_summary={"completed_sessions": 1, "planned_sessions": 4},
        active_scheme={"plan_id": "plan-1", "version": 2},
        recovery_snapshot={"recovery": {"overall_score": 65}},
    )

    assert result["generation_mode"] == "skill"
    assert result["decision_status"] == "ai_risk_advisory"
    assert result["planning_trace"][-2]["skill"] == "revise-training-scheme"
    assert result["planning_trace"][-1]["stage"] == "deterministic_normalization"


class FeasibleRepairRunner(FakeRunner):
    def run(self, skill_name, context):
        candidate, final_context = super().run(skill_name, context)
        candidate["feasibility"]["level"] = "feasible"
        candidate["periodization"][0]["weeks"] += 2
        return candidate, final_context


def test_ai_candidate_keeps_source_without_local_quality_repair():
    facts = _facts()
    result = ProfessionalSchemePlanner(runner=FeasibleRepairRunner()).plan(
        facts, TrainingLoadEnvelope.calculate(facts),
    )

    assert result["generation_mode"] == "skill"
    assert result["inference_source"] == "ai"
    assert result["validation_status"] == "passed"
    assert result["recommendation_status"] == "feasible"
    assert result["decision_status"] == "ai_validated"
    assert result["adjustments"] == []
    assert sum(phase["weeks"] for phase in result["periodization"]) == 18


def test_reconcile_estimates_duration_only_workouts_in_weekly_total():
    """定时跑（无距离）按个人配速估算距离后参与周量对齐，避免周目标全摊给距离型课程。"""
    facts = _facts(athlete_profile={"recent_running_pace_sec_per_km": 330})
    envelope = TrainingLoadEnvelope.calculate(facts)
    candidate = ProfessionalSchemePlanner().plan(facts, envelope)
    week = candidate["first_four_weeks"][0]
    target = float(week["target_km"])
    active = [w for w in week["workouts"] if str(w.get("type") or "") != "rest"]
    # 把一个课程改成定时跑：只给时长，无距离
    duration_only = active[0]
    duration_only.pop("distance_km", None)
    duration_only["duration_minutes"] = 60
    duration_only["intensity_zone"] = 4
    for w in active[1:]:
        w["distance_km"] = round(target * 0.15, 1)

    normalized, trace = TrainingSchemeCandidateNormalizer.normalize(candidate, facts, envelope)
    normalized_week = normalized["first_four_weeks"][0]
    estimated = next(w for w in normalized_week["workouts"] if w.get("distance_estimated"))
    assert estimated["distance_km"] > 0
    assert estimated["duration_minutes"] == 60
    total = sum(w.get("distance_km") or 0 for w in normalized_week["workouts"])
    assert total == pytest.approx(target)
    # 定时跑换算的文案须是用户视角执行导向（时长为主、距离为参考）
    trace_adj = "；".join(str(x) for x in (trace.get("adjustments") or []))
    assert "按你的配速换算" in trace_adj
    assert "实际按时长跑" in trace_adj
    # 定时跑参与对齐后，距离型课程不再被过度放大
    max_km = max(w.get("distance_km") or 0 for w in normalized_week["workouts"])
    assert max_km <= target * 0.5
    # 估算被记录进调整说明
    assert any("定时跑" in item or "估算" in item for item in normalized_week["reconciliation"]["adjustments"])


class _AuditDagRunner(CoachSkillRunner):
    """DAG mock：framework/near_term 用确定性兜底，audit 可编程。"""

    def __init__(self, audit_responses=None, audit_error=False):
        self.calls = []
        self.audit_responses = list(audit_responses or [])
        self.audit_error = audit_error

    def run(self, skill_name, context):
        self.calls.append(skill_name)
        if skill_name == "audit-training-scheme":
            if self.audit_error:
                raise SkillRunError("audit provider unavailable")
            if self.audit_responses:
                response = self.audit_responses.pop(0)
            else:
                response = {"verdict": "pass", "summary": "课表可执行", "issues": []}
            return response, context.with_trace({"skill": skill_name})
        base = ProfessionalSchemePlanner()._fallback(
            _facts(), TrainingLoadEnvelope.calculate(_facts()), reason="fixture",
        )
        if skill_name == "build-training-framework":
            return {
                "feasibility": base["feasibility"],
                "ability_summary": {"weekly_pattern": "近四周稳定"},
                "goal_demand_summary": {"marathon": "需要专项耐力"},
                "ability_gap": ["后程耐力"],
                "recommended_entry_phase": "基础期",
                "entry_rationale": "先吸收训练",
                "periodization": base["periodization"],
                "load_progression": base["load_progression"],
                "weekly_principles": {"long_run": "周末优先"},
                "method_basis": ["五级强度模型"],
                "assumptions": [], "uncertainties": [], "risk_flags": [],
                "user_explanation": "先建立专项耐力。",
            }, context.with_trace({"skill": skill_name})
        weeks = [copy.deepcopy(week) for week in base["first_four_weeks"][:2]]
        # 长距离设为 25km（> 基础期全马×50% ≈ 21.1km），确保 long_run 审计被确定性复核确认
        for week in weeks:
            for w in week.get("workouts") or []:
                if str(w.get("type") or "").lower() in ("long", "long_run"):
                    w["distance_km"] = 25.0
        return {
            "first_two_weeks": weeks,
            "review": {"status": "ok", "items": [], "safety_hold": None},
            "entry_review": {"recommended_entry_phase": "基础期", "decision": "hold",
                             "confidence": 0.8, "evidence": [], "adjustments": [],
                             "applied": False, "applied_adjustments": [],
                             "handoff": {"status": "review_pending",
                                         "requires_user_confirmation": True}},
            "assumptions": [], "uncertainties": [], "risk_flags": [],
            "data_gaps": ["缺少睡眠"], "user_explanation": "先执行并观察恢复。",
        }, context.with_trace({"skill": skill_name})


def test_audit_pass_keeps_single_generation():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    runner = _AuditDagRunner(audit_responses=[{"verdict": "pass", "summary": "可执行", "issues": []}])
    result = ProfessionalSchemePlanner(runner=runner).plan(facts, envelope)

    assert result["audit"]["status"] == "passed"
    assert result["audit"]["verdict"] == "pass"
    assert runner.calls.count("build-near-term-schedule") == 1
    assert runner.calls.count("audit-training-scheme") == 1


def test_audit_fail_triggers_rebuild_until_pass():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    runner = _AuditDagRunner(audit_responses=[
        {"verdict": "fail", "summary": "长距离失控",
         "issues": [{"code": "long_run_exceeds_limit", "severity": "critical",
                     "message": "长距离超限", "recommendation": "收敛"}]},
        {"verdict": "pass", "summary": "已修正", "issues": []},
    ])
    result = ProfessionalSchemePlanner(runner=runner).plan(facts, envelope)

    assert result["audit"]["status"] == "passed"
    # 初审 fail → 打回重生成 near_term（第二次）→ 再审计 pass
    assert runner.calls.count("build-near-term-schedule") == 2
    assert runner.calls.count("audit-training-scheme") == 2


def test_audit_fail_after_two_rebuilds_marks_failed():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    runner = _AuditDagRunner(audit_responses=[
        {"verdict": "fail", "summary": "x", "issues": [{"code": "a", "severity": "critical",
                                                        "message": "m", "recommendation": "r"}]},
        {"verdict": "fail", "summary": "y", "issues": [{"code": "b", "severity": "critical",
                                                        "message": "m", "recommendation": "r"}]},
        {"verdict": "fail", "summary": "z", "issues": [{"code": "c", "severity": "critical",
                                                        "message": "m", "recommendation": "r"}]},
    ])
    result = ProfessionalSchemePlanner(runner=runner).plan(facts, envelope)

    assert result["audit"]["status"] == "failed"
    assert result["audit"]["verdict"] == "fail"
    # 初 + 2 轮打回 = 3 次 near_term，3 次审计
    assert runner.calls.count("build-near-term-schedule") == 3
    assert runner.calls.count("audit-training-scheme") == 3


def test_audit_unavailable_does_not_block_draft():
    facts = _facts()
    envelope = TrainingLoadEnvelope.calculate(facts)
    runner = _AuditDagRunner(audit_error=True)
    result = ProfessionalSchemePlanner(runner=runner).plan(facts, envelope)

    assert result["audit"]["status"] == "unavailable"
    assert result["generation_mode"] == "skill"
    assert runner.calls.count("audit-training-scheme") == 1


def test_scheme_audit_validator_normalizes_verdict_and_issues():
    from src.coach_runtime.schemas import validate_output

    failed = validate_output("SchemeAudit", {
        "verdict": "pass",  # 有 critical 应强制 fail
        "summary": "长距离失控",
        "issues": [
            {"code": "long_run_exceeds_limit", "severity": "critical",
             "message": "周日 34.5km 超限", "recommendation": "收敛至 26-30km"},
            {"code": "weekday_session_too_long", "severity": "warning",
             "message": "周三 20km 约 100 分钟", "recommendation": "留意"},
        ],
    })
    assert failed["verdict"] == "fail"
    assert len(failed["issues"]) == 2

    passed = validate_output("SchemeAudit", {
        "verdict": "fail",  # 只有 warning 应归一化为 pass
        "summary": "仅提示",
        "issues": [{"code": "w", "severity": "warning", "message": "x", "recommendation": "y"}],
    })
    assert passed["verdict"] == "pass"


def test_parse_fixed_unavailable_and_effective_days():
    from src.training_planning import _effective_available_days, _parse_fixed_unavailable

    assert _parse_fixed_unavailable("周三晚") == {2}
    assert _parse_fixed_unavailable("每周二、周四") == {1, 3}
    assert _parse_fixed_unavailable("周末上午") == {5, 6}
    assert _parse_fixed_unavailable("") == set()
    assert _parse_fixed_unavailable(None) == set()

    days = _effective_available_days({
        "available_days": [0, 1, 2, 3, 5, 6], "fixed_unavailable_days": [2],
    })
    assert days == [0, 1, 3, 5, 6]
    # 全部被排除时退回原列表，避免空排期
    assert _effective_available_days(
        {"available_days": [2], "fixed_unavailable_days": [2]},
    ) == [2]


def test_deterministic_schedule_skips_fixed_unavailable_day():
    from src.training_planning import ProfessionalSchemePlanner

    facts = _facts()
    facts["constraints"] = {
        **facts.get("constraints", {}),
        "available_days": [0, 1, 2, 3, 5, 6],
        "fixed_unavailable_days": [2],  # 周三不可训练
    }
    envelope = TrainingLoadEnvelope.calculate(facts)
    workouts = ProfessionalSchemePlanner()._week_workouts(
        facts, envelope, target_km=40.0, recovery=False, week_number=1,
    )
    # _week_workouts 只返回有课日；固定不可训练日（周三=2）不得出现
    assert workouts, "确定性排期应生成课程"
    assert all(w.get("weekday") != 2 for w in workouts)


def test_deterministic_audit_recheck_downgrades_false_critical():
    """AI 审计误判的 critical 被确定性复核降级为 warning，不触发打回。"""
    from src.training_planning import (
        _confirm_long_run_exceed,
        _confirm_quality_back_to_back,
        _confirm_weekday_too_long,
    )

    facts = _facts(athlete_profile={"recent_running_pace_sec_per_km": 330})
    # 基础期，长距离 21km < 全马×50%（21.1km）→ 不确认
    result = {
        "periodization": [{"name": "基础期", "weeks": 4}],
        "first_two_weeks": [{"week": 1, "workouts": [
            {"weekday": 6, "type": "long_run", "pace_intent": "long_run", "distance_km": 21.0},
        ]}],
    }
    assert _confirm_long_run_exceed(result, facts) is False
    # 25km 真超限 → 确认
    result["first_two_weeks"][0]["workouts"][0]["distance_km"] = 25.0
    assert _confirm_long_run_exceed(result, facts) is True

    # 周中 130 分钟 → 确认；supplement 明确允许 → 豁免
    weekday_result = {"first_two_weeks": [{"week": 1, "workouts": [
        {"weekday": 1, "type": "easy", "duration_minutes": 130},
    ]}]}
    assert _confirm_weekday_too_long(weekday_result, facts) is True
    facts_exempt = _facts(athlete_profile={"recent_running_pace_sec_per_km": 330})
    facts_exempt["supplement"] = {"additional_context": "工作日晚上可跑 2 小时"}
    assert _confirm_weekday_too_long(weekday_result, facts_exempt) is False

    # 间歇后次日 10km 轻松跑 → 不确认（标准恢复跑）；真连排 → 确认
    recovery_result = {"first_two_weeks": [{"week": 1, "workouts": [
        {"weekday": 3, "type": "interval", "pace_intent": "interval", "distance_km": 10.0},
        {"weekday": 4, "type": "easy", "pace_intent": "easy", "distance_km": 10.0},
    ]}]}
    assert _confirm_quality_back_to_back(recovery_result, facts) is False
    back_to_back = {"first_two_weeks": [{"week": 1, "workouts": [
        {"weekday": 3, "type": "interval", "pace_intent": "interval", "distance_km": 10.0},
        {"weekday": 4, "type": "tempo", "pace_intent": "tempo", "distance_km": 12.0},
    ]}]}
    assert _confirm_quality_back_to_back(back_to_back, facts) is True


def test_verify_audit_critical_downgrades_and_preserves():
    """_verify_audit_critical：误判 critical 降级 warning，确认的保留 critical。"""
    facts = _facts(athlete_profile={"recent_running_pace_sec_per_km": 330})
    planner = ProfessionalSchemePlanner()
    result = {
        "periodization": [{"name": "基础期", "weeks": 4}],
        "first_two_weeks": [{"week": 1, "workouts": [
            {"weekday": 6, "type": "long_run", "pace_intent": "long_run", "distance_km": 21.0},
            {"weekday": 3, "type": "interval", "pace_intent": "interval", "distance_km": 10.0},
            {"weekday": 4, "type": "easy", "pace_intent": "easy", "distance_km": 10.0},
        ]}],
    }
    audit = {
        "verdict": "fail", "summary": "AI 判定不通过",
        "issues": [
            {"code": "long_run_exceeds_limit", "severity": "critical",
             "message": "周日 21km 超过全马 50%", "recommendation": "收敛"},
            {"code": "quality_back_to_back", "severity": "critical",
             "message": "间歇后 10km 轻松跑恢复不足", "recommendation": "缩短"},
        ],
    }
    verified = planner._verify_audit_critical(facts, result, audit)
    severities = {issue["code"]: issue["severity"] for issue in verified["issues"]}
    assert severities["long_run_exceeds_limit"] == "warning"   # 21 < 21.1 误判
    assert severities["quality_back_to_back"] == "warning"     # 恢复跑误判
    assert "降级为提示" in verified["issues"][0]["message"]

    # 确认的 critical 保留
    result["first_two_weeks"][0]["workouts"][0]["distance_km"] = 25.0
    verified2 = planner._verify_audit_critical(facts, result, audit)
    assert next(i for i in verified2["issues"] if i["code"] == "long_run_exceeds_limit")["severity"] == "critical"


def test_deterministic_audit_catches_ai_missed_issues():
    """AI 审计漏报时，确定性规则主动补抓（用户案例：周中连休 + 周末质量课连排 + 长距离超限）。"""
    facts = _facts(athlete_profile={"recent_running_pace_sec_per_km": 330})
    facts["constraints"]["available_days"] = [0, 1, 2, 3, 4, 5, 6]
    planner = ProfessionalSchemePlanner()
    result = {
        "periodization": [{"name": "基础期", "weeks": 4}],
        "first_two_weeks": [{"week": 1, "workouts": [
            {"weekday": 0, "type": "easy", "pace_intent": "easy", "distance_km": 16.3},
            {"weekday": 1, "type": "threshold", "pace_intent": "threshold", "distance_km": 14.0},
            {"weekday": 2, "type": "easy", "pace_intent": "easy", "distance_km": 19.1},
            {"weekday": 3, "type": "rest"},
            {"weekday": 5, "type": "marathon_pace", "pace_intent": "marathon_pace", "duration_minutes": 70},
            {"weekday": 6, "type": "long_run", "pace_intent": "long_run", "distance_km": 32.7},
        ]}],
    }
    # AI 审计漏报（pass、空 issues）
    audit = {"verdict": "pass", "summary": "课表可执行", "issues": []}
    verified = planner._verify_audit_critical(facts, result, audit)
    codes = {issue["code"]: issue["severity"] for issue in verified["issues"]}
    # 长距离 32.7 > 基础期全马×50%（21.1）→ 补抓 critical
    assert codes.get("long_run_exceeds_limit") == "critical"
    # marathon_pace（质量课）+ 周日 32.7km 长距离连排 → 补抓 critical
    assert codes.get("quality_back_to_back") == "critical"
    # 周中（周三周四）连续两天无训练且可训练 → 补抓 warning
    assert codes.get("weekday_rest_streak") == "warning"
    # 有 critical → 打回
    assert any(issue["severity"] == "critical" for issue in verified["issues"])
