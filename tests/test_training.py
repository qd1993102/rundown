"""交互式训练方案领域服务测试。"""

from __future__ import annotations

import stat
from datetime import date, timedelta

import pytest

from src.memory import build_memory_file
from src.training import TrainingError, TrainingService
from src.training_planning import ProfessionalSchemePlanner


def _goal(service: TrainingService, **overrides):
    payload = {
        "name": "10K 跑进 45 分钟",
        "distance": "10k",
        "target_time": "00:45:00",
        "target_date": "2026-10-18",
    }
    payload.update(overrides)
    return service.create_goal(payload)


def _draft(service: TrainingService, **overrides):
    goal_id = overrides.pop("goal_id", None)
    if goal_id is None:
        goal_id = _goal(service)["goal_id"]
    payload = {
        "goal_id": goal_id,
        "available_days": [1, 3, 5, 6],
        "max_session_minutes": 100,
    }
    payload.update(overrides)
    return service.create_draft(payload)


def _active(service: TrainingService):
    draft = _draft(service)
    return service.activate(draft["plan_id"])


def _next_training_date(scheme: dict) -> date:
    weekdays = {
        int(item["weekday"])
        for item in scheme["weekly_pattern"]
        if item.get("type") != "rest"
    }
    return date.today() + timedelta(days=min(
        (weekday - date.today().weekday()) % 7 for weekday in weekdays
    ))


def test_update_draft_recalculates_in_place_before_activation(tmp_path):
    service = TrainingService(tmp_path / "memory")
    first_goal = _goal(service)
    second_goal = _goal(service, name="半马完赛", distance="hm")
    draft = _draft(service, goal_id=first_goal["goal_id"])

    updated = service.update_draft(draft["plan_id"], {
        "goal_id": second_goal["goal_id"],
        "available_days": [0, 2, 6],
        "max_session_minutes": 75,
        "reported_weekly_mileage": 36,
        "preferred_terrain": "公路",
    })

    assert updated["plan_id"] == draft["plan_id"]
    assert updated["created_at"] == draft["created_at"]
    assert updated["status"] == "draft"
    assert updated["goal_id"] == second_goal["goal_id"]
    assert updated["goal_snapshot"]["name"] == "半马完赛"
    assert updated["constraints"]["available_days"] == [0, 2, 6]
    assert updated["weekly_mileage_target"] == 36
    assert updated["baseline_snapshot"]["reported_weekly_mileage"] == 36
    assert service.plan() is None


def test_draft_training_summary_uses_previous_complete_weeks_and_recent_days(tmp_path):
    target = date(2026, 8, 9)  # 周日；当前周不得进入长期基线
    previous_monday = target - timedelta(days=target.weekday() + 7)
    activity = {
        "activity_id": "prior-run",
        "activity_type": "running",
        "activity_name": "轻松跑",
        "activity_date": str(previous_monday),
        "distance_meters": 10000,
        "duration_seconds": 3600,
        "training_analysis": {"primary_type": "aerobic", "confidence": 0.9},
    }

    def loader(start, end):
        states = {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }
        return ([activity] if start <= previous_monday <= end else []), states

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    context = service._training_summary_context(target=target)

    assert context["reference_window"]["kind"] == "previous_complete_natural_weeks"
    assert len(context["reference_weeks"]) == 8
    assert context["reference_weeks"][0]["running_distance_km"] == 10.0
    assert all(
        week["window_end"] < str(target - timedelta(days=target.weekday()))
        for week in context["reference_weeks"]
    )
    assert [item["date"] for item in context["recent_days"]] == [
        str(target - timedelta(days=2)), str(target - timedelta(days=1)),
    ]


def test_update_draft_rejects_an_activated_scheme(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)
    service.activate(draft["plan_id"])

    with pytest.raises(TrainingError) as error:
        service.update_draft(draft["plan_id"], {
            "goal_id": draft["goal_id"],
            "available_days": [1, 3, 5],
            "max_session_minutes": 90,
        })

    assert error.value.code == "invalid_plan_state"


def test_create_confirm_and_read_realtime_training_home(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)

    empty = service.home(today=date(2026, 7, 31))
    assert empty["has_active_plan"] is False

    active = service.activate(draft["plan_id"])
    target = _next_training_date(active)
    home = service.home(today=target)

    assert active["status"] == "active"
    assert active["version"] == 1
    assert home["has_active_plan"] is True
    assert home["scheme"]["goal_id"] == draft["goal_id"]
    assert home["scheme"]["goal_snapshot"]["name"] == "10K 跑进 45 分钟"
    assert home["today"]["date"] == str(target)
    assert len(home["week"]["workouts"]) == 7
    assert home["today"]["training_prescription"]["blocks"]
    assert home["today"]["training_prescription"]["structure_version"] == 2
    assert home["today"]["training_prescription"]["steps"]
    assert home["today"]["training_prescription"]["targets"]["feel"]["label"]
    assert home["plan_timeline"]["total_weeks"] == sum(
        phase["weeks"] for phase in home["scheme"]["periodization"]
    )
    assert (tmp_path / "memory/plans/history" / f"{draft['plan_id']}-v1.md").exists()
    assert stat.S_IMODE((tmp_path / "memory/plans/active-plan.md").stat().st_mode) == 0o600


def test_week_progress_exposes_actual_facts_besides_plan_execution(tmp_path):
    """训练页周进度除计划课执行外，并列展示周内实际运动事实
    （全部已同步活动，含计划外/部分完成），口径与报告页 actual_summary 一致。"""
    from datetime import timedelta

    def loader(start, end):
        start = date.fromisoformat(str(start))
        activities = [
            {"activity_id": "run-1", "activity_type": "running", "activity_name": "早跑",
             "activity_date": str(start), "distance_meters": 8000, "duration_seconds": 3000},
            {"activity_id": "ride-1", "activity_type": "cycling", "activity_name": "骑行",
             "activity_date": str(start), "distance_meters": 20000, "duration_seconds": 1800},
            {"activity_id": "run-2", "activity_type": "running", "activity_name": "晚跑",
             "activity_date": str(start + timedelta(days=1)), "distance_meters": 5000,
             "duration_seconds": 1800},
        ]
        states = {
            str(start + timedelta(days=i)): "synced"
            for i in range((end - start).days + 1)
        }
        return activities, states

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    draft = _draft(service)
    active = service.activate(draft["plan_id"])
    home = service.home(today=_next_training_date(active))
    progress = home["week"]["progress"]

    assert "completed_sessions" in progress
    assert "completed_km" in progress
    # 实际量：3 次活动（2 跑 + 1 骑行），跑步距离 8 + 5 = 13 km，骑行不计入跑步距离
    assert progress["actual_sessions"] == 3
    assert progress["actual_running_km"] == 13.0


def test_plan_timeline_keeps_bridge_week_outside_phase_progress(tmp_path):
    service = TrainingService(tmp_path / "memory")
    scheme = {
        "effective_from": "2026-08-04",  # 周二，先进入不计阶段的衔接周
        "activation": {"bridge_week": {"required": True}},
        "periodization": [
            {"name": "基础期", "weeks": 3, "purpose": "建立连续性"},
            {"name": "专项期", "weeks": 2, "purpose": "专项适应"},
        ],
    }

    bridge = service._plan_timeline(scheme, date(2026, 8, 4))
    first_full_week = service._plan_timeline(scheme, date(2026, 8, 10))

    assert bridge["status"] == "bridge"
    assert bridge["planned_week"] == 0
    assert bridge["progress_percent"] == 0
    assert first_full_week["status"] == "scheduled_timeline"
    assert first_full_week["planned_week"] == 1
    assert first_full_week["current_phase"]["name"] == "基础期"


def test_activation_has_effective_lifecycle_and_does_not_reach_back(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)

    active = service.activate(draft["plan_id"])
    previous_day = date.today() - timedelta(days=1)

    assert active["activated_at"]
    assert active["effective_from"] == str(date.today())
    assert active["effective_to"] is None
    assert service.resolve_plan_context(previous_day) == {
        "status": "no_effective_plan",
        "exists": False,
        "target_date": str(previous_day),
        "comparison_status": "not_applicable",
    }
    assert service.home(today=previous_day)["has_active_plan"] is False


def test_draft_is_reference_only_for_historical_daily_report_context(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)

    context = service.resolve_plan_context(date.today())

    assert context["status"] == "draft_available"
    assert context["plan_id"] == draft["plan_id"]
    assert context["comparison_status"] == "not_applicable"
    assert context["reference_only"] is True


def test_activation_preview_can_schedule_and_creates_bridge_week(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)
    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()) or 7)

    scheduled_preview = service.activation_preview(draft["plan_id"], {"start_mode": "next_week"})
    scheduled = service.activate(draft["plan_id"], {"preview_id": scheduled_preview["preview_id"]})

    assert scheduled["status"] == "scheduled"
    assert scheduled["effective_from"] == str(next_monday)
    assert service.resolve_plan_context(date.today())["status"] == "scheduled_plan"

    service = TrainingService(tmp_path / "bridge")
    draft = _draft(service)
    if date.today().weekday() == 0:
        pytest.skip("周一不产生衔接周")
    preview = service.activation_preview(draft["plan_id"], {"start_mode": "today"})
    active = service.activate(draft["plan_id"], {"preview_id": preview["preview_id"]})
    week = service.home(today=date.today())["week"]

    assert active["status"] == "active"
    assert preview["bridge_week"]["required"] is True
    assert week["week_kind"] == "bridge"
    assert week["counts_toward_phase"] is False
    assert all(item["type"] == "rest" for item in week["workouts"][:date.today().weekday()])


