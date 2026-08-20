"""测试 Web 多用户同步与日报生成。"""

import asyncio
import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path
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


def _request(path, body, api_key, method="POST", query="", path_params=None):
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
        "query_string": query.encode(),
        "path_params": path_params or {},
        "headers": [
            (b"content-type", b"application/json"),
            (b"cookie", f"neurun_key={api_key}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }, receive)


async def _wait_sync_task(server, api_key, task_id, timeout=2):
    route = server.routes[("/api/sync/tasks/{task_id}", "GET")]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await route(_request(
            f"/api/sync/tasks/{task_id}", {}, api_key, method="GET",
            path_params={"task_id": task_id},
        ))
        payload = json.loads(response.body)
        if payload.get("status") in {"succeeded", "failed", "interrupted"}:
            return response, payload
        await asyncio.sleep(0.005)
    raise AssertionError(f"同步任务 {task_id} 未在期限内结束")


def _active_user(tmp_path):
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    manager.update(user.api_key, token_status="active")
    return manager, user


def test_healthz_is_public_and_supports_get_and_head(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURUN_RELEASE_SHA", "a" * 40)
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
    assert json.loads(get_response.body) == {"status": "ok", "release": "a" * 40}
    assert get_response.headers["cache-control"] == "no-store"
    assert head_response.status_code == 200
    assert head_response.body == b""
    assert head_response.headers["cache-control"] == "no-store"
    manager.get.assert_not_called()


def test_html_response_injects_aliyun_arms_rum_before_body_end():
    import hashlib

    from src.web import _html_response

    user = SimpleNamespace(api_key="rd_private-session-key")
    response = _html_response(
        "<!doctype html><html><body>neurun</body></html>", user
    )
    html = response.body.decode()

    assert html.count("https://sdk.rum.aliyuncs.com/v2/browser-sdk.js") == 1
    assert "proj-xtrace-331c87d116484cdd1fe18f4f5e917845-cn-hangzhou" in html
    assert "workspace=default-cms-1561822425896437-cn-hangzhou" in html
    assert "service_id=fdmbbfbh11@80570cae83e7211869362" in html
    assert "env: 'prod'" in html
    assert "spaMode: 'history'" in html
    assert "perf: true" in html
    assert "webVitals: true" in html
    assert "api: true" in html
    assert "staticResource: true" in html
    assert "jsError: true" in html
    assert "consoleError: true" in html
    assert "action: true" in html
    assert "tracing: false" in html
    account_digest = hashlib.sha256(user.api_key.encode()).hexdigest()[:24]
    assert f'user: {{ name: "account_{account_digest}" }}' in html
    assert "rd_private-session-key" not in html
    assert html.count('data-neurun-contact-widget=""') == 1
    assert 'aria-controls="neurunContactPanel"' in html
    assert 'aria-expanded="false"' in html
    assert "(hover:hover) and (pointer:fine)" in html
    assert "calc(92px + env(safe-area-inset-bottom))" in html
    assert "event.key === 'Escape'" in html
    assert "suppressFocusPreview" in html
    assert "二维码暂不可用，请稍后重试" in html
    assert html.index("https://sdk.rum.aliyuncs.com/v2/browser-sdk.js") < html.index(
        "</body>"
    )


def test_html_response_keeps_anonymous_rum_identity_for_logged_out_pages():
    from src.web import _html_response

    response = _html_response("<!doctype html><html><body>login</body></html>")

    html = response.body.decode()

    assert "user: {" not in html
    assert 'data-neurun-contact-widget=""' not in html


def test_html_response_does_not_duplicate_contact_widget():
    from src.web import _html_response

    user = SimpleNamespace(api_key="rd_contact")
    first = _html_response("<html><body>neurun</body></html>", user).body.decode()
    second = _html_response(first, user).body.decode()

    assert second.count('data-neurun-contact-widget=""') == 1
    assert second.count("https://sdk.rum.aliyuncs.com/v2/browser-sdk.js") == 1


def test_contact_qr_requires_login_and_serves_default_asset(tmp_path):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    anonymous = asyncio.run(server.routes[("/contact/qr", "GET")](_request(
        "/contact/qr", {}, "", method="GET",
    )))
    response = asyncio.run(server.routes[("/contact/qr", "GET")](_request(
        "/contact/qr", {}, user.api_key, method="GET",
    )))

    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "no-store"
    assert response.status_code == 200
    assert Path(response.path).name == "contact-wechat.jpg"
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        'inline; filename="neurun-wechat-group.jpg"'
    )


def test_contact_qr_prefers_persistent_override(tmp_path):
    manager, user = _active_user(tmp_path)
    override = tmp_path / "contact-wechat.jpg"
    override.write_bytes(b"new qr")
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/contact/qr", "GET")](_request(
        "/contact/qr", {}, user.api_key, method="GET",
    )))

    assert Path(response.path) == override


def test_contact_qr_returns_404_when_no_asset_exists(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    monkeypatch.setattr(web, "_ASSET_DIR", tmp_path / "missing-assets")
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/contact/qr", "GET")](_request(
        "/contact/qr", {}, user.api_key, method="GET",
    )))

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_coros_sleep_reauthorization_is_exposed_to_existing_users():
    setup_html = (Path(__file__).parents[1] / "web/templates/setup.html").read_text()
    profile_html = (Path(__file__).parents[1] / "web/templates/profile.html").read_text()

    assert "rebind=coros&scope=training" in profile_html
    assert "rebind=coros&scope=sleep" in profile_html
    assert "Coros Training Hub 登录账号（邮箱或手机号）" in setup_html
    assert "Coros App 登录邮箱（不支持手机号）" in setup_html
    assert '<option value="cn" selected>中国大陆</option>' in setup_html
    assert '<option value="eu" selected>' not in setup_html
    assert "/api/coros/auth/training" in setup_html
    assert "/api/coros/auth/sleep" in setup_html
    assert "searchParams.get('rebind')" in setup_html
    assert "body.rebind = true" in setup_html
    assert 'id="coros-auto-relogin"' in setup_html
    assert '<label class="check-row">' in setup_html
    assert ".form-group label.check-row{display:flex;" in setup_html
    assert ".check-row input[type=checkbox]{flex:0 0 18px;width:18px;height:18px;" in setup_html
    assert "padding:0;accent-color:#4caf50" in setup_html
    assert "appearance:auto;background:initial;border:initial;border-radius:initial" in setup_html
    assert "body.auto_refresh" in setup_html
    assert "加密保存" in setup_html
    assert "fetch('/api/setup/capabilities')" in setup_html
    assert "fetch('/api/profile')" not in setup_html
    assert 'id="corosReloginRow"' in profile_html
    assert "/api/coros/auth/training/refresh-credential" in profile_html
    assert "/api/coros/auth/sleep/refresh-credential" in profile_html

    init_script = setup_html.split("// ── Init ──", 1)[1]
    assert "selectProvider(rebindProvider==='coros'?'coros':'garmin');" in init_script
    assert "selectProvider('garmin');" not in init_script
    assert "selectProvider('coros');" not in setup_html


def test_profile_page_exposes_change_password_form():
    profile_html = (Path(__file__).parents[1] / "web/templates/profile.html").read_text()

    assert "🔑 修改密码" in profile_html
    assert 'id="pwCurrent"' in profile_html
    assert 'id="pwNew"' in profile_html
    assert 'id="pwConfirm"' in profile_html
    assert "fetch('/api/password'" in profile_html
    assert "changePassword()" in profile_html


