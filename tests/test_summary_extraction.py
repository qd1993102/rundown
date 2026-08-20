from src.summary_extraction import build_session_summary


def test_l0_uses_summary_volume_and_explicit_pace_proxy():
    facts = build_session_summary({
        "summaryDTO": {"duration": 3600, "distance": 10000, "calories": 700},
    })
    assert facts.granularity == "L0"
    assert facts.volume.duration_s == 3600
    assert facts.volume.distance_m == 10000
    assert facts.structure is None
    assert facts.intensity is None
    assert facts.data_quality["intensity_basis"] is None
    assert facts.schema_version == "session-summary"
    assert facts.fact_version.startswith("session-summary:")


def test_session_fact_version_is_stable_and_changes_with_source_detail():
    first = build_session_summary({"summaryDTO": {"duration": 600, "distance": 2000}})
    repeated = build_session_summary({"summaryDTO": {"duration": 600, "distance": 2000}})
    changed = build_session_summary({"summaryDTO": {"duration": 601, "distance": 2000}})

    assert first.fact_version == repeated.fact_version
    assert first.fact_version != changed.fact_version


def test_l1_aggregates_splits_and_degrades_hr_to_pace():
    facts = build_session_summary({
        "summaryDTO": {"duration": 1800, "distance": 5000, "elevationGain": 80},
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "distance": 1000, "duration": 240, "averageRunCadence": 180},
            {"splitType": "RECOVERY", "distance": 1000, "duration": 300, "averageRunCadence": 170},
            {"splitType": "INTERVAL_ACTIVE", "distance": 1000, "duration": 235, "averageRunCadence": 182},
        ],
    })
    assert facts.granularity == "L1"
    assert facts.structure is not None and facts.structure.n_splits == 3
    assert facts.structure.avg_cadence > 170
    assert facts.intensity is not None and facts.intensity.basis == "pace"
    assert facts.terrain is not None and facts.terrain.ascent_m == 80


def test_coros_lap_list_flattens_lap_items_into_splits():
    """Coros lapList（含 lapItemList）应展开为有顺序的分段序列。"""
    facts = build_session_summary({
        "summary": {"duration": 600, "distance": 3000, "elevationGain": 20},
        "lapList": [
            {"lapItemList": [
                {"distance": 500, "duration": 150, "averageHR": 150, "avgCadence": 172},
                {"distance": 500, "duration": 160, "averageHR": 148, "avgCadence": 170},
            ]},
            {"lapItemList": [
                {"distance": 1000, "duration": 290, "averageHR": 155, "avgCadence": 174},
            ]},
        ],
    })
    assert facts.granularity == "L1"
    assert facts.structure is not None and facts.structure.n_splits == 3
    assert facts.intensity is not None and facts.intensity.basis == "hr"
    assert facts.terrain is not None and facts.terrain.ascent_m == 20


def test_coros_lap_without_lap_items_uses_lap_summary():
    """lap 无 lapItemList 时退化为 lap 汇总本身作为分段。"""
    facts = build_session_summary({
        "summary": {"duration": 300, "distance": 1200},
        "lapList": [
            {"distance": 1200, "duration": 300, "averageHR": 160},
        ],
    })
    assert facts.granularity == "L1"
    assert facts.structure is not None and facts.structure.n_splits == 1


def test_l2_prefers_one_hz_metrics_for_intensity_and_grade():
    facts = build_session_summary({
        "summaryDTO": {"duration": 60, "distance": 300},
        "splitSummaries": [{"distance": 300, "duration": 60, "averageHR": 150}],
        "activity_detail_metrics": [
            {"duration": 1, "pace_sec_per_km": 240, "heartRate": 150, "grade_pct": 6},
            {"duration": 1, "pace_sec_per_km": 300, "heartRate": 160, "grade_pct": -1},
        ],
    })
    assert facts.granularity == "L2"
    assert facts.intensity is not None and facts.intensity.basis == "hr"
    assert facts.terrain is not None and facts.terrain.grade_profile


