"""测试 memory.py — 日报生成、数据汇总、AI 洞察。"""

import stat
from datetime import date
from pathlib import Path
from src.memory import (
    Memory, MemoryType, MemoryStatus,
    parse_front_matter, build_memory_file,
    MemoryWriter, MemoryReader, MemoryValidator, MemoryStore,
)

# ── Sample Data ────────────────────────────────

SAMPLE_ACTIVITIES = [
    {
        "activity_id": "123", "activity_name": "晨跑", "activity_date": "2026-06-25",
        "duration_seconds": 3600, "distance_meters": 10000,
        "avg_heart_rate": 145, "training_load": 120.0, "activity_training_load": 120.0,
        "start_time": "2026-06-25 07:00",
    },
    {
        "activity_id": "124", "activity_name": "室内跑步", "activity_date": "2026-06-25",
        "duration_seconds": 1800, "distance_meters": 5000,
        "avg_heart_rate": 135, "training_load": 60.0, "activity_training_load": 60.0,
        "start_time": "2026-06-25 18:00",
    },
]

SAMPLE_HEALTH = {
    "sleep_duration_hours": 7.5, "deep_sleep_hours": 1.5, "rem_sleep_hours": 1.8,
    "deep_sleep_percentage": 20.0, "rem_sleep_percentage": 24.0,
    "resting_heart_rate": 48, "hrv_last_night_avg": 55.0,
    "hrv_weekly_avg": 52.0, "hrv_status": "balanced",
    "body_battery_high": 85, "body_battery_low": 20,
    "avg_stress_level": 28, "max_stress_level": 65,
    "training_readiness_score": 72, "training_readiness_level": "MODERATE",
    "total_steps": 12000, "total_distance_meters": 9500.0,
    "total_calories": 2200, "active_calories": 400,
}

EMPTY_HEALTH: dict = {}


class TestFrontMatter:
    def test_parse(self):
        text = "---\ntype: daily_report\ndate: 2026-06-25\n---\n\n# Hello\nWorld"
        fm, body = parse_front_matter(text)
        assert fm["type"] == "daily_report"
        assert str(fm["date"]) == "2026-06-25"
        assert body.strip() == "# Hello\nWorld"

    def test_no_front_matter(self):
        text = "# Just markdown"
        fm, body = parse_front_matter(text)
        assert fm == {}
        assert body == "# Just markdown"

    def test_build(self):
        fm = {"type": "goal", "status": "active"}
        body = "# My Goal"
        result = build_memory_file(fm, body)
        assert "---" in result
        assert "type: goal" in result
        assert "# My Goal" in result


class TestActivitySummarize:
    def test_summarize_with_data(self):
        summary = MemoryWriter._summarize_activities(SAMPLE_ACTIVITIES, SAMPLE_HEALTH)
        assert summary["is_rest_day"] is False
        assert summary["is_training_day"] is True
        assert summary["total_sessions"] == 2
        assert summary["total_duration_min"] == 90.0  # 3600+1800 = 5400s = 90min
        assert summary["total_distance_km"] == 15.0
        assert summary["total_training_load"] == 180.0
        assert summary["day_type"] == "long_run"  # 90min + 15km > 10km
        assert summary["daily_steps"] == 12000

    def test_summarize_rest_day(self):
        summary = MemoryWriter._summarize_activities(
            [], SAMPLE_HEALTH, activity_state="confirmed_rest",
        )
        assert summary["is_rest_day"] is True
        assert summary["is_training_day"] is False
        assert summary["activity_state"] == "confirmed_rest"

    def test_empty_activity_without_sync_coverage_is_unknown(self):
        summary = MemoryWriter._summarize_activities([], SAMPLE_HEALTH)

        assert summary["is_rest_day"] is False
        assert summary["is_training_day"] is False
        assert summary["activity_state"] == "unknown"
        assert summary["day_type"] == "unknown"

    def test_summarize_no_health(self):
        summary = MemoryWriter._summarize_activities(SAMPLE_ACTIVITIES, {})
        assert summary["daily_steps"] == 0


class TestSleepSummarize:
    def test_good_sleep(self):
        s = MemoryWriter._summarize_sleep(SAMPLE_HEALTH)
        assert s["total_hours"] == 7.5
        assert s["quality"] == "good"  # >= 7h + deep_pct >= 15
        assert s["deep_sleep_pct"] == 20.0

    def test_empty_health(self):
        s = MemoryWriter._summarize_sleep({})
        assert s["quality"] == "unknown"