def test_active_coros_user_can_open_sleep_reauthorization_page(tmp_path):
    manager, user = _active_user(tmp_path)
    manager.update(user.api_key, provider="coros", token_status="active")
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/setup", "GET")](_request(
        "/setup", {}, user.api_key, method="GET",
        query="rebind=coros&scope=sleep",
    )))

    assert response.status_code == 200
    assert "认证 Coros 睡眠数据" in response.body.decode()


def test_api_change_password_updates_login_password(tmp_path):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))
    route = server.routes[("/api/password", "POST")]

    anonymous = asyncio.run(route(_request(
        "/api/password",
        {"current_password": "safe-password", "new_password": "next-password"},
        "",
    )))
    assert anonymous.status_code == 401

    wrong_current = asyncio.run(route(_request(
        "/api/password",
        {"current_password": "wrong-password", "new_password": "next-password"},
        user.api_key,
    )))
    assert wrong_current.status_code == 400
    assert json.loads(wrong_current.body)["message"] == "当前密码错误"

    short_new = asyncio.run(route(_request(
        "/api/password",
        {"current_password": "safe-password", "new_password": "short"},
        user.api_key,
    )))
    assert short_new.status_code == 400

    response = asyncio.run(route(_request(
        "/api/password",
        {"current_password": "safe-password", "new_password": "next-password"},
        user.api_key,
    )))
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "ok"
    assert manager.authenticate("runner@example.com", "safe-password") is None
    assert manager.authenticate("runner@example.com", "next-password") == user


def test_authenticated_page_attributes_rum_to_pseudonymous_account(tmp_path):
    import hashlib

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/", "GET")](_request(
        "/", {}, user.api_key, method="GET",
    )))
    html = response.body.decode()
    account_digest = hashlib.sha256(user.api_key.encode()).hexdigest()[:24]

    assert response.status_code == 200
    assert f'user: {{ name: "account_{account_digest}" }}' in html
    assert user.api_key not in html
    assert user.email not in html
    assert user.nickname not in html


def test_web_routes_expose_dashboard_but_no_generic_chat_endpoint(tmp_path):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/", "GET")](_request(
        "/", {}, user.api_key, method="GET",
    )))

    assert response.status_code == 200
    assert 'id="todayConclusion"' in response.body.decode()
    assert ("/api/chat/stream", "POST") not in server.routes


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

        def close(self):
            calls.append(("close", None))

    def fake_sync(**kwargs):
        calls.append(("sync", kwargs))
        return SimpleNamespace(), FakeStorage(), SimpleNamespace(), 123

    monkeypatch.setattr(web, "_do_data_sync", fake_sync)
    register_web_routes(server, manager, config)

    async def scenario():
        response = await server.routes[("/api/sync", "POST")](_request(
            "/api/sync",
            {"mode": "batch", "from_date": "2026-07-17", "to_date": "2026-07-19"},
            user.api_key,
        ))
        payload = json.loads(response.body)
        terminal_response, terminal = await _wait_sync_task(
            server, user.api_key, payload["task_id"],
        )
        return response, payload, terminal_response, terminal

    response, payload, terminal_response, terminal = asyncio.run(scenario())

    assert response.status_code == 202
    assert response.headers["location"] == payload["links"]["self"]
    assert payload["status"] == "queued"
    assert payload["mode"] == "batch"
    assert payload["from_date"] == "2026-07-17"
    assert payload["to_date"] == "2026-07-19"
    assert terminal_response.status_code == 200
    assert terminal_response.headers["cache-control"] == "no-store"
    assert terminal["status"] == "succeeded"
    assert terminal["result"]["from_date"] == "2026-07-17"
    assert terminal["result"]["to_date"] == "2026-07-19"
    assert calls[0][0] == "sync"
    assert calls[-1][0] == "close"


def test_sync_route_keeps_health_responsive_and_rejects_same_user(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(
        data_dir=str(tmp_path), sync_max_concurrency=1, sync_max_pending=2,
    )
    started = threading.Event()
    release = threading.Event()

    class FakeStorage:
        def backup_to(self, path):
            pass

        def close(self):
            pass

    def fake_sync(**kwargs):
        kwargs["progress_callback"](
            "syncing_metrics", 2, 4, "正在同步健康指标",
            {
                "current": 17,
                "total": 33,
                "date": "2026-07-25",
                "metric": "stress",
                "outcome": "completed",
            },
        )
        started.set()
        release.wait(timeout=2)
        return SimpleNamespace(), FakeStorage(), SimpleNamespace(), 123

    monkeypatch.setattr(web, "_do_data_sync", fake_sync)
    register_web_routes(server, manager, config)

    async def scenario():
        first = await server.routes[("/api/sync", "POST")](
            _request("/api/sync", {"mode": "single", "date": "2026-07-26"}, user.api_key)
        )
        first_payload = json.loads(first.body)
        while not started.is_set():
            await asyncio.sleep(0.005)

        running = await server.routes[("/api/sync/tasks/{task_id}", "GET")](_request(
            f"/api/sync/tasks/{first_payload['task_id']}", {}, user.api_key,
            method="GET", path_params={"task_id": first_payload["task_id"]},
        ))

        health_started = time.perf_counter()
        health = await server.routes[("/healthz", "GET")](
            _request("/healthz", {}, "", method="GET")
        )
        health_elapsed = time.perf_counter() - health_started
        duplicate = await server.routes[("/api/sync", "POST")](
            _request("/api/sync", {"mode": "single", "date": "2026-07-26"}, user.api_key)
        )
        release.set()
        completed, completed_payload = await _wait_sync_task(
            server, user.api_key, first_payload["task_id"],
        )
        return running, health, health_elapsed, first, first_payload, duplicate, completed, completed_payload

    running, health, health_elapsed, first, first_payload, duplicate, completed, completed_payload = asyncio.run(scenario())

    assert running.status_code == 200
    assert running.headers["retry-after"] == "1"
    running_payload = json.loads(running.body)
    assert running_payload["status"] == "running"
    assert running_payload["progress"]["current"] == 2
    assert running_payload["progress"]["total"] == 4
    assert running_payload["progress"]["items"] == {
        "current": 17,
        "total": 33,
        "date": "2026-07-25",
        "metric": "stress",
        "outcome": "completed",
    }
    assert health.status_code == 200
    assert health_elapsed < 0.1
    assert first.status_code == 202
    assert duplicate.status_code == 409
    duplicate_payload = json.loads(duplicate.body)
    assert duplicate_payload["code"] == "sync_in_progress"
    assert duplicate_payload["task_id"] == first_payload["task_id"]
    assert duplicate_payload["links"]["self"] == first_payload["links"]["self"]
    assert completed.status_code == 200
    assert completed_payload["status"] == "succeeded"


def test_sync_route_rejects_distinct_user_over_pending_capacity(tmp_path, monkeypatch):
    import src.web as web

    manager, first_user = _active_user(tmp_path)
    second_user = manager.register_account(
        "跑者二", "runner-two@example.com", "safe-password",
    )
    manager.update(second_user.api_key, token_status="active")
    server = _FakeServer()
    config = Config(
        data_dir=str(tmp_path), sync_max_concurrency=1, sync_max_pending=1,
    )
    started = threading.Event()
    release = threading.Event()

    class FakeStorage:
        def backup_to(self, path):
            pass

        def close(self):
            pass

    def fake_sync(**kwargs):
        started.set()
        release.wait(timeout=2)
        return SimpleNamespace(), FakeStorage(), SimpleNamespace(), 123

    monkeypatch.setattr(web, "_do_data_sync", fake_sync)
    register_web_routes(server, manager, config)

    async def scenario():
        first = await server.routes[("/api/sync", "POST")](
            _request("/api/sync", {"mode": "single", "date": "2026-07-26"}, first_user.api_key)
        )
        first_payload = json.loads(first.body)
        while not started.is_set():
            await asyncio.sleep(0.005)
        excess = await server.routes[("/api/sync", "POST")](
            _request("/api/sync", {"mode": "single", "date": "2026-07-26"}, second_user.api_key)
        )
        release.set()
        await _wait_sync_task(server, first_user.api_key, first_payload["task_id"])
        return first, excess

    first, excess = asyncio.run(scenario())

    assert first.status_code == 202
    assert excess.status_code == 503
    assert json.loads(excess.body)["code"] == "sync_capacity_exceeded"
    second_task_file = tmp_path / second_user.api_key / "sync-tasks.json"
    assert not second_task_file.exists()


def test_sync_task_route_hides_other_users_tasks(tmp_path, monkeypatch):
    import src.web as web

    manager, owner = _active_user(tmp_path)
    other = manager.register_account(
        "其他跑者", "other-runner@example.com", "safe-password",
    )
    manager.update(other.api_key, token_status="active")
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    release = threading.Event()

    class FakeStorage:
        def backup_to(self, path):
            pass

        def close(self):
            pass

    def fake_sync(**kwargs):
        release.wait(timeout=2)
        return SimpleNamespace(), FakeStorage(), SimpleNamespace(), 123

    monkeypatch.setattr(web, "_do_data_sync", fake_sync)
    register_web_routes(server, manager, config)

    async def scenario():
        accepted = await server.routes[("/api/sync", "POST")](_request(
            "/api/sync", {"mode": "single", "date": "2026-07-26"}, owner.api_key,
        ))
        task_id = json.loads(accepted.body)["task_id"]
        hidden = await server.routes[("/api/sync/tasks/{task_id}", "GET")](_request(
            f"/api/sync/tasks/{task_id}", {}, other.api_key, method="GET",
            path_params={"task_id": task_id},
        ))
        release.set()
        await _wait_sync_task(server, owner.api_key, task_id)
        return hidden

    hidden = asyncio.run(scenario())

    assert hidden.status_code == 404
    assert json.loads(hidden.body)["code"] == "sync_task_not_found"


def test_sync_calendar_route_returns_local_month_status(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    calls = []

    class FakeStorage:
        def __init__(self, user_config):
            calls.append(("init", user_config.api_key))

        def close(self):
            calls.append(("close", None))

        def get_local_user_id(self):
            return 88

        def get_sync_calendar(self, user_id, start, end):
            calls.append(("calendar", user_id, start, end))
            return {
                "days": [{"date": "2026-07-01", "status": "synced"}],
                "summary": {"synced": 1, "latest_synced_date": "2026-07-01"},
            }

    monkeypatch.setattr(web, "Storage", FakeStorage)
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/sync/calendar", "GET")](_request(
        "/api/sync/calendar", {}, user.api_key, method="GET", query="month=2026-07",
    )))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert payload["month"] == "2026-07"
    assert payload["days"][0]["status"] == "synced"
    assert calls[-2] == (
        "calendar", 88, date(2026, 7, 1), date(2026, 7, 31),
    )
    assert calls[-1] == ("close", None)


