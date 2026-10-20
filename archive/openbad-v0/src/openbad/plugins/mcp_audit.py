"""MCP audit record persistence for Phase 9.

Every tool invocation through the MCP session layer produces an
:class:`AuditRecord` stored in the ``mcp_audit`` table.  Audit entries are
queryable by ``task_id`` and ``run_id``.

Failed calls include a structured ``error_summary`` string so post-hoc
analysis can distinguish successful tool calls from errors without decoding
the full payload.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Schema / initialization
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS mcp_audit (
    audit_id    TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    task_id     TEXT,
    run_id      TEXT,
    tool_name   TEXT NOT NULL,
    success     INTEGER NOT NULL DEFAULT 1,
    error_summary TEXT,
    payload     TEXT,
    recorded_at TEXT NOT NULL
)
"""

_INSERT = """
INSERT INTO mcp_audit
    (audit_id, session_id, task_id, run_id, tool_name, success,
     error_summary, payload, recorded_at)
VALUES
    (:audit_id, :session_id, :task_id, :run_id, :tool_name, :success,
     :error_summary, :payload, :recorded_at)
"""

_SELECT_BY_TASK = "SELECT * FROM mcp_audit WHERE task_id = ? ORDER BY recorded_at"
_SELECT_BY_RUN = "SELECT * FROM mcp_audit WHERE run_id = ? ORDER BY recorded_at"


def initialize_audit_db(conn: sqlite3.Connection) -> None:
    """Create the ``mcp_audit`` table if it does not exist."""
    conn.execute(_CREATE_TABLE)
    conn.commit()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AuditRecord:
    """An immutable record of a single MCP tool invocation."""

    audit_id: str
    session_id: str
    tool_name: str
    success: bool
    recorded_at: datetime
    task_id: str | None = None
    run_id: str | None = None
    error_summary: str | None = None
    payload: str | None = None

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "success": self.success,
            "error_summary": self.error_summary,
            "payload": self.payload,
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> AuditRecord:
        return cls(
            audit_id=row["audit_id"],
            session_id=row["session_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            tool_name=row["tool_name"],
            success=bool(row["success"]),
            error_summary=row["error_summary"],
            payload=row["payload"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MCPAuditStore:
    """Persists and queries MCP tool invocation audit records.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection`` with the ``mcp_audit`` table already
        created (call :func:`initialize_audit_db` beforehand or pass a DB
        that already has it).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn

    def record(
        self,
        session_id: str,
        tool_name: str,
        *,
        success: bool = True,
        task_id: str | None = None,
        run_id: str | None = None,
        error_summary: str | None = None,
        payload: str | None = None,
    ) -> AuditRecord:
        """Persist a tool invocation audit record and return it.

        Parameters
        ----------
        session_id:
            The MCP session that made the call.
        tool_name:
            The tool that was invoked.
        success:
            Whether the call succeeded.
        task_id:
            Optional owning task ID for queryability.
        run_id:
            Optional run ID for queryability.
        error_summary:
            Human-readable error summary (only on failure).
        payload:
            Optional JSON string with extra call context.
        """
        now = datetime.now(tz=UTC)
        audit_id = str(uuid.uuid4())
        self._conn.execute(
            _INSERT,
            {
                "audit_id": audit_id,
                "session_id": session_id,
                "task_id": task_id,
                "run_id": run_id,
                "tool_name": tool_name,
                "success": int(success),
                "error_summary": error_summary,
                "payload": payload,
                "recorded_at": now.isoformat(),
            },
        )
        self._conn.commit()
        return AuditRecord(
            audit_id=audit_id,
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            tool_name=tool_name,
            success=success,
            error_summary=error_summary,
            payload=payload,
            recorded_at=now,
        )

    def query_by_task(self, task_id: str) -> list[AuditRecord]:
        """Return all audit records for *task_id*, oldest first."""
        rows = self._conn.execute(_SELECT_BY_TASK, (task_id,)).fetchall()
        return [AuditRecord._from_row(r) for r in rows]

    def query_by_run(self, run_id: str) -> list[AuditRecord]:
        """Return all audit records for *run_id*, oldest first."""
        rows = self._conn.execute(_SELECT_BY_RUN, (run_id,)).fetchall()
        return [AuditRecord._from_row(r) for r in rows]
