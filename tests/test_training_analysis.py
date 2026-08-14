"""训练内容识别：事实归一化、个人基线、课型与地形分类。"""

from datetime import date


def _baseline(*, threshold_pace: float = 300, threshold_hr: int = 170):
    from src.training_analysis import AthleteBaseline

    return AthleteBaseline(
        threshold_pace_sec_per_km=threshold_pace,
        threshold_heart_rate=threshold_hr,
        sample_count=8,
        status="sufficient",
        source="recent_activities",
    )


def test_interval_structure_and_hilly_terrain_are_independent():
    from src.training_analysis import TrainingSessionAnalyzer

    activity = {
        "activity_id": "hill-intervals",
        "activity_name": "周二训练",
        "activity_type": "running",
        "duration_seconds": 3600,
        "distance_meters": 8000,
        "avg_heart_rate": 158,
        "elevation_gain": 240,
        "training_load": 145,
    }
    splits = [
        {"type": "WARMUP", "distance_m": 1500, "duration_sec": 630, "avg_hr": 140},
        {"type": "INTERVAL_ACTIVE", "distance_m": 800, "duration_sec": 230, "avg_hr": 166, "elevation_gain": 35},
        {"type": "RECOVERY", "distance_m": 400, "duration_sec": 190, "avg_hr": 145},
        {"type": "INTERVAL_ACTIVE", "distance_m": 800, "duration_sec": 228, "avg_hr": 168, "elevation_gain": 36},
        {"type": "RECOVERY", "distance_m": 400, "duration_sec": 192, "avg_hr": 146},
        {"type": "INTERVAL_ACTIVE", "distance_m": 800, "duration_sec": 226, "avg_hr": 169, "elevation_gain": 38},
        {"type": "RECOVERY", "distance_m": 400, "duration_sec": 195, "avg_hr": 147},
    ]

    result = TrainingSessionAnalyzer().analyze(activity, splits, _baseline())

    assert result.primary_type == "interval"
    assert result.terrain == "hilly"
    assert "hill_repeats" in result.specialties
    assert result.confidence >= 0.8
    assert any("工作" in item and "恢复" in item for item in result.evidence)


def test_work_labels_without_recovery_structure_are_not_intervals():
    from src.training_analysis import TrainingSessionAnalyzer

    result = TrainingSessionAnalyzer().analyze({
        "activity_id": "work-only", "activity_name": "连续跑",
        "activity_type": "running", "duration_seconds": 2400,
        "distance_meters": 8000, "avg_heart_rate": 166,
        "elevation_gain": 10,
    }, [
        {"type": "INTERVAL_ACTIVE", "distance_m": 2000, "duration_sec": 600},
        {"type": "INTERVAL_ACTIVE", "distance_m": 2000, "duration_sec": 600},
    ], _baseline())

    assert result.primary_type == "tempo"


def test_same_absolute_pace_is_interpreted_against_personal_baseline():
    from src.training_analysis import TrainingSessionAnalyzer

    activity = {
        "activity_id": "same-pace",
        "activity_name": "持续跑",
        "activity_type": "running",
        "duration_seconds": 3300,
        "distance_meters": 10000,
        "avg_heart_rate": None,
        "elevation_gain": 20,
    }
    analyzer = TrainingSessionAnalyzer()

    faster_athlete = analyzer.analyze(
        activity, [], _baseline(threshold_pace=300),
    )
    slower_athlete = analyzer.analyze(
        activity, [], _baseline(threshold_pace=360),
    )

    assert faster_athlete.primary_type == "aerobic"
    assert slower_athlete.primary_type == "tempo"


def test_elevation_alone_does_not_prove_trail_or_mountain():
    from src.training_analysis import TrainingSessionAnalyzer

    result = TrainingSessionAnalyzer().analyze({
        "activity_id": "road-climb",
        "activity_name": "公路长坡",
        "activity_type": "running",
        "duration_seconds": 7200,
        "distance_meters": 15000,
        "avg_heart_rate": 148,
        "elevation_gain": 800,
    }, [], _baseline())

    assert result.terrain == "hilly"
    assert result.terrain != "trail"
    assert result.terrain != "mountain"


