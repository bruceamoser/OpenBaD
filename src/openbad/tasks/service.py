"""High-level task service layer.

:class:`TaskService` wraps :class:`~openbad.tasks.store.TaskStore`,
:class:`~openbad.tasks.lease.LeaseStore`, and the model transition helpers
into a single coherent API that is independent from the WUI, scheduler, and
any MQTT transport.

Use :meth:`TaskService.get_instance` to obtain the process-wide singleton.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from pathlib import Path

from openbad.tasks.lease import Lease, LeaseStore
from openbad.tasks.models import (
    NodeModel,
    NodeStatus,
    TaskModel,
    TaskStatus,
    assert_valid_node_transition,
    assert_valid_task_transition,
)
from openbad.tasks.store import TaskStore

log = logging.getLogger(__name__)

_lock = threading.Lock()
_instance: TaskService | None = None


class TaskService:
    """Unified task service that coordinates the store and lease layers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = TaskStore(conn)
        self._leases = LeaseStore(conn)

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, db_path: str | Path | None = None) -> TaskService:
        """Return the process-wide singleton, creating it on first call.

        Parameters
        ----------
        db_path:
            Optional path override; used only when the singleton is first
            created.  Subsequent calls ignore it.
        """
        global _instance  # noqa: PLW0603
        if _instance is not None:
            return _instance
        with _lock:
            if _instance is not None:
                return _instance
            from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db

            effective = Path(db_path) if db_path else DEFAULT_STATE_DB_PATH
            conn = initialize_state_db(effective)
            _instance = cls(conn)
            log.info("TaskService singleton initialised (db=%s)", effective)
            return _instance

    @classmethod
    def reset_instance(cls) -> None:
        """Tear down the singleton — for tests only."""
        global _instance  # noqa: PLW0603
        with _lock:
            _instance = None

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        *,
        description: str = "",
        owner: str = "system",
        parent_task_id: str | None = None,
    ) -> TaskModel:
        """Create a new task in PENDING status and return it."""
        task = TaskModel.new(
            title,
            description=description,
            owner=owner,
            parent_task_id=parent_task_id,
        )
        return self._store.create_task(task)

    def get_task(self, task_id: str) -> TaskModel | None:
        """Return the task or ``None``."""
        return self._store.get_task(task_id)

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskModel]:
        """Return tasks with optional status filter and pagination."""
        return self._store.list_tasks(status=status, limit=limit, offset=offset)

    def transition_task(self, task_id: str, next_status: TaskStatus) -> TaskModel:
        """Apply a validated status transition to a task.

        Raises :class:`ValueError` if the transition is illegal.
        Raises :class:`KeyError` if *task_id* is not found.
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task {task_id!r} not found")
        assert_valid_task_transition(task.status, next_status)
        self._store.update_task_status(task_id, next_status)
        self._store.append_event(
            task_id,
            "task_status_changed",
            payload={"from": task.status.value, "to": next_status.value},
        )
        updated = self._store.get_task(task_id)
        assert updated is not None
        return updated

    def cancel_task(self, task_id: str) -> TaskModel:
        """Convenience wrapper to cancel a task."""
        return self.transition_task(task_id, TaskStatus.CANCELLED)

    def complete_task(self, task_id: str) -> TaskModel:
        """Convenience wrapper to mark a task done."""
        return self.transition_task(task_id, TaskStatus.DONE)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        owner: str | None = None,
    ) -> TaskModel:
        """Update mutable task metadata and return the refreshed task."""
        task = self._store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task {task_id!r} not found")
        self._store.update_task_fields(
            task_id,
            title=title,
            description=description,
            owner=owner,
        )
        updated = self._store.get_task(task_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------
    # Node lifecycle
    # ------------------------------------------------------------------

    def create_node(
        self,
        task_id: str,
        title: str,
        *,
        node_type: str = "reason",
        max_retries: int = 0,
    ) -> NodeModel:
        """Create a node attached to *task_id* and return it."""
        node = NodeModel.new(
            task_id, title, node_type=node_type, max_retries=max_retries
        )
        return self._store.create_node(node)

    def get_node(self, node_id: str) -> NodeModel | None:
        """Return the node or ``None``."""
        return self._store.get_node(node_id)

    def list_nodes(self, task_id: str) -> list[NodeModel]:
        """Return all nodes for a task in creation order."""
        return self._store.list_nodes(task_id)

    def transition_node(self, node_id: str, next_status: NodeStatus) -> NodeModel:
        """Apply a validated status transition to a node.

        Raises :class:`ValueError` if the transition is illegal.
        Raises :class:`KeyError` if *node_id* is not found.
        """
        node = self._store.get_node(node_id)
        if node is None:
            raise KeyError(f"Node {node_id!r} not found")
        assert_valid_node_transition(node.status, next_status)
        self._store.update_node_status(node_id, next_status)
        self._store.append_event(
            node.task_id,
            "node_status_changed",
            node_id=node_id,
            payload={"from": node.status.value, "to": next_status.value},
        )
        updated = self._store.get_node(node_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> str:
        """Append an event and return its *event_id*."""
        return self._store.append_event(
            task_id, event_type, node_id=node_id, payload=payload
        )

    def list_events(self, task_id: str) -> list[dict]:
        """Return all events for *task_id* in creation order."""
        return self._store.list_events(task_id)

    # ------------------------------------------------------------------
    # Lease helpers
    # ------------------------------------------------------------------

    def acquire_task_lease(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: float = 300,
    ) -> Lease | None:
        """Atomically acquire a lease on *task_id* for *owner_id*.

        Returns the :class:`Lease` on success, ``None`` if already held.
        """
        return self._leases.acquire("task", task_id, owner_id, ttl_seconds)

    def release_task_lease(self, lease_id: str, owner_id: str) -> bool:
        """Release a task lease owned by *owner_id*."""
        return self._leases.release(lease_id, owner_id)

    def generate_run_id(self) -> str:
        """Generate a unique run ID for a task execution record."""
        return str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def task_to_dict(self, task: TaskModel) -> dict:
        """Serialise a task model for WUI / API use."""
        return task.to_dict()

    def node_to_dict(self, node: NodeModel) -> dict:
        """Serialise a node model for WUI / API use."""
        return node.to_dict()

    # ------------------------------------------------------------------
    # Query helpers (eliminate raw SQL in callers)
    # ------------------------------------------------------------------

    def list_active_tasks(self, *, limit: int = 200) -> list[dict]:
        """Return non-terminal tasks as dicts, newest first."""
        rows = self._conn.execute(
            """
            SELECT task_id, title, description, status, kind, horizon,
                   priority, owner, created_at, updated_at
            FROM tasks
            WHERE status NOT IN ('done', 'failed', 'cancelled')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_completed_tasks(self, *, limit: int = 50) -> list[dict]:
        """Return terminal tasks as dicts, most-recently updated first."""
        rows = self._conn.execute(
            """
            SELECT task_id, title, description, status, kind, horizon,
                   priority, owner, created_at, updated_at
            FROM tasks
            WHERE status IN ('done', 'failed', 'cancelled')
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def top_pending_user_task(self) -> TaskModel | None:
        """Return the highest-priority pending non-system task, or *None*."""
        row = self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
              AND kind NOT IN ('scheduled', 'system')
              AND (due_at IS NULL OR due_at <= strftime('%%s', 'now'))
              AND title NOT LIKE 'Heartbeat%%'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
        ).fetchone()
        if row is None:
            return None
        from openbad.tasks.store import _task_from_row

        return _task_from_row(row)

    def pending_system_task_exists(self, *, title_prefix: str) -> bool:
        """Return True if a pending system task with the given title prefix exists."""
        row = self._conn.execute(
            """
            SELECT 1 FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title LIKE ? || '%%'
            LIMIT 1
            """,
            (title_prefix,),
        ).fetchone()
        return row is not None

    def find_pending_system_task(self, *, title: str) -> TaskModel | None:
        """Return a pending system task with an exact title, or *None*."""
        row = self._conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (title,),
        ).fetchone()
        if row is None:
            return None
        return self._store.get_task(str(row["task_id"]))

    def list_due_endocrine_followups(self) -> list[TaskModel]:
        """Return all due endocrine re-enable followup tasks."""
        rows = self._conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title LIKE 'Endocrine follow-up: re-enable %%'
              AND (due_at IS NULL OR due_at <= strftime('%%s', 'now'))
            ORDER BY created_at ASC
            """,
        ).fetchall()
        result: list[TaskModel] = []
        for row in rows:
            task = self._store.get_task(str(row["task_id"]))
            if task is not None:
                result.append(task)
        return result
