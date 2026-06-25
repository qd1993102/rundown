"""数据源抽象层 — 定义统一的 Provider 接口。

支持 Garmin / Coros / 其他运动平台的即插即用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol


@dataclass
class ActivityData:
    """标准化活动数据。"""
    activity_id: str
    activity_name: str
    activity_type: str       # running, cycling, swimming, etc.
    start_time: str          # ISO format
    duration_seconds: int
    distance_meters: float = 0
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    training_load: float = 0
    calories: int = 0
    elevation_gain: float = 0
    has_gps: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyHealth:
    """标准化每日健康数据。"""
    metric_date: date
    sleep_duration_hours: float = 0
    deep_sleep_hours: float = 0
    rem_sleep_hours: float = 0
    deep_sleep_pct: float = 0
    rem_sleep_pct: float = 0
    resting_heart_rate: int | None = None
    hrv_weekly_avg: float | None = None
    hrv_last_night_avg: float | None = None
    hrv_status: str = "balanced"
    avg_stress_level: int | None = None
    body_battery_high: int | None = None
    body_battery_low: int | None = None
    total_steps: int = 0
    total_distance_meters: float = 0
    total_calories: int = 0
    active_calories: int = 0
    training_readiness_score: int | None = None
    training_readiness_level: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class AuthProvider(ABC):
    """认证接口。"""

    @abstractmethod
    def login(self, email: str, password: str) -> bool:
        """登录，返回是否成功。"""
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        """检查是否已认证。"""
        ...

    @abstractmethod
    def get_user_id(self) -> int:
        """获取用户 ID。"""
        ...

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """获取认证请求头。"""
        ...


class ActivityProvider(ABC):
    """活动数据接口。"""

    @abstractmethod
    def fetch_activities(self, start: date, end: date) -> list[ActivityData]:
        """获取日期范围内的活动列表。"""
        ...

    @abstractmethod
    def fetch_activity_detail(self, activity_id: str) -> dict[str, Any]:
        """获取单条活动的详细数据（分段、HR、功率等）。"""
        ...


class HealthProvider(ABC):
    """健康数据接口。"""

    @abstractmethod
    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        """获取某天的健康指标。"""
        ...

    @abstractmethod
    def fetch_health_range(self, start: date, end: date) -> list[DailyHealth]:
        """获取日期范围内的健康指标。"""
        ...


class DataProvider(ABC):
    """完整数据源 — 组合认证+活动+健康。"""

    @abstractmethod
    def authenticate(self) -> bool:
        """执行认证流程。"""
        ...

    @property
    @abstractmethod
    def user_id(self) -> int:
        ...

    @property
    @abstractmethod
    def activities(self) -> ActivityProvider:
        ...

    @property
    @abstractmethod
    def health(self) -> HealthProvider:
        ...
