"""Tests for the research planner module."""

from __future__ import annotations

import json
import sqlite3

import pytest

from openbad.tasks.research_planner import (
    build_plan_prompt,
    build_synthesis_description,
    enqueue_plan,
    parse_plan,
    should_plan,
)
from openbad.tasks.research_queue import (
    ResearchNode,
    ResearchQueue,
    initialize_research_db,
)
from openbad.tasks.research_service import ResearchService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    initialize_research_db(c)
    return c


@pytest.fixture()
def queue(conn):
    return ResearchQueue(conn)


@pytest.fixture()
def svc(conn):
    return ResearchService(conn)


def _make_node(
    *,
    title: str = "Test topic",
    description: str = "Desc",
    parent_node_id: str | None = None,
) -> ResearchNode:
    from datetime import UTC, datetime

    return ResearchNode(
        node_id="test-node-1",
        title=title,
        priority=0,
        enqueued_at=datetime.now(tz=UTC),
        description=description,
        parent_node_id=parent_node_id,
    )


# ---------------------------------------------------------------------------
# parse_plan
# ---------------------------------------------------------------------------


class TestParsePlan:
    def test_valid_json_array(self):
        raw = json.dumps([
            {"title": "Q1", "description": "Desc 1"},
            {"title": "Q2", "description": "Desc 2"},
            {"title": "Q3", "description": "Desc 3"},
        ])
        result = parse_plan(raw)
        assert len(result) == 3
        assert result[0]["title"] == "Q1"

    def test_markdown_fences(self):
        raw = (
            '```json\n'
            '[{"title": "Q1", "description": "D1"}, '
            '{"title": "Q2", "description": "D2"}]\n```'
        )
        result = parse_plan(raw)
        assert len(result) == 2

    def test_too_few_subtasks(self):
        raw = json.dumps([{"title": "Q1", "description": "D1"}])
        result = parse_plan(raw)
        assert result == []

    def test_max_subtasks_enforced(self):
        items = [{"title": f"Q{i}", "description": f"D{i}"} for i in range(10)]
        raw = json.dumps(items)
        result = parse_plan(raw)
        assert len(result) <= 6

    def test_invalid_json(self):
        result = parse_plan("this is not json")
        assert result == []

    def test_non_array(self):
        result = parse_plan('{"title": "single"}')
        assert result == []

    def test_missing_title_skipped(self):
        raw = json.dumps([
            {"title": "Q1", "description": "D1"},
            {"description": "no title"},
            {"title": "Q3", "description": "D3"},
        ])
        result = parse_plan(raw)
        assert len(result) == 2
        assert result[0]["title"] == "Q1"
        assert result[1]["title"] == "Q3"


# ---------------------------------------------------------------------------
# should_plan
# ---------------------------------------------------------------------------


class TestShouldPlan:
    def test_top_level_node(self):
        node = _make_node()
        assert should_plan(node) is True

    def test_child_node(self):
        node = _make_node(parent_node_id="parent-123")
        assert should_plan(node) is False


# ---------------------------------------------------------------------------
# build_plan_prompt
# ---------------------------------------------------------------------------


class TestBuildPlanPrompt:
    def test_contains_title_and_description(self):
        node = _make_node(title="Semantic Graph", description="How to use it")
        system, user = build_plan_prompt(node)
        assert "JSON" in system
        assert "Semantic Graph" in user
        assert "How to use it" in user


# ---------------------------------------------------------------------------
# enqueue_plan
# ---------------------------------------------------------------------------


class TestEnqueuePlan:
    def test_creates_child_nodes(self, svc):
        parent = svc.enqueue("Parent Topic", description="Full desc")
        subtasks = [
            {"title": "Sub 1", "description": "Desc 1"},
            {"title": "Sub 2", "description": "Desc 2"},
        ]
        children = enqueue_plan(svc, parent, subtasks)
        assert len(children) == 2
        for child in children:
            assert child.parent_node_id == parent.node_id
            assert child.priority == parent.priority

    def test_children_appear_in_queue(self, svc):
        parent = svc.enqueue("Parent")
        subtasks = [
            {"title": "Sub A", "description": "DA"},
            {"title": "Sub B", "description": "DB"},
        ]
        enqueue_plan(svc, parent, subtasks)
        pending = svc.list_pending()
        # Parent + 2 children.
        titles = {n.title for n in pending}
        assert "Sub A" in titles
        assert "Sub B" in titles


# ---------------------------------------------------------------------------
# parent_node_id in queue
# ---------------------------------------------------------------------------


class TestParentNodeId:
    def test_enqueue_with_parent(self, queue):
        parent = queue.enqueue("Parent")
        child = queue.enqueue("Child", parent_node_id=parent.node_id)
        assert child.parent_node_id == parent.node_id
        fetched = queue.get(child.node_id)
        assert fetched is not None
        assert fetched.parent_node_id == parent.node_id

    def test_list_children(self, queue):
        parent = queue.enqueue("Parent")
        queue.enqueue("Child 1", parent_node_id=parent.node_id)
        queue.enqueue("Child 2", parent_node_id=parent.node_id)
        queue.enqueue("Unrelated")
        children = queue.list_children(parent.node_id)
        assert len(children) == 2

    def test_list_pending_children(self, queue):
        parent = queue.enqueue("Parent")
        c1 = queue.enqueue("Child 1", parent_node_id=parent.node_id)
        queue.enqueue("Child 2", parent_node_id=parent.node_id)
        queue.complete(c1.node_id)
        pending = queue.list_pending_children(parent.node_id)
        assert len(pending) == 1
        assert pending[0].title == "Child 2"

    def test_is_child_property(self, queue):
        parent = queue.enqueue("Parent")
        child = queue.enqueue("Child", parent_node_id=parent.node_id)
        assert not parent.is_child
        assert child.is_child

    def test_to_dict_includes_parent(self, queue):
        parent = queue.enqueue("Parent")
        child = queue.enqueue("Child", parent_node_id=parent.node_id)
        d = child.to_dict()
        assert d["parent_node_id"] == parent.node_id


# ---------------------------------------------------------------------------
# build_synthesis_description
# ---------------------------------------------------------------------------


class TestBuildSynthesisDescription:
    def test_includes_parent_and_child_findings(self):
        parent = _make_node(title="Big Topic", description="Broad description")
        child1 = _make_node(title="Sub 1")
        child1.node_id = "c1"
        child2 = _make_node(title="Sub 2")
        child2.node_id = "c2"
        summaries = {
            "c1": "Finding about sub 1",
            "c2": "Finding about sub 2",
        }
        desc = build_synthesis_description(parent, [child1, child2], summaries)
        assert "Big Topic" in desc
        assert "Finding about sub 1" in desc
        assert "Finding about sub 2" in desc
        assert "Sub 1" in desc
        assert "Sub 2" in desc
