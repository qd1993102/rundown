"""存储模块 — 管理 SQLite 数据库，提供数据持久化与查询接口。

复用 garmy LocalDB (SyncManager + HealthDB) 作为存储引擎，
在此基础上封装自定义查询接口。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from garmy.localdb import HealthDB, SyncManager
from garmy.localdb.progress import ProgressReporter

from .config import Config
from .local_files import atomic_write_private, ensure_private_dir, restrict_private_file
from .resource_lifecycle import close_runtime_resources

logger = logging.getLogger(__name__)

_CALENDAR_METRIC_TYPE = "neurun_provider_sync"


def _ensure_progress_reporter_compat(progress_reporter: Any) -> None:
    """补齐旧版 garmy ``ProgressReporter.warning`` 缺失的兼容接口。

    garmy 的 ``ActivitiesIterator`` 在拉取失败时调用 ``warning``，但部分
    版本的 ``ProgressReporter`` 只实现了 ``info``/``error``，会覆盖原始异常。
    """
    if not callable(getattr(progress_reporter, "warning", None)):
        progress_reporter.warning = logger.warning


class GarmySyncProgressReporter(ProgressReporter):
    """把 garmy 的逐日期/逐指标事件适配为 Web 可持久化的真实进度。"""

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None],
        *,
        min_emit_interval: float = 1.0,
    ) -> None:
        super().__init__(use_tqdm=False)
        self._callback = callback
        self._min_emit_interval = max(0.0, min_emit_interval)
        self._current = 0
        self._total = 0
        self._last_emit_at = float("-inf")
        self._last_emitted_current = -1
        self._last_emitted_date: date | None = None
        self._last_item: dict[str, Any] | None = None

    def start_sync(self, total: int) -> None:
        self._current = 0
        self._total = max(0, int(total))
        self._last_emit_at = float("-inf")
        self._last_emitted_current = -1
        self._last_emitted_date = None
        self._last_item = None
        super().start_sync(total)
        self._emit(
            task=None, sync_date=None, outcome="started", force=True,
        )

    def task_complete(self, task: str, sync_date: date) -> None:
        super().task_complete(task, sync_date)
        self._advance(task, sync_date, "completed")

    def task_skipped(self, task: str, sync_date: date) -> None:
        super().task_skipped(task, sync_date)
        self._advance(task, sync_date, "skipped")

    def task_failed(self, task: str, sync_date: date) -> None:
        super().task_failed(task, sync_date)
        self._advance(task, sync_date, "failed")

    def end_sync(self) -> None:
        if (
            self._last_item is not None
            and self._last_emitted_current != self._current
        ):
            self._emit(
                task=self._last_item["metric"],
                sync_date=date.fromisoformat(self._last_item["date"]),
                outcome=self._last_item["outcome"],
                force=True,
            )
        super().end_sync()

    def _advance(self, task: str, sync_date: date, outcome: str) -> None:
        self._current += 1
        self._last_item = {
            "metric": task,
            "date": sync_date.isoformat(),
            "outcome": outcome,
        }
        self._emit(
            task=task,
            sync_date=sync_date,
            outcome=outcome,
            force=self._current >= self._total,
        )

    def _emit(
        self,
        *,
        task: str | None,
        sync_date: date | None,
        outcome: str,
        force: bool,
    ) -> None:
        now = time.monotonic()
        date_changed = sync_date is not None and sync_date != self._last_emitted_date
        if (
            not force
            and not date_changed
            and now - self._last_emit_at < self._min_emit_interval
        ):
            return

        payload = {
            "current": self._current,
            "total": self._total,
            "date": sync_date.isoformat() if sync_date is not None else None,
            "metric": task,
            "outcome": outcome,
        }
        try:
            self._callback(payload)
        except Exception as exc:
            logger.warning(
                "同步逐项进度上报失败: error_type=%s", type(exc).__name__,
            )
        self._last_emit_at = now
        self._last_emitted_current = self._current
        self._last_emitted_date = sync_date


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
        ensure_private_dir(self._db_path.parent)
        self._db: HealthDB | None = None
        self._sync_manager: SyncManager | None = None
        self._initialized = False
        self._injected_client = None  # Web 模式：外部注入的已认证 APIClient

    @property
    def db(self) -> HealthDB:
        """获取 HealthDB 实例（懒初始化）。"""
        if self._db is None:
            self._db = HealthDB(str(self._db_path))
            restrict_private_file(self._db_path)
            logger.info("HealthDB 已连接: %s", self._db_path)
        return self._db

    @property
    def sync_manager(self) -> SyncManager:
        """获取 SyncManager 实例（懒初始化，需要 initialize）。

        garmy 2.0: SyncManager 接受 db_path (Path/str)，不是 HealthDB 实例。
        """
        if self._sync_manager is None:
            self._sync_manager = SyncManager(db_path=str(self._db_path))
            _ensure_progress_reporter_compat(self._sync_manager.progress)
        return self._sync_manager

    def initialize_sync(self) -> None:
        """初始化 SyncManager。

        CLI 模式：config.email/password 登录（token_dir 默认 ~/.garmy）
        Web 模式：使用 set_api_client() 预先注入的 APIClient（每用户隔离 token_dir）
        """
        if self._initialized:
            return

        if self._injected_client is not None:
            logger.info("使用已有 APIClient 初始化 SyncManager（Web 模式）...")
            from garmy.localdb.sync import ActivitiesIterator
            sm = self.sync_manager
            sm.api_client = self._injected_client
            sm.activities_iterator = ActivitiesIterator(
                self._injected_client, sm.config.sync, sm.progress)
            sm.activities_iterator.initialize()
        else:
            logger.info("使用凭据初始化 SyncManager（CLI 模式）...")
            self.sync_manager.initialize(
                email=self._config.email,
                password=self._config.password,
            )

        self._initialized = True
        logger.info("SyncManager 初始化完成")

    def set_api_client(self, api_client) -> None:
        """注入已验证的 APIClient（Web 多用户模式，在 initialize_sync 前调用）。"""
        self._injected_client = api_client

    def close(self) -> None:
        """关闭网络会话和 SQLite Engine；允许重复调用。"""
        close_runtime_resources(
            self._injected_client,
            getattr(self._sync_manager, "api_client", None),
        )

        databases = [
            self._db,
            getattr(self._sync_manager, "db", None),
        ]
        disposed: set[int] = set()
        for database in databases:
            engine = getattr(database, "engine", None)
            if engine is None or id(engine) in disposed:
                continue
            engine.dispose()
            disposed.add(id(engine))

        self._db = None
        self._sync_manager = None
        self._injected_client = None
        self._initialized = False

    # ── 备份 / 恢复 ────────────────────────────

    def backup_to(self, backup_path: str | Path) -> None:
        """使用 sqlite3 在线备份 API 将数据库备份到指定路径。

        确保备份目标目录存在。备份过程中其他读操作不受影响。
        """
        import sqlite3

        bp = Path(backup_path)
        ensure_private_dir(bp.parent)

        # 确保数据库已创建
        _ = self.db

        src = sqlite3.connect(str(self._db_path))
        dst = sqlite3.connect(str(bp))
        try:
            src.backup(dst)
            logger.info("数据库已备份到: %s", bp)
        finally:
            src.close()
            dst.close()
        restrict_private_file(bp)

    def restore_from(self, backup_path: str | Path) -> bool:
        """从备份路径恢复数据库。备份不存在时返回 False。"""
        import shutil

        bp = Path(backup_path)
        if not bp.exists():
            logger.info("备份不存在，跳过恢复: %s", bp)
            return False

        ensure_private_dir(self._db_path.parent)
        shutil.copy2(str(bp), str(self._db_path))
        restrict_private_file(self._db_path)
        # 重置懒加载的实例
        self._db = None
        logger.info("数据库已从备份恢复: %s -> %s", bp, self._db_path)
        return True

    # ── 同步 ──────────────────────────────────

    def sync_range(
        self,
        user_id: int,
        start: date,
        end: date,
        metrics: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        """同步指定日期范围的数据。

        Args:
            user_id: 运动平台用户 ID (int)。
            start: 起始日期。
            end: 结束日期。
            metrics: 要同步的指标列表，None 表示全部。
            progress_callback: 可选的真实逐项进度回调，仅供 Web 任务使用。

        Returns:
            同步结果统计 {metric_type: count}。
        """
        if progress_callback is not None:
            reporter = GarmySyncProgressReporter(progress_callback)
            manager = self.sync_manager
            manager.progress = reporter
            _ensure_progress_reporter_compat(reporter)
            if manager.activities_iterator is not None:
                manager.activities_iterator.progress = reporter
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

    def mark_sync_calendar_range(
        self,
        user_id: int,
        start: date,
        end: date,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """记录 Provider 范围同步对每一天的整体处理状态。"""
        import sqlite3

        if start > end:
            raise ValueError("同步日历开始日期不能晚于结束日期")
        if status not in {"pending", "completed", "failed"}:
            raise ValueError(f"不支持的同步日历状态: {status}")

        _ = self.db
        rows = []
        current = start
        while current <= end:
            rows.append((
                user_id,
                str(current),
                _CALENDAR_METRIC_TYPE,
                status,
                (error_message or "")[:500] or None,
            ))
            current += timedelta(days=1)

        with sqlite3.connect(str(self._db_path)) as db:
            db.executemany("""
                INSERT INTO sync_status
                    (user_id, sync_date, metric_type, status, synced_at,
                     error_message, created_at)
                VALUES (?, ?, ?, ?, datetime('now'), ?, datetime('now'))
                ON CONFLICT(user_id, sync_date, metric_type) DO UPDATE SET
                    status = excluded.status,
                    synced_at = datetime('now'),
                    error_message = excluded.error_message
            """, rows)

    def get_sync_calendar(
        self,
        user_id: int | None,
        start: date,
        end: date,
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        """聚合范围同步、指标状态与本地数据，生成逐日同步日历。"""
        import sqlite3

        if start > end:
            raise ValueError("同步日历开始日期不能晚于结束日期")

        today = today or date.today()
        status_by_date: dict[str, list[dict[str, Any]]] = {}
        activity_counts: dict[str, int] = {}
        running_counts: dict[str, int] = {}
        health_dates: set[str] = set()

        if user_id is not None:
            _ = self.db
            with sqlite3.connect(str(self._db_path)) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute("""
                    SELECT sync_date, metric_type, status, synced_at
                    FROM sync_status
                    WHERE user_id = ? AND sync_date >= ? AND sync_date <= ?
                    ORDER BY sync_date, metric_type
                """, (user_id, str(start), str(end))):
                    key = str(row["sync_date"])
                    status_by_date.setdefault(key, []).append(dict(row))

                activity_columns = {
                    str(row["name"])
                    for row in db.execute("PRAGMA table_info(activities)")
                }
                activity_type_expr = (
                    "LOWER(COALESCE(activity_type, ''))"
                    if "activity_type" in activity_columns
                    else "''"
                )
                for row in db.execute(f"""
                    SELECT activity_date, COUNT(*) AS count,
                        SUM(CASE WHEN
                            {activity_type_expr} LIKE '%running%'
                            OR {activity_type_expr} IN ('run', 'trail_run')
                            OR LOWER(COALESCE(activity_name, '')) LIKE '%run%'
                            OR COALESCE(activity_name, '') LIKE '%跑步%'
                            THEN 1 ELSE 0 END) AS running_count
                    FROM activities
                    WHERE user_id = ? AND activity_date >= ? AND activity_date <= ?
                    GROUP BY activity_date
                """, (user_id, str(start), str(end))):
                    key = str(row["activity_date"])
                    activity_counts[key] = int(row["count"])
                    running_counts[key] = int(row["running_count"] or 0)

                for row in db.execute("""
                    SELECT metric_date
                    FROM daily_health_metrics
                    WHERE user_id = ? AND metric_date >= ? AND metric_date <= ?
                """, (user_id, str(start), str(end))):
                    health_dates.add(str(row["metric_date"]))

        days: list[dict[str, Any]] = []
        counts = {
            "synced": 0,
            "partial": 0,
            "failed": 0,
            "syncing": 0,
            "unsynced": 0,
            "future": 0,
        }
        latest_synced_date: str | None = None
        current = start
        while current <= end:
            key = str(current)
            rows = status_by_date.get(key, [])
            marker = next(
                (row for row in rows if row["metric_type"] == _CALENDAR_METRIC_TYPE),
                None,
            )
            metric_rows = [
                row for row in rows if row["metric_type"] != _CALENDAR_METRIC_TYPE
            ]
            metric_statuses = {str(row["status"]).lower() for row in metric_rows}
            has_local_data = key in activity_counts or key in health_dates
            has_running = running_counts.get(key, 0) > 0

            if current > today:
                day_status = "future"
            elif has_running:
                day_status = "synced"
            elif marker and str(marker["status"]).lower() == "pending":
                day_status = "syncing"
            elif marker and str(marker["status"]).lower() == "failed":
                day_status = "failed"
            elif marker and str(marker["status"]).lower() == "completed":
                if metric_statuses.intersection({"failed", "pending"}):
                    day_status = "partial"
                else:
                    day_status = "synced"
            elif "failed" in metric_statuses:
                day_status = "failed"
            elif has_local_data or metric_statuses:
                day_status = "partial"
            else:
                day_status = "unsynced"

            synced_values = [str(row["synced_at"]) for row in rows if row["synced_at"]]
            synced_at = max(synced_values) if synced_values else None
            counts[day_status] += 1
            if day_status == "synced":
                latest_synced_date = key
            days.append({
                "date": key,
                "status": day_status,
                "activity_count": activity_counts.get(key, 0),
                "running_count": running_counts.get(key, 0),
                "has_health": key in health_dates,
                "synced_at": synced_at,
            })
            current += timedelta(days=1)

        return {
            "days": days,
            "summary": {
                **counts,
                "total_days": len(days),
                "latest_synced_date": latest_synced_date,
            },
        }

    def get_local_user_id(self) -> int | None:
        """从当前用户 SQLite 中读取唯一的平台用户 ID，不访问远端平台。"""
        from sqlalchemy import text

        session = self.db.get_session()
        try:
            rows = session.execute(text("""
                SELECT DISTINCT user_id FROM (
                    SELECT user_id FROM activities
                    UNION ALL
                    SELECT user_id FROM daily_health_metrics
                    UNION ALL
                    SELECT user_id FROM sync_status WHERE status = 'completed'
                    UNION ALL
                    SELECT user_id FROM timeseries
                )
                ORDER BY user_id
            """)).scalars().all()
        finally:
            session.close()

        if not rows:
            return None
        if len(rows) > 1:
            raise RuntimeError("用户数据库包含多个平台用户 ID，无法确定日报归属")
        return int(rows[0])

    def reset_pending_metrics(self, user_id: int, start: date, end: date,
    force: bool = False) -> int:
        """清理区间内同步记录，强制下次 sync 重新拉取。

        解决 garmy SyncManager 在记录已存在时跳过（即使上次失败）的问题。
        force=True 时清除所有记录（包括 completed），强制全量重拉。
        """
        import sqlite3
        try:
            db = sqlite3.connect(str(self._db_path))
            if force:
                cur = db.execute(
                    "DELETE FROM sync_status WHERE user_id = ? AND sync_date >= ? AND sync_date <= ?",
                    (user_id, str(start), str(end)),
                )
                db.execute(
                    "DELETE FROM daily_health_metrics WHERE user_id = ? AND metric_date >= ? AND metric_date <= ?",
                    (user_id, str(start), str(end)),
                )
                db.execute(
                    "DELETE FROM activities WHERE user_id = ? AND activity_date >= ? AND activity_date <= ?",
                    (user_id, str(start), str(end)),
                )
            else:
                cur = db.execute(
                    "DELETE FROM sync_status WHERE user_id = ? AND sync_date >= ? AND sync_date <= ? AND status IN ('pending', 'failed')",
                    (user_id, str(start), str(end)),
                )
            deleted = cur.rowcount
            db.commit()
            db.close()
            action = "强制重置" if force else "重置 pending/failed"
            if deleted:
                logger.info("%s %d 条同步记录 (%s ~ %s)", action, deleted, start, end)
            return deleted
        except Exception as exc:
            logger.warning("重置同步记录失败: %s", exc)
            return 0

    def has_local_data(self, user_id: int, target_date: date) -> bool:
        """检查本地是否已有指定日期的数据（活动 + 健康）。"""
        import sqlite3
        try:
            db = sqlite3.connect(str(self._db_path))
            has_health = db.execute(
                "SELECT 1 FROM daily_health_metrics WHERE user_id = ? AND metric_date = ?",
                (user_id, str(target_date)),
            ).fetchone()
            has_activity = db.execute(
                "SELECT 1 FROM activities WHERE user_id = ? AND activity_date = ?",
                (user_id, str(target_date)),
            ).fetchone()
            db.close()
            return bool(has_health or has_activity)
        except Exception:
            return False
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
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
        atomic_write_private(path, buffer.getvalue(), private_parent=False)

        logger.info("已导出 %d 行到 %s", len(data), output_path)

    def export_json(
        self, data: list[dict[str, Any]], output_path: str
    ) -> None:
        """导出数据为 JSON 文件。"""
        path = Path(output_path)
        atomic_write_private(
            path,
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            private_parent=False,
        )

        logger.info("已导出 %d 行到 %s", len(data), output_path)
