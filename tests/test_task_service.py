from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import NodeStatus, TaskStatus
from openbad.tasks.service import TaskService


@pytest.fixture()
def svc(tmp_path: Path) -> TaskService:
    conn = initialize_state_db(tmp_path / "state.db")
    return TaskService(conn)


# ---------------------------------------------------------------------------
# Create and retrieve task
# ---------------------------------------------------------------------------


def test_create_and_retrieve_task(svc: TaskService) -> None:
    task = svc.create_task("Write unit tests", description="High priority")

    fetched = svc.get_task(task.task_id)
    assert fetched is not None
    assert fetched.title == "Write unit tests"
    assert fetched.description == "High priority"
    assert fetched.status == TaskStatus.PENDING


def test_get_nonexistent_task_returns_none(svc: TaskService) -> None:
    assert svc.get_task("ghost") is None


def test_list_tasks_returns_all(svc: TaskService) -> None:
    svc.create_task("A")
    svc.create_task("B")

    tasks = svc.list_tasks()
    assert len(tasks) == 2


def test_list_tasks_filtered_by_status(svc: TaskService) -> None:
    t1 = svc.create_task("Pending")
    t2 = svc.create_task("Running")
    svc.transition_task(t2.task_id, TaskStatus.RUNNING)

    pending = svc.list_tasks(status=TaskStatus.PENDING)
    running = svc.list_tasks(status=TaskStatus.RUNNING)

    assert len(pending) == 1 and pending[0].task_id == t1.task_id
    assert len(running) == 1 and running[0].task_id == t2.task_id


# ---------------------------------------------------------------------------
# Task status transitions
# ---------------------------------------------------------------------------


def test_task_valid_transition(svc: TaskService) -> None:
    task = svc.create_task("Transitioning task")
    updated = svc.transition_task(task.task_id, TaskStatus.RUNNING)

    assert updated.status == TaskStatus.RUNNING


def test_task_invalid_transition_raises(svc: TaskService) -> None:
    task = svc.create_task("Will fail")
    svc.transition_task(task.task_id, TaskStatus.RUNNING)
    svc.transition_task(task.task_id, TaskStatus.DONE)

    with pytest.raises(ValueError, match="Illegal task transition"):
        svc.transition_task(task.task_id, TaskStatus.RUNNING)


def test_cancel_task(svc: TaskService) -> None:
    task = svc.create_task("Doomed task")
    cancelled = svc.cancel_task(task.task_id)

    assert cancelled.status == TaskStatus.CANCELLED


