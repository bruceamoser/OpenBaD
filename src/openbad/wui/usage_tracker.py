"""Persistent token usage tracker for the WUI and chat orchestration.

Stores token consumption in SQLite so usage survives restarts and can be
aggregated by provider, model, and cognitive system over time.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_DAILY_CEILING = 1_000_000
_DEFAULT_HOURLY_CEILING = 100_000

_CREATE_USAGE_TABLE = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    system TEXT NOT NULL,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tokens INTEGER NOT NULL
);
"""

_CREATE_USAGE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events (timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model ON usage_events (provider, model);",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_system ON usage_events (system);",
)


@dataclass(frozen=True)
class UsageLimits:
    daily_ceiling: int
    hourly_ceiling: int


class UsageTracker:
    """Track token usage with persistent SQLite storage."""

    def __init__(
        self,
        db_path: str | Path = "/var/lib/openbad/state/usage.db",
        *,
        daily_ceiling: int = _DEFAULT_DAILY_CEILING,
        hourly_ceiling: int = _DEFAULT_HOURLY_CEILING,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_USAGE_TABLE)
        for statement in _CREATE_USAGE_INDEXES:
            self._conn.execute(statement)
        self._conn.commit()
        self._limits = UsageLimits(
            daily_ceiling=daily_ceiling,
            hourly_ceiling=hourly_ceiling,
        )

    @property
    def limits(self) -> UsageLimits:
        return self._limits

    def close(self) -> None:
        self._conn.close()

    def record(
        self,
        *,
        provider: str,
        model: str,
        system: str,
        tokens: int,
        request_id: str = "",
        session_id: str = "",
        timestamp: float | None = None,
    ) -> None:
        if tokens <= 0:
            return
        self._conn.execute(
            """
            INSERT INTO usage_events (
                timestamp, provider, model, system, request_id, session_id, tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp if timestamp is not None else time.time(),
                provider or "unknown",
                model or "unknown",
                system or "unknown",
                request_id,
                session_id,
                tokens,
            ),
        )
        self._conn.commit()

    def _sum_since(self, since: float) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) AS total FROM usage_events WHERE timestamp >= ?",
            (since,),
        ).fetchone()
        return int(row["total"])

    def _total_used(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) AS total FROM usage_events"
        ).fetchone()
        return int(row["total"])

    def _action_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS total FROM usage_events").fetchone()
        return int(row["total"])

    def snapshot(self) -> dict[str, object]:
        now = time.time()
        total_used = self._total_used()
        daily_used = self._sum_since(now - 86400)
        hourly_used = self._sum_since(now - 3600)
        action_count = self._action_count()
        daily_remaining_pct = (
            max(0.0, (1 - daily_used / self._limits.daily_ceiling) * 100)
            if self._limits.daily_ceiling
            else 0.0
        )
        hourly_remaining_pct = (
            max(0.0, (1 - hourly_used / self._limits.hourly_ceiling) * 100)
            if self._limits.hourly_ceiling
            else 0.0
        )

        return {
            "limits": {
                "daily_ceiling": self._limits.daily_ceiling,
                "hourly_ceiling": self._limits.hourly_ceiling,
            },
            "summary": {
                "total_used": total_used,
                "daily_used": daily_used,
                "hourly_used": hourly_used,
                "daily_remaining_pct": round(daily_remaining_pct, 2),
                "hourly_remaining_pct": round(hourly_remaining_pct, 2),
                "cost_per_action_avg": round(total_used / action_count, 2)
                if action_count
                else 0.0,
                "request_count": action_count,
            },
            "by_provider_model": self._group_by_provider_model(),
            "by_system": self._group_by_system(),
            "daily_series": self._daily_series(),
            "recent_events": self._recent_events(),
        }

    def _group_by_provider_model(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT provider, model, SUM(tokens) AS tokens, COUNT(*) AS request_count,
                   MAX(timestamp) AS last_timestamp
            FROM usage_events
            GROUP BY provider, model
            ORDER BY tokens DESC, provider ASC, model ASC
            """
        ).fetchall()
        return [
            {
                "provider": row["provider"],
                "model": row["model"],
                "tokens": int(row["tokens"]),
                "request_count": int(row["request_count"]),
                "last_timestamp": float(row["last_timestamp"] or 0.0),
            }
            for row in rows
        ]

    def _group_by_system(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT system, SUM(tokens) AS tokens, COUNT(*) AS request_count,
                   MAX(timestamp) AS last_timestamp
            FROM usage_events
            GROUP BY system
            ORDER BY tokens DESC, system ASC
            """
        ).fetchall()
        return [
            {
                "system": row["system"],
                "tokens": int(row["tokens"]),
                "request_count": int(row["request_count"]),
                "last_timestamp": float(row["last_timestamp"] or 0.0),
            }
            for row in rows
        ]

    def _daily_series(self, days: int = 14) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT strftime('%Y-%m-%d', datetime(timestamp, 'unixepoch')) AS day,
                   SUM(tokens) AS tokens,
                   COUNT(*) AS request_count
            FROM usage_events
            WHERE timestamp >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (time.time() - days * 86400,),
        ).fetchall()
        return [
            {
                "day": row["day"],
                "tokens": int(row["tokens"]),
                "request_count": int(row["request_count"]),
            }
            for row in rows
        ]

    def _recent_events(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT timestamp, provider, model, system, request_id, session_id, tokens
            FROM usage_events
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "timestamp": float(row["timestamp"]),
                "provider": row["provider"],
                "model": row["model"],
                "system": row["system"],
                "request_id": row["request_id"],
                "session_id": row["session_id"],
                "tokens": int(row["tokens"]),
            }
            for row in rows
        ]