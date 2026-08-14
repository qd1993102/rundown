"""共享单日训练摘要合同测试。"""

from datetime import date

from src.training_day_summary import TrainingDaySummaryBuilder


def test_day_summary_backfills_cadence_stride_and_keeps_explicit_gaps():
    target = date(2026, 8, 14)
    result = TrainingDaySummaryBuilder.build(
        target=target,
        activities=[{
            "activity_id": "coros-1", "activity_date": str(target),
            "activity_type": "running", "activity_name": "Indoor Run",
            "distance_meters": 8141.26, "duration_seconds": 2582.45,
            "avg_heart_rate": 155,
            "session_summary": {
                "granularity": "L2",
                "structure": {"avg_cadence": 160, "avg_stride": 118},
                "pace_profile": {"avg_pace_sec_per_km": 317.2},
                "segment_sequence": [{"pace_sec_per_km": 301.5}],
                "quantity_gate": {"quantity_reliable": True},
            },
        }],
        states={str(target): "complete"},
    )
    metrics = result["sessions"][0]["metrics"]
    assert metrics["pace_sec_per_km"] == 317.2
    assert metrics["cadence"] == 160
    assert metrics["stride_length_cm"] == 118
    fields = {item["field"] for item in result["gaps"]}
    assert "heart_rate" not in fields
    assert "cadence" not in fields
    assert "stride_length" not in fields
    assert "reliable_splits" not in fields


def test_builder_preserves_activity_facts_and_analysis_evidence():
    summary = TrainingDaySummaryBuilder.build(
        target=date(2026, 8, 8),
        activities=[{
            "activity_id": "run-1",
            "activity_date": "2026-08-08",
            "activity_type": "running",
            "activity_name": "周末训练",
            "distance_meters": 30000,
            "duration_seconds": 10800,
            "training_analysis": {
                "primary_type": "interval",
                "terrain": "flat",
                "confidence": 0.91,
                "specialties": ["pace_change"],
            },
        }],
        states={"2026-08-08": "synced"},
        facts_cutoff="2026-08-08T20:00:00+08:00",
    )

    assert summary["schema_version"] == "training-day-summary-v1"
    assert summary["status"] == "training"
    assert summary["primary_type"] == "interval"
    assert summary["total_distance_km"] == 30
    assert {item["feature_code"] for item in summary["observed_features"]} == {
        "session_interval", "long_duration",
    }
    evidence = summary["observed_features"][0]["evidence"]
    assert any(item["metric"] == "distance" and item["value"] == 30 for item in evidence)
    assert summary["summary_version"].startswith("training-day-summary-v1:2026-08-08:")
    assert summary["fact_version"] == summary["summary_version"]
    assert summary["evidence"][0]["field"] == "activity_coverage"
    assert any(item["field"] == "sleep" for item in summary["gaps"])
    assert summary["semantic_status"] == "unavailable"
    assert summary["data_quality"]["facts_cutoff"] == "2026-08-08T20:00:00+08:00"


def test_session_metrics_keep_numbers_for_ai_characteristics():
    summary = TrainingDaySummaryBuilder.build(
        target=date(2026, 8, 8),
        activities=[{
            "activity_id": "case-quality",
            "activity_date": "2026-08-08",
            "activity_type": "running",
            "distance_meters": 12000,
            "duration_seconds": 2640,
            "pace_per_km": 220,
            "avg_hr": 158,
            "max_hr": 174,
            "elevation_gain": 86,
            "training_analysis": {
                "primary_type": "tempo",
                "confidence": 0.88,
                "specialties": ["threshold_development", "late_fade"],
            },
        }],
        states={"2026-08-08": "synced"},
    )

    metrics = summary["sessions"][0]["metrics"]
    assert metrics["pace_sec_per_km"] == 220
    assert metrics["avg_heart_rate"] == 158
    assert metrics["max_heart_rate"] == 174
    assert metrics["elevation_gain_m"] == 86
    assert any(
        item.get("metric") == "avg_heart_rate"
        for feature in summary["observed_features"]
        for item in feature.get("evidence", [])
    )


