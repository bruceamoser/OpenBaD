from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openbad.tasks.research_queue import (
    ResearchNode,
    ResearchNodeStatus,
    ResearchQueue,
    initialize_research_db,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "research.db")
    initialize_research_db(conn)
    return conn


@pytest.fixture()
def queue(db: sqlite3.Connection) -> ResearchQueue:
    return ResearchQueue(db)


# ---------------------------------------------------------------------------
# Enqueue / basics
# ---------------------------------------------------------------------------


def test_enqueue_returns_node(queue: ResearchQueue) -> None:
    node = queue.enqueue("Study X", priority=0)
    assert isinstance(node, ResearchNode)
    assert node.title == "Study X"
    assert node.node_id


def test_enqueue_with_explicit_id(queue: ResearchQueue) -> None:
    node = queue.enqueue("Study Y", node_id="explicit-1")
    assert node.node_id == "explicit-1"


def test_enqueue_with_source_task(queue: ResearchQueue) -> None:
    node = queue.enqueue("Study Z", source_task_id="task-abc")
    assert node.source_task_id == "task-abc"


def test_enqueued_node_is_pending(queue: ResearchQueue) -> None:
    node = queue.enqueue("Study A")
    assert node.status == ResearchNodeStatus.PENDING


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


def test_dequeue_highest_priority_first(queue: ResearchQueue) -> None:
    queue.enqueue("Low urgency", priority=10)
    queue.enqueue("High urgency", priority=-5)
    queue.enqueue("Normal", priority=0)

    first = queue.dequeue()
    assert first is not None
    assert first.title == "High urgency"


def test_dequeue_fifo_within_same_priority(queue: ResearchQueue) -> None:
    queue.enqueue("First", priority=0, node_id="n1")
    queue.enqueue("Second", priority=0, node_id="n2")

    first = queue.dequeue()
    assert first is not None
    assert first.node_id == "n1"


def test_list_pending_in_priority_order(queue: ResearchQueue) -> None:
    queue.enqueue("C", priority=5)
    queue.enqueue("A", priority=-1)
    queue.enqueue("B", priority=2)

    pending = queue.list_pending()
    priorities = [n.priority for n in pending]
    assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_node_persists_and_reloads(queue: ResearchQueue) -> None:
    node = queue.enqueue("Persist me", priority=3, description="details", source_task_id="t1")
    loaded = queue.get(node.node_id)
    assert loaded is not None
    assert loaded.title == "Persist me"
    assert loaded.priority == 3
    assert loaded.description == "details"
    assert loaded.source_task_id == "t1"


def test_dequeued_node_status_persists(queue: ResearchQueue, db: sqlite3.Connection) -> None:
    node = queue.enqueue("To dequeue")
    queue.dequeue()

    queue2 = ResearchQueue(db)
    loaded = queue2.get(node.node_id)
    assert loaded is not None
    assert loaded.status == ResearchNodeStatus.DEQUEUED


def test_get_unknown_node_returns_none(queue: ResearchQueue) -> None:
    assert queue.get("not-there") is None


def test_dequeue_from_empty_returns_none(queue: ResearchQueue) -> None:
    assert queue.dequeue() is None


# ---------------------------------------------------------------------------
# Peek
# ---------------------------------------------------------------------------


def test_peek_does_not_remove(queue: ResearchQueue) -> None:
    queue.enqueue("Peek me", priority=0)
    queue.peek()
    assert len(queue.list_pending()) == 1


def test_peek_empty_returns_none(queue: ResearchQueue) -> None:
    assert queue.peek() is None


def test_node_to_dict(queue: ResearchQueue) -> None:
    node = queue.enqueue("Dict check", priority=1)
    d = node.to_dict()
    assert d["title"] == "Dict check"
    assert d["priority"] == 1
    assert d["status"] == "pending"
