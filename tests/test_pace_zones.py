"""tests/test_pace_zones.py — 个人配速区间计算引擎测试。"""

from datetime import date

from src.pace_zones import (
    PBRecord,
    apply_environment_compensation,
    calibrate_from_recent_activities,
    clean_pb_data,
    compute_all_zones,
    compute_hr_zones,
    compute_pace_baseline,
    fallback_from_recent_avg,
    load_cached_pace_zones,
    refresh_pace_zones_if_stale,
    save_pace_zones_to_profile,
)


TODAY = date(2026, 8, 20)


def _pb(updated: str | None = None) -> dict:
    return {"5k": {"time": "24:18", "updated": updated or TODAY.isoformat()}}


class TestCleanPbData:
    def test_no_pb_warns(self):
        effective, records, warnings = clean_pb_data({}, today=TODAY)
        assert effective is None
        assert warnings == ["无任何有效 PB 数据"]

    def test_recent_5k_becomes_effective(self):
        pbs = {
            "5k": {"time": "24:18", "updated": TODAY.isoformat()},
            "10k": {"time": "55:00", "updated": "2026-01-01"},
        }
        effective, records, _ = clean_pb_data(pbs, today=TODAY)
        assert effective is not None
        assert effective.distance == "5k"
        assert effective.weight == 1.0
        ten_k = next(r for r in records if r.distance == "10k")
        assert ten_k.weight == 0.2  # > 180 天时效衰减

    def test_hm_vdot_inconsistency_penalized(self):
        pbs = {
            "5k": {"time": "24:18", "updated": TODAY.isoformat()},
            "half_marathon": {"time": "2:30:00", "updated": TODAY.isoformat()},
        }
        effective, records, warnings = clean_pb_data(pbs, today=TODAY)
        hm = next(r for r in records if r.distance == "half_marathon")
        assert hm.weight == 0.0
        assert any("VDOT" in w for w in warnings)
        assert effective.distance == "5k"

    def test_5k_10k_merge_anchor(self):
        pbs = {
            "5k": {"time": "24:18", "updated": TODAY.isoformat()},
            "10k": {"time": "50:00", "updated": TODAY.isoformat()},
        }
        effective, records, _ = clean_pb_data(pbs, today=TODAY)
        five_k = next(r for r in records if r.distance == "5k")
        assert "锚点" in five_k.reason
        assert effective.distance == "5k"


class TestComputeHrZones:
    def test_karvonen_with_provided_hr(self):
        zones, source, warnings = compute_hr_zones(51, 190)
        assert source == "provided"
        assert not warnings
        assert zones["z1"][0] <= zones["z1"][1] <= zones["z2"][0] <= zones["z2"][1]
        assert 130 <= zones["z2"][0] <= 140
        assert zones["z2"][1] <= 152

    def test_max_hr_estimated_from_age(self):
        zones, source, warnings = compute_hr_zones(51, 0, age=34)
        assert source == "estimated"
        assert any("估算" in w for w in warnings)
        assert 180 <= zones["z5"][1] <= 186

    def test_default_max_hr_without_age(self):
        zones, source, warnings = compute_hr_zones(51, 0)
        assert source == "default"
        assert any("默认值 180" in w for w in warnings)


class TestComputePaceBaseline:
    def test_zone_order_and_relations(self):
        pb = PBRecord(distance="5k", time_seconds=1458, updated_days_ago=0,
                      weight=1.0, vdot=37.5)
        baseline = compute_pace_baseline(pb)
        assert baseline["z1"][0] > baseline["z2"][1]  # 恢复跑最慢
        assert baseline["z2"][0] > baseline["z3"][1]  # 有氧跑慢于马拉松配速
        assert baseline["z3"][0] > baseline["z4"][1]  # 马拉松配速慢于阈值
        assert baseline["z4"][0] > baseline["z5"][1]  # 阈值慢于间歇
        assert baseline["z5"][1] < 291.6 * 0.95 + 1   # 间歇快于 PB 配速