def test_explicit_trail_evidence_and_climbing_can_identify_mountain_session():
    from src.training_analysis import TrainingSessionAnalyzer

    result = TrainingSessionAnalyzer().analyze({
        "activity_id": "mountain-trail",
        "activity_name": "山地越野跑",
        "activity_type": "trail_running",
        "duration_seconds": 9000,
        "distance_meters": 18000,
        "avg_heart_rate": 150,
        "elevation_gain": 1100,
    }, [], _baseline())

    assert result.terrain == "mountain"
    assert "mountain_endurance" in result.specialties
    assert any("爬升" in item for item in result.evidence)


def test_baseline_builder_uses_only_activities_before_target_time():
    from src.training_analysis import AthleteBaselineBuilder

    activities = [
        {
            "activity_id": "before-1", "activity_type": "running",
            "start_time": "2026-07-01 08:00:00", "duration_seconds": 3600,
            "distance_meters": 10000, "avg_heart_rate": 150,
        },
        {
            "activity_id": "before-2", "activity_type": "running",
            "start_time": "2026-07-10 08:00:00", "duration_seconds": 3300,
            "distance_meters": 10000, "avg_heart_rate": 160,
        },
        {
            "activity_id": "future", "activity_type": "running",
            "start_time": "2026-08-02 08:00:00", "duration_seconds": 2400,
            "distance_meters": 10000, "avg_heart_rate": 190,
        },
    ]

    baseline = AthleteBaselineBuilder().build(
        activities, target_time="2026-07-31 12:00:00",
        profile={"personal_info": {"max_heart_rate": 200, "resting_heart_rate": 50}},
    )

    assert baseline.sample_count == 2
    # 阈值只来自档案推导（Karvonen：50 + 0.88×(200−50) = 182），不被未来活动抬高
    assert baseline.threshold_heart_rate == 182
    assert baseline.source == "profile"


def test_baseline_builder_ignores_activities_older_than_42_days():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build([
        {
            "activity_id": "too-old", "activity_type": "running",
            "start_time": "2026-05-01 08:00:00", "duration_seconds": 2400,
            "distance_meters": 10000, "avg_heart_rate": 190,
        },
        {
            "activity_id": "recent", "activity_type": "running",
            "start_time": "2026-07-20 08:00:00", "duration_seconds": 3600,
            "distance_meters": 10000, "avg_heart_rate": 150,
        },
    ], target_time="2026-07-31 12:00:00")

    assert baseline.sample_count == 1
    # 无档案阈值时不猜测（不再拿单一活动平均心率当阈值）
    assert baseline.threshold_heart_rate is None
    assert baseline.source == "unavailable"


def test_baseline_threshold_derived_from_profile_max_and_resting_hr():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build(
        [],
        profile={"personal_info": {"max_heart_rate": 190, "resting_heart_rate": 55}},
    )
    assert baseline.threshold_heart_rate == round(55 + 0.88 * (190 - 55))
    assert baseline.source == "profile"


def test_baseline_threshold_derived_from_profile_max_hr_only():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build(
        [], profile={"personal_info": {"max_heart_rate": 190}},
    )
    assert baseline.threshold_heart_rate == round(190 * 0.88)


def test_baseline_explicit_threshold_hr_wins_over_max_hr():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build(
        [],
        profile={
            "threshold_heart_rate": 170,
            "personal_info": {"max_heart_rate": 190, "resting_heart_rate": 55},
        },
    )
    assert baseline.threshold_heart_rate == 170


def test_baseline_without_profile_does_not_guess():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build([])
    assert baseline.threshold_heart_rate is None
    assert baseline.threshold_pace_sec_per_km is None
    assert baseline.status == "insufficient"
    assert baseline.source == "unavailable"


def test_baseline_threshold_pace_derived_from_personal_bests():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build(
        [], profile={"personal_bests": {"half_marathon": {"time": "1:40:00"}}},
    )
    # 半马 100min → 6000s / 21.0975km ≈ 284.4s/km，最接近阈值配速
    assert baseline.threshold_heart_rate is None
    assert baseline.threshold_pace_sec_per_km is not None
    assert abs(baseline.threshold_pace_sec_per_km - (6000 / 21.0975)) < 1
    assert baseline.source == "personal_bests"
    assert baseline.status == "sufficient"


def test_baseline_explicit_threshold_pace_wins_over_pb():
    from src.training_analysis import AthleteBaselineBuilder

    baseline = AthleteBaselineBuilder().build(
        [],
        profile={
            "threshold_pace_sec_per_km": 300,
            "personal_bests": {"half_marathon": {"time": "1:40:00"}},
        },
    )
    assert baseline.threshold_pace_sec_per_km == 300
    assert baseline.source == "profile"


