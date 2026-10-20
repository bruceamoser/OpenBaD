"""SQLite CRUD for routines and routine runs."""

from __future__ import annotations

import sqlite3
import time

from openbad.routines import Routine, RoutineRun, RunStatus


def _routine_to_row(r: Routine) -> dict:
    return {
        "routine_id": r.routine_id,
        "name": r.name,
        "description": r.description,
        "body_md": r.body_md,
        "recurrence_rule": r.recurrence_rule,
        "next_run_at": r.next_run_at,
        "enabled": 1 if r.enabled else 0,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _routine_from_row(row: sqlite3.Row) -> Routine:
    return Routine(
        routine_id=row["routine_id"],
        name=row["name"],
        description=row["description"],
        body_md=row["body_md"],
        recurrence_rule=row["recurrence_rule"],
        next_run_at=row["next_run_at"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_from_row(row: sqlite3.Row) -> RoutineRun:
    return RoutineRun(
        run_id=row["run_id"],
        routine_id=row["routine_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=RunStatus(row["status"]),
        output=row["output"],
        error=row["error"],
        tokens_used=row["tokens_used"],
    )


class RoutineStore:
    """CRUD operations for routines backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── Routines ──────────────────────────────────────────────────

    def create(self, routine: Routine) -> Routine:
        self._conn.execute(
            """
            INSERT INTO routines (
                routine_id, name, description, body_md,
                recurrence_rule, next_run_at, enabled,
                created_at, updated_at
            ) VALUES (
                :routine_id, :name, :description, :body_md,
                :recurrence_rule, :next_run_at, :enabled,
                :created_at, :updated_at
            )
            """,
            _routine_to_row(routine),
        )
        self._conn.commit()
        return routine

    def get(self, routine_id: str) -> Routine | None:
        row = self._conn.execute(
            "SELECT * FROM routines WHERE routine_id = ?", (routine_id,)
        ).fetchone()
        return _routine_from_row(row) if row else None

    def list_all(self, *, enabled_only: bool = False) -> list[Routine]:
        if enabled_only:
            rows = self._conn.execute(
                "SELECT * FROM routines WHERE enabled = 1 ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM routines ORDER BY created_at"
            ).fetchall()
        return [_routine_from_row(r) for r in rows]

    def update(
        self,
        routine_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        body_md: str | None = None,
        recurrence_rule: str | None = ...,  # type: ignore[assignment]
        next_run_at: float | None = ...,  # type: ignore[assignment]
        enabled: bool | None = None,
    ) -> Routine | None:
        existing = self.get(routine_id)
        if existing is None:
            return None
        sets: list[str] = []
        params: dict[str, object] = {"rid": routine_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if description is not None:
            sets.append("description = :description")
            params["description"] = description
        if body_md is not None:
            sets.append("body_md = :body_md")
            params["body_md"] = body_md
        if recurrence_rule is not ...:
            sets.append("recurrence_rule = :recurrence_rule")
            params["recurrence_rule"] = recurrence_rule
        if next_run_at is not ...:
            sets.append("next_run_at = :next_run_at")
            params["next_run_at"] = next_run_at
        if enabled is not None:
            sets.append("enabled = :enabled")
            params["enabled"] = 1 if enabled else 0
        if not sets:
            return existing
        sets.append("updated_at = :updated_at")
        params["updated_at"] = time.time()
        self._conn.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE routine_id = :rid",
            params,
        )
        self._conn.commit()
        return self.get(routine_id)

    def delete(self, routine_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM routines WHERE routine_id = ?", (routine_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def due_routines(self, now: float | None = None) -> list[Routine]:
        """Return enabled routines whose next_run_at <= now."""
        if now is None:
            now = time.time()
        rows = self._conn.execute(
            """
            SELECT * FROM routines
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at
            """,
            (now,),
        ).fetchall()
        return [_routine_from_row(r) for r in rows]

    # ── Runs ──────────────────────────────────────────────────────

    def create_run(self, run: RoutineRun) -> RoutineRun:
        self._conn.execute(
            """
            INSERT INTO routine_runs (
                run_id, routine_id, started_at, finished_at,
                status, output, error, tokens_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id, run.routine_id, run.started_at,
                run.finished_at, run.status.value, run.output,
                run.error, run.tokens_used,
            ),
        )
        self._conn.commit()
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        output: str = "",
        error: str = "",
        tokens_used: int = 0,
    ) -> None:
        self._conn.execute(
            """
            UPDATE routine_runs
            SET finished_at = ?, status = ?, output = ?, error = ?, tokens_used = ?
            WHERE run_id = ?
            """,
            (time.time(), status.value, output, error, tokens_used, run_id),
        )
        self._conn.commit()

    def list_runs(
        self, routine_id: str, *, limit: int = 20
    ) -> list[RoutineRun]:
        rows = self._conn.execute(
            """
            SELECT * FROM routine_runs
            WHERE routine_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (routine_id, limit),
        ).fetchall()
        return [_run_from_row(r) for r in rows]