def test_calendar_month_range_handles_december_boundary():
    from src.web import _calendar_month_range

    month, start, end = _calendar_month_range("2026-12")

    assert month == "2026-12"
    assert start == date(2026, 12, 1)
    assert end == date(2026, 12, 31)


@pytest.mark.parametrize("month", ["2026-7", "2026-13", "not-a-month"])
def test_sync_calendar_route_rejects_invalid_month(tmp_path, month):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/api/sync/calendar", "GET")](_request(
        "/api/sync/calendar", {}, user.api_key, method="GET", query=f"month={month}",
    )))

    assert response.status_code == 400
    assert "YYYY-MM" in json.loads(response.body)["message"]


def test_sync_template_contains_accessible_calendar_contract():
    from pathlib import Path

    html = Path("web/templates/sync.html").read_text(encoding="utf-8")

    assert 'id="syncCalendar"' in html
    assert 'id="calendarSummary"' in html
    assert 'aria-label="上个月"' in html
    assert 'aria-label="下个月"' in html
    assert "loadSyncCalendar" in html
    assert "button.setAttribute('aria-current','date')" in html
    assert ".calendar-day.today{outline:2px solid color-mix(in srgb,var(--accent) 72%,white)" in html
    assert "box-shadow:0 0 0 4px var(--accent-glow)" in html
    assert ".calendar-day.today{outline:2px solid var(--text-secondary)" not in html
    assert ".calendar-day.today .calendar-day-number{text-decoration" not in html
    assert "var text='已同步 '" in html
    assert 'id="syncTaskCard"' in html
    assert 'aria-live="polite"' in html
    assert "neurun-sync-active-task" in html
    assert "pollSyncTask" in html
    assert "Retry-After" in html
    assert "visibilitychange" in html
    assert 'id="syncTaskItemProgress"' in html
    assert 'id="syncTaskItemBar"' in html
    assert "progress.items" in html
    assert "已处理" in html
    assert "daily_health:'每日健康'" in html