def test_builder_does_not_treat_unknown_day_as_rest():
    summary = TrainingDaySummaryBuilder.build(
        target=date(2026, 8, 9),
        activities=[],
        states={"2026-08-09": "unknown"},
    )

    assert summary["status"] == "unknown"
    assert summary["primary_type"] == "unknown"
    assert summary["data_quality"]["status"] == "unknown"


def test_prepare_daily_analysis_extracts_traceable_quality_session():
    summary = TrainingDaySummaryBuilder.build(
        target=date(2026, 8, 12),
        activities=[{
            "activity_id": "quality-interval",
            "activity_date": "2026-08-12",
            "activity_type": "running",
            "activity_name": "Track workout",
            "distance_meters": 12000,
            "duration_seconds": 3000,
            "avg_heart_rate": 166,
            "training_analysis": {
                "primary_type": "interval",
                "confidence": 0.91,
            },
        }],
        states={"2026-08-12": "synced"},
    )

    prepared = TrainingDaySummaryBuilder.prepare_daily_analysis(summary)

    assert prepared["daily_analysis"]["status"] == "ready"
    assert prepared["daily_analysis"]["analysis_version"].startswith("training-day-analysis-v1:")
    quality = prepared["daily_analysis"]["quality_sessions"][0]
    assert quality["activity_id"] == "quality-interval"
    assert quality["quality_type"] == "interval"
    assert quality["label"] == "间歇"
    assert quality["confidence"] == 0.91
    assert quality["evidence_source"] == "training_session_analysis"
    assert quality["metrics"]["heart_rate"]["value"] == 166


def test_prepare_daily_analysis_excludes_easy_and_low_confidence_sessions():
    summary = TrainingDaySummaryBuilder.build(
        target=date(2026, 8, 13),
        activities=[
            {
                "activity_id": "easy",
                "activity_date": "2026-08-13",
                "activity_type": "running",
                "distance_meters": 8000,
                "duration_seconds": 3000,
                "training_analysis": {"primary_type": "aerobic", "confidence": 0.9},
            },
            {
                "activity_id": "uncertain-tempo",
                "activity_date": "2026-08-13",
                "activity_type": "running",
                "distance_meters": 6000,
                "duration_seconds": 1800,
                "training_analysis": {"primary_type": "tempo", "confidence": 0.4},
            },
        ],
        states={"2026-08-13": "synced"},
    )

    prepared = TrainingDaySummaryBuilder.prepare_daily_analysis(summary)

    assert prepared["daily_analysis"]["quality_sessions"] == []
    assert prepared["daily_analysis"]["quality_session_count"] == 0


def test_aggregate_is_compact_for_draft_history_and_keeps_daily_for_week_review():
    summaries = [
        TrainingDaySummaryBuilder.build(
            target=date(2026, 8, 3),
            activities=[{
                "activity_id": "easy-1", "activity_date": "2026-08-03",
                "activity_type": "running", "distance_meters": 8000,
                "duration_seconds": 3000,
                "training_analysis": {"primary_type": "aerobic", "confidence": 0.7},
            }],
            states={"2026-08-03": "synced"},
        ),
    ]
    compact = TrainingDaySummaryBuilder.aggregate(
        summaries, start=date(2026, 8, 3), end=date(2026, 8, 9),
        include_daily_summaries=False,
    )
    detailed = TrainingDaySummaryBuilder.aggregate(
        summaries, start=date(2026, 8, 3), end=date(2026, 8, 9),
    )

    assert compact["running_distance_km"] == 8
    assert len(compact["summary_versions"]) == 1
    assert compact["daily_summaries"] == []
    assert len(detailed["daily_summaries"]) == 1
    assert detailed["primary_type_counts"] == {"aerobic": 1}
