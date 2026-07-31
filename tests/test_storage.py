"""测试 storage.py — garmy 存储兼容层。"""

import logging
import stat
from datetime import date

import pytest


def test_progress_reporter_gets_warning_compatibility(caplog):
    """旧版 garmy ProgressReporter 缺少 warning 时不应覆盖原始同步异常。"""
    from src.storage import _ensure_progress_reporter_compat

    class Reporter:
        pass

    reporter = Reporter()
    _ensure_progress_reporter_compat(reporter)

    with caplog.at_level(logging.WARNING, logger="src.storage"):
        reporter.warning("activity fetch failed")

    assert "activity fetch failed" in caplog.text


def test_garmy_progress_reporter_emits_real_item_progress():
    """长耗时指标阶段必须把 garmy 的逐项事件转换为可持久化进度。"""
    from src.storage import GarmySyncProgressReporter

    updates = []
    reporter = GarmySyncProgressReporter(
        updates.append, min_emit_interval=0,
    )
    first_day = date(2026, 4, 1)
    second_day = date(2026, 4, 2)

    reporter.start_sync(3)
    reporter.task_complete("sleep", first_day)
    reporter.task_skipped("stress", first_day)
    reporter.task_failed("heart_rate", second_day)
    reporter.end_sync()

    assert updates == [
        {"current": 0, "total": 3, "date": None, "metric": None,
         "outcome": "started"},
        {"current": 1, "total": 3, "date": "2026-04-01",
         "metric": "sleep", "outcome": "completed"},
        {"current": 2, "total": 3, "date": "2026-04-01",
         "metric": "stress", "outcome": "skipped"},
        {"current": 3, "total": 3, "date": "2026-04-02",
         "metric": "heart_rate", "outcome": "failed"},
    ]


