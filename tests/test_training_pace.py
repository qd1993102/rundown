"""个体化配速校准与当天执行覆盖测试。"""

from __future__ import annotations

from datetime import date, timedelta

from src.training_pace import (
    DailyPaceAdjustmentEngine,
    PaceCalibrationProfileBuilder,
)
from src.training_planning import ensure_training_prescription, iter_leaf_workout_steps


def _activity(
    target: date,
    index: int,
    *,
    pace: int,
    primary_type: str = "aerobic",
    terrain: str = "flat",
    confidence: float = 0.86,
    features: dict | None = None,
) -> dict:
    return {
        "activity_id": f"activity-{index}",
        "activity_type": "running",
        "activity_name": "有氧跑" if primary_type == "aerobic" else "质量训练",
        "activity_date": str(target - timedelta(days=index + 1)),
        "distance_meters": 10_000,
        "duration_seconds": pace * 10,
        "training_analysis": {
            "analysis_id": f"analysis-{index}",
            "primary_type": primary_type,
            "terrain": terrain,
            "confidence": confidence,
            "features": features or {},
        },
    }


def _easy_workout() -> dict:
    return {
        "type": "easy",
        "title": "轻松有氧跑",
        "duration_minutes": 50,
        "distance_km": 8,
        "training_prescription": {
            "intensity_zone": 2,
            "targets": {
                "feel": {"label": "能完整对话", "rpe": "2–3"},
            },
        },
    }


def test_profile_prefers_confirmed_threshold_for_z4():
    target = date(2026, 8, 4)

    profile = PaceCalibrationProfileBuilder().build(
        [],
        target=target,
        athlete_profile={"threshold_pace_sec_per_km": 270},
    )

    z4 = profile["zones"]["z4"]
    assert z4["status"] == "available"
    assert z4["source"] == "user_confirmed_threshold"
    assert z4["confidence"] == "high"
    assert z4["min_sec_per_km"] <= 270 <= z4["max_sec_per_km"]


def test_profile_uses_comparable_aerobic_sessions_and_excludes_hills():
    target = date(2026, 8, 4)
    activities = [
        _activity(target, 0, pace=330),
        _activity(target, 1, pace=336),
        _activity(target, 2, pace=342),
        _activity(target, 3, pace=290, terrain="hilly"),
    ]

    profile = PaceCalibrationProfileBuilder().build(activities, target=target)

    z2 = profile["zones"]["z2"]
    assert z2["status"] == "available"
    assert z2["sample_count"] == 3
    assert z2["min_sec_per_km"] > 300
    assert profile["data_quality"]["excluded_reasons"]["terrain_mismatch"] == 1
    assert profile["zones"]["z3"]["status"] == "unavailable"


def test_z5_target_is_bound_to_observed_work_interval_duration():
    target = date(2026, 8, 4)
    activities = []
    for index, pace in enumerate((238, 242, 245)):
        features = {
            "splits": [{
                "index": 1,
                "split_type": "INTERVAL_ACTIVE",
                "distance_m": 750,
                "duration_sec": 180 + index * 15,
                "pace_sec_per_km": pace,
            }],
        }
        activities.append(_activity(
            target,
            index,
            pace=320,
            primary_type="interval",
            features=features,
        ))

    profile = PaceCalibrationProfileBuilder().build(activities, target=target)

    z5 = profile["zones"]["z5"]
    assert z5["status"] == "available"
    assert z5["applies_to"] == "work_interval"
    assert z5["work_duration_seconds"] == {"min": 180, "max": 210}
    assert z5["sample_count"] == 3


def test_daily_adjustment_without_goal_keeps_base_or_slows_and_collapses_explanation():
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )
    engine = DailyPaceAdjustmentEngine()

    ready = engine.apply(
        _easy_workout(),
        profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
    )
    cautious = engine.apply(
        _easy_workout(),
        profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 52}},
    )

    ready_guidance = ready["training_prescription"]["pace_guidance"]
    cautious_guidance = cautious["training_prescription"]["pace_guidance"]
    assert ready_guidance["today_target"]["status"] == "unchanged"
    assert ready_guidance["today_target"]["min_sec_per_km"] >= ready_guidance["base_target"]["min_sec_per_km"]
    assert cautious_guidance["today_target"]["status"] == "slower"
    assert cautious_guidance["today_target"]["min_sec_per_km"] > cautious_guidance["base_target"]["min_sec_per_km"]
    assert cautious_guidance["explanation"]["presentation"] == "collapsed"
    assert cautious_guidance["explanation"]["trigger_label"] == "为什么这样建议"
    assert cautious_guidance["explanation"]["sample_count"] == 3


