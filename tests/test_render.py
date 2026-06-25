"""测试 render.py — HTML 生成和三主题切换。"""

import tempfile
from datetime import date
from pathlib import Path

from src.render import (
    _md_to_html, _inline, _sparkline,
    _hero, _metric_strip, _sessions, _load, _rec, _ai_section,
    render_daily_html,
)
from src.memory import Memory, MemoryType

SAMPLE_FM = {
    "type": "daily_report",
    "date": "2026-06-25",
    "generated": "2026-06-25T08:00:00",
    "yesterday_activities": {
        "is_rest_day": False,
        "is_training_day": True,
        "sessions": [
            {"type": "running", "name": "晨跑", "duration_min": 60, "distance_km": 10.0,
             "avg_hr": 145, "training_load": 120},
        ],
        "total_sessions": 1, "total_duration_min": 60, "total_distance_km": 10.0,
        "total_training_load": 120, "day_type": "easy_run",
        "daily_steps": 8000, "daily_distance_km": 6.5, "daily_calories": 2000,
        "daily_active_cal": 350, "activity_level": "active",
    },
    "last_night_sleep": {"total_hours": 7.5, "quality": "good", "sleep_score": 78,
                         "deep_sleep_pct": 20, "rem_sleep_pct": 24},
    "this_morning": {"resting_hr": 48, "hrv_ms": 55, "hrv_status": "balanced",
                     "body_battery_morning": 85, "training_readiness_score": 72,
                     "training_readiness_level": "MODERATE", "avg_stress": 25},
    "training_load": {"acwr": 1.05, "acwr_status": "optimal",
                      "acute_load_7d": 300, "chronic_load_28d": 285},
    "recovery": {"overall_score": 78, "level": "good"},
    "recommendation": {"ready_to_train": True, "intensity": "moderate",
                       "training_advice": "适合中等强度训练", "caution": []},
    "anomalies": {"count": 0, "level": "normal", "items": []},
    "ai_insight": {
        "conclusion": "状态良好", "observations": ["睡眠充足", "HRV平衡"],
        "warnings": [], "recommendations": ["维持现有节奏"],
        "confidence": "medium",
    },
    "trends_7d": {},
    "session_analyses": [],
}


class TestMarkdownToHTML:
    def test_headers(self):
        html = _md_to_html("# Title\n## Section\n### Sub")
        assert "<h1>Title</h1>" in html
        assert "<h2>Section</h2>" in html
        assert "<h3>Sub</h3>" in html

    def test_list(self):
        html = _md_to_html("- item1\n- item2")
        assert "<ul>" in html
        assert "<li>item1</li>" in html

    def test_bold(self):
        assert "<strong>x</strong>" in _inline("**x**")

    def test_code(self):
        assert "<code>x</code>" in _inline("`x`")


class TestSparkline:
    def test_flat_line(self):
        svg = _sparkline([5, 5, 5], "red")
        assert "<svg" in svg
        assert "<polyline" in svg

    def test_rising(self):
        svg = _sparkline([1, 2, 3, 4, 5], "green")
        assert "<svg" in svg


class TestHTMLComponents:
    def test_hero(self):
        h = _hero(SAMPLE_FM)
        assert "7.5" in h
        assert "睡眠" in h
        assert "78" in h

    def test_metric_strip(self):
        m = _metric_strip(SAMPLE_FM)
        assert "55" in m
        assert "48" in m
        assert "85" in m

    def test_sessions(self):
        s = _sessions(SAMPLE_FM)
        assert "晨跑" in s
        assert "60" in s
        assert "10.0" in s

    def test_rest_day(self):
        fm = dict(SAMPLE_FM)
        fm["yesterday_activities"] = {"is_rest_day": True, "daily_steps": 6000,
                                       "daily_distance_km": 4.0, "daily_active_cal": 50,
                                       "activity_level": "light"}
        s = _sessions(fm)
        assert "🧘" in s
        assert "休息日" in s

    def test_load(self):
        l = _load(SAMPLE_FM)
        assert "1.05" in l
        assert "optimal" in l

    def test_rec(self):
        r = _rec(SAMPLE_FM)
        assert "可以训练" in r

    def test_ai_section(self):
        a = _ai_section(SAMPLE_FM)
        assert "状态良好" in a
        assert "睡眠充足" in a


class TestFullRender:
    def test_generates_valid_html(self):
        mem = Memory(
            id="2026-06-25", type=MemoryType.DAILY_REPORT,
            path=Path("/tmp/test.md"),
            front_matter=SAMPLE_FM,
            body="# 测试日报\n\n内容",
        )
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            html = render_daily_html(mem, f.name)
            assert "RUNDOWN" in html
            assert "2026-06-25" in html
            assert "sport" in html  # default theme
            assert "setTheme" in html  # theme switcher JS

    def test_dark_theme_selection(self):
        mem = Memory(
            id="2026-06-25", type=MemoryType.DAILY_REPORT,
            path=Path("/tmp/test.md"),
            front_matter=SAMPLE_FM, body="test",
        )
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            html = render_daily_html(mem, f.name)
            assert 'data-theme="sport"' in html
            assert "dark" in html  # theme option exists
            assert "fresh" in html