def test_garmy_progress_reporter_throttles_fast_same_date_updates(monkeypatch):
    """同一天的快速事件应节流，但换日和最终项必须立即落盘。"""
    import src.storage as storage_module

    ticks = iter([0.0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(storage_module.time, "monotonic", lambda: next(ticks))
    updates = []
    reporter = storage_module.GarmySyncProgressReporter(
        updates.append, min_emit_interval=1,
    )

    reporter.start_sync(3)
    reporter.task_complete("sleep", date(2026, 4, 1))
    reporter.task_complete("stress", date(2026, 4, 1))
    reporter.task_complete("sleep", date(2026, 4, 2))

    assert [(item["current"], item["date"]) for item in updates] == [
        (0, None), (1, "2026-04-01"), (3, "2026-04-02"),
    ]


def test_storage_close_releases_http_sessions_and_sqlite_engines():
    """一次同步结束后应显式释放网络连接池与两个 SQLite Engine。"""
    from types import SimpleNamespace

    from src.storage import Storage

    class Closeable:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    class Engine:
        def __init__(self):
            self.disposed = 0

        def dispose(self):
            self.disposed += 1

    session = Closeable()
    api_client = SimpleNamespace(
        http_client=SimpleNamespace(session=session)
    )
    primary_engine = Engine()
    sync_engine = Engine()

    storage = Storage.__new__(Storage)
    storage._db = SimpleNamespace(engine=primary_engine)
    storage._sync_manager = SimpleNamespace(
        api_client=api_client,
        db=SimpleNamespace(engine=sync_engine),
    )
    storage._injected_client = api_client
    storage._initialized = True

    storage.close()

    assert session.closed == 1
    assert primary_engine.disposed == 1
    assert sync_engine.disposed == 1
    assert storage._db is None
    assert storage._sync_manager is None


def test_non_garmin_resync_updates_existing_activity_duration(tmp_path):
    """修正 Provider 映射后，普通重同步也应更新已有活动时长。"""
    from sqlalchemy import text

    from src.config import Config
    from src.main import _sync_provider
    from src.providers.base import ActivityData
    from src.storage import Storage

    activity = ActivityData(
        activity_id="coros-1",
        activity_name="Paused Run",
        activity_type="running",
        start_time="2026-07-19 08:00:00",
        duration_seconds=4200,
        distance_meters=10000,
        elevation_gain=120,
    )

    class Activities:
        def fetch_activities(self, start, end):
            return [activity]

    class Health:
        def fetch_health_range(self, start, end):
            return []

    class Provider:
        activities = Activities()
        health = Health()

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    target = date(2026, 7, 19)
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    activity.duration_seconds = 3600
    activity.elevation_gain = 180
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    session = storage.db.get_session()
    row = session.execute(text(
        "SELECT duration_seconds, activity_type, elevation_gain FROM activities "
        "WHERE activity_id = 'coros-1'"
    )).one()
    session.close()
    assert row.duration_seconds == 3600
    assert row.activity_type == "running"
    assert row.elevation_gain == 180


def test_activity_detail_resync_replaces_splits_instead_of_duplicating(tmp_path):
    from sqlalchemy import text

    from src.activity import ensure_tables, store_activity_detail
    from src.config import Config
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    ensure_tables(storage)
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (1, 'detail-1', '2026-07-29')
    """))
    session.commit()
    session.close()
    detail = {
        "splitSummaries": [
            {"splitType": "INTERVAL_ACTIVE", "distance": 1000, "duration": 240},
            {"splitType": "RECOVERY", "distance": 400, "duration": 180},
        ]
    }

    store_activity_detail(storage, 1, "detail-1", detail)
    store_activity_detail(storage, 1, "detail-1", detail)

    session = storage.db.get_session()
    count = session.execute(text(
        "SELECT COUNT(*) FROM activity_splits WHERE activity_id = 'detail-1'"
    )).scalar_one()
    session.close()
    assert count == 2


def test_provider_sync_persists_available_activity_splits(tmp_path):
    from src.activity import get_activity_splits
    from src.config import Config
    from src.main import _sync_provider
    from src.providers.base import ActivityData
    from src.storage import Storage

    target = date(2026, 7, 29)
    activity = ActivityData(
        activity_id="coros-detail", activity_name="间歇训练",
        activity_type="running", start_time="2026-07-29 07:00:00",
        duration_seconds=3000, distance_meters=8000,
    )

    class Activities:
        def fetch_activities(self, start, end):
            return [activity]

        def fetch_activity_detail(self, activity_id):
            return {"data": {"laps": [
                {"type": "INTERVAL_ACTIVE", "distanceMeters": 800,
                 "durationSeconds": 230},
                {"type": "RECOVERY", "distanceMeters": 400,
                 "durationSeconds": 190},
            ]}}

    class Health:
        def fetch_health_range(self, start, end):
            return []

    provider = type("Provider", (), {
        "activities": Activities(), "health": Health(),
    })()
    storage = Storage(Config(db_path=str(tmp_path / "data.db")))

    _sync_provider(provider, storage, 1, target, target, "coros")

    splits = get_activity_splits(storage, "coros-detail")
    assert [split["type"] for split in splits] == ["INTERVAL_ACTIVE", "RECOVERY"]


def test_non_garmin_resync_merges_sleep_into_existing_health(tmp_path):
    """Coros 重新授权后，普通批量同步应补齐睡眠且保留已有 RHR。"""
    from sqlalchemy import text

    from src.config import Config
    from src.main import _sync_provider
    from src.providers.base import DailyHealth
    from src.storage import Storage

    target = date(2026, 7, 27)

    class Activities:
        def fetch_activities(self, start, end):
            return []

    class Health:
        records = [DailyHealth(metric_date=target, resting_heart_rate=48)]

        def fetch_health_range(self, start, end):
            return self.records

    class Provider:
        activities = Activities()
        health = Health()

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    Provider.health.records = [DailyHealth(
        metric_date=target,
        sleep_duration_hours=7.5,
        deep_sleep_hours=1.5,
        rem_sleep_hours=1.25,
        deep_sleep_pct=20.0,
        rem_sleep_pct=16.67,
    )]
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    session = storage.db.get_session()
    row = session.execute(text("""
        SELECT sleep_duration_hours, deep_sleep_hours, rem_sleep_hours,
               resting_heart_rate
        FROM daily_health_metrics
        WHERE user_id = 1 AND metric_date = '2026-07-27'
    """)).one()
    session.close()
    assert row.sleep_duration_hours == 7.5
    assert row.deep_sleep_hours == 1.5
    assert row.rem_sleep_hours == 1.25
    assert row.resting_heart_rate == 48


def test_coros_sync_maps_provider_item_progress_to_web_stages(tmp_path):
    """Coros 活动分页和逐日健康进度应映射到各自四阶段。"""
    from src.config import Config
    from src.main import _sync_provider
    from src.storage import Storage

    target = date(2026, 7, 27)

    class Activities:
        def fetch_activities(self, start, end, *, progress_callback=None):
            progress_callback({
                "current": 2, "total": 2, "date": None,
                "metric": "activities", "outcome": "completed",
            })
            return []

    class Health:
        def fetch_health_range(self, start, end, *, progress_callback=None):
            progress_callback({
                "current": 1, "total": 1, "date": "2026-07-27",
                "metric": "daily_health", "outcome": "skipped",
            })
            return []

    class Provider:
        activities = Activities()
        health = Health()

    updates = []
    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    _sync_provider(
        Provider(), storage, 1, target, target, "coros",
        progress_callback=lambda *args: updates.append(args),
    )

    assert updates == [
        ("syncing_activities", 2, 4, "正在同步运动记录"),
        ("syncing_activities", 2, 4, "正在同步运动记录", {
            "current": 2, "total": 2, "date": None,
            "metric": "activities", "outcome": "completed",
        }),
        ("syncing_metrics", 3, 4, "正在同步健康指标"),
        ("syncing_metrics", 3, 4, "正在同步健康指标", {
            "current": 1, "total": 1, "date": "2026-07-27",
            "metric": "daily_health", "outcome": "skipped",
        }),
    ]


def test_get_local_user_id_reads_unique_id_without_remote_provider(tmp_path):
    from sqlalchemy import text

    from src.config import Config
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (412749563, 'local-1', '2026-07-19')
    """))
    session.commit()
    session.close()

    assert storage.get_local_user_id() == 412749563


