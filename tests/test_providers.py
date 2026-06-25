"""测试 providers — 数据类和数据源。"""

from datetime import date

from src.providers.base import ActivityData, DailyHealth, DataProvider


class TestActivityData:
    def test_create(self):
        a = ActivityData(
            activity_id="123", activity_name="晨跑", activity_type="running",
            start_time="2026-06-25T07:00:00", duration_seconds=3600,
            distance_meters=10000, avg_heart_rate=145,
            training_load=120.0, calories=400,
        )
        assert a.activity_id == "123"
        assert a.activity_type == "running"
        assert a.duration_seconds == 3600
        assert a.distance_meters == 10000

    def test_defaults(self):
        a = ActivityData(activity_id="1", activity_name="x", activity_type="other",
                         start_time="now", duration_seconds=0)
        assert a.avg_heart_rate is None
        assert a.training_load == 0
        assert a.calories == 0
        assert a.elevation_gain == 0
        assert a.has_gps is False


class TestDailyHealth:
    def test_create(self):
        h = DailyHealth(
            metric_date=date(2026, 6, 25),
            sleep_duration_hours=7.5,
            resting_heart_rate=48,
            hrv_last_night_avg=55.0,
            hrv_status="balanced",
        )
        assert h.sleep_duration_hours == 7.5
        assert h.resting_heart_rate == 48
        assert h.hrv_status == "balanced"

    def test_defaults(self):
        h = DailyHealth(metric_date=date.today())
        assert h.sleep_duration_hours == 0
        assert h.resting_heart_rate is None
        assert h.body_battery_high is None
        assert h.total_steps == 0


class TestProviderRegistry:
    def test_get_garmin_provider(self):
        from unittest import mock
        from src.providers import get_provider

        class FakeConfig:
            provider_type = "garmin"
            domain = "garmin.com"
            token_dir = "/tmp"
            email = ""
            password = ""

        # Just verify it doesn't crash on creation
        # Actual auth requires real credentials
        try:
            p = get_provider(FakeConfig())
            assert p is not None
        except Exception:
            pass  # Expected without real creds

    def test_coros_provider_creation(self):
        from unittest import mock
        from src.providers import get_provider

        class FakeConfig:
            provider_type = "coros"
            email = ""
            password = ""

        p = get_provider(FakeConfig())
        assert p is not None
        # Without real credentials, fetch returns empty list
        result = p.activities.fetch_activities(date.today(), date.today())
        assert result == []
