"""训练服务装配与日报能力背景构造的单元测试。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.training_service_factory import (
    build_capacity_athlete_context,
    project_capacity_profile,
    _running_distances,
)


def test_running_distances_filters_by_window_and_running_only():
    items = [
        {"activity_type": "running", "activity_name": "早跑",
         "activity_date": "2026-08-09", "distance_meters": 20000},
        {"activity_type": "running", "activity_name": "本周长跑",
         "activity_date": "2026-08-15", "distance_meters": 30000},
        {"activity_type": "cycling", "activity_name": "骑行",
         "activity_date": "2026-08-09", "distance_meters": 50000},
        {"activity_type": "running", "activity_name": "超窗",
         "activity_date": "2026-08-20", "distance_meters": 10000},
    ]
    got = _running_distances(items, date(2026, 8, 3), date(2026, 8, 9))
    assert got == [20.0]  # 仅上一周内跑步活动，骑行与窗口外排除


class _MockStorage:
    def __init__(self, activities):
        self._activities = activities

    def get_local_user_id(self):
        return "u1"

    def get_activities_range(self, user_id, start, end):
        result = []
        for item in self._activities:
            d = date.fromisoformat(str(item["activity_date"])[:10])
            if start <= d <= end:
                result.append(item)
        return result

    def get_sync_calendar(self, user_id, start, end, today=None):
        return {"summary": {"synced": 7}}

    def close(self):
        pass


def test_setup_baseline_longest_distance_uses_28d_window(tmp_path):
    """周量基线取上一完整自然周，但长距离能力取近 28 天窗口：
    本周刚完成的 30km 长距离不得被上一周窗口漏掉。"""
    from src.config import Config
    from src.training_service_factory import build_training_service

    activities = [
        {"activity_id": "a1", "activity_type": "running", "activity_name": "上周长跑",
         "activity_date": "2026-08-09", "distance_meters": 20000},
        {"activity_id": "a2", "activity_type": "running", "activity_name": "本周长跑",
         "activity_date": "2026-08-15", "distance_meters": 30000},
    ]
    storage = _MockStorage(activities)
    config = Config(data_dir=str(tmp_path))
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.db_path).touch()  # 触发 storage 分支而非空基线
    service = build_training_service(config, storage_factory=lambda cfg: storage)

    setup = service._setup_context(target=date(2026, 8, 15))
    baseline = setup["baseline"]
    assert baseline["previous_week_km"] == 20.0      # 周量：上一完整自然周 08-03~08-09
    assert baseline["longest_distance_km"] == 30.0   # 长距离能力：28 天窗口（含本周 30km）
    assert baseline["reference_window_end"] == "2026-08-09"


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
