"""训练服务装配与日报能力背景构造的单元测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.training_service_factory import (
    build_capacity_athlete_context,
    project_capacity_profile,
)


def test_capacity_context_gates_training_load_omitted():
    ctx = build_capacity_athlete_context(
        SimpleNamespace(memory_dir="memory", db_path="data.db"),
        date(2026, 8, 3),
        omitted_sections=("sleep", "training_load"),
    )
    assert ctx["status"] == "unavailable"
    assert ctx["reason"] == "training_load_omitted"
    assert ctx["source"] == "training_domain_capacity_profile"


def test_capacity_context_returns_not_loaded_when_config_incomplete():
    ctx = build_capacity_athlete_context(
        SimpleNamespace(provider_type="garmin"),
        date(2026, 8, 3),
        omitted_sections=(),
    )
    assert ctx["status"] == "unavailable"
    assert ctx["reason"] == "not_loaded"


def test_capacity_context_survives_training_domain_failure(monkeypatch):
    def boom(config, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        "src.training_service_factory.build_training_service", boom,
    )
    ctx = build_capacity_athlete_context(
        SimpleNamespace(memory_dir="memory", db_path="data.db"),
        date(2026, 8, 3),
        omitted_sections=(),
    )
    assert ctx["status"] == "unavailable"
    assert ctx["reason"] == "capacity_load_error"


def test_capacity_context_projects_available_profile(monkeypatch):
    profile = {
        "as_of": "2026-08-03",
        "facts_cutoff": "2026-08-03T08:00:00+08:00",
        "sync_coverage": "sufficient",
        "confidence": "high",
        "current_sustainable_capacity": {
            "weekly_km": 60.0, "long_run_km": 18.0,
            "recent_running_pace_sec_per_km": 330,
            "pace_sample_count": 8, "observed_weeks": 6,
        },
        "historical_proven_capacity": {
            "weekly_km": 90.0, "long_run_km": 30.0,
            "interruption_context": "travel",
        },
        "entry_load_envelope": {
            "minimum_weekly_km": 48.0, "maximum_weekly_km": 66.0,
        },
        "current_readiness": {"recovery_score": 70, "status": "ready"},
    }
    monkeypatch.setattr(
        "src.training_service_factory.build_training_service",
        lambda config, **kwargs: SimpleNamespace(
            capacity_profile=lambda target: profile
        ),
    )
    ctx = build_capacity_athlete_context(
        SimpleNamespace(memory_dir="memory", db_path="data.db"),
        date(2026, 8, 3),
        omitted_sections=(),
    )
    assert ctx["status"] == "available"
    assert ctx["facts_cutoff"] == profile["facts_cutoff"]
    cap = ctx["capacity_profile"]["current_sustainable_capacity"]
    assert cap["weekly_km"] == 60.0
    assert cap["long_run_km"] == 18.0
    assert cap["recent_running_pace_sec_per_km"] == 330
    assert (
        ctx["capacity_profile"]["historical_proven_capacity"]["interruption_context"]
        == "travel"
    )
    assert ctx["capacity_profile"]["sync_coverage"] == "sufficient"


def test_projection_turns_zero_or_empty_capacity_into_none():
    projected = project_capacity_profile({
        "as_of": "2026-08-03",
        "confidence": "low",
        "current_sustainable_capacity": {
            "weekly_km": 0.0, "long_run_km": 0.0,
            "recent_running_pace_sec_per_km": None,
            "pace_sample_count": 0, "observed_weeks": 0,
        },
    })
    cap = projected["current_sustainable_capacity"]
    assert cap["weekly_km"] is None
    assert cap["long_run_km"] is None
    assert cap["recent_running_pace_sec_per_km"] is None
    assert cap["pace_sample_count"] == 0
    assert projected["confidence"] == "low"


def test_projection_keeps_missing_sections_stable():
    projected = project_capacity_profile({
        "as_of": "2026-08-03",
        "confidence": "high",
        "current_sustainable_capacity": {
            "weekly_km": 45.0, "long_run_km": None,
            "recent_running_pace_sec_per_km": None,
            "pace_sample_count": 0, "observed_weeks": 4,
        },
    })
    assert projected["as_of"] == "2026-08-03"
    assert projected["facts_cutoff"] is None
    assert projected["historical_proven_capacity"] == {}
    assert projected["entry_load_envelope"] == {}
    assert projected["current_readiness"] == {}
