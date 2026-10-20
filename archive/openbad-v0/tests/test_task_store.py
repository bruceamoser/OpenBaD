from __future__ import annotations

import time
from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import NodeModel, NodeStatus, TaskModel, TaskStatus
from openbad.tasks.store import TaskStore


@pytest.fixture()
def store(tmp_path: Path) -> TaskStore:
    conn = initialize_state_db(tmp_path / "state.db")
    return TaskStore(conn)


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


def test_task_create_and_read(store: TaskStore) -> None:
    task = TaskModel.new("Buy milk")
    store.create_task(task)

    fetched = store.get_task(task.task_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id
    assert fetched.title == "Buy milk"
    assert fetched.status == TaskStatus.PENDING


def test_task_get_missing_returns_none(store: TaskStore) -> None:
    assert store.get_task("does-not-exist") is None


def test_task_update_status(store: TaskStore) -> None:
    task = TaskModel.new("Write tests")
    store.create_task(task)

    store.update_task_status(task.task_id, TaskStatus.RUNNING)

    fetched = store.get_task(task.task_id)
    assert fetched is not None
    assert fetched.status == TaskStatus.RUNNING


def test_task_list_all(store: TaskStore) -> None:
    for title in ("A", "B", "C"):
        store.create_task(TaskModel.new(title))

    tasks = store.list_tasks()
    assert len(tasks) == 3


def test_task_list_filtered_by_status(store: TaskStore) -> None:
    t1 = TaskModel.new("Pending task")
    t2 = TaskModel.new("Running task")
    store.create_task(t1)
    store.create_task(t2)
    store.update_task_status(t2.task_id, TaskStatus.RUNNING)

    pending = store.list_tasks(status=TaskStatus.PENDING)
    running = store.list_tasks(status=TaskStatus.RUNNING)

    assert len(pending) == 1
    assert pending[0].task_id == t1.task_id
    assert len(running) == 1
    assert running[0].task_id == t2.task_id


def test_task_list_pagination(store: TaskStore) -> None:
    for i in range(5):
        store.create_task(TaskModel.new(f"Task {i}"))

    page1 = store.list_tasks(limit=3, offset=0)
    page2 = store.list_tasks(limit=3, offset=3)

    assert len(page1) == 3
    assert len(page2) == 2
    ids_page1 = {t.task_id for t in page1}
    ids_page2 = {t.task_id for t in page2}
    assert ids_page1.isdisjoint(ids_page2)


# ---------------------------------------------------------------------------
# Node CRUD
# ---------------------------------------------------------------------------


def test_node_create_and_read(store: TaskStore) -> None:
    task = TaskModel.new("Parent")
    store.create_task(task)

    node = NodeModel.new(task.task_id, "Reason step")
    store.create_node(node)

    fetched = store.get_node(node.node_id)
    assert fetched is not None
    assert fetched.node_id == node.node_id
    assert fetched.task_id == task.task_id
    assert fetched.status == NodeStatus.PENDING


def test_node_get_missing_returns_none(store: TaskStore) -> None:
    assert store.get_node("no-such-node") is None


def test_node_update_status(store: TaskStore) -> None:
    task = TaskModel.new("Task with node")
    store.create_task(task)
    node = NodeModel.new(task.task_id, "Step")
    store.create_node(node)

    store.update_node_status(node.node_id, NodeStatus.RUNNING)

    fetched = store.get_node(node.node_id)
    assert fetched is not None
    assert fetched.status == NodeStatus.RUNNING


def test_node_list_by_task(store: TaskStore) -> None:
    task = TaskModel.new("Multi-node task")
    store.create_task(task)

    for label in ("step-1", "step-2", "step-3"):
        store.create_node(NodeModel.new(task.task_id, label))

    nodes = store.list_nodes(task.task_id)
    assert len(nodes) == 3
    assert all(n.task_id == task.task_id for n in nodes)


# ---------------------------------------------------------------------------
# Event ordering (append-only)
# ---------------------------------------------------------------------------


def test_event_append_and_ordering(store: TaskStore) -> None:
    task = TaskModel.new("Event task")
    store.create_task(task)

    # Insert events with slight time separation to guarantee ordering
    for i, etype in enumerate(("created", "started", "finished")):
        store.append_event(task.task_id, etype, payload={"seq": i})
        time.sleep(0.01)

    events = store.list_events(task.task_id)

    assert len(events) == 3
    assert [e["event_type"] for e in events] == ["created", "started", "finished"]
    assert events[0]["payload"] == {"seq": 0}
    assert events[2]["payload"] == {"seq": 2}


def test_event_append_preserves_payload(store: TaskStore) -> None:
    task = TaskModel.new("Payload task")
    store.create_task(task)

    payload = {"key": "value", "nums": [1, 2, 3]}
    store.append_event(task.task_id, "test_event", payload=payload)

    events = store.list_events(task.task_id)
    assert events[0]["payload"] == payload


def test_event_node_id_optional(store: TaskStore) -> None:
    task = TaskModel.new("Node-less event")
    store.create_task(task)

    store.append_event(task.task_id, "task_level_event")

    events = store.list_events(task.task_id)
    assert len(events) == 1
    assert events[0]["node_id"] is None
