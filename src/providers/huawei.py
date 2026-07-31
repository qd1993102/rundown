"""Huawei authentication through the CrewPals per-user token service."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any

import httpx

from ..local_files import (
    atomic_write_private,
    ensure_private_dir,
    read_private_text,
    restrict_private_file,
)
from .base import ActivityData, ActivityProvider, AuthProvider, DailyHealth, DataProvider, HealthProvider

logger = logging.getLogger(__name__)
TOKEN_URL = "https://api.crewpals.com/api/v1/huawei/access_token"
HEALTH_API_URL = "https://health-api.cloud.huawei.com/healthkit/v2"


def _epoch_millis(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number // 1000 if number > 10_000_000_000_000 else number


def _iso_time(value: Any) -> str:
    millis = _epoch_millis(value)
    if millis is None:
        return str(value or "")
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def _find_value(data: Any, names: tuple[str, ...]) -> Any:
    """递归读取 Huawei 响应中的同义字段。"""
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            if value not in (None, ""):
                return value
        for value in data.values():
            found = _find_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_value(value, names)
            if found not in (None, ""):
                return found
    return None


def _number(data: Any, names: tuple[str, ...], default: float = 0) -> float:
    value = _find_value(data, names)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _record_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("activityRecord", "activityRecords", "records", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _record_list(value)
            if nested:
                return nested
    return []


def _summary_value(record: dict[str, Any], data_type: str, field_name: str) -> float:
    summaries = record.get("activitySummary", {}).get("dataSummary", [])
    for summary in summaries if isinstance(summaries, list) else []:
        if not isinstance(summary, dict) or data_type not in str(summary.get("dataTypeName", "")):
            continue
        for value in summary.get("value", []) or []:
            if isinstance(value, dict) and value.get("fieldName") == field_name:
                return _number(value, ("floatValue", "integerValue", "longValue", "value"))
    return 0


def _optional_number(data: Any, names: tuple[str, ...]) -> float | None:
    value = _find_value(data, names)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_summary_value(
    record: dict[str, Any], data_type: str, field_name: str,
) -> float | None:
    summaries = record.get("activitySummary", {}).get("dataSummary", [])
    for summary in summaries if isinstance(summaries, list) else []:
        if not isinstance(summary, dict) or data_type not in str(summary.get("dataTypeName", "")):
            continue
        for value in summary.get("value", []) or []:
            if isinstance(value, dict) and value.get("fieldName") == field_name:
                return _optional_number(
                    value, ("floatValue", "integerValue", "longValue", "value")
                )
    return None


class HuaweiAuth(AuthProvider):
    def __init__(self, config):
        self.config = config
        self.token: dict[str, Any] | None = None
        self.token_path = Path(config.huawei_token_dir) / "huawei-oauth.json"

    def ensure_token_store(self) -> bool:
        """创建私有 token 目录，并检查已有 token 文件。

        返回 token 文件是否已存在。空 token 文件没有有效语义，因此不会预创建。
        """
        token_dir = self.token_path.parent
        ensure_private_dir(token_dir)
        if not self.token_path.exists():
            return False
        restrict_private_file(self.token_path)
        try:
            token = json.loads(read_private_text(self.token_path))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Huawei token 文件无效: {self.token_path}") from exc
        if not isinstance(token, dict) or not token.get("access_token"):
            raise ValueError(f"Huawei token 文件缺少 access_token: {self.token_path}")
        return True

    def _load(self) -> None:
        if not self.token_path.exists():
            self.token = None
            return
        try:
            self.token = json.loads(read_private_text(self.token_path))
        except ValueError:
            self.token = None

    def _save(self, token: dict[str, Any]) -> None:
        token = dict(token)
        if not token.get("expires_at") and not token.get("expired_at"):
            token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
        self.ensure_token_store()
        atomic_write_private(
            self.token_path,
            json.dumps(token, ensure_ascii=False, indent=2),
        )
        self.token = token

    def _fetch_access_token(self) -> bool:
        response = httpx.get(TOKEN_URL, headers={
            "Authorization": self.config.group_pals_token,
            "Accept": "application/json",
        }, timeout=30)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError(
                    "CrewPals Huawei AT 接口返回 404，请确认服务端已部署 "
                    f"{TOKEN_URL}"
                ) from exc
            raise
        payload = response.json()
        token = payload.get("data", payload) if isinstance(payload, dict) else {}
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200, "200"):
            raise RuntimeError(f"CrewPals 获取 Huawei AT 失败: {payload.get('msg', 'unknown error')}")
        if isinstance(token, dict):
            token = dict(token)
            aliases = {
                "accessToken": "access_token",
                "refreshToken": "refresh_token",
                "expiredAt": "expired_at",
                "openId": "open_id",
                "userId": "user_id",
            }
            for source, target in aliases.items():
                value = token.pop(source, None)
                if value is not None and target not in token:
                    token[target] = value
            token.pop("@type", None)
        if not token.get("access_token"):
            raise RuntimeError(f"CrewPals 未返回 Huawei access_token: {token.get('error', 'unknown error')}")
        if not token.get("expired_at") and not token.get("expires_at"):
            raise RuntimeError("CrewPals 返回值缺少 expired_at")
        self._save(token)
        if not self.is_authenticated():
            raise RuntimeError("CrewPals 返回的 Huawei access_token 已过期")
        return True

    def login(self, email: str = "", password: str = "") -> bool:
        del email, password
        self.ensure_token_store()
        self._load()
        if self.is_authenticated():
            return True
        return self._fetch_access_token()

    def is_authenticated(self) -> bool:
        if not self.token or not self.token.get("access_token"):
            return False
        expires_at = self.token.get("expires_at") or self.token.get("expired_at") or 0
        try:
            return int(expires_at) > time.time() + 60
        except (TypeError, ValueError):
            return False

    def get_user_id(self) -> int:
        token = self.token or {}
        if token.get("user_id") is not None:
            try:
                return int(token["user_id"])
            except (TypeError, ValueError):
                pass
        subject = str(token.get("open_id") or token.get("openid") or token.get("sub") or self.config.group_pals_token)
        return int.from_bytes(hashlib.sha256(subject.encode()).digest()[:7], "big")

    def get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token['access_token']}"} if self.is_authenticated() else {}


class HuaweiActivities(ActivityProvider):
    def __init__(self, auth: HuaweiAuth):
        self._auth = auth

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._auth.is_authenticated() and not self._auth.login():
            raise RuntimeError("Huawei 认证失败")
        response = httpx.get(
            f"{HEALTH_API_URL}{endpoint}",
            headers={**self._auth.get_headers(), "Accept": "application/json"},
            params=params,
            timeout=30,
        )
        if response.status_code == 401:
            self._auth._fetch_access_token()
            response = httpx.get(
                f"{HEALTH_API_URL}{endpoint}",
                headers={**self._auth.get_headers(), "Accept": "application/json"},
                params=params,
                timeout=30,
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def fetch_activities(self, start: date, end: date) -> list[ActivityData]:
        start_ms = int(datetime.combine(start, datetime_time.min).timestamp() * 1000)
        end_ms = int(datetime.combine(end, datetime_time.max).timestamp() * 1000)
        payload = self._get("/activityRecords", {
            "startTime": start_ms,
            "endTime": end_ms,
        })
        activities = []
        for record in _record_list(payload):
            start_value = _find_value(record, ("startTime", "start_time"))
            end_value = _find_value(record, ("endTime", "end_time"))
            start_epoch = _epoch_millis(start_value)
            end_epoch = _epoch_millis(end_value)
            active_time = record.get("activeTime")
            duration = int(float(active_time) / 1000) if active_time not in (None, "") else 0
            if not duration:
                try:
                    duration = int(float(record.get("duration") or record.get("durationSeconds") or 0))
                except (TypeError, ValueError):
                    duration = 0
            if not duration and start_epoch is not None and end_epoch is not None:
                duration = max(0, (end_epoch - start_epoch) // 1000)
            activity_id = str(_find_value(record, (
                "activityRecordId", "recordId", "activityId", "id",
            )) or "")
            if not activity_id:
                canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
                activity_id = hashlib.sha256(canonical.encode()).hexdigest()[:24]
            avg_hr = _summary_value(record, "heart_rate.statistics", "avg") or _number(
                record, ("averageHeartRate", "avgHeartRate", "averageHR", "avg_hr"))
            max_hr = _summary_value(record, "heart_rate.statistics", "max") or _number(
                record, ("maxHeartRate", "maximumHeartRate", "maxHR", "max_hr"))
            activities.append(ActivityData(
                activity_id=activity_id,
                activity_name=str(_find_value(record, ("activityName", "name", "title")) or "华为训练"),
                activity_type=str(_find_value(record, ("activityType", "activityTypeName", "type")) or "unknown"),
                start_time=_iso_time(start_value),
                duration_seconds=duration,
                distance_meters=_summary_value(record, "distance.total", "distance") or _number(
                    record, ("distance", "distanceMeters", "totalDistance")),
                avg_heart_rate=int(avg_hr) if avg_hr else None,
                max_heart_rate=int(max_hr) if max_hr else None,
                training_load=_number(record, ("trainingLoad", "activityTrainingLoad")),
                calories=int(_summary_value(record, "calories.burnt.total", "calories_total") or _number(
                    record, ("calories", "totalCalories", "calorie"))),
                elevation_gain=(
                    summary_elevation
                    if (summary_elevation := _optional_summary_value(
                        record, "altitude.statistics", "ascent_total",
                    )) is not None
                    else _optional_number(
                        record, ("elevationGain", "totalClimb", "climb"),
                    )
                ),
                has_gps=bool(_find_value(record, ("route", "track", "longitude", "latitude"))),
                extra={"provider": "huawei", "raw": record},
            ))
        logger.info("Huawei: %d 条活动 (%s ~ %s)", len(activities), start, end)
        return activities

    def fetch_activity_detail(self, activity_id: str) -> dict[str, Any]:
        payload = self._get("/activityRecords", {"activityRecordId": activity_id})
        for record in _record_list(payload):
            record_id = _find_value(record, ("activityRecordId", "recordId", "activityId", "id"))
            if str(record_id or "") == str(activity_id):
                return record
        direct_id = _find_value(payload, ("activityRecordId", "recordId", "activityId", "id"))
        return payload if str(direct_id or "") == str(activity_id) else {}


class HuaweiHealth(HealthProvider):
    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        return None

    def fetch_health_range(self, start: date, end: date) -> list[DailyHealth]:
        return []


class HuaweiProvider(DataProvider):
    def __init__(self, config):
        self.auth = HuaweiAuth(config)
        self._activities = HuaweiActivities(self.auth)
        self._health = HuaweiHealth()

    def authenticate(self) -> bool:
        return self.auth.login()

    @property
    def user_id(self) -> int:
        return self.auth.get_user_id()

    @property
    def activities(self) -> ActivityProvider:
        return self._activities

    @property
    def health(self) -> HealthProvider:
        return self._health
