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

# Region-specific base URLs (ref: coros-mcp)
_BASE_URLS = {
    "eu": "https://teameuapi.coros.com", "us": "https://teamapi.coros.com",
    "cn": "https://teamcnapi.coros.com", "asia": "https://teamcnapi.coros.com",
}


def _base_for_auth(auth) -> str:
    if hasattr(auth, '_auth') and hasattr(auth._auth, 'region'):
        return _BASE_URLS.get(auth._auth.region, _BASE_URLS["us"])
    return _BASE_URLS["us"]


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
        # Coros API requires YYYYMMDD format (no hyphens)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        try:
            raw, total = _run(_fetch(self._auth._auth, start_str, end_str))
        except Exception as e:
            logger.warning("Coros 获取活动失败: %s", e)
            return []
        result = []
        for a in (raw or []):
            # coros-mcp ActivitySummary has: activity_id, name, sport_type,
            # start_time (unix ts, may be int or str), duration_seconds, etc.
            ts = getattr(a, 'start_time', 0) or 0
            try:
                from datetime import datetime
                # coros-mcp may return start_time as int or str — coerce to int
                ts_int = int(ts) if isinstance(ts, str) else ts
                start_str = datetime.fromtimestamp(ts_int).strftime("%Y-%m-%d %H:%M:%S") if ts_int > 100000 else str(ts_int)
            except Exception:
                start_str = str(ts)
            sport_map = {"100": "running", "101": "running_indoor", "200": "cycling",
                         "300": "swimming", "500": "strength"}
            sport_type = str(getattr(a, 'sport_type', 100))
            result.append(ActivityData(
                activity_id=str(getattr(a, 'activity_id', '')),
                activity_name=getattr(a, 'name', '训练') or '训练',
                activity_type=sport_map.get(sport_type, sport_type),
                start_time=start_str,
                duration_seconds=int(getattr(a, 'duration_seconds', 0) or 0),
                distance_meters=float(getattr(a, 'distance_meters', 0) or 0),
                avg_heart_rate=getattr(a, 'avg_hr', None),
                training_load=float(getattr(a, 'training_load', 0) or 0),
                calories=int(getattr(a, 'calories', 0) or 0),
                elevation_gain=float(getattr(a, 'elevation_gain', 0) or 0),
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
        self._analyse_cache: dict | None = None
        self._hrv_cache: list | None = None
        # Note: cache persists for process lifetime — fine for CLI (short-lived),
        # but MCP server may need restart to pick up new Coros data.

    def _get_analyse_data(self) -> dict:
        """获取 analyse/query 数据（缓存）。"""
        if self._analyse_cache is not None:
            return self._analyse_cache
        import httpx
        try:
            r = httpx.get(
                f"{_base_for_auth(self._auth)}/analyse/query",
                headers=self._auth.get_headers(),
                timeout=15,
            )
            if r.status_code == 200 and r.json().get("result") == "0000":
                self._analyse_cache = r.json().get("data", {})
        except Exception:
            self._analyse_cache = {}
        return self._analyse_cache or {}

    def _get_hrv_data(self) -> list:
        """获取 HRV 数据（缓存）。"""
        if self._hrv_cache is not None:
            return self._hrv_cache
        try:
            from coros_mcp.coros_api import fetch_hrv
            self._hrv_cache = _run(fetch_hrv(self._auth._auth))
        except Exception:
            self._hrv_cache = []
        return self._hrv_cache or []

    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        # Build from analyse data (RHR, load, distance, duration)
        analyse = self._get_analyse_data()
        day_list = analyse.get("dayList", []) or []
        t7_list = analyse.get("t7dayList", []) or []

        rhr = None
        distance = 0.0
        duration = 0.0
        training_load = 0.0
        stress_level = None  # Coros: tiredRate

        ts_compact = target_date.strftime("%Y%m%d")
        for item in day_list:
            if str(item.get("happenDay", "")) == ts_compact:
                rhr = item.get("rhr")
                distance = float(item.get("distance", 0) or 0)
                duration = float(item.get("duration", 0) or 0)
                training_load = float(item.get("trainingLoad", 0) or 0)
                stress_level = item.get("tiredRate")  # 0-100 fatigue index
                break

        # Check t7dayList for RHR if not found
        if rhr is None:
            for item in t7_list:
                if str(item.get("happenDay", "")) == ts_compact:
                    rhr = item.get("rhr")
                    break

        # HRV from dashboard
        hrv_val = None
        hrv_status = "balanced"
        for r in self._get_hrv_data():
            if str(getattr(r, 'date', '')) == ts_compact:
                hrv_val = getattr(r, 'avg_sleep_hrv', None)
                break

        # If we have at least HRV or RHR, return data
        if rhr is not None or hrv_val is not None or distance > 0:
            return DailyHealth(
                metric_date=target_date,
                resting_heart_rate=rhr,
                hrv_last_night_avg=hrv_val,
                hrv_weekly_avg=hrv_val,
                hrv_status=hrv_status,
                total_distance_meters=distance,
                total_steps=0,
                avg_stress_level=stress_level,
            )

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
