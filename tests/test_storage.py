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
    )

    class Activities:
        def fetch_activities(self, start, end):
            return [activity]

    class Health:
        def fetch_daily_health(self, target):
            return None

    class Provider:
        activities = Activities()
        health = Health()

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    target = date(2026, 7, 19)
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    activity.duration_seconds = 3600
    _sync_provider(Provider(), storage, 1, target, target, "coros")

    session = storage.db.get_session()
    duration = session.execute(text(
        "SELECT duration_seconds FROM activities WHERE activity_id = 'coros-1'"
    )).scalar_one()
    session.close()
    assert duration == 3600


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
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (7, 'legacy-activity', '2026-07-20')
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
        7, date(2026, 7, 23), date(2026, 7, 23), "failed",
        error_message="provider timeout",
    )
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 24), date(2026, 7, 24), "pending",
    )

    result = storage.get_sync_calendar(
        7, date(2026, 7, 19), date(2026, 7, 27),
        today=date(2026, 7, 26),
    )
    days = {item["date"]: item for item in result["days"]}

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
    assert result["summary"]["synced"] == 2
