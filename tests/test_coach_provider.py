"""日报教练只通过一个显式 Skill 使用供应商无关配置。"""

from __future__ import annotations

from datetime import date

from src.coach import _normalize_report_date_language, get_coach_insight


def _set_generic_ai_environment(monkeypatch) -> None:
    monkeypatch.setenv("NEURUN_AI_API_KEY", "generic-key")
    monkeypatch.setenv("NEURUN_AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("NEURUN_AI_MODEL", "example-model")


class RecordingRunner:
    def __init__(self, result: dict):
        self.result = result
        self.calls = []

    def run(self, skill_name, context):
        self.calls.append((skill_name, context))
        return self.result, context


def test_daily_insight_uses_one_explicit_skill_and_structured_facts(monkeypatch):
    _set_generic_ai_environment(monkeypatch)
    runner = RecordingRunner({
        "plan_execution": {
            "today_planned": "恢复",
            "today_actual": "昨天完成训练",
            "today_match": "符合",
            "comparison_status": "applicable",
            "week_completion": "稳定",
            "on_track": True,
            "deviation_note": "",
        },
        "conclusion": "今日状态稳定",
        "observations": ["昨天完成训练"],
        "recommendations": ["明天恢复"],
        "warnings": [],
        "plan_adjusted": False,
    })
    monkeypatch.setattr("src.coach._daily_runner", lambda: runner)

    result = get_coach_insight({
        "daily_activities": {"count": 1},
        "recovery": {"overall_score": 72},
        "plan_context": {"status": "effective", "plan_id": "plan-1"},
    }, date(2026, 8, 3))

    assert len(runner.calls) == 1
    skill_name, context = runner.calls[0]
    assert skill_name == "review-daily-training"
    assert context.facts["daily_facts"]["daily_activities"] == {"count": 1}
    assert context.facts["active_scheme"]["plan_id"] == "plan-1"
    assert "tools" not in context.to_payload()
    assert result is not None
    assert result["model"] == "example-model"
    assert result["generation_mode"] == "online_ai"
    assert result["semantic_status"] == "available"
    assert result["fallback_reason"] is None
    assert result["conclusion"] == "当日状态稳定"
    assert result["observations"] == ["2026-08-02完成训练"]
    assert result["recommendations"] == ["2026-08-04恢复"]


def test_daily_facts_carry_athlete_context_for_capacity_background(monkeypatch):
    _set_generic_ai_environment(monkeypatch)
    runner = RecordingRunner({
        "plan_execution": {
            "today_planned": "恢复",
            "today_actual": "完成 8km",
            "today_match": "符合",
            "comparison_status": "applicable",
            "week_completion": "",
            "on_track": True,
            "deviation_note": "",
        },
        "conclusion": "状态稳定",
        "observations": [],
        "recommendations": [],
        "warnings": [],
        "plan_adjusted": False,
    })
    monkeypatch.setattr("src.coach._daily_runner", lambda: runner)

    result = get_coach_insight({
        "daily_activities": {"count": 0},
        "recovery": {"overall_score": 72},
        "athlete_context": {
            "status": "available",
            "facts_cutoff": "2026-08-02T23:59:00+08:00",
            "capacity_profile": {
                "current_sustainable_capacity": {"weekly_km": 60.0},
            },
        },
        "plan_context": {"status": "no_effective_plan"},
    }, date(2026, 8, 3))

    assert result is not None
    _, context = runner.calls[0]
    daily_facts = context.facts["daily_facts"]
    assert daily_facts["athlete_context"]["status"] == "available"
    assert daily_facts["athlete_context"]["capacity_profile"][
        "current_sustainable_capacity"
    ]["weekly_km"] == 60.0


def test_daily_facts_default_athlete_context_when_missing(monkeypatch):
    _set_generic_ai_environment(monkeypatch)
    runner = RecordingRunner({
        "plan_execution": {
            "today_planned": "恢复",
            "today_actual": "完成 8km",
            "today_match": "符合",
            "comparison_status": "not_applicable",
            "week_completion": "",
            "on_track": None,
            "deviation_note": "",
        },
        "conclusion": "状态稳定",
        "observations": [],
        "recommendations": [],
        "warnings": [],
        "plan_adjusted": False,
    })
    monkeypatch.setattr("src.coach._daily_runner", lambda: runner)

    get_coach_insight({
        "daily_activities": {"count": 0},
        "recovery": {},
        "plan_context": {"status": "no_effective_plan"},
    }, date(2026, 8, 3))

    _, context = runner.calls[0]
    assert context.facts["daily_facts"]["athlete_context"] == {}


def test_report_date_language_normalizer_recurses_without_changing_keys():
    result = _normalize_report_date_language({
        "plan_execution": {"today_actual": "今日跑步，昨日休息"},
        "warnings": ["明天降低强度"],
    }, date(2026, 8, 2))

    assert result == {
        "plan_execution": {"today_actual": "当日跑步，2026-08-01休息"},
        "warnings": ["2026-08-03降低强度"],
    }


def test_daily_insight_forces_plan_execution_not_applicable_without_effective_plan(
    monkeypatch,
):
    _set_generic_ai_environment(monkeypatch)
    runner = RecordingRunner({
        "plan_execution": {
            "today_planned": "后来方案的恢复跑",
            "today_actual": "完成 10km",
            "today_match": "未执行",
            "comparison_status": "applicable",
            "week_completion": "",
            "on_track": False,
            "deviation_note": "",
        },
        "conclusion": "训练完成",
        "observations": [],
        "recommendations": [],
        "warnings": [],
        "plan_adjusted": True,
    })
    monkeypatch.setattr("src.coach._daily_runner", lambda: runner)

    result = get_coach_insight({
        "plan_context": {"status": "no_effective_plan"},
    }, date(2026, 8, 2))

    assert result is not None
    assert result["plan_execution"] == {
        "today_planned": "当日尚无生效计划",
        "today_actual": "完成 10km",
        "today_match": "不适用",
        "comparison_status": "not_applicable",
        "week_completion": "",
        "on_track": None,
        "deviation_note": "报告日期早于首个生效方案，不进行计划执行评价。",
    }
    assert result["plan_adjusted"] is False


def test_daily_insight_receives_structure_classification_with_group_paces(monkeypatch):
    _set_generic_ai_environment(monkeypatch)
    runner = RecordingRunner({
        "plan_execution": {
            "today_planned": "", "today_actual": "", "today_match": "",
            "comparison_status": "not_applicable", "week_completion": "",
            "on_track": None, "deviation_note": "",
        },
        "conclusion": "间歇训练", "observations": [], "recommendations": [],
        "warnings": [], "plan_adjusted": False,
    })
    monkeypatch.setattr("src.coach._daily_runner", lambda: runner)

    get_coach_insight({
        "daily_activities": {"count": 1},
        "recovery": {},
        "plan_context": {"status": "no_effective_plan"},
        "session_analyses": [{
            "activity_name": "间歇跑",
            "structure_classification": {
                "structure_type": "interval", "label": "间歇", "confidence": 1.0,
                "alternations": 4, "composition": [],
                "work_recovery_groups": [
                    {"group": 1, "work_pace_sec_per_km": 224, "work_avg_hr": 147,
                     "recovery_pace_sec_per_km": 304, "recovery_avg_hr": 139},
                    {"group": 2, "work_pace_sec_per_km": 224, "work_avg_hr": 149,
                     "recovery_pace_sec_per_km": 306, "recovery_avg_hr": 141},
                ],
            },
        }],
    }, date(2026, 8, 3))

    _, context = runner.calls[0]
    daily_facts = context.facts["daily_facts"]
    clf = daily_facts["session_analyses"][0]["structure_classification"]
    assert clf["label"] == "间歇"
    assert clf["alternations"] == 4
    assert len(clf["work_recovery_groups"]) == 2
    assert clf["work_recovery_groups"][0]["work_pace_sec_per_km"] == 224
