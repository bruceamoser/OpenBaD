from __future__ import annotations

import time
from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskModel
from openbad.tasks.scheduler import QuietHoursWindow, SchedulerConfig, TaskScheduler
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def scheduler_and_store(tmp_path: Path):
    conn = initialize_state_db(tmp_path / "state.db")
    store = TaskStore(conn)
    dispatched: list[tuple[str, str]] = []

    def callback(task: TaskModel, lease_id: str) -> None:
        dispatched.append((task.task_id, lease_id))

    sched = TaskScheduler(conn, "worker-1", callback)
    return sched, store, dispatched


# ---------------------------------------------------------------------------
# Single dispatch under concurrent wake
# ---------------------------------------------------------------------------


def test_single_dispatch_per_task(scheduler_and_store) -> None:
    """A task is dispatched exactly once even if tick is called twice."""
    sched, store, dispatched = scheduler_and_store

    task = TaskModel.new("Due task")
    store.create_task(task)

    sched.tick()
    sched.tick()  # second tick: lease still held → no duplicate

    task_ids = [t for t, _ in dispatched]
    assert task_ids.count(task.task_id) == 1


def test_undispatchable_task_not_dispatched_twice_across_workers(
    tmp_path: Path,
) -> None:
    """Two schedulers racing for the same task: only one wins."""
    conn = initialize_state_db(tmp_path / "state.db")
    store = TaskStore(conn)

    task = TaskModel.new("Shared task")
    store.create_task(task)

    wins: list[str] = []

    def make_callback(worker_id: str):
        def cb(t: TaskModel, _lid: str) -> None:
            wins.append(worker_id)

        return cb

    sched_a = TaskScheduler(conn, "worker-A", make_callback("A"))
    sched_b = TaskScheduler(conn, "worker-B", make_callback("B"))

    sched_a.tick()
    sched_b.tick()

    assert len(wins) == 1  # only one winner


# ---------------------------------------------------------------------------
# Quiet-hours suppression
# ---------------------------------------------------------------------------


def test_quiet_hours_suppresses_non_urgent(tmp_path: Path) -> None:
    conn = initialize_state_db(tmp_path / "state.db")
    store = TaskStore(conn)
    dispatched: list[str] = []

    def callback(task: TaskModel, _lid: str) -> None:
        dispatched.append(task.task_id)

    task = TaskModel.new("Suppressed task")
    store.create_task(task)

    # Configure an all-day quiet window (hour 0 to 23)
    config = SchedulerConfig(quiet_hours=[QuietHoursWindow(0, 23)])
    sched = TaskScheduler(conn, "worker-1", callback, config)

    result = sched.tick(urgent=False)

    assert result == []
    assert dispatched == []


def test_quiet_hours_bypassed_when_urgent(tmp_path: Path) -> None:
    conn = initialize_state_db(tmp_path / "state.db")
    store = TaskStore(conn)
    dispatched: list[str] = []

    def callback(task: TaskModel, _lid: str) -> None:
        dispatched.append(task.task_id)

    task = TaskModel.new("Urgent task")
    store.create_task(task)

    # All-day quiet window
    config = SchedulerConfig(quiet_hours=[QuietHoursWindow(0, 23)])
    sched = TaskScheduler(conn, "worker-1", callback, config)

    result = sched.tick(urgent=True)  # bypass quiet hours

    assert len(result) == 1
    assert dispatched == [task.task_id]


def test_quiet_hours_window_overnight() -> None:
    window = QuietHoursWindow(22, 6)
    assert window.is_active(23)
    assert window.is_active(0)
    assert window.is_active(5)
    assert not window.is_active(12)


def test_quiet_hours_window_daytime() -> None:
    window = QuietHoursWindow(8, 18)
    assert window.is_active(12)
    assert not window.is_active(7)
    assert not window.is_active(19)


# ---------------------------------------------------------------------------
# Lease prevents duplicate dispatch
# ---------------------------------------------------------------------------


def test_lease_prevents_duplicate_dispatch(tmp_path: Path) -> None:
    conn = initialize_state_db(tmp_path / "state.db")
    store = TaskStore(conn)
    dispatched: list[str] = []

    def callback(task: TaskModel, _lid: str) -> None:
        dispatched.append(task.task_id)

    task = TaskModel.new("Leased task")
    store.create_task(task)

    # First scheduler dispatches and holds the lease
    sched_a = TaskScheduler(conn, "worker-A", callback, SchedulerConfig(lease_ttl_seconds=60))
    sched_b = TaskScheduler(conn, "worker-B", callback, SchedulerConfig(lease_ttl_seconds=60))

    sched_a.tick()
    sched_b.tick()  # B cannot acquire the held lease

    assert dispatched.count(task.task_id) == 1


def test_not_due_task_skipped(scheduler_and_store) -> None:
    sched, store, dispatched = scheduler_and_store

    future = time.time() + 3600  # 1 hour from now
    task = TaskModel.new("Future task", due_at=future)
    store.create_task(task)

    sched.tick()

    assert dispatched == []


def test_due_task_is_dispatched(scheduler_and_store) -> None:
    sched, store, dispatched = scheduler_and_store

    past = time.time() - 1  # 1 second ago
    task = TaskModel.new("Past due task", due_at=past)
    store.create_task(task)

    sched.tick()

    assert len(dispatched) == 1
    assert dispatched[0][0] == task.task_id
