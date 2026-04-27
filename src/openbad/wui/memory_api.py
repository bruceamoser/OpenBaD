"""Memory Inspector API endpoints.

Registers the following routes on a supplied :class:`aiohttp.web.Application`:

- ``GET  /api/memory/stats``         — tier counts + STM token usage
- ``GET  /api/memory/stm``           — list all STM entries
- ``GET  /api/memory/episodic``      — list recent episodic entries
- ``GET  /api/memory/semantic``      — list all semantic entries
- ``GET  /api/memory/procedural``    — list all procedural entries (with skills)
- ``GET  /api/memory/entry/{key}``   — read a single entry from any tier
- ``POST /api/memory/recall``        — run a recall query (semantic + episodic)

Call :func:`setup_memory_routes` to register all routes.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from openbad.memory.controller import MemoryController

_KEY_CTRL = "memory_api_controller"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    """Serialise a MemoryEntry for JSON response."""
    return {
        "key": entry.key,
        "value": str(entry.value),
        "tier": entry.tier.value,
        "entry_id": entry.entry_id,
        "created_at": entry.created_at,
        "accessed_at": entry.accessed_at,
        "access_count": entry.access_count,
        "ttl_seconds": entry.ttl_seconds,
        "context": entry.context,
        "metadata": entry.metadata,
    }


def _skill_to_dict(skill: Any) -> dict[str, Any]:
    """Serialise a Skill for JSON response."""
    return skill.to_dict() if hasattr(skill, "to_dict") else {"raw": str(skill)}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _get_stats(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    return web.json_response(ctrl.stats())


async def _get_stm(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    keys = ctrl.stm.list_keys()
    entries = []
    now = time.time()
    for key in keys:
        entry = ctrl.stm.read(key)
        if entry is not None:
            d = _entry_to_dict(entry)
            # Add computed fields useful for the UI
            d["age_seconds"] = now - entry.created_at if entry.created_at else 0
            ttl = entry.ttl_seconds
            if ttl and ttl > 0 and entry.created_at:
                d["ttl_remaining"] = max(
                    0, ttl - (now - entry.created_at)
                )
            else:
                d["ttl_remaining"] = None
            entries.append(d)
    usage = ctrl.stm.usage()
    return web.json_response({"entries": entries, "usage": usage})


async def _get_episodic(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    limit_str = request.rel_url.query.get("limit", "50")
    try:
        limit = int(limit_str)
    except (TypeError, ValueError):
        limit = 50
    entries = ctrl.episodic.recent(n=limit)
    return web.json_response({
        "entries": [_entry_to_dict(e) for e in reversed(entries)],
        "total": ctrl.episodic.size(),
    })


async def _get_semantic(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    keys = ctrl.semantic.list_keys()
    entries = []
    for key in keys:
        entry = ctrl.semantic.read(key)
        if entry is not None:
            d = _entry_to_dict(entry)
            d["has_vector"] = ctrl.semantic.get_vector(key) is not None
            entries.append(d)
    return web.json_response({
        "entries": entries,
        "total": ctrl.semantic.size(),
    })


async def _get_procedural(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    keys = ctrl.procedural.list_keys()
    entries = []
    for key in keys:
        entry = ctrl.procedural.read(key)
        if entry is not None:
            d = _entry_to_dict(entry)
            skill = ctrl.procedural.get_skill(key)
            d["skill"] = _skill_to_dict(skill) if skill else None
            entries.append(d)
    return web.json_response({
        "entries": entries,
        "total": ctrl.procedural.size(),
    })


async def _get_entry(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    key = request.match_info["key"]
    entry = ctrl.read(key)
    if entry is None:
        raise web.HTTPNotFound(text=f"memory entry {key!r} not found")
    return web.json_response(_entry_to_dict(entry))


async def _post_recall(request: web.Request) -> web.Response:
    ctrl: MemoryController = request.app[_KEY_CTRL]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    query = str(body.get("query", "")).strip()
    if not query:
        raise web.HTTPBadRequest(text="query is required")

    top_k = body.get("top_k", 10)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="top_k must be an integer") from exc

    results = ctrl.recall(query, top_k=top_k)
    return web.json_response({"results": results})


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_memory_routes(
    app: web.Application,
    controller: MemoryController,
) -> None:
    """Register Memory Inspector API routes on *app*.

    Parameters
    ----------
    app:
        The :class:`aiohttp.web.Application` to register routes on.
    controller:
        A fully-initialised :class:`MemoryController` instance.
    """
    app[_KEY_CTRL] = controller

    app.router.add_get("/api/memory/stats", _get_stats)
    app.router.add_get("/api/memory/stm", _get_stm)
    app.router.add_get("/api/memory/episodic", _get_episodic)
    app.router.add_get("/api/memory/semantic", _get_semantic)
    app.router.add_get("/api/memory/procedural", _get_procedural)
    app.router.add_get("/api/memory/entry/{key}", _get_entry)
    app.router.add_post("/api/memory/recall", _post_recall)
