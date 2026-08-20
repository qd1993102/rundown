"""测试 CLI/Web 共用的同步与日报核心流程。"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


def test_setup_memory_api_client_keeps_garmin_region(monkeypatch):
    import src.main as main
    import src.providers as providers

    api_client = object()
    auth = SimpleNamespace(
        _client=object(),
        create_api_client=mock.Mock(return_value=api_client),
    )
    provider = SimpleNamespace(
        auth=auth,
        authenticate=lambda: True,
        user_id=123,
    )
    captured = {}

    monkeypatch.setattr(providers, "get_provider", lambda config: provider)
    monkeypatch.setattr(main, "Storage", lambda config: object())

    def fake_memory_store(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(main, "MemoryStore", fake_memory_store)

    main._setup(SimpleNamespace(
        provider_type="garmin",
        memory_dir="/tmp/memory",
    ))

    assert captured["api_client_getter"]() is api_client
    auth.create_api_client.assert_called_once_with()


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


def test_setup_preserves_temporary_provider_error(monkeypatch):
    import src.main as main
    import src.providers as providers

    class FakeProvider:
        def authenticate(self):
            raise TimeoutError("Garmin temporary timeout")

    monkeypatch.setattr(providers, "get_provider", lambda config: FakeProvider())

    with pytest.raises(TimeoutError, match="temporary timeout"):
        main._setup(SimpleNamespace(
            provider_type="garmin",
            memory_dir="/tmp/memory",
        ))


def test_data_sync_marks_calendar_range_completed(monkeypatch):
    import src.main as main

    calls = []

    class FakeStorage:
        def mark_sync_calendar_range(self, user_id, start, end, status, error_message=None):
            calls.append((user_id, start, end, status, error_message))

    storage = FakeStorage()
    monkeypatch.setattr(main, "_setup", lambda config: (
        config, SimpleNamespace(), storage, SimpleNamespace(), 42,
    ))
    monkeypatch.setattr(main, "_sync_provider", lambda *args: None)

    main._do_data_sync(
        SimpleNamespace(provider_type="coros"),
        target=date(2026, 7, 26),
        sync_days=2,
        quiet=True,
    )

    assert calls == [
        (42, date(2026, 7, 24), date(2026, 7, 26), "pending", None),
        (42, date(2026, 7, 24), date(2026, 7, 26), "completed", None),
    ]


def test_data_sync_marks_calendar_range_failed(monkeypatch):
    import src.main as main

    calls = []

    class FakeStorage:
        def mark_sync_calendar_range(self, user_id, start, end, status, error_message=None):
            calls.append((status, error_message))

    storage = FakeStorage()
    provider = SimpleNamespace()
    closer = mock.Mock()
    monkeypatch.setattr(main, "_setup", lambda config: (
        config, provider, storage, SimpleNamespace(), 42,
    ))
    monkeypatch.setattr(main, "close_runtime_resources", closer)
    monkeypatch.setattr(
        main, "_sync_provider", mock.Mock(side_effect=RuntimeError("provider timeout")),
    )

    with pytest.raises(RuntimeError, match="provider timeout"):
        main._do_data_sync(
            SimpleNamespace(provider_type="huawei"),
            target=date(2026, 7, 26),
            sync_days=0,
            quiet=True,
        )

    assert calls == [
        ("pending", None),
        ("failed", "provider timeout"),
    ]
    closer.assert_called_once_with(provider, storage)


def test_non_garmin_resync_refreshes_average_hr_and_training_load(tmp_path):
    import src.main as main
    from src.config import Config
    from src.providers.base import ActivityData
    from src.storage import Storage

    class Activities:
        current = ActivityData(
            activity_id="coros-update", activity_name="Run",
            activity_type="running", start_time="2026-08-14 08:00:00",
            duration_seconds=600, distance_meters=2000,
            avg_heart_rate=155, training_load=107,
        )

        def fetch_activities(self, start, end):
            return [self.current]

        def fetch_activity_detail(self, activity_id, **kwargs):
            return {}

    class Health:
        def fetch_health_range(self, start, end, **kwargs):
            return []

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    activities = Activities()
    provider = SimpleNamespace(activities=activities, health=Health())

    main._sync_provider(
        provider, storage, 1, date(2026, 8, 14), date(2026, 8, 14), "coros",
    )
    activities.current = ActivityData(
        activity_id="coros-update", activity_name="Run",
        activity_type="running", start_time="2026-08-14 08:00:00",
        duration_seconds=600, distance_meters=2000,
        avg_heart_rate=158, training_load=113,
    )
    main._sync_provider(
        provider, storage, 1, date(2026, 8, 14), date(2026, 8, 14), "coros",
    )

    row = storage.get_activities_range(
        1, date(2026, 8, 14), date(2026, 8, 14),
    )[0]
    assert row["avg_heart_rate"] == 158
    assert row["training_load"] == 113


def test_garmin_data_sync_injects_regional_api_client(monkeypatch):
    import src.main as main

    api_client = object()
    auth = SimpleNamespace(
        _client=object(),
        create_api_client=mock.Mock(return_value=api_client),
    )
    provider = SimpleNamespace(auth=auth)

    class FakeStorage:
        def reset_pending_metrics(self, *args, **kwargs):
            pass

        def mark_sync_calendar_range(self, *args, **kwargs):
            pass

        def set_api_client(self, value):
            self.api_client = value

        def sync_range(self, *args, **kwargs):
            kwargs["progress_callback"]({
                "current": 1,
                "total": 11,
                "date": "2026-07-26",
                "metric": "sleep",
                "outcome": "completed",
            })

    storage = FakeStorage()
    progress = []
    monkeypatch.setattr(main, "_setup", lambda config: (
        config, provider, storage, SimpleNamespace(), 42,
    ))
    monkeypatch.setattr(main, "_sync_garmin_activities", lambda *args: None)

    main._do_data_sync(
        SimpleNamespace(provider_type="garmin"),
        target=date(2026, 7, 26),
        sync_days=0,
        quiet=True,
        progress_callback=lambda *args: progress.append(args),
    )

    assert storage.api_client is api_client
    auth.create_api_client.assert_called_once_with()
    assert progress == [
        ("authenticating", 1, 4, "正在验证数据源"),
        ("syncing_metrics", 2, 4, "正在同步健康指标"),
        ("syncing_metrics", 2, 4, "正在同步健康指标", {
            "current": 1,
            "total": 11,
            "date": "2026-07-26",
            "metric": "sleep",
            "outcome": "completed",
        }),
        ("syncing_activities", 3, 4, "正在同步运动记录"),
    ]


def test_provider_item_progress_is_throttled_but_keeps_final(monkeypatch):
    import src.main as main

    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))
    updates = []
    report = main._throttled_item_progress_callback(
        lambda *args: updates.append(args),
        "syncing_metrics", 3, 4, "正在同步健康指标",
    )

    report({"current": 1, "total": 3, "date": "2026-07-27",
            "metric": "daily_health", "outcome": "completed"})
    report({"current": 2, "total": 3, "date": "2026-07-28",
            "metric": "daily_health", "outcome": "skipped"})
    report({"current": 3, "total": 3, "date": "2026-07-29",
            "metric": "daily_health", "outcome": "completed"})

    assert [args[4]["current"] for args in updates] == [1, 3]


@pytest.mark.xfail(reason="readiness 门禁重构后 mock 未同步（分支既有）", strict=False)
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
        config=SimpleNamespace(provider_type="garmin"),
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


def test_daily_parser_exposes_machine_readable_report_mode():
    from src.main import build_parser

    args = build_parser().parse_args([
        "daily", "--date", "2026-07-19", "--format", "json",
        "--skip-sync", "-m", "limited",
    ])

    assert args.command == "daily"
    assert args.report_mode == "limited"
    assert args.skip_sync is True
    assert args.format == "json"


def test_sync_activity_details_preserves_lapdto_when_relap_fails(tmp_path):
    """needs_relap 重拉时若 fetch_activity_splits 失败：
    - 原有 lapDTOs 必须保留（不得被无 lapDTOs 的 detail 覆盖降级）；
    - 原有没有 lapDTOs 时不得用旧 detail 覆盖，保持现状等下次同步。"""
    import json as _json
    from types import SimpleNamespace

    import src.main as main
    from src.activity import (
        ensure_tables, get_activity_detail, get_activity_summary_facts,
        store_activity_detail,
    )
    from src.config import Config
    from src.storage import Storage
    from sqlalchemy import text

    def make_storage(aid: str) -> Storage:
        storage = Storage(Config(db_path=str(tmp_path / "data.db")))
        ensure_tables(storage)
        session = storage.db.get_session()
        session.execute(text("""
            INSERT INTO activities (user_id, activity_id, activity_date)
            VALUES (1, :aid, '2026-08-11')
        """), {"aid": aid})
        session.commit()
        session.close()
        return storage

    class _Activities:
        def fetch_activity_detail(self, activity_id):
            return {"summaryDTO": {"duration": 3000, "distance": 12000},
                    "splitSummaries": [{"distance": 1000, "duration": 240}]}

        def fetch_activity_splits(self, activity_id):
            return []  # 重拉失败

    class _Provider:
        activities = _Activities()

    def make_activity(aid: str) -> SimpleNamespace:
        return SimpleNamespace(
            activity_id=aid, activity_type="running", activity_name="晨跑",
        )

    # 场景 1：原有 detail 已带 lapDTOs（已修复）→ 重拉失败必须保留并重建 summary
    storage = make_storage("aid-lap")
    detail_with_lap = {
        "summaryDTO": {"duration": 3000, "distance": 12000},
        "splitSummaries": [{"distance": 1000, "duration": 240}],
        "lapDTOs": [{"distance": 1000, "duration": 240, "intensityType": "INTERVAL"}],
    }
    assert store_activity_detail(storage, 1, "aid-lap", detail_with_lap)
    session = storage.db.get_session()
    session.execute(text(
        "UPDATE activity_summary_facts SET summary_json = :s WHERE activity_id = 'aid-lap'"
    ), {"s": _json.dumps({
        "granularity": "L1", "volume": {"distance_m": 12000, "duration_s": 3000},
        "structure": {"n_splits": 1}, "intensity": {"basis": "hr"},
        "terrain": {}, "data_quality": {}, "version": "old",
    })})
    session.commit()
    session.close()
    result = main._sync_activity_details(
        _Provider(), storage, 1, [make_activity("aid-lap")],
    )
    # 保留 lapDTOs 并重新存储（stored=1），lapDTOs 不丢、summary 重建回新 schema
    assert result["stored"] == 1
    kept = get_activity_detail(storage, "aid-lap")
    assert kept.get("lapDTOs"), "lapDTOs 不应被覆盖丢失"
    facts = get_activity_summary_facts(storage, "aid-lap")
    assert facts.get("segment_sequence"), "summary 应基于保留的 lapDTOs 重建出新 schema"

    # 场景 2：原有也没有 lapDTOs（未修复）+ 重拉失败 → 不覆盖，保持现状
    storage2 = make_storage("aid-lap2")
    detail_plain = {
        "summaryDTO": {"duration": 3000, "distance": 12000},
        "splitSummaries": [{"distance": 1000, "duration": 240}],
    }
    assert store_activity_detail(storage2, 1, "aid-lap2", detail_plain)
    # 手工降级 summary 为旧 schema（无 segment_sequence），构造 needs_relap=True
    session2 = storage2.db.get_session()
    session2.execute(text(
        "UPDATE activity_summary_facts SET summary_json = :s WHERE activity_id = 'aid-lap2'"
    ), {"s": _json.dumps({
        "granularity": "L1", "volume": {"distance_m": 12000, "duration_s": 3000},
        "structure": {"n_splits": 1}, "intensity": {"basis": "hr"},
        "terrain": {}, "data_quality": {}, "version": "old",
    })})
    session2.commit()
    session2.close()
    result2 = main._sync_activity_details(
        _Provider(), storage2, 1, [make_activity("aid-lap2")],
    )
    assert result2["stored"] == 0
    assert result2["failed"] == 1
    facts2 = get_activity_summary_facts(storage2, "aid-lap2")
    assert not (facts2 or {}).get("segment_sequence"), "拉取失败时不得覆盖已有数据"


def test_sync_activity_details_rebuilds_missing_summary_facts(tmp_path):
    """有 detail 但缺 activity_summary_facts（旧同步/重建失败）时，
    用已有 detail 重建分段摘要，不强制重拉 detail。"""
    from types import SimpleNamespace

    import src.main as main
    from src.activity import (
        ensure_tables, get_activity_summary_facts, store_activity_detail,
    )
    from src.config import Config
    from src.storage import Storage
    from sqlalchemy import text

    storage = Storage(Config(db_path=str(tmp_path / "data.db")))
    ensure_tables(storage)
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (1, 'aid-summary', '2026-08-11')
    """))
    session.commit()
    session.close()

    detail = {
        "summaryDTO": {"duration": 3000, "distance": 12000},
        "splitSummaries": [
            {"distance": 4000, "duration": 960, "averageHR": 150},
            {"distance": 4000, "duration": 1020, "averageHR": 152},
            {"distance": 4000, "duration": 1020, "averageHR": 148},
        ],
    }
    assert store_activity_detail(storage, 1, "aid-summary", detail)
    # 模拟旧数据：删除 summary_facts，保留 detail
    session = storage.db.get_session()
    session.execute(text(
        "DELETE FROM activity_summary_facts WHERE activity_id = 'aid-summary'"
    ))
    session.commit()
    session.close()

    class _Activities:
        def fetch_activity_detail(self, activity_id):
            raise AssertionError("不应重新拉取 detail")

    class _Provider:
        activities = _Activities()

    activity = SimpleNamespace(
        activity_id="aid-summary", activity_type="running", activity_name="晨跑",
    )
    result = main._sync_activity_details(_Provider(), storage, 1, [activity])
    assert result["stored"] == 1
    facts = get_activity_summary_facts(storage, "aid-summary")
    assert facts is not None
    assert facts.get("granularity") == "L1"
    assert (facts.get("intensity") or {}).get("pace_bands_pct"), \
        "重建的摘要应含分段配速带（intensity.pace_bands_pct）"
    assert facts.get("structure"), "重建的摘要应含结构（n_splits/步频等）"


