"""Coros Training Hub 自动重登凭据与重试边界。"""

from __future__ import annotations

import hashlib
import json
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import httpx
import pytest
from cryptography.fernet import Fernet


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_coros_relogin_credential_is_encrypted_and_private(tmp_path):
    from src.providers.coros_credentials import CorosReloginCredentialStore

    token_dir = tmp_path / "user-a" / "tokens"
    store = CorosReloginCredentialStore(str(token_dir), _key())

    store.save("runner@example.com", "platform-password", "eu")

    raw = store.path.read_text(encoding="utf-8")
    password_digest = hashlib.md5(
        b"platform-password", usedforsecurity=False,
    ).hexdigest()
    assert "runner@example.com" not in raw
    assert "platform-password" not in raw
    assert password_digest not in raw
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == {
        "account": "runner@example.com",
        "accountType": 2,
        "pwd": password_digest,
        "region": "eu",
        "version": 1,
    }


def test_coros_relogin_credential_never_falls_back_without_key(tmp_path):
    from src.providers.coros_credentials import (
        CorosCredentialKeyUnavailable,
        CorosReloginCredentialStore,
    )

    store = CorosReloginCredentialStore(str(tmp_path / "tokens"), "")

    with pytest.raises(CorosCredentialKeyUnavailable, match="加密密钥"):
        store.save("runner@example.com", "platform-password", "eu")

    assert not store.path.exists()


def test_coros_mobile_relogin_payload_is_encrypted_separately(tmp_path):
    from src.providers.coros_credentials import CorosMobileCredentialStore

    token_dir = tmp_path / "user-a" / "tokens"
    store = CorosMobileCredentialStore(str(token_dir), _key())
    payload = {"account": "sleep@example.com", "pwd": "opaque-mobile-value"}

    store.save(payload, "eu")

    raw = store.path.read_text(encoding="utf-8")
    assert "sleep@example.com" not in raw
    assert "opaque-mobile-value" not in raw
    assert store.load() == {
        "login_payload": payload,
        "region": "eu",
        "version": 1,
    }


def test_coros_relogin_credential_rejects_corrupt_ciphertext(tmp_path):
    from src.providers.coros_credentials import (
        CorosCredentialCorrupted,
        CorosReloginCredentialStore,
    )

    store = CorosReloginCredentialStore(str(tmp_path / "tokens"), _key())
    store.path.parent.mkdir(parents=True)
    store.path.write_text("not-a-fernet-token", encoding="utf-8")

    with pytest.raises(CorosCredentialCorrupted, match="无法解密"):
        store.load()


