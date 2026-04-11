"""Persisted heartbeat state for the Phase 9 scheduler subsystem.

:class:`HeartbeatStore` reads and updates a single-row ``heartbeat_state``
table so the scheduler can resume after process restart without re-dispatching
already-processed work.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HeartbeatState:
    """Mutable snapshot of the persisted heartbeat row."""

    last_heartbeat_at: float = 0.0
    last_triage_at: float = 0.0
    last_context_required_dispatch_at: float = 0.0
    last_research_review_at: float = 0.0
    last_sleep_cycle_at: float = 0.0
    last_maintenance_at: float = 0.0
    silent_skip_count: int = 0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class HeartbeatStore:
    """Read/write access to the ``heartbeat_state`` table.

    The table is constrained to exactly one row (``id = 1``).  The first call
    to :meth:`load` creates that row with zeroed timestamps if it does not
    exist.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the ``heartbeat_state`` table if it does not already exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeat_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_heartbeat_at REAL NOT NULL DEFAULT 0.0,
                last_triage_at REAL NOT NULL DEFAULT 0.0,
                last_context_required_dispatch_at REAL NOT NULL DEFAULT 0.0,
                last_research_review_at REAL NOT NULL DEFAULT 0.0,
                last_sleep_cycle_at REAL NOT NULL DEFAULT 0.0,
                last_maintenance_at REAL NOT NULL DEFAULT 0.0,
                silent_skip_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> HeartbeatState:
        """Return the current heartbeat state, inserting defaults on first use."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO heartbeat_state (id) VALUES (1)
            """
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT last_heartbeat_at, last_triage_at,
                   last_context_required_dispatch_at, last_research_review_at,
                   last_sleep_cycle_at, last_maintenance_at, silent_skip_count
            FROM heartbeat_state WHERE id = 1
            """
        ).fetchone()
        return HeartbeatState(
            last_heartbeat_at=row[0],
            last_triage_at=row[1],
            last_context_required_dispatch_at=row[2],
            last_research_review_at=row[3],
            last_sleep_cycle_at=row[4],
            last_maintenance_at=row[5],
            silent_skip_count=row[6],
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_heartbeat(self, ts: float | None = None) -> None:
        """Stamp ``last_heartbeat_at`` with *ts* (defaults to ``time.time()``)."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_heartbeat_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def record_triage(self, ts: float | None = None) -> None:
        """Stamp ``last_triage_at``."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_triage_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def record_context_required_dispatch(self, ts: float | None = None) -> None:
        """Stamp ``last_context_required_dispatch_at``."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_context_required_dispatch_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def record_research_review(self, ts: float | None = None) -> None:
        """Stamp ``last_research_review_at``."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_research_review_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def record_sleep_cycle(self, ts: float | None = None) -> None:
        """Stamp ``last_sleep_cycle_at``."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_sleep_cycle_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def record_maintenance(self, ts: float | None = None) -> None:
        """Stamp ``last_maintenance_at``."""
        self._conn.execute(
            "UPDATE heartbeat_state SET last_maintenance_at = ? WHERE id = 1",
            (ts if ts is not None else time.time(),),
        )
        self._conn.commit()

    def increment_silent_skip(self) -> int:
        """Increment ``silent_skip_count`` by one and return the new value."""
        self._conn.execute(
            "UPDATE heartbeat_state SET silent_skip_count = silent_skip_count + 1 WHERE id = 1"
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT silent_skip_count FROM heartbeat_state WHERE id = 1"
        ).fetchone()
        return int(row[0])

    def reset_silent_skip(self) -> None:
        """Reset ``silent_skip_count`` to zero when work is dispatched."""
        self._conn.execute(
            "UPDATE heartbeat_state SET silent_skip_count = 0 WHERE id = 1"
        )
        self._conn.commit()
