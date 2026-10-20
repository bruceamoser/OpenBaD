"""Research, capability, MCP audit, and scheduler state API endpoints.

Registers the following routes on a supplied :class:`aiohttp.web.Application`:

- ``GET /api/research``                        — list pending research nodes
- ``POST /api/research``                       — create a research node
- ``GET /api/research/{research_id}``          — get research node by id
- ``PATCH /api/research/{research_id}``        — update a pending research node
- ``POST /api/research/{research_id}/complete``— mark a research node complete
- ``GET /api/capabilities``                    — list registered capabilities
- ``GET /api/mcp/audit``                       — recent MCP audit records
- ``GET /api/scheduler/state``                 — scheduler config / quiet-hour status

Call :func:`setup_research_routes` to register all routes.
"""

from __future__ import annotations

import sqlite3

from aiohttp import web

from openbad.plugins.mcp_audit import MCPAuditStore
from openbad.plugins.registry import CapabilityRegistry, PermissionPolicy
from openbad.tasks.research_queue import ResearchQueue
from openbad.tasks.scheduler import SchedulerConfig

_KEY_RESEARCH = "research_api_queue"
_KEY_CAPABILITIES = "research_api_registry"
_KEY_AUDIT = "research_api_audit"
_KEY_SCHEDULER = "research_api_scheduler_cfg"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _list_research(request: web.Request) -> web.Response:
    queue: ResearchQueue = request.app[_KEY_RESEARCH]
    nodes = queue.list_pending()
    return web.json_response({"nodes": [n.to_dict() for n in nodes]})


async def _get_research(request: web.Request) -> web.Response:
    queue: ResearchQueue = request.app[_KEY_RESEARCH]
    research_id = request.match_info["research_id"]
    node = queue.get(research_id)
    if node is None:
        raise web.HTTPNotFound(text=f"research node {research_id!r} not found")
    return web.json_response(node.to_dict())


async def _create_research(request: web.Request) -> web.Response:
    queue: ResearchQueue = request.app[_KEY_RESEARCH]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")
    title = str(body.get("title", "")).strip()
    if not title:
        raise web.HTTPBadRequest(text="title is required")
    try:
        priority = int(body.get("priority", 0))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="priority must be an integer") from exc
    node = queue.enqueue(
        title,
        description=str(body.get("description", "")),
        priority=priority,
        source_task_id=(str(body.get("source_task_id")).strip() or None)
        if body.get("source_task_id") is not None
        else None,
    )
    return web.json_response(node.to_dict(), status=201)


async def _patch_research(request: web.Request) -> web.Response:
    queue: ResearchQueue = request.app[_KEY_RESEARCH]
    research_id = request.match_info["research_id"]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")
    priority_raw = body.get("priority")
    try:
        priority = None if priority_raw is None else int(priority_raw)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="priority must be an integer") from exc
    try:
        node = queue.update(
            research_id,
            title=None if body.get("title") is None else str(body.get("title")).strip(),
            description=None if body.get("description") is None else str(body.get("description")),
            priority=priority,
            source_task_id=None
            if body.get("source_task_id") is None
            else (str(body.get("source_task_id")).strip() or None),
        )
    except KeyError:
        raise web.HTTPNotFound(text=f"research node {research_id!r} not found") from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(node.to_dict())


async def _complete_research(request: web.Request) -> web.Response:
    queue: ResearchQueue = request.app[_KEY_RESEARCH]
    research_id = request.match_info["research_id"]
    try:
        node = queue.complete(research_id)
    except KeyError:
        raise web.HTTPNotFound(text=f"research node {research_id!r} not found") from None
    return web.json_response(node.to_dict())


async def _list_capabilities(request: web.Request) -> web.Response:
    registry: CapabilityRegistry = request.app[_KEY_CAPABILITIES]
    entries = registry.list_all()
    return web.json_response(
        {
            "capabilities": [
                {
                    "capability_id": e.capability_id,
                    "manifest_name": e.plugin_name,
                    "permissions": e.permissions,
                    "system1": e.system1,
                    "description": e.description,
                }
                for e in entries
            ]
        }
    )


async def _list_audit(request: web.Request) -> web.Response:
    audit: MCPAuditStore = request.app[_KEY_AUDIT]
    task_id = request.rel_url.query.get("task_id")
    run_id = request.rel_url.query.get("run_id")
    if task_id:
        records = audit.query_by_task(task_id)
    elif run_id:
        records = audit.query_by_run(run_id)
    else:
        records = []
    return web.json_response({"records": [r.to_dict() for r in records]})


async def _get_scheduler_state(request: web.Request) -> web.Response:
    cfg: SchedulerConfig = request.app[_KEY_SCHEDULER]
    return web.json_response(
        {
            "quiet_hour_active": cfg.is_quiet_hour(),
            "poll_limit": cfg.poll_limit,
            "lease_ttl_seconds": cfg.lease_ttl_seconds,
            "quiet_hours": [
                {"start_hour": w.start_hour, "end_hour": w.end_hour}
                for w in cfg.quiet_hours
            ],
        }
    )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_research_routes(
    app: web.Application,
    conn: sqlite3.Connection,
    *,
    registry: CapabilityRegistry | None = None,
    scheduler_config: SchedulerConfig | None = None,
) -> None:
    """Register research, capability, audit, and scheduler routes on *app*.

    Parameters
    ----------
    app:
        The :class:`aiohttp.web.Application` to register routes on.
    conn:
        An open SQLite connection to the state database (must have
        ``research_queue`` and ``mcp_audit`` tables).
    registry:
        Optional pre-populated :class:`~openbad.plugins.registry.CapabilityRegistry`.
        A registry with an open :class:`~openbad.plugins.registry.PermissionPolicy`
        is created if omitted.
    scheduler_config:
        Optional :class:`~openbad.tasks.scheduler.SchedulerConfig`.
        Defaults to an empty config if omitted.
    """
    app[_KEY_RESEARCH] = ResearchQueue(conn)
    app[_KEY_CAPABILITIES] = registry or CapabilityRegistry(PermissionPolicy(None))
    app[_KEY_AUDIT] = MCPAuditStore(conn)
    app[_KEY_SCHEDULER] = scheduler_config or SchedulerConfig()

    app.router.add_get("/api/research", _list_research)
    app.router.add_post("/api/research", _create_research)
    app.router.add_get("/api/research/{research_id}", _get_research)
    app.router.add_patch("/api/research/{research_id}", _patch_research)
    app.router.add_post("/api/research/{research_id}/complete", _complete_research)
    app.router.add_get("/api/capabilities", _list_capabilities)
    app.router.add_get("/api/mcp/audit", _list_audit)
    app.router.add_get("/api/scheduler/state", _get_scheduler_state)