def test_coros_access_token_1019_relogs_and_retries_once(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    key = _key()
    auth = CorosAuth(
        str(tmp_path / "tokens"), credential_key=key,
        remember_credentials=True,
    )
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")
    refreshed = StoredAuth(
        access_token="refreshed-token", user_id="12345", region="eu",
        timestamp=2,
    )
    seen_tokens: list[str] = []

    def operation():
        token = auth.get_headers()["accessToken"]
        seen_tokens.append(token)
        if token == "expired-token":
            raise RuntimeError("access token is invalid (result=1019)")
        return "ok"

    with mock.patch.object(
        auth, "_login_with_replay", return_value=refreshed,
    ) as relogin:
        assert auth.run_with_training_relogin(operation) == "ok"

    assert seen_tokens == ["expired-token", "refreshed-token"]
    relogin.assert_called_once()
    saved = json.loads((tmp_path / "tokens" / "coros-auth.json").read_text())
    assert saved["access_token"] == "refreshed-token"
    assert auth.credential_store.path.exists()


def test_coros_login_opt_in_saves_relogin_credential(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(
        str(tmp_path / "tokens"), credential_key=_key(),
        remember_credentials=True,
    )
    stored = StoredAuth(
        access_token="training-token", user_id="12345", region="eu",
        timestamp=1,
    )

    with (
        mock.patch.object(auth, "_login_training_password", return_value=stored),
        mock.patch("coros_mcp.coros_api._mobile_login") as mobile_login,
    ):
        assert auth.login_training(
            "runner@example.com", "platform-password", "eu",
            auto_refresh=True,
        ) is True

    assert auth.auto_relogin_enabled is True
    assert auth.auto_relogin_warning is None
    assert auth.credential_store.load()["account"] == "runner@example.com"
    mobile_login.assert_not_called()


def test_coros_training_login_never_writes_upstream_global_auth(tmp_path):
    from src.providers.coros import CorosAuth

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    response = mock.Mock(status_code=200)
    response.json.return_value = {
        "result": "0000",
        "data": {"accessToken": "training-token", "userId": "12345"},
    }

    with (
        mock.patch("httpx.post", return_value=response),
        mock.patch("coros_mcp.coros_api._save_auth") as save_global,
    ):
        assert auth.login_training(
            "runner@example.com", "platform-password", "eu",
        ) is True

    save_global.assert_not_called()
    assert auth.get_headers()["accessToken"] == "training-token"


def test_coros_sleep_login_preserves_training_and_encrypts_mobile_replay(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    token_dir = tmp_path / "tokens"
    auth = CorosAuth(str(token_dir), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="training-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()

    with mock.patch(
        "coros_mcp.coros_api._mobile_login",
        new=mock.AsyncMock(return_value=(
            "mobile-token", {"account": "sleep@example.com", "pwd": "opaque"},
        )),
    ):
        assert auth.login_sleep(
            "sleep@example.com", "mobile-password", auto_refresh=True,
        ) is True

    assert auth.get_headers()["accessToken"] == "training-token"
    assert auth.sleep_auto_refresh_enabled is True
    assert auth.has_sleep_access() is True
    saved = json.loads((token_dir / "coros-auth.json").read_text())
    assert saved["mobile_access_token"] == "mobile-token"
    assert saved.get("mobile_login_payload") is None
    assert "sleep@example.com" not in (token_dir / "coros-mobile-relogin.enc").read_text()


def test_coros_mobile_1019_replays_encrypted_payload_and_retries(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="training-token", user_id="12345", region="eu",
        timestamp=1, mobile_access_token="expired-mobile-token",
    )
    auth._save()
    auth.mobile_credential_store.save({"opaque": "replay"}, "eu")
    seen: list[str] = []

    def operation():
        token = auth._auth.mobile_access_token
        seen.append(token)
        if token == "expired-mobile-token":
            error = RuntimeError("mobile token invalid")
            error.code = "1019"
            raise error
        return "ok"

    response = mock.Mock(status_code=200)
    response.json.return_value = {
        "result": "0000", "data": {"accessToken": "fresh-mobile-token"},
    }
    with mock.patch("httpx.post", return_value=response) as post:
        assert auth.run_with_sleep_relogin(operation) == "ok"

    assert seen == ["expired-mobile-token", "fresh-mobile-token"]
    assert post.call_args.kwargs["json"] == {"opaque": "replay"}
    assert auth.mobile_credential_store.enabled is True
    saved = json.loads((tmp_path / "tokens" / "coros-auth.json").read_text())
    assert saved["mobile_access_token"] == "fresh-mobile-token"
    assert saved.get("mobile_login_payload") is None


def test_coros_mobile_rejected_replay_only_disables_sleep_refresh(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth, CorosReloginRejected

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="training-token", user_id="12345", region="eu",
        timestamp=1, mobile_access_token="expired-mobile-token",
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")
    auth.mobile_credential_store.save({"opaque": "replay"}, "eu")
    response = mock.Mock(status_code=200)
    response.json.return_value = {"result": "1019"}

    def expired():
        error = RuntimeError("mobile token invalid")
        error.code = "1019"
        raise error

    with mock.patch("httpx.post", return_value=response):
        with pytest.raises(CorosReloginRejected):
            auth.run_with_sleep_relogin(expired)

    assert auth.mobile_credential_store.enabled is False
    assert auth.credential_store.enabled is True
    assert auth.get_headers()["accessToken"] == "training-token"


def test_coros_login_without_key_keeps_binding_but_warns(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(
        str(tmp_path / "tokens"), remember_credentials=True,
    )
    stored = StoredAuth(
        access_token="training-token", user_id="12345", region="eu",
        timestamp=1,
    )

    with (
        mock.patch.object(auth, "_login_training_password", return_value=stored),
        mock.patch("coros_mcp.coros_api._mobile_login", new=mock.AsyncMock(
            return_value=("mobile-token", {"encrypted": "payload"}),
        )),
    ):
        assert auth.login("runner@example.com", "platform-password") is True

    assert auth.auto_relogin_enabled is False
    assert "加密密钥" in (auth.auto_relogin_warning or "")


def test_coros_replay_login_preserves_mobile_credentials(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1, mobile_access_token="mobile-token",
        mobile_login_payload={"encrypted": "payload"},
    )
    response = mock.Mock(status_code=200)
    response.json.return_value = {
        "result": "0000",
        "data": {"accessToken": "refreshed-token", "userId": "12345"},
    }

    with mock.patch("httpx.post", return_value=response) as post:
        refreshed = auth._login_with_replay({
            "account": "runner@example.com",
            "accountType": 2,
            "pwd": "password-digest",
            "region": "eu",
            "version": 1,
        })

    request = post.call_args.kwargs
    assert request["json"] == {
        "account": "runner@example.com",
        "accountType": 2,
        "pwd": "password-digest",
    }
    assert request["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert refreshed.access_token == "refreshed-token"
    assert refreshed.mobile_access_token == "mobile-token"
    assert refreshed.mobile_login_payload == {"encrypted": "payload"}


def test_coros_rejected_relogin_deletes_saved_credential(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import (
        CorosAuth,
        CorosReloginRejected,
    )

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")

    def expired():
        raise RuntimeError("access token is invalid (result=1019)")

    with mock.patch.object(
        auth, "_login_with_replay",
        side_effect=CorosReloginRejected("access token is invalid"),
    ):
        with pytest.raises(CorosReloginRejected):
            auth.run_with_training_relogin(expired)

    assert not auth.credential_store.path.exists()


def test_coros_unknown_relogin_business_error_keeps_credential(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")
    response = mock.Mock(status_code=200)
    response.json.return_value = {"result": "2005", "message": "busy"}

    def expired():
        raise RuntimeError("access token is invalid (result=1019)")

    with mock.patch("httpx.post", return_value=response):
        with pytest.raises(RuntimeError, match="result=2005"):
            auth.run_with_training_relogin(expired)

    assert auth.credential_store.path.exists()


def test_coros_relogin_is_singleflight_across_provider_instances(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    key = _key()
    token_dir = tmp_path / "tokens"
    seed = CorosAuth(str(token_dir), credential_key=key)
    seed._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    seed._save()
    seed.credential_store.save("runner@example.com", "password", "eu")
    first = CorosAuth(str(token_dir), credential_key=key)
    second = CorosAuth(str(token_dir), credential_key=key)
    login_count = 0
    count_lock = threading.Lock()

    def relogin(_self, _payload):
        nonlocal login_count
        with count_lock:
            login_count += 1
        time.sleep(0.05)
        return StoredAuth(
            access_token="refreshed-token", user_id="12345", region="eu",
            timestamp=2,
        )

    def operation(auth):
        def request():
            token = auth.get_headers()["accessToken"]
            if token == "expired-token":
                raise RuntimeError("access token is invalid (result=1019)")
            return token

        return auth.run_with_training_relogin(request)

    with mock.patch.object(CorosAuth, "_login_with_replay", autospec=True,
                           side_effect=relogin):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(operation, (first, second)))

    assert results == ["refreshed-token", "refreshed-token"]
    assert login_count == 1


def test_coros_second_1019_deletes_relogin_credential(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth, CorosAuthenticationError

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")
    refreshed = StoredAuth(
        access_token="still-invalid", user_id="12345", region="eu",
        timestamp=2,
    )

    def always_expired():
        raise RuntimeError("access token is invalid (result=1019)")

    with mock.patch.object(auth, "_login_with_replay", return_value=refreshed):
        with pytest.raises(CorosAuthenticationError, match="重登后仍然失效"):
            auth.run_with_training_relogin(always_expired)

    assert not auth.credential_store.path.exists()


def test_coros_temporary_relogin_failure_keeps_credential(tmp_path):
    from coros_mcp.models import StoredAuth
    from src.providers.coros import CorosAuth

    auth = CorosAuth(str(tmp_path / "tokens"), credential_key=_key())
    auth._auth = StoredAuth(
        access_token="expired-token", user_id="12345", region="eu",
        timestamp=1,
    )
    auth._save()
    auth.credential_store.save("runner@example.com", "password", "eu")

    def expired():
        raise RuntimeError("access token is invalid (result=1019)")

    with mock.patch.object(
        auth, "_login_with_replay", side_effect=httpx.ReadTimeout("timeout"),
    ):
        with pytest.raises(httpx.ReadTimeout):
            auth.run_with_training_relogin(expired)

    assert auth.credential_store.path.exists()