def test_coros_real_units_laps_and_frequency_normalize_to_shared_contract():
    detail = {
        "summary": {
            "sportType": 101,
            "distance": 814126,
            "workoutTime": 258245,
            "totalTime": 448108,
            "avgHr": 155,
            "maxHr": 169,
            "avgCadence": 160,
            "avgStepLen": 118,
            "trainingLoad": 107,
            "calories": 495255,
        },
        "lapList": [{"lapItemList": [
            {"distance": 100000, "time": 30151, "avgPace": 301.52,
             "avgHr": 164, "avgCadence": 166, "avgStrideLength": 119},
            {"distance": 100000, "time": 31892, "avgPace": 318.93,
             "avgHr": 149, "avgCadence": 159, "avgStrideLength": 118},
            {"distance": 614126, "time": 198202, "avgPace": 322.73,
             "avgHr": 154, "avgCadence": 160, "avgStrideLength": 117},
        ]}],
        "frequencyList": [
            {"timestamp": 100, "heart": 150, "speed": 300, "cadence": 160},
            {"timestamp": 200, "heart": 160, "speed": 320, "cadence": 164},
        ],
    }

    facts = build_session_summary(detail)
    result = facts.to_dict()

    assert facts.granularity == "L2"
    assert facts.volume.distance_m == 8141.26
    assert facts.volume.duration_s == 2582.45
    assert facts.volume.elapsed_duration_s == 4481.08
    assert facts.volume.calories == 495.255
    assert facts.pace_profile is not None
    assert facts.pace_profile.avg_pace_sec_per_km == 317.2
    assert facts.structure is not None
    assert facts.structure.avg_cadence == 162
    assert 117 <= (facts.structure.avg_stride or 0) <= 119
    assert result["quantity_gate"]["quantity_reliable"] is True
    assert len(result["segment_sequence"]) == 3
    assert result["segment_sequence"][0]["stride_length_cm"] == 119
    assert "frequencyList" not in result


def test_structure_facts_without_kinematics_data():
    """无心率/步频/步幅/GCT/VO 时，动力学画像字段合理为 None。"""
    facts = build_session_summary({"summaryDTO": {"distance": 2000}, "splitSummaries": [
        {"distance": 1000, "duration": 100}, {"distance": 1000, "duration": 200},
    ]})
    assert facts.structure is not None
    assert facts.structure.n_splits == 2
    assert facts.structure.cadence_cv_pct is None
    assert facts.structure.cadence_half_diff is None
    assert facts.structure.stride_cv_pct is None
    assert facts.structure.stride_half_diff is None
    assert facts.structure.gct_cv_pct is None
    assert facts.structure.gct_half_diff is None
    assert facts.structure.vo_cv_pct is None
    assert facts.structure.vo_half_diff is None
    assert facts.structure.avg_vertical_ratio is None
    assert facts.structure.hr_cv_pct is None
    assert facts.structure.hr_half_diff is None


# ── session-summary-v2：训练深度分析 ──────────────────────────────

def test_v2_parses_provider_training_effect_and_intensity_minutes():
    facts = build_session_summary({
        "summaryDTO": {
            "duration": 3212, "distance": 20000,
            "trainingEffect": 4.4, "anaerobicTrainingEffect": 0.2,
            "trainingEffectLabel": "AEROBIC_BASE",
            "aerobicTrainingEffectMessage": "HIGHLY_IMPROVING_AEROBIC_ENDURANCE_10",
            "moderateIntensityMinutes": 45, "vigorousIntensityMinutes": 8,
        },
    })
    assert facts.effect is not None
    assert facts.effect.aerobic_training_effect == 4.4
    assert facts.effect.anaerobic_training_effect == 0.2
    assert facts.effect.label == "AEROBIC_BASE"
    assert facts.effect.moderate_intensity_minutes == 45
    assert facts.effect.vigorous_intensity_minutes == 8
    assert facts.effect.estimated is False


def test_v2_parses_elevation_profile_with_range_and_descent():
    facts = build_session_summary({
        "summaryDTO": {
            "distance": 20000, "elevationGain": 1066, "elevationLoss": 1060,
            "maxElevation": 94, "minElevation": -2,
        },
    })
    assert facts.elevation_profile is not None
    assert facts.elevation_profile.ascent_m == 1066
    assert facts.elevation_profile.descent_m == 1060
    assert facts.elevation_profile.max_elevation_m == 94
    assert facts.elevation_profile.min_elevation_m == -2
    assert facts.elevation_profile.gain_per_km == 53.3


