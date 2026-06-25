"""Coros Provider — 直接调用 Coros Training Hub API。

API 端点: https://teamapi.coros.com
认证: email/password → access_token
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, timedelta
from typing import Any

from .base import (
    ActivityData, DailyHealth,
    AuthProvider, ActivityProvider, HealthProvider, DataProvider,
)

logger = logging.getLogger(__name__)

COROS_BASE = "https://teamapi.coros.com"


class CorosAuth(AuthProvider):
    """Coros 认证。"""

    def __init__(self):
        self._token: str | None = None
        self._user_id: int | None = None
        self._email = ""
        self._password = ""

    def login(self, email: str, password: str) -> bool:
        import httpx
        self._email = email
        self._password = password

        # Coros uses MD5 for password
        pwd_hash = hashlib.md5(password.encode()).hexdigest()

        # 判断账号类型：纯数字 → 手机号登录，否则邮箱登录
        is_phone = email.isdigit() and len(email) >= 10
        payload = {
            "password": pwd_hash,
            "accountType": 1 if is_phone else 2,
        }
        if is_phone:
            payload["mobile"] = email
            logger.info("Coros: 使用手机号登录 (%s)", email[:3] + "****" + email[-3:])
        else:
            payload["email"] = email

        try:
            r = httpx.post(
                f"{COROS_BASE}/api/v1/auth/login",
                json=payload,
                timeout=15,
            )
            if r.status_code != 200:
                logger.error("Coros 登录失败: %d %s", r.status_code, r.text[:200])
                return False

            data = r.json()
            if data.get("result") != "0000":
                logger.error("Coros 登录失败: %s", data.get("message", data))
                return False

            self._token = data["data"]["accessToken"]
            self._user_id = int(data["data"].get("userId", 0))
            logger.info("Coros: 登录成功 (user_id=%d)", self._user_id)
            return True
        except Exception as e:
            logger.error("Coros 登录异常: %s", e)
            return False

    def is_authenticated(self) -> bool:
        return self._token is not None

    def get_user_id(self) -> int:
        return self._user_id or 0

    def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        } if self._token else {}


class CorosActivity(ActivityProvider):
    """Coros 活动数据。"""

    def __init__(self, auth: CorosAuth):
        self._auth = auth

    def fetch_activities(self, start: date, end: date) -> list[ActivityData]:
        import httpx
        if not self._auth.is_authenticated():
            return []

        all_activities = []
        page = 1
        while True:
            try:
                r = httpx.get(
                    f"{COROS_BASE}/api/v1/activity/list",
                    headers=self._auth.get_headers(),
                    params={
                        "startDate": str(start),
                        "endDate": str(end),
                        "page": page,
                        "pageSize": 50,
                    },
                    timeout=15,
                )
                if r.status_code != 200:
                    break
                data = r.json()
                if data.get("result") != "0000":
                    break
                items = data.get("data", {}).get("list", [])
                if not items:
                    break

                for item in items:
                    mode_map = {"10": "running", "20": "cycling", "30": "swimming",
                                "50": "strength", "100": "walking"}
                    all_activities.append(ActivityData(
                        activity_id=str(item.get("id", "")),
                        activity_name=item.get("name", "训练"),
                        activity_type=mode_map.get(str(item.get("mode", "")), "other"),
                        start_time=item.get("startTime", ""),
                        duration_seconds=int(item.get("totalTime", 0) or 0),
                        distance_meters=float(item.get("totalDistance", 0) or 0),
                        avg_heart_rate=item.get("avgHeartRate"),
                        training_load=float(item.get("trainingLoad", 0) or 0),
                        calories=int(item.get("totalCalories", 0) or 0),
                        elevation_gain=float(item.get("totalAscent", 0) or 0),
                    ))
                page += 1
                if page > 10:
                    break
            except Exception as e:
                logger.warning("Coros 获取活动失败: %s", e)
                break

        logger.info("Coros: %d 条活动 (%s ~ %s)", len(all_activities), start, end)
        return all_activities

    def fetch_activity_detail(self, activity_id: str) -> dict[str, Any]:
        import httpx
        try:
            r = httpx.get(
                f"{COROS_BASE}/api/v1/activity/detail",
                headers=self._auth.get_headers(),
                params={"id": activity_id},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("result") == "0000":
                    return data.get("data", {})
        except Exception:
            pass
        return {}


class CorosHealth(HealthProvider):
    """Coros 健康数据。"""

    def __init__(self, auth: CorosAuth):
        self._auth = auth

    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        import httpx
        try:
            r = httpx.get(
                f"{COROS_BASE}/api/v1/health/daily",
                headers=self._auth.get_headers(),
                params={"date": str(target_date)},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("result") != "0000":
                return None
            d = data.get("data", {})
            if not d:
                return None

            sleep = d.get("sleep", {}) or {}
            hrv = d.get("hrv", {}) or {}
            hr = d.get("heartRate", {}) or {}
            stress = d.get("stress", {}) or {}
            steps = d.get("step", {}) or {}
            cal = d.get("calorie", {}) or {}

            return DailyHealth(
                metric_date=target_date,
                sleep_duration_hours=float(sleep.get("totalHours", 0) or 0),
                deep_sleep_hours=float(sleep.get("deepHours", 0) or 0),
                rem_sleep_hours=float(sleep.get("remHours", 0) or 0),
                deep_sleep_pct=float(sleep.get("deepPercent", 0) or 0),
                rem_sleep_pct=float(sleep.get("remPercent", 0) or 0),
                resting_heart_rate=hr.get("resting"),
                hrv_weekly_avg=hrv.get("weeklyAvg"),
                hrv_last_night_avg=hrv.get("lastNightAvg"),
                hrv_status=hrv.get("status", "balanced") or "balanced",
                avg_stress_level=stress.get("avgLevel"),
                total_steps=int(steps.get("total", 0) or 0),
                total_distance_meters=float(steps.get("distance", 0) or 0),
                total_calories=int(cal.get("total", 0) or 0),
                active_calories=int(cal.get("active", 0) or 0),
            )
        except Exception as e:
            logger.warning("Coros 获取健康数据失败 (%s): %s", target_date, e)
            return None

    def fetch_health_range(self, start: date, end: date) -> list[DailyHealth]:
        result = []
        d = start
        while d <= end:
            h = self.fetch_daily_health(d)
            if h:
                result.append(h)
            d += timedelta(days=1)
        return result


class CorosProvider(DataProvider):
    """Coros 数据源。"""

    def __init__(self, config):
        self._config = config
        self.auth = CorosAuth()
        self._activities = CorosActivity(self.auth)
        self._health = CorosHealth(self.auth)

    def authenticate(self) -> bool:
        return self.auth.login(self._config.email, self._config.password)

    @property
    def user_id(self) -> int:
        return self.auth.get_user_id()

    @property
    def activities(self) -> ActivityProvider:
        return self._activities

    @property
    def health(self) -> HealthProvider:
        return self._health
