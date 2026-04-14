"""Research node model and SQLite-backed priority queue for Phase 9.

:class:`ResearchNode` models a unit of research work with a numeric
:attr:`priority` (lower = higher urgency; think heap ordering).
:class:`ResearchQueue` persists nodes and returns them in deterministic
priority order via :meth:`ResearchQueue.dequeue`.

Priority is a signed integer with the following convention:
- Negative values → higher urgency (popped first).
- 0 → normal priority.
- Positive values → background / low urgency.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from datetime import UTC, datetime
from enum import auto

from openbad.tasks.models import StrEnum

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS research_queue (
    node_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 0,
    source_task_id TEXT,
    enqueued_at TEXT NOT NULL,
    dequeued_at TEXT
)
"""

_INSERT = """
INSERT INTO research_queue
    (node_id, title, description, priority, source_task_id, enqueued_at)
VALUES
    (:node_id, :title, :description, :priority, :source_task_id, :enqueued_at)
"""

# Order: priority ASC (lower = urgent), then enqueued_at ASC (FIFO within same priority)
_SELECT_NEXT = """
SELECT * FROM research_queue
WHERE dequeued_at IS NULL
ORDER BY priority ASC, enqueued_at ASC
LIMIT 1
"""

_SELECT_ALL_PENDING = """
SELECT * FROM research_queue
WHERE dequeued_at IS NULL
ORDER BY priority ASC, enqueued_at ASC
"""

_SELECT_COMPLETED = """
SELECT * FROM research_queue
WHERE dequeued_at IS NOT NULL
ORDER BY dequeued_at DESC
LIMIT ?
"""

_SELECT_PENDING_BY_TITLE = """
SELECT * FROM research_queue
WHERE dequeued_at IS NULL AND title = ?
ORDER BY priority ASC, enqueued_at ASC
LIMIT 1
"""

_MARK_DEQUEUED = "UPDATE research_queue SET dequeued_at = ? WHERE node_id = ?"
_SELECT_BY_ID = "SELECT * FROM research_queue WHERE node_id = ?"
_UPDATE_PENDING_NODE = """
UPDATE research_queue
SET title = ?,
    description = ?,
    priority = ?,
    source_task_id = ?
WHERE node_id = ?
"""


def initialize_research_db(conn: sqlite3.Connection) -> None:
    """Create the ``research_queue`` table if it does not exist."""
    conn.execute(_CREATE_TABLE)
    conn.commit()


# ---------------------------------------------------------------------------
# Node model
# ---------------------------------------------------------------------------


class ResearchNodeStatus(StrEnum):
    PENDING = auto()
    DEQUEUED = auto()


