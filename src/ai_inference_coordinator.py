"""Web 端在线 AI 推理的用户公平并发准入与短期状态。"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TypeVar


T = TypeVar("T")


class AIInferenceCapacityExceededError(RuntimeError):
    """当前进程已达到在线 AI 推理并发上限。"""


class AIInferenceInProgressError(RuntimeError):
    """同一用户已有在线 AI 工作正在执行或等待。"""


_OPERATION_LABELS = {
    "daily_report": "训练日报",
    "weekly_review": "周复盘",
    "training_draft": "训练方案草稿",
    "training_draft_update": "训练方案重算",
    "training_adjustment": "训练调整",
    "scheme_revision": "方案重规划",
    "race_strategy": "比赛策略",
}
_ACTIVE_STATES = {"waiting", "running"}
_TERMINAL_STATES = {"succeeded", "failed"}
_FAILURE_LABELS = {
    "provider_timeout": "AI 服务响应超时，未得到完整结果",
    "provider_unavailable": "AI 服务当前不可用",
    "provider_request_failed": "AI 服务请求失败",
    "schema_invalid": "AI 返回内容无法解析为可执行结构",
    "validator": "本地安全结构校验未通过",
    "stage_failed": "该阶段未能完成",
}


class AIInferenceCoordinator:
    """通过用户 singleflight、有界等待和独立线程池执行在线 AI 工作。

    已接纳工作立即进入受保护后台协程；调用方刷新或断开不会取消实际任务。
    只有获得执行槽位后才向 executor 提交工作，因此等待不占用线程。
    """

    def __init__(
        self,
        max_concurrency: int,
        max_pending: int,
        wait_timeout_seconds: float,
        *,
        terminal_retention_seconds: float = 300,
    ):
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须大于 0")
        if max_pending < max_concurrency:
            raise ValueError("max_pending 不能小于 max_concurrency")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds 必须大于 0")
        if terminal_retention_seconds <= 0:
            raise ValueError("terminal_retention_seconds 必须大于 0")
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending
        self.wait_timeout_seconds = wait_timeout_seconds
        self.terminal_retention_seconds = terminal_retention_seconds
        self._state_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._pending_users: set[str] = set()
        self._tasks_by_user: dict[str, dict[str, Any]] = {}
        self._active_count = 0
        self._background_tasks: set[asyncio.Task[T]] = set()
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="neurun-ai",
        )

    @property
    def in_flight(self) -> int:
        """返回正在独立 AI executor 中执行的工作数。"""
        return self._active_count

    @property
    def pending_count(self) -> int:
        """返回执行中和等待执行的用户总数。"""
        return len(self._pending_users)

    async def run(
        self,
        user_key: str,
        work: Callable[[], T],
        *,
        operation: str = "ai_generation",
        result_mapper: Callable[[T], Any] | None = None,
    ) -> T:
        """按用户接纳工作，并保护它不受 HTTP 调用方取消影响。"""
        execution = await self.start(
            user_key, work, operation=operation, result_mapper=result_mapper,
        )
        return await asyncio.shield(execution)

    async def start(
        self,
        user_key: str,
        work: Callable[[], T],
        *,
        operation: str = "ai_generation",
        result_mapper: Callable[[T], Any] | None = None,
    ) -> asyncio.Task[T]:
        """接纳后台 AI 任务，立即返回其状态句柄而不等待推理完成。"""
        if not user_key:
            raise ValueError("user_key 不能为空")
        now = time.monotonic()
        async with self._state_lock:
            self._prune_terminal_locked(user_key, now)
            if user_key in self._pending_users:
                raise AIInferenceInProgressError(
                    "你已有 AI 生成任务进行中，请等待完成后再试",
                )
            if len(self._pending_users) >= self.max_pending:
                raise AIInferenceCapacityExceededError(
                    "当前 AI 推理请求较多，请稍后重试",
                )
            self._pending_users.add(user_key)
            task_id = uuid.uuid4().hex
            self._tasks_by_user[user_key] = {
                "task_id": task_id,
                "request_id": task_id,
                "operation": operation,
                "state": "waiting",
                "stage": "queued",
                "submitted_at": _timestamp(),
                "started_at": None,
                "finished_at": None,
                "error_message": None,
                "failure_stage": None,
                "failure_type": None,
                "retryable": True,
                "completed_stages": [],
                "framework_id": None,
                "facts_snapshot_id": None,
                "summary_version": None,
                "result": None,
                "_submitted_monotonic": now,
                "_started_monotonic": None,
                "_finished_monotonic": None,
            }

        try:
            execution = asyncio.create_task(
                self._execute_admitted(user_key, work, result_mapper),
            )
        except BaseException:
            async with self._state_lock:
                self._pending_users.discard(user_key)
                self._tasks_by_user.pop(user_key, None)
            raise
        self._background_tasks.add(execution)
        execution.add_done_callback(self._forget_background)
        return execution

    async def current_status(self, user_key: str) -> dict[str, Any] | None:
        """读取当前用户最新任务；终态超过保留时间后返回空。"""
        now = time.monotonic()
        async with self._state_lock:
            self._prune_terminal_locked(user_key, now)
            record = self._tasks_by_user.get(user_key)
            if record is None:
                return None
            public = {
                key: copy.deepcopy(value)
                for key, value in record.items()
                if not key.startswith("_") and value is not None
            }
            started = record.get("_started_monotonic")
            submitted = record["_submitted_monotonic"]
            finished = record.get("_finished_monotonic")
            public["elapsed_seconds"] = max(
                0, int((finished or now) - (started or submitted)),
            )
            public["elapsed_ms"] = public["elapsed_seconds"] * 1000
            public["message"] = self._status_message(record)
            return public

    async def _execute_admitted(
        self,
        user_key: str,
        work: Callable[[], T],
        result_mapper: Callable[[T], Any] | None,
    ) -> T:
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.wait_timeout_seconds,
                )
                acquired = True
            except TimeoutError as exc:
                await self._finish(
                    user_key,
                    state="failed",
                    error_message="等待 AI 推理资源超时，请稍后重试。",
                )
                raise AIInferenceCapacityExceededError(
                    "等待 AI 推理槽位超时，请稍后重试",
                ) from exc

            async with self._state_lock:
                self._active_count += 1
                record = self._tasks_by_user[user_key]
                record["state"] = "running"
                if record.get("operation") in {"training_draft", "training_draft_update"}:
                    record["stage"] = "training_framework"
                record["started_at"] = _timestamp()
                record["_started_monotonic"] = time.monotonic()

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, work)
            public_result = result_mapper(result) if result_mapper else None
            await self._finish(
                user_key,
                state="succeeded",
                result=copy.deepcopy(public_result),
            )
            return result
        except AIInferenceCapacityExceededError:
            raise
        except BaseException as exc:
            failure_stage = str(getattr(exc, "failure_stage", "") or "")
            failure_type = str(getattr(exc, "failure_type", "") or "")
            retryable = bool(getattr(exc, "retryable", True))
            completed_stages = list(getattr(exc, "completed_stages", []) or [])
            framework_id = getattr(exc, "framework_id", None)
            facts_snapshot_id = getattr(exc, "facts_snapshot_id", None)
            summary_version = getattr(exc, "summary_version", None)
            reason = _FAILURE_LABELS.get(failure_type, "该阶段未能完成")
            message = f"{reason}，已完成阶段可复用；请点击“重新生成”" if retryable else reason
            await self._finish(
                user_key,
                state="failed",
                error_message=message if failure_stage else "AI 生成失败，请查看失败阶段后点击“重新生成”。",
                failure_stage=failure_stage or None,
                failure_type=failure_type or None,
                retryable=retryable,
                completed_stages=completed_stages,
                framework_id=framework_id,
                facts_snapshot_id=facts_snapshot_id,
                summary_version=summary_version,
            )
            raise
        finally:
            if acquired:
                async with self._state_lock:
                    self._active_count -= 1
                self._semaphore.release()

    async def _finish(
        self,
        user_key: str,
        *,
        state: str,
        error_message: str | None = None,
        failure_stage: str | None = None,
        failure_type: str | None = None,
        retryable: bool = True,
        completed_stages: list[str] | None = None,
        framework_id: str | None = None,
        facts_snapshot_id: str | None = None,
        summary_version: str | None = None,
        result: Any = None,
    ) -> None:
        now = time.monotonic()
        async with self._state_lock:
            self._pending_users.discard(user_key)
            record = self._tasks_by_user.get(user_key)
            if record is None or record.get("state") in _TERMINAL_STATES:
                return
            record["state"] = state
            if state == "succeeded" and record.get("operation") in {"training_draft", "training_draft_update"}:
                record["stage"] = "complete"
            record["finished_at"] = _timestamp()
            record["_finished_monotonic"] = now
            record["error_message"] = error_message
            record["failure_stage"] = failure_stage
            record["failure_type"] = failure_type
            record["retryable"] = retryable
            record["completed_stages"] = list(completed_stages or [])
            if failure_stage:
                record["stage"] = failure_stage
            record["framework_id"] = framework_id
            record["facts_snapshot_id"] = facts_snapshot_id
            record["summary_version"] = summary_version
            record["result"] = result

    def _prune_terminal_locked(self, user_key: str, now: float) -> None:
        record = self._tasks_by_user.get(user_key)
        if not record or record.get("state") in _ACTIVE_STATES:
            return
        finished = record.get("_finished_monotonic")
        if finished is not None and now - finished >= self.terminal_retention_seconds:
            self._tasks_by_user.pop(user_key, None)

    @staticmethod
    def _status_message(record: dict[str, Any]) -> str:
        label = _OPERATION_LABELS.get(record.get("operation"), "AI 内容")
        state = record.get("state")
        if state == "waiting":
            return f"{label}正在等待执行，页面可以安全刷新"
        if state == "running":
            return f"正在生成{label}，页面可以安全刷新"
        if state == "succeeded":
            return f"{label}已生成"
        return str(record.get("error_message") or f"{label}生成失败，请重试")

    def _forget_background(self, task: asyncio.Task[T]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def shutdown(self, *, wait: bool = True) -> None:
        """关闭独立 executor；供受控服务退出或测试清理使用。"""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