def test_stable_state_with_goal_allows_small_progression_even_when_acwr_is_high():
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )

    workout = DailyPaceAdjustmentEngine().apply(
        _easy_workout(),
        profile=profile,
        recovery_snapshot={
            "recovery": {"overall_score": 85},
            "training_load": {"acwr_status": "high_risk"},
        },
        goal_context={"target_date": "2026-10-18", "target_time": "02:30:00"},
    )

    guidance = workout["training_prescription"]["pace_guidance"]
    assert guidance["today_target"]["status"] == "progressed"
    assert guidance["today_target"]["min_sec_per_km"] < guidance["base_target"]["min_sec_per_km"]
    assert guidance["goal_progression"] == {"applied": True, "ratio": 0.01}
    assert any("风险提示" in reason for reason in guidance["explanation"]["reasons"])


def test_high_acwr_only_amplifies_an_existing_recovery_or_performance_warning():
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )

    workout = DailyPaceAdjustmentEngine().apply(
        _easy_workout(),
        profile=profile,
        recovery_snapshot={
            "recovery": {"overall_score": 52},
            "training_load": {"acwr_status": "high_risk"},
        },
        goal_context={"target_date": "2026-10-18"},
    )

    guidance = workout["training_prescription"]["pace_guidance"]
    assert guidance["today_target"]["status"] == "slower"
    assert guidance["today_target"]["min_sec_per_km"] == round(
        guidance["base_target"]["min_sec_per_km"] * 1.04,
    )
    assert guidance["goal_progression"]["applied"] is False


def test_low_recovery_removes_numeric_pace_but_keeps_safety_action_visible():
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )

    workout = DailyPaceAdjustmentEngine().apply(
        _easy_workout(),
        profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 35}},
    )

    prescription = workout["training_prescription"]
    guidance = prescription["pace_guidance"]
    assert guidance["today_target"]["status"] == "feel_only"
    assert guidance["safety_action"] == "今天不设数字配速硬目标，按可对话体感完成"
    assert prescription["targets"]["pace"]["status"] == "unavailable"


def test_step_target_resolver_assigns_work_and_recovery_targets_separately():
    target = date(2026, 8, 4)
    activities = []
    for index, pace in enumerate((238, 242, 245)):
        activities.append(_activity(
            target, index, pace=320, primary_type="interval",
            features={"splits": [{
                "index": 1, "split_type": "INTERVAL_ACTIVE",
                "duration_sec": 180, "pace_sec_per_km": pace,
            }]},
        ))
    profile = PaceCalibrationProfileBuilder().build(activities, target=target)
    workout = ensure_training_prescription({
        "type": "quality", "title": "间歇耐受跑", "is_key": True,
        "duration_minutes": 50, "distance_km": 10,
        "stimuli": ["high_intensity_tolerance", "running_economy"],
    }, synthesize_steps=True)

    resolved = DailyPaceAdjustmentEngine().apply(workout, profile=profile)
    leaves = iter_leaf_workout_steps(resolved["training_prescription"]["steps"])
    work = next(step for step in leaves if step["role"] == "work")
    recovery = next(step for step in leaves if step["role"] == "recovery")

    assert work["resolved_target"]["status"] == "available"
    assert work["resolved_target"]["metric"] == "pace"
    assert work["resolved_target"]["display_range"]
    assert recovery["resolved_target"]["status"] == "feel_only"
    assert "呼吸恢复" in recovery["resolved_target"]["label"]


