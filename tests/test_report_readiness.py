from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from src.config import Config
from src.report_readiness import (
    DailyReportReadinessError,
    DailyReportReadinessService,
    enforce_report_readiness,
)
from src.storage import Storage


def _storage(tmp_path) -> Storage:
    return Storage(Config(db_path=str(tmp_path / "data.db")))


def _insert_health(storage: Storage, user_id: int, target: date) -> None:
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO daily_health_metrics (
            user_id, metric_date, sleep_duration_hours,
            resting_heart_rate, hrv_last_night_avg, avg_stress_level,
            created_at, updated_at
        ) VALUES (
            :uid, :metric_date, 7.5, 48, 56, 22,
            datetime('now'), datetime('now')
        )
    """), {"uid": user_id, "metric_date": str(target)})
    session.commit()
    session.close()


def test_activity_row_without_coverage_still_blocks_report(tmp_path):
    storage = _storage(tmp_path)
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (user_id, activity_id, activity_date)
        VALUES (7, 'legacy-run', '2026-07-20')
    """))
    session.commit()
    session.close()

    readiness = DailyReportReadinessService(
        storage, provider_type="huawei", today=date(2026, 8, 3),
    ).check(7, date(2026, 7, 20))

    assert readiness.status == "blocked"
    assert readiness.dimensions["activity"].status == "missing"
    assert readiness.dimensions["activity"].observed_days == 0
    assert "activity" in readiness.blockers


def test_huawei_is_ready_when_activity_window_has_proven_coverage(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )

    readiness = DailyReportReadinessService(
        storage, provider_type="huawei", today=date(2026, 8, 3),
    ).check(7, target)

    assert readiness.status == "ready"
    assert readiness.finality == "final"
    assert readiness.dimensions["sleep"].status == "unsupported"
    assert readiness.dimensions["recovery"].status == "unsupported"
    assert readiness.dimensions["load_28d"].status == "complete"
    assert "sleep" in readiness.omitted_sections
    assert "recovery" in readiness.omitted_sections


def test_supported_health_and_history_gaps_produce_limited_report(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(7, target, target, "completed")

    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)

    assert readiness.status == "limited"
    assert readiness.dimensions["activity"].status == "complete"
    assert readiness.dimensions["sleep"].status == "missing"
    assert readiness.dimensions["load_28d"].status == "insufficient_history"
    assert set(readiness.omitted_sections) >= {
        "sleep", "recovery", "trends_7d", "training_load", "recommendation",
    }


def test_complete_garmin_coverage_is_ready(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )
    for offset in range(7):
        _insert_health(storage, 7, target - timedelta(days=offset))

    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)

    assert readiness.status == "ready"
    assert readiness.finality == "final"
    assert readiness.omitted_sections == ()
    assert readiness.dimensions["sleep"].status == "complete"
    assert readiness.dimensions["trends_7d"].observed_days == 7
    assert readiness.dimensions["load_28d"].observed_days == 28


def _ready_garmin_memory(tmp_path, target: date):
    from src.memory import MemoryStore

    storage = _storage(tmp_path)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )
    for offset in range(7):
        _insert_health(storage, 7, target - timedelta(days=offset))
    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)
    assert readiness.status == "ready"
    memory_store = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )
    return storage, memory_store, readiness


def test_daily_report_stores_athlete_context_and_renders_background(tmp_path):
    _, memory_store, readiness = _ready_garmin_memory(
        tmp_path, date(2026, 7, 20),
    )
    athlete_context = {
        "status": "available",
        "source": "training_domain_capacity_profile",
        "facts_cutoff": "2026-07-19T23:59:00+08:00",
        "capacity_profile": {
            "as_of": "2026-07-20",
            "sync_coverage": "sufficient",
            "confidence": "high",
            "current_sustainable_capacity": {
                "weekly_km": 60.0, "long_run_km": 18.0,
                "recent_running_pace_sec_per_km": 330,
                "pace_sample_count": 8, "observed_weeks": 6,
            },
            "historical_proven_capacity": {},
            "entry_load_envelope": {},
            "current_readiness": {},
        },
    }

    report = memory_store.generate_daily_report(
        "7", date(2026, 7, 20), readiness=readiness.to_dict(),
        athlete_context=athlete_context, persist=False,
    )
    finalized = memory_store.finalize_daily_report(report)

    assert report.front_matter["athlete_context"] is athlete_context
    assert "能力背景" in finalized.body
    assert "可持续周跑量参考约 60 km" in finalized.body
    assert "长距离参考 18 km" in finalized.body
    assert "参考配速 5'30\"/km" in finalized.body
    assert "数据截至 2026-07-19T23:59:00+08:00" in finalized.body


