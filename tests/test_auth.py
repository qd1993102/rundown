from types import SimpleNamespace
from unittest import mock


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
