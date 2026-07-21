"""Coros Provider — 基于 coros-mcp 库。

pip install git+https://github.com/cygnusb/coros-mcp.git
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
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


def _int_value(value: Any) -> int:
    """将 Coros 的数字/字符串字段安全转为整数。"""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_activity_item(item: dict[str, Any]) -> ActivityData:
    """将 Coros 原始活动映射为统一模型，运动时长排除暂停时间。"""
    total_time = _int_value(item.get("totalTime"))
    workout_time = _int_value(item.get("workoutTime"))
    if workout_time > 0 and (total_time <= 0 or workout_time <= total_time):
        active_time = workout_time
    else:
        active_time = total_time

    timestamp = _int_value(item.get("startTime"))
    start_time = (
        datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if timestamp > 100000 else str(timestamp)
    )
    sport_type = str(item.get("sportType", 100))
    sport_map = {
        "100": "running", "101": "running_indoor", "200": "cycling",
        "300": "swimming", "500": "strength",
    }

    return ActivityData(
        activity_id=str(item.get("labelId", "")),
        activity_name=item.get("name") or item.get("remark") or "训练",
        activity_type=sport_map.get(sport_type, sport_type),
        start_time=start_time,
        duration_seconds=active_time,
        distance_meters=float(item.get("distance") or item.get("totalDistance") or 0),
        avg_heart_rate=item.get("avgHr"),
        max_heart_rate=item.get("maxHr"),
        training_load=float(item.get("trainingLoad") or 0),
        calories=_int_value(item.get("calorie")),
        elevation_gain=float(
            item.get("ascent") or item.get("totalAscent")
            or item.get("elevationGain") or 0
        ),
        extra={
            "provider": "coros",
            "total_time_seconds": total_time,
            "workout_time_seconds": workout_time,
            "paused_seconds": max(total_time - active_time, 0),
        },
    )


def _fetch_activity_items(auth: CorosAuth, start: date, end: date) -> list[dict[str, Any]]:
    """直接读取 Coros 活动列表原始字段，保留 coros-mcp 丢弃的 workoutTime。"""
    import httpx

    items: list[dict[str, Any]] = []
    page = 1
    size = 100
    with httpx.Client(timeout=30) as client:
        while True:
            response = client.get(
                f"{_base_for_auth(auth)}/activity/query",
                params={
                    "startDay": start.strftime("%Y%m%d"),
                    "endDay": end.strftime("%Y%m%d"),
                    "pageNumber": page,
                    "size": size,
                },
                headers=auth.get_headers(),
            )
            response.raise_for_status()
            body = response.json()
            if body.get("result") != "0000":
                raise RuntimeError(
                    f"{body.get('message', 'unknown error')} "
                    f"(result={body.get('result', 'unknown')})"
                )
            data = body.get("data") or {}
            page_items = data.get("dataList", data.get("list", [])) or []
            items.extend(page_items)
            total = _int_value(data.get("totalCount") or data.get("count"))
            if not page_items or len(page_items) < size or (total and len(items) >= total):
                break
            page += 1
    return items


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
    """Coros 认证，并按 neurun 用户目录隔离持久化 token。"""

    _TOKEN_FILENAME = "coros-auth.json"

    def __init__(self, token_dir: str | None = None):
        self._auth: Any = None
        self._token_path = (
            Path(token_dir).expanduser() / self._TOKEN_FILENAME
            if token_dir else None
        )
        self._restore()

    def _restore(self) -> bool:
        """从用户专属 token 文件恢复认证，不读取 coros-mcp 的全局凭证。"""
        if self._token_path is None or not self._token_path.exists():
            return False
        try:
            from coros_mcp.models import StoredAuth
            raw = self._token_path.read_text(encoding="utf-8")
            if hasattr(StoredAuth, "model_validate_json"):
                self._auth = StoredAuth.model_validate_json(raw)
            else:  # pragma: no cover - pydantic v1 compatibility
                self._auth = StoredAuth.parse_raw(raw)
            os.chmod(self._token_path, stat.S_IRUSR | stat.S_IWUSR)
            return True
        except Exception as exc:
            self._auth = None
            logger.warning("Coros token 恢复失败，请重新绑定账号: %s", exc)
            return False

    def _save(self) -> None:
        """以 0700 目录、0600 文件权限保存当前用户的 Coros token。"""
        if self._token_path is None or self._auth is None:
            return
        self._token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._token_path.parent, stat.S_IRWXU)
        if hasattr(self._auth, "model_dump_json"):
            raw = self._auth.model_dump_json()
        else:  # pragma: no cover - pydantic v1 compatibility
            raw = self._auth.json()
        self._token_path.write_text(raw, encoding="utf-8")
        os.chmod(self._token_path, stat.S_IRUSR | stat.S_IWUSR)

    def migrate_legacy_token(self) -> bool:
        """将 coros-mcp 旧版全局 token 迁移到当前用户目录。"""
        if self.is_authenticated():
            return True
        try:
            from coros_mcp.coros_api import get_stored_auth
            legacy_auth = get_stored_auth()
            if (legacy_auth is None or not legacy_auth.access_token
                    or not legacy_auth.user_id):
                return False
            self._auth = legacy_auth
            self._save()
            logger.info("Coros: 已将旧版全局 token 迁移到用户目录")
            return True
        except Exception as exc:
            self._auth = None
            logger.warning("Coros 旧版 token 迁移失败: %s", exc)
            return False

    def login(self, email: str, password: str) -> bool:
        from coros_mcp.coros_api import login as _login
        region = "cn" if (email.isdigit() and len(email) >= 10) else "eu"
        logger.info("Coros: 登录 (region=%s, account=%s...)", region, email[:3])
        try:
            self._auth = _run(_login(email, password, region, skip_mobile=True))
            self._save()
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
        if not self._auth.is_authenticated():
            return []
        try:
            raw = _fetch_activity_items(self._auth, start, end)
        except Exception as e:
            logger.error("Coros 获取活动失败: %s", e)
            raise RuntimeError(f"Coros 获取活动失败，请重新绑定账号或稍后重试: {e}") from e
        result = [_parse_activity_item(item) for item in raw]
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
        self.auth = CorosAuth(getattr(config, "token_dir", None))
        self._activities = CorosActivity(self.auth)
        self._health = CorosHealth(self.auth)

    def authenticate(self) -> bool:
        # Web 后续请求不保存密码，使用绑定时写入的用户隔离 token。
        if self.auth.is_authenticated() and not self._config.password:
            return True
        if not self._config.email or not self._config.password:
            logger.error("Coros 认证信息不可用，请重新绑定账号")
            return False
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