def test_get_local_user_id_rejects_mixed_user_database(tmp_path):
    from sqlalchemy import text

    from src.config import Config
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES
            (1, 'local-1', '2026-07-19'),
            (2, 'local-2', '2026-07-19')
    """))
    session.commit()
    session.close()

    with pytest.raises(RuntimeError, match="多个平台用户 ID"):
        storage.get_local_user_id()


def test_get_local_user_id_ignores_failed_calendar_only_sync(tmp_path):
    from src.config import Config
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 26), date(2026, 7, 26), "failed",
    )

    assert storage.get_local_user_id() is None

    storage.mark_sync_calendar_range(
        7, date(2026, 7, 26), date(2026, 7, 26), "completed",
    )
    assert storage.get_local_user_id() == 7


def test_storage_database_and_backup_are_private(tmp_path):
    from src.config import Config
    from src.storage import Storage

    db_path = tmp_path / "rd_test" / "data.db"
    backup_path = tmp_path / "backup" / "rd_test.db"
    storage = Storage(Config(db_path=str(db_path)))

    _ = storage.db
    storage.backup_to(backup_path)

    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600


def test_storage_exports_are_private_files_without_changing_selected_directory(tmp_path):
    from src.config import Config
    from src.storage import Storage

    output_dir = tmp_path / "shared-output"
    output_dir.mkdir(mode=0o755)
    storage = Storage(Config(db_path=str(tmp_path / "data" / "data.db")))
    csv_path = output_dir / "activities.csv"
    json_path = output_dir / "activities.json"

    storage.export_csv([{"activity_id": "1"}], str(csv_path))
    storage.export_json([{"activity_id": "1"}], str(json_path))

    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE(csv_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(json_path.stat().st_mode) == 0o600


def test_sync_calendar_tracks_empty_days_and_aggregates_legacy_data(tmp_path):
    from sqlalchemy import text

    from src.config import Config
    from src.storage import Storage

    storage = Storage(Config(db_path=str(tmp_path / "data" / "data.db")))
    session = storage.db.get_session()
    session.execute(text("ALTER TABLE activities ADD COLUMN activity_type VARCHAR"))
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (7, 'legacy-activity', '2026-07-20')
    """))
    session.execute(text("""
        INSERT INTO activities
            (user_id, activity_id, activity_date, activity_name, activity_type)
        VALUES
            (7, 'name-running', '2026-07-18', 'Morning Run', NULL),
            (7, 'type-running', '2026-07-17', '晨练', 'running_indoor')
    """))
    session.execute(text("""
        INSERT INTO sync_status
            (user_id, sync_date, metric_type, status, synced_at)
        VALUES (7, '2026-07-19', 'activities', 'completed', datetime('now'))
    """))
    session.commit()
    session.close()

    storage.mark_sync_calendar_range(
        7, date(2026, 7, 21), date(2026, 7, 22), "completed",
    )
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 17), date(2026, 7, 18), "failed",
    )
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 23), date(2026, 7, 23), "failed",
        error_message="provider timeout",
    )
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 24), date(2026, 7, 24), "pending",
    )

    result = storage.get_sync_calendar(
        7, date(2026, 7, 17), date(2026, 7, 27),
        today=date(2026, 7, 26),
    )
    days = {item["date"]: item for item in result["days"]}

    assert days["2026-07-17"]["status"] == "synced"
    assert days["2026-07-17"]["running_count"] == 1
    assert days["2026-07-18"]["status"] == "synced"
    assert days["2026-07-18"]["running_count"] == 1
    assert days["2026-07-19"]["status"] == "partial"
    assert days["2026-07-20"]["status"] == "partial"
    assert days["2026-07-20"]["activity_count"] == 1
    assert days["2026-07-21"]["status"] == "synced"
    assert days["2026-07-22"]["status"] == "synced"
    assert days["2026-07-23"]["status"] == "failed"
    assert days["2026-07-24"]["status"] == "syncing"
    assert days["2026-07-25"]["status"] == "unsynced"
    assert days["2026-07-27"]["status"] == "future"
    assert result["summary"]["latest_synced_date"] == "2026-07-22"
    assert result["summary"]["synced"] == 4
