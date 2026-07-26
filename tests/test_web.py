"""测试 Web 多用户同步与日报生成。"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest
from starlette.requests import Request

from src.config import Config
from src.users import UserManager
from src.web import register_web_routes


class _FakeServer:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(func):
            for method in methods:
                self.routes[(path, method)] = func
            return func
        return decorator


def _request(path, body, api_key, method="POST"):
    raw = json.dumps(body).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({
        "type": "http",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"cookie", f"neurun_key={api_key}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }, receive)


def _active_user(tmp_path):
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    manager.update(user.api_key, token_status="active")
    return manager, user


def test_healthz_is_public_and_supports_get_and_head(tmp_path):
    manager = mock.Mock()
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    register_web_routes(server, manager, config)

    get_response = asyncio.run(server.routes[("/healthz", "GET")](_request(
        "/healthz", {}, "", method="GET",
    )))
    head_response = asyncio.run(server.routes[("/healthz", "HEAD")](_request(
        "/healthz", {}, "", method="HEAD",
    )))

    assert get_response.status_code == 200
    assert json.loads(get_response.body) == {"status": "ok"}
    assert get_response.headers["cache-control"] == "no-store"
    assert head_response.status_code == 200
    assert head_response.body == b""
    assert head_response.headers["cache-control"] == "no-store"
    manager.get.assert_not_called()


def test_legacy_coros_token_migrates_only_for_single_active_user(tmp_path):
    from src.config import Config
    from src.web import _migrate_legacy_coros_auth

    user = SimpleNamespace(api_key="rd_coros", provider="coros", token_status="active")
    manager = SimpleNamespace(list_all=lambda: [user])
    config = Config(data_dir=str(tmp_path)).for_user(user.api_key)

    with mock.patch(
        "src.providers.coros.CorosAuth.migrate_legacy_token", return_value=True
    ) as migrate:
        assert _migrate_legacy_coros_auth(manager, user, config) is True

    migrate.assert_called_once_with()


def test_legacy_coros_token_does_not_guess_between_multiple_users(tmp_path):
    from src.config import Config
    from src.web import _migrate_legacy_coros_auth

    user = SimpleNamespace(api_key="rd_coros_a", provider="coros", token_status="active")
    other = SimpleNamespace(api_key="rd_coros_b", provider="coros", token_status="active")
    manager = SimpleNamespace(list_all=lambda: [user, other])
    config = Config(data_dir=str(tmp_path)).for_user(user.api_key)

    with mock.patch("src.providers.coros.CorosAuth.migrate_legacy_token") as migrate:
        assert _migrate_legacy_coros_auth(manager, user, config) is False

    migrate.assert_not_called()


def test_parse_single_day_sync_request():
    from src.web import _parse_sync_request

    parsed = _parse_sync_request({
        "mode": "single",
        "date": "2026-07-19",
        "force": True,
    }, default_days=30)

    assert parsed.mode == "single"
    assert parsed.target == date(2026, 7, 19)
    assert parsed.start == date(2026, 7, 19)
    assert parsed.end == date(2026, 7, 19)
    assert parsed.sync_days == 0
    assert parsed.force is True


def test_parse_batch_sync_request_uses_exact_inclusive_range():
    from src.web import _parse_sync_request

    parsed = _parse_sync_request({
        "mode": "batch",
        "from_date": "2026-07-15",
        "to_date": "2026-07-19",
    }, default_days=30)

    assert parsed.mode == "batch"
    assert parsed.target == date(2026, 7, 19)
    assert parsed.start == date(2026, 7, 15)
    assert parsed.end == date(2026, 7, 19)
    assert parsed.sync_days == 4


def test_parse_batch_sync_request_rejects_reversed_range():
    from src.web import _parse_sync_request

    with pytest.raises(ValueError, match="开始日期不能晚于结束日期"):
        _parse_sync_request({
            "mode": "batch",
            "from_date": "2026-07-20",
            "to_date": "2026-07-19",
        }, default_days=30)


def test_parse_sync_request_keeps_legacy_sync_payload_compatible():
    from src.web import _parse_sync_request

    parsed = _parse_sync_request({
        "date": "2026-07-19",
        "sync_days": 30,
    }, default_days=7)

    assert parsed.mode == "legacy"
    assert parsed.target == date(2026, 7, 19)
    assert parsed.sync_days == 30
    assert not hasattr(parsed, "skip_sync")


def test_sync_route_only_persists_data_without_generating_reports(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    calls = []

    class FakeStorage:
        def backup_to(self, path):
            calls.append(("backup", path))

    def fake_sync(**kwargs):
        calls.append(("sync", kwargs))
        return SimpleNamespace(), FakeStorage(), SimpleNamespace(), 123

    monkeypatch.setattr(web, "_do_data_sync", fake_sync)
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/sync", "POST")](_request(
        "/api/sync",
        {"mode": "batch", "from_date": "2026-07-17", "to_date": "2026-07-19"},
        user.api_key,
    )))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload == {
        "status": "ok",
        "message": "批量同步完成（2026-07-17 ~ 2026-07-19）",
        "mode": "batch",
        "from_date": "2026-07-17",
        "to_date": "2026-07-19",
    }
    assert calls[0][0] == "sync"


def test_report_route_explicitly_generates_without_sync(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    calls = []

    def fake_daily(**kwargs):
        calls.append(kwargs)
        return (
            SimpleNamespace(id="2026-07-19"),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            123,
        )

    monkeypatch.setattr(web, "_do_daily_sync", fake_daily)
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/reports", "POST")](_request(
        "/api/reports", {"date": "2026-07-19"}, user.api_key,
    )))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload == {
        "status": "ok",
        "message": "2026-07-19 日报已生成",
        "date": "2026-07-19",
    }
    assert len(calls) == 1
    assert calls[0]["target"] == date(2026, 7, 19)
    assert calls[0]["skip_sync"] is True
    assert calls[0]["quiet"] is True


def test_detects_coros_expired_token_error():
    from src.web import _is_coros_auth_error

    assert _is_coros_auth_error(
        RuntimeError("Access token is invalid (result=1019)")
    ) is True
    assert _is_coros_auth_error(RuntimeError("temporary network error")) is False


@pytest.mark.parametrize("provider", ["garmin", "coros", "huawei"])
def test_sync_auth_failure_expires_bound_provider(tmp_path, monkeypatch, provider):
    import src.web as web
    from src.main import ProviderAuthenticationError

    manager, user = _active_user(tmp_path)
    manager.update(
        user.api_key,
        provider=provider,
        garmin_email="runner@example.com",
        token_status="active",
    )
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr(
        web,
        "_do_data_sync",
        mock.Mock(side_effect=ProviderAuthenticationError(provider)),
    )
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/sync", "POST")](_request(
        "/api/sync", {"mode": "single", "date": "2026-07-26"}, user.api_key,
    )))

    assert response.status_code == 401
    assert "重新绑定" in json.loads(response.body)["message"]
    assert manager.get(user.api_key).token_status == "expired"
