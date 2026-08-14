"""在线 AI 推理的 Web 准入控制测试。"""

import asyncio
import threading

import pytest

from src.ai_inference_coordinator import (
    AIInferenceCapacityExceededError,
    AIInferenceCoordinator,
    AIInferenceInProgressError,
)


def test_ai_inference_coordinator_enforces_user_singleflight_and_bounded_waiting():
    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1,
            max_pending=2,
            wait_timeout_seconds=0.5,
        )
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def first_work() -> str:
            calls.append(threading.current_thread().name)
            started.set()
            assert release.wait(timeout=1)
            return "first"

        first = asyncio.create_task(coordinator.run(
            "user-a",
            first_work,
            operation="weekly_review",
            result_mapper=lambda value: {"report": value},
        ))
        assert await asyncio.to_thread(started.wait, 1)
        running = await coordinator.current_status("user-a")
        assert running["operation"] == "weekly_review"
        assert running["state"] == "running"
        assert "页面可以安全刷新" in running["message"]

        with pytest.raises(AIInferenceInProgressError):
            await coordinator.run("user-a", lambda: "duplicate")

        second = asyncio.create_task(
            coordinator.run("user-b", lambda: calls.append("second") or "second"),
        )
        await asyncio.sleep(0.01)
        waiting = await coordinator.current_status("user-b")
        assert waiting["state"] == "waiting"
        assert len(calls) == 1
        assert calls[0].startswith("neurun-ai")
        assert coordinator.in_flight == 1
        assert coordinator.pending_count == 2

        with pytest.raises(AIInferenceCapacityExceededError):
            await coordinator.run("user-c", lambda: "third")

        release.set()
        assert await first == "first"
        assert await second == "second"
        succeeded = await coordinator.current_status("user-a")
        assert succeeded["state"] == "succeeded"
        assert succeeded["result"] == {"report": "first"}
        assert calls[-1] == "second"
        assert coordinator.in_flight == 0
        assert coordinator.pending_count == 0
        coordinator.shutdown()

    asyncio.run(exercise())


def test_ai_inference_wait_timeout_does_not_start_work_thread():
    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1,
            max_pending=2,
            wait_timeout_seconds=0.02,
        )
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def blocking_work() -> None:
            started.set()
            assert release.wait(timeout=1)

        first = asyncio.create_task(coordinator.run("user-a", blocking_work))
        assert await asyncio.to_thread(started.wait, 1)

        with pytest.raises(AIInferenceCapacityExceededError):
            await coordinator.run("user-b", lambda: calls.append("started"))

        assert calls == []
        assert coordinator.pending_count == 1
        release.set()
        await first
        coordinator.shutdown()

    asyncio.run(exercise())


def test_ai_inference_start_returns_before_background_work_finishes():
    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1, max_pending=1, wait_timeout_seconds=0.5,
        )
        started = threading.Event()
        release = threading.Event()

        def work() -> str:
            started.set()
            assert release.wait(timeout=1)
            return "done"

        execution = await coordinator.start("user-a", work, operation="training_draft")
        assert not execution.done()
        assert await asyncio.to_thread(started.wait, 1)
        assert (await coordinator.current_status("user-a"))["state"] == "running"
        release.set()
        assert await execution == "done"
        assert (await coordinator.current_status("user-a"))["state"] == "succeeded"
        coordinator.shutdown()

    asyncio.run(exercise())


def test_cancelled_caller_keeps_user_and_capacity_until_thread_finishes():
    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1,
            max_pending=2,
            wait_timeout_seconds=0.5,
        )
        started = threading.Event()
        release = threading.Event()

        def blocking_work() -> None:
            started.set()
            assert release.wait(timeout=1)

        request = asyncio.create_task(coordinator.run("user-a", blocking_work))
        assert await asyncio.to_thread(started.wait, 1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

        assert coordinator.in_flight == 1
        assert coordinator.pending_count == 1
        status = await coordinator.current_status("user-a")
        assert status["state"] == "running"
        with pytest.raises(AIInferenceInProgressError):
            await coordinator.run("user-a", lambda: None)

        release.set()
        for _ in range(100):
            if coordinator.pending_count == 0:
                break
            await asyncio.sleep(0.005)
        assert coordinator.in_flight == 0
        assert coordinator.pending_count == 0
        coordinator.shutdown()

    asyncio.run(exercise())


def test_terminal_ai_task_status_expires():
    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1,
            max_pending=1,
            wait_timeout_seconds=0.5,
            terminal_retention_seconds=0.01,
        )

        assert await coordinator.run("user-a", lambda: "done") == "done"
        assert (await coordinator.current_status("user-a"))["state"] == "succeeded"
        await asyncio.sleep(0.02)
        assert await coordinator.current_status("user-a") is None
        coordinator.shutdown()

    asyncio.run(exercise())


def test_failed_training_dag_task_exposes_stage_and_resume_contract():
    class StageFailure(RuntimeError):
        failure_stage = "near_term_schedule"
        failure_type = "provider_timeout"
        retryable = True
        completed_stages = ["training_framework"]

    async def exercise() -> None:
        coordinator = AIInferenceCoordinator(
            max_concurrency=1, max_pending=1, wait_timeout_seconds=0.5,
        )

        with pytest.raises(StageFailure):
            await coordinator.run("user-a", lambda: (_ for _ in ()).throw(StageFailure("近期课表超时")), operation="training_draft")
        status = await coordinator.current_status("user-a")
        assert status["state"] == "failed"
        assert status["request_id"] == status["task_id"]
        assert status["stage"] == "near_term_schedule"
        assert status["failure_stage"] == "near_term_schedule"
        assert status["failure_type"] == "provider_timeout"
        assert status["retryable"] is True
        assert status["completed_stages"] == ["training_framework"]
        assert "重新生成" in status["message"] or "近期课表超时" in status["message"]
        coordinator.shutdown()

    asyncio.run(exercise())
