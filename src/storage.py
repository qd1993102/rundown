"""存储模块 — 管理 SQLite 数据库，提供数据持久化与查询接口。

复用 garmy LocalDB (SyncManager + HealthDB) 作为存储引擎，
在此基础上封装自定义查询接口。
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from garmy.localdb import HealthDB, SyncManager

from .config import Config

logger = logging.getLogger(__name__)


class Storage:
    """运动数据存储管理器。

    在 garmy LocalDB 之上封装：
    - 数据库初始化
    - SyncManager 调度同步
    - 按日期范围查询
    - 按类型统计
    - 导出 CSV / JSON
    """

    def __init__(self, config: Config):
        self._config = config
        self._db_path = Path(config.db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: HealthDB | None = None
        self._sync_manager: SyncManager | None = None
        self._initialized = False

    @property
    def db(self) -> HealthDB:
        """获取 HealthDB 实例（懒初始化）。"""
        if self._db is None:
            self._db = HealthDB(str(self._db_path))
            logger.info("HealthDB 已连接: %s", self._db_path)
        return self._db

    @property
    def sync_manager(self) -> SyncManager:
        """获取 SyncManager 实例（懒初始化，需要 initialize）。

        garmy 2.0: SyncManager 接受 db_path (Path/str)，不是 HealthDB 实例。
        """
        if self._sync_manager is None:
            self._sync_manager = SyncManager(db_path=str(self._db_path))
        return self._sync_manager

    def initialize_sync(self) -> None:
        """初始化 SyncManager（在首次同步前调用）。"""
        if not self._initialized:
            logger.info("初始化 SyncManager...")
            self.sync_manager.initialize(
                email=self._config.email,
                password=self._config.password,
            )
            self._initialized = True
            logger.info("SyncManager 初始化完成")

    # ── 同步 ──────────────────────────────────

    def sync_range(
        self,
        user_id: int,
        start: date,
        end: date,
        metrics: list[str] | None = None,
    ) -> dict[str, int]:
        """同步指定日期范围的数据。

        Args:
            user_id: 运动平台用户 ID (int)。
            start: 起始日期。
            end: 结束日期。
            metrics: 要同步的指标列表，None 表示全部。

        Returns:
            同步结果统计 {metric_type: count}。
        """
        self.initialize_sync()
        logger.info(
            "开始同步: %s ~ %s, metrics=%s",
            start, end, metrics or "all",
        )
        result = self.sync_manager.sync_range(
            user_id=user_id,
            start_date=start,
            end_date=end,
            metrics=metrics,
        )
        logger.info("同步完成: %s", result)
        return result

    def get_sync_status(self, user_id: int, sync_date: date,
                        metric_type: str) -> str | None:
        """查询某天某指标的同步状态。"""
        try:
            from garmy.localdb.models import MetricType
            mt = MetricType(metric_type) if metric_type else None
            if mt is None:
                return None
            return self.db.get_sync_status(user_id, sync_date, mt)
        except Exception:
            return None

    def get_all_sync_status(self, user_id: int) -> list[dict[str, Any]]:
        """查询所有同步状态记录（用于 status 命令）。"""
        try:
            rows = self.db.get_pending_metrics(user_id, date.today())
            return [{"date": str(date.today()), "metric": m, "status": "pending"}
                    for m in rows]
        except Exception:
            return []

    def reset_pending_metrics(self, user_id: int, start: date, end: date) -> int:
        """清理区间内 pending/failed 的同步记录，强制下次 sync 重新拉取。

        解决 garmy SyncManager 在记录已存在时跳过（即使上次失败）的问题。
        """
        import sqlite3
        try:
            db = sqlite3.connect(str(self._db_path))
            cur = db.execute(
                "DELETE FROM sync_status WHERE user_id = ? AND sync_date >= ? AND sync_date <= ? AND status IN ('pending', 'failed')",
                (user_id, str(start), str(end)),
            )
            deleted = cur.rowcount
            db.commit()
            db.close()
            if deleted:
                logger.info("重置 %d 条 pending/failed 同步记录 (%s ~ %s)", deleted, start, end)
            return deleted
        except Exception as exc:
            logger.warning("重置 pending 记录失败: %s", exc)
            return 0

    # ── 活动查询 ──────────────────────────────

    def get_activities_range(
        self, user_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        """按日期范围查询活动。

        先尝试含 distance_meters 列的查询，如列不存在则回退。
        """
        try:
            from sqlalchemy import text
            session = self.db.get_session()
            rows = session.execute(text("""
                SELECT user_id, activity_id, activity_date, activity_name,
                       duration_seconds, avg_heart_rate, training_load,
                       start_time, distance_meters, created_at
                FROM activities
                WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                ORDER BY start_time
            """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            session.close()
            return [
                {
                    "user_id": r[0], "activity_id": r[1], "activity_date": r[2],
                    "activity_name": r[3], "duration_seconds": r[4],
                    "avg_heart_rate": r[5], "training_load": r[6],
                    "start_time": r[7], "distance_meters": r[8], "created_at": r[9],
                }
                for r in rows
            ]
        except Exception:
            # distance_meters 列可能不存在，回退
            return self._get_activities_range_fallback(user_id, start, end)

    def _get_activities_range_fallback(
        self, user_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        """回退查询：不含 distance_meters 列。"""
        try:
            from sqlalchemy import text
            session = self.db.get_session()
            rows = session.execute(text("""
                SELECT user_id, activity_id, activity_date, activity_name,
                       duration_seconds, avg_heart_rate, training_load,
                       start_time, created_at
                FROM activities
                WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                ORDER BY start_time
            """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            session.close()
            return [
                {
                    "user_id": r[0], "activity_id": r[1], "activity_date": r[2],
                    "activity_name": r[3], "duration_seconds": r[4],
                    "avg_heart_rate": r[5], "training_load": r[6],
                    "start_time": r[7], "distance_meters": None, "created_at": r[8],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("查询活动失败: %s", exc)
            return []

    def get_activities_by_type(
        self, user_id: int, activity_type: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """按运动类型筛选活动。"""
        start = date.today() - timedelta(days=days)
        end = date.today()
        all_activities = self.get_activities_range(user_id, start, end)
        return [
            a for a in all_activities
            if a.get("activity_type_name", "").lower() == activity_type.lower()
        ]

    def get_recent_activities(
        self, user_id: int, days: int = 7
    ) -> list[dict[str, Any]]:
        """获取最近 N 天活动。"""
        start = date.today() - timedelta(days=days)
        return self.get_activities_range(user_id, start, date.today())

    # ── 健康指标查询 ──────────────────────────

    def get_health_metrics(
        self, user_id: int, target_date: date
    ) -> dict[str, Any] | None:
        """获取某天的健康指标数据。

        garmy 2.0: get_health_metrics 接受日期范围，返回列表。
        """
        try:
            results = self.db.get_health_metrics(user_id, target_date, target_date)
            if not results:
                return None
            # 返回第一条（当天唯一）
            return results[0] if results else None
        except Exception as exc:
            logger.warning("查询健康指标 (%s) 失败: %s", target_date, exc)
            return None

    def get_health_metrics_range(
        self, user_id: int, days: int = 7
    ) -> list[dict[str, Any]]:
        """获取最近 N 天的健康指标。"""
        end = date.today()
        start = end - timedelta(days=days)
        try:
            return self.db.get_health_metrics(user_id, start, end)
        except Exception as exc:
            logger.warning("查询健康指标范围失败: %s", exc)
            return []

    def get_timeseries(
        self, user_id: int, metric_type: str, start_time: int, end_time: int,
    ) -> list[tuple]:
        """获取时序数据（身体电量曲线、心率曲线等）。"""
        try:
            from garmy.localdb.models import MetricType
            mt = MetricType(metric_type)
            return self.db.get_timeseries(user_id, mt, start_time, end_time)
        except Exception:
            return []

    # ── 统计查询 ──────────────────────────────

    def get_weekly_summary(
        self, user_id: int, target_date: date | None = None
    ) -> dict[str, Any]:
        """获取周训练汇总。

        Returns:
            {total_activities, total_duration_min, total_distance_km, ...}
        """
        if target_date is None:
            target_date = date.today()
        # 找到本周一
        monday = target_date - timedelta(days=target_date.weekday())
        sunday = monday + timedelta(days=6)

        activities = self.get_activities_range(user_id, monday, sunday)

        total_duration = sum(a.get("duration", 0) or 0 for a in activities)
        total_distance = sum(a.get("distance", 0) or 0 for a in activities)
        total_calories = sum(a.get("calories", 0) or 0 for a in activities)
        total_load = sum(a.get("activity_training_load", 0) or 0 for a in activities)

        # 按类型分组
        by_type: dict[str, dict[str, Any]] = {}
        for a in activities:
            atype = a.get("activity_type_name", "unknown")
            if atype not in by_type:
                by_type[atype] = {"count": 0, "duration_min": 0, "distance_km": 0}
            by_type[atype]["count"] += 1
            by_type[atype]["duration_min"] += round(
                (a.get("duration", 0) or 0) / 60, 1
            )
            by_type[atype]["distance_km"] += round(
                (a.get("distance", 0) or 0) / 1000, 1
            )

        return {
            "week_start": str(monday),
            "week_end": str(sunday),
            "total_activities": len(activities),
            "total_duration_min": round(total_duration / 60, 1),
            "total_distance_km": round(total_distance / 1000, 1),
            "total_calories": total_calories,
            "total_training_load": total_load,
            "by_type": by_type,
        }

    # ── 导出 ──────────────────────────────────

    def export_csv(
        self,
        data: list[dict[str, Any]],
        output_path: str,
        columns: list[str] | None = None,
    ) -> None:
        """导出数据为 CSV 文件。"""
        if not data:
            logger.warning("没有数据可导出")
            return

        if columns is None:
            columns = list(data[0].keys())

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)

        logger.info("已导出 %d 行到 %s", len(data), output_path)

    def export_json(
        self, data: list[dict[str, Any]], output_path: str
    ) -> None:
        """导出数据为 JSON 文件。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info("已导出 %d 行到 %s", len(data), output_path)