def test_v2_pace_profile_computes_fastest_percentiles_cv_and_half_split():
    facts = build_session_summary({
        "summaryDTO": {"duration": 5400, "distance": 20000, "maxSpeed": 5.5},
        "splitSummaries": [
            {"distance": 5000, "duration": 1300, "pace_per_km": 260},
            {"distance": 5000, "duration": 1350, "pace_per_km": 270},
            {"distance": 5000, "duration": 1350, "pace_per_km": 270},
            {"distance": 5000, "duration": 1400, "pace_per_km": 280},
        ],
    })
    assert facts.pace_profile is not None
    assert facts.pace_profile.fastest_pace_sec_per_km == 181.8  # 1000/5.5
    assert facts.pace_profile.split_count == 4
    assert facts.pace_profile.cv_pct is not None
    assert facts.pace_profile.positive_split is True  # 后半较前半慢
    assert facts.pace_profile.half_pace_diff_s > 0
    assert facts.pace_profile.p50 is not None
    assert facts.pace_profile.p5 <= facts.pace_profile.p50 <= facts.pace_profile.p95


def test_v2_structure_profile_detects_interval_work_recovery():
    facts = build_session_summary({
        "summaryDTO": {"duration": 1800, "distance": 5000},
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "distance": 1000, "duration": 240, "pace_per_km": 240},
            {"splitType": "RECOVERY", "distance": 1000, "duration": 300, "pace_per_km": 320},
            {"splitType": "INTERVAL_ACTIVE", "distance": 1000, "duration": 235, "pace_per_km": 235},
            {"splitType": "RECOVERY", "distance": 1000, "duration": 290, "pace_per_km": 315},
        ],
    })
    assert facts.structure_profile is not None
    assert facts.structure_profile.work_blocks == 2
    assert facts.structure_profile.recovery_blocks == 2
    assert facts.structure_profile.composite_type == "interval"


def test_v2_structure_profile_fartlek_via_name():
    facts = build_session_summary({
        "summaryDTO": {"duration": 1800, "distance": 5000},
        "activityName": "fartlek 变速跑",
        "splitSummaries": [
            {"splitType": "MAIN", "distance": 2500, "duration": 900, "pace_per_km": 300},
        ],
    })
    assert facts.structure_profile is not None
    assert facts.structure_profile.composite_type == "fartlek"


def test_v2_l0_without_splits_has_no_pace_or_structure_profile():
    facts = build_session_summary({
        "summaryDTO": {"duration": 3600, "distance": 10000, "elevationGain": 80},
    })
    assert facts.pace_profile is None
    assert facts.structure_profile is None
    assert facts.elevation_profile is not None


def test_v2_volume_carries_elapsed_duration_for_pause_detection():
    facts = build_session_summary({
        "summaryDTO": {
            "duration": 3600, "elapsedDuration": 3720, "distance": 10000,
        },
    })
    assert facts.volume.elapsed_duration_s == 3720


def test_v2_structure_carries_max_cadence():
    facts = build_session_summary({
        "summaryDTO": {"duration": 600, "distance": 2000, "maxRunCadence": 102},
        "splitSummaries": [{"distance": 2000, "duration": 600}],
    })
    assert facts.structure is not None
    assert facts.structure.max_cadence == 102


def test_estimate_training_effect_marks_estimated_and_uses_hr_first():
    from src.summary_extraction import estimate_training_effect

    result = estimate_training_effect(
        [
            {"duration_sec": 1800, "avg_hr": 165, "pace_per_km": 260},
            {"duration_sec": 1800, "avg_hr": 155, "pace_per_km": 275},
        ],
        threshold_heart_rate=165,
        threshold_pace_sec_per_km=270,
    )
    assert result is not None
    assert result["estimated"] is True
    assert result["estimate_method"] == "zone_minutes_weighted"
    assert result["estimate_basis"] == "heart_rate"
    assert result["aerobic_training_effect"] is not None
    assert 1.0 <= result["aerobic_training_effect"] <= 5.0


def test_estimate_training_effect_falls_back_to_pace_proxy():
    from src.summary_extraction import estimate_training_effect

    result = estimate_training_effect(
        [
            {"duration_sec": 1800, "pace_per_km": 240},
            {"duration_sec": 1800, "pace_per_km": 280},
        ],
        threshold_pace_sec_per_km=270,
    )
    assert result is not None
    assert result["estimated"] is True
    assert result["estimate_basis"] == "pace"


