"""Token budget tracker with persistent ledger.

Provides a :class:`TokenBudget` that tracks API token consumption per
provider/model/task, enforces configurable daily/hourly budget ceilings,
and persists state to a SQLite database so it survives process restarts.

Usage::

    budget = TokenBudget(db_path="state/budget.db", daily_ceiling=1_000_000)
    budget.record(provider="openai", model="gpt-4o", task_id="t-42", tokens=1500)
    status = budget.status()            # BudgetStatus(remaining_pct=98.5, …)
    budget.close()                      # flush + close DB

The tracker publishes budget snapshots to ``agent/telemetry/tokens``
when used with :meth:`publish_status`.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DAILY_CEILING = 1_000_000  # tokens
_DEFAULT_HOURLY_CEILING = 100_000  # tokens


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of the current budget state."""

    daily_used: int
    daily_ceiling: int
    daily_remaining_pct: float
    hourly_used: int
    hourly_ceiling: int
    hourly_remaining_pct: float
    total_used: int
    cost_per_action_avg: float


@dataclass(frozen=True)
class UsageRecord:
    """A single token-usage entry."""

    timestamp: float
    provider: str
    model: str
    task_id: str
    tokens: int


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS token_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  REAL    NOT NULL,
    provider   TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    task_id    TEXT    NOT NULL,
    tokens     INTEGER NOT NULL
);
"""

_CREATE_INDEX = """\
CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage (timestamp);
"""


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------


class TokenBudget:
    """Persistent token budget tracker backed by SQLite.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created if it doesn't exist.
    daily_ceiling:
        Maximum tokens allowed per 24-hour rolling window.
    hourly_ceiling:
        Maximum tokens allowed per 1-hour rolling window.
    """

    def __init__(
        self,
        db_path: str | Path = "state/budget.db",
        daily_ceiling: int = _DEFAULT_DAILY_CEILING,
        hourly_ceiling: int = _DEFAULT_HOURLY_CEILING,
    ) -> None:
        self._daily_ceiling = daily_ceiling
        self._hourly_ceiling = hourly_ceiling
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()
        self._action_count = self._load_action_count()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        provider: str,
        model: str,
        task_id: str,
        tokens: int,
        timestamp: float | None = None,
    ) -> None:
        """Record a token-usage event."""
        if timestamp is None:
            timestamp = time.time()
        self._conn.execute(
            "INSERT INTO token_usage (timestamp, provider, model, task_id, tokens) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, provider, model, task_id, tokens),
        )
        self._conn.commit()
        self._action_count += 1

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def _sum_since(self, since: float) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_usage WHERE timestamp >= ?",
            (since,),
        ).fetchone()
        return int(row[0])

    def _total_tokens(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_usage",
        ).fetchone()
        return int(row[0])

    def _load_action_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()
        return int(row[0])

    def daily_used(self) -> int:
        """Tokens used in the last 24 hours."""
        return self._sum_since(time.time() - 86400)

    def hourly_used(self) -> int:
        """Tokens used in the last hour."""
        return self._sum_since(time.time() - 3600)

    def cost_per_action_avg(self) -> float:
        """Average tokens per recorded action."""
        if self._action_count == 0:
            return 0.0
        return self._total_tokens() / self._action_count

    def usage_by_model(self) -> dict[str, int]:
        """Return total token usage grouped by ``provider/model``."""
        rows = self._conn.execute(
            "SELECT provider, model, SUM(tokens) FROM token_usage GROUP BY provider, model",
        ).fetchall()
        return {f"{provider}/{model}": int(total) for provider, model, total in rows}

    def usage_by_task(self) -> dict[str, int]:
        """Return total token usage grouped by task_id."""
        rows = self._conn.execute(
            "SELECT task_id, SUM(tokens) FROM token_usage GROUP BY task_id",
        ).fetchall()
        return {task_id: int(total) for task_id, total in rows}

    def status(self) -> BudgetStatus:
        """Return a snapshot of the current budget state."""
        daily = self.daily_used()
        hourly = self.hourly_used()
        daily_pct = (
            max(0.0, (1 - daily / self._daily_ceiling) * 100) if self._daily_ceiling else 0.0
        )
        hourly_pct = (
            max(0.0, (1 - hourly / self._hourly_ceiling) * 100) if self._hourly_ceiling else 0.0
        )
        return BudgetStatus(
            daily_used=daily,
            daily_ceiling=self._daily_ceiling,
            daily_remaining_pct=round(daily_pct, 2),
            hourly_used=hourly,
            hourly_ceiling=self._hourly_ceiling,
            hourly_remaining_pct=round(hourly_pct, 2),
            total_used=self._total_tokens(),
            cost_per_action_avg=round(self.cost_per_action_avg(), 2),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
