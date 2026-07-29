"""测试 Web 多用户同步与日报生成。"""

import asyncio
import json
import threading
import time
from datetime import date
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
    assert "body.auto_refresh" in setup_html
    assert "加密保存" in setup_html
    assert 'id="corosReloginRow"' in profile_html
    assert "/api/coros/auth/training/refresh-credential" in profile_html
    assert "/api/coros/auth/sleep/refresh-credential" in profile_html

    init_script = setup_html.split("// ── Init ──", 1)[1]
    assert "selectProvider(rebindProvider==='coros'?'coros':'garmin');" in init_script
    assert "selectProvider('garmin');" not in init_script


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

    dashboard = Path("web/templates/chat.html").read_text(encoding="utf-8")
    reports = Path("web/templates/reports.html").read_text(encoding="utf-8")

    assert 'id="saveImageBtn"' in dashboard
    assert 'id="saveStatus"' in dashboard
    assert 'aria-live="polite"' in dashboard
    assert "function buildReportCanvas" in dashboard
    assert "canvas.toBlob" in dashboard
    assert "navigator.canShare" in dashboard
    assert "navigator.share" in dashboard
    assert "URL.createObjectURL" in dashboard
    assert "$('planStrip').style.display='grid'" in dashboard
    assert "@media(min-width:641px)" in dashboard
    assert "min-height:44px" in dashboard

    assert "@media(min-width:641px)" in reports
    assert "min-height:44px" in reports
    assert 'aria-live="polite"' in reports


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
        for name in ("sync", "chat", "reports", "profile")
    }
    nav_styles = []
    nav_markup = []

    for html in templates.values():
        assert 'class="app-nav"' in html
        assert 'aria-label="主要导航"' in html
        assert html.count("data-nav-item") == 3
        assert html.count('data-nav-item aria-current="page"') == 1
        assert 'href="/sync"' in html
        assert 'href="/reports"' in html
        assert 'href="/profile"' in html
        assert ".app-nav{position:fixed" in html
        assert "safe-area-inset-bottom" in html
        assert "@media(min-width:641px)" in html
        assert ".nav-tabs" not in html

        style_start = html.index(".app-header{")
        style_end = html.index(".theme-btn.active{background:var(--accent);color:#fff}")
        nav_styles.append(html[style_start:style_end])
        nav_start = html.index('<nav class="app-nav"')
        nav_end = html.index("</nav>", nav_start) + len("</nav>")
        nav_markup.append(
            html[nav_start:nav_end].replace(' aria-current="page"', "")
        )

    assert len(set(nav_styles)) == 1
    assert len(set(nav_markup)) == 1


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