def test_daily_templates_are_mobile_first_and_support_local_png_export():
    from pathlib import Path

    dashboard = Path("web/templates/dashboard.html").read_text(encoding="utf-8")
    reports = Path("web/templates/reports.html").read_text(encoding="utf-8")

    assert 'id="shareCardBtn"' in dashboard
    assert 'id="qualityBanner"' in dashboard
    assert "report.data_readiness" in dashboard
    assert 'id="todayConclusion"' in dashboard
    assert 'class="report-evidence"' in dashboard
    assert "style.setProperty('--visible-count',String(visibleCount))" in dashboard
    assert "repeat(var(--visible-count,3),minmax(0,1fr))" in dashboard
    assert "repeat(min(var(--visible-count,2),2),minmax(0,1fr))" in dashboard
    assert "今天对计划意味着什么" in dashboard
    assert "@media(min-width:641px)" in dashboard
    assert "min-height:44px" in dashboard
    assert "d.daily_activities||d.yesterday_activities" in dashboard
    assert "downloadDailyShareCard" in dashboard
    assert "function reportPalette" in dashboard
    assert "roundedRect" in dashboard
    assert "href=\"/static/tokens.css\"" in dashboard
    assert "href=\"/static/components.css\"" in dashboard

    assert "@media(min-width:641px)" in reports
    assert "min-height:44px" in reports
    assert 'aria-live="polite"' in reports
    assert 'id="dailyStatus"' in reports
    assert "/api/reports/readiness?date=" in reports
    assert "genReport(\\'limited\\')" in reports
    assert 'data-report-tab="daily"' in reports
    assert 'data-report-tab="weekly"' in reports
    assert 'data-report-tab="adjustments"' not in reports
    assert "weekly_checkpoint" in reports
    assert "#single-sync" in reports
    assert "requestedDate=new URLSearchParams(location.search).get('date')" in reports
    assert 'id="genDateLabel"' in reports
    assert 'id="genDateHint"' in reports
    assert 'id="weekDateLabel"' in reports
    assert 'class="report-date-input"' in reports
    assert "function shiftDailyDate(offset)" in reports
    assert "function shiftWeekDate(offset)" in reports
    assert "document.getElementById('genDate').max=todayStr" in reports
    assert 'id="dailyPagination"' in reports
    assert 'id="weeklyPagination"' in reports
    assert "function renderPagination(kind,pagination)" in reports
    assert "function loadReportPage(kind,page)" in reports
    assert "/api/reports?" in reports
    assert "/api/reports/weekly?" in reports
    assert 'id="aiTaskStatus"' in reports
    assert "/api/ai/tasks/current" in reports
    assert "function refreshAIStatus()" in reports
    # 日报生成后按钮状态不得被 readiness 竞态覆盖：成功路径 await load() 后再渲染状态
    assert "await load();" in reports
    assert "await load()" in reports
    assert "[hidden]{display:none!important}" in reports
    assert ".weekly-review-details:not([open])>.weekly-review-body{display:none}" in reports
    # 周进度数据新鲜度与手动刷新（渐进披露）
    assert "function weeklyFreshnessLevel(report)" in reports
    assert "function weeklyFreshnessMarkup(report,refreshButton)" in reports
    assert "class=\"weekly-freshness" in reports
    assert "更新进度" in reports
    assert "重新生成进度" in reports
    assert "补齐同步数据" in reports
    assert "onclick=\"genWeeklyReport()\"" in reports
    assert "archivedWeeks" in reports
    assert "将覆盖当前归档版本" in reports
    assert "lastSync" in reports
    assert "fetch('/api/user')" in reports
    assert "if(j.last_sync)lastSync=j.last_sync" in reports
    assert "质量课总结" in reports
    assert "task.message" in reports
    assert "未关联训练方案" in reports
    assert "制定训练方案（可选）" in reports
    assert "查看完整复盘" in reports
    assert "近期变化" in reports
    assert "恢复与风险" in reports
    assert "下周行动" in reports
    training = Path("web/templates/training.html").read_text(encoding="utf-8")
    assert "function sessionBriefMarkup" in training
    assert "sessionBriefMarkup(data.brief)" in training
    assert "findingMarkup(data.brief)" not in training
    assert "pace_result" in training
    assert "recommendationExplanationMarkup(f.explanation)" in training
    assert "explanation.sample_count" in training
    assert "function timelineMarkup(timeline)" in training
    assert "function prescriptionMarkup(session,compact=false)" in training
    assert "训练处方" in training
    assert "blockLabel" in training
    assert "function stepsCompactSummary(steps)" in training
    assert "function stepMarkup(step)" in training
    assert "逐段处方 · Workout Steps v2" in training
    assert "intensityZoneLabel" in training
    assert "Z2 轻松有氧" in training
    assert "data-session-detail" in training
    assert "data-session-detail-panel" in training
    assert "week-overview" in training
    assert "secondary-info" in training
    assert "href=\"/static/tokens.css\"" in training
    assert "href=\"/static/components.css\"" in training
    # 本周节奏卡：刷新进度按钮 + 数据状态行
    assert "function weekRhythmCardMarkup()" in training
    assert "async function refreshWeekProgress(button)" in training
    assert "onclick=\"refreshWeekProgress(this)\"" in training
    assert "刷新进度" in training
    assert 'class="week-sync-status' in training
    assert "进度统计至" in training
    assert "先同步数据再刷新进度" in training
    assert "weekRhythmCard" in training
    # 周进度并列展示实际运动事实（计划执行 vs 实际量）
    assert "actual_sessions" in training
    assert "actual_running_km" in training
    assert 'pill fact' in training
    assert "实际" in training
    assert "跑步" in training
    # 无完全完成课时收敛“完成/已跑”0 值，避免误解为没练
    assert "const planPills" in training
    assert "doneCount>0" in training
    # 异步回调不得在 await 后访问 event.currentTarget（会被重置为 null）
    assert "const button=event.currentTarget" in training
    assert "const submitter=event.submitter" in training
    assert "finally{event.currentTarget.disabled=false}" not in training
    assert "event.currentTarget.disabled=false" not in training
    assert "event.submitter.disabled=false" not in training
    # 重规划支持赛事延期日期输入 + 作废方案入口
    assert "reviseRaceDate" in training
    assert "new_target_date" in training
    assert "data-close-scheme" in training
    assert "closeSchemeRevision" in training
    assert "作废方案" in training
    # 安全规范化分级展示：安排变化直接展示，技术性对齐计数合并
    assert "function adjustmentKind(text)" in training
    assert "function adjustmentsMarkup(adjustments)" in training
    assert "function adjustmentsInlineMarkup(adjustments)" in training
    assert "方案已做哪些安全调整" in training
    assert "方案已通过确定性安全校验" in training
    # 课程格子展示时长与距离（定时跑换算距离标记“约”）
    assert "duration_minutes?`${esc(w.duration_minutes)} 分钟`" in training
    assert "w.distance_estimated?'约 ':'" in training
    assert "当前教练定位" not in training
    assert "function modeBannerMarkup" not in training


def test_training_pace_result_is_visible_and_reasons_are_expandable():
    from pathlib import Path

    training = Path("web/templates/training.html").read_text(encoding="utf-8")

    assert "pace_guidance" in training
    assert "pace_result" in training
    assert "function recommendationExplanationMarkup" in training
    assert '<details class="recommendation-explanation">' in training
    assert "recommendationExplanationMarkup(f.explanation)" in training
    assert "为什么这样建议" in training
    assert "为什么这样安排" in training
    assert '<p class="why">${esc(session.reason)}</p>' not in training
    assert '<p class="why">${esc(t?.reason' not in training


def test_report_sync_handoff_preserves_and_anchors_target_date():
    from pathlib import Path

    sync = Path("web/templates/sync.html").read_text(encoding="utf-8")

    assert 'data-mode="single"' in sync
    assert 'data-mode="batch"' in sync
    assert "requestedReportDate=new URLSearchParams(location.search).get('date')" in sync
    assert "document.getElementById('singleDate').value=requestedReportDate||today" in sync
    assert "initialCalendarDate=requestedReportDate" in sync
    assert "function anchorRequestedSyncDate()" in sync
    assert "anchorRequestedSyncDate();" in sync
    assert "action.href='/reports?tab=daily'+(returnDate?'&date='" in sync
    assert 'id="singleDateLabel"' in sync
    assert 'id="singleDateHint"' in sync
    assert 'class="date-input-label"' in sync
    assert '可从日历改选' in sync
    assert "function shiftSingleDate(offset)" in sync
    assert "function setSingleDateOffset(offset)" in sync
    assert "document.getElementById('singleDate').max=today" in sync
    assert "calendarDaysByDate" in sync


def test_training_owns_goal_management_not_profile():
    from pathlib import Path

    training = Path("web/templates/training.html").read_text(encoding="utf-8")
    profile = Path("web/templates/profile.html").read_text(encoding="utf-8")

    assert "创建并使用这个目标" in training
    assert "编辑当前目标" in training
    assert "调整目标或重规划" in training
    assert 'id="goalsList"' not in profile
    assert "/api/goals" not in profile
    assert "训练目标" not in profile


def test_report_list_keeps_scores_on_the_summary_row_on_mobile():
    from pathlib import Path

    reports = Path("web/templates/reports.html").read_text(encoding="utf-8")

    assert (
        ".report-row{display:grid;grid-template-columns:44px minmax(0,1fr) auto"
        in reports
    )
    assert ".report-training{white-space:nowrap;overflow:hidden;text-overflow:ellipsis" in reports
    assert ".report-scores{grid-column:auto;display:flex;flex-wrap:nowrap" in reports
    assert ".report-score{text-align:center;min-width:36px" in reports