def test_daily_report_forces_athlete_context_unavailable_when_load_omitted(tmp_path):
    _, memory_store, readiness = _ready_garmin_memory(
        tmp_path, date(2026, 7, 20),
    )
    # 模拟编排层误传可用背景，受限版下写入器仍强制不可用。
    provided = {
        "status": "available",
        "source": "training_domain_capacity_profile",
        "facts_cutoff": "2026-07-20T00:00:00+08:00",
        "capacity_profile": {},
    }
    readiness_dict = dict(readiness.to_dict())
    readiness_dict["omitted_sections"] = ["training_load"]

    report = memory_store.generate_daily_report(
        "7", date(2026, 7, 20), readiness=readiness_dict,
        athlete_context=provided, persist=False,
    )
    finalized = memory_store.finalize_daily_report(report)

    ctx = report.front_matter["athlete_context"]
    assert ctx["status"] == "unavailable"
    assert ctx["reason"] == "training_load_omitted"
    assert "能力背景" not in finalized.body


def test_daily_report_defaults_athlete_context_unavailable_when_not_loaded(tmp_path):
    _, memory_store, readiness = _ready_garmin_memory(
        tmp_path, date(2026, 7, 20),
    )

    report = memory_store.generate_daily_report(
        "7", date(2026, 7, 20), readiness=readiness.to_dict(), persist=False,
    )
    finalized = memory_store.finalize_daily_report(report)

    ctx = report.front_matter["athlete_context"]
    assert ctx["status"] == "unavailable"
    assert ctx["reason"] == "not_loaded"
    assert "能力背景" not in finalized.body


def test_zero_sleep_placeholder_does_not_count_as_health_trend(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )
    session = storage.db.get_session()
    for offset in range(7):
        session.execute(text("""
            INSERT INTO daily_health_metrics (
                user_id, metric_date, sleep_duration_hours,
                created_at, updated_at
            ) VALUES (
                7, :metric_date, 0,
                datetime('now'), datetime('now')
            )
        """), {"metric_date": str(target - timedelta(days=offset))})
    session.commit()
    session.close()

    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)

    assert readiness.status == "limited"
    assert readiness.dimensions["sleep"].status == "missing"
    assert readiness.dimensions["trends_7d"].observed_days == 0


def test_failed_activity_sync_blocks_even_limited_mode(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(
        7, target, target, "failed", error_message="provider timeout",
    )

    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)

    assert readiness.status == "blocked"
    assert readiness.dimensions["activity"].status == "failed"
    with pytest.raises(DailyReportReadinessError) as exc_info:
        enforce_report_readiness(readiness, "limited")
    assert exc_info.value.code == "report_data_incomplete"


def test_limited_mode_requires_explicit_opt_in(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(7, target, target, "completed")
    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)

    with pytest.raises(DailyReportReadinessError):
        enforce_report_readiness(readiness, "complete")
    assert enforce_report_readiness(readiness, "limited") is readiness


def test_today_report_is_always_provisional(tmp_path):
    storage = _storage(tmp_path)
    target = date(2026, 8, 3)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )

    readiness = DailyReportReadinessService(
        storage, provider_type="huawei", today=target,
    ).check(7, target)

    assert readiness.status == "ready"
    assert readiness.finality == "provisional"
    assert readiness.data_as_of is not None


def test_limited_report_persists_coverage_and_omits_unsupported_conclusions(tmp_path):
    from src.memory import MemoryStore

    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(7, target, target, "completed")
    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)
    enforce_report_readiness(readiness, "limited")
    memory_store = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )

    report = memory_store.generate_daily_report(
        "7", target, readiness=readiness.to_dict(),
    )

    assert report.front_matter["data_readiness"] == "limited"
    assert report.front_matter["last_night_sleep"]["status"] == "unavailable"
    assert report.front_matter["recovery"]["status"] == "unavailable"
    assert report.front_matter["training_load"]["status"] == "unavailable"
    assert report.front_matter["recommendation"]["intensity"] == "unknown"
    # 受限版同样生成洞察：缺失维度保留未知，不伪造恢复评分 / ACWR 结论
    ai = report.front_matter["ai_insight"]
    assert isinstance(ai, dict) and ai.get("conclusion")
    observations = "；".join(ai.get("observations") or [])
    assert "睡眠数据缺失" in observations or "恢复指标缺失" in observations
    assert "综合恢复评分" not in observations
    assert "ACWR" not in observations
    assert "数据不完整" in report.body
    assert "未计算 ACWR" in report.body


