"""Web 同步任务的单机准入、singleflight 与并发控制。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


class SyncInProgressError(RuntimeError):
    """同一用户已有同步正在执行或排队。"""


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

    @property
    def pending_count(self) -> int:
        """返回当前正在执行和等待执行的用户数。"""
        return len(self._pending_users)

    async def run(self, user_key: str, work: Callable[[], T]) -> T:
        """接纳并执行一个用户同步；超出边界时立即抛出明确异常。"""
        async with self._state_lock:
            if user_key in self._pending_users:
                raise SyncInProgressError("该用户已有同步任务正在进行")
            if len(self._pending_users) >= self.max_pending:
                raise SyncCapacityExceededError("服务器同步任务已满，请稍后重试")
            self._pending_users.add(user_key)

        try:
            async with self._semaphore:
                return await asyncio.to_thread(work)
        finally:
            async with self._state_lock:
                self._pending_users.discard(user_key)