def test_capacity_profile_uses_historical_as_ceiling_not_first_week_load(tmp_path):
    today = date.today()
    activities = []
    for offset in (1, 8, 15):
        activities.append({
            "activity_id": f"r{offset}", "activity_type": "running",
            "activity_date": str(today - timedelta(days=offset)), "distance_meters": 8000,
        })

    def loader(start, end):
        return [item for item in activities if start <= date.fromisoformat(item["activity_date"]) <= end], {
            str(today - timedelta(days=index)): "synced" for index in range(28)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    service.update_capacity_facts({"historical_weekly_km": 90, "historical_long_run_km": 32})
    profile = service.capacity_profile()

    assert profile["historical_proven_capacity"]["weekly_km"] == 90
    assert profile["current_sustainable_capacity"]["weekly_km"] < 20
    assert profile["entry_load_envelope"]["maximum_weekly_km"] < 25


def test_recent_two_days_training_is_separate_sequencing_context(tmp_path):
    target = date(2026, 8, 9)
    activities = [
        {
            "activity_id": "long-1", "activity_type": "running",
            "activity_date": "2026-08-08", "activity_name": "周末长距离跑",
            "distance_meters": 30000, "duration_seconds": 10800,
        },
        {
            "activity_id": "old-1", "activity_type": "running",
            "activity_date": "2026-08-06", "activity_name": "间歇跑",
            "distance_meters": 8000, "duration_seconds": 2400,
        },
    ]

    def loader(start, end):
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], {str(day): "synced" for day in (target - timedelta(days=2), target - timedelta(days=1))}

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    short_term = service._recent_two_days_training(target=target)

    assert short_term["status"] == "sufficient"
    assert short_term["window_start"] == "2026-08-07"
    assert short_term["window_end"] == "2026-08-08"
    assert [item["date"] for item in short_term["activities"]] == ["2026-08-08"]
    assert short_term["activities"][0]["is_long"] is True
    assert short_term["constraints"]["avoid_quality_after_heavy"] is True
    assert short_term["constraints"]["prefer_long_run_weekend"] is True


