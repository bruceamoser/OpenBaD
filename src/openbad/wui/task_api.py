"""Task and task-event API endpoints for the OpenBaD WUI.

Registers the following routes on a supplied :class:`aiohttp.web.Application`:

- ``GET  /api/tasks``                          — list tasks (optional ``?status=``)
- ``POST /api/tasks``                          — create a new task
- ``GET  /api/tasks/{task_id}``                — get task detail
- ``PATCH /api/tasks/{task_id}``               — update task metadata
- ``POST /api/tasks/{task_id}/pause``          — transition to BLOCKED
- ``POST /api/tasks/{task_id}/resume``         — transition to RUNNING
- ``POST /api/tasks/{task_id}/complete``       — transition to DONE
- ``POST /api/tasks/{task_id}/cancel``         — transition to CANCELLED
- ``GET  /api/tasks/{task_id}/events``         — list events, oldest-first

Call :func:`setup_task_routes` to register all routes.
"""

from __future__ import annotations

import sqlite3

from aiohttp import web

from openbad.tasks.models import TaskStatus
from openbad.tasks.service import TaskService
from openbad.tasks.store import TaskStore

_APP_KEY = "task_api_service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_to_dict(task) -> dict:  # type: ignore[type-arg]
    return {
        "task_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "status": str(task.status),
        "kind": str(task.kind),
        "priority": task.priority,
        "owner": task.owner,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "parent_task_id": task.parent_task_id,
        "due_at": task.due_at,
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _list_tasks(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    status_str = request.rel_url.query.get("status")
    status: TaskStatus | None = None
    if status_str:
        try:
            status = TaskStatus(status_str)
        except ValueError:
            raise web.HTTPBadRequest(text=f"invalid status: {status_str!r}") from None
    tasks = svc.list_tasks(status=status)
    return web.json_response({"tasks": [_task_to_dict(t) for t in tasks]})


async def _create_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")
    title = body.get("title", "").strip()
    if not title:
        raise web.HTTPBadRequest(text="title is required")
    task = svc.create_task(
        title,
        description=body.get("description", ""),
        owner=body.get("owner", "system"),
    )
    return web.json_response(_task_to_dict(task), status=201)


async def _get_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    task = svc.get_task(task_id)
    if task is None:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found")
    return web.json_response(_task_to_dict(task))


async def _pause_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    try:
        task = svc.transition_task(task_id, TaskStatus.BLOCKED)
    except KeyError:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found") from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(_task_to_dict(task))


async def _patch_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    title = body.get("title")
    description = body.get("description")
    owner = body.get("owner")
    try:
        task = svc.update_task(
            task_id,
            title=None if title is None else str(title).strip(),
            description=None if description is None else str(description),
            owner=None if owner is None else str(owner).strip(),
        )
    except KeyError:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found") from None
    return web.json_response(_task_to_dict(task))


async def _resume_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    try:
        task = svc.transition_task(task_id, TaskStatus.RUNNING)
    except KeyError:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found") from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(_task_to_dict(task))


async def _cancel_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    try:
        task = svc.transition_task(task_id, TaskStatus.CANCELLED)
    except KeyError:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found") from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(_task_to_dict(task))


async def _complete_task(request: web.Request) -> web.Response:
    svc: TaskService = request.app[_APP_KEY]
    task_id = request.match_info["task_id"]
    try:
        task = svc.complete_task(task_id)
    except KeyError:
        raise web.HTTPNotFound(text=f"task {task_id!r} not found") from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(_task_to_dict(task))


async def _list_task_events(request: web.Request) -> web.Response:
    store: TaskStore = request.app[_APP_KEY + "_store"]
    task_id = request.match_info["task_id"]
    events = store.list_events(task_id)
    return web.json_response({"task_id": task_id, "events": events})


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_task_routes(app: web.Application, conn: sqlite3.Connection) -> None:
    """Register all task API routes on *app*, backed by *conn*.

    Parameters
    ----------
    app:
        The :class:`aiohttp.web.Application` to register routes on.
    conn:
        An open SQLite connection to the state database.
    """
    app[_APP_KEY] = TaskService(conn)
    app[_APP_KEY + "_store"] = TaskStore(conn)

    app.router.add_get("/api/tasks", _list_tasks)
    app.router.add_post("/api/tasks", _create_task)
    app.router.add_get("/api/tasks/{task_id}", _get_task)
    app.router.add_patch("/api/tasks/{task_id}", _patch_task)
    app.router.add_post("/api/tasks/{task_id}/pause", _pause_task)
    app.router.add_post("/api/tasks/{task_id}/resume", _resume_task)
    app.router.add_post("/api/tasks/{task_id}/complete", _complete_task)
    app.router.add_post("/api/tasks/{task_id}/cancel", _cancel_task)
    app.router.add_get("/api/tasks/{task_id}/events", _list_task_events)