def test_primary_navigation_uses_one_mobile_bottom_bar_contract():
    from pathlib import Path

    templates = {
        name: Path(f"web/templates/{name}.html").read_text(encoding="utf-8")
        for name in ("training", "sync", "dashboard", "reports", "profile")
    }
    nav_styles = []
    nav_markup = []

    for html in templates.values():
        assert 'class="app-nav"' in html
        assert 'aria-label="主要导航"' in html
        assert html.count("data-nav-item") == 4
        assert html.count('data-nav-item aria-current="page"') == 1
        assert 'href="/training"' in html
        assert 'href="/sync"' in html
        assert 'href="/reports"' in html
        assert 'href="/profile"' in html
        # Shared CSS is now loaded from web/static/ instead of inlined
        assert 'href="/static/tokens.css"' in html
        assert 'href="/static/reset.css"' in html
        assert 'href="/static/components.css"' in html
        assert 'href="/static/utilities.css"' in html
        # No inlined CSS theme blocks - using shared tokens.css
        assert '[data-theme="fresh"] {' not in html
        assert '[data-theme="sport"] {' not in html
        assert '[data-theme="dark"] {' not in html
        assert ".nav-tabs" not in html

        nav_start = html.index('<nav class="app-nav"')
        nav_end = html.index("</nav>", nav_start) + len("</nav>")
        nav_markup.append(
            html[nav_start:nav_end].replace(' aria-current="page"', "")
        )

    assert len(set(nav_markup)) == 1


def test_training_page_reuses_the_shared_three_theme_palette():
    from pathlib import Path

    templates = {
        name: Path(f"web/templates/{name}.html").read_text(encoding="utf-8")
        for name in ("training", "sync", "dashboard", "reports", "profile")
    }

    # All pages now reference the shared tokens.css instead of inlining themes
    for name, html in templates.items():
        assert 'href="/static/tokens.css"' in html, f"{name} missing shared tokens.css"
        # No inlined CSS theme blocks - using shared tokens.css
        assert '[data-theme="fresh"] {' not in html, f"{name} still has inlined fresh theme"
        assert '[data-theme="sport"] {' not in html, f"{name} still has inlined sport theme"
        assert '[data-theme="dark"] {' not in html, f"{name} still has inlined dark theme"

    # Verify shared tokens.css has all three themes
    tokens_css = Path("web/static/tokens.css").read_text(encoding="utf-8")
    for theme in ("fresh", "sport", "dark"):
        assert f'[data-theme="{theme}"]' in tokens_css, f"tokens.css missing {theme} theme"
    assert "--accent-strong" in tokens_css
    assert "--surface-raised" in tokens_css
    assert "--font-display" in tokens_css


def test_training_page_and_api_complete_confirmed_adjustment_flow(tmp_path):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    page = asyncio.run(server.routes[("/training", "GET")](
        _request("/training", {}, user.api_key, method="GET")
    ))
    empty = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, user.api_key, method="GET")
    ))
    assert page.status_code == 200
    assert "今天练什么，一眼就知道" in page.body.decode()
    assert "修改目标" in page.body.decode()
    assert "修改训练基础" in page.body.decode()
    assert "修改现实约束" in page.body.decode()
    assert "还有什么需要教练知道" in page.body.decode()
    assert "additional_context" in page.body.decode()
    assert "生成草稿" in page.body.decode()
    assert "更新方案草稿" in page.body.decode()
    assert "读取并校验训练事实" in page.body.decode()
    assert "neurun-training-draft-task" in page.body.decode()
    assert "之前的草稿任务已失效，请重新生成。" in page.body.decode()
    assert "error.status=r.status" in page.body.decode()
    assert "zhPlan" in page.body.decode()
    assert "fartlek" in page.body.decode()
    assert "变速跑" in page.body.decode()
    assert "建议从哪个阶段开始" in page.body.decode()
    assert "当前切入点" in page.body.decode()
    assert "方案周期" in page.body.decode()
    assert "查看依据与不确定性" in page.body.decode()
    assert "能力依据" in page.body.decode()
    assert "负荷逻辑" in page.body.decode()
    assert "周期逻辑" in page.body.decode()
    assert "draftPaceLabel" in page.body.decode()
    assert "点击课表中的课程" in page.body.decode()
    assert "draft-session-details" in page.body.decode()
    assert "setDraftFormBusy" in page.body.decode()
    assert json.loads(empty.body)["has_active_plan"] is False

    goal_response = asyncio.run(server.routes[("/api/goals", "POST")](
        _request("/api/goals", {
            "name": "半马跑进 100 分钟",
            "distance": "hm",
            "target_time": "01:40:00",
            "target_date": "2026-11-15",
        }, user.api_key)
    ))
    goal_id = json.loads(goal_response.body)["id"]
    profile_with_goal = asyncio.run(server.routes[("/api/profile", "GET")](
        _request("/api/profile", {}, user.api_key, method="GET")
    ))
    assert json.loads(profile_with_goal.body)["goals"][0]["goal_id"] == goal_id
    async def create_and_wait():
        created = await server.routes[("/api/training/plans", "POST")](_request(
            "/api/training/plans", {
            "goal_id": goal_id, "available_days": [1, 3, 5, 6],
            "max_session_minutes": 100,
            "additional_context": "本周出差两天，长距离尽量安排周末",
            "request_id": "supplement-test-001",
            }, user.api_key,
        ))
        task_id = json.loads(created.body)["task"]["task_id"]
        for _ in range(50):
            response = await server.routes[("/api/training/tasks/{task_id}", "GET")](_request(
                f"/api/training/tasks/{task_id}", {}, user.api_key,
                method="GET", path_params={"task_id": task_id},
            ))
            task = json.loads(response.body)["task"]
            if task["state"] in {"succeeded", "failed"}:
                return created, task
            await asyncio.sleep(0.01)
        return created, task
    created, task = asyncio.run(create_and_wait())
    assert created.status_code == 202
    assert task["state"] == "succeeded"
    pending_home = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, user.api_key, method="GET")
    ))
    draft = json.loads(pending_home.body)["pending_scheme_draft"]
    assert draft["generation_mode"] in {"skill", "deterministic_fallback"}
    assert draft["inference_source"] in {"ai", "deterministic"}
    assert draft["validation_status"] in {"passed", "repaired", "fallback"}
    assert draft["decision_status"] in {
        "ai_validated", "ai_repaired", "ai_risk_advisory", "deterministic_fallback",
    }
    assert draft["supplement"]["request_id"] == "supplement-test-001"
    assert draft["supplement"]["char_count"] == len("本周出差两天，长距离尽量安排周末")
    assert len(draft["periodization"]) >= 3
    assert len(draft["first_four_weeks"]) == 4
    assert len(draft["near_term_schedule"]) == 2
    assert all(
        workout.get("date")
        for week in draft["near_term_schedule"]
        for workout in week["workouts"]
    )
    async def update_and_wait():
        updated = await server.routes[(
            "/api/training/plans/{plan_id}", "PUT",
        )](_request(
            f"/api/training/plans/{draft['plan_id']}", {
                "goal_id": goal_id, "available_days": [0, 2, 6],
                "max_session_minutes": 80, "reported_weekly_mileage": 35,
            }, user.api_key, method="PUT", path_params={"plan_id": draft["plan_id"]},
        ))
        task_id = json.loads(updated.body)["task"]["task_id"]
        for _ in range(50):
            response = await server.routes[("/api/training/tasks/{task_id}", "GET")](_request(
                f"/api/training/tasks/{task_id}", {}, user.api_key,
                method="GET", path_params={"task_id": task_id},
            ))
            task = json.loads(response.body)["task"]
            if task["state"] in {"succeeded", "failed"}:
                return updated, task
            await asyncio.sleep(0.01)
        return updated, task
    updated, task = asyncio.run(update_and_wait())
    assert updated.status_code == 202
    assert task["state"] == "succeeded"
    pending_home = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, user.api_key, method="GET")
    ))
    updated_draft = json.loads(pending_home.body)["pending_scheme_draft"]
    assert updated_draft["plan_id"] == draft["plan_id"]
    assert updated_draft["weekly_mileage_target"] == 35
    assert updated_draft["status"] == "draft"
    activated = asyncio.run(server.routes[(
        "/api/training/plans/{plan_id}/activate", "POST",
    )](_request(
        f"/api/training/plans/{updated_draft['plan_id']}/activate", {}, user.api_key,
        path_params={"plan_id": updated_draft["plan_id"]},
    )))
    assert json.loads(activated.body)["scheme"]["version"] == 1

    brief_response = asyncio.run(server.routes[(
        "/api/training/session-brief", "GET",
    )](_request(
        "/api/training/session-brief", {}, user.api_key, method="GET",
    )))
    assert json.loads(brief_response.body)["brief"]["finding"]["recommendations"]

    revision_response = asyncio.run(server.routes[(
        "/api/training/scheme-revisions", "POST",
    )](_request(
        "/api/training/scheme-revisions", {
            "trigger": "constraints_change",
            "reason": "未来几周只能安排三个训练日",
            "constraints": {"available_days": [1, 4, 6], "max_session_minutes": 80},
        }, user.api_key,
    )))
    revision = json.loads(revision_response.body)["proposal"]
    assert revision["scope"] == "scheme"
    assert revision["status"] == "pending"

    early_strategy = asyncio.run(server.routes[(
        "/api/training/race-strategy", "POST",
    )](_request(
        "/api/training/race-strategy", {}, user.api_key,
    )))
    assert early_strategy.status_code == 409

    feedback_response = asyncio.run(server.routes[(
        "/api/training/feedback", "POST",
    )](_request("/api/training/feedback", {
        "feedback_type": "time_limited",
        "target_date": str(date.today()),
        "available_minutes": 25,
    }, user.api_key)))
    feedback = json.loads(feedback_response.body)["feedback"]
    proposal_response = asyncio.run(server.routes[(
        "/api/training/proposals", "POST",
    )](_request("/api/training/proposals", {
        "feedback_id": feedback["feedback_id"],
    }, user.api_key)))
    proposal = json.loads(proposal_response.body)["proposal"]

    before = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, user.api_key, method="GET")
    ))
    assert json.loads(before.body)["scheme"]["version"] == 1

    approved = asyncio.run(server.routes[(
        "/api/training/proposals/{proposal_id}/approve", "POST",
    )](_request(
        f"/api/training/proposals/{proposal['proposal_id']}/approve",
        {"base_version": 1, "idempotency_key": "web-confirm-1"}, user.api_key,
        path_params={"proposal_id": proposal["proposal_id"]},
    )))
    payload = json.loads(approved.body)
    assert payload["scheme"]["version"] == 2
    assert payload["proposal"]["status"] == "approved"


