"""共享的单日训练事实与摘要。

该模块只做确定性归一化和聚合，不生成训练方案，也不把自然语言报告当作事实来源。
AI 摘要（如有）只能在此结构化事实之上补充非枚举运动特点。
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable


SUMMARY_SCHEMA_VERSION = "training-day-summary-v1"
ANALYSIS_SCHEMA_VERSION = "training-day-analysis-v1"

_QUALITY_TYPES = {"interval", "tempo", "fartlek"}
_QUALITY_LABELS = {
    "interval": "间歇",
    "tempo": "节奏",
    "fartlek": "变速",
    "mixed": "混合",
}


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(item: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(item.get(key))
        if value is not None:
            return value
    return None


def _running(activity: dict[str, Any]) -> bool:
    text = " ".join((
        str(activity.get("activity_type") or ""),
        str(activity.get("activity_type_name") or ""),
        str(activity.get("activity_name") or ""),
    )).lower()
    return any(token in text for token in ("run", "跑", "越野", "trail"))


def _analysis(activity: dict[str, Any]) -> dict[str, Any]:
    raw = activity.get("training_analysis") or activity.get("analysis") or {}
    return raw if isinstance(raw, dict) else {}


def _feature(
    code: str,
    label: str,
    *,
    confidence: float,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "feature_code": code,
        "label": label,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": copy.deepcopy(evidence),
    }


class TrainingDaySummaryBuilder:
    """从活动和健康事实构造可被日报、周报和草稿共享的摘要。"""

    @classmethod
    def build(
        cls,
        *,
        target: date,
        activities: Iterable[dict[str, Any]] | None = None,
        states: dict[str, Any] | None = None,
        sleep: dict[str, Any] | None = None,
        recovery: dict[str, Any] | None = None,
        training_load: dict[str, Any] | None = None,
        plan_context: dict[str, Any] | None = None,
        finality: str | None = None,
        facts_cutoff: Any = None,
    ) -> dict[str, Any]:
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(activities or []):
            if not isinstance(raw, dict):
                continue
            activity_date = _day(
                raw.get("activity_date") or raw.get("date") or raw.get("start_time")
            )
            if activity_date != target:
                continue
            activity_id = str(raw.get("activity_id") or f"row-{index}")
            if activity_id in seen:
                continue
            seen.add(activity_id)
            unique.append(raw)

        sessions: list[dict[str, Any]] = []
        types: list[str] = []
        features: list[dict[str, Any]] = []
        total_distance = 0.0
        total_duration = 0.0
        running_count = 0
        for raw in unique:
            distance = max(0.0, (_number(raw.get("distance_meters")) or 0.0) / 1000)
            duration = max(0.0, (_number(raw.get("duration_seconds")) or 0.0) / 60)
            analysis = _analysis(raw)
            primary_type = str(analysis.get("primary_type") or "")
            confidence = _number(analysis.get("confidence")) or 0.0
            running = _running(raw)
            role = primary_type or ("long" if running and distance >= 20 else "other")
            if role == "aerobic":
                role = "easy"
            if running:
                running_count += 1
                total_distance += distance
                total_duration += duration
                if primary_type and primary_type != "unknown":
                    types.append(primary_type)
            compact_summary = raw.get("session_summary")
            summary_structure = (
                (compact_summary.get("structure") or {})
                if isinstance(compact_summary, dict) else {}
            )
            summary_pace = (
                (compact_summary.get("pace_profile") or {})
                if isinstance(compact_summary, dict) else {}
            )
            session = {
                "activity_id": str(raw.get("activity_id") or ""),
                "name": str(raw.get("activity_name") or "实际活动"),
                "type": str(raw.get("activity_type") or "other"),
                "is_running": running,
                "session_role": role,
                "distance_km": round(distance, 1),
                "duration_minutes": round(duration),
                "training_load": _number(raw.get("training_load")),
                "metrics": {
                    "pace_sec_per_km": round(_first_number(raw, "pace_sec_per_km", "pace_per_km", "avg_pace_sec_per_km") or _number(summary_pace.get("avg_pace_sec_per_km")) or (duration * 60 / distance if distance > 0 and duration > 0 else 0), 1) or None,
                    "avg_heart_rate": _first_number(raw, "avg_heart_rate", "average_hr", "avg_hr"),
                    "max_heart_rate": _first_number(raw, "max_heart_rate", "max_hr"),
                    "elevation_gain_m": _first_number(raw, "elevation_gain", "elevation_gain_m"),
                    "cadence": _first_number(raw, "avg_cadence", "cadence") or _number(summary_structure.get("avg_cadence")),
                    "stride_length_cm": _first_number(raw, "stride_length_cm", "avg_stride_length") or _number(summary_structure.get("avg_stride")),
                    "power_w": _first_number(raw, "avg_power", "average_power", "power"),
                },
                "analysis": {
                    "primary_type": primary_type or "unknown",
                    "terrain": str(analysis.get("terrain") or "unknown"),
                    "confidence": round(max(0.0, min(1.0, confidence)), 2),
                    "specialties": [str(item) for item in analysis.get("specialties") or []],
                },
            }
            if isinstance(compact_summary, dict):
                session["session_summary"] = copy.deepcopy(compact_summary)
            sessions.append(session)
            evidence = [
                {"metric": "distance", "value": round(distance, 1), "unit": "km"},
                {"metric": "duration", "value": round(duration), "unit": "minutes"},
            ]
            if isinstance(compact_summary, dict):
                evidence.append({
                    "metric": "session_summary.granularity",
                    "value": compact_summary.get("granularity"),
                    "source": "activity_summary_facts",
                })
            metrics = session["metrics"]
            for metric, unit in (
                ("pace_sec_per_km", "sec/km"), ("avg_heart_rate", "bpm"),
                ("max_heart_rate", "bpm"), ("elevation_gain_m", "m"),
            ):
                if metrics.get(metric) is not None:
                    evidence.append({"metric": metric, "value": metrics[metric], "unit": unit, "source": "activity_record"})
            if primary_type and primary_type != "unknown":
                evidence.append({
                    "metric": "training_analysis.primary_type",
                    "value": primary_type,
                    "source": "training_session_analyzer",
                })
                features.append(_feature(
                    f"session_{primary_type}",
                    f"识别为{primary_type}",
                    confidence=confidence or 0.5,
                    evidence=evidence,
                ))
            if running and distance >= 20:
                features.append(_feature(
                    "long_duration", "长距离训练", confidence=1.0,
                    evidence=[{"metric": "distance", "value": round(distance, 1), "unit": "km", "threshold": 20}],
                ))
            # 训练结构判定（分析层产物）并入特点层，供草稿/周报 feature 消费
            classification = analysis.get("structure_classification")
            if isinstance(classification, dict):
                structure_type = str(classification.get("structure_type") or "unknown")
                if structure_type != "unknown":
                    alternations = classification.get("alternations") or 0
                    label = str(classification.get("label") or structure_type)
                    label_text = (
                        f"{label}（{alternations} 组快慢交替）"
                        if alternations else label
                    )
                    features.append(_feature(
                        f"structure_{structure_type}",
                        label_text,
                        confidence=float(classification.get("confidence") or 0),
                        evidence=[{
                            "metric": "structure_classification.alternations",
                            "value": alternations,
                            "source": "classify_training_structure",
                        }],
                    ))

        target_state = str((states or {}).get(str(target)) or "unknown")
        coverage = (
            "sufficient"
            if target_state in {"synced", "complete", "covered", "rest", "confirmed_rest"}
            else "unknown"
        )
        if unique:
            day_status = "training" if running_count else "other_activity"
        elif target_state in {"rest", "confirmed_rest"}:
            day_status = "confirmed_rest"
        else:
            day_status = "unknown"
        primary_type = Counter(types).most_common(1)[0][0] if types else "unknown"
        data_as_of = str(facts_cutoff or target)
        evidence = [{
            "kind": "coverage",
            "source": "activity_sync_state",
            "field": "activity_coverage",
            "data_as_of": data_as_of,
            "value": target_state,
            "confidence": None,
        }]
        evidence.extend({
            "kind": "fact",
            "source": f"activity:{item.get('activity_id') or 'unknown'}",
            "field": "distance_km",
            "data_as_of": data_as_of,
            "value": item.get("distance_km"),
            "confidence": 1.0,
        } for item in sessions)
        gaps = []
        for field, missing, effect in (
            ("activity_coverage", coverage == "unknown", "无法确认无活动是否为休息"),
            ("sleep", not sleep, "无法结合睡眠判断恢复"),
            ("recovery", not recovery, "无法判断当日恢复水平"),
            ("training_load", not training_load, "无法判断当日负荷状态"),
        ):
            if missing:
                gaps.append({"field": field, "reason": "missing", "affects": effect})
        for session in sessions:
            metrics = session.get("metrics") or {}
            summary = session.get("session_summary") or {}
            quantity_reliable = bool(
                (summary.get("quantity_gate") or {}).get("quantity_reliable", True)
            )
            for field, missing, effect in (
                ("average_pace", metrics.get("pace_sec_per_km") is None, "无法比较配速"),
                ("heart_rate", metrics.get("avg_heart_rate") is None, "无法评估心率强度"),
                ("cadence", metrics.get("cadence") is None, "无法评估步频"),
                ("stride_length", metrics.get("stride_length_cm") is None, "无法评估步幅"),
                ("reliable_splits", not quantity_reliable or not (summary.get("segment_sequence") or []), "无法使用分段量与结构"),
            ):
                if missing:
                    gaps.append({
                        "field": field,
                        "reason": "missing_or_unreliable",
                        "affects": effect,
                        "activity_id": session.get("activity_id"),
                    })
        result = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "date": str(target),
            "data_as_of": data_as_of,
            "finality": str(finality or "provisional"),
            "status": day_status,
            "sessions": sessions,
            "running_session_count": running_count,
            "total_distance_km": round(total_distance, 1),
            "total_duration_minutes": round(total_duration),
            "primary_type": primary_type,
            "observed_features": features,
            "sleep": copy.deepcopy(sleep or {}),
            "recovery": copy.deepcopy(recovery or {}),
            "training_load": copy.deepcopy(training_load or {}),
            "plan_context": copy.deepcopy(plan_context or {"status": "unknown"}),
            "evidence": evidence,
            "gaps": gaps,
            "data_quality": {
                "status": coverage,
                "facts_cutoff": data_as_of,
                "source": "deterministic_training_day_fact_builder",
            },
            "semantic_status": "unavailable",
            "semantic_summary": None,
        }
        version_payload = {
            key: result[key]
            for key in (
                "schema_version", "date", "data_as_of", "finality", "status",
                "sessions", "sleep", "recovery", "training_load", "plan_context",
                "evidence", "gaps",
            )
        }
        digest = hashlib.sha256(json.dumps(
            version_payload, ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()[:12]
        result["fact_version"] = f"{SUMMARY_SCHEMA_VERSION}:{target}:{digest}"
        result["summary_version"] = result["fact_version"]
        return result

    @classmethod
    def prepare_daily_analysis(cls, summary: dict[str, Any]) -> dict[str, Any]:
        """Attach deterministic day analysis used by daily and weekly consumers."""
        result = copy.deepcopy(summary)
        quality_sessions: list[dict[str, Any]] = []
        quality_gaps: list[dict[str, Any]] = []
        analyzed_sessions = 0

        def value_or_gap(value: Any, *, field: str, reason: str = "missing") -> dict[str, Any]:
            if value is None or value == "":
                return {"status": "gap", "value": None, "reason": reason, "field": field}
            return {"status": "available", "value": copy.deepcopy(value), "field": field}

        for session in result.get("sessions") or []:
            if not isinstance(session, dict) or not session.get("is_running"):
                continue
            analyzed_sessions += 1
            analysis = session.get("analysis") or {}
            primary_type = str(analysis.get("primary_type") or "unknown")
            primary_confidence = _number(analysis.get("confidence")) or 0.0
            classification = session.get("structure_classification") or {}
            structure_type = str(classification.get("structure_type") or "unknown")
            structure_confidence = _number(classification.get("confidence")) or 0.0
            quantity_reliable = bool(classification.get("quantity_reliable", True))
            work_recovery_groups = classification.get("work_recovery_groups") or []
            summary_fact = session.get("session_summary") or {}
            metrics = copy.deepcopy(session.get("metrics") or {})
            has_primary_evidence = any(
                metrics.get(key) is not None
                for key in ("pace_sec_per_km", "avg_heart_rate")
            )

            quality_type = ""
            confidence = 0.0
            evidence_source = ""
            evidence: list[dict[str, Any]] = []
            if (
                primary_type in _QUALITY_TYPES
                and primary_confidence >= 0.6
                and has_primary_evidence
            ):
                quality_type = primary_type
                confidence = primary_confidence
                evidence_source = "training_session_analysis"
                evidence = [{
                    "field": "analysis.primary_type",
                    "value": primary_type,
                    "confidence": round(primary_confidence, 2),
                }]
            elif (
                (
                    structure_type in _QUALITY_TYPES
                    or (structure_type == "mixed" and work_recovery_groups)
                )
                and structure_confidence >= 0.6
                and has_primary_evidence
            ):
                quality_type = structure_type
                confidence = structure_confidence
                evidence_source = "session_structure_classification"
                evidence = [{
                    "field": "structure_classification.structure_type",
                    "value": structure_type,
                    "confidence": round(structure_confidence, 2),
                    "alternations": int(classification.get("alternations") or 0),
                }]
            if not quality_type:
                if (
                    primary_type in _QUALITY_TYPES
                    or structure_type in _QUALITY_TYPES | {"mixed"}
                ):
                    quality_gaps.append({
                        "activity_id": str(session.get("activity_id") or ""),
                        "reason": "quality_evidence_below_threshold",
                        "confidence": round(max(primary_confidence, structure_confidence), 2),
                        "missing_primary_evidence": not has_primary_evidence,
                    })
                continue
            segment_sequence = summary_fact.get("segment_sequence") or []
            reliable_segment_count = len(segment_sequence) if quantity_reliable else None
            effect = summary_fact.get("effect")
            facts = {
                "distance_km": value_or_gap(session.get("distance_km") or None, field="distance_km"),
                "duration_minutes": value_or_gap(session.get("duration_minutes") or None, field="duration_minutes"),
                "average_pace": value_or_gap(metrics.get("pace_sec_per_km"), field="pace_sec_per_km"),
                "heart_rate": value_or_gap(metrics.get("avg_heart_rate"), field="avg_heart_rate"),
                "cadence": value_or_gap(metrics.get("cadence"), field="cadence"),
                "stride_length": value_or_gap(metrics.get("stride_length_cm"), field="stride_length_cm"),
                "reliable_segments": value_or_gap(
                    reliable_segment_count or None,
                    field="reliable_segment_count",
                    reason="quantity_unreliable" if not quantity_reliable else "missing",
                ),
                "work_recovery_groups": value_or_gap(work_recovery_groups or None, field="work_recovery_groups"),
                "training_effect": value_or_gap(effect, field="training_effect"),
            }
            quality_sessions.append({
                "date": str(result.get("date") or ""),
                "activity_id": str(session.get("activity_id") or ""),
                "name": str(session.get("name") or "跑步训练"),
                "quality_type": quality_type,
                "label": _QUALITY_LABELS.get(quality_type, quality_type),
                "confidence": round(confidence, 2),
                "evidence_source": evidence_source,
                "evidence": evidence,
                "fact_version": (
                    summary_fact.get("fact_version")
                    or result.get("fact_version")
                    or result.get("summary_version")
                ),
                "granularity": summary_fact.get("granularity"),
                "distance_km": session.get("distance_km"),
                "duration_minutes": session.get("duration_minutes"),
                "metrics": facts,
                "gaps": [
                    {"field": item["field"], "reason": item["reason"]}
                    for item in facts.values() if item["status"] == "gap"
                ],
            })

        status = (
            "ready"
            if (result.get("data_quality") or {}).get("status") == "sufficient"
            else "degraded"
        )
        source_versions = [
            str((session.get("session_summary") or {}).get("fact_version"))
            for session in result.get("sessions") or []
            if (session.get("session_summary") or {}).get("fact_version")
        ]
        analysis_digest = hashlib.sha256(json.dumps({
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "day_fact_version": result.get("fact_version") or result.get("summary_version"),
            "source_session_fact_versions": source_versions,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        result["daily_analysis"] = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_version": f"{ANALYSIS_SCHEMA_VERSION}:{analysis_digest}",
            "day_fact_version": result.get("fact_version") or result.get("summary_version"),
            "source_session_fact_versions": source_versions,
            "status": status,
            "analyzed_sessions": analyzed_sessions,
            "activity_analyses": [
                {
                    "activity_id": session.get("activity_id"),
                    "analysis": copy.deepcopy(session.get("analysis") or {}),
                    "structure_classification": copy.deepcopy(
                        session.get("structure_classification") or {}
                    ),
                }
                for session in result.get("sessions") or []
            ],
            "quality_session_count": len(quality_sessions),
            "quality_sessions": quality_sessions,
            "quality_candidate_gaps": quality_gaps,
            "source_summary_version": result.get("summary_version"),
            "coverage": copy.deepcopy(result.get("data_quality") or {}),
            "data_as_of": result.get("data_as_of"),
            "evidence": copy.deepcopy(result.get("evidence") or []),
            "gaps": copy.deepcopy(result.get("gaps") or []),
        }
        return result

    @classmethod
    def aggregate(
        cls,
        summaries: Iterable[dict[str, Any]],
        *,
        start: date | None = None,
        end: date | None = None,
        include_daily_summaries: bool = True,
    ) -> dict[str, Any]:
        selected = []
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            summary_date = _day(summary.get("date"))
            if summary_date is None or (start and summary_date < start) or (end and summary_date > end):
                continue
            selected.append(summary)
        total_distance = sum(float(item.get("total_distance_km") or 0) for item in selected)
        total_duration = sum(float(item.get("total_duration_minutes") or 0) for item in selected)
        feature_counts = Counter(
            str(feature.get("feature_code") or "")
            for item in selected
            for feature in item.get("observed_features") or []
            if feature.get("feature_code")
        )
        types = Counter(
            str(item.get("primary_type") or "unknown")
            for item in selected
            if item.get("primary_type") not in {None, "unknown"}
        )
        quality_sessions = []
        seen_quality_ids: set[str] = set()
        for item in selected:
            for session in (item.get("daily_analysis") or {}).get("quality_sessions") or []:
                activity_id = str(session.get("activity_id") or "")
                if activity_id and activity_id in seen_quality_ids:
                    continue
                if activity_id:
                    seen_quality_ids.add(activity_id)
                quality_sessions.append(copy.deepcopy(session))
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "window_start": str(start) if start else None,
            "window_end": str(end) if end else None,
            "day_count": len(selected),
            "training_days": sum(1 for item in selected if item.get("status") == "training"),
            "running_distance_km": round(total_distance, 1),
            "running_duration_minutes": round(total_duration),
            "primary_type_counts": dict(types),
            "feature_counts": dict(feature_counts),
            "summary_versions": [
                str(item.get("summary_version"))
                for item in selected if item.get("summary_version")
            ],
            "fact_versions": [
                str(item.get("fact_version"))
                for item in selected if item.get("fact_version")
            ],
            "quality_sessions": quality_sessions,
            "daily_analysis_statuses": {
                str(item.get("date")): str(
                    (item.get("daily_analysis") or {}).get("status") or "missing"
                )
                for item in selected
            },
            "gaps": [
                copy.deepcopy(gap)
                for item in selected for gap in item.get("gaps") or []
            ],
            "daily_summaries": copy.deepcopy(selected) if include_daily_summaries else [],
            "data_quality": {
                "status": "sufficient" if selected else "unknown",
                "source": "training_day_summary",
            },
        }
