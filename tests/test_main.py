"""测试 CLI/Web 共用的同步与日报核心流程。"""

from datetime import date
from types import SimpleNamespace


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
