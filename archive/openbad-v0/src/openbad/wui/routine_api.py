"""Routine API endpoints for the OpenBaD WUI.

Routes:
- GET    /api/routines                     — list all routines
- POST   /api/routines                     — create a routine
- GET    /api/routines/{routine_id}        — get routine detail
- PATCH  /api/routines/{routine_id}        — update routine fields
- DELETE /api/routines/{routine_id}        — delete a routine
- POST   /api/routines/{routine_id}/toggle — enable/disable
- POST   /api/routines/{routine_id}/run    — trigger immediate execution
- GET    /api/routines/{routine_id}/runs   — list past runs
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, UTC

from aiohttp import web

from openbad.routines import Routine, RunStatus
from openbad.routines.store import RoutineStore
from openbad.state.db import initialize_state_db
from openbad.tasks.recurrence import compute_next_due, validate_recurrence_rule

_APP_KEY = "routine_store"


def _routine_to_dict(r: Routine) -> dict:
    return {
        "routine_id": r.routine_id,
        "name": r.name,
        "description": r.description,
        "body_md": r.body_md,
        "recurrence_rule": r.recurrence_rule,
        "next_run_at": r.next_run_at,
        "enabled": r.enabled,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _run_to_dict(run) -> dict:
    return {
        "run_id": run.run_id,
        "routine_id": run.routine_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": str(run.status),
        "output": run.output,
        "error": run.error,
        "tokens_used": run.tokens_used,
    }


async def _list_routines(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    routines = store.list_all()
    return web.json_response({"routines": [_routine_to_dict(r) for r in routines]})


async def _create_routine(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    body = await request.json()

    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    body_md = body.get("body_md", "").strip()
    if not body_md:
        return web.json_response({"error": "body_md is required"}, status=400)

    recurrence_rule = body.get("recurrence_rule") or None
    next_run_at = body.get("next_run_at")

    if recurrence_rule:
        try:
            validate_recurrence_rule(recurrence_rule)
        except Exception as exc:
            return web.json_response({"error": f"Invalid recurrence rule: {exc}"}, status=400)
        if next_run_at is None:
            next_run_at = compute_next_due(
                recurrence_rule,
                after=datetime.fromtimestamp(time.time(), tz=UTC),
            )

    routine = Routine.create(
        name=name,
        body_md=body_md,
        description=body.get("description", ""),
        recurrence_rule=recurrence_rule,
        next_run_at=next_run_at,
        enabled=body.get("enabled", True),
    )
    store.create(routine)
    return web.json_response(_routine_to_dict(routine), status=201)


async def _get_routine(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    routine = store.get(rid)
    if routine is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_routine_to_dict(routine))


async def _update_routine(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    body = await request.json()

    kwargs: dict = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "description" in body:
        kwargs["description"] = body["description"]
    if "body_md" in body:
        kwargs["body_md"] = body["body_md"]
    if "recurrence_rule" in body:
        rule = body["recurrence_rule"] or None
        if rule:
            try:
                validate_recurrence_rule(rule)
            except Exception as exc:
                return web.json_response({"error": f"Invalid recurrence rule: {exc}"}, status=400)
        kwargs["recurrence_rule"] = rule
        # Recompute next_run if rule changed
        if rule and "next_run_at" not in body:
            kwargs["next_run_at"] = compute_next_due(
                rule, after=datetime.fromtimestamp(time.time(), tz=UTC),
            )
    if "next_run_at" in body:
        kwargs["next_run_at"] = body["next_run_at"]
    if "enabled" in body:
        kwargs["enabled"] = body["enabled"]

    updated = store.update(rid, **kwargs)
    if updated is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_routine_to_dict(updated))


async def _delete_routine(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    if not store.delete(rid):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"deleted": True})


async def _toggle_routine(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    routine = store.get(rid)
    if routine is None:
        return web.json_response({"error": "not found"}, status=404)

    new_enabled = not routine.enabled
    # Recompute next_run when re-enabling
    next_run_at = routine.next_run_at
    if new_enabled and routine.recurrence_rule and not next_run_at:
        next_run_at = compute_next_due(
            routine.recurrence_rule,
            after=datetime.fromtimestamp(time.time(), tz=UTC),
        )

    updated = store.update(rid, enabled=new_enabled, next_run_at=next_run_at)
    return web.json_response(_routine_to_dict(updated))


async def _trigger_run(request: web.Request) -> web.Response:
    """Immediately execute a routine (does not affect schedule)."""
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    routine = store.get(rid)
    if routine is None:
        return web.json_response({"error": "not found"}, status=404)

    from openbad.routines.daemon import _execute_routine
    asyncio.ensure_future(_execute_routine(routine, store))

    return web.json_response({"status": "triggered", "routine_id": rid})


async def _list_runs(request: web.Request) -> web.Response:
    store: RoutineStore = request.app[_APP_KEY]
    rid = request.match_info["routine_id"]
    limit = int(request.query.get("limit", "20"))
    runs = store.list_runs(rid, limit=limit)
    return web.json_response({"runs": [_run_to_dict(r) for r in runs]})


def setup_routine_routes(app: web.Application) -> None:
    """Register routine API routes on the app."""
    conn = initialize_state_db()
    app[_APP_KEY] = RoutineStore(conn)

    app.router.add_get("/api/routines", _list_routines)
    app.router.add_post("/api/routines", _create_routine)
    app.router.add_get("/api/routines/{routine_id}", _get_routine)
    app.router.add_patch("/api/routines/{routine_id}", _update_routine)
    app.router.add_delete("/api/routines/{routine_id}", _delete_routine)
    app.router.add_post("/api/routines/{routine_id}/toggle", _toggle_routine)
    app.router.add_post("/api/routines/{routine_id}/run", _trigger_run)
    app.router.add_get("/api/routines/{routine_id}/runs", _list_runs)
