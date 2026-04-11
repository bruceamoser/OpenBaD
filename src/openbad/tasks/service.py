"""High-level task service layer.

:class:`TaskService` wraps :class:`~openbad.tasks.store.TaskStore`,
:class:`~openbad.tasks.lease.LeaseStore`, and the model transition helpers
into a single coherent API that is independent from the WUI, scheduler, and
any MQTT transport.
"""

from __future__ import annotations

import sqlite3
import uuid

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


class TaskService:
    """Unified task service that coordinates the store and lease layers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._store = TaskStore(conn)
        self._leases = LeaseStore(conn)

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