def test_raw_history_does_not_require_daily_report(tmp_path):
    from sqlalchemy import text

    from src.config import Config
    from src.memory import MemoryStore
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities
            (user_id, activity_id, activity_date, activity_name,
             duration_seconds, avg_heart_rate, training_load, start_time)
        VALUES
            (7, 'raw-only', '2026-07-29', '晨跑', 3600, 145, 90,
             '2026-07-29 07:00:00')
    """))
    session.commit()
    session.close()

    memory = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )
    entries = memory.get_training_history_entries(
        user_id=7, days=3, end_date=date(2026, 7, 30),
    )

    training_day = next(item for item in entries if item["date"] == "2026-07-29")
    assert training_day["is_rest"] is False
    assert training_day["duration"] == 60
    assert training_day["load"] == 90


def test_analysis_persistence_is_idempotent_and_versioned(tmp_path):
    from src.config import Config
    from src.storage import Storage
    from src.training_analysis import (
        ActivityFactsNormalizer,
        TrainingSessionAnalyzer,
        get_latest_training_analysis,
        save_training_analysis,
    )

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    activity = {
        "activity_id": "persisted-analysis",
        "activity_name": "有氧跑",
        "activity_type": "running",
        "duration_seconds": 3000,
        "distance_meters": 8000,
        "avg_heart_rate": 145,
        "elevation_gain": 30,
    }
    baseline = _baseline()
    facts = ActivityFactsNormalizer().normalize(activity, [])
    analysis = TrainingSessionAnalyzer().analyze(activity, [], baseline)

    first_id = save_training_analysis(
        storage.db, 7, facts, baseline, analysis,
    )
    second_id = save_training_analysis(
        storage.db, 7, facts, baseline, analysis,
    )
    latest = get_latest_training_analysis(
        storage.db, 7, "persisted-analysis",
    )

    assert first_id == second_id
    assert latest is not None
    assert latest["analysis_id"] == first_id
    assert latest["primary_type"] == "aerobic"
    assert latest["algorithm_version"] == "session-analyzer-v1"
    assert latest["features"]["activity_id"] == "persisted-analysis"


def test_daily_report_contains_structured_training_analysis(tmp_path):
    from sqlalchemy import text

    from src.activity import ensure_tables, store_activity_detail
    from src.config import Config
    from src.main import _ensure_activity_columns
    from src.memory import MemoryStore
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    _ensure_activity_columns(storage)
    ensure_tables(storage)
    session = storage.db.get_session()
    for index, (day, duration, distance, hr) in enumerate((
        ("2026-07-26", 3600, 10000, 148),
        ("2026-07-27", 3300, 10000, 155),
        ("2026-07-28", 3000, 10000, 162),
    )):
        session.execute(text("""
            INSERT INTO activities
                (user_id, activity_id, activity_date, activity_name,
                 activity_type, duration_seconds, distance_meters,
                 avg_heart_rate, training_load, elevation_gain, start_time)
            VALUES
                (7, :aid, :day, '基线跑', 'running', :duration, :distance,
                 :hr, 80, 20, :start_time)
        """), {
            "aid": f"baseline-{index}", "day": day,
            "duration": duration, "distance": distance, "hr": hr,
            "start_time": f"{day} 07:00:00",
        })
    session.execute(text("""
        INSERT INTO activities
            (user_id, activity_id, activity_date, activity_name,
             activity_type, duration_seconds, distance_meters,
             avg_heart_rate, training_load, elevation_gain, start_time)
        VALUES
            (7, 'report-interval', '2026-07-29', '坡道训练', 'running',
             3600, 8000, 160, 150, 240, '2026-07-29 07:00:00')
    """))
    session.commit()
    session.close()
    store_activity_detail(storage, 7, "report-interval", {
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "distance": 800, "duration": 230},
            {"splitType": "RECOVERY", "distance": 400, "duration": 190},
            {"splitType": "INTERVAL_ACTIVE", "distance": 800, "duration": 228},
        ]
    })

    memory = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )
    report = memory.generate_daily_report(
        7,
        date(2026, 7, 29),
        readiness={
            "status": "ready",
            "finality": "final",
            "data_as_of": "2026-07-29 08:00:00",
            "dimensions": {},
            "omitted_sections": [],
        },
    )

    analysis = report.front_matter["session_analyses"][0]
    assert analysis["primary_type"] == "interval"
    assert analysis["terrain"] == "hilly"
    assert analysis["algorithm_version"] == "session-analyzer-v1"
    assert "坡地间歇跑" in report.body
    assert any(
        "爬升专项" in item
        for item in report.front_matter["recommendation"]["follow_up_constraints"]
    )


def test_natural_week_bounds_are_monday_through_sunday():
    from src.training_analysis import natural_week_bounds

    monday, sunday = natural_week_bounds(date(2026, 7, 29))

    assert monday == date(2026, 7, 27)
    assert sunday == date(2026, 8, 2)


def test_history_does_not_treat_unsynced_empty_day_as_rest(tmp_path):
    from sqlalchemy import text

    from src.config import Config
    from src.memory import MemoryStore
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data" / "data.db")))
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO sync_status
            (user_id, sync_date, metric_type, status, synced_at, created_at)
        VALUES
            (7, '2026-07-28', 'neurun_provider_sync', 'completed',
             datetime('now'), datetime('now'))
    """))
    session.commit()
    session.close()

    memory = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )
    entries = memory.get_training_history_entries(
        user_id=7, days=2, end_date=date(2026, 7, 29),
    )
    by_date = {entry["date"]: entry for entry in entries}

    assert by_date["2026-07-28"]["activity_state"] == "confirmed_rest"
    assert by_date["2026-07-28"]["is_rest"] is True
    assert by_date["2026-07-29"]["activity_state"] == "unknown"
    assert by_date["2026-07-29"]["is_rest"] is False