def test_z5_step_duration_mismatch_downgrades_only_that_step_to_feel():
    target = date(2026, 8, 4)
    activities = [
        _activity(
            target, index, pace=320, primary_type="interval",
            features={"splits": [{
                "index": 1, "split_type": "INTERVAL_ACTIVE",
                "duration_sec": 180, "pace_sec_per_km": pace,
            }]},
        )
        for index, pace in enumerate((238, 242, 245))
    ]
    profile = PaceCalibrationProfileBuilder().build(activities, target=target)
    workout = ensure_training_prescription({
        "type": "quality", "title": "间歇耐受跑", "is_key": True,
        "duration_minutes": 50, "distance_km": 10,
        "stimuli": ["high_intensity_tolerance"],
    }, synthesize_steps=True)
    work = next(
        step for step in iter_leaf_workout_steps(
            workout["training_prescription"]["steps"],
        ) if step["role"] == "work"
    )
    work["intensity_intent"]["calibration_context"]["duration_seconds"] = 600

    resolved = DailyPaceAdjustmentEngine().apply(workout, profile=profile)
    resolved_work = next(
        step for step in iter_leaf_workout_steps(
            resolved["training_prescription"]["steps"],
        ) if step["role"] == "work"
    )

    assert resolved_work["resolved_target"]["status"] == "feel_only"
    assert "不可比" in resolved_work["resolved_target"]["reason"]


def test_extreme_weather_slows_pace_conservatively_without_guessing():
    """极端天气（高温等）触发配速保守放慢，且原因可追溯。"""
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )
    engine = DailyPaceAdjustmentEngine()
    normal = engine.apply(
        _easy_workout(), profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
    )
    hot = engine.apply(
        _easy_workout(), profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
        constraints={"weather": "本周持续高温"},
    )
    normal_target = normal["training_prescription"]["pace_guidance"]["today_target"]
    hot_target = hot["training_prescription"]["pace_guidance"]["today_target"]
    assert hot_target["status"] == "slower"
    assert hot_target["min_sec_per_km"] > normal_target["min_sec_per_km"]
    assert hot_target["max_sec_per_km"] > normal_target["max_sec_per_km"]
    assert any(
        "极端天气" in reason
        for reason in hot["training_prescription"]["pace_guidance"]["explanation"]["reasons"]
    )


def test_absent_or_mild_weather_does_not_alter_pace():
    """无天气输入或非极端天气不猜测、不调整配速。"""
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, index, pace=pace) for index, pace in enumerate((330, 336, 342))],
        target=target,
    )
    engine = DailyPaceAdjustmentEngine()
    base = engine.apply(
        _easy_workout(), profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
    )
    mild = engine.apply(
        _easy_workout(), profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
        constraints={"weather": "晴，微风"},
    )
    assert mild["training_prescription"]["pace_guidance"]["today_target"] == \
        base["training_prescription"]["pace_guidance"]["today_target"]


def test_weather_normalization_speeds_up_summer_samples():
    """夏季/高温样本折算后配速变快（去掉高温影响），温和季节基本不变。"""
    from src.training_pace import _weather_adjusted_pace

    assert _weather_adjusted_pace(340, date(2026, 8, 1)) == round(340 * 0.94)
    assert _weather_adjusted_pace(340, date(2026, 5, 1)) == round(340 * 0.99)
    assert _weather_adjusted_pace(340, date(2026, 1, 1)) == round(340 * 1.03)
    # 补充信息高温 → 更强修正
    assert _weather_adjusted_pace(340, date(2026, 8, 1), "本周持续高温") == round(340 * 0.92)
    # 无日期 → 不修正
    assert _weather_adjusted_pace(340, None) == 340


def test_pb_fallback_when_samples_insufficient():
    """样本不足时用 PB 推算兜底 Z 区间（source=pb_derived）。"""
    target = date(2026, 8, 4)
    pb = {"half_marathon": {"time": "1:12:48"}}  # 半马 3:27/km = 207s
    profile = PaceCalibrationProfileBuilder().build([], target=target, personal_bests=pb)

    z4 = profile["zones"]["z4"]
    assert z4["status"] == "available"
    assert z4["source"] == "pb_derived"
    assert z4["min_sec_per_km"] == round(207 * 0.95)
    assert z4["max_sec_per_km"] == round(207 * 1.05)
    # z2 兜底 = T × 1.28~1.38（轻松跑）
    assert profile["zones"]["z2"]["source"] == "pb_derived"
    assert profile["zones"]["z2"]["min_sec_per_km"] == round(207 * 1.28)