def test_estimate_training_effect_returns_none_without_threshold():
    from src.summary_extraction import estimate_training_effect

    assert estimate_training_effect(
        [{"duration_sec": 1800, "avg_hr": 165}],
        threshold_heart_rate=None,
        threshold_pace_sec_per_km=None,
    ) is None


def test_compact_planning_projection_keeps_summary_subset_only():
    from src.summary_extraction import build_session_summary, compact_session_summary_for_planning

    facts = build_session_summary({
        "summaryDTO": {
            "duration": 3212, "distance": 20000,
            "trainingEffect": 4.4, "trainingEffectLabel": "AEROBIC_BASE",
            "elevationGain": 20, "elevationLoss": 19, "maxSpeed": 5.5,
        },
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "distance": 3000, "duration": 720, "pace_per_km": 240, "averageHR": 160},
            {"splitType": "RECOVERY", "distance": 2000, "duration": 600, "pace_per_km": 300, "averageHR": 140},
            {"splitType": "INTERVAL_ACTIVE", "distance": 3000, "duration": 700, "pace_per_km": 238, "averageHR": 162},
            {"splitType": "RECOVERY", "distance": 2000, "duration": 590, "pace_per_km": 305, "averageHR": 141},
        ],
    })
    compact = compact_session_summary_for_planning(facts.to_dict())

    # 概要子集存在
    assert compact["granularity"] == "L1"
    assert compact["volume"]["distance_m"] == 20000
    assert compact["effect"]["aerobic_training_effect"] == 4.4
    assert compact["structure_profile"]["composite_type"] == "interval"
    # 详情字段被压缩掉
    assert "hr_bands_pct" not in compact
    assert "evidence" not in compact
    assert "elevation_profile" not in compact
    assert "split_types" not in compact


def test_compact_planning_projection_handles_empty():
    from src.summary_extraction import compact_session_summary_for_planning

    assert compact_session_summary_for_planning(None) == {}
    assert compact_session_summary_for_planning({})["granularity"] is None


# ── 训练结构判定（classify_training_structure） ─────────────────

from src.summary_extraction import classify_training_structure


def _seq(rows):
    return [
        {"pace_sec_per_km": p, "avg_hr": h, "avg_cadence": c, "stride_length_cm": s, "split_type": t}
        for p, h, c, s, t in rows
    ]


def _seq_with_distance(rows):
    return [
        {"pace_sec_per_km": p, "avg_hr": h, "avg_cadence": c, "stride_length_cm": s,
         "split_type": t, "distance_m": d}
        for p, h, c, s, t, d in rows
    ]