def test_platform_thresholds_load_from_health_metrics(tmp_path):
    """平台乳酸阈值（lthr/ltsp）从 daily_health_metrics 读取，取目标日及之前最近一条。"""
    from sqlalchemy import text

    from src.config import Config
    from src.memory import load_platform_thresholds
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data" / "data.db")))
    session = storage.db.get_session()
    try:
        session.execute(text("ALTER TABLE daily_health_metrics ADD COLUMN lthr INTEGER"))
    except Exception:
        pass
    try:
        session.execute(text("ALTER TABLE daily_health_metrics ADD COLUMN ltsp INTEGER"))
    except Exception:
        pass
    session.execute(text("""
        INSERT INTO daily_health_metrics
            (user_id, metric_date, resting_heart_rate, lthr, ltsp, created_at, updated_at)
        VALUES
            (7, '2026-07-20', 55, 169, 265, datetime('now'), datetime('now')),
            (7, '2026-07-28', 53, 170, 260, datetime('now'), datetime('now'))
    """))
    session.commit()
    session.close()

    # 目标日当天及之前最近一条
    assert load_platform_thresholds(storage.db, 7, date(2026, 7, 20)) == {
        "threshold_heart_rate": 169, "threshold_pace_sec_per_km": 265,
    }
    # 取 <= 目标日最近一条（7-28）
    assert load_platform_thresholds(storage.db, 7, date(2026, 7, 29)) == {
        "threshold_heart_rate": 170, "threshold_pace_sec_per_km": 260,
    }
    # 早于任何记录 → 空
    assert load_platform_thresholds(storage.db, 7, date(2026, 7, 1)) == {}
    storage.close()


def test_platform_threshold_merges_into_baseline_profile():
    """平台阈值并入档案后 AthleteBaseline 使用它；档案显式阈值优先。"""
    from unittest import mock

    from src.memory import MemoryWriter
    from src.training_analysis import AthleteBaselineBuilder

    fake = {"threshold_heart_rate": 170, "threshold_pace_sec_per_km": 260}
    with mock.patch("src.memory.load_platform_thresholds", return_value=fake):
        merged = MemoryWriter._merge_platform_thresholds(
            {"personal_info": {}}, None, 7, "2026-07-29 08:00:00",
        )
    assert merged["threshold_heart_rate"] == 170
    assert merged["threshold_pace_sec_per_km"] == 260
    baseline = AthleteBaselineBuilder().build([], profile=merged)
    assert baseline.threshold_heart_rate == 170
    assert baseline.threshold_pace_sec_per_km == 260
    assert baseline.source == "profile"

    # 档案显式阈值优先，平台值不覆盖
    with mock.patch("src.memory.load_platform_thresholds", return_value=fake):
        merged2 = MemoryWriter._merge_platform_thresholds(
            {"personal_info": {}, "threshold_heart_rate": 180}, None, 7,
            "2026-07-29 08:00:00",
        )
    assert merged2["threshold_heart_rate"] == 180
    assert merged2["threshold_pace_sec_per_km"] == 260