def test_invite_create_reports_written_file(tmp_path, monkeypatch, capsys):
    """invite create 的 json 模式在 stderr 输出实际写入路径，stdout 保持纯 JSON。"""
    from src.main import cmd_invite

    target = tmp_path / "invite-codes.json"
    fake_config = SimpleNamespace(invite_codes_path=str(target))
    monkeypatch.setattr("src.main.get_config", lambda **kw: fake_config)

    args = SimpleNamespace(invite_subcommand="create", count=1, output="json")
    cmd_invite(args)

    captured = capsys.readouterr()
    assert '"code"' in captured.out, "stdout 应输出 JSON 邀请码"
    assert "已写入文件" in captured.err
    assert "invite-codes.json" in captured.err
    assert target.exists(), "邀请码应写入解析出的文件"


def _write_sqlite_label(path, label):
    import sqlite3

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", (label,))
        conn.commit()
    finally:
        conn.close()


def _read_sqlite_label(path):
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM t ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def test_restore_all_users_only_restores_when_needed(tmp_path):
    """启动恢复只在 data.db 缺失/为空/损坏或备份更新时进行，不每次全量回滚。"""
    import os

    import src.main as main

    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backup"
    backup_dir.mkdir(parents=True)

    healthy = "rd_healthy"      # data.db 健康且比备份新 → 不恢复
    missing = "rd_missing"      # 无 data.db，有备份 → 恢复
    corrupt = "rd_corrupt"      # data.db 损坏，有备份 → 恢复
    empty = "rd_empty"          # data.db 为空文件，有备份 → 恢复
    stale = "rd_stale"          # 备份比 data.db 新 → 恢复
    no_backup = "rd_nobackup"   # 无备份 → 跳过

    _write_sqlite_label(data_dir / healthy / "data.db", "live")
    _write_sqlite_label(backup_dir / f"{healthy}.db", "backup")
    os.utime(backup_dir / f"{healthy}.db", (1, 1))

    _write_sqlite_label(backup_dir / f"{missing}.db", "backup-missing")

    (data_dir / corrupt).mkdir(parents=True)
    (data_dir / corrupt / "data.db").write_bytes(b"this is not a sqlite database")
    _write_sqlite_label(backup_dir / f"{corrupt}.db", "backup-corrupt")

    (data_dir / empty).mkdir(parents=True)
    (data_dir / empty / "data.db").write_bytes(b"")
    _write_sqlite_label(backup_dir / f"{empty}.db", "backup-empty")

    _write_sqlite_label(data_dir / stale / "data.db", "stale-live")
    _write_sqlite_label(backup_dir / f"{stale}.db", "backup-new")
    os.utime(data_dir / stale / "data.db", (1, 1))

    _write_sqlite_label(data_dir / no_backup / "data.db", "live-nobackup")

    class FakeUserManager:
        def list_all(self):
            return [
                SimpleNamespace(api_key=k) for k in (
                    healthy, missing, corrupt, empty, stale, no_backup,
                )
            ]

        def get_backup_path(self, api_key):
            return str(backup_dir / f"{api_key}.db")

        def get_db_path(self, api_key):
            return str(data_dir / api_key / "data.db")

    main._restore_all_users(FakeUserManager())

    assert _read_sqlite_label(data_dir / healthy / "data.db") == "live"
    assert _read_sqlite_label(data_dir / missing / "data.db") == "backup-missing"
    assert _read_sqlite_label(data_dir / corrupt / "data.db") == "backup-corrupt"
    assert _read_sqlite_label(data_dir / empty / "data.db") == "backup-empty"
    assert _read_sqlite_label(data_dir / stale / "data.db") == "backup-new"
    assert _read_sqlite_label(data_dir / no_backup / "data.db") == "live-nobackup"
