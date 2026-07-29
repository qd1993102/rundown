"""Web 异步同步任务的用户级持久化与状态机。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .local_files import (
    LocalPersistenceError,
    atomic_write_private,
    read_private_text,
)


ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
_DEFAULT_PROGRESS = {
    "kind": "stage",
    "current": 0,
    "total": 4,
    "label": "等待执行",
}


def utc_now() -> str:
    """返回便于 JSON/API 使用的 UTC ISO 8601 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class SyncTask:
    """一个用户同步任务的持久化快照。"""

    task_id: str
    mode: str
    from_date: str
    to_date: str
    force: bool
    runner_instance_id: str
    status: str = "queued"
    stage: str = "queued"
    progress: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_PROGRESS))
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    updated_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_api_dict(self) -> dict[str, Any]:
        """移除内部 runner 标识后返回稳定的轮询响应。"""
        data = self.to_dict()
        data.pop("runner_instance_id", None)
        data["links"] = {"self": f"/api/sync/tasks/{self.task_id}"}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncTask":
        status = str(data.get("status") or "queued")
        if status not in ALL_STATUSES:
            raise ValueError(f"未知同步任务状态: {status}")
        return cls(
            task_id=str(data["task_id"]),
            mode=str(data.get("mode") or "legacy"),
            from_date=str(data.get("from_date") or ""),
            to_date=str(data.get("to_date") or ""),
            force=bool(data.get("force")),
            runner_instance_id=str(data.get("runner_instance_id") or ""),
            status=status,
            stage=str(data.get("stage") or "queued"),
            progress=dict(data.get("progress") or _DEFAULT_PROGRESS),
            created_at=str(data.get("created_at") or utc_now()),
            started_at=data.get("started_at"),
            updated_at=str(data.get("updated_at") or utc_now()),
            finished_at=data.get("finished_at"),
            result=dict(data["result"]) if isinstance(data.get("result"), dict) else None,
            error=dict(data["error"]) if isinstance(data.get("error"), dict) else None,
        )


class SyncTaskStore:
    """以原子私有 JSON 文件保存单个用户最近的同步任务。"""

    def __init__(
        self,
        path: str | Path,
        runner_instance_id: str,
        *,
        max_tasks: int = 50,
        retention_days: int = 7,
    ):
        self.path = Path(path)
        self.runner_instance_id = runner_instance_id
        self.max_tasks = max_tasks
        self.retention_days = retention_days
        self._lock = threading.RLock()
        self._tasks: dict[str, SyncTask] = {}
        self._load()
        self._interrupt_previous_runner()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(read_private_text(self.path))
            raw_tasks = payload.get("tasks", [])
            if not isinstance(raw_tasks, list):
                raise ValueError("tasks 必须是数组")
            self._tasks = {
                task.task_id: task
                for task in (SyncTask.from_dict(item) for item in raw_tasks)
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise LocalPersistenceError(
                f"同步任务文件格式无效 {self.path}: {exc}"
            ) from exc

    def _interrupt_previous_runner(self) -> None:
        changed = False
        now = utc_now()
        with self._lock:
            for task in self._tasks.values():
                if (
                    task.status in ACTIVE_STATUSES
                    and task.runner_instance_id != self.runner_instance_id
                ):
                    task.status = "interrupted"
                    task.stage = "done"
                    task.updated_at = now
                    task.finished_at = now
                    task.error = {
                        "code": "sync_interrupted",
                        "message": "服务已重启，请重新发起同步",
                        "retryable": True,
                        "action": "retry",
                    }
                    changed = True
            if changed:
                self._save()

    def _retained_tasks(self) -> list[SyncTask]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        active = [task for task in self._tasks.values() if task.status in ACTIVE_STATUSES]
        terminal = [
            task for task in self._tasks.values()
            if task.status in TERMINAL_STATUSES and _parse_time(task.created_at) >= cutoff
        ]
        terminal.sort(key=lambda task: task.created_at, reverse=True)
        retained = active + terminal[: self.max_tasks]
        retained.sort(key=lambda task: task.created_at, reverse=True)
        return retained

    def _save(self) -> None:
        retained = self._retained_tasks()
        self._tasks = {task.task_id: task for task in retained}
        atomic_write_private(
            self.path,
            json.dumps(
                {"version": 1, "tasks": [task.to_dict() for task in retained]},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
        )

    def create(
        self,
        *,
        task_id: str,
        mode: str,
        from_date: str,
        to_date: str,
        force: bool,
    ) -> SyncTask:
        with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"同步任务已存在: {task_id}")
            task = SyncTask(
                task_id=task_id,
                mode=mode,
                from_date=from_date,
                to_date=to_date,
                force=force,
                runner_instance_id=self.runner_instance_id,
            )
            self._tasks[task_id] = task
            self._save()
            return task

    def get(self, task_id: str) -> SyncTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return SyncTask.from_dict(task.to_dict()) if task is not None else None

    def _active_task(self, task_id: str) -> SyncTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise ValueError(f"同步任务已进入终态: {task.status}")
        return task

    @staticmethod
    def _progress(
        *,
        stage: str,
        current: int,
        total: int,
        label: str,
        items: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        progress = {
            "kind": "stage",
            "current": current,
            "total": total,
            "label": label,
        }
        if items is not None:
            progress["items"] = dict(items)
        return progress

    def mark_running(
        self,
        task_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        label: str,
        items: dict[str, Any] | None = None,
    ) -> SyncTask:
        with self._lock:
            task = self._active_task(task_id)
            now = utc_now()
            task.status = "running"
            task.stage = stage
            task.progress = self._progress(
                stage=stage, current=current, total=total, label=label,
                items=items,
            )
            task.started_at = task.started_at or now
            task.updated_at = now
            self._save()
            return task

    def update_progress(
        self,
        task_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        label: str,
        items: dict[str, Any] | None = None,
    ) -> SyncTask:
        with self._lock:
            task = self._active_task(task_id)
            if task.status != "running":
                raise ValueError("同步任务尚未开始运行")
            task.stage = stage
            task.progress = self._progress(
                stage=stage, current=current, total=total, label=label,
                items=items,
            )
            task.updated_at = utc_now()
            self._save()
            return task

    def mark_succeeded(
        self, task_id: str, *, result: dict[str, Any],
    ) -> SyncTask:
        with self._lock:
            task = self._active_task(task_id)
            now = utc_now()
            task.status = "succeeded"
            task.stage = "done"
            task.progress = self._progress(
                stage="done", current=4, total=4, label="同步完成",
            )
            task.result = dict(result)
            task.error = None
            task.updated_at = now
            task.finished_at = now
            self._save()
            return task

    def mark_failed(
        self, task_id: str, *, error: dict[str, Any],
    ) -> SyncTask:
        with self._lock:
            task = self._active_task(task_id)
            now = utc_now()
            task.status = "failed"
            task.stage = "done"
            task.result = None
            task.error = dict(error)
            task.updated_at = now
            task.finished_at = now
            self._save()
            return task
