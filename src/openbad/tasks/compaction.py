"""Post-node raw output compaction for Phase 9 task orchestration.

After a node completes, raw payloads in ``task_events`` may be large and are
no longer needed in full.  :func:`compact_node_output` replaces the raw
payload of the node's completion event with a lightweight ``{"compacted": true}``
marker, preserving only the structured summary (written via :class:`NoteStore`)
for downstream context reconstruction.

The compaction step is intentionally a single-purpose hook: it writes a note
(if a summary is provided) and then clears the raw payload from the most
recent ``node_output`` event for the given node.  It does nothing if no such
event exists, making it safe to call unconditionally after each node finishes.
"""

from __future__ import annotations

import json
import sqlite3

from openbad.tasks.notes import NoteStore

# ---------------------------------------------------------------------------
# Compaction result
# ---------------------------------------------------------------------------


class CompactionResult:
    """Outcome of a :func:`compact_node_output` call."""

    def __init__(
        self,
        *,
        compacted: bool,
        note_id: int | None,
        event_id: str | None,
    ) -> None:
        self.compacted = compacted
        """``True`` if a raw payload was replaced."""
        self.note_id = note_id
        """The newly written note ID, or ``None`` if no summary was provided."""
        self.event_id = event_id
        """The event whose payload was cleared, or ``None`` if none found."""

    def __repr__(self) -> str:
        return (
            f"CompactionResult(compacted={self.compacted!r},"
            f" note_id={self.note_id!r}, event_id={self.event_id!r})"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compact_node_output(
    conn: sqlite3.Connection,
    task_id: str,
    node_id: str,
    *,
    summary_text: str | None = None,
    facts: list[str] | None = None,
    implications: list[str] | None = None,
    artifact_refs: list[str] | None = None,
) -> CompactionResult:
    """Replace the raw ``node_output`` event payload with a compact marker.

    Parameters
    ----------
    conn:
        Open ``sqlite3.Connection`` to the state database.
    task_id:
        The owning task.
    node_id:
        The completed node.
    summary_text:
        Prose summary retained as a :class:`~openbad.tasks.notes.TaskNote`.
        If ``None`` no note is written.
    facts / implications / artifact_refs:
        Structured fields forwarded to :meth:`~openbad.tasks.notes.NoteStore.add_note`.

    Returns
    -------
    CompactionResult
        Describes what was written and whether compaction occurred.
    """
    # Optionally write a structured note
    note_id: int | None = None
    if summary_text is not None:
        ns = NoteStore(conn)
        note = ns.add_note(
            task_id,
            summary_text,
            facts=facts,
            implications=implications,
            artifact_refs=artifact_refs,
        )
        note_id = note.note_id

    # Find the latest node_output event for this node
    row = conn.execute(
        "SELECT event_id FROM task_events"
        " WHERE task_id = ? AND node_id = ? AND event_type = 'node_output'"
        " ORDER BY created_at DESC LIMIT 1",
        (task_id, node_id),
    ).fetchone()

    if row is None:
        return CompactionResult(compacted=False, note_id=note_id, event_id=None)

    event_id: str = row["event_id"]
    conn.execute(
        "UPDATE task_events SET payload_json = ? WHERE event_id = ?",
        (json.dumps({"compacted": True}), event_id),
    )
    conn.commit()

    return CompactionResult(compacted=True, note_id=note_id, event_id=event_id)
