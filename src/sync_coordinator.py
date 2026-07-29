"""Web 同步任务的单机准入、singleflight 与并发控制。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")
logger = logging.getLogger(__name__)


class SyncInProgressError(RuntimeError):
    """同一用户已有同步正在执行或排队。"""

    def __init__(self, message: str, task_id: str | None = None):
        super().__init__(message)
        self.task_id = task_id


class SyncCapacityExceededError(RuntimeError):
    """当前进程已达到同步任务接纳上限。"""


class SyncCoordinator:
    """在单个 Web 进程内限制同步并发和等待任务数量。

    ``max_pending`` 包含正在执行和等待执行的不同用户。同步函数在线程池
    中执行，避免阻塞 Starlette/uvicorn 的 asyncio 事件循环。
    """

    def __init__(self, max_concurrency: int, max_pending: int):
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须大于 0")
        if max_pending < max_concurrency:
            raise ValueError("max_pending 不能小于 max_concurrency")
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._state_lock = asyncio.Lock()
        self._pending_users: set[str] = set()
        self._active_task_ids: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_by_id: dict[str, asyncio.Task[None]] = {}

    @property
    def pending_count(self) -> int:
        """返回当前正在执行和等待执行的用户数。"""
        return len(self._pending_users)

    async def run(self, user_key: str, work: Callable[[], T]) -> T:
        """接纳并执行一个用户同步；超出边界时立即抛出明确异常。"""
        async with self._state_lock:
            if user_key in self._pending_users:
                raise SyncInProgressError(
                    "该用户已有同步任务正在进行",
                    self._active_task_ids.get(user_key),
                )
            if len(self._pending_users) >= self.max_pending:
                raise SyncCapacityExceededError("服务器同步任务已满，请稍后重试")
            self._pending_users.add(user_key)

        try:
            async with self._semaphore:
                return await asyncio.to_thread(work)
        finally:
            async with self._state_lock:
                self._pending_users.discard(user_key)

    @staticmethod
    async def _call(callback: Callable[..., Any] | None, *args: Any) -> Any:
        if callback is None:
            return None
        return await asyncio.to_thread(callback, *args)

    async def submit(
        self,
        user_key: str,
        task_id: str,
        work: Callable[[], T],
        *,
        on_accept: Callable[[], Any] | None = None,
        on_started: Callable[[], Any] | None = None,
        on_succeeded: Callable[[T], Any] | None = None,
        on_failed: Callable[[Exception], Any] | None = None,
    ) -> str:
        """持久化接纳后立即返回，由受控后台协程继续执行同步。"""
        async with self._state_lock:
            if user_key in self._pending_users:
                raise SyncInProgressError(
                    "该用户已有同步任务正在进行",
                    self._active_task_ids.get(user_key),
                )
            if len(self._pending_users) >= self.max_pending:
                raise SyncCapacityExceededError("服务器同步任务已满，请稍后重试")

            # 任务记录必须先可靠落盘，才能占位并向调用方返回已接纳。
            await self._call(on_accept)
            self._pending_users.add(user_key)
            self._active_task_ids[user_key] = task_id

            try:
                background = asyncio.create_task(
                    self._execute_submitted(
                        user_key=user_key,
                        task_id=task_id,
                        work=work,
                        on_started=on_started,
                        on_succeeded=on_succeeded,
                        on_failed=on_failed,
                    )
                )
            except Exception:
                self._pending_users.discard(user_key)
                self._active_task_ids.pop(user_key, None)
                try:
                    await self._call(
                        on_failed,
                        RuntimeError("无法注册同步后台任务"),
                    )
                except Exception:
                    logger.error(
                        "无法记录同步任务注册失败: task_id=%s", task_id,
                    )
                raise

            self._background_tasks.add(background)
            self._background_by_id[task_id] = background
            background.add_done_callback(
                lambda finished, accepted_task_id=task_id: self._forget_background(
                    accepted_task_id, finished
                )
            )

        return task_id

    async def _execute_submitted(
        self,
        *,
        user_key: str,
        task_id: str,
        work: Callable[[], T],
        on_started: Callable[[], Any] | None,
        on_succeeded: Callable[[T], Any] | None,
        on_failed: Callable[[Exception], Any] | None,
    ) -> None:
        try:
            async with self._semaphore:
                await self._call(on_started)
                result = await asyncio.to_thread(work)
                await self._call(on_succeeded, result)
        except asyncio.CancelledError:
            # 进程退出时保留 queued/running；新实例会将其恢复为 interrupted。
            raise
        except Exception as exc:
            try:
                await self._call(on_failed, exc)
            except Exception as callback_exc:
                logger.error(
                    "无法记录同步任务失败终态: task_id=%s error_type=%s",
                    task_id,
                    type(callback_exc).__name__,
                )
        finally:
            async with self._state_lock:
                self._pending_users.discard(user_key)
                if self._active_task_ids.get(user_key) == task_id:
                    self._active_task_ids.pop(user_key, None)

    def _forget_background(
        self, task_id: str, finished: asyncio.Task[None],
    ) -> None:
        self._background_tasks.discard(finished)
        if self._background_by_id.get(task_id) is finished:
            self._background_by_id.pop(task_id, None)
        if not finished.cancelled():
            try:
                finished.exception()
            except Exception:
                logger.error(
                    "同步后台协程异常未被处理: task_id=%s", task_id,
                    exc_info=True,
                )

    async def wait(self, task_id: str) -> None:
        """等待一个当前实例任务结束；仅供测试和受控关闭流程使用。"""
        task = self._background_by_id.get(task_id)
        if task is not None:
            await asyncio.shield(task)
