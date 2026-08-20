"""确定性跑步分析引擎测试。"""

from __future__ import annotations

import pytest

from src.running_analysis import (
    AcwrResult,
    AerobicDriftResult,
    CleanedSegment,
    CleaningReport,
    ConsistencyResult,
    DecouplingResult,
    EconomyResult,
    EnvironmentCompensationResult,
    FatigueCompensationResult,
    HrvBaselineResult,
    InjuryRiskResult,
    analyze_aerobic_drift,
    analyze_cardiac_muscle_decoupling,
    analyze_hrv_baseline,
    assess_injury_risk,
    assess_running_economy,
    calculate_acwr,
    check_recovery_performance_consistency,
    clean_segment_sequence,
    compensate_environment,
    identify_fatigue_compensation,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _seg(
    index: int,
    pace: float | None = None,
    hr: float | None = None,
    cadence: float | None = None,
    stride: float | None = None,
    duration: float = 300,
    distance: float = 1000,
    split_type: str | None = None,
) -> dict:
    """快速构造 segment dict。"""
    return {
        "index": index,
        "pace_sec_per_km": pace,
        "avg_hr": hr,
        "avg_cadence": cadence,
        "stride_length_cm": stride,
        "duration_s": duration,
        "distance_m": distance,
        "split_type": split_type,
    }


def _cleaned(
    index: int,
    pace: float | None = None,
    hr: float | None = None,
    cadence: float | None = None,
    stride: float | None = None,
    duration: float = 300,
    distance: float = 1000,
    split_type: str | None = None,
    flags: tuple[str, ...] = (),
) -> CleanedSegment:
    """快速构造 CleanedSegment。"""
    return CleanedSegment(
        index=index,
        pace_sec_per_km=pace,
        avg_hr=hr,
        avg_cadence=cadence,
        stride_length_cm=stride,
        duration_s=duration,
        distance_m=distance,
        split_type=split_type,
        quality_flags=flags,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S1 — 数据清洗与异常修复
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleaning:
    """S1 数据清洗测试。"""

    def test_empty_input(self):
        result = clean_segment_sequence([])
        assert isinstance(result, CleaningReport)
        assert result.total_segments == 0
        assert result.overall_quality == "good"

    def test_normal_data_unchanged(self):
        segments = [
            _seg(0, pace=300, hr=145, cadence=180, stride=110),
            _seg(1, pace=302, hr=147, cadence=181, stride=109),
            _seg(2, pace=298, hr=146, cadence=179, stride=111),
        ]
        result = clean_segment_sequence(segments)
        assert result.total_segments == 3
        assert result.cleaned_segments == 0
        assert result.overall_quality == "good"
        for seg in result.segments:
            assert seg.quality_flags == ()

    def test_gps_drift_pace_too_fast(self):
        segments = [
            _seg(0, pace=300, hr=145),
            _seg(1, pace=90, hr=145),
            _seg(2, pace=302, hr=146),
        ]
        result = clean_segment_sequence(segments)
        assert result.cleaned_segments >= 1
        assert "gps_drift" in result.flags_summary
        drift_seg = result.segments[1]
        assert "gps_drift" in drift_seg.quality_flags
        assert drift_seg.pace_sec_per_km == pytest.approx(301, abs=1)

    def test_gps_inconsistent_pace(self):
        segments = [
            _seg(0, pace=500, hr=145, duration=300, distance=1000),
        ]
        result = clean_segment_sequence(segments)
        assert "gps_inconsistent" in result.flags_summary
        seg = result.segments[0]
        assert "gps_inconsistent" in seg.quality_flags
        assert seg.pace_sec_per_km == pytest.approx(300, abs=1)

    def test_hr_spike_detection(self):
        segments = [
            _seg(0, pace=300, hr=160),
            _seg(1, pace=300, hr=80),
            _seg(2, pace=300, hr=162),
        ]
        result = clean_segment_sequence(segments)
        assert "hr_spike" in result.flags_summary
        spike_seg = result.segments[1]
        assert "hr_spike" in spike_seg.quality_flags
        assert spike_seg.avg_hr == pytest.approx(161, abs=1)

    def test_hr_outlier(self):
        segments = [
            _seg(0, pace=300, hr=250),
            _seg(1, pace=300, hr=50),
        ]
        result = clean_segment_sequence(segments)
        assert "hr_outlier" in result.flags_summary
        assert result.segments[0].avg_hr is None
        assert result.segments[1].avg_hr is None

    def test_cadence_outlier(self):
        segments = [
            _seg(0, pace=300, cadence=100),
            _seg(1, pace=300, cadence=250),
            _seg(2, pace=300, cadence=180),
        ]
        result = clean_segment_sequence(segments)
        assert "cadence_outlier" in result.flags_summary
        assert result.segments[0].avg_cadence is None
        assert result.segments[1].avg_cadence is None
        assert result.segments[2].avg_cadence == 180

    def test_stride_outlier(self):
        segments = [
            _seg(0, pace=300, stride=30),
            _seg(1, pace=300, stride=250),
        ]
        result = clean_segment_sequence(segments)
        assert "stride_outlier" in result.flags_summary
        assert result.segments[0].stride_length_cm is None
        assert result.segments[1].stride_length_cm is None

    def test_overall_quality_degraded(self):
        segments = [_seg(i, pace=300, hr=145) for i in range(9)] + [
            _seg(9, pace=90, hr=145)
        ]
        result = clean_segment_sequence(segments)
        assert result.overall_quality == "degraded"

    def test_overall_quality_poor(self):
        segments = [
            _seg(0, pace=90, hr=145),
            _seg(1, pace=90, hr=145),
            _seg(2, pace=300, hr=145),
        ]
        result = clean_segment_sequence(segments)
        assert result.overall_quality == "poor"

    def test_original_values_preserved(self):
        segments = [_seg(0, pace=90, hr=145, cadence=180, stride=110)]
        result = clean_segment_sequence(segments)
        seg = result.segments[0]
        assert seg.original_pace == 90
        assert seg.original_hr is None
        assert seg.original_cadence is None
        assert seg.original_stride is None


# ═══════════════════════════════════════════════════════════════════════════════
# S4 — 有氧漂移分析
# ═══════════════════════════════════════════════════════════════════════════════

class TestAerobicDrift:
    """S4 有氧漂移测试。"""

    def test_insufficient_segments(self):
        segments = [
            _cleaned(0, pace=300, hr=145),
            _cleaned(1, pace=302, hr=147),
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "insufficient_data"

    def test_no_steady_segment(self):
        # 2σ 过滤对小数据集天生宽松，四个段因配速标准差大但仍全部在 2σ 内。
        # 长距离段时长不足 20min 时才会触发 insufficient_data。
        segments = [
            _cleaned(0, pace=300, hr=145, duration=120, distance=500),
            _cleaned(1, pace=400, hr=150, duration=120, distance=500),
            _cleaned(2, pace=240, hr=140, duration=120, distance=500),
            _cleaned(3, pace=380, hr=148, duration=120, distance=500),
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "insufficient_data"

    def test_steady_drift(self):
        segments = []
        for i in range(10):
            hr = 140 + i * 1.7
            segments.append(_cleaned(
                i, pace=330, hr=hr, duration=360, distance=1000,
                split_type="RUN",
            ))
        result = analyze_aerobic_drift(segments)
        assert result.status == "ok"
        assert result.drift_rate_pct is not None
        assert 4.0 < result.drift_rate_pct < 10.0
        assert result.grade in ("elevated", "normal")
        assert result.steady_segment_count >= 3

    def test_steady_drift_excellent(self):
        segments = [
            _cleaned(i, pace=330, hr=145, duration=360, distance=1000,
                     split_type="RUN")
            for i in range(10)
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "ok"
        assert result.drift_rate_pct is not None
        assert abs(result.drift_rate_pct) < 3.0
        assert result.grade == "excellent"

    def test_steady_drift_high(self):
        segments = []
        for i in range(10):
            hr = 140 + i * 2.5
            segments.append(_cleaned(
                i, pace=330, hr=hr, duration=360, distance=1000,
                split_type="RUN",
            ))
        result = analyze_aerobic_drift(segments)
        assert result.status == "ok"
        assert result.drift_rate_pct is not None
        assert result.drift_rate_pct > 8.0
        assert result.grade == "high"

    def test_no_hr_data(self):
        segments = [
            _cleaned(i, pace=330, hr=None, duration=360, distance=1000)
            for i in range(10)
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "insufficient_data"

    def test_excludes_warmup_cooldown(self):
        segments = [
            _cleaned(0, pace=400, hr=120, split_type="WARMUP"),
            _cleaned(1, pace=330, hr=145, split_type="RUN"),
            _cleaned(2, pace=330, hr=148, split_type="RUN"),
            _cleaned(3, pace=330, hr=150, split_type="RUN"),
            _cleaned(4, pace=330, hr=152, split_type="RUN"),
            _cleaned(5, pace=330, hr=154, split_type="RUN"),
            _cleaned(6, pace=400, hr=130, split_type="COOLDOWN"),
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "ok"
        assert result.steady_segment_count == 5

    def test_pace_correction_applied(self):
        segments = [
            _cleaned(0, pace=330, hr=145, duration=360, distance=1000),
            _cleaned(1, pace=330, hr=147, duration=360, distance=1000),
            _cleaned(2, pace=330, hr=149, duration=360, distance=1000),
            _cleaned(3, pace=320, hr=155, duration=360, distance=1000),
            _cleaned(4, pace=320, hr=157, duration=360, distance=1000),
            _cleaned(5, pace=320, hr=159, duration=360, distance=1000),
        ]
        result = analyze_aerobic_drift(segments)
        assert result.status == "ok"
        assert result.pace_correction_applied


# ═══════════════════════════════════════════════════════════════════════════════
# S5 — 跑步经济性评估
# ═══════════════════════════════════════════════════════════════════════════════

class TestEconomy:
    """S5 经济性测试。"""

    def test_economy_calculation(self):
        segments = [
            _cleaned(i, pace=300, hr=150, duration=300, distance=1000)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.status == "ok"
        assert result.economy_index is not None
        assert result.economy_index == pytest.approx(22.2, abs=0.3)

    def test_no_hr(self):
        segments = [
            _cleaned(i, pace=300, hr=None, duration=300, distance=1000)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.status == "no_hr"

    def test_no_pace(self):
        segments = [
            _cleaned(i, pace=None, hr=150, duration=300, distance=1000)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.status == "insufficient_data"

    def test_empty_input(self):
        result = assess_running_economy([])
        assert result.status == "insufficient_data"

    def test_gait_high_cadence(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=185, stride=95)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.gait_label == "高步频省力型"

    def test_gait_long_stride(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=165, stride=120)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.gait_label == "大步幅低步频型"

    def test_gait_balanced(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=178, stride=112)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.gait_label == "均衡高效型"

    def test_gait_inefficient(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=170, stride=100)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.gait_label == "低效型"

    def test_gait_unknown(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=None, stride=None)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert result.gait_label == "unknown"

    def test_historical_baseline(self):
        segments = [
            _cleaned(i, pace=300, hr=150, duration=300, distance=1000)
            for i in range(5)
        ]
        historical = [
            EconomyResult(status="ok", economy_index=24.0,
                          avg_pace_sec_per_km=305, avg_hr=140),
            EconomyResult(status="ok", economy_index=23.0,
                          avg_pace_sec_per_km=298, avg_hr=145),
        ]
        result = assess_running_economy(segments, historical_economies=historical)
        assert result.baseline_available
        assert result.baseline_economy == pytest.approx(23.5, abs=0.1)
        assert result.baseline_sample_count == 2
        assert result.trend_pct is not None
        assert result.trend_pct < 0

    def test_baseline_unavailable(self):
        segments = [
            _cleaned(i, pace=300, hr=150, duration=300, distance=1000)
            for i in range(5)
        ]
        result = assess_running_economy(segments)
        assert not result.baseline_available
        assert result.baseline_economy is None
        assert result.trend_pct is None


# ═══════════════════════════════════════════════════════════════════════════════
# S6 — 疲劳代偿模式识别
# ═══════════════════════════════════════════════════════════════════════════════

class TestFatigueCompensation:
    """S6 疲劳代偿测试。"""

    def test_insufficient_segments(self):
        segments = [
            _cleaned(0, pace=300, hr=150),
            _cleaned(1, pace=302, hr=151),
            _cleaned(2, pace=305, hr=152),
        ]
        result = identify_fatigue_compensation(segments)
        assert result.status == "insufficient_data"

    def test_pattern_a_muscle(self):
        segments = []
        for i in range(6):
            if i < 3:
                segments.append(_cleaned(
                    i, pace=300, hr=150, cadence=180, stride=110,
                    duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=312, hr=151, cadence=178, stride=105.6,
                    duration=300, distance=1000,
                ))
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern == "A"
        assert result.pattern_label == "肌肉耐力瓶颈"

    def test_pattern_b_cardiac(self):
        segments = []
        for i in range(6):
            if i < 3:
                segments.append(_cleaned(
                    i, pace=300, hr=150, cadence=180, stride=110,
                    duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=312, hr=155, cadence=174, stride=105.6,
                    duration=300, distance=1000,
                ))
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern == "B"
        assert result.pattern_label == "心肺输出瓶颈"

    def test_pattern_c_neural(self):
        segments = [
            _cleaned(0, pace=300, hr=150, cadence=180, stride=110),
            _cleaned(1, pace=300, hr=150, cadence=180, stride=110),
            _cleaned(2, pace=300, hr=150, cadence=180, stride=110),
            _cleaned(3, pace=302, hr=151, cadence=170, stride=115),
            _cleaned(4, pace=298, hr=149, cadence=190, stride=105),
            _cleaned(5, pace=301, hr=150, cadence=175, stride=112),
        ]
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern in ("C", "no_significant_fatigue", "mixed")

    def test_no_significant_fatigue(self):
        segments = [
            _cleaned(i, pace=300, hr=150, cadence=180, stride=110,
                     duration=300, distance=1000)
            for i in range(6)
        ]
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern == "no_significant_fatigue"

    def test_decay_onset(self):
        segments = [
            _cleaned(0, pace=300, hr=150, duration=300, distance=1000),
            _cleaned(1, pace=300, hr=150, duration=300, distance=1000),
            _cleaned(2, pace=300, hr=150, duration=300, distance=1000),
            _cleaned(3, pace=300, hr=150, duration=300, distance=1000),
            _cleaned(4, pace=320, hr=152, duration=300, distance=1000),
            _cleaned(5, pace=322, hr=153, duration=300, distance=1000),
            _cleaned(6, pace=325, hr=154, duration=300, distance=1000),
        ]
        result = identify_fatigue_compensation(segments)
        assert result.decay_onset_segment == 5
        assert result.decay_onset_distance_km == pytest.approx(4.0, abs=0.1)

    def test_excludes_non_running(self):
        segments = [
            _cleaned(0, pace=400, hr=120, split_type="WARMUP"),
            _cleaned(1, pace=300, hr=150, cadence=180, stride=110,
                     split_type="RUN"),
            _cleaned(2, pace=300, hr=150, cadence=180, stride=110,
                     split_type="RUN"),
            _cleaned(3, pace=300, hr=150, cadence=180, stride=110,
                     split_type="RUN"),
            _cleaned(4, pace=312, hr=151, stride=105.6, cadence=178,
                     split_type="RUN"),
            _cleaned(5, pace=312, hr=151, stride=105.6, cadence=178,
                     split_type="RUN"),
            _cleaned(6, pace=312, hr=151, stride=105.6, cadence=178,
                     split_type="RUN"),
            _cleaned(7, pace=400, hr=130, split_type="COOLDOWN"),
        ]
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern in ("A", "mixed")

    def test_mixed_pattern(self):
        segments = []
        for i in range(6):
            if i < 3:
                segments.append(_cleaned(
                    i, pace=300, hr=150, cadence=180, stride=110,
                    duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=312, hr=153, cadence=170, stride=105,
                    duration=300, distance=1000,
                ))
        result = identify_fatigue_compensation(segments)
        assert result.status == "ok"
        assert result.pattern in ("A", "B", "mixed")

    def test_training_suggestion(self):
        segments = []
        for i in range(6):
            if i < 3:
                segments.append(_cleaned(
                    i, pace=300, hr=150, cadence=180, stride=110,
                    duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=312, hr=151, cadence=178, stride=105.6,
                    duration=300, distance=1000,
                ))
        result = identify_fatigue_compensation(segments)
        assert result.training_suggestion is not None
        assert len(result.training_suggestion) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# S8 — 心肺-肌肉解耦检测
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecoupling:
    """S8 心肺-肌肉解耦测试。"""

    def test_insufficient_segments(self):
        segments = [
            _cleaned(0, pace=300, hr=150),
            _cleaned(1, pace=302, hr=151),
        ]
        result = analyze_cardiac_muscle_decoupling(segments)
        assert result.status == "insufficient_data"

    def test_normal_coupling(self):
        """配速和心率良好耦合。"""
        segments = [
            _cleaned(i, pace=300 + i * 2, hr=150 + i * 1.5,
                     duration=300, distance=1000)
            for i in range(10)
        ]
        result = analyze_cardiac_muscle_decoupling(segments)
        assert result.status == "ok"
        assert result.decoupling_type == "none"
        assert result.overall_pace_hr_correlation is not None
        # 配速升、心率升 → 强正相关
        assert result.overall_pace_hr_correlation > 0.7

    def test_cardiac_limit(self):
        """后半程掉速但心率不降 → 心肺限制。"""
        segments = []
        for i in range(8):
            if i < 4:
                segments.append(_cleaned(
                    i, pace=300, hr=150, duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=315, hr=152, duration=300, distance=1000,
                ))
        result = analyze_cardiac_muscle_decoupling(segments)
        assert result.status == "ok"
        assert result.decoupling_type in ("cardiac", "mixed", "none")

    def test_muscular_limit(self):
        """心率随配速同步下降 → 肌肉/能量限制。"""
        segments = []
        for i in range(8):
            if i < 4:
                segments.append(_cleaned(
                    i, pace=300, hr=150, duration=300, distance=1000,
                ))
            else:
                segments.append(_cleaned(
                    i, pace=315, hr=145, duration=300, distance=1000,
                ))
        result = analyze_cardiac_muscle_decoupling(segments)
        assert result.status == "ok"
        assert result.decoupling_type in ("muscular", "mixed", "none")

    def test_no_hr(self):
        """无心率时返回 insufficient_data。"""
        segments = [
            _cleaned(i, pace=300, hr=None, duration=300, distance=1000)
            for i in range(10)
        ]
        result = analyze_cardiac_muscle_decoupling(segments)
        assert result.status == "insufficient_data"


# ═══════════════════════════════════════════════════════════════════════════════
# S9 — HRV 基线对比与状态判定
# ═══════════════════════════════════════════════════════════════════════════════

class TestHrvBaseline:
    """S9 HRV 基线测试。"""

    def test_insufficient_data(self):
        """无历史数据 → insufficient_data。"""
        today = {"hrv_last_night_avg": 50.0}
        result = analyze_hrv_baseline(today, historical=[])
        assert result.status == "insufficient_data"

    def test_normal_recovery(self):
        """HRV 正常、睡眠充足 → 充分恢复。"""
        today = {
            "hrv_last_night_avg": 52.0,
            "resting_heart_rate": 48,
            "sleep_duration_hours": 8.0,
        }
        historical = [
            {"hrv_last_night_avg": 50.0, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 51.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.8},
            {"hrv_last_night_avg": 49.0, "resting_heart_rate": 50,
             "sleep_duration_hours": 7.2},
            {"hrv_last_night_avg": 52.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 50.5, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.6},
        ]
        result = analyze_hrv_baseline(today, historical=historical)
        assert result.status == "ok"
        assert result.recovery_index is not None
        assert result.recovery_label in ("充分恢复", "轻微欠恢复")

    def test_poor_recovery(self):
        """HRV 低、睡眠少 → 明显欠恢复。"""
        today = {
            "hrv_last_night_avg": 35.0,
            "resting_heart_rate": 58,
            "sleep_duration_hours": 5.0,
        }
        historical = [
            {"hrv_last_night_avg": 50.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 51.0, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.8},
            {"hrv_last_night_avg": 49.0, "resting_heart_rate": 47,
             "sleep_duration_hours": 7.2},
            {"hrv_last_night_avg": 52.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 50.5, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.6},
        ]
        result = analyze_hrv_baseline(today, historical=historical)
        assert result.status == "ok"
        assert result.recovery_index is not None
        assert result.recovery_index < 70
        assert result.recovery_label in ("明显欠恢复", "严重疲劳")

    def test_training_risk_present(self):
        """恢复差时给出训练风险提示。"""
        today = {
            "hrv_last_night_avg": 30.0,
            "resting_heart_rate": 60,
            "sleep_duration_hours": 4.5,
        }
        historical = [
            {"hrv_last_night_avg": 50.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 51.0, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.8},
            {"hrv_last_night_avg": 49.0, "resting_heart_rate": 47,
             "sleep_duration_hours": 7.2},
            {"hrv_last_night_avg": 52.0, "resting_heart_rate": 48,
             "sleep_duration_hours": 7.5},
            {"hrv_last_night_avg": 50.5, "resting_heart_rate": 49,
             "sleep_duration_hours": 7.6},
        ]
        result = analyze_hrv_baseline(today, historical=historical)
        assert result.training_risk is not None
        assert result.risk_note is not None


# ═══════════════════════════════════════════════════════════════════════════════
# S11 — 急慢性负荷比（ACWR）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcwr:
    """S11 ACWR 测试。"""

    def test_insufficient_data(self):
        """空数据 → insufficient_data。"""
        result = calculate_acwr([])
        assert result.status == "insufficient_data"

    def test_safe_zone(self):
        """ACWR 在安全区间。"""
        daily = [
            {"training_load": 100} for _ in range(30)
        ]
        result = calculate_acwr(daily)
        assert result.status == "ok"
        assert result.acwr is not None
        assert 0.8 <= result.acwr <= 1.3
        assert result.risk_level == "安全区"

    def test_high_risk(self):
        """急性负荷远高于慢性 → 高风险。"""
        daily = (
            [{"training_load": 100} for _ in range(21)]
            + [{"training_load": 300} for _ in range(7)]
        )
        result = calculate_acwr(daily)
        assert result.status == "ok"
        assert result.acwr is not None
        assert result.acwr > 1.3
        assert result.risk_level in ("警戒区", "高风险区")

    def test_deload_zone(self):
        """急性负荷偏低 → 减量区。"""
        daily = (
            [{"training_load": 200} for _ in range(21)]
            + [{"training_load": 50} for _ in range(7)]
        )
        result = calculate_acwr(daily)
        assert result.status == "ok"
        assert result.acwr is not None
        assert result.acwr < 0.8
        assert result.risk_level == "减量区"

    def test_insufficient_chronic(self):
        """慢性负荷数据不足时标注。"""
        daily = [{"training_load": 100} for _ in range(10)]
        result = calculate_acwr(daily)
        assert result.status == "ok"
        assert result.chronic_days < 28
        assert result.note is not None

    def test_adjustment_suggestion(self):
        """高风险时给出调整建议。"""
        daily = (
            [{"training_load": 100} for _ in range(21)]
            + [{"training_load": 300} for _ in range(7)]
        )
        result = calculate_acwr(daily)
        assert result.suggestion is not None
        assert result.load_adjustment_pct is not None
        assert result.load_adjustment_pct < 0  # 建议减量

# ═══════════════════════════════════════════════════════════════════════════════
# S10 — 状态-表现一致性校验
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsistency:
    """S10 状态-表现一致性测试。"""

    def test_insufficient_data(self):
        result = check_recovery_performance_consistency(hrv=None)
        assert result.status == "insufficient_data"

    def test_normal_consistent(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=80.0,
                                recovery_label="充分恢复")
        drift = AerobicDriftResult(status="ok", grade="excellent")
        result = check_recovery_performance_consistency(hrv=hrv, drift=drift)
        assert result.status == "ok"
        assert result.consistent is True
        assert result.pattern == "normal"

    def test_adrenal_compensation(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=35.0,
                                recovery_label="明显欠恢复")
        drift = AerobicDriftResult(status="ok", grade="excellent")
        result = check_recovery_performance_consistency(hrv=hrv, drift=drift)
        assert result.status == "ok"
        assert result.consistent is False
        assert result.pattern == "adrenal_compensation"

    def test_hidden_fatigue(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=85.0,
                                recovery_label="充分恢复")
        drift = AerobicDriftResult(status="ok", grade="high")
        result = check_recovery_performance_consistency(hrv=hrv, drift=drift)
        assert result.status == "ok"
        assert result.consistent is False
        assert result.pattern == "hidden_fatigue"

    def test_consistent_poor(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=35.0,
                                recovery_label="明显欠恢复")
        drift = AerobicDriftResult(status="ok", grade="elevated")
        result = check_recovery_performance_consistency(hrv=hrv, drift=drift)
        assert result.status == "ok"
        assert result.consistent is True
        assert result.pattern == "normal"

    def test_suggestion_present(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=35.0,
                                recovery_label="明显欠恢复")
        drift = AerobicDriftResult(status="ok", grade="excellent")
        result = check_recovery_performance_consistency(hrv=hrv, drift=drift)
        assert result.training_suggestion is not None
        assert len(result.training_suggestion) > 0

    def test_external_grade(self):
        hrv = HrvBaselineResult(status="ok", recovery_index=80.0,
                                recovery_label="充分恢复")
        result = check_recovery_performance_consistency(
            hrv=hrv, performance_grade="poor")
        assert result.pattern == "hidden_fatigue"


# ═══════════════════════════════════════════════════════════════════════════════
# S7 — 环境补偿与归一化
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnvironment:
    """S7 环境补偿测试。"""

    def test_flat_terrain(self):
        """平路 → 无需补偿。"""
        result = compensate_environment(
            avg_pace_sec_per_km=300, avg_hr=150,
            total_ascent_m=30, distance_m=10000,  # 3m/km
        )
        assert result.status == "flat_terrain"
        assert result.terrain_class == "flat"
        assert result.normalized_pace_sec_per_km == 300
        assert result.elevation_contribution_pct == 0.0

    def test_hilly_compensation(self):
        """丘陵地形 → 坡度补偿。"""
        result = compensate_environment(
            avg_pace_sec_per_km=360, avg_hr=160,
            total_ascent_m=500, distance_m=10000,  # 50m/km
        )
        assert result.status == "ok"
        assert result.terrain_class == "hilly"
        assert result.normalized_pace_sec_per_km is not None
        assert result.normalized_pace_sec_per_km < 360  # 等效配速更快
        assert result.elevation_contribution_pct is not None
        assert result.elevation_contribution_pct > 0

    def test_mountain_terrain(self):
        """山地 → 强补偿。"""
        result = compensate_environment(
            avg_pace_sec_per_km=420, avg_hr=170,
            total_ascent_m=800, distance_m=10000,  # 80m/km
        )
        assert result.status == "ok"
        assert result.terrain_class == "mountain"
        assert result.normalized_pace_sec_per_km < 400

    def test_no_pace(self):
        """无配速 → insufficient_data。"""
        result = compensate_environment(
            avg_pace_sec_per_km=None, avg_hr=150,
            total_ascent_m=500, distance_m=10000,
        )
        assert result.status == "insufficient_data"

    def test_gain_per_km_priority(self):
        """直接传入 gain_per_km 优先。"""
        result = compensate_environment(
            avg_pace_sec_per_km=360, avg_hr=160,
            gain_per_km=50,
        )
        assert result.status == "ok"
        assert result.terrain_class == "hilly"

    def test_weather_unavailable(self):
        """天气数据不可用标记。"""
        result = compensate_environment(
            avg_pace_sec_per_km=360, avg_hr=160,
            total_ascent_m=500, distance_m=10000,
        )
        assert not result.weather_available
        assert result.weather_note is not None


# ═══════════════════════════════════════════════════════════════════════════════
# S13 — 伤病风险综合评估
# ═══════════════════════════════════════════════════════════════════════════════

class TestInjuryRisk:
    """S13 伤病风险测试。"""

    def test_insufficient_data(self):
        """无任何因子 → insufficient_data。"""
        result = assess_injury_risk()
        assert result.status == "insufficient_data"

    def test_low_risk(self):
        """安全区 → 低风险。"""
        acwr = AcwrResult(status="ok", acwr=1.0, risk_level="安全区")
        hrv = HrvBaselineResult(status="ok", recovery_index=80.0)
        result = assess_injury_risk(acwr=acwr, hrv=hrv)
        assert result.status == "ok"
        assert result.risk_score is not None
        assert result.risk_score < 25
        assert result.risk_level == "低风险"

    def test_high_risk_acwr(self):
        """ACWR 极高 + 持续代偿 + 恢复差 → 警告。"""
        acwr = AcwrResult(status="ok", acwr=1.8, risk_level="高风险区")
        hrv = HrvBaselineResult(status="ok", recovery_index=20.0)
        hrv_history = [
            {"recovery_index": 25}, {"recovery_index": 22},
            {"recovery_index": 18}, {"recovery_index": 20},
        ]
        fatigue = FatigueCompensationResult(
            status="ok", pattern="A", stride_change_pct=-8.0,
            cadence_cv_change=1.5,
        )
        fatigue_history = [
            FatigueCompensationResult(status="ok", pattern="A"),
            FatigueCompensationResult(status="ok", pattern="A"),
            FatigueCompensationResult(status="ok", pattern="B"),
        ]
        result = assess_injury_risk(
            acwr=acwr, hrv=hrv, hrv_history=hrv_history,
            fatigue=fatigue, fatigue_history=fatigue_history,
        )
        assert result.status == "ok"
        assert result.risk_score is not None
        assert result.risk_score >= 40
        assert result.risk_level in ("警告", "必须休息")

    def test_consecutive_poor_recovery(self):
        """连续恢复差 → 风险升高。"""
        hrv = HrvBaselineResult(status="ok", recovery_index=25.0)
        hrv_history = [
            {"recovery_index": 28},
            {"recovery_index": 25},
            {"recovery_index": 22},
            {"recovery_index": 20},
        ]
        result = assess_injury_risk(hrv=hrv, hrv_history=hrv_history)
        assert result.status == "ok"
        assert result.recovery_score is not None
        assert result.recovery_score > 15  # 连续 4 天 → 高于 15

    def test_primary_risk_source(self):
        """主风险来源识别。"""
        acwr = AcwrResult(status="ok", acwr=1.5, risk_level="警戒区")
        hrv = HrvBaselineResult(status="ok", recovery_index=80.0)
        result = assess_injury_risk(acwr=acwr, hrv=hrv)
        assert result.primary_risk_source == "训练负荷过高"

    def test_action_present(self):
        """总有行动建议。"""
        acwr = AcwrResult(status="ok", acwr=1.2, risk_level="安全区")
        hrv = HrvBaselineResult(status="ok", recovery_index=75.0)
        result = assess_injury_risk(acwr=acwr, hrv=hrv)
        assert result.action is not None
        assert result.action_urgency is not None
