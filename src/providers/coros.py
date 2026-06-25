"""Coros Provider — 基于 coros-mcp 库。

pip install git+https://github.com/cygnusb/coros-mcp.git
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from .base import (
    ActivityData, DailyHealth,
    AuthProvider, ActivityProvider, HealthProvider, DataProvider,
)

logger = logging.getLogger(__name__)


def _run(coro):
    """同步包装器。"""
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as ex:
            return ex.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


class CorosAuth(AuthProvider):
    def __init__(self):
        self._auth: Any = None

    def login(self, email: str, password: str) -> bool:
        from coros_mcp.coros_api import login as _login
        region = "cn" if (email.isdigit() and len(email) >= 10) else "eu"
        logger.info("Coros: 登录 (region=%s, account=%s...)", region, email[:3])
        try:
            self._auth = _run(_login(email, password, region, skip_mobile=True))
            logger.info("Coros: 登录成功 (user_id=%s)", self._auth.user_id)
            return True
        except Exception as e:
            logger.error("Coros 登录失败: %s", e)
            return False

    def is_authenticated(self) -> bool:
        return self._auth is not None

    def get_user_id(self) -> int:
        return int(self._auth.user_id) if self._auth else 0

    def get_headers(self) -> dict[str, str]:
        if self._auth is None:
            return {}
        import json as _json
        return {
            "accessToken": self._auth.access_token,
            "yfheader": _json.dumps({"userId": self._auth.user_id}),
        }


class CorosActivity(ActivityProvider):
    def __init__(self, auth: CorosAuth):
        self._auth = auth

    def fetch_activities(self, start: date, end: date) -> list[ActivityData]:
        from coros_mcp.coros_api import fetch_activities as _fetch
        if not self._auth.is_authenticated():
            return []
        try:
            raw = _run(_fetch(self._auth._auth, str(start), str(end)))
        except Exception as e:
            logger.warning("Coros 获取活动失败: %s", e)
            return []
        result = []
        for a in raw:
            d = a.__dict__ if hasattr(a, '__dict__') else (a if isinstance(a, dict) else {})
            result.append(ActivityData(
                activity_id=str(d.get("id", d.get("labelId", ""))),
                activity_name=d.get("name", d.get("title", "训练")),
                activity_type=str(d.get("sportType", d.get("mode", "100"))),
                start_time=str(d.get("startTime", "")),
                duration_seconds=int(d.get("totalTime", 0) or 0),
                distance_meters=float(d.get("totalDistance", 0) or 0),
                avg_heart_rate=d.get("avgHeartRate"),
                training_load=float(d.get("trainingLoad", 0) or 0),
                calories=int(d.get("totalCalories", 0) or 0),
                elevation_gain=float(d.get("totalAscent", 0) or 0),
            ))
        logger.info("Coros: %d 条活动 (%s ~ %s)", len(result), start, end)
        return result

    def fetch_activity_detail(self, activity_id: str) -> dict[str, Any]:
        from coros_mcp.coros_api import fetch_activity_detail as _detail
        try:
            raw = _run(_detail(self._auth._auth, str(activity_id), 10))
            return raw.__dict__ if hasattr(raw, '__dict__') else (raw if isinstance(raw, dict) else {})
        except Exception:
            return {}


class CorosHealth(HealthProvider):
    def __init__(self, auth: CorosAuth):
        self._auth = auth

    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        ts = str(target_date)

        # Try fetch_daily_records first (combines sleep + activity data)
        try:
            from coros_mcp.coros_api import fetch_daily_records as _fetch
            records = _run(_fetch(self._auth._auth, ts, ts))
            if records:
                r = records[0]
                d = r.__dict__ if hasattr(r, '__dict__') else (r if isinstance(r, dict) else {})
                return DailyHealth(
                    metric_date=target_date,
                    sleep_duration_hours=float(getattr(r, 'sleep_total_hours', 0) or 0),
                    deep_sleep_hours=float(getattr(r, 'sleep_deep_hours', 0) or 0),
                    rem_sleep_hours=float(getattr(r, 'sleep_rem_hours', 0) or 0),
                    resting_heart_rate=getattr(r, 'resting_heart_rate', None),
                    hrv_weekly_avg=getattr(r, 'hrv_weekly_avg', None),
                    hrv_last_night_avg=getattr(r, 'hrv_last_night_avg', None),
                    hrv_status=getattr(r, 'hrv_status', "balanced") or "balanced",
                    total_steps=int(getattr(r, 'total_steps', 0) or 0),
                    total_distance_meters=float(getattr(r, 'total_distance', 0) or 0),
                    total_calories=int(getattr(r, 'total_calories', 0) or 0),
                    active_calories=int(getattr(r, 'active_calories', 0) or 0),
                )
        except Exception:
            pass

        # Fallback: HRV-only data
        try:
            from coros_mcp.coros_api import fetch_hrv
            records = _run(fetch_hrv(self._auth._auth))
            ts_short = target_date.strftime("%Y%m%d")
            for r in records:
                r_date = str(getattr(r, 'date', ''))
                if r_date == ts_short:
                    return DailyHealth(
                        metric_date=target_date,
                        hrv_weekly_avg=getattr(r, 'avg_sleep_hrv', None),
                        hrv_last_night_avg=getattr(r, 'avg_sleep_hrv', None),
                        hrv_status="balanced",
                    )
        except Exception:
            pass

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
