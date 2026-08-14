"""测试 memory.py — 日报生成、数据汇总、AI 洞察。"""

import stat
from datetime import date
from pathlib import Path
from src.memory import (
    Memory, MemoryType, MemoryStatus,
    parse_front_matter, build_memory_file, get_daily_activities,
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

    def test_daily_activities_prefers_canonical_and_falls_back_to_legacy(self):
        assert get_daily_activities({
            "daily_activities": {"total_sessions": 2},
            "yesterday_activities": {"total_sessions": 1},
        }) == {"total_sessions": 2}
        assert get_daily_activities({
            "yesterday_activities": {"total_sessions": 1},
        }) == {"total_sessions": 1}


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


def test_plan_execution_summary_uses_running_facts_and_keeps_unknown_as_pending():
    plan_context = {
        "comparison_status": "applicable",
        "status": "active",
        "goal": {"name": "秋季半马", "target_date": "2026-09-20"},
        "current_phase": {"name": "基础期"},
        "week_start": "2026-08-03",
        "week_end": "2026-08-09",
        "weekly_mileage_target": 40,
        "workout": {
            "title": "轻松跑", "type": "easy", "purpose": "有氧积累",
            "intensity": "RPE 3", "distance_km": 8,
        },
    }
    summary = MemoryWriter._build_plan_execution_summary(
        plan_context,
        {
            "activity_state": "training", "is_rest_day": False,
            "sessions": [{"type": "running"}], "total_distance_km": 8,
            "total_duration_min": 48,
        },
        [
            {"activity_type": "running", "distance_meters": 10000},
            {"activity_type": "cycling", "distance_meters": 30000},
        ],
        date(2026, 8, 3),
        {"training_advice": "建议轻松训练或主动恢复", "caution": ["注意补水"]},
    )

    assert summary["status"] == "completed"
    assert summary["comparison"]["primary_metric"] == "距离"
    assert summary["comparison"]["ratio"] == 1.0
    assert summary["planned"]["training_prescription"]["targets"]["feel"]["label"]
    assert summary["week"]["recorded_km"] == 10
    assert summary["goal"]["days_to_goal"] == 48
    assert summary["guidance"] == "建议轻松训练或主动恢复"
    assert summary["caution"] == ["注意补水"]

    pending = MemoryWriter._build_plan_execution_summary(
        plan_context, {"activity_state": "unknown"}, [], date(2026, 8, 3),
    )
    assert pending["status"] == "awaiting_sync"
    assert pending["actual"] is None
    assert pending["sync_url"].startswith("/sync?date=2026-08-03")


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
    @staticmethod
    def _base_insight(session_analyses=None, load=None, seven_day=None, **kwargs):
        return MemoryWriter._generate_ai_insight(
            MemoryWriter._summarize_activities(SAMPLE_ACTIVITIES, SAMPLE_HEALTH),
            MemoryWriter._summarize_sleep(SAMPLE_HEALTH),
            MemoryWriter._summarize_morning(SAMPLE_HEALTH),
            load if load is not None else MemoryWriter._calc_training_load(SAMPLE_ACTIVITIES, []),
            MemoryWriter._calc_recovery_score(SAMPLE_HEALTH, SAMPLE_HEALTH),
            {"count": 0, "level": "normal", "items": []},
            seven_day if seven_day is not None else [],
            session_analyses=session_analyses,
            **kwargs,
        )

    def test_generates(self):
        result = self._base_insight()
        assert "conclusion" in result
        assert len(result["observations"]) > 0
        observations = "\n".join(result["observations"])
        # 本地洞察聚焦解读（训练量/结构由深度分析呈现，不再重复罗列）
        assert "昨夜睡眠" in observations
        assert "当日完成 2 节训练" not in observations
        assert "昨天完成" not in observations

    def test_observation_three_blocks_in_order(self):
        # 训练日：运动概要 → 强度分布解释与分析 → 恢复分析
        session_summary = {
            "granularity": "L1",
            "volume": {"distance_m": 12000, "duration_s": 3000, "pace_sec_per_km": 250},
            "structure": {"avg_cadence": 184.0, "avg_stride": 110.0, "n_splits": 6},
            "intensity": {
                "pace_bands_pct": {"240-300": 60.0, "300-360": 40.0},
                "hr_bands_pct": {"130-145": 67.0, "145-160": 33.0},
                "basis": "hr",
            },
            "pace_profile": {
                "avg_pace_sec_per_km": 250.0, "p50": 255.0, "split_count": 6,
            },
            "quantity_gate": {"quantity_reliable": True},
            "effect": {
                "aerobic_training_effect": 4.0, "estimated": True,
                "estimate_basis": "heart_rate",
            },
        }
        analysis = {
            "session_summary": session_summary,
            "structure_classification": {
                "structure_type": "interval",
                "label": "间歇结构",
                "alternations": 4,
                "confidence": 1.0,
                "work_recovery_groups": [
                    {"work_pace_sec_per_km": 224, "recovery_pace_sec_per_km": 304},
                ],
            },
        }
        result = self._base_insight(
            session_analyses=[analysis],
            load={
                "acute_load_7d": 180.0, "chronic_load_28d": 150.0,
                "acwr": 1.2, "acwr_status": "optimal",
            },
        )
        observations = result["observations"]
        # 运动概要含训练效果解释（估算必带依据），且"间歇结构（4 组快慢交替"在前
        overview = next(o for o in observations if o.startswith("运动概要"))
        assert "间歇结构（4 组快慢交替" in overview
        assert "训练效果 有氧 4" in overview
        assert "高强度刺激" in overview and "估算，依据心率" in overview
        # 四块各自成段、按序出现：运动概要 → 强度分布 → 恢复分析 → 近 7 天负荷与恢复
        overview_idx = next(i for i, o in enumerate(observations) if "12.0km" in o)
        intensity_idx = next(i for i, o in enumerate(observations) if "步频" in o)
        recovery_idx = next(i for i, o in enumerate(observations) if o.startswith("恢复分析"))
        week_idx = next(i for i, o in enumerate(observations) if o.startswith("近 7 天负荷与恢复"))
        assert overview_idx < intensity_idx < recovery_idx < week_idx
        joined = "\n".join(observations)
        # 运动概要：距离 + 课型结构 + 平均配速
        assert joined.startswith("运动概要：12.0km")
        assert "12.0km" in joined and "间歇结构" in joined and "3 组快慢交替" not in joined
        assert "间歇结构（4 组快慢交替" in joined
        assert "平均配速" in joined
        # 强度分布：配速带/心率带 + 步频/步幅（avg_stride 为 cm，转米展示）
        assert "配速以" in joined and "心率以 130–145 bpm 为主" in joined
        assert "平均步频 184 spm、步幅 1.10 m" in joined
        # 恢复分析：睡眠/HRV/身体电量/恢复评分合并为一段（ACWR 归属近 7 天负荷块）
        recovery_item = next(o for o in observations if o.startswith("恢复分析"))
        assert "昨夜睡眠" in recovery_item
        assert "综合恢复评分" in recovery_item
        assert "ACWR" not in recovery_item
        # 近 7 天负荷与恢复：ACWR 与负荷趋势
        week_item = next(o for o in observations if o.startswith("近 7 天负荷与恢复"))
        assert "ACWR" in week_item and "负荷" in week_item

    def test_intensity_basis_pace_noted_when_hr_missing(self):
        session_summary = {
            "granularity": "L1",
            "volume": {"distance_m": 10000, "duration_s": 3600, "pace_sec_per_km": 300},
            "structure": {},
            "intensity": {
                "pace_bands_pct": {"300-360": 70.0, "240-300": 30.0},
                "hr_bands_pct": {},
                "basis": "pace",
            },
            "pace_profile": {"avg_pace_sec_per_km": 300.0, "p50": 310.0},
        }
        analysis = {"session_summary": session_summary, "structure_classification": {}}
        result = self._base_insight(session_analyses=[analysis])
        joined = "\n".join(result["observations"])
        assert "心率缺失" in joined

    def test_unknown_structure_no_fabricated_steady_claim(self):
        # 无结构判定（如 L0 无分段）时不得编造"稳定配速"
        session_summary = {
            "granularity": "L0",
            "volume": {"distance_m": 10000, "duration_s": 3600, "pace_sec_per_km": 300},
        }
        analysis = {
            "session_summary": session_summary,
            "structure_classification": {"structure_type": "unknown", "label": "未知"},
        }
        result = self._base_insight(session_analyses=[analysis])
        joined = "\n".join(result["observations"])
        assert "稳定配速" not in joined
        assert "10.0km" in joined

    def test_observation_seven_day_load_and_recovery_trend(self):
        # 近 7 天负荷与恢复：ACWR + 睡眠/HRV 趋势，正确按老→新计算方向
        seven_day = [
            {"metric_date": "2026-06-19", "sleep_duration_hours": 7.6, "hrv_last_night_avg": 58.0},
            {"metric_date": "2026-06-20", "sleep_duration_hours": 7.5, "hrv_last_night_avg": 57.0},
            {"metric_date": "2026-06-21", "sleep_duration_hours": 7.4, "hrv_last_night_avg": 56.0},
            {"metric_date": "2026-06-22", "sleep_duration_hours": 6.5, "hrv_last_night_avg": 52.0},
            {"metric_date": "2026-06-23", "sleep_duration_hours": 6.2, "hrv_last_night_avg": 50.0},
            {"metric_date": "2026-06-24", "sleep_duration_hours": 6.0, "hrv_last_night_avg": 48.0},
        ]
        load = {
            "acute_load_7d": 180.0, "chronic_load_28d": 150.0,
            "acwr": 1.2, "acwr_status": "optimal",
        }
        result = self._base_insight(load=load, seven_day=seven_day)
        week_item = next(o for o in result["observations"] if o.startswith("近 7 天负荷与恢复"))
        assert "ACWR 1.2" in week_item and "最优区间" in week_item
        assert "睡眠近 3 天走低" in week_item
        assert "HRV 近期走低" in week_item
        # 睡眠/HRV 事实只出现在近 7 天块，当日恢复块不重复
        recovery_item = next(o for o in result["observations"] if o.startswith("恢复分析"))
        assert "走低" not in recovery_item

    def test_seven_day_block_skipped_without_load_or_trend(self):
        # 无负荷数据也无 7 天趋势时，不输出近 7 天块，也不编造 ACWR
        result = self._base_insight(load={"acute_load_7d": 0, "chronic_load_28d": 0, "acwr": 1.0, "acwr_status": "optimal"})
        assert not any(o.startswith("近 7 天负荷与恢复") for o in result["observations"])

    def test_recovery_sleep_fact_appears_once(self):
        # 同一事实（睡眠）在观察内部只出现一次，不重复强调
        result = self._base_insight()
        joined = "\n".join(result["observations"])
        assert joined.count("昨夜") == 1
        assert joined.count("睡眠") == 1


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
