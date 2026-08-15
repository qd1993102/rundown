"""测试 render.py — HTML 生成和三主题切换。"""

import stat
import tempfile
from datetime import date
from pathlib import Path

from src.render import (
    _md_to_html, _inline, _sparkline,
    _hero, _metric_strip, _sessions, _load, _rec, _ai_section, _detail,
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

    def test_canonical_activity_field_takes_precedence(self):
        fm = dict(SAMPLE_FM)
        fm["daily_activities"] = {
            "is_rest_day": False,
            "sessions": [{
                "type": "running", "name": "规范字段活动",
                "duration_min": 30, "distance_km": 5,
                "training_load": 50,
            }],
            "total_duration_min": 30,
            "total_distance_km": 5,
            "total_training_load": 50,
        }

        rendered = _sessions(fm)

        assert "规范字段活动" in rendered
        assert "晨跑" not in rendered

    def test_rest_day(self):
        fm = dict(SAMPLE_FM)
        fm["yesterday_activities"] = {"is_rest_day": True, "daily_steps": 6000,
                                       "daily_distance_km": 4.0, "daily_active_cal": 50,
                                       "activity_level": "light"}
        s = _sessions(fm)
        assert "🧘" in s
        assert "休息日" in s

    def test_unknown_activity_state_is_not_rendered_as_rest(self):
        fm = dict(SAMPLE_FM)
        fm["yesterday_activities"] = {
            "activity_state": "unknown",
            "is_rest_day": False,
            "sessions": [],
        }

        s = _sessions(fm)

        assert "运动数据未同步" in s
        assert "无法判断当天是否训练或休息" in s
        assert "<h3>休息日</h3>" not in s

    def test_load(self):
        l = _load(SAMPLE_FM)
        assert "1.05" in l
        assert "optimal" in l

    def test_load_stats_round_load_values_to_integers(self):
        # 历史数据含浮点二进制精度噪声（acute_load_7d: 1000.6190490722656），
        # 急性/慢性负荷必须统一取整展示，不得输出长小数。
        fm = dict(SAMPLE_FM)
        fm["training_load"] = {
            "acwr": 1.05, "acwr_status": "optimal",
            "acute_load_7d": 1000.6190490722656, "chronic_load_28d": 1022.1,
        }
        l = _load(fm)
        assert "1000.6190490722656" not in l
        assert ">1001<" in l
        assert ">1022<" in l
        assert ">1022.1<" not in l

    def test_rec(self):
        r = _rec(SAMPLE_FM)
        assert "可以训练" in r

    def test_unavailable_load_and_recommendation_do_not_show_fake_scores(self):
        fm = dict(SAMPLE_FM)
        fm["training_load"] = {
            "status": "unavailable", "reason": "活动历史覆盖不足，未计算 ACWR",
        }
        fm["recommendation"] = {
            "status": "unavailable", "training_advice": "数据不足",
        }

        assert "未计算 ACWR" in _load(fm)
        assert "暂不提供训练强度建议" in _rec(fm)
        assert "建议休息" not in _rec(fm)

    def test_load_renders_capacity_context_when_available(self):
        fm = dict(SAMPLE_FM)
        fm["athlete_context"] = {
            "status": "available",
            "facts_cutoff": "2026-07-19T23:59:00+08:00",
            "capacity_profile": {
                "current_sustainable_capacity": {
                    "weekly_km": 60.0, "long_run_km": 18.0,
                    "recent_running_pace_sec_per_km": 330,
                },
            },
        }

        html = _load(fm)

        assert "能力参考" in html
        assert "可持续周跑量参考 60 km" in html
        assert "长距离 18 km" in html
        assert "参考配速 5'30\"/km" in html
        assert "2026-07-19T23:59:00+08:00" in html

    def test_load_omits_capacity_context_when_unavailable_or_missing(self):
        fm = dict(SAMPLE_FM)
        fm["athlete_context"] = {
            "status": "unavailable", "reason": "training_load_omitted",
        }

        assert "能力参考" not in _load(fm)
        assert "能力参考" not in _load(SAMPLE_FM)

    def test_load_omits_capacity_context_when_profile_empty(self):
        fm = dict(SAMPLE_FM)
        fm["athlete_context"] = {
            "status": "available",
            "capacity_profile": {"current_sustainable_capacity": {}},
        }

        assert "能力参考" not in _load(fm)

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
            assert "NEURUN" in html
            assert "2026-06-25" in html
            assert "sport" in html  # default theme
            assert "setTheme" in html  # theme switcher JS
            assert stat.S_IMODE(Path(f.name).stat().st_mode) == 0o600

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

    def test_limited_report_keeps_quality_warning_in_html(self):
        fm = dict(SAMPLE_FM)
        fm.update({
            "data_readiness": "limited",
            "report_finality": "provisional",
            "data_as_of": "2026-06-25 08:00:00",
            "omitted_sections": ["recovery", "training_load"],
        })
        mem = Memory(
            id="2026-06-25", type=MemoryType.DAILY_REPORT,
            path=Path("/tmp/test.md"), front_matter=fm, body="test",
        )
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            html = render_daily_html(mem, f.name)

        assert "数据不完整的受限版" in html
        assert "不是全天最终结果" in html


def _v2_analysis() -> dict:
    return {
        "activity_name": "晨跑 10k",
        "display_name": "有氧跑",
        "primary_type": "aerobic",
        "confidence": 0.8,
        "algorithm_version": "session-analyzer-v1",
        "session_summary": {
            "granularity": "L1",
            "volume": {
                "duration_s": 2700, "elapsed_duration_s": 2760,
                "distance_m": 10000, "calories": 620,
            },
            "effect": {
                "aerobic_training_effect": 3.0, "anaerobic_training_effect": 0.0,
                "label": "AEROBIC_BASE", "estimated": False,
            },
            "elevation_profile": {
                "ascent_m": 60, "descent_m": 58,
                "max_elevation_m": 40, "min_elevation_m": 10,
            },
            "pace_profile": {
                "avg_pace_sec_per_km": 270, "fastest_pace_sec_per_km": 240,
                "p5": 250, "p25": 265, "p50": 270, "p75": 275, "p95": 290,
                "cv_pct": 6.5, "half_pace_diff_s": 5.0,
                "positive_split": True, "split_count": 4,
            },
            "intensity": {
                "basis": "hr",
                "pace_bands_pct": {"-inf-240": 5.0, "240-300": 90.0, "300-inf": 5.0},
                "hr_bands_pct": {"-inf-130": 10.0, "130-145": 80.0, "145-inf": 10.0},
            },
            "structure": {"avg_cadence": 178, "max_cadence": 195},
            "structure_profile": {
                "split_types": ["INTERVAL_ACTIVE", "RECOVERY", "INTERVAL_ACTIVE", "RECOVERY"],
                "work_blocks": 2, "recovery_blocks": 2, "composite_type": "interval",
            },
        },
    }


class TestDeepDetail:
    def test_detail_renders_three_blocks_with_dict_analysis(self):
        html = _detail({"session_analyses": [_v2_analysis()]})
        assert "整体水平" in html
        assert "强度分布" in html
        assert "配速节奏" in html
        assert "10.00 km" in html
        assert "含暂停" in html
        assert "折全马约" in html
        assert "4'00\"" in html
        assert "有氧 3" in html

    def test_detail_marks_estimated_effect(self):
        analysis = _v2_analysis()
        analysis["session_summary"]["effect"] = {
            "aerobic_training_effect": 3.8, "estimated": True,
            "estimate_basis": "heart_rate", "estimate_method": "zone_minutes_weighted",
        }
        html = _detail({"session_analyses": [analysis]})
        assert "≈" in html
        assert "估算，依据心率" in html

    def test_detail_shows_unavailable_for_l0_without_splits(self):
        analysis = _v2_analysis()
        analysis["session_summary"] = {
            "granularity": "L0",
            "volume": {"duration_s": 1800, "distance_m": 5000},
            "effect": None,
        }
        html = _detail({"session_analyses": [analysis]})
        assert "强度分布与配速节奏不可得" in html
        assert "整体水平" in html

    def test_detail_renders_hr_bands_with_bpm_labels(self):
        html = _detail({"session_analyses": [_v2_analysis()]})
        assert "<130: 10%" in html
        assert "130–145: 80%" in html


def _classification_fm() -> dict:
    analysis = _v2_analysis()
    analysis["structure_classification"] = {
        "structure_type": "fartlek", "label": "变速", "confidence": 0.9,
        "missing_evidence": [], "alternations": 3,
        "composition": [{"role": "body", "pct": 50}, {"role": "fast", "pct": 30}, {"role": "slow", "pct": 20}],
        "quantity_reliable": False,
        "fatigue_signal": {"detected": True, "note": "心率漂移 +8bpm、步幅后半程下降 4%"},
        "cadence_consistency": {"avg": 178.0, "cv_pct": 2.5},
    }
    return {"session_analyses": [analysis]}


class TestStructureClassification:
    def test_detail_shows_weighted_classification(self):
        html = _detail(_classification_fm())
        assert "训练结构：变速（3 组快慢交替）" in html
        assert "置信 90%" in html
        assert "疲劳信号：心率漂移 +8bpm、步幅后半程下降 4%" in html
        assert "步频一致性：CV 2.5%" in html

    def test_detail_marks_quantity_unreliable(self):
        html = _detail(_classification_fm())
        assert "仅强度模式有效" in html

    def test_detail_lists_missing_evidence(self):
        fm = _classification_fm()
        analysis = fm["session_analyses"][0]
        analysis["structure_classification"] = {
            "structure_type": "fartlek", "label": "变速", "confidence": 0.40,
            "missing_evidence": ["心率", "步频", "步幅"], "alternations": 2,
            "composition": [], "quantity_reliable": True,
            "fatigue_signal": {"detected": False},
        }
        html = _detail(fm)
        assert "置信 40%" in html
        assert "缺 心率/步频/步幅" in html

    def test_detail_falls_back_to_structure_profile_without_classification(self):
        fm = {"session_analyses": [_v2_analysis()]}
        html = _detail(fm)
        assert "训练结构：间歇结构" in html


def test_detail_shows_per_group_pace():
    fm = _classification_fm()
    analysis = fm["session_analyses"][0]
    analysis["structure_classification"] = {
        "structure_type": "fartlek", "label": "变速", "confidence": 0.9,
        "missing_evidence": [], "alternations": 2,
        "composition": [], "quantity_reliable": True,
        "fatigue_signal": {"detected": False},
        "work_recovery_groups": [
            {"group": 1, "work_pace_sec_per_km": 255, "work_distance_m": 1000, "work_avg_hr": 160,
             "recovery_pace_sec_per_km": 360, "recovery_distance_m": 1000, "recovery_avg_hr": 145},
            {"group": 2, "work_pace_sec_per_km": 255, "work_distance_m": 1200, "work_avg_hr": 162,
             "recovery_pace_sec_per_km": 360, "recovery_distance_m": 1200, "recovery_avg_hr": 146},
        ],
    }
    html = _detail(fm)
    assert "每组配速：第1组 快 4'15\"/km · 1.0km(hr160) → 慢 6'00\"/km · 1.0km(hr145)" in html
    assert "第2组 快 4'15\"/km · 1.2km(hr162)" in html