def test_training_api_is_user_isolated_and_requires_active_session(tmp_path):
    manager, first = _active_user(tmp_path)
    second = manager.register_account("第二位", "second@example.com", "safe-password")
    manager.update(second.api_key, token_status="active")
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    goal = asyncio.run(server.routes[("/api/goals", "POST")](
        _request("/api/goals", {
            "name": "第一位用户目标", "distance": "10k",
            "target_date": "2026-11-01",
        }, first.api_key)
    ))
    first_goal_id = json.loads(goal.body)["id"]
    asyncio.run(server.routes[("/api/training/plans", "POST")](
        _request("/api/training/plans", {
            "goal_id": first_goal_id, "available_days": [1, 3, 6],
            "max_session_minutes": 90,
        }, first.api_key)
    ))
    first_home = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, first.api_key, method="GET")
    ))
    second_home = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, second.api_key, method="GET")
    ))
    anonymous = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, "", method="GET")
    ))

    assert json.loads(first_home.body)["setup"]["active_goals"][0]["goal_id"] == first_goal_id
    assert json.loads(second_home.body)["setup"]["active_goals"] == []
    assert anonymous.status_code == 401


def test_training_template_supports_320px_and_explicit_confirmation():
    html = Path("web/templates/training.html").read_text(encoding="utf-8")

    assert "min-width:320px" in html
    assert "@media(max-width:360px)" in html
    assert 'aria-live="polite"' in html
    assert 'data-step="goal"' in html
    assert 'data-step="baseline"' in html
    assert 'data-step="constraints"' in html
    assert 'data-step="review"' in html
    assert 'data-step="confirm"' in html
    assert "上一步" in html
    assert "确认方案并进入备赛" in html
    assert "保守规则兜底" in html
    assert "AI 方案 · 已安全规范化" in html
    assert "AI 风险评估 · 待你选择" in html
    assert "方案已做哪些安全调整" in html
    assert "可选路线" in html
    assert "首四周负荷" in html
    assert "near_term_schedule" in html
    assert "weekRange" in html
    assert "确认调整" in html
    assert "保持原计划" in html
    assert "position:absolute" not in html


def test_training_setup_reads_previous_completed_week_and_ignores_dynamic_volume(
    tmp_path, monkeypatch,
):
    import src.web as web

    manager, user = _active_user(tmp_path)
    config = Config(data_dir=str(tmp_path))
    user_cfg = config.for_user(user.api_key)
    Path(user_cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(user_cfg.db_path).touch()

    class FakeStorage:
        def __init__(self, _config):
            pass

        def get_local_user_id(self):
            return 7

        def get_activities_range(self, _user_id, _start, _end):
            return [
                {
                    "activity_id": "run-1", "activity_date": str(date.today() - timedelta(days=7)),
                    "activity_name": "晨跑", "activity_type": "running",
                    "distance_meters": 12000, "duration_seconds": 3600,
                },
                {
                    "activity_id": "run-dynamic", "activity_date": str(date.today()),
                    "activity_name": "动态周大跑量", "activity_type": "running",
                    "distance_meters": 80000, "duration_seconds": 21600,
                },
                {
                    "activity_id": "ride-1", "activity_date": str(date.today()),
                    "activity_name": "骑行", "activity_type": "cycling",
                    "distance_meters": 50000, "duration_seconds": 7200,
                },
            ]

        def get_sync_calendar(self, _user_id, _start, _end, **_kwargs):
            return {"days": [], "summary": {"synced": 22, "partial": 6}}

        def close(self):
            pass

    monkeypatch.setattr(web, "Storage", FakeStorage)
    server = _FakeServer()
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/training/home", "GET")](
        _request("/api/training/home", {}, user.api_key, method="GET")
    ))
    baseline = json.loads(response.body)["setup"]["baseline"]

    assert baseline["coverage"] == "sufficient"
    assert baseline["activity_count"] == 1
    assert baseline["distance_km"] == 12
    assert baseline["previous_week_km"] == 12
    assert baseline["reference_window_kind"] == "previous_completed_natural_week"
    assert baseline["recent_7d_km"] == 0


