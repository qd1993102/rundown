"""Provider detail adapters for the canonical activity-detail contract.

Only this module knows third-party field names and units.  Storage, summary
extraction, reports, weekly reviews and planning consume the returned canonical
shape without provider conditionals.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any


DetailMatcher = Callable[[dict[str, Any]], bool]
DetailNormalizer = Callable[[dict[str, Any]], dict[str, Any]]
_ADAPTERS: list[tuple[str, DetailMatcher, DetailNormalizer]] = []


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def register_detail_adapter(
    name: str, matcher: DetailMatcher, normalizer: DetailNormalizer,
) -> None:
    """Register or replace one provider adapter; consumers remain unchanged."""
    _ADAPTERS[:] = [adapter for adapter in _ADAPTERS if adapter[0] != name]
    # Explicit/new adapters take precedence over the Coros legacy-shape fallback.
    _ADAPTERS.insert(0, (name, matcher, normalizer))


def normalize_activity_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical copy selected by the first matching adapter."""
    if not isinstance(detail, dict):
        return {}
    if detail.get("_neurun_canonical_detail"):
        return copy.deepcopy(detail)
    for name, matcher, normalizer in _ADAPTERS:
        if matcher(detail):
            result = normalizer(detail)
            result["_neurun_provider"] = name
            result["_neurun_canonical_detail"] = True
            return result
    result = copy.deepcopy(detail)
    result["_neurun_canonical_detail"] = True
    return result


def _is_coros(detail: dict[str, Any]) -> bool:
    summary = detail.get("summary")
    return bool(
        isinstance(summary, dict)
        and (
            isinstance(detail.get("frequencyList"), list)
            or summary.get("sportType") is not None
            or (_number(summary.get("distance")) or 0) >= 10000
        )
    )


def _normalize_coros(detail: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(detail)
    summary = normalized.get("summary") or {}

    def normalize_row(row: dict[str, Any], *, summary_row: bool = False) -> None:
        distance = _number(row.get("distance"))
        if distance is not None:
            row["distance"] = distance / 100.0
        duration = _number(row.get("workoutTime" if summary_row else "time"))
        if duration is None:
            duration = _number(row.get("totalTime" if summary_row else "totalLength"))
        if duration is not None:
            row["duration"] = duration / 100.0
        if summary_row:
            elapsed = _number(row.get("totalTime"))
            if elapsed is not None:
                row["elapsedDuration"] = elapsed / 100.0
            calories = _number(row.get("calories"))
            if calories is None:
                calories = _number(row.get("calorie"))
            if calories is not None:
                row["calories"] = calories / 1000.0
        for target, source in {
            "averageHR": "avgHr", "maxHR": "maxHr",
            "averageRunCadence": "avgCadence", "maxRunCadence": "maxCadence",
            "strideLength": "avgStrideLength", "trainingLoad": "trainingLoad",
            "elevationGain": "elevGain", "elevationLoss": "totalDescent",
        }.items():
            if row.get(source) is not None:
                row[target] = row[source]
        pace = _number(row.get("avgPace")) or _number(row.get("avgSpeed"))
        if pace and pace > 0:
            row["pace"] = pace

    normalize_row(summary, summary_row=True)
    fastest_pace = _number(summary.get("maxSpeed"))
    if fastest_pace and fastest_pace > 0:
        summary["fastestPace"] = fastest_pace
        summary["maxSpeed"] = None
    normalized["summary"] = summary

    for lap in normalized.get("lapList") or []:
        if not isinstance(lap, dict):
            continue
        items = lap.get("lapItemList") or []
        if isinstance(items, list) and items:
            for item in items:
                if isinstance(item, dict):
                    normalize_row(item)
        else:
            normalize_row(lap)
    leaf_groups = [
        lap for lap in (normalized.get("lapList") or [])
        if isinstance(lap, dict) and isinstance(lap.get("lapItemList"), list)
        and lap.get("lapItemList")
    ]
    if leaf_groups:
        normalized["lapList"] = [max(
            leaf_groups, key=lambda lap: len(lap.get("lapItemList") or [])
        )]

    metrics: list[dict[str, Any]] = []
    previous_timestamp: float | None = None
    for point in normalized.get("frequencyList") or []:
        if not isinstance(point, dict):
            continue
        timestamp = _number(point.get("timestamp"))
        duration_s = 1.0
        if timestamp is not None and previous_timestamp is not None:
            delta = (timestamp - previous_timestamp) / 100.0
            if 0 < delta <= 10:
                duration_s = delta
        if timestamp is not None:
            previous_timestamp = timestamp
        pace = _number(point.get("speed"))
        heart = _number(point.get("heart"))
        cadence = _number(point.get("cadence"))
        if not any(value and value > 0 for value in (pace, heart, cadence)):
            continue
        metrics.append({
            "duration": duration_s,
            "pace_sec_per_km": pace if pace and 120 <= pace <= 1200 else None,
            "heartRate": heart if heart and heart > 0 else None,
            "cadence": cadence if cadence and cadence > 0 else None,
        })
    if metrics:
        normalized["activity_detail_metrics"] = metrics
    return normalized


register_detail_adapter("coros", _is_coros, _normalize_coros)
