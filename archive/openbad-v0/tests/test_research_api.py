from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from openbad.plugins.mcp_audit import MCPAuditStore, initialize_audit_db
from openbad.plugins.registry import CapabilityRegistry, PermissionPolicy
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db
from openbad.tasks.scheduler import QuietHoursWindow, SchedulerConfig
from openbad.wui.research_api import setup_research_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "research.db"))
    conn.row_factory = sqlite3.Row
    initialize_research_db(conn)
    initialize_audit_db(conn)
    return conn


@pytest.fixture()
def app(db) -> web.Application:
    a = web.Application()
    setup_research_routes(a, db)
    return a


@pytest.fixture()
async def client(app, aiohttp_client) -> TestClient:
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# Research endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_research_empty(client: TestClient) -> None:
    resp = await client.get("/api/research")
    assert resp.status == 200
    data = await resp.json()
    assert data["nodes"] == []


@pytest.mark.asyncio
async def test_list_research_returns_pending(client: TestClient, db) -> None:
    queue = ResearchQueue(db)
    queue.enqueue("Find X", priority=5)
    queue.enqueue("Find Y", priority=3)

    resp = await client.get("/api/research")
    data = await resp.json()
    assert len(data["nodes"]) == 2


@pytest.mark.asyncio
async def test_get_research_by_id(client: TestClient, db) -> None:
    queue = ResearchQueue(db)
    node = queue.enqueue("Find Z", priority=1)

    resp = await client.get(f"/api/research/{node.node_id}")
    assert resp.status == 200
    data = await resp.json()
    assert data["node_id"] == node.node_id
    assert data["title"] == "Find Z"


@pytest.mark.asyncio
async def test_get_research_not_found(client: TestClient) -> None:
    resp = await client.get("/api/research/ghost-node")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_create_research_node(client: TestClient) -> None:
    resp = await client.post("/api/research", json={"title": "Find A", "priority": -1})
    assert resp.status == 201
    data = await resp.json()
    assert data["title"] == "Find A"
    assert data["priority"] == -1


@pytest.mark.asyncio
async def test_patch_research_node(client: TestClient, db) -> None:
    queue = ResearchQueue(db)
    node = queue.enqueue("Find B", priority=2)

    resp = await client.patch(
        f"/api/research/{node.node_id}",
        json={"title": "Find B2", "description": "updated", "priority": 0},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["title"] == "Find B2"
    assert data["description"] == "updated"
    assert data["priority"] == 0


@pytest.mark.asyncio
async def test_complete_research_node(client: TestClient, db) -> None:
    queue = ResearchQueue(db)
    node = queue.enqueue("Find C", priority=1)

    resp = await client.post(f"/api/research/{node.node_id}/complete")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "dequeued"


# ---------------------------------------------------------------------------
# Capability endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_empty(client: TestClient) -> None:
    resp = await client.get("/api/capabilities")
    assert resp.status == 200
    data = await resp.json()
    assert data["capabilities"] == []


@pytest.mark.asyncio
async def test_capabilities_with_registry(db, aiohttp_client) -> None:
    from openbad.plugins.manifest import CapabilityEntry, CapabilityManifest

    policy = PermissionPolicy({"file.read"})
    registry = CapabilityRegistry(policy)
    manifest = CapabilityManifest(
        name="test_pack",
        version="1.0",
        module="test.pack",
        capabilities=[
            CapabilityEntry(
                id="test_pack.do_thing",
                permissions=["file.read"],
                description="Does a thing",
            )
        ],
    )
    registry.register(manifest)

    a = web.Application()
    setup_research_routes(a, db, registry=registry)
    cli = await aiohttp_client(a)

    resp = await cli.get("/api/capabilities")
    data = await resp.json()
    assert len(data["capabilities"]) == 1
    assert data["capabilities"][0]["capability_id"] == "test_pack.do_thing"


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_empty_no_filter(client: TestClient) -> None:
    resp = await client.get("/api/mcp/audit")
    assert resp.status == 200
    data = await resp.json()
    assert data["records"] == []


@pytest.mark.asyncio
async def test_audit_by_task_id(client: TestClient, db) -> None:
    audit = MCPAuditStore(db)
    audit.record(
        "session-1",
        "read_file",
        success=True,
        task_id="task-99",
        run_id="run-1",
    )

    resp = await client.get("/api/mcp/audit?task_id=task-99")
    data = await resp.json()
    assert len(data["records"]) == 1
    assert data["records"][0]["task_id"] == "task-99"


@pytest.mark.asyncio
async def test_audit_by_run_id(client: TestClient, db) -> None:
    audit = MCPAuditStore(db)
    audit.record(
        "session-2",
        "write_file",
        success=False,
        task_id="task-100",
        run_id="run-special",
    )

    resp = await client.get("/api/mcp/audit?run_id=run-special")
    data = await resp.json()
    assert len(data["records"]) == 1
    assert data["records"][0]["run_id"] == "run-special"


# ---------------------------------------------------------------------------
# Scheduler state endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_state_default(client: TestClient) -> None:
    resp = await client.get("/api/scheduler/state")
    assert resp.status == 200
    data = await resp.json()
    assert data["quiet_hour_active"] is False
    assert data["poll_limit"] == 10
    assert data["quiet_hours"] == []


@pytest.mark.asyncio
async def test_scheduler_state_with_config(db, aiohttp_client) -> None:
    cfg = SchedulerConfig(
        lease_ttl_seconds=120.0,
        poll_limit=5,
        quiet_hours=[QuietHoursWindow(0, 23)],  # always active
    )
    a = web.Application()
    setup_research_routes(a, db, scheduler_config=cfg)
    cli = await aiohttp_client(a)

    resp = await cli.get("/api/scheduler/state")
    data = await resp.json()
    assert data["quiet_hour_active"] is True
    assert data["poll_limit"] == 5
    assert len(data["quiet_hours"]) == 1
