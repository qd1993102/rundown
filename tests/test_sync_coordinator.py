"""测试 Web 同步准入、singleflight 和受控并发。"""

import asyncio
import threading
import time

import pytest

from src.sync_coordinator import (
    SyncCapacityExceededError,
    SyncCoordinator,
    SyncInProgressError,
)


def test_sync_work_does_not_block_event_loop():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=2)

    async def scenario():
        started = time.perf_counter()
        task = asyncio.create_task(
            coordinator.run("runner-a", lambda: time.sleep(0.15))
        )
        await asyncio.sleep(0.02)
        event_loop_delay = time.perf_counter() - started
        await task
        return event_loop_delay

    assert asyncio.run(scenario()) < 0.1


def test_sync_concurrency_is_bounded():
    coordinator = SyncCoordinator(max_concurrency=2, max_pending=10)
    lock = threading.Lock()
    active = 0
    peak = 0

    def work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
        finally:
            with lock:
                active -= 1

    async def scenario():
        await asyncio.gather(*(
            coordinator.run(f"runner-{index}", work)
            for index in range(6)
        ))

    asyncio.run(scenario())
    assert peak == 2
    assert coordinator.pending_count == 0


def test_same_user_is_singleflight():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=2)
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        release.wait(timeout=2)

    async def scenario():
        first = asyncio.create_task(coordinator.run("runner-a", work))
        while not started.is_set():
            await asyncio.sleep(0.005)
        with pytest.raises(SyncInProgressError):
            await coordinator.run("runner-a", lambda: None)
        release.set()
        await first

    asyncio.run(scenario())


def test_capacity_excess_fails_fast():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=2)
    started = threading.Event()
    release = threading.Event()

    def blocking_work():
        started.set()
        release.wait(timeout=2)

    async def scenario():
        first = asyncio.create_task(coordinator.run("runner-a", blocking_work))
        while not started.is_set():
            await asyncio.sleep(0.005)
        second = asyncio.create_task(
            coordinator.run("runner-b", lambda: time.sleep(0.01))
        )
        await asyncio.sleep(0)
        with pytest.raises(SyncCapacityExceededError):
            await coordinator.run("runner-c", lambda: None)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())


def test_failed_work_releases_user_and_capacity():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=1)

    def fail():
        raise RuntimeError("simulated failure")

    async def scenario():
        with pytest.raises(RuntimeError, match="simulated failure"):
            await coordinator.run("runner-a", fail)
        assert coordinator.pending_count == 0
        assert await coordinator.run("runner-a", lambda: "retried") == "retried"

    asyncio.run(scenario())


def test_submit_returns_immediately_and_keeps_background_task():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=2)
    started = threading.Event()
    release = threading.Event()
    events = []

    def work():
        started.set()
        release.wait(timeout=2)
        return "done"

    async def scenario():
        task_id = await coordinator.submit(
            "runner-a", "st_async", work,
            on_accept=lambda: events.append("accepted"),
            on_started=lambda: events.append("started"),
            on_succeeded=lambda result: events.append(("succeeded", result)),
            on_failed=lambda exc: events.append(("failed", type(exc).__name__)),
        )
        assert task_id == "st_async"
        assert coordinator.pending_count == 1
        while not started.is_set():
            await asyncio.sleep(0.005)
        release.set()
        await coordinator.wait("st_async")

    asyncio.run(scenario())

    assert events == ["accepted", "started", ("succeeded", "done")]
    assert coordinator.pending_count == 0


def test_submit_duplicate_exposes_existing_task_id():
    coordinator = SyncCoordinator(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        release.wait(timeout=2)

    async def scenario():
        await coordinator.submit("runner-a", "st_first", work)
        while not started.is_set():
            await asyncio.sleep(0.005)
        with pytest.raises(SyncInProgressError) as captured:
            await coordinator.submit("runner-a", "st_second", lambda: None)
        assert captured.value.task_id == "st_first"
        release.set()
        await coordinator.wait("st_first")

    asyncio.run(scenario())