def test_report_route_explicitly_generates_without_sync(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    calls = []

    def fake_daily(**kwargs):
        calls.append(kwargs)
        return (
            SimpleNamespace(id="2026-07-19", front_matter={
                "data_readiness": "complete",
                "report_finality": "final",
                "data_as_of": "2026-07-19 08:00:00",
            }),
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
        "data_readiness": "complete",
        "report_finality": "final",
        "data_as_of": "2026-07-19 08:00:00",
    }
    assert len(calls) == 1
    assert calls[0]["target"] == date(2026, 7, 19)
    assert calls[0]["skip_sync"] is True
    assert calls[0]["quiet"] is True
    assert calls[0]["report_mode"] == "complete"


def test_ai_capacity_rejection_prevents_daily_weekly_and_adjustment_work(tmp_path, monkeypatch):
    import src.web as web
    from src.ai_inference_coordinator import AIInferenceCapacityExceededError

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    calls = []

    async def reject_when_full(self, user_key, work, **kwargs):
        del self, user_key, work, kwargs
        raise AIInferenceCapacityExceededError("满载")

    monkeypatch.setattr(web.AIInferenceCoordinator, "run", reject_when_full)
    monkeypatch.setattr(web, "_do_daily_sync", lambda **kwargs: calls.append(kwargs))
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    daily = asyncio.run(server.routes[("/api/reports", "POST")](_request(
        "/api/reports", {"date": "2026-07-19"}, user.api_key,
    )))
    weekly = asyncio.run(server.routes[("/api/reports/weekly", "POST")](_request(
        "/api/reports/weekly", {"date": "2026-07-19"}, user.api_key,
    )))
    adjustment = asyncio.run(server.routes[("/api/training/proposals", "POST")](_request(
        "/api/training/proposals", {"feedback_id": "not-started"}, user.api_key,
    )))

    for response in (daily, weekly, adjustment):
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["code"] == "ai_capacity_reached"
        assert payload["retry_after_seconds"] == 5
        assert response.headers["retry-after"] == "5"
    assert calls == []


def test_same_user_ai_request_in_progress_returns_429(tmp_path, monkeypatch):
    import src.web as web
    from src.ai_inference_coordinator import AIInferenceInProgressError

    manager, user = _active_user(tmp_path)
    server = _FakeServer()

    async def reject_duplicate(self, user_key, work, **kwargs):
        del self, user_key, work, kwargs
        raise AIInferenceInProgressError("重复")

    monkeypatch.setattr(web.AIInferenceCoordinator, "run", reject_duplicate)
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/api/reports", "POST")](_request(
        "/api/reports", {"date": "2026-07-19"}, user.api_key,
    )))
    payload = json.loads(response.body)

    assert response.status_code == 429
    assert payload["code"] == "ai_request_in_progress"
    assert payload["retry_after_seconds"] == 5
    assert payload["task_status_url"] == "/api/ai/tasks/current"
    assert response.headers["retry-after"] == "5"


def test_current_ai_task_status_is_scoped_to_logged_in_user(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    seen = []

    async def current_status(self, user_key):
        del self
        seen.append(user_key)
        return {
            "task_id": "task-1",
            "operation": "weekly_review",
            "state": "running",
            "elapsed_seconds": 12,
            "message": "正在生成周复盘，页面可以安全刷新",
        }

    monkeypatch.setattr(web.AIInferenceCoordinator, "current_status", current_status)
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/api/ai/tasks/current", "GET")](_request(
        "/api/ai/tasks/current", {}, user.api_key, method="GET",
    )))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["task"]["operation"] == "weekly_review"
    assert payload["task"]["state"] == "running"
    assert seen == [user.api_key]
    assert response.headers["cache-control"] == "no-store"


def test_close_scheme_route_returns_ok_without_active_plan(tmp_path):
    from src.training import TrainingService

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/training/scheme/close", "POST")](
        _request("/api/training/scheme/close", {"reason": "测试"}, user.api_key)
    ))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["closed"] is None  # 无方案时作废为空，不报错


def test_weekly_report_routes_require_explicit_generation(tmp_path):
    from src.training import TrainingService

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    service = TrainingService(config.for_user(user.api_key).memory_dir)
    goal = service.create_goal({
        "name": "10K 备赛", "distance": "10k", "target_date": "2026-10-18",
    })
    draft = service.create_draft({
        "goal_id": goal["goal_id"], "available_days": [1, 3, 5],
        "max_session_minutes": 90,
    })
    service.activate(draft["plan_id"])
    manager.update(user.api_key, last_sync="2026-08-13")
    register_web_routes(server, manager, config)

    listed = asyncio.run(server.routes[("/api/reports/weekly", "GET")](
        _request("/api/reports/weekly", {}, user.api_key, method="GET")
    ))
    assert json.loads(listed.body) == {"status": "ok", "reports": []}

    created = asyncio.run(server.routes[("/api/reports/weekly", "POST")](
        _request("/api/reports/weekly", {"date": str(date.today())}, user.api_key)
    ))
    payload = json.loads(created.body)
    assert created.status_code == 200
    assert payload["status"] == "ok"
    assert payload["report"]["type"] == "weekly_checkpoint"
    assert payload["report"]["progression_decision"]["action"] == "not_final"
    assert payload["report"]["training_url"].startswith("/training?")
    # 周进度数据新鲜度：响应必须携带最近同步时间，供前端判定是否提示“更新进度”
    assert payload["last_sync"] == "2026-08-13"
    task_response = asyncio.run(server.routes[("/api/ai/tasks/current", "GET")](_request(
        "/api/ai/tasks/current", {}, user.api_key, method="GET",
    )))
    task = json.loads(task_response.body)["task"]
    assert task["operation"] == "weekly_review"
    assert task["state"] == "succeeded"
    assert task["result"]["report"]["type"] == "weekly_checkpoint"
    assert ("/api/training/week-review", "GET") not in server.routes
    assert ("/api/reports/adjustments", "GET") not in server.routes


def test_weekly_report_route_does_not_require_training_plan(tmp_path):
    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    created = asyncio.run(server.routes[("/api/reports/weekly", "POST")](_request(
        "/api/reports/weekly", {"date": str(date.today())}, user.api_key,
    )))
    payload = json.loads(created.body)

    assert created.status_code == 200
    assert payload["status"] == "ok"
    assert payload["report"]["type"] == "weekly_checkpoint"
    assert payload["report"]["plan_context"] is None
    assert payload["report"]["progression_decision"] is None


def test_report_recommendation_route_creates_pending_training_proposal(tmp_path):
    from datetime import timedelta

    from src.training import TrainingService

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    service = TrainingService(config.for_user(user.api_key).memory_dir)
    goal = service.create_goal({
        "name": "10K 备赛", "distance": "10k", "target_date": "2026-10-18",
        "goal_intent": "performance", "target_time": "00:45:00",
    })
    draft = service.create_draft({
        "goal_id": goal["goal_id"], "available_days": [1, 3, 5],
        "max_session_minutes": 90,
    })
    active = service.activate(draft["plan_id"])
    week_start = date.today() - timedelta(days=date.today().weekday() + 7)
    week_id = week_start.strftime("%G-W%V")
    service.repository.save_weekly_report({
        "type": "weekly_report", "report_id": f"weekly-{week_start}",
        "week_id": week_id, "week_start": str(week_start),
        "week_end": str(week_start + timedelta(days=6)),
        "plan_id": active["plan_id"], "plan_version": active["version"],
        "progression_decision": {
            "decision_id": "decision-web", "action": "deload",
            "rationale": "恢复下降，下一周先降载",
        },
        "adaptation_signal": {"recommendation": "scheme_revision"},
        "execution_summary": {},
    })
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/training/proposals/from-report", "POST")](
        _request(
            "/api/training/proposals/from-report", {"week_id": week_id}, user.api_key,
        )
    ))
    payload = json.loads(response.body)

    assert response.status_code == 201
    assert payload["proposal"]["status"] == "pending"
    assert payload["proposal"]["source_week_id"] == week_id
    assert service.plan()["version"] == active["version"]


