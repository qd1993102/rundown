"""可解释的跑步训练内容识别。

本模块只处理派生语义，不修改 Provider 原始活动或训练负荷。训练主类型
（有氧/节奏/间歇）与地形属性（平路/坡地/越野/山地）分轴输出。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from statistics import median
from typing import Any, Iterable


ANALYZER_VERSION = "session-analyzer-v1"
ACTIVITY_SYNC_METRIC_TYPE = "neurun_provider_sync"


class ActivityDayState(StrEnum):
    """指定自然日的运动事实状态。"""

    TRAINING = "training"
    CONFIRMED_REST = "confirmed_rest"
    UNKNOWN = "unknown"


def natural_week_bounds(target_date: date) -> tuple[date, date]:
    """返回目标日期所在自然周的周一和周日。"""

    monday = target_date - timedelta(days=target_date.weekday())
    return monday, monday + timedelta(days=6)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return None


def _is_running(activity: dict[str, Any]) -> bool:
    value = " ".join((
        str(activity.get("activity_type") or ""),
        str(activity.get("activity_type_name") or ""),
        str(activity.get("activity_name") or ""),
    )).lower()
    return any(token in value for token in ("run", "跑", "越野", "trail"))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class AthleteBaseline:
    """分析时点的个人能力参照。"""

    threshold_pace_sec_per_km: float | None = None
    threshold_heart_rate: int | None = None
    sample_count: int = 0
    status: str = "insufficient"
    source: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AthleteBaselineBuilder:
    """从活动发生前的近期原始跑步记录构建保守基线。"""

    def build(
        self,
        activities: list[dict[str, Any]],
        *,
        target_time: str | datetime | None = None,
        profile: dict[str, Any] | None = None,
    ) -> AthleteBaseline:
        cutoff = _parse_time(target_time)
        candidates: list[tuple[datetime | None, dict[str, Any]]] = []
        for activity in activities:
            if not _is_running(activity):
                continue
            started_at = _parse_time(
                activity.get("start_time") or activity.get("activity_date")
            )
            if cutoff and started_at:
                comparable_started = started_at.replace(tzinfo=None)
                comparable_cutoff = cutoff.replace(tzinfo=None)
                if comparable_started >= comparable_cutoff:
                    continue
                if comparable_started < comparable_cutoff - timedelta(days=42):
                    continue
            duration = _first_number(activity, "duration_seconds", "duration") or 0
            distance = _first_number(activity, "distance_meters", "distance") or 0
            if duration <= 0 or distance < 1000:
                continue
            candidates.append((started_at, activity))

        eligible = [
            activity for started_at, activity in candidates
            if not cutoff
            or not started_at
            or started_at.replace(tzinfo=None)
            >= cutoff.replace(tzinfo=None) - timedelta(days=28)
        ]
        if len(eligible) < 3:
            eligible = [activity for _, activity in candidates]

        # 个人阈值只来自档案：显式阈值心率/配速优先，其次由最大心率（可选叠加
        # 静息心率，Karvonen 公式，LT2≈88% 心率储备）推导；两者都没有时不得用
        # 近期训练平均心率/配速分位猜测——训练构成（大量轻松跑）会拉低基准，
        # 导致同一心率被高估为“阈值贴边”。
        profile = profile or {}
        personal = profile.get("personal_info") or {}
        personal_bests = profile.get("personal_bests") or {}
        profile_pace = _first_number(
            profile, "threshold_pace_sec_per_km", "lactate_threshold_pace"
        )
        profile_hr = _first_number(
            profile, "threshold_heart_rate", "lactate_threshold_heart_rate"
        )
        profile_max_hr = _first_number(
            personal, "max_heart_rate", "maximum_heart_rate", "hr_max", "max_hr"
        )
        profile_rest_hr = _first_number(
            personal, "resting_heart_rate", "rest_hr", "resting_hr"
        )

        threshold_pace = profile_pace
        if profile_hr:
            threshold_hr = profile_hr
        elif profile_max_hr and profile_rest_hr:
            threshold_hr = round(
                profile_rest_hr + 0.88 * (profile_max_hr - profile_rest_hr)
            )
        elif profile_max_hr:
            threshold_hr = round(profile_max_hr * 0.88)
        else:
            threshold_hr = None

        # PB 兜底锚点：无显式阈值配速但档案填过 PB 时，用 PB 推导阈值配速
        # （半马配速最接近阈值，10K/5K 按经验差折算）。经验折算可靠度低于实测，
        # 通过 source 标记区分，不参与心率锚点的混算。
        source = "profile" if (threshold_pace or threshold_hr) else "unavailable"
        if not threshold_pace and personal_bests:
            from .training_pace import _derive_threshold_pace
            derived_pace = _derive_threshold_pace(personal_bests)
            if derived_pace:
                threshold_pace = derived_pace
                source = "personal_bests"
        sample_count = len(eligible)
        sufficient = bool(threshold_pace or threshold_hr)

        return AthleteBaseline(
            threshold_pace_sec_per_km=(
                round(threshold_pace, 1) if threshold_pace else None
            ),
            threshold_heart_rate=threshold_hr,
            sample_count=sample_count,
            status="sufficient" if sufficient else "insufficient",
            source=source,
        )


@dataclass(frozen=True)
class AnalyzerPolicy:
    """v1 可回放的命名阈值集合。"""

    min_running_duration_sec: int = 20 * 60
    tempo_intensity_low: float = 0.94
    tempo_intensity_high: float = 1.12
    interval_min_work_blocks: int = 2
    interval_pace_contrast: float = 1.12
    hilly_min_gain_m: float = 100
    hilly_min_gain_per_km: float = 20
    mountain_min_gain_m: float = 500
    mountain_min_gain_per_km: float = 40


@dataclass(frozen=True)
class NormalizedSplit:
    index: int
    split_type: str
    distance_m: float | None
    duration_sec: float | None
    pace_sec_per_km: float | None
    avg_hr: float | None
    avg_power: float | None
    elevation_gain_m: float | None


@dataclass(frozen=True)
class NormalizedActivityFacts:
    activity_id: str
    activity_name: str
    activity_type: str
    duration_sec: float
    distance_m: float
    avg_hr: float | None
    training_load: float | None
    elevation_gain_m: float | None
    elevation_gain_per_km: float | None
    avg_pace_sec_per_km: float | None
    splits: tuple[NormalizedSplit, ...]
    data_quality: dict[str, str]


class ActivityFactsNormalizer:
    """把现有活动 dict 与详情分段归一化为分类器事实。"""

    def normalize(
        self,
        activity: dict[str, Any],
        splits: list[dict[str, Any]] | None = None,
    ) -> NormalizedActivityFacts:
        duration = _first_number(activity, "duration_seconds", "duration") or 0
        distance = _first_number(
            activity, "distance_meters", "distance_m", "distance"
        ) or 0
        elevation = _first_number(activity, "elevation_gain", "elevation_gain_m")
        pace = _first_number(
            activity, "avg_pace_sec_per_km", "pace_sec_per_km"
        )
        if pace is None and duration > 0 and distance > 0:
            pace = duration / (distance / 1000)

        normalized_splits = []
        for index, split in enumerate(splits or []):
            split_distance = _first_number(split, "distance_m", "distance")
            split_duration = _first_number(split, "duration_sec", "duration")
            split_pace = _first_number(
                split, "pace_sec_per_km", "pace_per_km"
            )
            if (
                split_pace is None
                and split_duration
                and split_distance
                and split_distance > 0
            ):
                split_pace = split_duration / (split_distance / 1000)
            normalized_splits.append(NormalizedSplit(
                index=int(split.get("index", index) or index),
                split_type=str(
                    split.get("type") or split.get("split_type") or "unknown"
                ).upper(),
                distance_m=split_distance,
                duration_sec=split_duration,
                pace_sec_per_km=split_pace,
                avg_hr=_first_number(split, "avg_hr", "average_hr"),
                avg_power=_first_number(split, "avg_power", "average_power"),
                elevation_gain_m=_first_number(
                    split, "elevation_gain", "elevation_gain_m"
                ),
            ))

        gain_per_km = None
        if elevation is not None and distance > 0:
            gain_per_km = elevation / (distance / 1000)

        return NormalizedActivityFacts(
            activity_id=str(activity.get("activity_id") or ""),
            activity_name=str(activity.get("activity_name") or ""),
            activity_type=str(
                activity.get("activity_type")
                or activity.get("activity_type_name")
                or "unknown"
            ),
            duration_sec=duration,
            distance_m=distance,
            avg_hr=_first_number(activity, "avg_heart_rate", "average_hr"),
            training_load=_first_number(
                activity, "training_load", "activity_training_load"
            ),
            elevation_gain_m=elevation,
            elevation_gain_per_km=gain_per_km,
            avg_pace_sec_per_km=pace,
            splits=tuple(normalized_splits),
            data_quality={
                "summary": "complete" if duration > 0 and distance > 0 else "partial",
                "splits": "complete" if normalized_splits else "unavailable",
                "elevation": "complete" if elevation is not None else "unavailable",
            },
        )


@dataclass(frozen=True)
class TrainingSessionAnalysis:
    primary_type: str
    terrain: str
    confidence: float
    evidence: tuple[str, ...]
    specialties: tuple[str, ...] = ()
    training_implications: tuple[str, ...] = ()
    data_quality: dict[str, str] = field(default_factory=dict)
    algorithm_version: str = ANALYZER_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = list(self.evidence)
        result["specialties"] = list(self.specialties)
        result["training_implications"] = list(self.training_implications)
        return result


class TrainingSessionAnalyzer:
    """确定性 v1 训练内容分析器。"""

    def __init__(self, policy: AnalyzerPolicy | None = None):
        self.policy = policy or AnalyzerPolicy()
        self.normalizer = ActivityFactsNormalizer()

    def analyze(
        self,
        activity: dict[str, Any],
        splits: list[dict[str, Any]] | None,
        baseline: AthleteBaseline,
        recovery: dict[str, Any] | None = None,
    ) -> TrainingSessionAnalysis:
        facts = self.normalizer.normalize(activity, splits)
        data_quality = dict(facts.data_quality)
        data_quality["athlete_baseline"] = baseline.status

        if not _is_running(activity):
            return TrainingSessionAnalysis(
                primary_type="unknown",
                terrain="unknown",
                confidence=0.2,
                evidence=("当前仅对跑步活动进行训练内容识别",),
                data_quality=data_quality,
            )

        primary_type, type_confidence, type_evidence = self._classify_type(
            facts, baseline
        )
        terrain, terrain_confidence, terrain_evidence = self._classify_terrain(
            facts
        )
        specialties = self._specialties(primary_type, terrain)
        implications = self._implications(
            primary_type, terrain, recovery or {}
        )

        known_confidences = [type_confidence]
        if terrain != "unknown":
            known_confidences.append(terrain_confidence)
        confidence = round(sum(known_confidences) / len(known_confidences), 2)

        return TrainingSessionAnalysis(
            primary_type=primary_type,
            terrain=terrain,
            confidence=confidence,
            evidence=tuple(type_evidence + terrain_evidence),
            specialties=tuple(specialties),
            training_implications=tuple(implications),
            data_quality=data_quality,
        )

    def _classify_type(
        self,
        facts: NormalizedActivityFacts,
        baseline: AthleteBaseline,
    ) -> tuple[str, float, list[str]]:
        labeled_work_count, labeled_recovery_count = self._labeled_interval_counts(
            facts.splits
        )
        if (
            labeled_work_count >= self.policy.interval_min_work_blocks
            and labeled_recovery_count >= labeled_work_count - 1
        ):
            return (
                "interval", 0.92,
                [f"检测到 {labeled_work_count} 个明确工作段及工作—恢复结构"],
            )

        pace_splits = [
            split for split in facts.splits
            if split.pace_sec_per_km and split.pace_sec_per_km > 0
        ]
        repeated = self._pace_interval_count(pace_splits)
        if repeated >= self.policy.interval_min_work_blocks:
            return (
                "interval", 0.82,
                [f"配速变化形成 {repeated} 组重复工作—恢复循环"],
            )

        name = facts.activity_name.lower()
        if "间歇" in name or "interval" in name:
            return "interval", 0.62, ["活动名称提示间歇，但缺少完整分段证据"]

        intensity = None
        intensity_evidence = ""
        if baseline.threshold_heart_rate and facts.avg_hr:
            intensity = facts.avg_hr / baseline.threshold_heart_rate
            intensity_evidence = (
                f"平均心率为个人阈值心率的 {intensity:.0%}"
            )
        elif baseline.threshold_pace_sec_per_km and facts.avg_pace_sec_per_km:
            intensity = (
                baseline.threshold_pace_sec_per_km / facts.avg_pace_sec_per_km
            )
            intensity_evidence = (
                f"平均配速强度为个人阈值配速的 {intensity:.0%}"
            )
        elif facts.avg_hr or facts.avg_pace_sec_per_km:
            # 无个人阈值：不猜测强度，明确标注评估不可用，避免“阈值贴边”类误导。
            intensity_evidence = "缺少个人阈值心率/配速，强度评估不可用"

        if (
            facts.duration_sec >= self.policy.min_running_duration_sec
            and intensity is not None
            and self.policy.tempo_intensity_low
            <= intensity
            <= self.policy.tempo_intensity_high
        ):
            confidence = 0.84 if baseline.status == "sufficient" else 0.68
            return "tempo", confidence, [intensity_evidence, "主体段连续稳定"]

        if (
            facts.duration_sec >= self.policy.min_running_duration_sec
            and intensity is not None
            and intensity < self.policy.tempo_intensity_low
        ):
            confidence = 0.82 if baseline.status == "sufficient" else 0.66
            return "aerobic", confidence, [intensity_evidence, "未发现重复高强度结构"]

        if "节奏" in name or "tempo" in name:
            return "tempo", 0.58, ["活动名称提示节奏跑，但个人强度证据不足"]
        if any(token in name for token in ("有氧", "轻松", "easy")):
            return "aerobic", 0.56, ["活动名称提示有氧跑，但个人强度证据不足"]

        if intensity_evidence:
            # 有平均心率/配速但无个人阈值（或未落入既有强度带）：如实说明评估不可用
            return "unknown", 0.35, [intensity_evidence]
        return "unknown", 0.35, ["缺少足够分段或个人强度证据，保守返回未知"]

    @staticmethod
    def _labeled_interval_counts(
        splits: tuple[NormalizedSplit, ...],
    ) -> tuple[int, int]:
        work_count = sum(
            1 for split in splits
            if any(token in split.split_type for token in ("INTERVAL_ACTIVE", "WORK"))
        )
        recovery_count = sum(
            1 for split in splits
            if any(token in split.split_type for token in ("RECOVERY", "REST", "RWD_WALK"))
        )
        return work_count, recovery_count

    def _pace_interval_count(self, splits: list[NormalizedSplit]) -> int:
        if len(splits) < 3:
            return 0
        paces = [split.pace_sec_per_km for split in splits if split.pace_sec_per_km]
        if not paces:
            return 0
        center = median(paces)
        work_indices = [
            index for index, split in enumerate(splits)
            if split.pace_sec_per_km
            and split.pace_sec_per_km <= center / self.policy.interval_pace_contrast
        ]
        repeated = 0
        for index in work_indices:
            if index + 1 >= len(splits):
                continue
            recovery_pace = splits[index + 1].pace_sec_per_km
            work_pace = splits[index].pace_sec_per_km
            if (
                recovery_pace
                and work_pace
                and recovery_pace >= work_pace * self.policy.interval_pace_contrast
            ):
                repeated += 1
        return repeated

    def _classify_terrain(
        self,
        facts: NormalizedActivityFacts,
    ) -> tuple[str, float, list[str]]:
        text = f"{facts.activity_type} {facts.activity_name}".lower()
        explicit_trail = any(token in text for token in ("trail", "越野"))
        explicit_mountain = any(token in text for token in ("mountain", "山地", "山岳"))
        gain = facts.elevation_gain_m
        density = facts.elevation_gain_per_km
        climb_evidence = (
            f"累计爬升 {gain:.0f}m，单位距离爬升 {density:.1f}m/km"
            if gain is not None and density is not None
            else ""
        )

        if (
            (explicit_trail or explicit_mountain)
            and gain is not None
            and density is not None
            and gain >= self.policy.mountain_min_gain_m
            and density >= self.policy.mountain_min_gain_per_km
        ):
            return "mountain", 0.88, [climb_evidence, "活动类型或名称提供山地/越野路线证据"]
        if explicit_trail:
            evidence = ["活动类型或名称提供明确越野证据"]
            if climb_evidence:
                evidence.append(climb_evidence)
            return "trail", 0.78, evidence
        if (
            gain is not None
            and density is not None
            and gain >= self.policy.hilly_min_gain_m
            and density >= self.policy.hilly_min_gain_per_km
        ):
            return "hilly", 0.82, [climb_evidence, "爬升证据支持坡地训练，但不能单独证明越野"]
        if gain is not None:
            return "flat", 0.7, [climb_evidence or f"累计爬升 {gain:.0f}m"]
        return "unknown", 0.3, ["缺少可用爬升或路线证据"]

    @staticmethod
    def _specialties(primary_type: str, terrain: str) -> list[str]:
        if terrain == "mountain":
            return ["mountain_endurance"]
        if terrain == "trail":
            return ["trail_running"]
        if terrain == "hilly" and primary_type == "interval":
            return ["hill_repeats"]
        if terrain == "hilly":
            return ["hill_endurance"]
        return []

    @staticmethod
    def _implications(
        primary_type: str,
        terrain: str,
        recovery: dict[str, Any],
    ) -> list[str]:
        implications = []
        if primary_type in ("interval", "tempo"):
            implications.append("计入近期高强度训练分布")
            implications.append("后续训练避免连续安排同类高强度课")
        if terrain in ("hilly", "trail", "mountain"):
            implications.append("配速评价必须考虑地形与爬升，不能直接按平路比较")
            implications.append("计入爬升专项和腿部肌肉负荷")
        if terrain == "mountain":
            implications.append("后续 24–48 小时优先观察腿部恢复")

        recovery_level = str(
            recovery.get("level") or recovery.get("recovery_level") or ""
        ).lower()
        readiness = _number(
            recovery.get("training_readiness_score")
            or recovery.get("readiness")
        )
        if recovery_level == "poor" or (readiness is not None and readiness < 50):
            implications.append("当前恢复不足，下一次关键课应进入调整提案评估")
        return implications


CREATE_ACTIVITY_ANALYSES = """
CREATE TABLE IF NOT EXISTS activity_analyses (
    analysis_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    primary_type TEXT NOT NULL,
    terrain TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    features_json TEXT NOT NULL,
    baseline_snapshot_json TEXT NOT NULL,
    specialties_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    data_quality_json TEXT NOT NULL,
    implications_json TEXT NOT NULL,
    UNIQUE (user_id, activity_id, algorithm_version, input_fingerprint)
)
"""

CREATE_ACTIVITY_ANALYSIS_OVERRIDES = """
CREATE TABLE IF NOT EXISTS activity_analysis_overrides (
    user_id INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    primary_type TEXT,
    terrain TEXT,
    reason TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, activity_id)
)
"""


def _get_session(db: Any):
    if hasattr(db, "db"):
        return db.db.get_session()
    return db.get_session()


def ensure_analysis_tables(db: Any) -> None:
    """创建版本化派生分析表；不修改原始 activities。"""
    from sqlalchemy import text

    session = _get_session(db)
    try:
        session.execute(text(CREATE_ACTIVITY_ANALYSES))
        session.execute(text(CREATE_ACTIVITY_ANALYSIS_OVERRIDES))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_training_analysis(
    db: Any,
    user_id: int,
    facts: NormalizedActivityFacts,
    baseline: AthleteBaseline,
    analysis: TrainingSessionAnalysis,
) -> str:
    """幂等保存一次不可变训练分析，返回稳定 analysis_id。"""
    from sqlalchemy import text

    ensure_analysis_tables(db)
    features = asdict(facts)
    baseline_snapshot = baseline.to_dict()
    fingerprint_source = json.dumps(
        {"features": features, "baseline": baseline_snapshot},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    analysis_id = hashlib.sha256(
        f"{user_id}:{facts.activity_id}:{analysis.algorithm_version}:{fingerprint}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]

    session = _get_session(db)
    try:
        session.execute(text("""
            INSERT OR IGNORE INTO activity_analyses
                (analysis_id, user_id, activity_id, algorithm_version,
                 input_fingerprint, primary_type, terrain, confidence,
                 features_json, baseline_snapshot_json, specialties_json,
                 evidence_json, data_quality_json, implications_json)
            VALUES
                (:analysis_id, :user_id, :activity_id, :algorithm_version,
                 :input_fingerprint, :primary_type, :terrain, :confidence,
                 :features_json, :baseline_json, :specialties_json,
                 :evidence_json, :data_quality_json, :implications_json)
        """), {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "activity_id": facts.activity_id,
            "algorithm_version": analysis.algorithm_version,
            "input_fingerprint": fingerprint,
            "primary_type": analysis.primary_type,
            "terrain": analysis.terrain,
            "confidence": analysis.confidence,
            "features_json": json.dumps(features, ensure_ascii=False, default=str),
            "baseline_json": json.dumps(baseline_snapshot, ensure_ascii=False),
            "specialties_json": json.dumps(list(analysis.specialties), ensure_ascii=False),
            "evidence_json": json.dumps(list(analysis.evidence), ensure_ascii=False),
            "data_quality_json": json.dumps(analysis.data_quality, ensure_ascii=False),
            "implications_json": json.dumps(
                list(analysis.training_implications), ensure_ascii=False
            ),
        })
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return analysis_id


def get_latest_training_analysis(
    db: Any, user_id: int, activity_id: str,
) -> dict[str, Any] | None:
    """读取活动的最新自动分析，并叠加可选用户纠正。"""
    from sqlalchemy import text

    ensure_analysis_tables(db)
    session = _get_session(db)
    try:
        row = session.execute(text("""
            SELECT analysis_id, algorithm_version, primary_type, terrain,
                   confidence, specialties_json, evidence_json,
                   data_quality_json, implications_json, analyzed_at,
                   features_json
            FROM activity_analyses
            WHERE user_id = :user_id AND activity_id = :activity_id
            ORDER BY analyzed_at DESC, rowid DESC
            LIMIT 1
        """), {"user_id": user_id, "activity_id": str(activity_id)}).fetchone()
        if row is None:
            return None
        override = session.execute(text("""
            SELECT primary_type, terrain, reason, updated_at
            FROM activity_analysis_overrides
            WHERE user_id = :user_id AND activity_id = :activity_id
        """), {"user_id": user_id, "activity_id": str(activity_id)}).fetchone()
    finally:
        session.close()

    result = {
        "analysis_id": row[0],
        "algorithm_version": row[1],
        "primary_type": row[2],
        "terrain": row[3],
        "confidence": row[4],
        "specialties": json.loads(row[5]),
        "evidence": json.loads(row[6]),
        "data_quality": json.loads(row[7]),
        "training_implications": json.loads(row[8]),
        "analyzed_at": str(row[9]),
        "features": json.loads(row[10]),
    }
    if override:
        result["automatic_primary_type"] = result["primary_type"]
        result["automatic_terrain"] = result["terrain"]
        result["primary_type"] = override[0] or result["primary_type"]
        result["terrain"] = override[1] or result["terrain"]
        result["user_override"] = {
            "reason": override[2], "updated_at": str(override[3]),
        }
    return result


def display_name(primary_type: str, terrain: str) -> str:
    """将双轴结果组合为用户可见名称。"""
    type_names = {
        "aerobic": "有氧跑", "tempo": "节奏跑",
        "interval": "间歇跑", "unknown": "跑步训练",
    }
    terrain_prefix = {
        "hilly": "坡地", "trail": "越野", "mountain": "山地",
    }.get(terrain, "")
    return f"{terrain_prefix}{type_names.get(primary_type, '跑步训练')}"
