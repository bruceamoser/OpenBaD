"""Node executor run lifecycle persistence for Phase 9 task orchestration.

:class:`NodeExecutor` records the start and finish of each node execution
attempt as a ``task_runs`` row, transitions the owning node (and task) through
the correct lifecycle state, and persists structured failure summaries when a
run ends in error.

Design
------
* ``start_run()`` – inserts a ``task_runs`` row with ``status='running'`` and
  transitions the node to ``NodeStatus.RUNNING``.
* ``finish_run()`` – marks the run ``done`` or ``failed``, sets ``finished_at``,
  transitions the node accordingly, and (on failure) appends a
  ``node_failed`` event with a structured summary payload.

Failure summaries are written to ``task_events`` so they are queryable
alongside all other task lifecycle events.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time
import uuid

from openbad.tasks.models import NodeStatus, RunStatus, TaskStatus
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunRecord:
    """In-memory representation of a ``task_runs`` row."""

    run_id: str
    task_id: str
    node_id: str | None
    status: RunStatus
    actor: str
    routing_provider: str | None
    routing_model: str | None
    started_at: float
    finished_at: float | None = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        d = dict(data)
        d["status"] = RunStatus(d["status"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class NodeExecutor:
    """Manages ``task_runs`` lifecycle and propagates state to nodes/tasks.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection`` to the state database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = TaskStore(conn)

    # ------------------------------------------------------------------

    def start_run(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        actor: str = "system",
        routing_provider: str | None = None,
        routing_model: str | None = None,
    ) -> RunRecord:
        """Begin a new execution run for *task_id* / *node_id*.

        Inserts a ``task_runs`` row and transitions the node (if given) to
        ``RUNNING`` and the task to ``RUNNING``.

        Returns
        -------
        RunRecord
            The newly created run record.
        """
        run_id = str(uuid.uuid4())
        now = time.time()

        self._conn.execute(
            "INSERT INTO task_runs (run_id, task_id, node_id, status, actor,"
            " routing_provider, routing_model, started_at)"
            " VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
            (run_id, task_id, node_id, actor, routing_provider, routing_model, now),
        )
        self._conn.commit()

        # Transition task → RUNNING if it's currently PENDING
        task = self._store.get_task(task_id)
        if task is not None and task.status == TaskStatus.PENDING:
            self._store.update_task_status(task_id, TaskStatus.RUNNING)

        # Transition node → RUNNING
        if node_id is not None:
            node = self._store.get_node(node_id)
            if node is not None and node.status == NodeStatus.PENDING:
                self._store.update_node_status(node_id, NodeStatus.RUNNING)

        return RunRecord(
            run_id=run_id,
            task_id=task_id,
            node_id=node_id,
            status=RunStatus.RUNNING,
            actor=actor,
            routing_provider=routing_provider,
            routing_model=routing_model,
            started_at=now,
        )

    def finish_run(
        self,
        run_id: str,
        *,
        success: bool = True,
        failure_summary: str | None = None,
        failure_details: dict | None = None,
    ) -> RunRecord:
        """Complete an execution run.

        Parameters
        ----------
        run_id:
            The ID returned by :meth:`start_run`.
        success:
            ``True`` → run is marked ``done``; ``False`` → ``failed``.
        failure_summary:
            Short human-readable reason for the failure.  Only meaningful when
            ``success=False``.
        failure_details:
            Optional machine-readable failure data. Stored in the
            ``node_failed`` event payload.

        Returns
        -------
        RunRecord
            The updated run record.

        Raises
        ------
        ValueError
            If no run with *run_id* exists.
        """
        row = self._conn.execute(
            "SELECT run_id, task_id, node_id, status, actor, routing_provider,"
            " routing_model, started_at, finished_at FROM task_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No run found: {run_id!r}")

        new_status = RunStatus.DONE if success else RunStatus.FAILED
        now = time.time()

        self._conn.execute(
            "UPDATE task_runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (new_status.value, now, run_id),
        )
        self._conn.commit()

        task_id: str = row["task_id"]
        node_id: str | None = row["node_id"]

        # Transition node
        if node_id is not None:
            next_node_status = NodeStatus.DONE if success else NodeStatus.FAILED
            node = self._store.get_node(node_id)
            if node is not None and node.status == NodeStatus.RUNNING:
                self._store.update_node_status(node_id, next_node_status)

        # Persist failure summary as a task event
        if not success:

            payload = {"run_id": run_id, "node_id": node_id}
            if failure_summary:
                payload["summary"] = failure_summary
            if failure_details:
                payload.update(failure_details)
            self._store.append_event(
                task_id,
                "node_failed",
                node_id=node_id,
                payload=payload,
            )

        return RunRecord(
            run_id=run_id,
            task_id=task_id,
            node_id=node_id,
            status=new_status,
            actor=row["actor"],
            routing_provider=row["routing_provider"],
            routing_model=row["routing_model"],
            started_at=row["started_at"],
            finished_at=now,
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return a :class:`RunRecord` by ID, or ``None`` if not found."""
        row = self._conn.execute(
            "SELECT run_id, task_id, node_id, status, actor, routing_provider,"
            " routing_model, started_at, finished_at FROM task_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            status=RunStatus(row["status"]),
            actor=row["actor"],
            routing_provider=row["routing_provider"],
            routing_model=row["routing_model"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def list_runs(self, task_id: str) -> list[RunRecord]:
        """Return all runs for *task_id* ordered by ``started_at``."""
        rows = self._conn.execute(
            "SELECT run_id, task_id, node_id, status, actor, routing_provider,"
            " routing_model, started_at, finished_at"
            " FROM task_runs WHERE task_id = ? ORDER BY started_at ASC",
            (task_id,),
        ).fetchall()
        return [
            RunRecord(
                run_id=r["run_id"],
                task_id=r["task_id"],
                node_id=r["node_id"],
                status=RunStatus(r["status"]),
                actor=r["actor"],
                routing_provider=r["routing_provider"],
                routing_model=r["routing_model"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
            )
            for r in rows
        ]
