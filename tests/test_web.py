"""测试 Web 多用户同步的兼容迁移。"""

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest


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


def test_parse_sync_request_keeps_legacy_payload_compatible():
    from src.web import _parse_sync_request

    parsed = _parse_sync_request({
        "date": "2026-07-19",
        "sync_days": 30,
        "skip_sync": True,
    }, default_days=7)

    assert parsed.mode == "legacy"
    assert parsed.target == date(2026, 7, 19)
    assert parsed.sync_days == 30
    assert parsed.skip_sync is True


def test_generate_batch_reports_backfills_each_day_except_existing_target():
    from src.web import _generate_batch_reports

    class FakeMemoryStore:
        def __init__(self):
            self.generated = []

        def generate_daily_report(self, user_id, target_date):
            self.generated.append((user_id, target_date))
            return SimpleNamespace(id=str(target_date))

    store = FakeMemoryStore()
    count = _generate_batch_reports(
        store,
        user_id=123,
        start=date(2026, 7, 17),
        end=date(2026, 7, 19),
        existing_target=date(2026, 7, 19),
    )

    assert count == 3
    assert store.generated == [
        (123, date(2026, 7, 17)),
        (123, date(2026, 7, 18)),
    ]


def test_detects_coros_expired_token_error():
    from src.web import _is_coros_auth_error

    assert _is_coros_auth_error(
        RuntimeError("Access token is invalid (result=1019)")
    ) is True
    assert _is_coros_auth_error(RuntimeError("temporary network error")) is False
