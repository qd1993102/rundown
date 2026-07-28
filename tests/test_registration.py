"""邀请码注册、应用账号登录与会话门禁测试。"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.config import Config
from src.invitations import InvitationError, InvitationStore
from src.users import UserExistsError, UserManager, UserRecord, verify_password
from src.web import _registration_error, register_web_routes


def _write_invites(path):
    path.write_text(json.dumps({
        "codes": [{
            "id": "inv_test",
            "code": "NEURUN-TEST",
            "enabled": True,
            "created_at": "2026-07-21T00:00:00+00:00",
            "used_by": None,
            "used_at": None,
        }],
    }), encoding="utf-8")


def test_invitation_store_accepts_and_consumes_legacy_long_code(tmp_path):
    path = tmp_path / "invite-codes.json"
    _write_invites(path)
    store = InvitationStore(str(path))

    assert store.validate("NEURUN-TEST") is True
    store.consume("NEURUN-TEST", "runner@example.com")
    assert store.validate("NEURUN-TEST") is False

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["codes"][0]["used_by"] == "runner@example.com"
    assert data["codes"][0]["used_at"].endswith("+00:00")


def test_invitation_store_admin_create_list_show_and_revoke(tmp_path):
    path = tmp_path / "invite-codes.json"
    store = InvitationStore(str(path))

    created = store.create(2)
    assert len(created) == 2
    assert len(created[0].code) == 6
    assert set(created[0].code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert created[0].code != created[1].code
    assert path.stat().st_mode & 0o777 == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700

    listed = store.list_all()
    assert [item.id for item in listed] == [item.id for item in created]
    assert store.get(created[0].id) == created[0]
    assert store.get(created[0].id).to_admin_dict()["code"] == "******"
    assert store.get(created[0].id).to_admin_dict(reveal=True)["code"] == created[0].code

    revoked = store.revoke(created[0].id)
    assert revoked.enabled is False
    assert store.validate(created[0].code) is False


def test_invitation_store_rejects_shared_use_schema(tmp_path):
    path = tmp_path / "invite-codes.json"
    path.write_text(json.dumps({
        "codes": [{
            "id": "inv_shared",
            "code": "SHARED",
            "enabled": True,
            "created_at": "2026-07-21T00:00:00+00:00",
            "used_by": None,
            "used_at": None,
            "max_uses": 5,
            "uses": [],
        }],
    }), encoding="utf-8")

    with pytest.raises(InvitationError, match="只允许单次使用"):
        InvitationStore(str(path)).validate("SHARED")


def test_invitation_store_reports_missing_or_invalid_config(tmp_path):
    with pytest.raises(InvitationError, match="邀请码文件不存在"):
        InvitationStore(str(tmp_path / "missing.json")).validate("code")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(InvitationError, match="codes 数组"):
        InvitationStore(str(invalid)).validate("code")


def test_user_manager_registers_hashed_account_and_authenticates(tmp_path):
    manager = UserManager(str(tmp_path))
    user = manager.register_account(" 跑者 ", "Runner@Example.COM ", "safe-password")

    user_path = tmp_path / "users" / f"{user.api_key}.json"
    stored = json.loads(user_path.read_text(encoding="utf-8"))
    assert user.nickname == "跑者"
    assert user.email == "runner@example.com"
    assert stored["password_hash"] != "safe-password"
    assert verify_password("safe-password", stored["password_hash"]) is True
    assert manager.authenticate("RUNNER@example.com", "safe-password") == user
    assert manager.authenticate("runner@example.com", "wrong-password") is None
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "users").stat().st_mode) == 0o700
    assert stat.S_IMODE(user_path.stat().st_mode) == 0o600

    user_path.chmod(0o400)
    manager.update(user.api_key, nickname="新昵称")
    assert json.loads(user_path.read_text(encoding="utf-8"))["nickname"] == "新昵称"
    assert stat.S_IMODE(user_path.stat().st_mode) == 0o600

    manager.ensure_dirs(user.api_key)
    for directory in (
        tmp_path / user.api_key,
        tmp_path / user.api_key / "tokens",
        tmp_path / user.api_key / "memory",
        tmp_path / "backup",
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    with pytest.raises(UserExistsError, match="已注册"):
        manager.register_account("另一个昵称", "runner@example.com", "another-password")


def test_legacy_user_record_has_no_login_account():
    record = UserRecord.from_dict({
        "api_key": "rd_legacy",
        "garmin_email": "legacy@example.com",
        "token_status": "active",
    })
    assert record.has_account is False
    assert record.provider == "garmin"
    assert record.garmin_domain == "garmin.com"


def test_legacy_user_cookie_is_ignored(tmp_path):
    manager = UserManager(str(tmp_path))
    legacy_path = tmp_path / "users" / "rd_legacy.json"
    legacy_path.write_text(json.dumps({
        "api_key": "rd_legacy",
        "provider": "garmin",
        "token_status": "active",
    }), encoding="utf-8")

    assert manager.get("rd_legacy") is None
    assert manager.list_all() == []


@pytest.mark.parametrize(("nickname", "email", "password", "expected"), [
    ("a", "runner@example.com", "password", "昵称"),
    ("跑者", "not-an-email", "password", "邮箱"),
    ("跑者", "runner@example.com", "short", "8"),
    ("跑者", "runner@example.com", "safe-password", None),
])
def test_registration_field_validation(nickname, email, password, expected):
    result = _registration_error(nickname, email, password)
    if expected is None:
        assert result is None
    else:
        assert expected in result


class _FakeServer:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(func):
            for method in methods:
                self.routes[(path, method)] = func
            return func
        return decorator


def _request(path, body=None, cookie=None, scheme="http"):
    raw = json.dumps(body or {}).encode()
    headers = [(b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }, receive)


def _json(response):
    return json.loads(response.body)


def test_registration_and_login_routes_create_session_and_consume_invite(tmp_path):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    config = Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path))
    manager = UserManager(str(tmp_path))
    server = _FakeServer()
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/register", "POST")](_request(
        "/api/register",
        {"invite_code": "NEURUN-TEST", "nickname": "跑者", "email": "runner@example.com", "password": "safe-password"},
        scheme="https",
    )))
    assert response.status_code == 201
    assert _json(response)["next"] == "/setup"
    cookie = response.headers["set-cookie"]
    assert "neurun_key=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "Max-Age=2592000" in cookie
    assert json.loads(invite_path.read_text(encoding="utf-8"))["codes"][0]["used_by"] == "runner@example.com"

    login = asyncio.run(server.routes[("/api/login", "POST")](_request(
        "/api/login", {"email": "RUNNER@example.com", "password": "safe-password"}
    )))
    assert login.status_code == 200
    assert _json(login)["next"] == "/setup"

    duplicate = asyncio.run(server.routes[("/api/register", "POST")](_request(
        "/api/register",
        {"invite_code": "NEURUN-TEST", "nickname": "重复", "email": "runner@example.com", "password": "safe-password"},
    )))
    assert duplicate.status_code == 400
    assert "已经使用" in _json(duplicate)["message"]


def test_active_account_cannot_rebind_platform(tmp_path):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    config = Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path))
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    manager.update(user.api_key, token_status="active")
    server = _FakeServer()
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/setup", "POST")](_request(
        "/api/setup",
        {"provider": "coros", "email": "runner@example.com", "password": "platform-password"},
        cookie=f"neurun_key={user.api_key}",
    )))
    assert response.status_code == 409
    assert "暂不支持换绑" in _json(response)["message"]


def test_expired_connection_can_only_rebind_same_platform(tmp_path):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    config = Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path))
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    manager.update(user.api_key, provider="garmin", token_status="expired")
    server = _FakeServer()
    register_web_routes(server, manager, config)

    response = asyncio.run(server.routes[("/api/setup", "POST")](_request(
        "/api/setup",
        {"provider": "coros", "email": "runner@example.com", "password": "platform-password"},
        cookie=f"neurun_key={user.api_key}",
    )))
    assert response.status_code == 409
    assert "只能重新绑定原平台" in _json(response)["message"]


def test_setup_api_requires_application_session(tmp_path):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    server = _FakeServer()
    register_web_routes(
        server,
        UserManager(str(tmp_path)),
        Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path)),
    )

    response = asyncio.run(server.routes[("/api/setup", "POST")](_request(
        "/api/setup", {"provider": "garmin", "email": "data@example.com", "password": "secret"}
    )))
    assert response.status_code == 401
    assert "请先登录" in _json(response)["message"]


def test_huawei_setup_persists_user_credential_after_authentication(tmp_path, monkeypatch):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    config = Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path))
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    server = _FakeServer()
    register_web_routes(server, manager, config)

    class FakeHuaweiProvider:
        def __init__(self, user_config):
            assert user_config.group_pals_token == "group-user-token"

        def authenticate(self):
            return True

    monkeypatch.setattr("src.providers.huawei.HuaweiProvider", FakeHuaweiProvider)

    response = asyncio.run(server.routes[("/api/setup", "POST")](_request(
        "/api/setup",
        {"provider": "huawei", "group_pals_token": "group-user-token"},
        cookie=f"neurun_key={user.api_key}",
    )))

    assert response.status_code == 200
    assert manager.get(user.api_key).provider == "huawei"
    assert manager.get(user.api_key).token_status == "active"
    token_path = tmp_path / user.api_key / "huawei-tokens" / "group-pals-token"
    assert token_path.read_text(encoding="utf-8").strip() == "group-user-token"
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_huawei_setup_does_not_persist_credential_when_authentication_fails(
    tmp_path, monkeypatch,
):
    invite_path = tmp_path / "invite-codes.json"
    _write_invites(invite_path)
    config = Config(data_dir=str(tmp_path), invite_codes_file=str(invite_path))
    manager = UserManager(str(tmp_path))
    user = manager.register_account("跑者", "runner@example.com", "safe-password")
    server = _FakeServer()
    register_web_routes(server, manager, config)

    class FakeHuaweiProvider:
        def __init__(self, user_config):
            pass

        def authenticate(self):
            return False

    monkeypatch.setattr("src.providers.huawei.HuaweiProvider", FakeHuaweiProvider)

    response = asyncio.run(server.routes[("/api/setup", "POST")](_request(
        "/api/setup",
        {"provider": "huawei", "group_pals_token": "invalid-token"},
        cookie=f"neurun_key={user.api_key}",
    )))

    assert response.status_code == 400
    assert manager.get(user.api_key).token_status == "none"
    assert manager.get(user.api_key).provider == "garmin"
    token_path = tmp_path / user.api_key / "huawei-tokens" / "group-pals-token"
    assert not token_path.exists()


def test_invite_cli_generates_json_without_provider_credentials(tmp_path, monkeypatch, capsys):
    from src.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEURUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NEURUN_ACCOUNT", raising=False)
    monkeypatch.delenv("NEURUN_PASSWORD", raising=False)

    main(["invite", "create", "--count", "1", "--output", "json"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert len(payload) == 1
    assert len(payload[0]["code"]) == 6
    assert set(payload[0]["code"]) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert (tmp_path / "data" / "invite-codes.json").exists()


def test_admin_mcp_tools_are_local_and_opt_in(monkeypatch):
    from src.config import ConfigError
    from src.main import _admin_mcp_tools_enabled

    monkeypatch.delenv("NEURUN_ENABLE_ADMIN_TOOLS", raising=False)
    monkeypatch.delenv("NEURUN_SERVE_MODE", raising=False)
    assert _admin_mcp_tools_enabled("stdio", "127.0.0.1") is False

    monkeypatch.setenv("NEURUN_ENABLE_ADMIN_TOOLS", "true")
    assert _admin_mcp_tools_enabled("stdio", "0.0.0.0") is True
    assert _admin_mcp_tools_enabled("sse", "127.0.0.1") is True
    with pytest.raises(ConfigError, match="localhost"):
        _admin_mcp_tools_enabled("sse", "0.0.0.0")

    monkeypatch.setenv("NEURUN_SERVE_MODE", "true")
    with pytest.raises(ConfigError, match="serve 模式"):
        _admin_mcp_tools_enabled("stdio", "127.0.0.1")


def test_invite_mcp_tools_are_registered_only_when_enabled(monkeypatch, tmp_path):
    from src.mcp_server import create_server

    class FakeFastMCP:
        def __init__(self, **kwargs):
            self.tool_names = []

        def resource(self, *args, **kwargs):
            return lambda func: func

        def tool(self, *args, **kwargs):
            def decorator(func):
                self.tool_names.append(func.__name__)
                return func
            return decorator

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeFastMCP))
    config = SimpleNamespace(invite_codes_path=str(tmp_path / "invite-codes.json"))

    normal = create_server(config, object(), object(), object(), 0)
    assert not {"invite_create", "invite_list", "invite_show", "invite_revoke"} & set(normal.tool_names)

    admin = create_server(
        config, object(), object(), object(), 0, enable_admin_tools=True
    )
    assert {"invite_create", "invite_list", "invite_show", "invite_revoke"} <= set(admin.tool_names)