def test_transition_nonexistent_task_raises(svc: TaskService) -> None:
    with pytest.raises(KeyError):
        svc.transition_task("no-such-id", TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# Node creation and lifecycle
# ---------------------------------------------------------------------------


def test_create_and_retrieve_node(svc: TaskService) -> None:
    task = svc.create_task("Parent")
    node = svc.create_node(task.task_id, "Step 1")

    fetched = svc.get_node(node.node_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id
    assert fetched.status == NodeStatus.PENDING


def test_list_nodes_for_task(svc: TaskService) -> None:
    task = svc.create_task("Multi-step")
    svc.create_node(task.task_id, "A")
    svc.create_node(task.task_id, "B")

    nodes = svc.list_nodes(task.task_id)
    assert len(nodes) == 2


def test_node_valid_transition(svc: TaskService) -> None:
    task = svc.create_task("Task")
    node = svc.create_node(task.task_id, "Node")

    updated = svc.transition_node(node.node_id, NodeStatus.RUNNING)
    assert updated.status == NodeStatus.RUNNING


def test_node_invalid_transition_raises(svc: TaskService) -> None:
    task = svc.create_task("Task")
    node = svc.create_node(task.task_id, "Node")

    with pytest.raises(ValueError, match="Illegal node transition"):
        svc.transition_node(node.node_id, NodeStatus.DONE)  # pending -> done is illegal


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_transition_records_event(svc: TaskService) -> None:
    task = svc.create_task("Evented task")
    svc.transition_task(task.task_id, TaskStatus.RUNNING)

    events = svc.list_events(task.task_id)
    assert any(e["event_type"] == "task_status_changed" for e in events)


def test_append_and_list_events(svc: TaskService) -> None:
    task = svc.create_task("Events task")
    svc.append_event(task.task_id, "custom_event", payload={"key": "val"})

    events = svc.list_events(task.task_id)
    custom = [e for e in events if e["event_type"] == "custom_event"]
    assert len(custom) == 1
    assert custom[0]["payload"] == {"key": "val"}


# ---------------------------------------------------------------------------
# Lease helpers
# ---------------------------------------------------------------------------


def test_acquire_task_lease(svc: TaskService) -> None:
    task = svc.create_task("Leased task")
    lease = svc.acquire_task_lease(task.task_id, "worker-1")

    assert lease is not None
    assert lease.owner_id == "worker-1"


def test_lease_prevents_duplicate_acquisition(svc: TaskService) -> None:
    task = svc.create_task("Guarded task")
    lease_a = svc.acquire_task_lease(task.task_id, "worker-A")
    lease_b = svc.acquire_task_lease(task.task_id, "worker-B")

    assert lease_a is not None
    assert lease_b is None


def test_release_task_lease(svc: TaskService) -> None:
    task = svc.create_task("Releasable task")
    lease = svc.acquire_task_lease(task.task_id, "worker-A")
    assert lease is not None

    released = svc.release_task_lease(lease.lease_id, "worker-A")
    assert released is True


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def test_task_to_dict_round_trip(svc: TaskService) -> None:
    task = svc.create_task("Serializable")
    d = svc.task_to_dict(task)

    assert d["title"] == "Serializable"
    assert d["status"] == "pending"
    assert isinstance(d["task_id"], str)


def test_node_to_dict_round_trip(svc: TaskService) -> None:
    task = svc.create_task("Node parent")
    node = svc.create_node(task.task_id, "Node child")
    d = svc.node_to_dict(node)

    assert d["title"] == "Node child"
    assert d["status"] == "pending"


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def test_list_active_tasks_excludes_terminal(svc: TaskService) -> None:
    t1 = svc.create_task("Active")
    t2 = svc.create_task("Done")
    svc.transition_task(t2.task_id, TaskStatus.RUNNING)
    svc.transition_task(t2.task_id, TaskStatus.DONE)

    rows = svc.list_active_tasks()
    ids = {r["task_id"] for r in rows}
    assert t1.task_id in ids
    assert t2.task_id not in ids


def test_list_completed_tasks_only_terminal(svc: TaskService) -> None:
    svc.create_task("Active")
    t2 = svc.create_task("Done")
    svc.transition_task(t2.task_id, TaskStatus.RUNNING)
    svc.transition_task(t2.task_id, TaskStatus.DONE)

    rows = svc.list_completed_tasks()
    ids = {r["task_id"] for r in rows}
    assert t2.task_id in ids
    assert len(ids) == 1


def test_top_pending_user_task_skips_system(svc: TaskService) -> None:
    from openbad.tasks.models import TaskKind, TaskModel

    # Create a system task directly in the store
    sys_task = TaskModel.new("System task", kind=TaskKind.SYSTEM, owner="system")
    svc._store.create_task(sys_task)

    user_task = svc.create_task("User task", owner="user")
    top = svc.top_pending_user_task()
    assert top is not None
    assert top.task_id == user_task.task_id


def test_top_pending_user_task_none_when_empty(svc: TaskService) -> None:
    assert svc.top_pending_user_task() is None


def test_find_pending_system_task(svc: TaskService) -> None:
    from openbad.tasks.models import TaskKind, TaskModel

    title = "Endocrine follow-up: re-enable research"
    sys_task = TaskModel.new(title, kind=TaskKind.SYSTEM, owner="endocrine-doctor")
    svc._store.create_task(sys_task)

    found = svc.find_pending_system_task(title=title)
    assert found is not None
    assert found.task_id == sys_task.task_id


def test_find_pending_system_task_not_found(svc: TaskService) -> None:
    assert svc.find_pending_system_task(title="nonexistent") is None


def test_pending_system_task_exists(svc: TaskService) -> None:
    from openbad.tasks.models import TaskKind, TaskModel

    sys_task = TaskModel.new(
        "Endocrine follow-up: re-enable research", kind=TaskKind.SYSTEM, owner="system"
    )
    svc._store.create_task(sys_task)

    assert svc.pending_system_task_exists(title_prefix="Endocrine follow-up")
    assert not svc.pending_system_task_exists(title_prefix="Nonexistent")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_get_instance(tmp_path: Path) -> None:
    TaskService.reset_instance()
    try:
        svc = TaskService.get_instance(tmp_path / "singleton.db")
        assert svc is TaskService.get_instance()
    finally:
        TaskService.reset_instance()