def test_pb_caps_z4_when_recent_faster_than_pb():
    """近期样本快于 PB 推算阈值时，Z4 上限封顶到 PB T。"""
    target = date(2026, 8, 4)
    pb = {"half_marathon": {"time": "1:30:00"}}  # 4:16/km = 256s
    # tempo 活动配速 200s/km（3:20，快于 PB 阈值）
    activities = [
        _activity(target, i, pace=200, primary_type="tempo")
        for i in range(5)
    ]
    profile = PaceCalibrationProfileBuilder().build(activities, target=target, personal_bests=pb)
    z4 = profile["zones"]["z4"]
    assert z4["status"] == "available"
    assert z4["max_sec_per_km"] >= 256  # 上限不低于 PB 阈值
    assert z4.get("capped_by_pb") is True


def test_state_gap_marked_when_recent_slower_than_pb():
    """近期样本显著慢于 PB 时标记 state_gap=current_below_pb，不改配速。"""
    target = date(2026, 8, 4)
    pb = {"half_marathon": {"time": "1:12:48"}}  # 207s
    # tempo 活动配速 300s/km（5:00，慢于 PB 的 1.12 倍 232）
    activities = [
        _activity(target, i, pace=300, primary_type="tempo")
        for i in range(5)
    ]
    profile = PaceCalibrationProfileBuilder().build(activities, target=target, personal_bests=pb)
    assert profile.get("state_gap") == "current_below_pb"
    # 差距标记不改配速（z4 仍来自训练样本）
    assert profile["zones"]["z4"]["source"] != "pb_derived"


def _render_quality(kind, zone, duration=60, stimuli=None, pb=None):
    """构造质量课并跑完确定性管线，返回叶子步骤。"""
    from src.training_planning import ensure_training_prescription, iter_leaf_workout_steps
    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [_activity(target, i, pace=pace, primary_type="tempo") for i, pace in enumerate((250, 255, 260))],
        target=target, personal_bests=pb or {"half_marathon": {"time": "1:30:00"}},
    )
    w = {"type": kind, "title": kind, "duration_minutes": duration,
         "intensity_intent": {"zone": zone},
         "stimuli": stimuli or ["threshold_development"], "is_key": True}
    w = ensure_training_prescription(
        w, pace_reference_sec_per_km=profile["zones"]["z2"]["recent_median_sec_per_km"],
        synthesize_steps=True,
    )
    w = DailyPaceAdjustmentEngine().apply(w, profile=profile, recovery_snapshot={"recovery": {"overall_score": 85}})
    return {st["role"]: st for st in iter_leaf_workout_steps(w["training_prescription"].get("steps") or [])}


def test_tempo_work_step_uses_z4_pace():
    """节奏课工作段应落在 Z4（阈值）配速，而非被误判为 Z5。"""
    steps = _render_quality("tempo", zone=4)
    work = steps["work"]
    assert (work["intensity_intent"] or {}).get("zone") == 4
    assert work["resolved_target"]["status"] == "available"
    assert work["resolved_target"]["display_range"]


def test_interval_work_step_uses_z5_pace_from_pb():
    """间歇课工作段 Z5 配速由 PB 兜底（无训练样本时）。"""
    steps = _render_quality("interval", zone=5, stimuli=["high_intensity_tolerance"])
    work = steps["work"]
    assert (work["intensity_intent"] or {}).get("zone") == 5
    assert work["resolved_target"]["status"] == "available"
    assert work["resolved_target"]["display_range"]


def test_easy_warmup_slower_than_main():
    """轻松跑热身段配速慢于主体（渐进）。"""
    steps = _render_quality("easy", zone=2, stimuli=["aerobic"])
    warmup_range = steps["warmup"]["resolved_target"]["display_range"]
    main_range = steps["main"]["resolved_target"]["display_range"]

    def slow_end(r):
        text = r.split("–")[0].split("/")[0].strip()
        m, s = text.split(":")
        return int(m) * 60 + int(s)
    assert slow_end(warmup_range) > slow_end(main_range)