def test_week_review_proposes_acceleration_only_on_current_facts(tmp_path):
    today = date.today()
    activities = []
    for offset in range(0, 28, 3):
        activities.append({
            "activity_id": f"r{offset}", "activity_type": "running",
            "activity_date": str(today - timedelta(days=offset)), "distance_meters": 12000,
        })

    def loader(start, end):
        return [item for item in activities if start <= date.fromisoformat(item["activity_date"]) <= end], {
            str(start + timedelta(days=index)): "synced" for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    active = _active(service)
    review = service.review_week(target=today)

    assert review["plan_id"] == active["plan_id"]
    assert review["progression_decision"]["action"] in {"accelerate", "advance", "hold"}
    assert review["progression_decision"]["facts_cutoff"] == str(today)


def test_week_review_is_read_only_and_weekly_report_is_explicit(tmp_path):
    today = date.today()

    def loader(start, end):
        return [], {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    active = _active(service)

    first = service.review_week(target=today)
    second = service.review_week(target=today)

    assert first["progression_decision"]["week_id"] == second["progression_decision"]["week_id"]
    assert not list((tmp_path / "memory/reports/weekly").glob("*.md"))

    report = service.create_weekly_report(target=today)

    assert report["type"] == "weekly_checkpoint"
    assert report["week_id"] == first["progression_decision"]["week_id"]
    assert report["progression_decision"]["action"] == "not_final"
    assert report["training_url"].startswith("/training?")
    assert service.list_weekly_reports() == []


def test_week_review_without_plan_still_returns_and_archives_actual_week(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)
    activities = [
        {
            "activity_id": "run-1", "activity_type": "running",
            "activity_name": "轻松跑", "activity_date": str(target),
            "distance_meters": 8000, "duration_seconds": 3000,
        },
        {
            "activity_id": "ride-1", "activity_type": "cycling",
            "activity_name": "骑行", "activity_date": str(target + timedelta(days=2)),
            "distance_meters": 12000, "duration_seconds": 2400,
        },
    ]

    def loader(start, end):
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)

    review = service.review_week(target=target)

    assert review["type"] == "training_week_review"
    assert review["plan_context"] is None
    assert review["plan_id"] is None
    assert review["plan_version"] is None
    assert review["plan_execution_summary"] is None
    assert review["adaptation_signal"] is None
    assert review["progression_decision"] is None
    assert review["actual_summary"]["activity_count"] == 2
    assert review["actual_summary"]["active_days"] == 2
    assert review["actual_summary"]["running_days"] == 1
    assert review["actual_summary"]["running_distance_km"] == 8
    assert review["actual_summary"]["total_duration_minutes"] == 90
    assert review["actual_summary"]["longest_activity"]["distance_km"] == 12
    assert review["review_sections"]["overview"]["headline"]
    assert review["review_sections"]["next_week"]["actions"]

    report = service.create_weekly_report(target=target)

    assert report["type"] == "weekly_report"
    assert report["plan_context"] is None
    assert report["progression_decision"] is None
    assert report["actual_summary"] == review["actual_summary"]
    assert service.list_weekly_reports()[0]["week_id"] == report["week_id"]


def test_week_review_prepares_daily_analysis_before_quality_summary(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)
    activities = [{
        "activity_id": "quality-1",
        "activity_type": "running",
        "activity_name": "Intervals",
        "activity_date": str(target + timedelta(days=1)),
        "distance_meters": 10000,
        "duration_seconds": 2700,
        "avg_heart_rate": 164,
        "training_analysis": {
            "display_name": "间歇跑",
            "primary_type": "interval",
            "confidence": 0.88,
        },
    }]

    def loader(start, end):
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    review = service.review_week(target=target, include_ai=False)

    prerequisites = review["daily_prerequisites"]
    assert prerequisites["status"] == "complete"
    assert prerequisites["expected_days"] == 7
    assert prerequisites["prepared_days"] == 7
    assert len(prerequisites["summary_versions"]) == 7
    quality = review["quality_sessions"]
    assert len(quality) == 1
    assert quality[0]["activity_id"] == "quality-1"
    assert quality[0]["quality_type"] == "interval"
    assert review["review_sections"]["quality_sessions"]["items"] == quality
    daily = review["training_day_summary"]["daily_summaries"]
    assert all(item["daily_analysis"]["status"] == "ready" for item in daily)
    assert not (tmp_path / "memory/reports/daily").exists()


def test_current_week_prerequisites_exclude_future_days_and_degrade_unknown_coverage(tmp_path):
    today = date.today()

    def loader(start, end):
        return [], {
            str(start + timedelta(days=index)): "unknown"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    review = service.review_week(target=today, include_ai=False)
    manifest = review["daily_prerequisites"]

    assert manifest["status"] == "degraded"
    assert manifest["expected_days"] == today.weekday() + 1
    assert manifest["prepared_days"] == today.weekday() + 1
    assert all(date.fromisoformat(item["date"]) <= today for item in manifest["day_entries"])
    assert all(item["status"] == "degraded" for item in manifest["day_entries"])


def test_no_plan_week_review_has_complete_structure_trend_and_risk(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)
    activities = [
        {
            "activity_id": "current-long", "activity_type": "running",
            "activity_name": "长距离", "activity_date": str(target),
            "distance_meters": 40000, "duration_seconds": 12600,
            "training_analysis": {
                "display_name": "有氧跑", "primary_type": "aerobic",
                "confidence": 0.82,
            },
        },
        {
            "activity_id": "current-tempo", "activity_type": "running",
            "activity_name": "节奏跑", "activity_date": str(target + timedelta(days=2)),
            "distance_meters": 30000, "duration_seconds": 7200,
            "training_analysis": {
                "display_name": "节奏跑", "primary_type": "tempo",
                "confidence": 0.8,
            },
        },
        {
            "activity_id": "current-ride", "activity_type": "cycling",
            "activity_name": "骑行", "activity_date": str(target + timedelta(days=4)),
            "distance_meters": 10000, "duration_seconds": 2400,
        },
    ]
    for offset, distance_km in ((1, 30), (2, 40), (3, 20), (4, 30)):
        activities.append({
            "activity_id": f"reference-{offset}", "activity_type": "running",
            "activity_name": "历史跑步",
            "activity_date": str(target - timedelta(days=7 * offset)),
            "distance_meters": distance_km * 1000, "duration_seconds": 3600,
        })

    def loader(start, end):
        states = {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }
        states[str(target - timedelta(days=14))] = "unknown"
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], states

    setup_targets = []

    def load_setup(target_date=None):
        setup_targets.append(target_date)
        return {
            "recovery_snapshot": {
                "date": str(target + timedelta(days=6)),
                "recovery": {"overall_score": 45, "level": "cautious"},
                "sleep": {"total_hours": 5.8, "sleep_score": 58},
                "training_load": {"acwr": 1.5, "acwr_status": "overreaching"},
            },
        }

    service = TrainingService(
        tmp_path / "memory", activity_loader=loader,
        setup_context_loader=load_setup,
    )

    review = service.review_week(target=target)

    assert review["trend_summary"]["reference_week_count"] == 3
    assert review["trend_summary"]["comparison_status"] == "sufficient"
    assert review["trend_summary"]["running_km"]["current"] == 70
    assert review["trend_summary"]["running_km"]["delta_percent"] > 100
    assert review["actual_summary"]["training_breakdown"] == [
        {"label": "有氧跑", "sessions": 1, "distance_km": 40.0},
        {"label": "节奏跑", "sessions": 1, "distance_km": 30.0},
    ]
    assert set(review["review_sections"]) == {
        "overview", "quality_sessions", "trend", "recovery_and_risk", "next_week",
    }
    assert review["review_sections"]["recovery_and_risk"]["level"] == "high"
    assert any(
        item["code"] == "volume_increase"
        for item in review["review_sections"]["recovery_and_risk"]["flags"]
    )
    assert len(review["review_sections"]["next_week"]["actions"]) >= 2
    assert target + timedelta(days=6) in setup_targets
    assert review["finding"]["conclusion"] != (
        "本周共记录 3 次运动；当前没有关联训练方案，复盘只解释实际训练与恢复。"
    )


def test_current_week_checkpoint_without_plan_is_not_persisted(tmp_path):
    today = date.today()

    def loader(start, end):
        return [], {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)

    checkpoint = service.create_weekly_report(target=today)

    assert checkpoint["type"] == "weekly_checkpoint"
    assert checkpoint["plan_context"] is None
    assert checkpoint["progression_decision"] is None
    assert checkpoint["finding"]["status"] == "ok"
    assert service.list_weekly_reports() == []


def test_week_review_only_compares_dates_covered_by_partial_week_plan(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)

    def loader(start, end):
        return [], {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    service.repository.save_scheme({
        "type": "training_scheme", "plan_id": "partial-plan", "version": 1,
        "status": "active", "created_at": str(target),
        "activated_at": str(target + timedelta(days=2)),
        "effective_from": str(target + timedelta(days=2)), "effective_to": None,
        "weekly_mileage_target": 35,
        "weekly_pattern": [
            {
                "weekday": weekday, "title": "计划跑", "type": "easy",
                "purpose": "测试", "duration_minutes": 30,
                "distance_km": 5, "intensity": "轻松", "is_key": False,
            }
            for weekday in range(7)
        ],
    })

    review = service.review_week(target=target)

    assert review["plan_context"]["effective_from"] == str(target + timedelta(days=2))
    assert review["plan_execution_summary"]["comparison_start"] == str(
        target + timedelta(days=2)
    )
    assert review["plan_execution_summary"]["planned_sessions"] == 5
    assert review["plan_execution_summary"]["target_km"] == 25


def test_week_review_does_not_associate_completed_plan_after_closure(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)
    service = TrainingService(tmp_path / "memory")
    service.repository.save_scheme({
        "type": "training_scheme", "plan_id": "completed-plan", "version": 1,
        "status": "completed", "created_at": str(target - timedelta(days=21)),
        "effective_from": str(target - timedelta(days=21)),
        "closed_at": str(target - timedelta(days=1)), "effective_to": None,
        "weekly_mileage_target": 35, "weekly_pattern": [],
    })

    review = service.review_week(target=target)

    assert review["plan_context"] is None
    assert review["progression_decision"] is None


def test_resolve_plan_context_uses_historical_scheme_version(tmp_path):
    memory_dir = tmp_path / "memory"
    history = memory_dir / "plans/history"
    history.mkdir(parents=True)
    common = {
        "type": "training_scheme",
        "plan_id": "plan-history",
        "status": "active",
        "created_at": "2026-08-01T09:00:00+08:00",
        "goal_snapshot": {"name": "历史目标"},
        "weekly_mileage_target": 40,
        "weekly_pattern": [{
            "weekday": 6,
            "title": "恢复跑",
            "type": "easy",
            "duration_minutes": 45,
            "distance_km": 8,
            "intensity": "RPE 2",
            "is_key": False,
        }],
        "week_overrides": {},
    }
    version_one = {
        **common,
        "version": 1,
        "activated_at": "2026-08-01T09:00:00+08:00",
        "effective_from": "2026-08-01",
        "effective_to": "2026-08-02",
        "updated_at": "2026-08-01T09:00:00+08:00",
    }
    version_two = {
        **common,
        "version": 2,
        "activated_at": "2026-08-03T09:00:00+08:00",
        "effective_from": "2026-08-03",
        "effective_to": None,
        "updated_at": "2026-08-03T09:00:00+08:00",
    }
    (history / "plan-history-v1.md").write_text(
        build_memory_file(version_one, "历史版本"), encoding="utf-8",
    )
    (history / "plan-history-v2.md").write_text(
        build_memory_file(version_two, "当前版本"), encoding="utf-8",
    )
    (memory_dir / "plans/active-plan.md").write_text(
        build_memory_file(version_two, "当前版本"), encoding="utf-8",
    )

    context = TrainingService(memory_dir).resolve_plan_context(date(2026, 8, 2))

    assert context["status"] == "effective"
    assert context["plan_version"] == 1
    assert context["effective_from"] == "2026-08-01"
    assert context["effective_to"] == "2026-08-02"
    assert context["workout"]["date"] == "2026-08-02"
    assert context["workout"]["title"] == "恢复跑"
    assert TrainingService(memory_dir).home(
        today=date(2026, 8, 2),
    )["scheme"]["version"] == 1


@pytest.mark.parametrize("feedback_type", [
    "time_limited", "fatigue", "pain", "schedule_conflict",
])
def test_four_quick_feedback_types_create_diff_without_mutating_plan(
    tmp_path, feedback_type,
):
    service = TrainingService(tmp_path / feedback_type)
    active = _active(service)
    target = _next_training_date(active)
    payload = {
        "feedback_type": feedback_type,
        "target_date": str(target),
        "available_minutes": 25,
        "pain": {"location": "右膝", "severity": 5, "affects_daily_life": False},
    }

    feedback = service.submit_feedback(payload)
    proposal = service.propose(feedback["feedback_id"])

    assert proposal["status"] == "pending"
    assert proposal["base_version"] == active["version"]
    assert proposal["generation_mode"] == "deterministic_fallback"
    assert proposal["changes"][0]["before"] != proposal["changes"][0]["after"]
    assert service.plan()["version"] == 1
    after = proposal["changes"][0]["after"]
    if feedback_type == "pain":
        assert after["type"] == "rest"
        assert after["distance_km"] == 0
        assert "training_prescription" not in after
        assert "no_intensity_increase" in proposal["risk_flags"]
    elif feedback_type == "schedule_conflict":
        assert after["type"] == "rest"
        assert "training_prescription" not in after
    else:
        prescription = after["training_prescription"]
        assert prescription["structure_version"] == 2
        assert sum(
            block["duration_minutes"] for block in prescription["blocks"]
        ) == after["duration_minutes"]
        if feedback_type == "fatigue":
            assert prescription["intensity_zone"] == 1
            assert prescription["stimuli"] == ["recovery"]


def test_approve_is_versioned_idempotent_and_stale_proposal_conflicts(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)
    feedback_a = service.submit_feedback({
        "feedback_type": "time_limited", "target_date": str(date.today()),
        "available_minutes": 25,
    })
    feedback_b = service.submit_feedback({
        "feedback_type": "fatigue", "target_date": str(date.today() + timedelta(days=1)),
    })
    proposal_a = service.propose(feedback_a["feedback_id"])
    proposal_b = service.propose(feedback_b["feedback_id"])

    first = service.approve(
        proposal_a["proposal_id"], base_version=1, idempotency_key="confirm-a",
    )
    repeated = service.approve(
        proposal_a["proposal_id"], base_version=1, idempotency_key="confirm-a",
    )

    assert first["scheme"]["version"] == 2
    assert first["idempotent"] is False
    assert repeated["scheme"]["version"] == 2
    assert repeated["idempotent"] is True
    with pytest.raises(TrainingError) as exc_info:
        service.approve(
            proposal_b["proposal_id"], base_version=1, idempotency_key="confirm-b",
        )
    assert exc_info.value.code == "version_conflict"
    assert (tmp_path / "memory/plans/history" / f"{first['scheme']['plan_id']}-v2.md").exists()


def test_approve_recovers_if_scheme_saved_before_proposal_status(tmp_path, monkeypatch):
    service = TrainingService(tmp_path / "memory")
    _active(service)
    feedback = service.submit_feedback({
        "feedback_type": "fatigue", "target_date": str(date.today()),
    })
    proposal = service.propose(feedback["feedback_id"])
    original_save = service.repository.save_proposal
    failed = False

    def fail_once(item):
        nonlocal failed
        if item.get("status") == "approved" and not failed:
            failed = True
            raise OSError("simulated proposal write failure")
        original_save(item)

    monkeypatch.setattr(service.repository, "save_proposal", fail_once)
    with pytest.raises(OSError, match="simulated"):
        service.approve(
            proposal["proposal_id"], base_version=1, idempotency_key="recover-1",
        )
    monkeypatch.setattr(service.repository, "save_proposal", original_save)

    recovered = service.approve(
        proposal["proposal_id"], base_version=1, idempotency_key="recover-1",
    )
    assert recovered["idempotent"] is True
    assert recovered["scheme"]["version"] == 2
    assert recovered["proposal"]["status"] == "approved"


def test_rejected_proposal_does_not_change_active_scheme(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)
    feedback = service.submit_feedback({
        "feedback_type": "fatigue", "target_date": str(date.today()),
    })
    proposal = service.propose(feedback["feedback_id"])

    rejected = service.reject(proposal["proposal_id"], reason="保持原计划")

    assert rejected["status"] == "rejected"
    assert service.plan()["version"] == 1


def test_legacy_active_plan_migrates_without_losing_body(tmp_path):
    path = tmp_path / "memory/plans/active-plan.md"
    path.parent.mkdir(parents=True)
    path.write_text(build_memory_file({
        "type": "training_plan",
        "status": "active",
        "target_race": "杭州马拉松",
        "target_date": "2026-11-01",
        "current_phase": "基础期",
        "weekly_mileage_target": 50,
        "weekly_structure": {"周二": "轻松跑 8km", "周日": "长跑 18km"},
    }, "旧方案的完整自由文本说明。"), encoding="utf-8")

    active = TrainingService(tmp_path / "memory").plan()

    assert active["type"] == "training_scheme"
    assert active["version"] == 1
    assert active["legacy_notes"] == "旧方案的完整自由文本说明。"
    assert (tmp_path / "memory/plans/history" / f"{active['plan_id']}-v1.md").exists()


def test_activity_matching_never_turns_missing_sync_into_skipped(tmp_path):
    def loader(start, end):
        return ([{
            "activity_id": "run-1", "activity_date": "2026-07-28",
            "activity_name": "晨跑", "distance_meters": 8000,
            "duration_seconds": 2880,
        }], {"2026-07-30": "unknown"})

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    active = _active(service)
    active["activated_at"] = "2026-07-27T08:00:00+08:00"
    active["effective_from"] = "2026-07-27"
    service.repository.save_scheme(active)
    home = service.home(today=date(2026, 7, 31))

    statuses = {item["date"]: item["execution_status"] for item in home["week"]["workouts"]}
    assert statuses["2026-07-28"] in {"completed", "substituted"}
    assert statuses["2026-07-30"] == "unmatched"
    assert "skipped" not in statuses.values()


def test_home_and_brief_guard_against_impossible_easy_distance_time_pair(tmp_path):
    target = date(2026, 8, 3)

    def loader(start, end):
        activities = [
            {
                "activity_id": f"run-{index}",
                "activity_date": str(target - timedelta(days=index + 1)),
                "activity_name": "轻松跑", "distance_meters": 10000,
                "duration_seconds": 3220,
            }
            for index in range(3)
        ]
        return activities, {str(target): "synced"}

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    active = _active(service)
    active.update({
        "effective_from": str(target),
        "weekly_pattern": [{
            "weekday": target.weekday(), "title": "Easy Run", "type": "easy",
            "purpose": "保持有氧基础", "duration_minutes": 60,
            "distance_km": 13.7, "intensity": "轻松", "is_key": False,
        }],
    })
    service.repository.save_scheme(active)

    home = service.home(today=target)
    guard = home["today"]["training_prescription"]["pacing_guard"]
    brief = service.session_brief(target=target)

    assert guard["status"] == "requires_review"
    assert "60 分钟可对话强度" in brief["finding"]["recommendations"][0]
    assert brief["finding"]["risk_flags"]


def test_home_exposes_dynamic_personal_pace_with_collapsed_reason(tmp_path):
    target = date.today()
    activities = [
        {
            "activity_id": f"easy-{index}",
            "activity_date": str(target - timedelta(days=index + 1)),
            "activity_type": "running", "activity_name": "轻松有氧跑",
            "distance_meters": 10_000, "duration_seconds": pace * 10,
            "training_analysis": {
                "analysis_id": f"analysis-{index}",
                "primary_type": "aerobic", "terrain": "flat",
                "confidence": .86, "features": {},
            },
        }
        for index, pace in enumerate((330, 336, 342))
    ]

    def loader(start, end):
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], {str(target): "synced"}

    service = TrainingService(
        tmp_path / "memory",
        activity_loader=loader,
        setup_context_loader=lambda: {
            "recovery_snapshot": {"recovery": {"overall_score": 52}},
        },
    )
    active = _active(service)
    active.update({
        "effective_from": str(target),
        "weekly_pattern": [{
            "weekday": target.weekday(), "title": "轻松有氧跑", "type": "easy",
            "purpose": "积累有氧", "duration_minutes": 55,
            "distance_km": 9, "intensity": "能完整对话", "is_key": False,
        }],
    })
    service.repository.save_scheme(active)

    home = service.home(today=target)

    profile = home["pace_calibration_profile"]
    guidance = home["today"]["training_prescription"]["pace_guidance"]
    brief = service.session_brief(target=target)
    assert profile["zones"]["z2"]["status"] == "available"
    assert guidance["today_target"]["status"] == "slower"
    assert guidance["today_target"]["display_range"].endswith("/km")
    assert guidance["explanation"]["presentation"] == "collapsed"
    assert guidance["explanation"]["reasons"]
    assert brief["finding"]["pace_result"] == {
        "status": "slower",
        "range": guidance["today_target"]["display_range"],
        "safety_action": None,
    }
    assert brief["finding"]["explanation"]["presentation"] == "collapsed"


def test_home_goal_progression_is_not_blocked_by_acwr_when_state_is_stable(tmp_path):
    target = date.today()
    activities = [
        {
            "activity_id": f"goal-easy-{index}",
            "activity_date": str(target - timedelta(days=index + 1)),
            "activity_type": "running", "activity_name": "轻松有氧跑",
            "distance_meters": 10_000, "duration_seconds": pace * 10,
            "training_analysis": {
                "analysis_id": f"goal-analysis-{index}",
                "primary_type": "aerobic", "terrain": "flat",
                "confidence": .86, "features": {},
            },
        }
        for index, pace in enumerate((330, 336, 342))
    ]

    def loader(start, end):
        return [
            item for item in activities
            if start <= date.fromisoformat(item["activity_date"]) <= end
        ], {str(target): "synced"}

    service = TrainingService(
        tmp_path / "memory",
        activity_loader=loader,
        setup_context_loader=lambda: {
            "recovery_snapshot": {
                "recovery": {"overall_score": 85},
                "training_load": {"acwr_status": "high_risk"},
            },
        },
    )
    active = _active(service)
    active.update({
        "effective_from": str(target),
        "weekly_pattern": [{
            "weekday": target.weekday(), "title": "轻松有氧跑", "type": "easy",
            "purpose": "积累有氧", "duration_minutes": 55,
            "distance_km": 9, "intensity": "能完整对话", "is_key": False,
        }],
    })
    service.repository.save_scheme(active)

    home = service.home(today=target)
    guidance = home["today"]["training_prescription"]["pace_guidance"]

    assert guidance["today_target"]["status"] == "progressed"
    assert guidance["goal_progression"]["applied"] is True


def test_pain_feedback_requires_minimum_safety_details(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)

    with pytest.raises(TrainingError) as exc_info:
        service.submit_feedback({
            "feedback_type": "pain", "target_date": "2026-07-31",
            "pain": {"location": "右膝"},
        })
    assert exc_info.value.code == "pain_details_required"


def test_active_scheme_blocks_new_goal_creation(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)

    with pytest.raises(TrainingError) as exc_info:
        service.create_goal({
            "name": "不应并行的新目标",
            "distance": "10k",
            "target_date": "2026-12-01",
        })

    assert exc_info.value.code == "goal_creation_blocked"


def test_pain_feedback_persists_same_day_guidance_even_if_proposal_rejected(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    target = _next_training_date(active)
    feedback = service.submit_feedback({
        "feedback_type": "pain",
        "target_date": str(target),
        "pain": {"location": "右膝", "severity": 6, "affects_daily_life": False},
    })

    assert feedback["same_day_safety_guidance"]["blocks_intensity"] is True
    proposal = service.propose(feedback["feedback_id"])
    service.reject(proposal["proposal_id"], reason="保持原计划")

    day = next(item for item in service.home(today=target)["week"]["workouts"] if item["date"] == str(target))
    assert day["type"] == "rest"
    assert day["safety_guidance"]["read_only"] is True


def test_long_term_available_days_reuse_feedback_and_start_next_week(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    feedback = service.submit_feedback({
        "feedback_type": "constraint_change",
        "target_date": str(date.today()),
        "new_available_days": [0, 2, 5],
        "note": "长期工作安排变化",
    })

    proposal = service.propose(feedback["feedback_id"])
    assert proposal["scope"] == "scheme"
    assert proposal["effective_from"] == str(
        date.today() + timedelta(days=(7 - date.today().weekday()) or 7)
    )
    approved = service.approve(
        proposal["proposal_id"],
        base_version=active["version"],
        idempotency_key="long-term-days-1",
    )

    assert approved["scheduled"] is True
    assert service.plan()["version"] == active["version"]
    scheduled = service.repository.get_scheduled(active["plan_id"])
    assert scheduled["constraints"]["available_days"] == [0, 2, 5]


def test_cancel_scheduled_activation_returns_same_plan_to_draft(tmp_path):
    service = TrainingService(tmp_path / "memory")
    draft = _draft(service)
    preview = service.activation_preview(draft["plan_id"], {"start_mode": "next_week"})
    service.activate(draft["plan_id"], {"preview_id": preview["preview_id"]})

    returned = service.cancel_scheduled_activation(draft["plan_id"])

    assert returned["plan_id"] == draft["plan_id"]
    assert returned["status"] == "draft"
    assert service.repository.get_scheduled(draft["plan_id"]) is None
    assert service.repository.get_draft(draft["plan_id"])["status"] == "draft"


def test_post_workout_only_allows_explicit_skipped_and_is_idempotent(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    # 反馈接口只允许记录今天或过去的课次；不要依赖测试运行日恰好落在下一次训练日前。
    target = date.today()
    payload = {
        "feedback_type": "post_workout",
        "target_date": str(target),
        "planned_workout_id": f"{active['plan_id']}-{target}",
        "completion_status": "skipped",
        "idempotency_key": "skip-1",
    }

    first = service.submit_feedback(payload)
    repeated = service.submit_feedback(payload)

    assert first["feedback_id"] == repeated["feedback_id"]
    day = next(item for item in service.home(today=target)["week"]["workouts"] if item["date"] == str(target))
    assert day["execution_status"] == "skipped"
    with pytest.raises(TrainingError) as exc_info:
        service.submit_feedback({
            **payload,
            "idempotency_key": "complete-1",
            "completion_status": "completed",
        })
    assert exc_info.value.code == "skipped_confirmation_required"


def test_goal_rescheduling_requires_preview_then_returns_same_plan_to_draft(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    original_goal = service.goals.get(active["goal_id"])
    new_date = date.today() + timedelta(days=120)

    preview = service.preview_goal_rescheduling({"target_date": str(new_date)})

    assert service.plan()["version"] == active["version"]
    assert original_goal["target_date"] != str(new_date)
    confirmed = service.confirm_goal_rescheduling(
        preview["preview_id"], idempotency_key="reschedule-1",
    )

    assert confirmed["scheme"]["plan_id"] == active["plan_id"]
    assert confirmed["scheme"]["version"] == active["version"] + 1
    assert confirmed["scheme"]["status"] == "draft"
    assert service.plan() is None
    assert service.goals.get(active["goal_id"])["target_date"] == str(new_date)
    assert service.repository.get_rescheduling_preview(preview["preview_id"])["status"] == "confirmed"


def test_identifiers_cannot_escape_user_training_directory(tmp_path):
    service = TrainingService(tmp_path / "memory")

    with pytest.raises(TrainingError) as exc_info:
        service.activate("../../other-user")
    assert exc_info.value.code == "invalid_identifier"


def test_draft_rejects_mileage_that_constraints_cannot_safely_hold(tmp_path):
    today = date.today()

    def loader(start, end):
        return ([{
            "activity_id": f"pace-{index}",
            "activity_date": str(today - timedelta(days=index + 1)),
            "activity_name": "轻松跑", "distance_meters": 10000,
            "duration_seconds": 3000,
        } for index in range(3)], {})

    service = TrainingService(
        tmp_path / "memory",
        activity_loader=loader,
        setup_context_loader=lambda: {
            "baseline": {
                "coverage": "sufficient", "window_days": 28,
                "activity_count": 16, "distance_km": 160,
                "longest_distance_km": 24,
            },
        },
    )

    draft = _draft(service, available_days=[6], max_session_minutes=60)

    assert draft["weekly_mileage_target"] == 12
    assert "可训练时间上限" in draft["data_basis"][-1]


def test_setup_reuses_shared_goal_and_suggests_load_from_previous_week_baseline(tmp_path):
    service = TrainingService(
        tmp_path / "memory",
        setup_context_loader=lambda: {
                "baseline": {
                    "coverage": "sufficient", "window_days": 28,
                    "activity_count": 12, "distance_km": 120,
                    "longest_distance_km": 18, "previous_week_km": 30,
            },
            "known_constraints": {"preferred_terrain": "公路"},
        },
    )
    goal = _goal(service)

    home = service.home(today=date(2026, 7, 31))
    draft = service.create_draft({
        "goal_id": goal["goal_id"],
        "available_days": [1, 3, 5, 6],
        "max_session_minutes": 90,
        "preferred_terrain": "公路",
    })

    assert home["setup"]["active_goals"] == [goal]
    assert home["setup"]["baseline"]["distance_km"] == 120
    assert draft["goal_id"] == goal["goal_id"]
    assert draft["goal_snapshot"]["target_time"] == "00:45:00"
    assert draft["weekly_mileage_target"] == 30
    assert draft["generation_mode"] == "deterministic_fallback"
    assert draft["inference_source"] == "deterministic"
    assert draft["validation_status"] == "fallback"
    assert draft["decision_status"] == "deterministic_fallback"
    assert len(draft["first_four_weeks"]) == 4
    assert draft["constraints"]["preferred_terrain"] == "公路"
    assert draft["entry_phase_recommendation"]["name"] == draft["current_phase"]["name"]
    assert draft["entry_phase_recommendation"]["confirmed"] is False
    assert draft["recommended_start_date"] == str(date.today())
    assert len(draft["near_term_schedule"]) == 2
    current_monday = date.today() - timedelta(days=date.today().weekday())
    for projected_week in draft["near_term_schedule"]:
        week_start = date.fromisoformat(projected_week["week_start"])
        assert week_start == current_monday + timedelta(
            days=(int(projected_week["week"]) - 1) * 7,
        )
        assert projected_week["week_end"] == str(week_start + timedelta(days=6))
        for workout in projected_week["workouts"]:
            expected_date = week_start + timedelta(days=int(workout["weekday"]))
            assert workout["date"] == str(expected_date)
            assert workout["weekday_name"] == ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[expected_date.weekday()]
    assert all(
        item["type"] == "rest" or item.get("training_prescription", {}).get("structure_version") == 2
        for week in draft["near_term_schedule"]
        for item in week["workouts"]
    )
    assert all(
        item["type"] == "rest" or "pace_guidance" in item["training_prescription"]
        for item in draft["weekly_pattern"]
    )
    assert "goal" not in draft


def test_create_draft_rejects_unknown_goal_instead_of_copying_goal_fields(tmp_path):
    service = TrainingService(tmp_path / "memory")

    with pytest.raises(TrainingError) as exc_info:
        service.create_draft({
            "goal_id": "goal-does-not-exist",
            "goal_name": "不能在方案请求里偷偷创建",
            "available_days": [1, 3, 5],
            "max_session_minutes": 90,
        })

    assert exc_info.value.code == "goal_not_found"
    assert exc_info.value.status_code == 404


def test_partial_coverage_uses_previous_week_instead_of_dynamic_volume(tmp_path):
    service = TrainingService(
        tmp_path / "memory",
        setup_context_loader=lambda: {
            "baseline": {
                "coverage": "partial", "window_days": 28,
                "activity_count": 28, "distance_km": 168.9,
                "longest_distance_km": 20, "average_weekly_km": 42.2,
                "previous_week_km": 42.2,
                "recent_7d_km": 59.3,
            },
        },
    )
    goal = _goal(service)

    draft = service.create_draft({
        "goal_id": goal["goal_id"], "available_days": list(range(7)),
        "max_session_minutes": 180,
    })

    assert draft["weekly_mileage_target"] == 42.2
    assert "上一完整自然周实际跑量 42.2 km" in draft["data_basis"]
    assert all("长期周跑量方向" not in item for item in draft["data_basis"])


def test_training_goal_does_not_persist_legacy_weekly_mileage(tmp_path):
    service = TrainingService(tmp_path / "memory")

    goal = _goal(service, weekly_km=120)

    assert "weekly_mileage_km" not in goal
    stored = (tmp_path / "memory/goals/active" / f"{goal['goal_id']}.md").read_text()
    assert "weekly_mileage_km" not in stored


def test_training_goal_intent_is_explicit_and_legacy_goals_are_inferred(tmp_path):
    service = TrainingService(tmp_path / "memory")

    finish = service.create_goal({
        "name": "半马完赛", "distance": "hm", "target_date": "2026-10-18",
        "goal_intent": "completion",
    })
    assert finish["goal_intent"] == "completion"

    performance_service = TrainingService(tmp_path / "performance-memory")
    performance = performance_service.create_goal({
        "name": "10K 突破", "distance": "10k", "target_time": "00:45:00",
        "target_date": "2026-10-18", "goal_intent": "performance",
    })
    assert performance["goal_intent"] == "performance"

    with pytest.raises(TrainingError) as error:
        performance_service.update_goal(performance["goal_id"], {
            "goal_intent": "performance", "target_time": "",
        })
    assert error.value.code == "performance_target_required"

    legacy_path = tmp_path / "legacy-memory/goals/active/legacy.md"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(build_memory_file({
        "type": "goal", "id": "legacy", "title": "旧目标", "distance": "10k",
        "target_time": "00:50:00", "status": "active",
    }, "# 旧目标"), encoding="utf-8")
    legacy = TrainingService(tmp_path / "legacy-memory").list_goals()[0]
    assert legacy["goal_intent"] == "performance"


def test_user_confirmed_weekly_volume_has_priority_over_inferred_baseline(tmp_path):
    service = TrainingService(
        tmp_path / "memory",
        setup_context_loader=lambda: {
            "baseline": {
                "coverage": "partial", "window_days": 28,
                "activity_count": 28, "distance_km": 168.9,
                "longest_distance_km": 20, "average_weekly_km": 42.2,
                "recent_7d_km": 59.3,
            },
        },
    )
    goal = _goal(service, weekly_km=120)

    draft = service.create_draft({
        "goal_id": goal["goal_id"], "available_days": list(range(7)),
        "max_session_minutes": 180, "reported_weekly_mileage": 80,
    })

    assert draft["weekly_mileage_target"] == 80
    assert draft["baseline_snapshot"]["reported_weekly_mileage"] == 80
    assert "用户确认当前通常周跑量 80 km" in draft["data_basis"][0]




def test_race_scheme_requires_an_event_date(tmp_path):
    service = TrainingService(tmp_path / "memory")
    goal = _goal(service, target_date="")

    with pytest.raises(TrainingError) as exc_info:
        _draft(service, goal_id=goal["goal_id"])

    assert exc_info.value.code == "race_date_required"


def test_race_scheme_rejects_a_past_event_date(tmp_path):
    service = TrainingService(tmp_path / "memory")
    goal = _goal(service, target_date=str(date.today() - timedelta(days=1)))

    with pytest.raises(TrainingError) as exc_info:
        _draft(service, goal_id=goal["goal_id"])

    assert exc_info.value.code == "race_date_passed"



def test_session_brief_explains_execution_without_modifying_scheme(tmp_path):
    class ExplodingRunner:
        def run(self, *args, **kwargs):
            raise AssertionError("训练前说明不应等待在线 Skill")

    service = TrainingService(
        tmp_path / "memory",
        setup_context_loader=lambda: {
            "recovery_snapshot": {
                "recovery": {"overall_score": 70},
                "training_load": {"acwr": 2.04, "acwr_status": "high_risk"},
            },
        },
    )
    today_weekday = date.today().weekday()
    draft = _draft(service, available_days=sorted({
        today_weekday, (today_weekday + 2) % 7, (today_weekday + 4) % 7,
    }))
    active = service.activate(draft["plan_id"])
    service.scheme_planner.runner = ExplodingRunner()

    brief = service.session_brief(target=date.today())

    assert brief["type"] == "training_session_brief"
    assert brief["generation_mode"] == "deterministic"
    assert brief["finding"]["recommendations"]
    assert any("热身" in item or "组" in item for item in brief["finding"]["recommendations"])
    assert brief["finding"]["pace_result"]["status"] in {
        "unchanged", "slower", "feel_only", "not_applicable",
    }
    assert brief["finding"]["explanation"].get("presentation", "collapsed") == "collapsed"
    assert "evidence" not in brief["finding"]
    assert "trace" not in brief
    assert "fallback_reason" not in brief
    assert all("ACWR" not in item and "high_risk" not in item for item in brief["finding"]["recommendations"])
    assert any("明显疲劳" in item for item in brief["finding"]["recommendations"])
    assert service.plan()["version"] == active["version"]


def test_week_review_returns_execution_summary_and_adaptation_signal(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)

    review = service.review_week(target=date.today())

    assert review["type"] == "training_week_review"
    assert review["execution_summary"]["week_start"]
    assert review["adaptation_signal"]["recommendation"] in {
        "keep", "local_adjustment", "scheme_revision", "insufficient_data",
    }
    assert review["finding"]["status"] in {"ok", "insufficient_data"}


def test_week_review_with_plan_uses_one_primary_ai_call(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)
    calls = []

    class RecordingRunner:
        def run(self, skill_name, context):
            calls.append(skill_name)
            return {
                "skill": skill_name,
                "status": "ok",
                "conclusion": "结构化周事实已解释。",
                "evidence": [],
                "uncertainties": [],
                "risk_flags": [],
                "recommendations": [],
            }, context

    service.scheme_planner.runner = RecordingRunner()

    review = service.review_week(target=date.today())

    assert review["plan_context"]["status"] == "effective"
    assert calls == ["review-training-week"]
    assert review["plan_review"] is None


def test_completed_plan_week_uses_unified_plan_review_once(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    past_week = date.today() - timedelta(days=date.today().weekday() + 7)
    active["effective_from"] = str(past_week)
    service.repository.save_scheme(active)
    calls = []

    class RecordingRunner:
        def run(self, skill_name, context):
            calls.append(skill_name)
            assert skill_name == "review-training-plan"
            return {
                "skill": skill_name,
                "status": "ok",
                "conclusion": "本周执行稳定，建议保持当前阶段。",
                "evidence": ["数据覆盖完整"],
                "uncertainties": [], "risk_flags": [], "recommendations": [],
                "review_stage": "execution_review", "decision": "hold",
                "scope": "next_week", "recommended_entry_phase": None,
                "confidence": 0.8, "adjustments": [], "safety_hold": None,
                "user_explanation": "先保持当前安排。",
                "handoff": {"status": "review_pending", "requires_user_confirmation": True},
            }, context

    service.scheme_planner.runner = RecordingRunner()
    review = service.review_week(target=past_week)

    assert calls == ["review-training-plan"]
    assert review["plan_review"]["decision"] == "hold"
    assert review["plan_review"]["handoff"]["requires_user_confirmation"] is True
    assert "plan_review" not in review["finding"]


def test_race_strategy_is_available_only_inside_strategy_window(tmp_path):
    service = TrainingService(tmp_path / "memory")
    goal = _goal(
        service,
        name="两周后 10K",
        target_date=str(date.today() + timedelta(days=14)),
    )
    draft = _draft(service, goal_id=goal["goal_id"])
    service.activate(draft["plan_id"])

    strategy = service.race_strategy({"course": "城市公路"})

    assert strategy["type"] == "race_strategy"
    assert strategy["generation_mode"] == "deterministic_fallback"
    assert "天气信息未知" in strategy["finding"]["uncertainties"]


def test_race_strategy_rejects_requests_that_are_too_early(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)

    with pytest.raises(TrainingError) as exc_info:
        service.race_strategy({})

    assert exc_info.value.code == "race_strategy_not_available"


def test_active_scheme_structure_excludes_stale_conclusions():
    """重规划注入当前方案只保留结构，剔除旧 data_basis/feasibility 等结论字段：
    AI 不得复述旧结论（如旧的长距离能力），能力事实以重规划时刻实时计算为准。"""
    from src.training import _active_scheme_structure
    scheme = {
        "plan_id": "p1", "version": 3, "goal_id": "g1",
        "goal_snapshot": {"name": "全马", "target_date": "2026-10-18"},
        "constraints": {"available_days": [1, 3, 5]},
        "periodization": [{"name": "基础期", "weeks": 4}],
        "weekly_pattern": [{"weekday": 1, "title": "轻松跑"}],
        "first_four_weeks": [{"week": 1, "target_km": 50}],
        "load_progression": [{"week": 1, "target_km": 50}],
        "weekly_mileage_target": 50,
        "current_phase": {"name": "基础期"},
        "data_basis": ["旧结论：长距离最长仅 20km"],
        "feasibility": {"summary": "旧可行性"},
        "review": {"items": []},
        "adjustments": ["旧调整"],
        "audit": {},
        "planning_trace": [],
        "risk_flags": [],
        "baseline_snapshot": {"longest_distance_km": 20},
        "user_explanation": "旧解释",
    }
    structure = _active_scheme_structure(scheme)
    for key in ("data_basis", "feasibility", "review", "adjustments", "audit",
                "planning_trace", "risk_flags", "baseline_snapshot", "user_explanation"):
        assert key not in structure, key
    for key in ("plan_id", "version", "goal_snapshot", "periodization",
                "weekly_pattern", "first_four_weeks", "weekly_mileage_target"):
        assert key in structure, key


def test_scheme_revision_race_rescheduled_updates_goal_date_on_approve(tmp_path):
    """赛事延期重规划：候选携带新目标日期，确认后同步 goals 记录（避免下次重规划回旧日期）。"""
    from datetime import date, timedelta

    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    old_goal = service.goals.list_active()[0]
    new_date = str(date.today() + timedelta(days=60))

    proposal = service.propose_scheme_revision({
        "trigger": "race_rescheduled",
        "reason": "赛事改期",
        "new_target_date": new_date,
        "constraints": {"available_days": [1, 3, 5], "max_session_minutes": 90},
    })

    assert proposal["proposed_scheme"]["goal_snapshot"]["target_date"] == new_date
    # 确认前：goals 记录仍是旧日期（生效方案目标变化走预览/提案确认路径）
    assert service.goals.list_active()[0]["target_date"] == old_goal["target_date"]

    approved = service.approve(
        proposal["proposal_id"],
        base_version=active["version"],
        idempotency_key="race-rescheduled-1",
    )
    # 确认后：新方案目标日期 + goals 记录同步
    assert approved["scheme"]["goal_snapshot"]["target_date"] == new_date
    assert service.goals.list_active()[0]["target_date"] == new_date


def test_close_active_scheme_ends_today_and_clears_pending(tmp_path):
    """作废方案当天即结束（resolve 不再选中），并清理该方案未决提案。"""
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    proposal = service.propose_scheme_revision({
        "trigger": "execution_deviation", "reason": "待作废前生成提案",
        "constraints": {"available_days": [1, 3, 5], "max_session_minutes": 90},
    })

    closed = service.close_active_scheme(reason="测试作废")

    assert closed["status"] == "completed"
    assert closed["effective_to"] == str(date.today() - timedelta(days=1))
    assert service.home(today=date.today())["has_active_plan"] is False
    assert service.plan() is None
    # 未决提案被 superseded，不再作为待确认展示
    archived = service.repository.get_proposal(proposal["proposal_id"])
    assert archived["status"] == "superseded"


def test_repeated_scheme_revision_supersedes_older_pending(tmp_path):
    """连续多次重规划只保留最新一个待确认提案，旧 pending 标记 superseded。"""
    service = TrainingService(tmp_path / "memory")
    active = _active(service)

    first = service.propose_scheme_revision({
        "trigger": "execution_deviation", "reason": "第一次重规划",
        "constraints": {"available_days": [1, 3, 5], "max_session_minutes": 90},
    })
    second = service.propose_scheme_revision({
        "trigger": "execution_deviation", "reason": "第二次重规划",
        "constraints": {"available_days": [1, 4, 6], "max_session_minutes": 80},
    })

    pendings = service.repository.pending_proposals(active["plan_id"])
    assert [p["proposal_id"] for p in pendings] == [second["proposal_id"]]
    archived = service.repository.get_proposal(first["proposal_id"])
    assert archived["status"] == "superseded"
    assert archived.get("superseded_at")
    # 确认最新提案正常（版本按 active 推进）
    approved = service.approve(
        second["proposal_id"], base_version=active["version"],
        idempotency_key="scheme-supersede-1",
    )
    assert approved["scheme"]["version"] == active["version"] + 1


def test_scheme_revision_race_rescheduled_rejects_past_or_missing_date(tmp_path):
    from datetime import date, timedelta

    service = TrainingService(tmp_path / "memory")
    _active(service)

    with pytest.raises(TrainingError) as past:
        service.propose_scheme_revision({
            "trigger": "race_rescheduled", "reason": "赛事延期",
            "new_target_date": str(date.today() - timedelta(days=1)),
        })
    assert past.value.code == "invalid_race_date"

    with pytest.raises(TrainingError) as missing:
        service.propose_scheme_revision({
            "trigger": "race_rescheduled", "reason": "赛事延期",
            "new_target_date": "not-a-date",
        })
    assert missing.value.code == "invalid_race_date"


def test_scheme_revision_is_a_confirmed_new_version_and_preserves_active_plan(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)

    proposal = service.propose_scheme_revision({
        "trigger": "execution_deviation",
        "reason": "连续两周只能完成两次训练",
        "constraints": {
            "available_days": [1, 4, 6],
            "max_session_minutes": 80,
        },
    })

    assert proposal["scope"] == "scheme"
    assert proposal["status"] == "pending"
    assert proposal["proposed_scheme"]["constraints"]["available_days"] == [1, 4, 6]
    assert service.plan()["version"] == active["version"]

    approved = service.approve(
        proposal["proposal_id"],
        base_version=active["version"],
        idempotency_key="scheme-revision-1",
    )

    assert approved["scheme"]["version"] == active["version"] + 1
    assert approved["scheme"]["last_adjustment"]["scope"] == "scheme"
    assert approved["scheme"]["constraints"]["available_days"] == [1, 4, 6]
    assert proposal["plan_id"] == approved["scheme"]["plan_id"]


def test_weekly_report_recommendation_becomes_pending_training_proposal(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    week_start = date.today() - timedelta(days=date.today().weekday() + 7)
    week_id = week_start.strftime("%G-W%V")
    service.repository.save_weekly_report({
        "type": "weekly_report",
        "report_id": f"weekly-{week_start}",
        "week_id": week_id,
        "week_start": str(week_start),
        "week_end": str(week_start + timedelta(days=6)),
        "plan_id": active["plan_id"],
        "plan_version": active["version"],
        "progression_decision": {
            "decision_id": "decision-1", "action": "deload",
            "rationale": "恢复表现下降，下一周先降载",
        },
        "adaptation_signal": {"recommendation": "scheme_revision"},
        "execution_summary": {"completed_sessions": 1},
    })

    proposal = service.propose_from_weekly_report(week_id)

    assert proposal["status"] == "pending"
    assert proposal["trigger"] == "review_recommendation"
    assert proposal["source_report_id"] == f"weekly-{week_start}"
    assert proposal["source_week_id"] == week_id
    assert proposal["effective_from"] == str(week_start + timedelta(days=7))
    assert service.plan()["version"] == active["version"]


def test_weekly_report_without_adjustment_does_not_create_training_proposal(tmp_path):
    service = TrainingService(tmp_path / "memory")
    active = _active(service)
    week_start = date.today() - timedelta(days=date.today().weekday() + 7)
    week_id = week_start.strftime("%G-W%V")
    service.repository.save_weekly_report({
        "type": "weekly_report", "report_id": f"weekly-{week_start}",
        "week_id": week_id, "week_start": str(week_start),
        "week_end": str(week_start + timedelta(days=6)),
        "plan_id": active["plan_id"], "plan_version": active["version"],
        "progression_decision": {"action": "advance"},
        "adaptation_signal": {"recommendation": "keep"},
    })

    with pytest.raises(TrainingError) as error:
        service.propose_from_weekly_report(week_id)
    assert error.value.code == "report_adjustment_not_required"
    assert not service.repository.pending_proposals(active["plan_id"])


def test_scheme_revision_uses_local_week_facts_and_one_primary_ai_call(tmp_path):
    service = TrainingService(tmp_path / "memory")
    _active(service)
    calls = []

    class RecordingRevisionRunner:
        def run(self, skill_name, context):
            calls.append(skill_name)
            candidate = ProfessionalSchemePlanner()._fallback(
                context.facts["planning_fact_pack"],
                context.facts["training_load_envelope"],
                reason="fixture",
            )
            candidate.pop("generation_mode", None)
            candidate.pop("fallback_reason", None)
            candidate.pop("planning_trace", None)
            return candidate, context.with_trace({
                "skill": skill_name, "version": "test",
            })

    service.scheme_planner.runner = RecordingRevisionRunner()

    proposal = service.propose_scheme_revision({
        "trigger": "execution_deviation",
        "reason": "连续两周训练时间不足",
    })

    assert proposal["generation_mode"] == "skill"
    assert calls == ["revise-training-scheme"]


def test_draft_near_term_workouts_carry_pace_targets_and_weather_slowdown():
    """草稿投影为每堂非休息课回填 pace_targets；极端天气触发保守放慢。"""
    from src.training import _project_draft_near_term_schedule
    from src.training_pace import PaceCalibrationProfileBuilder

    target = date(2026, 8, 4)
    profile = PaceCalibrationProfileBuilder().build(
        [
            {
                "activity_id": f"activity-{index}",
                "activity_type": "running", "activity_name": "有氧跑",
                "activity_date": str(target - timedelta(days=index + 1)),
                "distance_meters": 10_000, "duration_seconds": pace * 10,
                "training_analysis": {
                    "analysis_id": f"analysis-{index}",
                    "primary_type": "aerobic", "terrain": "flat",
                    "confidence": 0.86, "features": {},
                },
            }
            for index, pace in enumerate((330, 336, 342))
        ],
        target=target,
    )

    def week(number):
        return {
            "week": number, "target_km": 40, "target_load": 180,
            "focus": "基础期", "recovery_week": False,
            "workouts": [
                {
                    "weekday": 0, "title": "轻松跑", "type": "easy",
                    "purpose": "恢复", "duration_minutes": 50,
                    "intensity": "Z2", "stimuli": ["aerobic"],
                    "intensity_intent": {"zone": 2, "preferred_metric": "feel",
                                         "fallback_feel": "能完整对话，结束仍有余量"},
                    "is_key": False,
                },
            ],
        }

    projected = _project_draft_near_term_schedule(
        [week(1), week(2)], anchor=target, pace_profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
    )
    workout = projected[0]["workouts"][0]
    targets = workout["pace_targets"]
    assert targets["status"] in {"available", "slower", "progressed", "unchanged"}
    assert targets["zone"] == 2
    assert targets["min_sec_per_km"] and targets["max_sec_per_km"]
    assert targets["display_range"]
    assert targets["basis"]
    assert targets["feel_fallback"] is None

    hot = _project_draft_near_term_schedule(
        [week(1), week(2)], anchor=target, pace_profile=profile,
        recovery_snapshot={"recovery": {"overall_score": 90}},
        weather_context="本周持续高温，注意补水",
    )[0]["workouts"][0]["pace_targets"]
    assert hot["min_sec_per_km"] > targets["min_sec_per_km"]

    no_profile = _project_draft_near_term_schedule(
        [week(1), week(2)], anchor=target, pace_profile=None,
    )[0]["workouts"][0]["pace_targets"]
    assert no_profile["status"] == "unavailable"
    assert no_profile["min_sec_per_km"] is None
    assert no_profile["feel_fallback"]


def test_create_draft_supersedes_old_drafts(tmp_path):
    """新草稿生成时自动归档旧 draft，避免草稿无限堆积。"""
    service = TrainingService(tmp_path / "memory")
    goal = _goal(service)

    first = _draft(service, goal_id=goal["goal_id"])
    assert first["status"] == "draft"
    second = _draft(service, goal_id=goal["goal_id"])

    drafts = service.repository.list_drafts()
    active = [d for d in drafts if d.get("status") == "draft"]
    superseded = [d for d in drafts if d.get("status") == "superseded"]
    assert [d["plan_id"] for d in active] == [second["plan_id"]]
    assert [d["plan_id"] for d in superseded] == [first["plan_id"]]
    assert superseded[0]["superseded_by"] == second["plan_id"]
    # 历史文件保留可回溯
    assert service.repository.get_draft(first["plan_id"])["status"] == "superseded"


def test_projection_forces_rest_on_untrainable_weekday():
    """课程排在不可训练日（available_days 外或 fixed_unavailable）时确定性转为休息。"""
    from datetime import date
    from src.training import _project_draft_near_term_schedule

    weeks = [{"week": 1, "workouts": [
        {"weekday": 4, "type": "easy", "distance_km": 10, "title": "轻松跑"},  # 周五不可训练
        {"weekday": 0, "type": "easy", "distance_km": 8, "title": "轻松跑"},  # 周一可训练
    ]}]
    projected = _project_draft_near_term_schedule(
        weeks, anchor=date(2026, 8, 10),
        constraints={"available_days": [0, 1, 2, 3, 5, 6]},
    )
    by_day = {w["weekday"]: w for w in projected[0]["workouts"]}
    assert by_day[4]["type"] == "rest"   # 周五转休息
    assert by_day[0]["type"] == "easy"   # 周一保留

    # fixed_unavailable_days 也强制
    weeks2 = [{"week": 1, "workouts": [
        {"weekday": 2, "type": "easy", "distance_km": 8, "title": "轻松跑"},
    ]}]
    projected2 = _project_draft_near_term_schedule(
        weeks2, anchor=date(2026, 8, 10),
        constraints={"available_days": [0, 1, 2, 3, 4, 5, 6],
                     "fixed_unavailable_days": [2]},
    )
    assert projected2[0]["workouts"][0]["type"] == "rest"

    # 无约束时不限制（兼容旧草稿）
    projected3 = _project_draft_near_term_schedule(
        weeks, anchor=date(2026, 8, 10), constraints={},
    )
    assert projected3[0]["workouts"][0]["type"] == "easy"


def test_weekly_pattern_forces_rest_on_untrainable_day():
    """生效方案 weekly_pattern 也不得包含不可训练日课程。"""
    from datetime import date
    from src.training import TrainingService

    service = TrainingService("")  # 仅用静态方法
    pattern = service._planning_week_pattern(
        [
            {"weekday": 4, "type": "easy", "distance_km": 10, "title": "轻松跑"},
            {"weekday": 0, "type": "easy", "distance_km": 8, "title": "轻松跑"},
        ],
        [0, 1, 2, 3, 5, 6],
        constraints={"available_days": [0, 1, 2, 3, 5, 6]},
    )
    by_day = {p["weekday"]: p for p in pattern}
    assert by_day[4]["type"] == "rest"  # 周五（不可训练）→ 休息
    assert by_day[0]["type"] == "easy"  # 周一保留


def test_draft_training_summary_compacts_session_summary_for_planning(tmp_path):
    from src.training import TrainingService

    target = date(2026, 8, 9)  # 周日
    activity_date = target - timedelta(days=1)
    activity = {
        "activity_id": "prior-run",
        "activity_type": "running",
        "activity_name": "轻松跑",
        "activity_date": str(activity_date),
        "distance_meters": 10000,
        "duration_seconds": 3600,
        "training_analysis": {"primary_type": "aerobic", "confidence": 0.9},
        "session_summary": {
            "granularity": "L1",
            "volume": {"duration_s": 3600, "distance_m": 10000},
            "effect": {"aerobic_training_effect": 3.0, "label": "AEROBIC_BASE", "estimated": False},
            "intensity": {"basis": "hr", "hr_bands_pct": {"130-145": 80.0}, "pace_bands_pct": {}},
            "pace_profile": {"avg_pace_sec_per_km": 360, "cv_pct": 5.0, "p50": 360},
            "structure_profile": {"composite_type": "steady", "work_blocks": 0, "split_types": []},
            "elevation_profile": {"ascent_m": 20},
        },
    }

    def loader(start, end):
        states = {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }
        return ([activity] if start <= activity_date <= end else []), states

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    context = service._training_summary_context(target=target)

    recent_day = [item for item in context["recent_days"] if item["date"] == str(activity_date)][0]
    session_summary = recent_day["sessions"][0]["session_summary"]
    # 消费侧压缩：保留规划概要，去掉带分布/海拔/结构详情
    assert session_summary["effect"]["aerobic_training_effect"] == 3.0
    assert session_summary["structure_profile"]["composite_type"] == "steady"
    assert session_summary["pace_profile"]["avg_pace_sec_per_km"] == 360
    assert "hr_bands_pct" not in (session_summary.get("intensity") or {})
    assert "elevation_profile" not in session_summary
    assert "split_types" not in session_summary.get("structure_profile", {})
    # reference_weeks 是聚合统计，不含逐课 session_summary
    assert all(
        "session_summary" not in (day.get("sessions") or [{}])[0]
        for week in context["reference_weeks"]
        for day in [week]
    )


def test_draft_training_summary_includes_structure_classification(tmp_path):
    from src.training import TrainingService

    target = date(2026, 8, 9)
    activity_date = target - timedelta(days=1)
    # 构造带 2 组快慢交替的 session_summary（含 segment_sequence）
    activity = {
        "activity_id": "interval-run",
        "activity_type": "running",
        "activity_name": "间歇跑",
        "activity_date": str(activity_date),
        "distance_meters": 10000,
        "duration_seconds": 3000,
        "training_analysis": {"primary_type": "interval", "confidence": 0.9},
        "session_summary": {
            "granularity": "L1",
            "volume": {"duration_s": 3000, "distance_m": 10000},
            "effect": {"aerobic_training_effect": 3.5},
            "intensity": {"basis": "hr"},
            "segment_sequence": [
                {"pace_sec_per_km": 300, "avg_hr": 140, "avg_cadence": 175, "stride_length_cm": 120, "split_type": "run"},
                {"pace_sec_per_km": 240, "avg_hr": 160, "avg_cadence": 185, "stride_length_cm": 143, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 145, "avg_cadence": 173, "stride_length_cm": 112, "split_type": "RECOVERY"},
                {"pace_sec_per_km": 240, "avg_hr": 162, "avg_cadence": 186, "stride_length_cm": 144, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 146, "avg_cadence": 173, "stride_length_cm": 112, "split_type": "RECOVERY"},
                {"pace_sec_per_km": 300, "avg_hr": 140, "avg_cadence": 175, "stride_length_cm": 120, "split_type": "cooldown"},
            ],
        },
    }

    def loader(start, end):
        states = {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }
        return ([activity] if start <= activity_date <= end else []), states

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    context = service._training_summary_context(target=target)

    recent_day = [item for item in context["recent_days"] if item["date"] == str(activity_date)][0]
    session = recent_day["sessions"][0]
    # 压缩保留序列 + 结构判定
    assert session["session_summary"]["segment_sequence"]
    classification = session.get("structure_classification")
    assert classification is not None
    assert classification["alternations"] >= 2
    assert classification["structure_type"] in ("interval", "fartlek")
    assert classification["label"] in ("间歇", "变速")


def test_week_quality_session_keeps_intensity_but_gaps_unreliable_segment_quantity(tmp_path):
    target = date.today() - timedelta(days=date.today().weekday() + 7)
    activity = {
        "activity_id": "unreliable-interval",
        "activity_type": "running",
        "activity_name": "Intervals",
        "activity_date": str(target + timedelta(days=1)),
        "distance_meters": 10000,
        "duration_seconds": 3000,
        "avg_heart_rate": 158,
        "training_analysis": {"primary_type": "interval", "confidence": 0.9},
        "session_summary": {
            "granularity": "L1",
            "volume": {"duration_s": 3000, "distance_m": 10000},
            "quantity_gate": {"ratio": 2.0, "quantity_reliable": False},
            "segment_sequence": [
                {"pace_sec_per_km": 300, "avg_hr": 140, "split_type": "run"},
                {"pace_sec_per_km": 240, "avg_hr": 160, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 145, "split_type": "RECOVERY"},
                {"pace_sec_per_km": 240, "avg_hr": 162, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 146, "split_type": "RECOVERY"},
            ],
        },
    }

    def loader(start, end):
        return ([activity] if start <= date.fromisoformat(activity["activity_date"]) <= end else []), {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    review = service.review_week(target=target, include_ai=False)

    session = review["quality_sessions"][0]
    assert session["activity_id"] == "unreliable-interval"
    assert session["metrics"]["reliable_segments"] == {
        "status": "gap",
        "value": None,
        "reason": "quantity_unreliable",
        "field": "reliable_segment_count",
    }
    day = next(
        item for item in review["training_day_summary"]["daily_summaries"]
        if item["date"] == activity["activity_date"]
    )
    assert day["sessions"][0]["structure_classification"]["quantity_reliable"] is False


def test_draft_training_summary_structure_feature_in_observed_features(tmp_path):
    from src.training import TrainingService

    target = date(2026, 8, 9)
    activity_date = target - timedelta(days=1)
    activity = {
        "activity_id": "interval-run-2",
        "activity_type": "running",
        "activity_name": "间歇跑",
        "activity_date": str(activity_date),
        "distance_meters": 10000,
        "duration_seconds": 3000,
        "training_analysis": {"primary_type": "interval", "confidence": 0.9},
        "session_summary": {
            "granularity": "L1",
            "volume": {"duration_s": 3000, "distance_m": 10000},
            "segment_sequence": [
                {"pace_sec_per_km": 300, "avg_hr": 140, "avg_cadence": 175, "stride_length_cm": 120, "split_type": "run"},
                {"pace_sec_per_km": 240, "avg_hr": 160, "avg_cadence": 185, "stride_length_cm": 143, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 145, "avg_cadence": 173, "stride_length_cm": 112, "split_type": "RECOVERY"},
                {"pace_sec_per_km": 240, "avg_hr": 162, "avg_cadence": 186, "stride_length_cm": 144, "split_type": "INTERVAL_ACTIVE"},
                {"pace_sec_per_km": 340, "avg_hr": 146, "avg_cadence": 173, "stride_length_cm": 112, "split_type": "RECOVERY"},
            ],
        },
    }

    def loader(start, end):
        states = {
            str(start + timedelta(days=index)): "synced"
            for index in range((end - start).days + 1)
        }
        return ([activity] if start <= activity_date <= end else []), states

    service = TrainingService(tmp_path / "memory", activity_loader=loader)
    context = service._training_summary_context(target=target)

    recent_day = [item for item in context["recent_days"] if item["date"] == str(activity_date)][0]
    codes = [f.get("feature_code") for f in recent_day.get("observed_features") or []]
    assert any(code and code.startswith("structure_") for code in codes), codes
    structure_feature = next(f for f in recent_day["observed_features"] if f["feature_code"].startswith("structure_"))
    assert structure_feature["label"] in ("间歇（2 组快慢交替）", "变速（2 组快慢交替）")
