"""Tests for ResearchService singleton and delegation."""

from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.research_queue import initialize_research_db
from openbad.tasks.research_service import ResearchService


@pytest.fixture()
def svc(tmp_path: Path) -> ResearchService:
    conn = initialize_state_db(tmp_path / "state.db")
    initialize_research_db(conn)
    return ResearchService(conn)


def test_enqueue_and_list_pending(svc: ResearchService) -> None:
    node = svc.enqueue("Research topic A", priority=0)
    pending = svc.list_pending()
    assert len(pending) == 1
    assert pending[0].node_id == node.node_id


def test_dequeue_returns_highest_priority(svc: ResearchService) -> None:
    svc.enqueue("Low prio", priority=5)
    svc.enqueue("High prio", priority=-1)
    node = svc.dequeue()
    assert node is not None
    assert node.title == "High prio"


def test_peek_does_not_consume(svc: ResearchService) -> None:
    svc.enqueue("Peekable")
    peeked = svc.peek()
    assert peeked is not None
    assert len(svc.list_pending()) == 1


def test_get_by_id(svc: ResearchService) -> None:
    node = svc.enqueue("Find me")
    fetched = svc.get(node.node_id)
    assert fetched is not None
    assert fetched.title == "Find me"


def test_complete_marks_dequeued(svc: ResearchService) -> None:
    node = svc.enqueue("To complete")
    svc.complete(node.node_id)
    completed = svc.list_completed()
    assert len(completed) == 1
    assert completed[0].node_id == node.node_id


def test_update_pending_node(svc: ResearchService) -> None:
    node = svc.enqueue("Old title", priority=5)
    updated = svc.update(node.node_id, title="New title", priority=1)
    assert updated is not None
    assert updated.title == "New title"
    assert updated.priority == 1


def test_enqueue_or_append_pending_deduplicates(svc: ResearchService) -> None:
    svc.enqueue("Anomaly detected")
    svc.enqueue_or_append_pending("Anomaly detected", observation="second occurrence")
    pending = svc.list_pending()
    assert len(pending) == 1
    assert "second occurrence" in pending[0].description


def test_singleton_lifecycle(tmp_path: Path) -> None:
    ResearchService.reset_instance()
    try:
        svc = ResearchService.get_instance(tmp_path / "singleton.db")
        assert svc is ResearchService.get_instance()
    finally:
        ResearchService.reset_instance()