def test_report_archive_reads_paginate_without_hiding_selected_daily_report(tmp_path):
    from src.memory import Memory, MemoryType
    from src.training import TrainingService

    manager, user = _active_user(tmp_path)
    config = Config(data_dir=str(tmp_path))
    memory_dir = Path(config.for_user(user.api_key).memory_dir)
    for day in range(1, 10):
        report_date = f"2026-01-{day:02d}"
        Memory(
            id=report_date,
            type=MemoryType.DAILY_REPORT,
            path=memory_dir / "auto/daily" / f"{report_date}.md",
            front_matter={
                "type": "daily_report", "report_date": report_date,
                "daily_activities": {
                    "activity_state": "known", "sessions": [{"type": "跑步"}],
                    "total_duration_min": 40, "total_distance_km": 7.0,
                },
            },
            body="# 日报",
        ).save()

    service = TrainingService(memory_dir)
    for week in range(1, 6):
        service.repository.save_weekly_report({
            "type": "weekly_report", "week_id": f"2026-W{week:02d}",
            "week_start": f"2026-0{week}-02", "week_end": f"2026-0{week}-08",
        })
    for index in range(1, 7):
        service.repository.save_proposal({
            "type": "adjustment_proposal", "proposal_id": f"adjustment-{index}",
            "plan_id": "plan-1", "scope": "scheme" if index % 2 else "session",
            "reason": f"第 {index} 次调整", "status": "approved",
            "target_date": f"2026-01-{index:02d}", "approved_at": f"2026-02-{index:02d}T08:00:00",
            "applied_version": index + 1,
        })

    server = _FakeServer()
    register_web_routes(server, manager, config)

    daily = asyncio.run(server.routes[("/api/reports", "GET")](_request(
        "/api/reports", {}, user.api_key, method="GET",
        query="page=2&per_page=3&date=2026-01-09",
    )))
    daily_payload = json.loads(daily.body)
    assert daily.status_code == 200
    assert daily_payload["pagination"] == {
        "page": 2, "per_page": 3, "total": 9, "total_pages": 3,
    }
    assert [item["date"] for item in daily_payload["reports"]] == [
        "2026-01-06", "2026-01-05", "2026-01-04",
    ]
    assert daily_payload["selected"]["date"] == "2026-01-09"

    weekly = asyncio.run(server.routes[("/api/reports/weekly", "GET")](_request(
        "/api/reports/weekly", {}, user.api_key, method="GET",
        query="page=2&per_page=2",
    )))
    weekly_payload = json.loads(weekly.body)
    assert weekly.status_code == 200
    assert weekly_payload["pagination"] == {
        "page": 2, "per_page": 2, "total": 5, "total_pages": 3,
    }
    assert [item["week_id"] for item in weekly_payload["reports"]] == [
        "2026-W03", "2026-W02",
    ]

    adjustments = asyncio.run(server.routes[("/api/training/adjustments", "GET")](_request(
        "/api/training/adjustments", {}, user.api_key, method="GET",
        query="page=2&per_page=2",
    )))
    adjustments_payload = json.loads(adjustments.body)
    assert adjustments.status_code == 200
    assert adjustments_payload["pagination"] == {
        "page": 2, "per_page": 2, "total": 6, "total_pages": 3,
    }
    assert [item["proposal_id"] for item in adjustments_payload["records"]] == [
        "adjustment-4", "adjustment-3",
    ]


def test_report_readiness_route_returns_dimension_evidence(tmp_path):
    from src.storage import Storage

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    user_cfg = config.for_user(user.api_key)
    storage = Storage(user_cfg)
    storage.mark_sync_calendar_range(
        7, date(2026, 7, 19), date(2026, 7, 19), "completed",
    )
    storage.close()
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/reports/readiness", "GET")](
        _request(
            "/api/reports/readiness", {}, user.api_key, method="GET",
            query="date=2026-07-19",
        )
    ))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["readiness"]["status"] == "limited"
    assert payload["readiness"]["dimensions"]["activity"]["status"] == "complete"
    assert payload["readiness"]["dimensions"]["sleep"]["status"] == "missing"


def test_report_route_returns_409_without_generating_when_gate_blocks(
    tmp_path, monkeypatch,
):
    import src.web as web
    from src.report_readiness import (
        CoverageDimension,
        DailyReportReadiness,
        DailyReportReadinessError,
    )

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    readiness = DailyReportReadiness(
        status="blocked",
        finality="provisional",
        data_as_of=None,
        dimensions={
            "activity": CoverageDimension(
                "missing", 0, 1, reason="缺少同步证据", action="sync_day",
            ),
        },
        blockers=("activity",),
        omitted_sections=(),
        suggested_actions=("sync_day",),
    )

    def blocked_daily(**kwargs):
        raise DailyReportReadinessError(readiness, kwargs["report_mode"])

    monkeypatch.setattr(web, "_do_daily_sync", blocked_daily)
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/reports", "POST")](
        _request(
            "/api/reports", {"date": "2026-07-19", "mode": "limited"},
            user.api_key,
        )
    ))
    payload = json.loads(response.body)

    assert response.status_code == 409
    assert payload["code"] == "report_data_incomplete"
    assert payload["readiness"]["status"] == "blocked"


def test_detects_coros_expired_token_error():
    from src.web import _is_coros_auth_error

    assert _is_coros_auth_error(
        RuntimeError("Access token is invalid (result=1019)")
    ) is True
    assert _is_coros_auth_error(RuntimeError("temporary network error")) is False


def test_provider_auth_error_reads_nested_http_status():
    from src.web import _is_provider_auth_error

    class NestedHTTPError(RuntimeError):
        def __init__(self, status_code):
            super().__init__(f"HTTP request failed: {status_code}")
            response = SimpleNamespace(status_code=status_code)
            self.error = SimpleNamespace(response=response)

    assert _is_provider_auth_error(NestedHTTPError(401), "garmin") is True
    assert _is_provider_auth_error(NestedHTTPError(403), "garmin") is True
    assert _is_provider_auth_error(NestedHTTPError(503), "garmin") is False


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

    async def scenario():
        response = await server.routes[("/api/sync", "POST")](_request(
            "/api/sync", {"mode": "single", "date": "2026-07-26"}, user.api_key,
        ))
        accepted = json.loads(response.body)
        terminal_response, terminal = await _wait_sync_task(
            server, user.api_key, accepted["task_id"],
        )
        return response, terminal_response, terminal

    response, terminal_response, terminal = asyncio.run(scenario())

    assert response.status_code == 202
    assert terminal_response.status_code == 200
    assert terminal["status"] == "failed"
    assert terminal["error"]["code"] == "provider_authentication_failed"
    assert terminal["error"]["action"] == "reauthorize"
    assert "重新绑定" in terminal["error"]["message"]
    assert manager.get(user.api_key).token_status == "expired"


def test_sync_temporary_provider_failure_keeps_connection_active(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    manager.update(
        user.api_key,
        provider="garmin",
        garmin_email="runner@example.com",
        token_status="active",
    )
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr(
        web,
        "_do_data_sync",
        mock.Mock(side_effect=TimeoutError("Garmin temporary timeout")),
    )
    register_web_routes(server, manager, config)

    async def scenario():
        response = await server.routes[("/api/sync", "POST")](_request(
            "/api/sync", {"mode": "single", "date": "2026-07-26"}, user.api_key,
        ))
        accepted = json.loads(response.body)
        terminal_response, terminal = await _wait_sync_task(
            server, user.api_key, accepted["task_id"],
        )
        return terminal_response, terminal

    terminal_response, terminal = asyncio.run(scenario())

    assert terminal_response.status_code == 200
    assert terminal["status"] == "failed"
    assert terminal["error"] == {
        "code": "provider_timeout",
        "message": "数据源请求超时，请稍后重试",
        "retryable": True,
        "action": "retry",
    }
    assert manager.get(user.api_key).token_status == "active"
