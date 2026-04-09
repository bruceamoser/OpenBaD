"""Tests for MCTS reasoning strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.cognitive.model_router import Priority
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStrategy
from openbad.cognitive.reasoning.mcts import (
    MCTSConfig,
    MCTSNode,
    MCTSReasoning,
    _parse_actions,
    _parse_value,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _mock_router(responses: list[str]) -> MagicMock:
    """Build a mock router cycling through canned responses."""
    adapter = MagicMock()
    call_idx = {"i": 0}

    async def _complete(prompt: str, model: str = "") -> MagicMock:
        idx = call_idx["i"] % len(responses)
        call_idx["i"] += 1
        result = MagicMock()
        result.content = responses[idx]
        result.tokens_used = 5
        return result

    adapter.complete = _complete

    router = MagicMock()
    decision = MagicMock()
    decision.provider = "mock"
    router.route = AsyncMock(return_value=(adapter, "test-model", decision))
    router.record_latency = MagicMock()
    return router


# ------------------------------------------------------------------ #
# Parsing
# ------------------------------------------------------------------ #


class TestParsing:
    def test_parse_actions(self) -> None:
        text = "ACTION 1: Try A\nACTION 2: Try B\nACTION 3: Try C\n"
        result = _parse_actions(text, 3)
        assert len(result) == 3
        assert result[0] == "Try A"

    def test_parse_actions_fallback(self) -> None:
        text = "line one\nline two"
        result = _parse_actions(text, 2)
        assert len(result) == 2

    def test_parse_value(self) -> None:
        assert _parse_value("VALUE: 0.75") == 0.75

    def test_parse_value_clamped(self) -> None:
        assert _parse_value("VALUE: 1.5") == 1.0

    def test_parse_value_default(self) -> None:
        assert _parse_value("nothing here") == 0.5


# ------------------------------------------------------------------ #
# MCTSNode
# ------------------------------------------------------------------ #


class TestMCTSNode:
    def test_defaults(self) -> None:
        node = MCTSNode(node_id="a", state="s")
        assert node.visits == 0
        assert node.value == 0.0
        assert node.children == []
        assert node.parent is None
        assert node.depth == 0

    def test_parent_linkage(self) -> None:
        root = MCTSNode(node_id="r", state="root")
        child = MCTSNode(node_id="c", state="child", parent=root, depth=1)
        root.children.append(child)
        assert child.parent is root


# ------------------------------------------------------------------ #
# UCB1 selection
# ------------------------------------------------------------------ #


class TestUCB1:
    def test_unexplored_picked_first(self) -> None:
        mcts = MCTSReasoning()
        root = MCTSNode(node_id="r", state="root", visits=10)
        visited = MCTSNode(
            node_id="a", state="a", visits=5, value=3.0, parent=root,
        )
        unvisited = MCTSNode(
            node_id="b", state="b", visits=0, value=0.0, parent=root,
        )
        root.children = [visited, unvisited]
        selected = mcts._ucb1_child(root)
        assert selected is unvisited

    def test_balances_explore_exploit(self) -> None:
        mcts = MCTSReasoning()
        root = MCTSNode(node_id="r", state="root", visits=20)
        high_value = MCTSNode(
            node_id="a", state="a", visits=10, value=8.0, parent=root,
        )
        low_visits = MCTSNode(
            node_id="b", state="b", visits=2, value=1.5, parent=root,
        )
        root.children = [high_value, low_visits]
        selected = mcts._ucb1_child(root)
        # With c=1.414, low_visits should get exploration bonus
        assert selected is not None


# ------------------------------------------------------------------ #
# Backpropagation
# ------------------------------------------------------------------ #


class TestBackpropagation:
    def test_updates_to_root(self) -> None:
        root = MCTSNode(node_id="r", state="root")
        child = MCTSNode(node_id="c", state="child", parent=root, depth=1)
        root.children.append(child)

        MCTSReasoning._backpropagate(child, 0.8)
        assert child.visits == 1
        assert child.value == pytest.approx(0.8)
        assert root.visits == 1
        assert root.value == pytest.approx(0.8)

    def test_multiple_backprops(self) -> None:
        root = MCTSNode(node_id="r", state="root")
        child = MCTSNode(node_id="c", state="child", parent=root, depth=1)
        root.children.append(child)

        MCTSReasoning._backpropagate(child, 0.5)
        MCTSReasoning._backpropagate(child, 0.9)
        assert root.visits == 2
        assert root.value == pytest.approx(1.4)


# ------------------------------------------------------------------ #
# Full reasoning
# ------------------------------------------------------------------ #


class TestReason:
    @pytest.mark.asyncio
    async def test_basic_reasoning(self) -> None:
        config = MCTSConfig(max_iterations=3, expansion_count=2, max_depth=2)
        responses = [
            # expand
            "ACTION 1: idea A\nACTION 2: idea B\n",
            # rollout
            "VALUE: 0.8",
            # expand
            "ACTION 1: nested1\nACTION 2: nested2\n",
            # rollout
            "VALUE: 0.7",
            # expand (already at depth, or re-expand)
            "ACTION 1: deep\nACTION 2: deeper\n",
            # rollout
            "VALUE: 0.6",
            # synth
            "The answer is 42.",
        ]
        router = _mock_router(responses)
        mcts = MCTSReasoning(config=config)
        result = await mcts.reason("What?", "ctx", router)

        assert isinstance(result, ReasoningResult)
        assert result.final_answer
        assert result.total_tokens > 0
        assert result.metadata["iterations"] == 3
        assert result.metadata["total_nodes"] >= 1

    @pytest.mark.asyncio
    async def test_custom_priority(self) -> None:
        config = MCTSConfig(
            priority=Priority.CRITICAL,
            max_iterations=1,
            expansion_count=1,
            max_depth=1,
        )
        responses = [
            "ACTION 1: single\n",
            "VALUE: 0.9",
            "Final.",
        ]
        router = _mock_router(responses)
        mcts = MCTSReasoning(config=config)
        await mcts.reason("p", "c", router)
        router.route.assert_called_with(Priority.CRITICAL)

    @pytest.mark.asyncio
    async def test_records_latency(self) -> None:
        config = MCTSConfig(max_iterations=1, expansion_count=1, max_depth=1)
        responses = ["ACTION 1: a\n", "VALUE: 0.5", "done"]
        router = _mock_router(responses)
        mcts = MCTSReasoning(config=config)
        await mcts.reason("p", "c", router)
        router.record_latency.assert_called_once()


# ------------------------------------------------------------------ #
# ABC conformance
# ------------------------------------------------------------------ #


class TestABCConformance:
    def test_is_reasoning_strategy(self) -> None:
        assert issubclass(MCTSReasoning, ReasoningStrategy)

    def test_instance(self) -> None:
        assert isinstance(MCTSReasoning(), ReasoningStrategy)


# ------------------------------------------------------------------ #
# Best path extraction
# ------------------------------------------------------------------ #


class TestBestPath:
    def test_follows_most_visited(self) -> None:
        root = MCTSNode(node_id="r", state="root", visits=10)
        a = MCTSNode(node_id="a", state="a", visits=7, parent=root)
        b = MCTSNode(node_id="b", state="b", visits=3, parent=root)
        root.children = [a, b]

        a1 = MCTSNode(node_id="a1", state="a1", visits=5, parent=a)
        a2 = MCTSNode(node_id="a2", state="a2", visits=2, parent=a)
        a.children = [a1, a2]

        path = MCTSReasoning._best_path(root)
        assert len(path) == 2
        assert path[0] is a
        assert path[1] is a1

    def test_empty_when_no_children(self) -> None:
        root = MCTSNode(node_id="r", state="root")
        path = MCTSReasoning._best_path(root)
        assert path == []