class TestMorningSummarize:
    def test_morning(self):
        m = MemoryWriter._summarize_morning(SAMPLE_HEALTH)
        assert m["resting_hr"] == 48
        assert m["hrv_ms"] == 55.0
        assert m["hrv_status"] == "balanced"
        assert m["body_battery_morning"] == 85
        assert m["training_readiness_score"] == 72


class TestRecoveryScore:
    def test_score_calculation(self):
        r = MemoryWriter._calc_recovery_score(SAMPLE_HEALTH, SAMPLE_HEALTH)
        assert 0 <= r["overall_score"] <= 100
        assert r["level"] in ("excellent", "good", "fair", "poor")

    def test_balanced_hrv_scores_higher_than_low_hrv(self):
        balanced = MemoryWriter._calc_recovery_score(
            SAMPLE_HEALTH, {**SAMPLE_HEALTH, "hrv_status": "balanced"},
        )
        low = MemoryWriter._calc_recovery_score(
            SAMPLE_HEALTH, {**SAMPLE_HEALTH, "hrv_status": "low"},
        )

        assert balanced["overall_score"] > low["overall_score"]


class TestTrainingLoad:
    def test_acwr(self):
        # acute: 180；28 天总负荷 180 折算为周均 45，ACWR=4.0。
        l = MemoryWriter._calc_training_load(SAMPLE_ACTIVITIES, SAMPLE_ACTIVITIES)
        assert l["acute_load_7d"] == 180.0
        assert l["chronic_load_28d"] == 45.0
        assert l["acwr"] == 4.0

    def test_uses_persisted_training_load_field(self):
        activities = [{"training_load": 80.0}, {"training_load": 40.0}]

        result = MemoryWriter._calc_training_load(activities, activities)

        assert result["acute_load_7d"] == 120.0
        assert result["chronic_load_28d"] == 30.0


class TestAIInsight:
    def test_generates(self):
        result = MemoryWriter._generate_ai_insight(
            MemoryWriter._summarize_activities(SAMPLE_ACTIVITIES, SAMPLE_HEALTH),
            MemoryWriter._summarize_sleep(SAMPLE_HEALTH),
            MemoryWriter._summarize_morning(SAMPLE_HEALTH),
            MemoryWriter._calc_training_load(SAMPLE_ACTIVITIES, []),
            MemoryWriter._calc_recovery_score(SAMPLE_HEALTH, SAMPLE_HEALTH),
            {"count": 0, "level": "normal", "items": []},
            [],
        )
        assert "conclusion" in result
        assert len(result["observations"]) > 0


class TestMemoryTypes:
    def test_enum_values(self):
        assert MemoryType.DAILY_REPORT.value == "daily_report"
        assert MemoryType.GOAL.value == "goal"
        assert MemoryStatus.ACTIVE.value == "active"

    def test_save_uses_private_atomic_file(self, tmp_path):
        path = tmp_path / "memory" / "daily" / "2026-07-26.md"
        memory = Memory(
            id="2026-07-26",
            type=MemoryType.DAILY_REPORT,
            path=path,
            front_matter={"type": "daily_report", "date": "2026-07-26"},
            body="# 日报",
        )

        memory.save()
        path.chmod(0o400)
        memory.body = "# 更新日报"
        memory.save()

        assert "更新日报" in path.read_text(encoding="utf-8")
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


class TestValidator:
    def test_daily_report_required_fields(self):
        from pathlib import Path
        import tempfile, os
        v = MemoryValidator(tempfile.mkdtemp())
        mem = Memory(
            id="test", type=MemoryType.DAILY_REPORT,
            path=Path("/tmp/test.md"),
            front_matter={"type": "daily_report", "date": "2026-06-25", "generated": "now"},
        )
        errors = v.validate(mem)
        assert len(errors) == 0

    def test_missing_required(self):
        from pathlib import Path
        import tempfile
        v = MemoryValidator(tempfile.mkdtemp())
        mem = Memory(
            id="test", type=MemoryType.DAILY_REPORT,
            path=Path("/tmp/test.md"),
            front_matter={"type": "daily_report"},
        )
        errors = v.validate(mem)
        assert len(errors) > 0
