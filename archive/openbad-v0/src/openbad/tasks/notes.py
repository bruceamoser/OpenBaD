"""Task note persistence APIs for Phase 9 context capture.

:class:`NoteStore` persists and queries structured notes linked to tasks and
optionally to individual nodes.  Each note carries:

* ``note_text`` — free-form prose or a log entry.
* ``summary_json`` — optional structured payload containing extracted facts,
  implications, and artifact references.

Notes are append-only; they are never updated in place.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time

# ---------------------------------------------------------------------------
# Note model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class TaskNote:
    """In-memory representation of a ``task_notes`` row."""

    note_id: int | None
    task_id: str
    note_text: str
    created_at: float
    summary: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskNote:
        return cls(**data)


# ---------------------------------------------------------------------------
# NoteStore
# ---------------------------------------------------------------------------


class NoteStore:
    """Append-only note storage keyed by task_id.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_note(
        self,
        task_id: str,
        note_text: str,
        *,
        facts: list[str] | None = None,
        implications: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        extra: dict | None = None,
    ) -> TaskNote:
        """Append a note for *task_id*.

        Parameters
        ----------
        task_id:
            The owning task.
        note_text:
            Human-readable prose for the note.
        facts:
            Extracted facts (list of strings).
        implications:
            Derived implications (list of strings).
        artifact_refs:
            Paths or URIs to associated artifacts.
        extra:
            Additional structured fields merged into ``summary_json``.

        Returns
        -------
        TaskNote
            The freshly inserted note (with ``note_id`` populated).
        """
        summary: dict = {}
        if facts is not None:
            summary["facts"] = facts
        if implications is not None:
            summary["implications"] = implications
        if artifact_refs is not None:
            summary["artifact_refs"] = artifact_refs
        if extra:
            summary.update(extra)

        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO task_notes (task_id, created_at, note_text, summary_json)"
            " VALUES (?, ?, ?, ?)",
            (task_id, now, note_text, json.dumps(summary)),
        )
        self._conn.commit()

        return TaskNote(
            note_id=cursor.lastrowid,
            task_id=task_id,
            note_text=note_text,
            created_at=now,
            summary=summary,
        )

    def list_notes(self, task_id: str) -> list[TaskNote]:
        """Return all notes for *task_id* in insertion order."""
        rows = self._conn.execute(
            "SELECT note_id, task_id, note_text, summary_json, created_at"
            " FROM task_notes WHERE task_id = ? ORDER BY note_id ASC",
            (task_id,),
        ).fetchall()
        return [
            TaskNote(
                note_id=row["note_id"],
                task_id=row["task_id"],
                note_text=row["note_text"],
                created_at=row["created_at"],
                summary=json.loads(row["summary_json"]) if row["summary_json"] else {},
            )
            for row in rows
        ]

    def get_note(self, note_id: int) -> TaskNote | None:
        """Return a single note by primary key, or ``None``."""
        row = self._conn.execute(
            "SELECT note_id, task_id, note_text, summary_json, created_at"
            " FROM task_notes WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        return TaskNote(
            note_id=row["note_id"],
            task_id=row["task_id"],
            note_text=row["note_text"],
            created_at=row["created_at"],
            summary=json.loads(row["summary_json"]) if row["summary_json"] else {},
        )
