"""导出模块 — CSV 和 JSON 数据导出。

支持：
- 活动数据导出
- 健康指标导出
- 记忆数据导出
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from .storage import Storage
from .memory import MemoryType, MemoryStore

logger = logging.getLogger(__name__)


class Exporter:
    """数据导出器。

    将 Garmin 数据和记忆导出为标准格式。
    """

    def __init__(self, storage: Storage, memory_store: MemoryStore):
        self._storage = storage
        self._memory = memory_store

    def export_activities_csv(
        self,
        user_id: str,
        output_path: str,
        days: int = 30,
        activity_type: str | None = None,
    ) -> int:
        """导出活动数据为 CSV。

        Returns:
            导出的记录数。
        """
        if activity_type:
            activities = self._storage.get_activities_by_type(
                user_id, activity_type, days,
            )
        else:
            activities = self._storage.get_recent_activities(user_id, days)

        if not activities:
            logger.warning("没有活动数据可导出")
            return 0

        # 主要字段
        columns = [
            "activity_id", "activity_name", "activity_type_name",
            "start_time_local", "duration", "distance",
            "average_hr", "max_hr", "calories",
            "aerobic_training_effect", "anaerobic_training_effect",
            "activity_training_load", "avg_stress",
        ]

        self._storage.export_csv(activities, output_path, columns)
        return len(activities)

    def export_activities_json(
        self, user_id: str, output_path: str, days: int = 30,
    ) -> int:
        """导出活动数据为 JSON。"""
        activities = self._storage.get_recent_activities(user_id, days)
        self._storage.export_json(activities, output_path)
        return len(activities)

    def export_health_csv(
        self, user_id: str, output_path: str, days: int = 30,
    ) -> int:
        """导出健康指标为 CSV。"""
        metrics_list = self._storage.get_health_metrics_range(user_id, days)

        if not metrics_list:
            logger.warning("没有健康数据可导出")
            return 0

        self._storage.export_csv(metrics_list, output_path)
        return len(metrics_list)

    def export_memories_json(
        self, output_path: str, memory_type: MemoryType | None = None,
    ) -> int:
        """导出记忆数据为 JSON。"""
        if memory_type:
            memories = self._memory.list_by_type(memory_type)
        else:
            memories = self._memory.query()

        data = [
            {
                "id": m.id,
                "type": m.type.value,
                "front_matter": m.front_matter,
                "body_preview": m.body[:500],
            }
            for m in memories
        ]

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("已导出 %d 条记忆到 %s", len(data), output_path)
        return len(data)
