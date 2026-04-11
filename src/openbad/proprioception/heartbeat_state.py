"""Heartbeat scheduler state persistence backed by the SQLite state DB.

The ``heartbeat_state`` table always contains exactly one row (id = 1).
:class:`HeartbeatStateStore` provides typed read/write helpers to keep that row
current across scheduler loop iterations.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time


@dataclasses.dataclass
class HeartbeatState:
    """Snapshot of the heartbeat state row."""

    last_heartbeat_at: float | None
    last_triage_at: float | None
    last_context_required_dispatch_at: float | None
    last_research_review_at: float | None
    last_sleep_cycle_at: float | None
    last_maintenance_at: float | None
    silent_skip_count: int


_EMPTY = HeartbeatState(
    last_heartbeat_at=None,
    last_triage_at=None,
    last_context_required_dispatch_at=None,
    last_research_review_at=None,
    last_sleep_cycle_at=None,
    last_maintenance_at=None,
    silent_skip_count=0,
)


class HeartbeatStateStore:
    """Read and write the singleton ``heartbeat_state`` row.

    The table uses ``id = 1`` as its only row (enforced by a CHECK constraint).
    On first read, if the row does not yet exist it is inserted with all
    timestamps as NULL.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_row(self) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO heartbeat_state (id) VALUES (1)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> HeartbeatState:
        """Return the current heartbeat state, bootstrapping the row as needed."""
        self._ensure_row()
        row = self._conn.execute("SELECT * FROM heartbeat_state WHERE id = 1").fetchone()
        return HeartbeatState(
            last_heartbeat_at=row["last_heartbeat_at"],
            last_triage_at=row["last_triage_at"],
            last_context_required_dispatch_at=row["last_context_required_dispatch_at"],
            last_research_review_at=row["last_research_review_at"],
            last_sleep_cycle_at=row["last_sleep_cycle_at"],
            last_maintenance_at=row["last_maintenance_at"],
            silent_skip_count=row["silent_skip_count"],
        )

    # ------------------------------------------------------------------
    # Write — individual fields
    # ------------------------------------------------------------------

    def record_heartbeat(self) -> None:
        """Update ``last_heartbeat_at`` to now and reset the silent-skip counter."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_heartbeat_at = ?, silent_skip_count = 0"
            " WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def record_triage(self) -> None:
        """Update ``last_triage_at`` to now."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_triage_at = ? WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def record_context_dispatch(self) -> None:
        """Update ``last_context_required_dispatch_at`` to now."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_context_required_dispatch_at = ?"
            " WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def record_research_review(self) -> None:
        """Update ``last_research_review_at`` to now."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_research_review_at = ? WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def record_sleep_cycle(self) -> None:
        """Update ``last_sleep_cycle_at`` to now."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_sleep_cycle_at = ? WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def record_maintenance(self) -> None:
        """Update ``last_maintenance_at`` to now."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET last_maintenance_at = ? WHERE id = 1",
            (time.time(),),
        )
        self._conn.commit()

    def increment_silent_skip(self) -> int:
        """Increment the silent-skip counter and return the new count."""
        self._ensure_row()
        self._conn.execute(
            "UPDATE heartbeat_state SET silent_skip_count = silent_skip_count + 1"
            " WHERE id = 1",
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT silent_skip_count FROM heartbeat_state WHERE id = 1"
        ).fetchone()
        return int(row["silent_skip_count"])