class TestCalibration:
    Z2_HR = (134, 148)

    def test_skips_with_few_activities(self):
        baseline = {"z2": (350, 365), "z4": (292, 297)}
        adjusted, meta, warnings = calibrate_from_recent_activities(
            baseline, {"z2": self.Z2_HR}, []
        )
        assert not meta["calibrated"]
        assert any("少于 3 条" in w for w in warnings)

    def test_pace_shift_when_z2_runs_slow(self):
        baseline = {"z2": (350, 365), "z4": (292, 297)}
        activities = [
            {"avg_heart_rate": 140, "distance_meters": 5000,
             "duration_seconds": 2000, "activity_date": f"2026-08-{d:02d}"}
            for d in range(10, 13)
        ]
        adjusted, meta, warnings = calibrate_from_recent_activities(
            baseline, {"z2": self.Z2_HR}, activities
        )
        assert meta["calibrated"] is True
        assert meta["method"] == "pace_shift"
        assert adjusted["z2"][1] > 365
        assert any("整体平移" in w for w in warnings)

    def test_hr_high_forces_slow_today(self):
        baseline = {"z2": (350, 365), "z4": (292, 297)}
        activities = [
            {"avg_heart_rate": 160, "distance_meters": 5000,
             "duration_seconds": 1800, "activity_date": f"2026-08-{d:02d}"}
            for d in range(10, 13)
        ]
        _, meta, _ = calibrate_from_recent_activities(
            baseline, {"z2": self.Z2_HR}, activities
        )
        assert meta["hr_high_count"] >= 2
        assert meta["force_slow_today"] is True


class TestEnvironmentCompensation:
    def test_hot_hr_priority_mode(self):
        paces = {"z2": (350, 365), "z4": (292, 297)}
        adjusted, meta, warnings = apply_environment_compensation(
            paces, temp_c=33, humidity=50
        )
        assert meta["hr_priority_mode"] is True
        assert adjusted["z2"][1] == 395
        assert any("心率优先" in w for w in warnings)

    def test_humid_shift(self):
        paces = {"z2": (350, 365), "z4": (292, 297)}
        adjusted, meta, _ = apply_environment_compensation(
            paces, temp_c=28, humidity=80
        )
        assert meta["temp_compensation_sec"] == 16
        assert adjusted["z2"][0] == 366

    def test_fatigue_uses_slow_end(self):
        paces = {"z2": (350, 365), "z4": (292, 297)}
        adjusted, meta, _ = apply_environment_compensation(
            paces, recent_3day_run_count=2
        )
        assert meta["fatigue_compensation"] is True
        assert adjusted["z2"] == (360, 365)

    def test_boundary_clamp_z2_not_faster_than_z4(self):
        paces = {"z2": (280, 300), "z4": (292, 297)}
        adjusted, _, warnings = apply_environment_compensation(
            paces, temp_c=33, humidity=50
        )
        assert adjusted["z2"][0] == 292
        assert any("封顶" in w for w in warnings)


class TestFallback:
    def test_no_activities(self):
        pace, reason = fallback_from_recent_avg([])
        assert pace is None
        assert "无任何跑步记录" in reason

    def test_insufficient_runs(self):
        activities = [
            {"distance_meters": 5000, "duration_seconds": 1800,
             "activity_date": "2026-08-18"}
        ]
        pace, reason = fallback_from_recent_avg(activities)
        assert pace is None
        assert "不足 3 条" in reason

    def test_recent_avg(self):
        activities = [
            {"distance_meters": 5000, "duration_seconds": 1800,
             "activity_date": "2026-08-18"},
            {"distance_meters": 10000, "duration_seconds": 3600,
             "activity_date": "2026-08-16"},
            {"distance_meters": 5000, "duration_seconds": 1500,
             "activity_date": "2026-08-14"},
        ]
        pace, reason = fallback_from_recent_avg(activities)
        assert pace == 340
        assert "平均配速反推" in reason


