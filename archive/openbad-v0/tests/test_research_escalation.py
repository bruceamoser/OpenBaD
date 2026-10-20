from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openbad.tasks.research_escalation import (
    EscalationRecord,
    ResearchEscalation,
    initialize_escalation_db,
)
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "escalation.db")
    initialize_research_db(conn)
    initialize_escalation_db(conn)
    return conn


@pytest.fixture()
def queue(db: sqlite3.Connection) -> ResearchQueue:
    return ResearchQueue(db)


@pytest.fixture()
def escalation(db: sqlite3.Connection, queue: ResearchQueue) -> ResearchEscalation:
    return ResearchEscalation(db, queue)


# ---------------------------------------------------------------------------
# Escalation trigger
# ---------------------------------------------------------------------------


def test_trigger_returns_escalation_record(escalation: ResearchEscalation) -> None:
    rec = escalation.trigger("task-1", "node-a")

    assert isinstance(rec, EscalationRecord)
    assert rec.task_id == "task-1"
    assert rec.node_id == "node-a"
    assert rec.research_node_id
    assert rec.escalation_id


def test_trigger_enqueues_research_node(
    escalation: ResearchEscalation, queue: ResearchQueue
) -> None:
    rec = escalation.trigger("task-1", "node-a")
    assert rec is not None

    node = queue.get(rec.research_node_id)
    assert node is not None
    assert node.source_task_id == "task-1"


def test_trigger_uses_escalation_priority(
    escalation: ResearchEscalation, queue: ResearchQueue
) -> None:
    rec = escalation.trigger("task-1", "node-a")
    assert rec is not None

    node = queue.get(rec.research_node_id)
    assert node is not None
    assert node.priority == -10  # default escalation priority


def test_trigger_custom_title(escalation: ResearchEscalation, queue: ResearchQueue) -> None:
    rec = escalation.trigger("task-2", "node-b", research_title="Custom research title")
    assert rec is not None

    node = queue.get(rec.research_node_id)
    assert node is not None
    assert node.title == "Custom research title"


def test_trigger_default_title_includes_node_id(
    escalation: ResearchEscalation, queue: ResearchQueue
) -> None:
    rec = escalation.trigger("task-3", "node-xyz")
    assert rec is not None

    node = queue.get(rec.research_node_id)
    assert node is not None
    assert "node-xyz" in node.title


# ---------------------------------------------------------------------------
# Duplicate suppression
# ---------------------------------------------------------------------------


def test_duplicate_trigger_returns_none(escalation: ResearchEscalation) -> None:
    escalation.trigger("task-1", "node-a")
    result = escalation.trigger("task-1", "node-a")

    assert result is None


def test_duplicate_does_not_create_second_research_node(
    escalation: ResearchEscalation, queue: ResearchQueue
) -> None:
    escalation.trigger("task-1", "node-a")
    escalation.trigger("task-1", "node-a")

    pending = queue.list_pending()
    assert len(pending) == 1


def test_different_nodes_are_not_duplicates(escalation: ResearchEscalation) -> None:
    r1 = escalation.trigger("task-1", "node-a")
    r2 = escalation.trigger("task-1", "node-b")

    assert r1 is not None
    assert r2 is not None
    assert r1.research_node_id != r2.research_node_id


# ---------------------------------------------------------------------------
# Source linkage queryability
# ---------------------------------------------------------------------------


def test_get_escalation_returns_record(escalation: ResearchEscalation) -> None:
    escalation.trigger("task-1", "node-a")
    rec = escalation.get_escalation("task-1", "node-a")

    assert rec is not None
    assert rec.task_id == "task-1"


def test_get_escalation_missing_returns_none(escalation: ResearchEscalation) -> None:
    assert escalation.get_escalation("no-task", "no-node") is None


def test_list_escalations_for_task(escalation: ResearchEscalation) -> None:
    escalation.trigger("task-1", "node-a")
    escalation.trigger("task-1", "node-b")
    escalation.trigger("task-2", "node-a")

    records = escalation.list_escalations("task-1")
    assert len(records) == 2
    assert all(r.task_id == "task-1" for r in records)


def test_research_node_for_returns_node(
    escalation: ResearchEscalation, queue: ResearchQueue
) -> None:
    escalation.trigger("task-1", "node-a")
    node = escalation.research_node_for("task-1", "node-a")

    assert node is not None
    assert node.source_task_id == "task-1"
