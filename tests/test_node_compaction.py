from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.compaction import compact_node_output
from openbad.tasks.models import NodeModel, TaskModel
from openbad.tasks.notes import NoteStore
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def store(db):
    return TaskStore(db)


def make_task_and_node(store: TaskStore) -> tuple[TaskModel, NodeModel]:
    task = TaskModel.new("Compaction task")
    store.create_task(task)
    node = NodeModel.new(task.task_id, "Work node")
    store.create_node(node)
    return task, node


def add_node_output_event(store: TaskStore, task_id: str, node_id: str, payload: dict) -> str:
    """Helper: append a node_output event and return its event_id."""
    return store.append_event(task_id, "node_output", node_id=node_id, payload=payload)


# ---------------------------------------------------------------------------
# Raw payload removal
# ---------------------------------------------------------------------------


def test_raw_payload_replaced_with_compact_marker(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"raw": "big blob"})

    result = compact_node_output(db, task.task_id, node.node_id)

    assert result.compacted is True
    events = store.list_events(task.task_id)
    output_events = [e for e in events if e["event_type"] == "node_output"]
    assert output_events[0]["payload"] == {"compacted": True}


def test_raw_content_no_longer_present_after_compaction(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"raw": "secret data", "tokens": 999})

    compact_node_output(db, task.task_id, node.node_id)

    events = store.list_events(task.task_id)
    payload = events[0]["payload"]
    assert "raw" not in payload
    assert "tokens" not in payload


def test_no_event_skips_compaction(db, store: TaskStore) -> None:
    """Compact with no node_output event is a no-op."""
    task, node = make_task_and_node(store)

    result = compact_node_output(db, task.task_id, node.node_id)

    assert result.compacted is False
    assert result.event_id is None


# ---------------------------------------------------------------------------
# Structured summary retention
# ---------------------------------------------------------------------------


def test_summary_note_written_when_provided(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"raw": "data"})

    result = compact_node_output(
        db,
        task.task_id,
        node.node_id,
        summary_text="Node completed successfully",
        facts=["found X", "confirmed Y"],
    )

    assert result.note_id is not None
    ns = NoteStore(db)
    note = ns.get_note(result.note_id)
    assert note is not None
    assert note.note_text == "Node completed successfully"
    assert note.summary["facts"] == ["found X", "confirmed Y"]


def test_no_note_when_no_summary(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"raw": "x"})

    result = compact_node_output(db, task.task_id, node.node_id)

    assert result.note_id is None
    ns = NoteStore(db)
    assert ns.list_notes(task.task_id) == []


def test_summary_retained_after_compaction(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"raw": "verbose"})

    compact_node_output(
        db,
        task.task_id,
        node.node_id,
        summary_text="Summary",
        implications=["Z follows"],
    )

    ns = NoteStore(db)
    notes = ns.list_notes(task.task_id)
    assert len(notes) == 1
    assert notes[0].summary["implications"] == ["Z follows"]


# ---------------------------------------------------------------------------
# Only most-recent node_output is compacted
# ---------------------------------------------------------------------------


def test_only_latest_output_event_compacted(db, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    add_node_output_event(store, task.task_id, node.node_id, {"run": 1})
    add_node_output_event(store, task.task_id, node.node_id, {"run": 2})

    result = compact_node_output(db, task.task_id, node.node_id)

    assert result.compacted is True
    events = store.list_events(task.task_id)
    output_events = [e for e in events if e["event_type"] == "node_output"]
    payloads = [e["payload"] for e in output_events]
    # First event unchanged, second replaced
    assert payloads[0] == {"run": 1}
    assert payloads[1] == {"compacted": True}
