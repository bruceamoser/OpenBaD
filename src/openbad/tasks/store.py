"""SQLite CRUD operations for tasks, nodes, and events."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from openbad.tasks.models import NodeModel, NodeStatus, TaskModel, TaskStatus


class TaskStore:
    """Provides CRUD operations backed by a SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    def create_task(self, task: TaskModel) -> TaskModel:
        """Insert *task* and return it unchanged."""
        self._conn.execute(
            """
            INSERT INTO tasks (
                task_id, title, description, kind, horizon, priority, status,
                due_at, parent_task_id, root_task_id, owner, lease_owner,
                recurrence_rule, requires_context, isolated_execution,
                notes_path, created_at, updated_at
            ) VALUES (
                :task_id, :title, :description, :kind, :horizon, :priority,
                :status, :due_at, :parent_task_id, :root_task_id, :owner,
                :lease_owner, :recurrence_rule, :requires_context,
                :isolated_execution, :notes_path, :created_at, :updated_at
            )
            """,
            _task_to_row(task),
        )
        self._conn.commit()
        return task

    def get_task(self, task_id: str) -> TaskModel | None:
        """Return the task with *task_id*, or ``None`` if not found."""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return _task_from_row(row) if row else None

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update the status of an existing task."""
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (status.value, time.time(), task_id),
        )
        self._conn.commit()

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskModel]:
        """Return tasks, optionally filtered by *status*, with pagination."""
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC"
                " LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_task_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def create_node(self, node: NodeModel) -> NodeModel:
        """Insert *node* and return it unchanged."""
        self._conn.execute(
            """
            INSERT INTO task_nodes (
                node_id, task_id, title, node_type, status,
                capability_requirements, model_requirements,
                reward_program_id, expected_info_gain, blockage_score,
                retry_count, max_retries, created_at, updated_at
            ) VALUES (
                :node_id, :task_id, :title, :node_type, :status,
                :capability_requirements, :model_requirements,
                :reward_program_id, :expected_info_gain, :blockage_score,
                :retry_count, :max_retries, :created_at, :updated_at
            )
            """,
            _node_to_row(node),
        )
        self._conn.commit()
        return node

    def get_node(self, node_id: str) -> NodeModel | None:
        """Return the node with *node_id*, or ``None`` if not found."""
        row = self._conn.execute(
            "SELECT * FROM task_nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return _node_from_row(row) if row else None

    def update_node_status(self, node_id: str, status: NodeStatus) -> None:
        """Update the status of an existing node."""
        self._conn.execute(
            "UPDATE task_nodes SET status = ?, updated_at = ? WHERE node_id = ?",
            (status.value, time.time(), node_id),
        )
        self._conn.commit()

    def list_nodes(self, task_id: str) -> list[NodeModel]:
        """Return all nodes for *task_id* ordered by creation time."""
        rows = self._conn.execute(
            "SELECT * FROM task_nodes WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [_node_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Event operations (append-only)
    # ------------------------------------------------------------------

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> str:
        """Append an event record and return its generated *event_id*."""
        event_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO task_events (event_id, task_id, node_id, event_type,
                                     created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                node_id,
                event_type,
                time.time(),
                json.dumps(payload or {}),
            ),
        )
        self._conn.commit()
        return event_id

    def list_events(self, task_id: str) -> list[dict]:
        """Return all events for *task_id* in insertion order."""
        rows = self._conn.execute(
            "SELECT event_id, task_id, node_id, event_type, created_at,"
            " payload_json FROM task_events WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "event_id": row["event_id"],
                    "task_id": row["task_id"],
                    "node_id": row["node_id"],
                    "event_type": row["event_type"],
                    "created_at": row["created_at"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def create_edge(self, task_id: str, from_node_id: str, to_node_id: str) -> None:
        """Insert a directed edge (from_node_id → to_node_id) for *task_id*.

        Silently does nothing if the edge already exists.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO task_edges (task_id, from_node_id, to_node_id)"
            " VALUES (?, ?, ?)",
            (task_id, from_node_id, to_node_id),
        )
        self._conn.commit()

    def list_edges(self, task_id: str) -> list[tuple[str, str]]:
        """Return all edges for *task_id* as (from_node_id, to_node_id) pairs."""
        rows = self._conn.execute(
            "SELECT from_node_id, to_node_id FROM task_edges WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [(row["from_node_id"], row["to_node_id"]) for row in rows]


# ---------------------------------------------------------------------------
# Row ↔ model helpers
# ---------------------------------------------------------------------------


def _task_to_row(task: TaskModel) -> dict:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "kind": task.kind.value,
        "horizon": task.horizon,
        "priority": task.priority,
        "status": task.status.value,
        "due_at": task.due_at,
        "parent_task_id": task.parent_task_id,
        "root_task_id": task.root_task_id,
        "owner": task.owner,
        "lease_owner": task.lease_owner,
        "recurrence_rule": task.recurrence_rule,
        "requires_context": int(task.requires_context),
        "isolated_execution": int(task.isolated_execution),
        "notes_path": task.notes_path,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _task_from_row(row: sqlite3.Row) -> TaskModel:
    from openbad.tasks.models import TaskKind

    return TaskModel(
        task_id=row["task_id"],
        title=row["title"],
        description=row["description"],
        kind=TaskKind(row["kind"]),
        horizon=row["horizon"],
        priority=row["priority"],
        status=TaskStatus(row["status"]),
        due_at=row["due_at"],
        parent_task_id=row["parent_task_id"],
        root_task_id=row["root_task_id"],
        owner=row["owner"],
        lease_owner=row["lease_owner"],
        recurrence_rule=row["recurrence_rule"],
        requires_context=bool(row["requires_context"]),
        isolated_execution=bool(row["isolated_execution"]),
        notes_path=row["notes_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _node_to_row(node: NodeModel) -> dict:
    return {
        "node_id": node.node_id,
        "task_id": node.task_id,
        "title": node.title,
        "node_type": node.node_type,
        "status": node.status.value,
        "capability_requirements": json.dumps(node.capability_requirements),
        "model_requirements": json.dumps(node.model_requirements),
        "reward_program_id": node.reward_program_id,
        "expected_info_gain": node.expected_info_gain,
        "blockage_score": node.blockage_score,
        "retry_count": node.retry_count,
        "max_retries": node.max_retries,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }


def _node_from_row(row: sqlite3.Row) -> NodeModel:
    return NodeModel(
        node_id=row["node_id"],
        task_id=row["task_id"],
        title=row["title"],
        node_type=row["node_type"],
        status=NodeStatus(row["status"]),
        capability_requirements=json.loads(row["capability_requirements"]),
        model_requirements=json.loads(row["model_requirements"]),
        reward_program_id=row["reward_program_id"],
        expected_info_gain=row["expected_info_gain"],
        blockage_score=row["blockage_score"],
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