@dataclasses.dataclass
class ResearchNode:
    """A unit of research work held in the priority queue."""

    node_id: str
    title: str
    priority: int
    enqueued_at: datetime
    description: str = ""
    source_task_id: str | None = None
    dequeued_at: datetime | None = None

    @property
    def status(self) -> ResearchNodeStatus:
        if self.dequeued_at is None:
            return ResearchNodeStatus.PENDING
        return ResearchNodeStatus.DEQUEUED

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "source_task_id": self.source_task_id,
            "enqueued_at": self.enqueued_at.isoformat(),
            "dequeued_at": self.dequeued_at.isoformat() if self.dequeued_at else None,
            "status": self.status.value,
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ResearchNode:
        return cls(
            node_id=row["node_id"],
            title=row["title"],
            description=row["description"] or "",
            priority=row["priority"],
            source_task_id=row["source_task_id"],
            enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
            dequeued_at=(
                datetime.fromisoformat(row["dequeued_at"]) if row["dequeued_at"] else None
            ),
        )


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class ResearchQueue:
    """SQLite-backed priority queue for research nodes.

    Priority ordering: lower priority value = higher urgency (dequeued first).
    Within the same priority, nodes are dequeued in FIFO (insertion) order.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` with the ``research_queue`` table.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn

    def enqueue(
        self,
        title: str,
        *,
        priority: int = 0,
        description: str = "",
        source_task_id: str | None = None,
        node_id: str | None = None,
    ) -> ResearchNode:
        """Add a research node to the queue.

        Parameters
        ----------
        title:
            A human-readable summary of the research goal.
        priority:
            Numeric priority.  Lower values are dequeued first.
        description:
            Optional extended description of the research task.
        source_task_id:
            Optional ID of the task that triggered this research item.
        node_id:
            Optional explicit node ID; a UUID4 is generated if omitted.
        """
        nid = node_id or str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        self._conn.execute(
            _INSERT,
            {
                "node_id": nid,
                "title": title,
                "description": description,
                "priority": priority,
                "source_task_id": source_task_id,
                "enqueued_at": now.isoformat(),
            },
        )
        self._conn.commit()
        return ResearchNode(
            node_id=nid,
            title=title,
            description=description,
            priority=priority,
            source_task_id=source_task_id,
            enqueued_at=now,
        )

    def enqueue_or_append_pending(
        self,
        title: str,
        *,
        priority: int = 0,
        description: str = "",
        source_task_id: str | None = None,
        observation: str | None = None,
        max_observations: int = 20,
    ) -> ResearchNode:
        """Reuse a pending node with the same title, appending observation details.

        This prevents repeated heartbeat anomalies from creating duplicate research
        rows while preserving fresh context in one place.
        """
        existing = self._conn.execute(_SELECT_PENDING_BY_TITLE, (title,)).fetchone()
        if existing is None:
            return self.enqueue(
                title,
                priority=priority,
                description=self._with_observation(description, observation, max_observations),
                source_task_id=source_task_id,
            )

        node = ResearchNode._from_row(existing)
        merged_description = self._with_observation(
            node.description or description,
            observation,
            max_observations,
        )
        merged_priority = min(node.priority, priority)
        self._conn.execute(
            _UPDATE_PENDING_NODE,
            (node.title, merged_description, merged_priority, source_task_id, node.node_id),
        )
        self._conn.commit()
        refreshed = self.get(node.node_id)
        if refreshed is None:
            return node
        return refreshed

    @staticmethod
    def _with_observation(
        description: str,
        observation: str | None,
        max_observations: int,
    ) -> str:
        """Append an observation line to the description history.

        History format:
        Observation history:
        - <timestamp> | <observation>
        """
        base = (description or "").strip()
        if not observation:
            return base

        marker = "Observation history:\n"
        now = datetime.now(tz=UTC).isoformat()
        entry = f"- {now} | {observation.strip()}"

        if marker not in base:
            prefix = base
            history_lines: list[str] = []
        else:
            prefix, history_blob = base.split(marker, 1)
            history_lines = [line for line in history_blob.splitlines() if line.startswith("- ")]

        if history_lines:
            last_payload = history_lines[-1].split("|", 1)[-1].strip()
            if last_payload == observation.strip():
                return (prefix.rstrip() + "\n\n" + marker + "\n".join(history_lines)).strip()

        history_lines.append(entry)
        history_lines = history_lines[-max(1, max_observations) :]
        prefix = prefix.strip()
        if prefix:
            return f"{prefix}\n\n{marker}{'\n'.join(history_lines)}"
        return f"{marker}{'\n'.join(history_lines)}"

    def dequeue(self) -> ResearchNode | None:
        """Remove and return the highest-priority pending node.

        Returns ``None`` when the queue is empty.
        """
        row = self._conn.execute(_SELECT_NEXT).fetchone()
        if row is None:
            return None
        node = ResearchNode._from_row(row)
        now = datetime.now(tz=UTC)
        self._conn.execute(_MARK_DEQUEUED, (now.isoformat(), node.node_id))
        self._conn.commit()
        node.dequeued_at = now
        return node

    def peek(self) -> ResearchNode | None:
        """Return the next pending node without removing it."""
        row = self._conn.execute(_SELECT_NEXT).fetchone()
        return ResearchNode._from_row(row) if row else None

    def list_pending(self) -> list[ResearchNode]:
        """Return all pending nodes in priority order."""
        rows = self._conn.execute(_SELECT_ALL_PENDING).fetchall()
        return [ResearchNode._from_row(r) for r in rows]

    def get(self, node_id: str) -> ResearchNode | None:
        """Return a node by ID regardless of status."""
        row = self._conn.execute(_SELECT_BY_ID, (node_id,)).fetchone()
        return ResearchNode._from_row(row) if row else None

    def list_completed(self, limit: int = 50) -> list[ResearchNode]:
        """Return recently completed nodes, newest first."""
        rows = self._conn.execute(_SELECT_COMPLETED, (limit,)).fetchall()
        return [ResearchNode._from_row(r) for r in rows]

    def update(
        self,
        node_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        source_task_id: str | None = None,
    ) -> ResearchNode:
        """Update a pending research node and return the refreshed record."""
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"Research node {node_id!r} not found")
        if node.dequeued_at is not None:
            raise ValueError(f"Research node {node_id!r} is already completed")

        next_title = node.title if title is None else title
        next_description = node.description if description is None else description
        next_priority = node.priority if priority is None else int(priority)
        next_source_task_id = node.source_task_id if source_task_id is None else source_task_id

        self._conn.execute(
            _UPDATE_PENDING_NODE,
            (next_title, next_description, next_priority, next_source_task_id, node_id),
        )
        self._conn.commit()
        refreshed = self.get(node_id)
        assert refreshed is not None
        return refreshed

    def complete(self, node_id: str) -> ResearchNode:
        """Mark a research node complete and return the updated record."""
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"Research node {node_id!r} not found")
        if node.dequeued_at is None:
            now = datetime.now(tz=UTC)
            self._conn.execute(_MARK_DEQUEUED, (now.isoformat(), node_id))
            self._conn.commit()
        refreshed = self.get(node_id)
        assert refreshed is not None
        return refreshed
