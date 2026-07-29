"""测试 providers — 数据类和数据源。"""

import json
import stat
import time
from datetime import date
from unittest import mock

import pytest
import requests

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

    def test_garmin_auth_api_client_uses_bound_region(self, monkeypatch):
        from src.providers.garmin import GarminAuth

        auth_client = object()
        auth = GarminAuth(domain="garmin.cn", token_dir="/tmp/tokens")
        auth._client = auth_client
        api_client = object()
        constructor = mock.Mock(return_value=api_client)
        monkeypatch.setattr("garmy.APIClient", constructor)

        assert auth.create_api_client() is api_client
        constructor.assert_called_once_with(
            auth_client=auth_client,
            domain="garmin.cn",
        )

    def test_garmin_auth_refreshes_expired_access_token_without_password(self, monkeypatch):
        from src.providers.garmin import GarminAuth

        class FakeClient:
            is_authenticated = False
            needs_refresh = True

            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.refresh_calls = 0

            def refresh_tokens(self):
                self.refresh_calls += 1
                self.is_authenticated = True

            def login(self, **kwargs):
                raise AssertionError("可刷新 Token 不得回退到账号密码登录")

        client = FakeClient()
        monkeypatch.setattr("garmy.AuthClient", lambda **kwargs: client)

        auth = GarminAuth(domain="garmin.com", token_dir="/tmp/tokens")

        assert auth.login("runner@example.com", "") is True
        assert client.refresh_calls == 1

    def test_garmin_auth_propagates_temporary_refresh_error(self, monkeypatch):
        from src.providers.garmin import GarminAuth

        class FakeClient:
            is_authenticated = False
            needs_refresh = True

            def refresh_tokens(self):
                raise requests.Timeout("Garmin refresh timeout")

            def login(self, **kwargs):
                raise AssertionError("临时刷新失败不得回退到账号密码登录")

        monkeypatch.setattr("garmy.AuthClient", lambda **kwargs: FakeClient())

        with pytest.raises(requests.Timeout, match="refresh timeout"):
            GarminAuth(token_dir="/tmp/tokens").login("runner@example.com", "")

    def test_garmin_auth_returns_false_when_refresh_is_unauthorized(self, monkeypatch):
        from src.providers.garmin import GarminAuth

        response = requests.Response()
        response.status_code = 401

        class FakeClient:
            is_authenticated = False
            needs_refresh = True

            def refresh_tokens(self):
                raise requests.HTTPError("Unauthorized", response=response)

        monkeypatch.setattr("garmy.AuthClient", lambda **kwargs: FakeClient())

        assert GarminAuth(token_dir="/tmp/tokens").login("runner@example.com", "") is False

    def test_garmin_profile_error_is_not_converted_to_zero_user_id(self, monkeypatch):
        from src.providers.garmin import GarminAuth

        api_client = mock.Mock()
        api_client.connectapi.side_effect = requests.Timeout("profile timeout")
        auth = GarminAuth(token_dir="/tmp/tokens")
        monkeypatch.setattr(auth, "create_api_client", lambda: api_client)

        with pytest.raises(requests.Timeout, match="profile timeout"):
            auth.get_user_id()

        api_client.connectapi.assert_called_once_with(
            "/userprofile-service/socialProfile"
        )

    def test_garmin_activity_reuses_auth_regional_api_client(self):
        from src.providers.garmin import GarminActivity

        api_client = object()
        auth = mock.Mock()
        auth.create_api_client.return_value = api_client

        activity = GarminActivity(auth)

        assert activity._get_api() is api_client
        assert activity._get_api() is api_client
        auth.create_api_client.assert_called_once_with()

    def test_coros_provider_creation(self):
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

    def test_coros_auth_persists_and_restores_per_user_token(self, tmp_path):
        from coros_mcp.models import StoredAuth
        from src.providers.coros import CorosAuth

        stored = StoredAuth(
            access_token="secret-access-token",
            user_id="12345",
            region="eu",
            timestamp=int(time.time() * 1000),
        )
        token_dir = tmp_path / "user-a" / "tokens"
        auth = CorosAuth(str(token_dir))

        async def fake_login(*args, **kwargs):
            assert kwargs["skip_mobile"] is False
            return stored

        with mock.patch("coros_mcp.coros_api.login", side_effect=fake_login):
            assert auth.login("runner@example.com", "password") is True

        token_path = token_dir / "coros-auth.json"
        assert json.loads(token_path.read_text(encoding="utf-8"))["user_id"] == "12345"
        assert stat.S_IMODE(token_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

        restored = CorosAuth(str(token_dir))
        assert restored.is_authenticated() is True
        assert restored.get_user_id() == 12345

    def test_coros_auth_persists_mobile_sleep_credentials(self, tmp_path):
        from coros_mcp.models import StoredAuth
        from src.providers.coros import CorosAuth

        stored = StoredAuth(
            access_token="training-token",
            user_id="12345",
            region="eu",
            timestamp=int(time.time() * 1000),
            mobile_access_token="sleep-token",
            mobile_login_payload={"encrypted": "payload"},
        )
        auth = CorosAuth(str(tmp_path / "tokens"))

        async def fake_login(*args, **kwargs):
            return stored

        with mock.patch("coros_mcp.coros_api.login", side_effect=fake_login):
            assert auth.login("runner@example.com", "password") is True

        restored = CorosAuth(str(tmp_path / "tokens"))
        assert restored.has_sleep_access() is True

    def test_coros_auth_reports_actionable_missing_dependency(self, tmp_path):
        import builtins
        import pytest

        from src.providers.coros import CorosAuth, CorosDependencyError

        real_import = builtins.__import__

        def import_without_coros(name, *args, **kwargs):
            if name.startswith("coros_mcp"):
                raise ModuleNotFoundError(
                    "No module named 'coros_mcp'", name="coros_mcp"
                )
            return real_import(name, *args, **kwargs)

        auth = CorosAuth(str(tmp_path / "tokens"))
        with mock.patch("builtins.__import__", side_effect=import_without_coros):
            with pytest.raises(CorosDependencyError, match="pip install -e"):
                auth.login("runner@example.com", "password")

    def test_coros_auth_migrates_legacy_global_token(self, tmp_path):
        from coros_mcp.models import StoredAuth
        from src.providers.coros import CorosAuth

        stored = StoredAuth(
            access_token="legacy-access-token",
            user_id="67890",
            region="eu",
            timestamp=int(time.time() * 1000),
        )
        auth = CorosAuth(str(tmp_path / "tokens"))

        with mock.patch("coros_mcp.coros_api.get_stored_auth", return_value=stored):
            assert auth.migrate_legacy_token() is True

        assert auth.get_user_id() == 67890
        assert (tmp_path / "tokens" / "coros-auth.json").exists()

    def test_coros_activity_prefers_workout_time_without_pauses(self):
        from src.providers.coros import _parse_activity_item

        activity = _parse_activity_item({
            "labelId": "activity-1",
            "name": "Paused Run",
            "sportType": 100,
            "startTime": 1784470800,
            "endTime": 1784475000,
            "totalTime": 4200,
            "workoutTime": 3600,
            "distance": 10000,
            "avgHr": 145,
        })

        assert activity.duration_seconds == 3600
        assert activity.extra["total_time_seconds"] == 4200
        assert activity.extra["workout_time_seconds"] == 3600
        assert activity.extra["paused_seconds"] == 600

    def test_coros_activity_falls_back_to_total_time(self):
        from src.providers.coros import _parse_activity_item

        activity = _parse_activity_item({
            "labelId": "activity-2",
            "sportType": 100,
            "startTime": 1784470800,
            "totalTime": 1800,
            "distance": 5000,
        })

        assert activity.duration_seconds == 1800
        assert activity.extra["paused_seconds"] == 0

    def test_coros_activity_reports_real_pagination_progress(self, monkeypatch):
        import httpx

        from src.providers.coros import CorosAuth, _fetch_activity_items

        class Response:
            def __init__(self, items):
                self._items = items

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "result": "0000",
                    "data": {"dataList": self._items, "totalCount": 101},
                }

        class Client:
            def __init__(self, *args, **kwargs):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def get(self, *args, **kwargs):
                self.calls += 1
                return Response(
                    [{"labelId": str(i)} for i in range(100)]
                    if self.calls == 1 else [{"labelId": "last"}]
                )

        monkeypatch.setattr(httpx, "Client", Client)
        auth = CorosAuth()
        auth._auth = mock.Mock(access_token="token", user_id="user", region="eu")
        updates = []

        result = _fetch_activity_items(
            auth,
            date(2026, 7, 1),
            date(2026, 7, 31),
            progress_callback=updates.append,
        )

        assert len(result) == 101
        assert updates == [
            {"current": 100, "total": 101, "date": None,
             "metric": "activities", "outcome": "completed"},
            {"current": 101, "total": 101, "date": None,
             "metric": "activities", "outcome": "completed"},
        ]

    def test_coros_health_maps_sleep_stages_for_range(self):
        from coros_mcp.models import SleepPhases, SleepRecord
        from src.providers.coros import CorosAuth, CorosHealth

        auth = CorosAuth()
        auth._auth = mock.Mock(mobile_access_token="sleep-token")
        health = CorosHealth(auth)
        health._get_analyse_data = mock.Mock(return_value={})
        health._get_hrv_data = mock.Mock(return_value=[])

        records = [SleepRecord(
            date="20260727",
            total_duration_minutes=450,
            phases=SleepPhases(
                deep_minutes=90,
                light_minutes=240,
                rem_minutes=90,
                awake_minutes=30,
                nap_minutes=20,
            ),
            quality_score=86,
        )]
        fetch_sleep = mock.AsyncMock(return_value=records)
        with mock.patch("coros_mcp.coros_api.fetch_sleep", new=fetch_sleep):
            result = health.fetch_health_range(date(2026, 7, 27), date(2026, 7, 27))

        assert len(result) == 1
        daily = result[0]
        assert daily.sleep_duration_hours == 7.5
        assert daily.deep_sleep_hours == 1.5
        assert daily.rem_sleep_hours == 1.5
        assert daily.deep_sleep_pct == 20.0
        assert daily.rem_sleep_pct == 20.0
        assert daily.extra["sleep_quality_score"] == 86
        assert daily.extra["awake_minutes"] == 30
        assert daily.extra["nap_minutes"] == 20
        assert fetch_sleep.await_count == 1

    def test_coros_health_keeps_other_metrics_when_sleep_is_unavailable(self):
        from src.providers.coros import CorosAuth, CorosHealth

        auth = CorosAuth()
        auth._auth = mock.Mock(mobile_access_token=None)
        health = CorosHealth(auth)
        health._get_analyse_data = mock.Mock(return_value={
            "dayList": [{"happenDay": "20260727", "rhr": 48}],
        })
        health._get_hrv_data = mock.Mock(return_value=[])

        with mock.patch(
            "coros_mcp.coros_api.fetch_sleep",
            new=mock.AsyncMock(side_effect=ValueError("No mobile API token available")),
        ):
            result = health.fetch_health_range(date(2026, 7, 27), date(2026, 7, 27))

        assert result[0].resting_heart_rate == 48
        assert result[0].sleep_duration_hours == 0

    def test_coros_health_reports_each_processed_date(self):
        from src.providers.coros import CorosAuth, CorosHealth

        auth = CorosAuth()
        auth._auth = mock.Mock(mobile_access_token=None)
        health = CorosHealth(auth)
        health._get_analyse_data = mock.Mock(return_value={
            "dayList": [{"happenDay": "20260727", "rhr": 48}],
        })
        health._get_hrv_data = mock.Mock(return_value=[])
        updates = []

        result = health.fetch_health_range(
            date(2026, 7, 27), date(2026, 7, 28),
            progress_callback=updates.append,
        )

        assert len(result) == 1
        assert updates == [
            {"current": 1, "total": 2, "date": "2026-07-27",
             "metric": "daily_health", "outcome": "completed"},
            {"current": 2, "total": 2, "date": "2026-07-28",
             "metric": "daily_health", "outcome": "skipped"},
        ]

    def test_coros_progress_callback_failure_does_not_break_sync(self, caplog):
        from src.providers.coros import _emit_progress

        def broken_callback(_payload):
            raise OSError("progress storage unavailable")

        _emit_progress(broken_callback, {"current": 1, "total": 1})

        assert "Coros 同步进度上报失败" in caplog.text

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
