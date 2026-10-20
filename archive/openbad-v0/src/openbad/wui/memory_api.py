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

import logging
import time
from typing import Any

from aiohttp import web

from openbad.memory.episodic import EpisodicMemory
from openbad.memory.procedural import ProceduralMemory
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory

log = logging.getLogger(__name__)

_KEY_STM = "memory_api_stm"
_KEY_EPISODIC = "memory_api_episodic"
_KEY_SEMANTIC = "memory_api_semantic"
_KEY_PROCEDURAL = "memory_api_procedural"


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
    stm: ShortTermMemory = request.app[_KEY_STM]
    episodic: EpisodicMemory = request.app[_KEY_EPISODIC]
    semantic: SemanticMemory = request.app[_KEY_SEMANTIC]
    procedural: ProceduralMemory = request.app[_KEY_PROCEDURAL]
    usage = stm.usage()
    return web.json_response({
        "stm": {
            "tokens_used": usage.get("tokens_used", 0),
            "tokens_max": usage.get("tokens_max", 0),
            "entry_count": len(stm.list_keys()),
            "oldest_entry_age": usage.get("oldest_entry_age", 0.0),
        },
        "episodic": {"entry_count": episodic.size()},
        "semantic": {"entry_count": semantic.size()},
        "procedural": {"entry_count": procedural.size()},
    })


async def _get_stm(request: web.Request) -> web.Response:
    stm: ShortTermMemory = request.app[_KEY_STM]
    keys = stm.list_keys()
    entries = []
    now = time.time()
    for key in keys:
        entry = stm.read(key)
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
    usage = stm.usage()
    return web.json_response({"entries": entries, "usage": usage})


async def _get_episodic(request: web.Request) -> web.Response:
    episodic: EpisodicMemory = request.app[_KEY_EPISODIC]
    limit_str = request.rel_url.query.get("limit", "50")
    try:
        limit = int(limit_str)
    except (TypeError, ValueError):
        limit = 50
    entries = episodic.recent(n=limit)
    return web.json_response({
        "entries": [_entry_to_dict(e) for e in reversed(entries)],
        "total": episodic.size(),
    })


async def _get_semantic(request: web.Request) -> web.Response:
    semantic: SemanticMemory = request.app[_KEY_SEMANTIC]
    keys = semantic.list_keys()
    entries = []
    for key in keys:
        entry = semantic.read(key)
        if entry is not None:
            d = _entry_to_dict(entry)
            d["has_vector"] = semantic.get_vector(key) is not None
            entries.append(d)
    return web.json_response({
        "entries": entries,
        "total": semantic.size(),
    })


async def _get_procedural(request: web.Request) -> web.Response:
    procedural: ProceduralMemory = request.app[_KEY_PROCEDURAL]
    keys = procedural.list_keys()
    entries = []
    for key in keys:
        entry = procedural.read(key)
        if entry is not None:
            d = _entry_to_dict(entry)
            skill = procedural.get_skill(key)
            d["skill"] = _skill_to_dict(skill) if skill else None
            entries.append(d)
    return web.json_response({
        "entries": entries,
        "total": procedural.size(),
    })


async def _get_entry(request: web.Request) -> web.Response:
    key = request.match_info["key"]
    # Search all tiers
    for store in (
        request.app[_KEY_STM],
        request.app[_KEY_EPISODIC],
        request.app[_KEY_SEMANTIC],
        request.app[_KEY_PROCEDURAL],
    ):
        entry = store.read(key)
        if entry is not None:
            return web.json_response(_entry_to_dict(entry))
    raise web.HTTPNotFound(text=f"memory entry {key!r} not found")


async def _post_recall(request: web.Request) -> web.Response:
    semantic: SemanticMemory = request.app[_KEY_SEMANTIC]
    episodic: EpisodicMemory = request.app[_KEY_EPISODIC]
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

    results: list[dict[str, Any]] = []

    # Semantic search — scored
    try:
        for entry, score in semantic.search(query, top_k=top_k):
            item: dict[str, Any] = {
                "key": entry.key,
                "value": str(entry.value),
                "tier": "semantic",
                "score": score,
                "metadata": entry.metadata,
            }
            results.append(item)
    except Exception:
        log.exception("Semantic recall failed")

    # Episodic prefix search — unscored, ordered by recency
    try:
        for entry in episodic.query(query)[:top_k]:
            item = {
                "key": entry.key,
                "value": str(entry.value),
                "tier": "episodic",
                "score": 0.0,
                "metadata": entry.metadata,
            }
            results.append(item)
    except Exception:
        log.exception("Episodic recall failed")

    results.sort(key=lambda r: r["score"], reverse=True)
    return web.json_response({"results": results[:top_k]})


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_memory_routes(
    app: web.Application,
    *,
    stm: ShortTermMemory,
    episodic: EpisodicMemory,
    semantic: SemanticMemory,
    procedural: ProceduralMemory,
) -> None:
    """Register Memory Inspector API routes on *app*.

    Parameters
    ----------
    app:
        The :class:`aiohttp.web.Application` to register routes on.
    stm, episodic, semantic, procedural:
        The live memory store instances (typically the same singletons
        used by :mod:`openbad.wui.chat_pipeline`).
    """
    app[_KEY_STM] = stm
    app[_KEY_EPISODIC] = episodic
    app[_KEY_SEMANTIC] = semantic
    app[_KEY_PROCEDURAL] = procedural

    app.router.add_get("/api/memory/stats", _get_stats)
    app.router.add_get("/api/memory/stm", _get_stm)
    app.router.add_get("/api/memory/episodic", _get_episodic)
    app.router.add_get("/api/memory/semantic", _get_semantic)
    app.router.add_get("/api/memory/procedural", _get_procedural)
    app.router.add_get("/api/memory/entry/{key}", _get_entry)
    app.router.add_post("/api/memory/recall", _post_recall)
