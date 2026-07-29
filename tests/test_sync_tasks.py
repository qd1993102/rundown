"""测试 Web 异步同步任务的持久化、状态机与重启恢复。"""

import json
import stat

import pytest

from src.sync_tasks import SyncTaskStore


def test_sync_task_store_persists_private_user_scoped_task(tmp_path):
    path = tmp_path / "runner" / "sync-tasks.json"
    store = SyncTaskStore(path, runner_instance_id="runner-a")

    task = store.create(
        task_id="st_private",
        mode="batch",
        from_date="2026-06-29",
        to_date="2026-07-29",
        force=False,
    )

    assert task.status == "queued"
    assert task.progress == {
        "kind": "stage", "current": 0, "total": 4, "label": "等待执行",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["tasks"][0]["task_id"] == "st_private"
    assert "api_key" not in path.read_text(encoding="utf-8")


def test_sync_task_store_records_progress_and_terminal_result(tmp_path):
    store = SyncTaskStore(tmp_path / "sync-tasks.json", "runner-a")
    store.create(
        task_id="st_success", mode="single",
        from_date="2026-07-29", to_date="2026-07-29", force=True,
    )

    store.mark_running(
        "st_success", stage="authenticating", current=1, total=4,
        label="正在验证数据源",
    )
    store.update_progress(
        "st_success", stage="syncing_activities", current=3, total=4,
        label="正在同步运动记录",
        items={
            "current": 8,
            "total": 11,
            "date": "2026-07-29",
            "metric": "activities",
            "outcome": "completed",
        },
    )
    running = store.get("st_success")
    assert running is not None
    assert running.progress["current"] == 3
    assert running.progress["items"] == {
        "current": 8,
        "total": 11,
        "date": "2026-07-29",
        "metric": "activities",
        "outcome": "completed",
    }
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["tasks"][0]["progress"]["items"]["current"] == 8
    task = store.mark_succeeded(
        "st_success",
        result={"from_date": "2026-07-29", "to_date": "2026-07-29"},
    )

    assert task.status == "succeeded"
    assert task.stage == "done"
    assert task.progress["current"] == 4
    assert task.finished_at is not None
    assert task.error is None

    with pytest.raises(ValueError, match="终态"):
        store.update_progress(
            "st_success", stage="syncing_metrics", current=2, total=4,
            label="不应允许",
        )


def test_new_runner_marks_old_active_tasks_interrupted(tmp_path):
    path = tmp_path / "sync-tasks.json"
    first = SyncTaskStore(path, "runner-a")
    first.create(
        task_id="st_queued", mode="single",
        from_date="2026-07-29", to_date="2026-07-29", force=False,
    )
    first.create(
        task_id="st_running", mode="batch",
        from_date="2026-07-01", to_date="2026-07-29", force=False,
    )
    first.mark_running(
        "st_running", stage="syncing_metrics", current=2, total=4,
        label="正在同步健康指标",
    )

    restarted = SyncTaskStore(path, "runner-b")

    for task_id in ("st_queued", "st_running"):
        task = restarted.get(task_id)
        assert task is not None
        assert task.status == "interrupted"
        assert task.error == {
            "code": "sync_interrupted",
            "message": "服务已重启，请重新发起同步",
            "retryable": True,
            "action": "retry",
        }
