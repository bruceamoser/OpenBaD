from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskModel, TaskStatus
from openbad.tasks.store import TaskStore
from openbad.wui.task_api import setup_task_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def store(db):
    return TaskStore(db)


@pytest.fixture()
def app(db) -> web.Application:
    a = web.Application()
    setup_task_routes(a, db)
    return a


@pytest.fixture()
async def client(app, aiohttp_client) -> TestClient:
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# List tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_empty(client: TestClient) -> None:
    resp = await client.get("/api/tasks")
    assert resp.status == 200
    data = await resp.json()
    assert data["tasks"] == []


@pytest.mark.asyncio
async def test_list_tasks_returns_all(client: TestClient, store: TaskStore) -> None:
    task_a = TaskModel.new("Task A")
    task_b = TaskModel.new("Task B")
    store.create_task(task_a)
    store.create_task(task_b)

    resp = await client.get("/api/tasks")
    data = await resp.json()
    assert len(data["tasks"]) == 2


@pytest.mark.asyncio
async def test_list_tasks_filtered_by_status(client: TestClient, store: TaskStore) -> None:
    t = TaskModel.new("A")
    store.create_task(t)
    store.update_task_status(t.task_id, TaskStatus.RUNNING)

    resp = await client.get("/api/tasks?status=running")
    data = await resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_list_tasks_invalid_status_returns_400(client: TestClient) -> None:
    resp = await client.get("/api/tasks?status=nonexistent")
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_returns_201(client: TestClient) -> None:
    resp = await client.post("/api/tasks", json={"title": "New task"})
    assert resp.status == 201
    data = await resp.json()
    assert data["title"] == "New task"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_task_missing_title_returns_400(client: TestClient) -> None:
    resp = await client.post("/api/tasks", json={"description": "No title"})
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Get task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Detail task")
    store.create_task(task)

    resp = await client.get(f"/api/tasks/{task.task_id}")
    assert resp.status == 200
    data = await resp.json()
    assert data["task_id"] == task.task_id


@pytest.mark.asyncio
async def test_get_task_not_found(client: TestClient) -> None:
    resp = await client.get("/api/tasks/ghost-task")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# Pause / Resume / Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_task(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Running task")
    store.create_task(task)
    store.update_task_status(task.task_id, TaskStatus.RUNNING)

    resp = await client.post(f"/api/tasks/{task.task_id}/pause")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "blocked"


@pytest.mark.asyncio
async def test_resume_task(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Blocked task")
    store.create_task(task)
    store.update_task_status(task.task_id, TaskStatus.RUNNING)
    store.update_task_status(task.task_id, TaskStatus.BLOCKED)

    resp = await client.post(f"/api/tasks/{task.task_id}/resume")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_task(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Pending task")
    store.create_task(task)

    resp = await client.post(f"/api/tasks/{task.task_id}/cancel")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_pause_nonexistent_returns_404(client: TestClient) -> None:
    resp = await client.post("/api/tasks/ghost/pause")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_cancel_invalid_transition_returns_400(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Done task")
    store.create_task(task)
    store.update_task_status(task.task_id, TaskStatus.RUNNING)
    store.update_task_status(task.task_id, TaskStatus.DONE)

    resp = await client.post(f"/api/tasks/{task.task_id}/cancel")
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Event history ordered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_history_ordered(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("Event task")
    store.create_task(task)
    store.append_event(task.task_id, "status_change", payload={"status": "running"})
    store.append_event(task.task_id, "note", payload={"text": "hello"})

    resp = await client.get(f"/api/tasks/{task.task_id}/events")
    assert resp.status == 200
    data = await resp.json()
    events = data["events"]
    assert len(events) == 2
    # Events should be ordered oldest-first by the store
    types = [e["event_type"] for e in events]
    assert types == ["status_change", "note"]


@pytest.mark.asyncio
async def test_event_history_empty(client: TestClient, store: TaskStore) -> None:
    task = TaskModel.new("No events")
    store.create_task(task)

    resp = await client.get(f"/api/tasks/{task.task_id}/events")
    assert resp.status == 200
    data = await resp.json()
    assert data["events"] == []
