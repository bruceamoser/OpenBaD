"""Reward-to-endocrine mapping and persistence layer for Phase 9.

Translates :class:`~openbad.tasks.reward_models.RewardResult` objects into
hormone adjustments via :class:`~openbad.endocrine.controller.EndocrineController`
and persists reward evaluation records in the ``reward_records`` SQLite table.

Hormone mapping is configurable via :class:`RewardEndocrineConfig`.  The
default mapping follows intuitive conventions:
- High positive reward → dopamine boost, cortisol reduction
- Negative reward → cortisol boost
- Timeout → adrenaline / cortisol surge
"""

from __future__ import annotations

import contextlib
import dataclasses
import sqlite3
import uuid
from datetime import UTC, datetime

from openbad.endocrine.controller import EndocrineController
from openbad.tasks.reward_models import RewardResult, RewardTrace, TraceOutcome

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS reward_records (
    record_id    TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    template_id  TEXT NOT NULL,
    score        REAL NOT NULL,
    rationale    TEXT,
    created_at   TEXT NOT NULL
)
"""

_INSERT = """
INSERT INTO reward_records
    (record_id, task_id, node_id, template_id, score, rationale, created_at)
VALUES
    (:record_id, :task_id, :node_id, :template_id, :score, :rationale, :created_at)
"""

_SELECT_BY_TASK = "SELECT * FROM reward_records WHERE task_id = ? ORDER BY created_at"
_SELECT_BY_NODE = "SELECT * FROM reward_records WHERE node_id = ? ORDER BY created_at"


def initialize_reward_db(conn: sqlite3.Connection) -> None:
    """Create the ``reward_records`` table if it does not exist."""
    conn.execute(_CREATE_TABLE)
    conn.commit()


# ---------------------------------------------------------------------------
# Hormone mapping config
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HormoneMapping:
    """A single hormone adjustment rule.

    Parameters
    ----------
    hormone:
        Name of the hormone to adjust (e.g. ``"dopamine"``).
    amount:
        Signed float.  Positive = boost, negative = reduction.
    """

    hormone: str
    amount: float


@dataclasses.dataclass
class RewardEndocrineConfig:
    """Configures how reward scores map to hormone adjustments.

    Each outcome has a list of :class:`HormoneMapping` rules applied whenever
    a :class:`~openbad.tasks.reward_models.RewardResult` with that outcome is
    processed.  Rules are cumulative within a single evaluation.
    """

    mappings: dict[TraceOutcome, list[HormoneMapping]] = dataclasses.field(
        default_factory=dict
    )

    @classmethod
    def default(cls) -> RewardEndocrineConfig:
        """Return sensible defaults matching the project endocrine conventions."""
        return cls(
            mappings={
                TraceOutcome.SUCCESS: [
                    HormoneMapping("dopamine", 0.3),
                    HormoneMapping("cortisol", -0.1),
                ],
                TraceOutcome.FAILURE: [
                    HormoneMapping("cortisol", 0.2),
                ],
                TraceOutcome.TIMEOUT: [
                    HormoneMapping("adrenaline", 0.4),
                    HormoneMapping("cortisol", 0.2),
                ],
                TraceOutcome.CANCELLED: [],
            }
        )


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RewardRecord:
    """A persisted snapshot of a reward evaluation."""

    record_id: str
    task_id: str
    node_id: str
    template_id: str
    score: float
    created_at: datetime
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "template_id": self.template_id,
            "score": self.score,
            "rationale": self.rationale,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> RewardRecord:
        return cls(
            record_id=row["record_id"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            template_id=row["template_id"],
            score=row["score"],
            rationale=row["rationale"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Reward→Endocrine bridge
# ---------------------------------------------------------------------------


class RewardEndocrineBridge:
    """Applies reward results to the endocrine system and persists records.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` with the ``reward_records`` table.
    controller:
        The :class:`~openbad.endocrine.controller.EndocrineController` to
        receive hormone adjustments.
    config:
        Hormone mapping configuration.  Defaults to :meth:`RewardEndocrineConfig.default`.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        controller: EndocrineController,
        config: RewardEndocrineConfig | None = None,
    ) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._controller = controller
        self._config = config or RewardEndocrineConfig.default()

    def apply(self, trace: RewardTrace, result: RewardResult) -> RewardRecord:
        """Persist reward record and apply configured hormone adjustments.

        Parameters
        ----------
        trace:
            The execution trace that was evaluated.
        result:
            The reward result from the evaluator.

        Returns
        -------
        RewardRecord
            The persisted record.
        """
        # Persist
        record_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        self._conn.execute(
            _INSERT,
            {
                "record_id": record_id,
                "task_id": trace.task_id,
                "node_id": trace.node_id,
                "template_id": result.template_id,
                "score": result.score,
                "rationale": result.rationale,
                "created_at": now.isoformat(),
            },
        )
        self._conn.commit()

        # Hormone adjustments — may raise on unrecognized hormone; callers
        # should handle gracefully (non-fatal mandate from issue AC).
        mappings = self._config.mappings.get(trace.outcome, [])
        for mapping in mappings:
            with contextlib.suppress(Exception):
                self._controller.trigger(mapping.hormone, mapping.amount)

        return RewardRecord(
            record_id=record_id,
            task_id=trace.task_id,
            node_id=trace.node_id,
            template_id=result.template_id,
            score=result.score,
            rationale=result.rationale,
            created_at=now,
        )

    def query_by_task(self, task_id: str) -> list[RewardRecord]:
        """Return all reward records for *task_id*, oldest first."""
        rows = self._conn.execute(_SELECT_BY_TASK, (task_id,)).fetchall()
        return [RewardRecord._from_row(r) for r in rows]

    def query_by_node(self, node_id: str) -> list[RewardRecord]:
        """Return all reward records for *node_id*, oldest first."""
        rows = self._conn.execute(_SELECT_BY_NODE, (node_id,)).fetchall()
        return [RewardRecord._from_row(r) for r in rows]
