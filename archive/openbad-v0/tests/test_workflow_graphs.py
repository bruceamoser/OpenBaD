"""Tests for openbad.frameworks.workflows — task workflow graphs."""

from __future__ import annotations

import pytest

from openbad.frameworks.workflows.nodes import (
    _make_node,
    should_retry,
)
from openbad.frameworks.workflows.registry import get_workflow
from openbad.frameworks.workflows.research_workflow import build_research_graph
from openbad.frameworks.workflows.scheduled_workflow import build_scheduled_graph
from openbad.frameworks.workflows.state import AgentState
from openbad.frameworks.workflows.system_workflow import build_system_graph
from openbad.frameworks.workflows.user_request_workflow import (
    build_user_request_graph,
)

# ── Helpers ───────────────────────────────────────────────────────────── #


def _initial_state(**overrides) -> AgentState:
    base: AgentState = {
        "messages": [],
        "context": "",
        "memory_refs": [],
        "task_metadata": {"task_id": "test-1"},
        "results": [],
        "retry_counts": {},
        "error": "",
        "status": "running",
    }
    base.update(overrides)
    return base


# ── AgentState ────────────────────────────────────────────────────────── #


class TestAgentState:
    def test_all_fields_present(self) -> None:
        state = _initial_state()
        assert "messages" in state
        assert "task_metadata" in state
        assert "retry_counts" in state
        assert "results" in state


# ── Node functions ────────────────────────────────────────────────────── #


class TestMakeNode:
    def test_appends_result(self) -> None:
        node = _make_node("execute")
        out = node(_initial_state())
        assert len(out["results"]) == 1
        assert out["results"][0]["node"] == "execute"
        assert out["results"][0]["status"] == "done"

    def test_preserves_existing_results(self) -> None:
        node = _make_node("plan")
        state = _initial_state(
            results=[{"node": "clarify", "status": "done"}],
        )
        out = node(state)
        assert len(out["results"]) == 2


# ── Retry logic ──────────────────────────────────────────────────────── #


class TestShouldRetry:
    def test_continue_when_no_error(self) -> None:
        decide = should_retry("execute")
        state = _initial_state()
        assert decide(state) == "continue"

    def test_retry_on_error_within_limit(self) -> None:
        decide = should_retry("execute", max_retries=2)
        state = _initial_state(error="something failed", retry_counts={"execute": 0})
        assert decide(state) == "retry"

    def test_continue_when_retries_exhausted(self) -> None:
        decide = should_retry("execute", max_retries=2)
        state = _initial_state(error="still failing", retry_counts={"execute": 2})
        assert decide(state) == "continue"

    def test_uses_default_max_retries(self) -> None:
        decide = should_retry("gather")
        state = _initial_state(error="net error", retry_counts={"gather": 1})
        # DEFAULT_MAX_RETRIES["gather"] == 2, so retry should still be possible.
        assert decide(state) == "retry"

    def test_zero_max_retries_never_retries(self) -> None:
        decide = should_retry("clarify")
        state = _initial_state(error="bad input", retry_counts={"clarify": 0})
        assert decide(state) == "continue"


# ── Graph compilation ────────────────────────────────────────────────── #


class TestUserRequestGraph:
    def test_compiles(self) -> None:
        graph = build_user_request_graph()
        compiled = graph.compile()
        assert compiled is not None

    def test_has_expected_nodes(self) -> None:
        graph = build_user_request_graph()
        node_names = set(graph.nodes.keys())
        assert {"clarify", "plan", "execute", "review"} <= node_names

    def test_invoke_traverses_all_nodes(self) -> None:
        compiled = build_user_request_graph().compile()
        result = compiled.invoke(_initial_state())
        node_names = [r["node"] for r in result["results"]]
        assert node_names == ["clarify", "plan", "execute", "review"]


class TestResearchGraph:
    def test_compiles(self) -> None:
        graph = build_research_graph()
        compiled = graph.compile()
        assert compiled is not None

    def test_has_expected_nodes(self) -> None:
        graph = build_research_graph()
        node_names = set(graph.nodes.keys())
        assert {"gather", "analyse", "summarise"} <= node_names

    def test_invoke_traverses_all_nodes(self) -> None:
        compiled = build_research_graph().compile()
        result = compiled.invoke(_initial_state())
        node_names = [r["node"] for r in result["results"]]
        assert node_names == ["gather", "analyse", "summarise"]


class TestSystemGraph:
    def test_compiles(self) -> None:
        graph = build_system_graph()
        compiled = graph.compile()
        assert compiled is not None

    def test_invoke_single_execute(self) -> None:
        compiled = build_system_graph().compile()
        result = compiled.invoke(_initial_state())
        node_names = [r["node"] for r in result["results"]]
        assert node_names == ["execute"]


class TestScheduledGraph:
    def test_compiles(self) -> None:
        graph = build_scheduled_graph()
        compiled = graph.compile()
        assert compiled is not None

    def test_invoke_single_execute(self) -> None:
        compiled = build_scheduled_graph().compile()
        result = compiled.invoke(_initial_state())
        node_names = [r["node"] for r in result["results"]]
        assert node_names == ["execute"]


# ── Registry ─────────────────────────────────────────────────────────── #


class TestRegistry:
    def test_get_user_requested(self) -> None:
        wf = get_workflow("user_requested")
        assert wf is not None

    def test_get_research(self) -> None:
        wf = get_workflow("research")
        assert wf is not None

    def test_get_system(self) -> None:
        wf = get_workflow("system")
        assert wf is not None

    def test_get_scheduled(self) -> None:
        wf = get_workflow("scheduled")
        assert wf is not None

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown task kind"):
            get_workflow("nonexistent")

    def test_invoke_via_registry(self) -> None:
        wf = get_workflow("research")
        result = wf.invoke(_initial_state())
        assert len(result["results"]) == 3


# ── State transitions ────────────────────────────────────────────────── #


class TestStateTransitions:
    def test_results_accumulate(self) -> None:
        wf = get_workflow("user_requested")
        result = wf.invoke(_initial_state())
        assert len(result["results"]) == 4

    def test_task_metadata_preserved(self) -> None:
        wf = get_workflow("user_requested")
        state = _initial_state(
            task_metadata={"task_id": "t-42", "kind": "user_requested"},
        )
        result = wf.invoke(state)
        assert result["task_metadata"]["task_id"] == "t-42"

    def test_context_preserved(self) -> None:
        wf = get_workflow("research")
        state = _initial_state(context="background info")
        result = wf.invoke(state)
        assert result["context"] == "background info"
