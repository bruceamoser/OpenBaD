"""Research escalation from blocked task nodes for Phase 9.

When a task node transitions to BLOCKED (dependency exhaustion), this module
can trigger a research escalation — a new :class:`ResearchNode` enqueued at
elevated priority.  Source linkage (blocked node → research node) is persisted
in the ``research_escalations`` table for queryability.

Duplicate suppression: only one escalation per blocked episode is allowed.
An episode is defined as a (task_id, node_id) pair.  Subsequent calls for the
same pair return ``None`` without enqueuing again.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from datetime import UTC, datetime

from openbad.tasks.research_queue import ResearchNode, ResearchQueue

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS research_escalations (
    escalation_id TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    research_node_id TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (task_id, node_id)
)
"""

_INSERT = """
INSERT INTO research_escalations
    (escalation_id, task_id, node_id, research_node_id, created_at)
VALUES
    (:escalation_id, :task_id, :node_id, :research_node_id, :created_at)
"""

_SELECT_BY_TASK_NODE = """
SELECT * FROM research_escalations
WHERE task_id = ? AND node_id = ?
"""

_SELECT_BY_TASK = """
SELECT * FROM research_escalations
WHERE task_id = ?
ORDER BY created_at
"""


def initialize_escalation_db(conn: sqlite3.Connection) -> None:
    """Create the ``research_escalations`` table if it does not exist."""
    conn.execute(_CREATE_TABLE)
    conn.commit()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class EscalationRecord:
    """A linkage between a blocked node and its research escalation."""

    escalation_id: str
    task_id: str
    node_id: str
    research_node_id: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "escalation_id": self.escalation_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "research_node_id": self.research_node_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> EscalationRecord:
        return cls(
            escalation_id=row["escalation_id"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            research_node_id=row["research_node_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Escalation trigger
# ---------------------------------------------------------------------------


class ResearchEscalation:
    """Triggers research escalations for blocked task nodes.

    Implements duplicate suppression: only the first escalation per
    (task_id, node_id) pair is persisted and enqueued.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` with ``research_escalations`` table.
    queue:
        The :class:`~openbad.tasks.research_queue.ResearchQueue` to enqueue
        research nodes into.
    escalation_priority:
        Priority given to escalation-triggered research nodes.  Defaults to
        ``-10`` (higher urgency than normal).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        queue: ResearchQueue,
        *,
        escalation_priority: int = -10,
    ) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._queue = queue
        self._priority = escalation_priority

    def trigger(
        self,
        task_id: str,
        node_id: str,
        *,
        research_title: str | None = None,
        research_description: str = "",
    ) -> EscalationRecord | None:
        """Escalate a blocked node to research, unless already escalated.

        Parameters
        ----------
        task_id:
            The task that contains the blocked node.
        node_id:
            The blocked node requiring research.
        research_title:
            Title for the research node.  Defaults to
            ``"Research for blocked node <node_id>"``.
        research_description:
            Optional description forwarded to the research node.

        Returns
        -------
        EscalationRecord
            The newly created escalation, or ``None`` if already escalated.
        """
        # Duplicate check
        existing = self._conn.execute(
            _SELECT_BY_TASK_NODE, (task_id, node_id)
        ).fetchone()
        if existing is not None:
            return None

        title = research_title or f"Research for blocked node {node_id}"
        research_node = self._queue.enqueue(
            title,
            priority=self._priority,
            description=research_description,
            source_task_id=task_id,
        )

        escalation_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        self._conn.execute(
            _INSERT,
            {
                "escalation_id": escalation_id,
                "task_id": task_id,
                "node_id": node_id,
                "research_node_id": research_node.node_id,
                "created_at": now.isoformat(),
            },
        )
        self._conn.commit()

        return EscalationRecord(
            escalation_id=escalation_id,
            task_id=task_id,
            node_id=node_id,
            research_node_id=research_node.node_id,
            created_at=now,
        )

    def get_escalation(self, task_id: str, node_id: str) -> EscalationRecord | None:
        """Return the escalation record for a (task_id, node_id) pair."""
        row = self._conn.execute(
            _SELECT_BY_TASK_NODE, (task_id, node_id)
        ).fetchone()
        return EscalationRecord._from_row(row) if row else None

    def list_escalations(self, task_id: str) -> list[EscalationRecord]:
        """Return all escalation records for a task."""
        rows = self._conn.execute(_SELECT_BY_TASK, (task_id,)).fetchall()
        return [EscalationRecord._from_row(r) for r in rows]

    def research_node_for(self, task_id: str, node_id: str) -> ResearchNode | None:
        """Return the research node linked to a blocked (task_id, node_id) pair."""
        record = self.get_escalation(task_id, node_id)
        if record is None:
            return None
        return self._queue.get(record.research_node_id)
