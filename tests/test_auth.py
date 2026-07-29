from types import SimpleNamespace
from unittest import mock

import pytest
import requests


def test_auth_manager_api_client_uses_configured_garmin_domain(monkeypatch):
    from src.auth import AuthManager

    auth_client = object()
    manager = AuthManager(SimpleNamespace(
        domain="garmin.cn",
        non_interactive=True,
    ))
    manager._client = auth_client
    api_client = object()
    constructor = mock.Mock(return_value=api_client)
    monkeypatch.setattr("garmy.APIClient", constructor)

    assert manager.create_api_client() is api_client
    constructor.assert_called_once_with(
        auth_client=auth_client,
        domain="garmin.cn",
    )


def test_auth_manager_refreshes_existing_token_before_login():
    from src.auth import AuthManager

    class FakeClient:
        is_authenticated = False
        needs_refresh = True

        def __init__(self):
            self.refresh_calls = 0

        def refresh_tokens(self):
            self.refresh_calls += 1
            self.is_authenticated = True

    client = FakeClient()

    assert AuthManager._try_existing_token(client) is True
    assert client.refresh_calls == 1


def test_auth_manager_does_not_hide_temporary_refresh_error():
    from src.auth import AuthManager

    class FakeClient:
        is_authenticated = False
        needs_refresh = True

        def refresh_tokens(self):
            raise TimeoutError("temporary refresh failure")

    with pytest.raises(TimeoutError, match="temporary refresh failure"):
        AuthManager._try_existing_token(FakeClient())


def test_auth_manager_allows_explicit_login_after_rejected_refresh():
    from src.auth import AuthManager

    response = requests.Response()
    response.status_code = 401

    class FakeClient:
        is_authenticated = False
        needs_refresh = True

        def refresh_tokens(self):
            raise requests.HTTPError("Unauthorized", response=response)

    assert AuthManager._try_existing_token(FakeClient()) is False


def test_expired_mfa_cleanup_closes_auth_http_session(monkeypatch):
    import src.auth as auth_module

    class Session:
        closed = False

        def close(self):
            self.closed = True

    session = Session()
    client = SimpleNamespace(http_client=SimpleNamespace(session=session))
    monkeypatch.setitem(auth_module._mfa_states, "expired", {
        "client": client,
        "expires": 0,
    })

    assert auth_module.cleanup_expired_mfa_states() == 1
    assert session.closed is True
    assert "expired" not in auth_module._mfa_states
