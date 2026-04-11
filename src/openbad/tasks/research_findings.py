"""Research findings persistence and memory writeback for Phase 9.

:class:`FindingStore` persists validated research findings with source
linkage to the originating research node and task.

When a finding is validated, :meth:`FindingStore.commit_finding` writes it to
the ``research_findings`` table and performs a memory writeback via the
injected :class:`~openbad.memory.base.MemoryStore`.  After writeback, a
reevaluation signal entry is written to the ``task_reevaluation_signals`` table
so the owning task can be re-evaluated.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_FINDINGS = """
CREATE TABLE IF NOT EXISTS research_findings (
    finding_id        TEXT PRIMARY KEY,
    research_node_id  TEXT NOT NULL,
    source_task_id    TEXT,
    content           TEXT NOT NULL,
    validated         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
)
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS task_reevaluation_signals (
    signal_id      TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    finding_id     TEXT NOT NULL,
    created_at     TEXT NOT NULL
)
"""

_INSERT_FINDING = """
INSERT INTO research_findings
    (finding_id, research_node_id, source_task_id, content, validated, created_at)
VALUES
    (:finding_id, :research_node_id, :source_task_id, :content, :validated, :created_at)
"""

_INSERT_SIGNAL = """
INSERT INTO task_reevaluation_signals
    (signal_id, task_id, finding_id, created_at)
VALUES
    (:signal_id, :task_id, :finding_id, :created_at)
"""

_SELECT_FINDING_BY_ID = "SELECT * FROM research_findings WHERE finding_id = ?"
_SELECT_FINDINGS_BY_NODE = "SELECT * FROM research_findings WHERE research_node_id = ?"
_SELECT_SIGNALS_BY_TASK = (
    "SELECT * FROM task_reevaluation_signals WHERE task_id = ? ORDER BY created_at"
)


def initialize_findings_db(conn: sqlite3.Connection) -> None:
    """Create the ``research_findings`` and ``task_reevaluation_signals`` tables."""
    conn.execute(_CREATE_FINDINGS)
    conn.execute(_CREATE_SIGNALS)
    conn.commit()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ResearchFinding:
    """A single validated research finding."""

    finding_id: str
    research_node_id: str
    content: str
    created_at: datetime
    source_task_id: str | None = None
    validated: bool = False

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "research_node_id": self.research_node_id,
            "source_task_id": self.source_task_id,
            "content": self.content,
            "validated": self.validated,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ResearchFinding:
        return cls(
            finding_id=row["finding_id"],
            research_node_id=row["research_node_id"],
            source_task_id=row["source_task_id"],
            content=row["content"],
            validated=bool(row["validated"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


@dataclasses.dataclass(frozen=True)
class ReevaluationSignal:
    """Signal that a task should be re-evaluated in light of new findings."""

    signal_id: str
    task_id: str
    finding_id: str
    created_at: datetime

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ReevaluationSignal:
        return cls(
            signal_id=row["signal_id"],
            task_id=row["task_id"],
            finding_id=row["finding_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FindingStore:
    """Persists validated research findings and manages memory writeback.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` with existing findings/signals tables.
    memory_store:
        A :class:`~openbad.memory.base.MemoryStore` to receive writeback
        entries when a finding is committed.  The finding is written to the
        SEMANTIC tier with key ``research.<research_node_id>``.
    """

    def __init__(self, conn: sqlite3.Connection, memory_store: MemoryStore) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._memory = memory_store

    def persist_finding(
        self,
        research_node_id: str,
        content: str,
        *,
        source_task_id: str | None = None,
    ) -> ResearchFinding:
        """Persist a (non-validated) research finding.

        Use :meth:`commit_finding` to mark a finding as validated and trigger
        writeback + reevaluation.
        """
        finding_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        self._conn.execute(
            _INSERT_FINDING,
            {
                "finding_id": finding_id,
                "research_node_id": research_node_id,
                "source_task_id": source_task_id,
                "content": content,
                "validated": 0,
                "created_at": now.isoformat(),
            },
        )
        self._conn.commit()
        return ResearchFinding(
            finding_id=finding_id,
            research_node_id=research_node_id,
            source_task_id=source_task_id,
            content=content,
            validated=False,
            created_at=now,
        )

    def commit_finding(
        self,
        research_node_id: str,
        content: str,
        *,
        source_task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResearchFinding:
        """Persist a *validated* finding, write to memory, and emit reevaluation signal.

        Steps:
        1. Insert finding row with ``validated=True``.
        2. Write a :class:`~openbad.memory.base.MemoryEntry` to the semantic tier.
        3. If *source_task_id* is provided, emit a reevaluation signal.

        Returns
        -------
        ResearchFinding
            The newly committed finding.
        """
        finding_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        self._conn.execute(
            _INSERT_FINDING,
            {
                "finding_id": finding_id,
                "research_node_id": research_node_id,
                "source_task_id": source_task_id,
                "content": content,
                "validated": 1,
                "created_at": now.isoformat(),
            },
        )
        self._conn.commit()

        # Memory writeback
        entry = MemoryEntry(
            key=f"research.{research_node_id}",
            value={"content": content, "finding_id": finding_id},
            tier=MemoryTier.SEMANTIC,
            context="research_writeback",
            metadata=metadata or {},
        )
        self._memory.write(entry)

        # Reevaluation signal
        if source_task_id:
            signal_id = str(uuid.uuid4())
            self._conn.execute(
                _INSERT_SIGNAL,
                {
                    "signal_id": signal_id,
                    "task_id": source_task_id,
                    "finding_id": finding_id,
                    "created_at": now.isoformat(),
                },
            )
            self._conn.commit()

        return ResearchFinding(
            finding_id=finding_id,
            research_node_id=research_node_id,
            source_task_id=source_task_id,
            content=content,
            validated=True,
            created_at=now,
        )

    def get_finding(self, finding_id: str) -> ResearchFinding | None:
        """Return a finding by ID."""
        row = self._conn.execute(_SELECT_FINDING_BY_ID, (finding_id,)).fetchone()
        return ResearchFinding._from_row(row) if row else None

    def list_findings(self, research_node_id: str) -> list[ResearchFinding]:
        """Return all findings for a research node."""
        rows = self._conn.execute(_SELECT_FINDINGS_BY_NODE, (research_node_id,)).fetchall()
        return [ResearchFinding._from_row(r) for r in rows]

    def list_signals(self, task_id: str) -> list[ReevaluationSignal]:
        """Return all reevaluation signals for a task, oldest first."""
        rows = self._conn.execute(_SELECT_SIGNALS_BY_TASK, (task_id,)).fetchall()
        return [ReevaluationSignal._from_row(r) for r in rows]
