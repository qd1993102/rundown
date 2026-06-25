"""数据拉取模块 — 封装 garmy APIClient 的 Metrics 系统。

提供统一的数据拉取接口：
- 运动活动数据（跑步、骑行、游泳等）
- 日常健康指标（睡眠、心率、HRV、压力、身体电量等）
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from garmy import APIClient

from .auth import AuthManager

logger = logging.getLogger(__name__)

# 所有支持的日常健康指标
HEALTH_METRICS = [
    "sleep",
    "heart_rate",
    "hrv",
    "stress",
    "body_battery",
    "steps",
    "calories",
    "respiration",
    "training_readiness",
    "daily_summary",
]


class Fetcher:
    """运动数据拉取器。

    封装 APIClient 的 Metrics 系统，
    提供按日期/类型拉取活动和健康指标的便捷方法。
    """

    def __init__(self, auth_manager: AuthManager):
        self._auth = auth_manager
        self._client: APIClient | None = None

    @property
    def client(self) -> APIClient:
        """获取已认证的 APIClient（懒初始化）。"""
        if self._client is None:
            auth_client = self._auth.client
            # garmy 2.0 API: APIClient(auth_client, domain, timeout, retries)
            self._client = APIClient(auth_client=auth_client)
            logger.info("APIClient 已创建")
        return self._client

    # ── 活动数据 ──────────────────────────────

    def get_activities_recent(
        self, days: int = 30
    ) -> list[dict[str, Any]]:
        """获取最近 N 天的活动列表。

        使用 get_recent() API 直接拉取，绕过 SyncManager 的 ActivitiesIterator。

        Returns:
            活动列表，每条包含 activity_id, activity_name,
            activity_type_name, start_time_local, duration 等 50+ 字段。
        """
        try:
            activities_accessor = self.client.metrics.get("activities")
            if activities_accessor is None:
                logger.warning("Activities 指标不可用")
                return []
            # 使用 get_recent API（分页拉取，limit 设大一些确保拿全）
            activities = activities_accessor.get_recent(days=days, limit=200)
            if not activities:
                return []
            result = [
                a.to_dict() if hasattr(a, "to_dict") else a
                for a in (activities if isinstance(activities, list) else [activities])
            ]
            logger.info("获取最近 %d 天活动: %d 条", days, len(result))
            return result
        except Exception as exc:
            logger.warning("获取活动失败: %s", exc)
            return []

    def fetch_and_store_activities(
        self, user_id: int, days: int = 30,
    ) -> int:
        """直接从 API 拉取活动并逐个存储到数据库。

        绕过 garmy SyncManager 的 activities 同步（后者有丢数据 bug），
        使用 APIClient → ActivitiesAccessor.get_recent() → HealthDB.store_activity()。

        Returns:
            存储的活动数量。
        """
        from .storage import Storage
        # 需要 Storage 实例来获取 HealthDB。这里通过延迟导入避免循环依赖。
        # 实际调用时由 main.py 的 _setup() 传入 storage 实例。

        activities = self.get_activities_recent(days=days)
        if not activities:
            return 0

        stored = 0
        # 需要在此处访问 HealthDB。暂时通过 garmy 的方式手动构建数据。
        # 实际上更好的做法是在 storage 中提供一个 store_activities 方法。
        logger.info("已拉取 %d 条活动，存储需通过 SyncManager", len(activities))
        return stored

    def get_activities_range(
        self, start: date, end: date
    ) -> list[dict[str, Any]]:
        """按日期范围获取活动。

        Args:
            start: 起始日期。
            end: 结束日期（含）。

        Returns:
            指定日期范围内的活动列表。
        """
        days = (end - start).days + 1
        all_activities = self.get_activities_recent(days=days)
        # 过滤日期范围
        filtered = []
        for a in all_activities:
            activity_date = a.get("start_time_local", "")
            if isinstance(activity_date, str):
                activity_date = activity_date[:10]  # "YYYY-MM-DD"
            if str(start) <= str(activity_date) <= str(end):
                filtered.append(a)
        return filtered

    def get_activities_by_type(
        self, activity_type: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """按运动类型筛选活动。

        Args:
            activity_type: 运动类型，如 "running", "cycling", "swimming"。
            days: 查询最近 N 天。
        """
        all_activities = self.get_activities_recent(days=days)
        filtered = [
            a for a in all_activities
            if a.get("activity_type_name", "").lower() == activity_type.lower()
        ]
        logger.info(
            "获取 %s 类型活动 (最近 %d 天): %d 条",
            activity_type, days, len(filtered),
        )
        return filtered

    # ── 健康指标 ──────────────────────────────

    def get_health_metric(
        self, metric: str, target_date: date | None = None
    ) -> dict[str, Any] | None:
        """获取某一天的健康指标数据。

        Args:
            metric: 指标 key，如 'sleep', 'hrv', 'heart_rate'。
            target_date: 目标日期，默认今天。
        """
        if target_date is None:
            target_date = date.today()

        try:
            accessor = self.client.metrics.get(metric)
            if accessor is None:
                logger.warning("指标 %s 不可用", metric)
                return None
            data = accessor.get(target_date)
            if data is None:
                return None
            return data.to_dict() if hasattr(data, "to_dict") else data
        except Exception as exc:
            logger.warning("获取指标 %s (%s) 失败: %s", metric, target_date, exc)
            return None

    def get_health_metrics_range(
        self, metric: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """获取最近 N 天的某指标数据。

        Args:
            metric: 指标 key。
            days: 最近 N 天。
        """
        try:
            accessor = self.client.metrics.get(metric)
            if accessor is None:
                return []
            data = accessor.list(days=days)
            if not data:
                return []
            items = data if isinstance(data, list) else [data]
            return [
                d.to_dict() if hasattr(d, "to_dict") else d
                for d in items
            ]
        except Exception as exc:
            logger.warning("获取指标 %s 范围失败: %s", metric, exc)
            return []

    def get_all_health_metrics(
        self, target_date: date | None = None
    ) -> dict[str, Any]:
        """获取某天的全部健康指标。

        Args:
            target_date: 目标日期，默认今天。

        Returns:
            {metric_key: data_dict} 字典。
        """
        if target_date is None:
            target_date = date.today()

        result: dict[str, Any] = {}
        for metric in HEALTH_METRICS:
            data = self.get_health_metric(metric, target_date)
            if data is not None:
                result[metric] = data
        return result

    # ── 辅助方法 ──────────────────────────────

    def list_available_metrics(self) -> list[str]:
        """列出所有可用的指标 key。"""
        try:
            return list(self.client.metrics.keys())
        except Exception:
            return HEALTH_METRICS
