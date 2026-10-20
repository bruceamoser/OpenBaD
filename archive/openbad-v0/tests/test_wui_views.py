"""Integration tests: WUI task/research/audit views render without errors.

These tests wire up both :func:`setup_task_routes` and
:func:`setup_research_routes` on a single aiohttp app, then exercise the
complete request/response cycle as the HTML views would.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from openbad.plugins.mcp_audit import MCPAuditStore, initialize_audit_db
from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskModel, TaskStatus
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db
from openbad.tasks.store import TaskStore
from openbad.wui.research_api import setup_research_routes
from openbad.wui.task_api import setup_task_routes

BUILD_DIR = Path(__file__).resolve().parent.parent / "src" / "openbad" / "wui" / "build"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def task_db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def research_db(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "research.db"))
    conn.row_factory = sqlite3.Row
    initialize_research_db(conn)
    initialize_audit_db(conn)
    return conn


@pytest.fixture()
def app(task_db, research_db) -> web.Application:
    a = web.Application()
    setup_task_routes(a, task_db)
    setup_research_routes(a, research_db)

    # Serve the static HTML views
    if BUILD_DIR.is_dir():

        async def _tasks_view(_req: web.Request) -> web.FileResponse:
            return web.FileResponse(BUILD_DIR / "tasks.html")

        async def _research_view(_req: web.Request) -> web.FileResponse:
            return web.FileResponse(BUILD_DIR / "research.html")

        a.router.add_get("/tasks.html", _tasks_view)
        a.router.add_get("/research.html", _research_view)

    return a


@pytest.fixture()
async def client(app, aiohttp_client) -> TestClient:
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# Task list / detail rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_list_empty_state(client: TestClient) -> None:
    """API returns empty list — view renders empty-state."""
    resp = await client.get("/api/tasks")
    assert resp.status == 200
    data = await resp.json()
    assert data["tasks"] == []


@pytest.mark.asyncio
async def test_task_list_populated_state(client: TestClient, task_db) -> None:
    """Multiple tasks — list endpoint returns all."""
    store = TaskStore(task_db)
    store.create_task(TaskModel.new("Alpha"))
    store.create_task(TaskModel.new("Beta"))

    resp = await client.get("/api/tasks")
    data = await resp.json()
    assert len(data["tasks"]) == 2


@pytest.mark.asyncio
async def test_task_detail_rendering(client: TestClient, task_db) -> None:
    """Detail endpoint returns task + event history."""
    store = TaskStore(task_db)
    task = TaskModel.new("Detail task")
    store.create_task(task)
    store.append_event(task.task_id, "status_change", payload={"status": "running"})

    detail_resp = await client.get(f"/api/tasks/{task.task_id}")
    events_resp = await client.get(f"/api/tasks/{task.task_id}/events")

    assert detail_resp.status == 200
    assert events_resp.status == 200

    detail = await detail_resp.json()
    events = await events_resp.json()

    assert detail["title"] == "Detail task"
    assert len(events["events"]) == 1


@pytest.mark.asyncio
async def test_task_blocked_state(client: TestClient, task_db) -> None:
    """Blocked task detail shows `blocked` status."""
    store = TaskStore(task_db)
    task = TaskModel.new("Blocked task")
    store.create_task(task)
    store.update_task_status(task.task_id, TaskStatus.RUNNING)
    store.update_task_status(task.task_id, TaskStatus.BLOCKED)

    resp = await client.get(f"/api/tasks/{task.task_id}")
    data = await resp.json()
    assert data["status"] == "blocked"


@pytest.mark.asyncio
async def test_task_view_html_served(client: TestClient) -> None:
    """tasks.html static file is served if the build directory exists."""
    resp = await client.get("/tasks.html")
    if BUILD_DIR.is_dir() and (BUILD_DIR / "tasks.html").exists():
        assert resp.status == 200
        text = await resp.text()
        assert "tasks" in text.lower()
    else:
        # Build dir absent in CI — skip gracefully
        assert resp.status in (200, 404)


# ---------------------------------------------------------------------------
# Research queue and audit panel rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_panel_empty_state(client: TestClient) -> None:
    resp = await client.get("/api/research")
    assert resp.status == 200
    data = await resp.json()
    assert data["nodes"] == []


@pytest.mark.asyncio
async def test_research_panel_populated(client: TestClient, research_db) -> None:
    queue = ResearchQueue(research_db)
    queue.enqueue("Investigate X", priority=2)
    queue.enqueue("Investigate Y", priority=5)

    resp = await client.get("/api/research")
    data = await resp.json()
    assert len(data["nodes"]) == 2


@pytest.mark.asyncio
async def test_audit_panel_empty_state(client: TestClient) -> None:
    resp = await client.get("/api/mcp/audit")
    assert resp.status == 200
    data = await resp.json()
    assert data["records"] == []


@pytest.mark.asyncio
async def test_audit_panel_with_records(client: TestClient, research_db) -> None:
    audit = MCPAuditStore(research_db)
    audit.record("s1", "read_file", success=True, task_id="t1", run_id="r1")

    resp = await client.get("/api/mcp/audit?task_id=t1")
    data = await resp.json()
    assert len(data["records"]) == 1
    assert data["records"][0]["tool_name"] == "read_file"


@pytest.mark.asyncio
async def test_research_view_html_served(client: TestClient) -> None:
    resp = await client.get("/research.html")
    if BUILD_DIR.is_dir() and (BUILD_DIR / "research.html").exists():
        assert resp.status == 200
        text = await resp.text()
        assert "research" in text.lower()
    else:
        assert resp.status in (200, 404)


# ---------------------------------------------------------------------------
# Scheduler and capabilities sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_state_section(client: TestClient) -> None:
    resp = await client.get("/api/scheduler/state")
    assert resp.status == 200
    data = await resp.json()
    assert "quiet_hour_active" in data
    assert "poll_limit" in data


@pytest.mark.asyncio
async def test_capabilities_section_empty(client: TestClient) -> None:
    resp = await client.get("/api/capabilities")
    assert resp.status == 200
    data = await resp.json()
    assert "capabilities" in data
