from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskModel
from openbad.tasks.notes import NoteStore, TaskNote
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def note_store(db) -> NoteStore:
    return NoteStore(db)


@pytest.fixture()
def task_id(db) -> str:
    """Insert a real task and return its ID (satisfies FK constraint)."""
    store = TaskStore(db)
    task = TaskModel.new("Note test task")
    store.create_task(task)
    return task.task_id


# ---------------------------------------------------------------------------
# Note write / read
# ---------------------------------------------------------------------------


def test_add_note_returns_note_with_id(note_store: NoteStore, task_id: str) -> None:
    note = note_store.add_note(task_id, "First note")

    assert note.note_id is not None
    assert note.note_text == "First note"
    assert note.task_id == task_id


def test_add_note_with_all_structured_fields(note_store: NoteStore, task_id: str) -> None:
    note = note_store.add_note(
        task_id,
        "Rich note",
        facts=["f1", "f2"],
        implications=["i1"],
        artifact_refs=["data/tasks/task-1/out.json"],
        extra={"custom_key": 42},
    )

    assert note.summary["facts"] == ["f1", "f2"]
    assert note.summary["implications"] == ["i1"]
    assert note.summary["artifact_refs"] == ["data/tasks/task-1/out.json"]
    assert note.summary["custom_key"] == 42


def test_note_persists_across_get(note_store: NoteStore, task_id: str) -> None:
    note = note_store.add_note(task_id, "Persisted", facts=["x"])

    fetched = note_store.get_note(note.note_id)  # type: ignore[arg-type]

    assert fetched is not None
    assert fetched.note_id == note.note_id
    assert fetched.note_text == "Persisted"
    assert fetched.summary["facts"] == ["x"]


def test_get_note_unknown_returns_none(note_store: NoteStore) -> None:
    assert note_store.get_note(9999) is None


# ---------------------------------------------------------------------------
# Note listing / filtering
# ---------------------------------------------------------------------------


def test_list_notes_returns_all_in_order(note_store: NoteStore, task_id: str) -> None:
    note_store.add_note(task_id, "Note A")
    note_store.add_note(task_id, "Note B")
    note_store.add_note(task_id, "Note C")

    notes = note_store.list_notes(task_id)

    assert len(notes) == 3
    assert [n.note_text for n in notes] == ["Note A", "Note B", "Note C"]


def test_list_notes_empty_for_unknown_task(note_store: NoteStore) -> None:
    assert note_store.list_notes("no-task") == []


def test_list_notes_scoped_to_task(db, note_store: NoteStore) -> None:
    store = TaskStore(db)
    task_a = TaskModel.new("Task A")
    task_b = TaskModel.new("Task B")
    store.create_task(task_a)
    store.create_task(task_b)

    note_store.add_note(task_a.task_id, "A note")
    note_store.add_note(task_b.task_id, "B note")

    a_notes = note_store.list_notes(task_a.task_id)
    b_notes = note_store.list_notes(task_b.task_id)

    assert len(a_notes) == 1
    assert a_notes[0].task_id == task_a.task_id
    assert len(b_notes) == 1
    assert b_notes[0].task_id == task_b.task_id


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def test_task_note_round_trip() -> None:
    note = TaskNote(
        note_id=1,
        task_id="t1",
        note_text="hello",
        created_at=1.0,
        summary={"facts": ["a"]},
    )
    assert TaskNote.from_dict(note.to_dict()) == note


def test_empty_summary_is_empty_dict(note_store: NoteStore, task_id: str) -> None:
    note = note_store.add_note(task_id, "No summary")

    fetched = note_store.get_note(note.note_id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.summary == {}