class TestComputeAllZones:
    def test_full_pipeline(self):
        result = compute_all_zones(
            personal_bests=_pb(), hr_rest=51, hr_max=190, age=34, today=TODAY,
        )
        assert len(result.zones) == 5
        assert result.effective_pb["distance"] == "5k"
        assert result.hr_max_source == "provided"
        z2, z5 = result.zones[1], result.zones[4]
        assert z2.pace_min_sec_per_km > z5.pace_min_sec_per_km
        assert "VDOT 映射" in z2.source
        for z in result.zones:
            assert z.hr_min <= z.hr_max
            assert z.pace_display != "不可用"

    def test_all_pb_expired_uses_fallback(self):
        pbs = {"5k": {"time": "24:18", "updated": "2026-01-01"}}
        activities = [
            {"distance_meters": 5000, "duration_seconds": 1800,
             "activity_date": "2026-08-18", "activity_type": "running"},
            {"distance_meters": 10000, "duration_seconds": 3600,
             "activity_date": "2026-08-16", "activity_type": "running"},
            {"distance_meters": 5000, "duration_seconds": 1500,
             "activity_date": "2026-08-14", "activity_type": "running"},
        ]
        result = compute_all_zones(
            personal_bests=pbs, hr_rest=51, hr_max=190,
            activities=activities, today=TODAY,
        )
        assert "fallback_recent_avg" in result.zones[1].source
        assert any("超过 180 天" in w for w in result.warnings)

    def test_no_data_pace_unavailable(self):
        result = compute_all_zones(personal_bests={}, hr_rest=0, hr_max=0, today=TODAY)
        assert result.hr_max_source == "default"
        for z in result.zones:
            assert z.pace_min_sec_per_km is None
            assert z.pace_display == "不可用"


class TestPersistence:
    @staticmethod
    def _make_profile(tmp_path):
        profile = tmp_path / "profile" / "fitness-assessment.md"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            "---\npersonal_info:\n  nickname: tester\n---\n\n# Fitness Assessment\n",
            encoding="utf-8",
        )
        return profile

    def test_save_and_load(self, tmp_path):
        self._make_profile(tmp_path)
        result = compute_all_zones(personal_bests=_pb(), hr_rest=51, hr_max=190,
                                   today=TODAY)
        ok = save_pace_zones_to_profile(str(tmp_path), result, 51, 190, age=34)
        assert ok is True
        cached = load_cached_pace_zones(str(tmp_path))
        assert cached is not None
        assert cached["hr_rest"] == 51
        assert cached["z2"]["pace_min_sec"] == result.zones[1].pace_min_sec_per_km
        assert "updated" in cached

    def test_save_missing_profile_returns_false(self, tmp_path):
        result = compute_all_zones(personal_bests=_pb(), hr_rest=51, hr_max=190,
                                   today=TODAY)
        assert save_pace_zones_to_profile(str(tmp_path), result, 51, 190) is False

    def test_refresh_uses_cache_when_fresh_and_forces_recompute(self, tmp_path):
        self._make_profile(tmp_path)
        result = compute_all_zones(personal_bests=_pb(), hr_rest=51, hr_max=190,
                                   today=TODAY)
        assert save_pace_zones_to_profile(str(tmp_path), result, 51, 190) is True
        cached_first = load_cached_pace_zones(str(tmp_path))
        refreshed = refresh_pace_zones_if_stale(
            str(tmp_path), personal_bests=_pb(), hr_rest=51, hr_max=190,
            force=False,
        )
        assert refreshed == cached_first
        forced = refresh_pace_zones_if_stale(
            str(tmp_path), personal_bests=_pb(), hr_rest=51, hr_max=190,
            force=True,
        )
        assert forced is not None
        assert forced["z2"] == cached_first["z2"]