def test_classify_fartlek_two_alternations_with_hr_and_cadence():
    # 变速：主体 320s/km，2 组快慢交替（快 255s，慢 360s），
    # 快段未达阈值（阈值 255=快段），无 INTERVAL 标记，心率与步频同步响应
    seq = _seq([
        (320, 140, 170, 110, "warmup"),
        (255, 160, 180, 120, "fast"),
        (360, 145, 172, 112, "recovery"),
        (255, 162, 181, 121, "fast"),
        (360, 146, 172, 112, "recovery"),
        (320, 140, 170, 110, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=255,
    )
    assert result["structure_type"] == "fartlek"
    assert result["alternations"] >= 2
    assert result["label"] == "变速"
    # 配速+心率+步频+步幅全证据 → 高置信
    assert result["confidence"] >= 0.9
    assert result["missing_evidence"] == []


def test_classify_interval_fast_above_threshold():
    # 间歇：快段强度达阈值以上（240s/km vs 阈值 250），2 组交替 → interval
    seq = _seq([
        (300, 140, 170, 110, "warmup"),
        (240, 170, 182, 122, "INTERVAL_ACTIVE"),
        (340, 145, 172, 112, "RECOVERY"),
        (240, 172, 183, 123, "INTERVAL_ACTIVE"),
        (340, 145, 172, 112, "RECOVERY"),
        (300, 140, 170, 110, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=250,
    )
    assert result["structure_type"] == "interval"
    assert result["label"] == "间歇"


def test_classify_tempo_continuous_z3():
    # 节奏：无交替，主体强度 Z3（0.88-1.0×阈值）
    seq = _seq([
        (300, 150, 175, 115, "warmup"),
        (280, 155, 176, 116, "run"),
        (280, 156, 176, 116, "run"),
        (280, 155, 176, 116, "run"),
        (300, 145, 174, 114, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=280,
    )
    assert result["structure_type"] == "tempo"
    assert result["label"] == "节奏"


def test_classify_aerobic_z2():
    seq = _seq([
        (320, 135, 170, 110, "warmup"),
        (320, 136, 171, 111, "run"),
        (320, 136, 171, 111, "run"),
        (330, 134, 170, 110, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=300,
    )
    assert result["structure_type"] == "aerobic"
    assert result["label"] == "有氧"


def test_classify_missing_hr_and_other_dimensions_lowers_confidence():
    # 只有配速：置信 = 0.40，标注缺失证据，结构仍可判定（交替结构）
    seq = _seq([
        (300, None, None, None, "warmup"),
        (240, None, None, None, "fast"),
        (340, None, None, None, "recovery"),
        (240, None, None, None, "fast"),
        (340, None, None, None, "recovery"),
    ])
    result = classify_training_structure(seq)
    assert result["confidence"] == 0.40
    assert "心率" in result["missing_evidence"]
    assert "步频" in result["missing_evidence"]
    assert "步幅" in result["missing_evidence"]


def test_composition_and_groups_carry_distance_when_quantity_reliable():
    # 距离可信时：快段占比按距离口径（快段距离 / 总距离），组明细携带每组距离/时长
    seq = _seq_with_distance([
        (320, 140, 170, 110, "warmup", 4000),
        (255, 160, 180, 120, "fast", 1000),
        (380, 145, 172, 112, "recovery", 1000),
        (255, 162, 181, 121, "fast", 1000),
        (380, 146, 172, 112, "recovery", 1000),
        (320, 140, 170, 110, "cooldown", 2000),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=255,
    )
    assert result["structure_type"] == "fartlek"
    by_role = {item["role"]: item for item in result["composition"]}
    assert by_role["fast"]["basis"] == "distance"
    assert by_role["fast"]["pct"] == 20.0  # 2×1000m / 10000m
    assert by_role["slow"]["pct"] == 20.0  # 2×1000m / 10000m
    assert by_role["body"]["pct"] == 60.0  # 4000 + 2000
    groups = result["work_recovery_groups"]
    assert groups[0]["work_distance_m"] == 1000
    assert groups[0]["recovery_distance_m"] == 1000
    assert "work_duration_s" in groups[0]
    assert "recovery_duration_s" in groups[0]


def test_composition_falls_back_to_segment_count_without_distance():
    # 分段无距离（如旧数据）时，占比回退按段数口径并标记 basis
    seq = _seq([
        (320, 140, 170, 110, "warmup"),
        (255, 160, 180, 120, "fast"),
        (360, 145, 172, 112, "recovery"),
        (255, 162, 181, 121, "fast"),
        (360, 146, 172, 112, "recovery"),
        (320, 140, 170, 110, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=255,
    )
    by_role = {item["role"]: item for item in result["composition"]}
    assert by_role["fast"]["basis"] == "segment_count"
    assert by_role["fast"]["pct"] == round(2 / 6 * 100, 1)
    groups = result["work_recovery_groups"]
    assert groups[0]["work_distance_m"] is None
    assert result["structure_type"] in ("fartlek", "interval")


def test_classify_no_segments_or_no_pace_is_unknown():
    assert classify_training_structure([])["structure_type"] == "unknown"
    result = classify_training_structure(
        [{"avg_hr": 140, "split_type": "run"}]
    )
    assert result["structure_type"] == "unknown"
    assert result["reason"] == "无配速序列"


def test_legacy_summary_rebuilt_from_splits_recognizes_mixed():
    """存量活动（旧 schema summary 无 segment_sequence）从 activity_splits 重建
    分段序列后应识别结构；×2 距离异常被 quantity_gate 标记为仅强度模式。"""
    from src.memory import MemoryWriter

    legacy_summary = {
        "granularity": "L1",
        # 旧 schema：没有 segment_sequence / quantity_gate / pace_profile
        "volume": {"distance_m": 20014.79, "duration_s": 5356.65},
        "structure": {"n_splits": 7, "avg_cadence": 175.9, "avg_stride": 126.5},
        "intensity": {"basis": "hr", "hr_bands_pct": {"130-145": 91.0}},
    }
    # 模拟 Garmin splitSummaries：分段距离合计是 summary 的 2 倍（×2 异常）
    splits = [
        {"type": "RWD_WALK", "distance_m": 15.38, "duration_sec": 10.85,
         "pace_per_km": 705.5, "avg_hr": 77, "avg_cadence": 37.7, "stride_length_cm": 79.4},
        {"type": "INTERVAL_ACTIVE", "distance_m": 4348.57, "duration_sec": 996.74,
         "pace_per_km": 229.2, "avg_hr": 149, "avg_cadence": 183.1, "stride_length_cm": 140.8},
        {"type": "RWD_RUN", "distance_m": 19993.75, "duration_sec": 5343.78,
         "pace_per_km": 267.3, "avg_hr": 136, "avg_cadence": 173.9, "stride_length_cm": 128.0},
        {"type": "INTERVAL_RECOVERY", "distance_m": 1202.28, "duration_sec": 373.39,
         "pace_per_km": 310.6, "avg_hr": 140, "avg_cadence": 171.0, "stride_length_cm": 125.0},
    ]
    sequence, quantity_reliable = MemoryWriter._segment_sequence_fallback(
        legacy_summary, splits,
    )
    # 重建成功 + ×2 异常被识别为量不可信（仅强度模式）
    assert len(sequence) == 4
    assert sequence[1]["pace_sec_per_km"] == 229.2
    assert sequence[1]["split_type"] == "INTERVAL_ACTIVE"
    assert quantity_reliable is False
    # 单次跑步融合分段变速：1 组快慢交替 → 混合，而非间歇或 unknown
    result = classify_training_structure(
        sequence, threshold_heart_rate=180, threshold_pace_sec_per_km=300,
        quantity_reliable=False,
    )
    assert result["structure_type"] == "mixed"
    assert result["label"] == "混合"
    assert result["alternations"] == 1
    assert result["quantity_reliable"] is False


def test_legacy_summary_fallback_keeps_quantity_reliable_when_ratio_ok():
    from src.memory import MemoryWriter

    summary = {
        "volume": {"distance_m": 10000.0},
    }
    splits = [
        {"type": "RUN", "distance_m": 6000.0, "duration_sec": 1800,
         "pace_per_km": 300.0, "avg_hr": 145, "avg_cadence": 180, "stride_length_cm": 120},
        {"type": "RUN", "distance_m": 4000.0, "duration_sec": 1200,
         "pace_per_km": 320.0, "avg_hr": 140, "avg_cadence": 178, "stride_length_cm": 118},
    ]
    sequence, quantity_reliable = MemoryWriter._segment_sequence_fallback(
        summary, splits,
    )
    assert quantity_reliable is True
    assert len(sequence) == 2


def test_segment_sequence_prefers_summary_over_splits():
    from src.memory import MemoryWriter

    summary = {
        "segment_sequence": [{"pace_sec_per_km": 250}],
        "quantity_gate": {"quantity_reliable": False},
        "volume": {"distance_m": 10000.0},
    }
    sequence, quantity_reliable = MemoryWriter._segment_sequence_fallback(
        summary, [{"type": "RUN", "distance_m": 5000.0, "pace_per_km": 999}],
    )
    # 新 schema summary 有 segment_sequence 时优先使用，不用 splits 重建
    assert sequence[0]["pace_sec_per_km"] == 250
    assert quantity_reliable is False


def test_classify_fatigue_signal_detected():
    # 后半程心率抬升 + 步幅下降 + 配速维持 → 疲劳信号
    seq = _seq([
        (300, 140, 170, 115, "run"),
        (300, 142, 170, 114, "run"),
        (300, 148, 170, 111, "run"),
        (300, 152, 170, 110, "run"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165,
    )
    assert result["fatigue_signal"]["detected"] is True


def _lap_dto(distance, duration, hr, cadence, stride, stype="INTERVAL"):
    return {
        "intensityType": stype, "distance": distance, "duration": duration,
        "averageSpeed": (distance / 1000) / (duration / 1000) * 3.6 / 3.6 * (1000 / (duration / (distance / 1000))),
        "averageHR": hr, "averageRunCadence": cadence, "strideLength": stride,
    }


def test_lap_dtos_are_trusted_split_source_over_split_summaries():
    # lapDTOs 距离/时长可信（合计=summary），优先于 splitSummaries
    detail = {
        "summaryDTO": {"duration": 4000, "distance": 12000, "trainingEffect": 3.0},
        "splitSummaries": [{"distance": 24000, "duration": 8000}],  # 异常 ×2
        "lapDTOs": [
            {"intensityType": "INTERVAL", "distance": 1000, "duration": 300,
             "averageSpeed": 3.33, "averageHR": 140, "averageRunCadence": 175, "strideLength": 120},
            {"intensityType": "INTERVAL", "distance": 1000, "duration": 300,
             "averageSpeed": 3.33, "averageHR": 140, "averageRunCadence": 175, "strideLength": 120},
        ],
    }
    facts = build_session_summary(detail)
    d = facts.to_dict()
    assert d["granularity"] == "L1"
    assert d["quantity_gate"]["ratio"] == round(2000 / 12000, 2)
    assert d["quantity_gate"]["quantity_reliable"] is False  # 2km vs 12km 明显不符
    assert len(d["segment_sequence"]) == 2
    assert d["segment_sequence"][0]["avg_cadence"] == 175


def test_lap_dtos_quantity_gate_reliable_and_classifier_detects_4_alternations():
    laps = []
    # 4 组 1000 快 + 400 慢，前后主体
    for i in range(2):
        laps.append({"intensityType": "INTERVAL", "distance": 1000, "duration": 300,
                     "averageSpeed": 3.33, "averageHR": 140, "averageRunCadence": 175, "strideLength": 120})
    fast_slow = [(1000, 220, 152, 186, 144), (400, 130, 140, 174, 112),
                 (1000, 218, 154, 187, 145), (400, 128, 141, 174, 113),
                 (1000, 215, 155, 188, 146), (400, 129, 140, 174, 112),
                 (1000, 216, 153, 187, 145), (400, 131, 142, 174, 113)]
    for dist, dur, hr, cad, stride in fast_slow:
        laps.append({"intensityType": "INTERVAL", "distance": dist, "duration": dur,
                     "averageSpeed": (dist / 1000) / (dur / 1000) * 3.6 / 3.6,
                     "averageHR": hr, "averageRunCadence": cad, "strideLength": stride})
    for i in range(2):
        laps.append({"intensityType": "INTERVAL", "distance": 1000, "duration": 300,
                     "averageSpeed": 3.33, "averageHR": 140, "averageRunCadence": 175, "strideLength": 120})

    total_dist = sum(l["distance"] for l in laps)
    total_dur = sum(l["duration"] for l in laps)
    detail = {"summaryDTO": {"duration": total_dur, "distance": total_dist}, "lapDTOs": laps}
    d = build_session_summary(detail).to_dict()
    assert d["quantity_gate"]["ratio"] == 1.0
    assert d["quantity_gate"]["quantity_reliable"] is True

    c = classify_training_structure(
        d["segment_sequence"], threshold_heart_rate=150, threshold_pace_sec_per_km=240,
        quantity_reliable=True,
    )
    assert c["alternations"] == 4
    assert c["structure_type"] == "interval"
    assert c["confidence"] == 1.0
    assert c["missing_evidence"] == []


def test_classify_outputs_work_recovery_group_pace_details():
    seq = _seq([
        (320, 140, 170, 110, "warmup"),
        (255, 160, 180, 120, "fast"),
        (380, 145, 172, 112, "recovery"),
        (255, 162, 181, 121, "fast"),
        (380, 146, 172, 112, "recovery"),
        (320, 140, 170, 110, "cooldown"),
    ])
    result = classify_training_structure(
        seq, threshold_heart_rate=165, threshold_pace_sec_per_km=255,
    )
    groups = result["work_recovery_groups"]
    assert len(groups) == 2
    first = groups[0]
    assert first["group"] == 1
    assert first["work_pace_sec_per_km"] == 255
    assert first["recovery_pace_sec_per_km"] == 380
    assert first["work_avg_hr"] == 160
    assert first["recovery_avg_hr"] == 145
    second = groups[1]
    assert second["work_pace_sec_per_km"] == 255
    assert second["recovery_pace_sec_per_km"] == 380
