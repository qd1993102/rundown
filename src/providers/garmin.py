"""Garmin Provider — 封装现有 garmy 库。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from .base import (
    ActivityData, DailyHealth,
    AuthProvider, ActivityProvider, HealthProvider, DataProvider,
)

logger = logging.getLogger(__name__)


class GarminAuth(AuthProvider):
    """Garmin 认证（封装 garmy AuthClient）。"""

    def __init__(self, domain: str = "garmin.com", token_dir: str = "~/.garmy"):
        self._domain = domain
        self._token_dir = token_dir
        self._client = None
        self._user_id: int | None = None

    def login(self, email: str, password: str) -> bool:
        from garmy import AuthClient
        self._client = AuthClient(domain=self._domain, token_dir=self._token_dir)
        try:
            if self._client.is_authenticated:
                logger.info("Garmin: Token 有效")
                return True
        except Exception:
            pass

        logger.info("Garmin: 执行登录...")
        try:
            result = self._client.login(email=email, password=password,
                                        prompt_mfa=lambda: input("MFA: ").strip(),
                                        return_on_mfa=True)
            if isinstance(result, tuple) and result[0] == "needs_mfa":
                code = input("MFA 验证码: ").strip()
                self._client.resume_login(code, result[1])
            logger.info("Garmin: 登录成功")
            return True
        except Exception as e:
            logger.error("Garmin 登录失败: %s", e)
            return False

    def is_authenticated(self) -> bool:
        if self._client is None:
            return False
        try:
            return self._client.is_authenticated
        except Exception:
            return False

    def get_user_id(self) -> int:
        if self._user_id is not None:
            return self._user_id
        from garmy import APIClient
        api = APIClient(auth_client=self._client)
        profile = api.profile
        if isinstance(profile, dict):
            self._user_id = int(profile.get("id", 0))
        return self._user_id or 0

    def get_headers(self) -> dict[str, str]:
        return self._client.get_auth_headers() if self._client else {}


class GarminActivity(ActivityProvider):
    """Garmin 活动数据。"""

    def __init__(self, auth: GarminAuth):
        self._auth = auth
        self._api = None

    def _get_api(self):
        if self._api is None:
            from garmy import APIClient
            self._api = APIClient(auth_client=self._auth._client)
        return self._api

    def fetch_activities(self, start: date, end: date) -> list[ActivityData]:
        api = self._get_api()
        aa = api.metrics.get("activities")
        if aa is None:
            return []
        days = (end - start).days + 1
        # Use raw API response to get distance/calories (ActivitySummary doesn't include them)
        raw_list = aa.raw(limit=200)
        result = []
        for a in (raw_list or []):
            st = a.get("startTimeLocal", "") or ""
            adate = st[:10] if st else ""
            if str(start) <= adate <= str(end):
                atype = a.get("activityType", {}) or {}
                result.append(ActivityData(
                    activity_id=str(a.get("activityId", "")),
                    activity_name=a.get("activityName", "") or "",
                    activity_type=atype.get("typeKey", "unknown") if isinstance(atype, dict) else "unknown",
                    start_time=st,
                    duration_seconds=int(a.get("duration", 0) or 0),
                    distance_meters=float(a.get("distance", 0) or 0),
                    avg_heart_rate=a.get("averageHR"),
                    max_heart_rate=a.get("maxHR"),
                    training_load=float(a.get("activityTrainingLoad", 0) or 0),
                    calories=int(a.get("calories", 0) or 0),
                    elevation_gain=float(a.get("elevationGain", 0) or 0),
                ))
        return result

    def fetch_activity_detail(self, activity_id: str) -> dict[str, Any]:
        api = self._get_api()
        try:
            return api.connectapi(f"/activity-service/activity/{activity_id}")
        except Exception:
            return {}


class GarminHealth(HealthProvider):
    """Garmin 健康数据（通过 SyncManager 存储，从 SQLite 读取）。"""

    def __init__(self, storage):
        self._storage = storage

    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        """从 SQLite 读取（需先 sync）。"""
        raw = self._storage.get_health_metrics(0, target_date)  # user_id ignored in call
        if not raw:
            return None
        return DailyHealth(
            metric_date=target_date,
            sleep_duration_hours=float(raw.get("sleep_duration_hours", 0) or 0),
            deep_sleep_hours=float(raw.get("deep_sleep_hours", 0) or 0),
            rem_sleep_hours=float(raw.get("rem_sleep_hours", 0) or 0),
            deep_sleep_pct=float(raw.get("deep_sleep_percentage", 0) or 0),
            rem_sleep_pct=float(raw.get("rem_sleep_percentage", 0) or 0),
            resting_heart_rate=raw.get("resting_heart_rate"),
            hrv_weekly_avg=raw.get("hrv_weekly_avg"),
            hrv_last_night_avg=raw.get("hrv_last_night_avg"),
            hrv_status=raw.get("hrv_status", "balanced") or "balanced",
            avg_stress_level=raw.get("avg_stress_level"),
            body_battery_high=raw.get("body_battery_high"),
            body_battery_low=raw.get("body_battery_low"),
            total_steps=int(raw.get("total_steps", 0) or 0),
            total_distance_meters=float(raw.get("total_distance_meters", 0) or 0),
            total_calories=int(raw.get("total_calories", 0) or 0),
            active_calories=int(raw.get("active_calories", 0) or 0),
            training_readiness_score=raw.get("training_readiness_score"),
            training_readiness_level=raw.get("training_readiness_level", "") or "",
        )

    def fetch_health_range(self, start: date, end: date) -> list[DailyHealth]:
        result = []
        d = start
        while d <= end:
            h = self.fetch_daily_health(d)
            if h:
                result.append(h)
            d += timedelta(days=1)
        return result


class GarminProvider(DataProvider):
    """Garmin 数据源。"""

    def __init__(self, config):
        self._config = config
        self.auth = GarminAuth(domain=config.domain, token_dir=config.token_dir)
        self._activity_provider = GarminActivity(self.auth)
        self._health_provider: GarminHealth | None = None

    def authenticate(self) -> bool:
        return self.auth.login(self._config.email, self._config.password)

    @property
    def user_id(self) -> int:
        return self.auth.get_user_id()

    @property
    def activities(self) -> ActivityProvider:
        return self._activity_provider

    @property
    def health(self) -> HealthProvider:
        if self._health_provider is None:
            from ..storage import Storage
            storage = Storage(self._config)
            self._health_provider = GarminHealth(storage)
        return self._health_provider
