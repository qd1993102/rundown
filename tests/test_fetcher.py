from unittest import mock


def test_fetcher_reuses_auth_managers_regional_api_client():
    from src.fetcher import Fetcher

    api_client = object()
    auth = mock.Mock()
    auth.create_api_client.return_value = api_client

    fetcher = Fetcher(auth)

    assert fetcher.client is api_client
    assert fetcher.client is api_client
    auth.create_api_client.assert_called_once_with()
