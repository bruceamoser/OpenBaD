"""Core triage capability pack for Phase 9 task and research operations.

This module wires up the ``core_triage.*`` capabilities — registered in
:data:`CORE_TRIAGE_MANIFEST` — to their underlying implementations.
Capabilities execute via :func:`execute_capability` which dispatches to the
correct handler, emits a task event, and returns a typed
:class:`CapabilityResponse`.

Errors in handler logic are caught and returned as structured
:class:`CapabilityError` responses rather than raised, keeping the runtime
non-fatal.
"""

from __future__ import annotations

import dataclasses
import sqlite3

from openbad.plugins.manifest import CapabilityEntry, CapabilityManifest
from openbad.tasks.models import TaskKind, TaskModel
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

CORE_TRIAGE_MANIFEST = CapabilityManifest(
    name="core_triage",
    version="1.0.0",
    module="openbad.plugins.core_triage",
    description="Core triage capabilities for task and research operations",
    capabilities=[
        CapabilityEntry(
            id="core_triage.create_task",
            description="Create a new task from triage input",
            permissions=["db.insert", "log.write"],
        ),
        CapabilityEntry(
            id="core_triage.queue_research",
            description="Queue a research subtask",
            permissions=["db.insert", "log.write"],
        ),
        CapabilityEntry(
            id="core_triage.cancel_task",
            description="Cancel an existing pending or running task",
            permissions=["db.update", "log.write"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Request / Response types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CapabilityRequest:
    """Input to a capability execution."""

    capability_id: str
    params: dict
    task_id: str | None = None  # Owning task context (for event emission)


@dataclasses.dataclass(frozen=True)
class CapabilityResponse:
    """Successful capability execution result."""

    capability_id: str
    output: dict
    event_id: str | None = None


@dataclasses.dataclass(frozen=True)
class CapabilityError:
    """Structured error returned when a capability handler raises."""

    capability_id: str
    message: str
    detail: dict = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capability executor
# ---------------------------------------------------------------------------


class CoreTriageExecutor:
    """Executes core triage capabilities and emits task events.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = TaskStore(conn)

    def execute(
        self, request: CapabilityRequest
    ) -> CapabilityResponse | CapabilityError:
        """Dispatch *request* to the appropriate handler.

        Returns a :class:`CapabilityResponse` on success or a
        :class:`CapabilityError` on any handler exception.
        """
        handlers = {
            "core_triage.create_task": self._create_task,
            "core_triage.queue_research": self._queue_research,
            "core_triage.cancel_task": self._cancel_task,
        }
        handler = handlers.get(request.capability_id)
        if handler is None:
            return CapabilityError(
                capability_id=request.capability_id,
                message=f"Unknown capability: {request.capability_id!r}",
            )
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            return CapabilityError(
                capability_id=request.capability_id,
                message=str(exc),
                detail={"type": type(exc).__name__},
            )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _create_task(self, request: CapabilityRequest) -> CapabilityResponse:
        title = request.params.get("title")
        if not title:
            raise ValueError("'title' is required")

        task = TaskModel.new(
            title,
            description=request.params.get("description", ""),
            kind=TaskKind.USER_REQUESTED,
        )
        self._store.create_task(task)

        event_id: str | None = None
        if request.task_id:
            event_id = self._store.append_event(
                request.task_id,
                "capability_create_task",
                payload={"new_task_id": task.task_id, "title": title},
            )

        return CapabilityResponse(
            capability_id=request.capability_id,
            output={"task_id": task.task_id, "title": title},
            event_id=event_id,
        )

    def _queue_research(self, request: CapabilityRequest) -> CapabilityResponse:
        title = request.params.get("title")
        if not title:
            raise ValueError("'title' is required")

        task = TaskModel.new(
            title,
            description=request.params.get("description", ""),
            kind=TaskKind.RESEARCH,
        )
        self._store.create_task(task)

        event_id: str | None = None
        if request.task_id:
            event_id = self._store.append_event(
                request.task_id,
                "capability_queue_research",
                payload={"research_task_id": task.task_id, "title": title},
            )

        return CapabilityResponse(
            capability_id=request.capability_id,
            output={"task_id": task.task_id, "title": title, "kind": "research"},
            event_id=event_id,
        )

    def _cancel_task(self, request: CapabilityRequest) -> CapabilityResponse:
        from openbad.tasks.models import TaskStatus

        target_id = request.params.get("task_id")
        if not target_id:
            raise ValueError("'task_id' is required")

        task = self._store.get_task(target_id)
        if task is None:
            raise ValueError(f"Task not found: {target_id!r}")
        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            raise ValueError(
                f"Cannot cancel task in status {task.status.value!r}"
            )

        self._store.update_task_status(target_id, TaskStatus.CANCELLED)

        event_id: str | None = None
        if request.task_id:
            event_id = self._store.append_event(
                request.task_id,
                "capability_cancel_task",
                payload={"cancelled_task_id": target_id},
            )

        return CapabilityResponse(
            capability_id=request.capability_id,
            output={"cancelled_task_id": target_id},
            event_id=event_id,
        )
