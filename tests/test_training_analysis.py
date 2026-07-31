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
    )

    assert baseline.sample_count == 2
    assert baseline.threshold_heart_rate < 190


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
    assert baseline.threshold_heart_rate == 150


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
    report = memory.generate_daily_report(7, date(2026, 7, 29))

    analysis = report.front_matter["session_analyses"][0]
    assert analysis["primary_type"] == "interval"
    assert analysis["terrain"] == "hilly"
    assert analysis["algorithm_version"] == "session-analyzer-v1"
    assert "坡地间歇跑" in report.body
    assert any(
        "爬升专项" in item
        for item in report.front_matter["recommendation"]["follow_up_constraints"]
    )


def test_coach_history_tool_prefers_raw_sqlite_history():
    from src.coach import _exec_get_training_history

    class RawHistoryStore:
        def get_training_history_entries(self, days):
            return [{
                "date": "2026-07-29", "is_rest": False,
                "duration": 60, "distance": 10, "load": 90,
                "sleep_h": 7.5, "sleep_score": 80,
                "hrv": 55, "rhr": 48, "bb": 80,
                "recovery": 75, "readiness": 70,
            }]

        def get(self, memory_id):
            raise AssertionError("有 SQLite 历史时不应读取历史日报")

    result = _exec_get_training_history(RawHistoryStore(), 30)

    assert "10.0km" in result
    assert "L90" in result


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


def test_current_week_progress_uses_natural_week_to_date_only():
    from src.coach import _exec_get_current_week_progress

    class WeekHistoryStore:
        def get_training_history_entries(self, *, days, end_date):
            assert days == 3
            assert end_date == date(2026, 7, 29)
            return [
                {
                    "date": "2026-07-29", "activity_state": "training",
                    "is_rest": False, "duration": 50, "distance": 10,
                    "load": 80,
                },
                {
                    "date": "2026-07-28", "activity_state": "unknown",
                    "is_rest": False, "duration": 0, "distance": 0,
                    "load": 0,
                },
                {
                    "date": "2026-07-27", "activity_state": "confirmed_rest",
                    "is_rest": True, "duration": 0, "distance": 0,
                    "load": 0,
                },
            ]

    result = _exec_get_current_week_progress(
        WeekHistoryStore(), target_date=date(2026, 7, 29),
    )

    assert "2026-07-27 至 2026-08-02" in result
    assert "已完成 1 次" in result
    assert "10.0km" in result
    assert "1 天运动数据状态未知" in result


def test_coach_prompt_uses_natural_week_progress_tool():
    from src.coach import load_coach_prompt

    prompt = load_coach_prompt()

    assert "get_current_week_progress" in prompt
    assert "get_training_history(days=7)" not in prompt
