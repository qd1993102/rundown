"""测试 Web 多用户同步与日报生成。"""

import asyncio
import json
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


def _request(path, body, api_key, method="POST", query=""):
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

    assert "rebind=coros" in profile_html
    assert "searchParams.get('rebind')" in setup_html
    assert "body.rebind = true" in setup_html


def test_active_coros_user_can_open_sleep_reauthorization_page(tmp_path):
    manager, user = _active_user(tmp_path)
    manager.update(user.api_key, provider="coros", token_status="active")
    server = _FakeServer()
    register_web_routes(server, manager, Config(data_dir=str(tmp_path)))

    response = asyncio.run(server.routes[("/setup", "GET")](_request(
        "/setup", {}, user.api_key, method="GET", query="rebind=coros",
    )))

    assert response.status_code == 200
    assert "启用 Coros 睡眠同步" in response.body.decode()


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


def test_sync_calendar_route_returns_local_month_status(tmp_path, monkeypatch):
    import src.web as web

    manager, user = _active_user(tmp_path)
    server = _FakeServer()
    config = Config(data_dir=str(tmp_path))
    calls = []

    class FakeStorage:
        def __init__(self, user_config):
            calls.append(("init", user_config.api_key))

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
    assert calls[-1] == (
        "calendar", 88, date(2026, 7, 1), date(2026, 7, 31),
    )


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
