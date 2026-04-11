"""Dependency gating, retry logic, and blocked-state enforcement for task DAG nodes.

:class:`DependencyGate` answers the question "is this node ready to run?" by
checking that all predecessor nodes in the DAG have reached ``NodeStatus.DONE``.

:class:`RetryPolicy` encapsulates the retry / blocked transition logic:
* ``record_attempt`` increments ``retry_count`` and returns whether a retry
  is still permitted.
* When retries are exhausted the node is transitioned to ``NodeStatus.BLOCKED``.

Both classes operate exclusively on the SQL state via :class:`~openbad.tasks.store.TaskStore`
and share an open ``sqlite3.Connection``.
"""

from __future__ import annotations

import sqlite3

from openbad.tasks.models import NodeStatus
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Dependency gate
# ---------------------------------------------------------------------------


class DependencyGate:
    """Determines whether a node's upstream dependencies are satisfied.

    A node is *ready* when every predecessor node (i.e. every node that has
    a directed edge **to** the candidate node) has status ``DONE``.  A node
    with no predecessors is always ready.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = TaskStore(conn)

    def is_ready(self, task_id: str, node_id: str) -> bool:
        """Return ``True`` if *node_id* may begin execution.

        Checks that every predecessor node in ``task_edges`` has
        ``status = 'done'``.
        """
        rows = self._conn.execute(
            "SELECT tn.status FROM task_edges te"
            " JOIN task_nodes tn ON tn.node_id = te.from_node_id"
            " WHERE te.task_id = ? AND te.to_node_id = ?",
            (task_id, node_id),
        ).fetchall()

        if not rows:
            # No predecessors — node is free to run
            return True

        return all(row["status"] == NodeStatus.DONE for row in rows)

    def unmet_dependencies(self, task_id: str, node_id: str) -> list[str]:
        """Return the node_ids of predecessors that are *not* yet ``DONE``."""
        rows = self._conn.execute(
            "SELECT te.from_node_id, tn.status FROM task_edges te"
            " JOIN task_nodes tn ON tn.node_id = te.from_node_id"
            " WHERE te.task_id = ? AND te.to_node_id = ?",
            (task_id, node_id),
        ).fetchall()
        return [row["from_node_id"] for row in rows if row["status"] != NodeStatus.DONE]


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


class RetryPolicy:
    """Manages retry counters and the transition to ``BLOCKED`` on exhaustion.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = TaskStore(conn)

    def record_attempt(self, node_id: str) -> bool:
        """Increment ``retry_count`` for *node_id* and evaluate retry eligibility.

        If ``retry_count`` still remains ≤ ``max_retries`` after incrementing,
        the node is left in its current status and ``True`` is returned (retry
        is permitted).

        If ``retry_count`` **exceeds** ``max_retries`` after incrementing, the
        node is transitioned to ``NodeStatus.BLOCKED`` and ``False`` is returned.

        Returns
        -------
        bool
            ``True``  → a retry is allowed.
            ``False`` → retry limit exceeded; node is now ``BLOCKED``.

        Raises
        ------
        ValueError
            If no node with *node_id* exists.
        """
        node = self._store.get_node(node_id)
        if node is None:
            raise ValueError(f"No node found: {node_id!r}")

        new_count = node.retry_count + 1
        self._conn.execute(
            "UPDATE task_nodes SET retry_count = ? WHERE node_id = ?",
            (new_count, node_id),
        )
        self._conn.commit()

        if new_count > node.max_retries:
            self._store.update_node_status(node_id, NodeStatus.BLOCKED)
            return False

        return True

    def retry_count(self, node_id: str) -> int:
        """Return the current ``retry_count`` for *node_id*."""
        node = self._store.get_node(node_id)
        if node is None:
            raise ValueError(f"No node found: {node_id!r}")
        return node.retry_count