def test_daily_report_can_be_built_then_finalized_with_one_write(tmp_path):
    from src.memory import MemoryStore

    storage = _storage(tmp_path)
    target = date(2026, 7, 20)
    storage.mark_sync_calendar_range(7, target, target, "completed")
    readiness = DailyReportReadinessService(
        storage, provider_type="garmin", today=date(2026, 8, 3),
    ).check(7, target)
    memory_store = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )

    report = memory_store.generate_daily_report(
        "7", target, readiness=readiness.to_dict(), persist=False,
    )

    assert report.body == ""
    assert report.path.exists() is False

    finalized = memory_store.finalize_daily_report(report)

    assert finalized is report
    assert "数据不完整" in report.body
    assert report.path.exists() is True


def test_memory_writer_rejects_bypassing_readiness_gate(tmp_path):
    from src.memory import MemoryStore

    storage = _storage(tmp_path)
    memory_store = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )

    with pytest.raises(RuntimeError, match="完整性门禁"):
        memory_store.generate_daily_report(
            "7",
            date(2026, 7, 20),
            readiness={"status": "blocked", "omitted_sections": []},
        )


def test_report_date_only_contains_same_day_activity_and_uses_same_day_terms(tmp_path):
    from src.memory import MemoryStore, get_daily_activities

    storage = _storage(tmp_path)
    target = date(2026, 8, 2)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )
    session = storage.db.get_session()
    session.execute(text("""
        INSERT INTO activities (
            user_id, activity_id, activity_date, activity_name,
            duration_seconds, start_time
        ) VALUES
            (7, 'previous-day', '2026-08-01', '8月1日室内跑', 3600,
             '2026-08-01 20:00:00'),
            (7, 'report-day', '2026-08-02', '8月2日户外跑', 1800,
             '2026-08-02 08:00:00')
    """))
    session.commit()
    session.close()
    readiness = DailyReportReadinessService(
        storage, provider_type="huawei", today=date(2026, 8, 3),
    ).check(7, target)
    memory_store = MemoryStore(
        str(tmp_path / "memory"), db_getter=lambda: storage.db,
    )

    report = memory_store.generate_daily_report(
        "7", target, readiness=readiness.to_dict(),
    )
    activities = get_daily_activities(report.front_matter)

    assert [item["name"] for item in activities["sessions"]] == ["8月2日户外跑"]
    assert report.front_matter["daily_activities"] == activities
    assert report.front_matter["yesterday_activities"] == activities
    assert "8月1日室内跑" not in report.body
    assert "## 🏃 当日训练" in report.body
    assert "昨日训练" not in report.body


def test_report_does_not_apply_a_later_training_scheme_to_history(tmp_path):
    from src.memory import MemoryStore, build_memory_file

    storage = _storage(tmp_path)
    target = date(2026, 8, 2)
    storage.mark_sync_calendar_range(
        7, target - timedelta(days=27), target, "completed",
    )
    memory_dir = tmp_path / "memory"
    active_path = memory_dir / "plans/active-plan.md"
    active_path.parent.mkdir(parents=True)
    active_path.write_text(build_memory_file({
        "type": "training_scheme",
        "plan_id": "later-plan",
        "version": 1,
        "status": "active",
        "created_at": "2026-08-03T10:45:00+08:00",
        "updated_at": "2026-08-03T11:47:00+08:00",
        "activated_at": "2026-08-03T11:47:00+08:00",
        "effective_from": "2026-08-03",
        "effective_to": None,
        "goal_snapshot": {"name": "全马目标"},
        "weekly_pattern": [{
            "weekday": 6, "title": "恢复跑", "type": "easy",
            "duration_minutes": 45, "distance_km": 10.9,
        }],
        "week_overrides": {},
    }, "次日才生效的方案"), encoding="utf-8")
    readiness = DailyReportReadinessService(
        storage, provider_type="huawei", today=date(2026, 8, 3),
    ).check(7, target)
    memory_store = MemoryStore(
        str(memory_dir), db_getter=lambda: storage.db,
    )

    report = memory_store.generate_daily_report(
        "7", target, readiness=readiness.to_dict(),
    )

    assert report.front_matter["plan_context"] == {
        "status": "no_effective_plan",
        "exists": False,
        "target_date": "2026-08-02",
        "comparison_status": "not_applicable",
    }
