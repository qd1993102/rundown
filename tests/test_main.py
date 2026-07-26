"""测试 CLI/Web 共用的同步与日报核心流程。"""

from datetime import date
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("provider_type", ["garmin", "coros", "huawei"])
def test_setup_authenticates_before_reading_provider_user_id(monkeypatch, provider_type):
    import src.main as main
    import src.providers as providers

    events = []

    class FakeProvider:
        auth = SimpleNamespace(_client=object())

        def authenticate(self):
            events.append("authenticate")
            return True

        @property
        def user_id(self):
            events.append("user_id")
            assert events[0] == "authenticate"
            return 123

    monkeypatch.setattr(providers, "get_provider", lambda config: FakeProvider())
    monkeypatch.setattr(main, "Storage", lambda config: object())
    monkeypatch.setattr(main, "MemoryStore", lambda *args, **kwargs: object())

    result = main._setup(SimpleNamespace(
        provider_type=provider_type,
        memory_dir="/tmp/memory",
    ))

    assert result[-1] == 123
    assert events == ["authenticate", "user_id"]


def test_setup_stops_before_user_id_when_authentication_fails(monkeypatch):
    import src.main as main
    import src.providers as providers

    class FakeProvider:
        auth = SimpleNamespace(_client=None)

        def authenticate(self):
            return False

        @property
        def user_id(self):
            raise AssertionError("认证失败后不得读取 user_id")

    monkeypatch.setattr(providers, "get_provider", lambda config: FakeProvider())
    monkeypatch.setattr(main, "Storage", lambda config: object())

    with pytest.raises(main.ProviderAuthenticationError, match="Garmin"):
        main._setup(SimpleNamespace(
            provider_type="garmin",
            memory_dir="/tmp/memory",
        ))


def test_setup_preserves_actionable_local_persistence_error(monkeypatch):
    import src.main as main
    import src.providers as providers
    from src.local_files import LocalPersistenceError

    class FakeProvider:
        def authenticate(self):
            raise LocalPersistenceError(
                "无法写入 /var/lib/neurun/token；请执行 chown -R neurun:neurun"
            )

    monkeypatch.setattr(providers, "get_provider", lambda config: FakeProvider())

    with pytest.raises(LocalPersistenceError, match="/var/lib/neurun/token"):
        main._setup(SimpleNamespace(
            provider_type="coros",
            memory_dir="/tmp/memory",
        ))


def test_daily_report_rerenders_body_with_coach_insight(monkeypatch):
    import src.main as main

    generated = []
    local_memory = SimpleNamespace(
        front_matter={"ai_insight": {"conclusion": "本地规则"}},
        body="本地规则正文",
    )
    coach_memory = SimpleNamespace(
        front_matter={"ai_insight": {"conclusion": "教练结论"}},
        body="教练结论正文",
    )

    class FakeMemoryStore:
        def generate_daily_report(self, user_id, target, ai_insight=None):
            generated.append((user_id, target, ai_insight))
            return coach_memory if ai_insight else local_memory

    memory_store = FakeMemoryStore()
    monkeypatch.setattr(main, "_local_report_context", lambda config: (
        None, SimpleNamespace(), memory_store, 123,
    ))
    monkeypatch.setattr(main, "_get_ai_insight", lambda fm, target, memory_store=None: {
        "conclusion": "教练结论",
        "model": "deepseek-chat",
    })

    result, *_ = main._do_daily_sync(
        config=SimpleNamespace(),
        target=date(2026, 7, 19),
        skip_sync=True,
        quiet=True,
    )

    assert result is coach_memory
    assert generated == [
        (123, date(2026, 7, 19), None),
        (123, date(2026, 7, 19), {
            "conclusion": "教练结论",
            "model": "deepseek-chat",
        }),
    ]
