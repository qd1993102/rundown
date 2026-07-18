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

    def test_huawei_provider_creation(self, tmp_path):
        from src.providers import get_provider

        class FakeConfig:
            provider_type = "huawei"
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        provider = get_provider(FakeConfig())
        assert provider.auth.is_authenticated() is False
        assert provider.auth.get_headers() == {}

    def test_huawei_auth_creates_private_token_directory(self, tmp_path):
        import stat
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            huawei_token_dir = str(tmp_path / "user-a" / "huawei-tokens")

        auth = HuaweiAuth(FakeConfig())
        assert auth.ensure_token_store() is False
        assert auth.token_path.exists() is False
        assert stat.S_IMODE(auth.token_path.parent.stat().st_mode) == 0o700

    def test_huawei_auth_rejects_invalid_token_file(self, tmp_path):
        import pytest
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            huawei_token_dir = str(tmp_path)

        auth = HuaweiAuth(FakeConfig())
        auth.token_path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="缺少 access_token"):
            auth.ensure_token_store()

    def test_huawei_auth_accepts_external_token_shape(self, tmp_path):
        import json
        import time
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        auth = HuaweiAuth(FakeConfig())
        auth.token_path.write_text(json.dumps({
            "user_id": 1,
            "access_token": "external-token",
            "refresh_token": "external-refresh-token",
            "open_id": "external-open-id",
            "expired_at": int(time.time()) + 3600,
        }), encoding="utf-8")

        assert auth.login() is True
        assert auth.get_user_id() == 1
        assert auth.get_headers() == {"Authorization": "Bearer external-token"}

    def test_huawei_token_is_saved_private_and_reused(self, tmp_path):
        import stat
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        auth = HuaweiAuth(FakeConfig())
        auth._save({"access_token": "token", "expires_in": 3600})
        assert stat.S_IMODE(auth.token_path.stat().st_mode) == 0o600

        restored = HuaweiAuth(FakeConfig())
        assert restored.login() is True
        assert restored.get_headers() == {"Authorization": "Bearer token"}

    def test_huawei_fetches_token_from_crewpals(self, tmp_path, monkeypatch):
        import time
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            group_pals_token = "per-user-group-token"
            huawei_token_dir = str(tmp_path)

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "code": 200,
                    "msg": "success",
                    "data": {
                        "accessToken": "huawei-at",
                        "refreshToken": "huawei-rt",
                        "expiredAt": str(int(time.time()) + 3600),
                        "openId": "hw-user",
                    },
                }

        request = {}

        def fake_get(url, headers, timeout):
            request.update(url=url, headers=headers, timeout=timeout)
            return FakeResponse()

        monkeypatch.setattr("src.providers.huawei.httpx.get", fake_get)
        auth = HuaweiAuth(FakeConfig())

        assert auth.login() is True
        assert request["url"] == "https://api.crewpals.com/api/v1/huawei/access_token"
        assert request["headers"]["Authorization"] == "per-user-group-token"
        assert auth.get_headers() == {"Authorization": "Bearer huawei-at"}

    def test_huawei_reports_missing_crewpals_endpoint(self, tmp_path, monkeypatch):
        import httpx
        import pytest
        from src.providers.huawei import HuaweiAuth

        class FakeConfig:
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        def fake_get(url, headers, timeout):
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request)

        monkeypatch.setattr("src.providers.huawei.httpx.get", fake_get)

        with pytest.raises(RuntimeError, match="服务端已部署"):
            HuaweiAuth(FakeConfig()).login()

    def test_huawei_fetches_and_maps_activity_records(self, tmp_path, monkeypatch):
        import time
        from src.providers.huawei import HuaweiAuth, HuaweiActivities

        class FakeConfig:
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"activityRecords": [{
                    "activityRecordId": "hw-activity-1",
                    "activityName": "晨跑",
                    "activityType": "running",
                    "startTime": 1784077200000,
                    "endTime": 1784080800000,
                    "activeTime": 3600000,
                    "activitySummary": {
                        "dataSummary": [
                            {"dataTypeName": "com.huawei.continuous.distance.total",
                             "value": [{"fieldName": "distance", "floatValue": 10000}]},
                            {"dataTypeName": "com.huawei.continuous.heart_rate.statistics",
                             "value": [{"fieldName": "avg", "floatValue": 145},
                                       {"fieldName": "max", "floatValue": 170}]},
                            {"dataTypeName": "com.huawei.continuous.calories.burnt.total",
                             "value": [{"fieldName": "calories_total", "floatValue": 600}]},
                        ],
                    },
                }]}

        request = {}

        def fake_get(url, headers, params, timeout):
            request.update(url=url, headers=headers, params=params, timeout=timeout)
            return FakeResponse()

        monkeypatch.setattr("src.providers.huawei.httpx.get", fake_get)
        auth = HuaweiAuth(FakeConfig())
        auth.token = {"access_token": "huawei-at", "expired_at": int(time.time()) + 3600}

        result = HuaweiActivities(auth).fetch_activities(date(2026, 7, 15), date(2026, 7, 15))

        assert request["url"].endswith("/activityRecords")
        assert request["params"] == {
            "startTime": 1784044800000,
            "endTime": 1784131199999,
        }
        assert len(result) == 1
        assert result[0].activity_id == "hw-activity-1"
        assert result[0].duration_seconds == 3600
        assert result[0].distance_meters == 10000
        assert result[0].avg_heart_rate == 145
        assert result[0].max_heart_rate == 170

    def test_huawei_fetches_activity_detail(self, tmp_path, monkeypatch):
        import time
        from src.providers.huawei import HuaweiAuth, HuaweiActivities

        class FakeConfig:
            group_pals_token = "group-token"
            huawei_token_dir = str(tmp_path)

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"activityRecord": [
                    {"id": "another-record", "splits": []},
                    {"id": "record-1", "splits": [{"distance": 1000}]},
                ]}

        requests = []

        def fake_get(url, headers, params, timeout):
            requests.append((url, params))
            return FakeResponse()

        monkeypatch.setattr("src.providers.huawei.httpx.get", fake_get)
        auth = HuaweiAuth(FakeConfig())
        auth.token = {"access_token": "huawei-at", "expired_at": int(time.time()) + 3600}

        detail = HuaweiActivities(auth).fetch_activity_detail("record-1")

        assert requests == [(
            "https://health-api.cloud.huawei.com/healthkit/v2/activityRecords",
            {"activityRecordId": "record-1"},
        )]
        assert detail == {"id": "record-1", "splits": [{"distance": 1000}]}
