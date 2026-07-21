"""测试 storage.py — garmy 存储兼容层。"""

import logging
from datetime import date


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
