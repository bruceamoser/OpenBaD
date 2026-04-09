"""Tests for Tree-of-Thoughts reasoning strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.cognitive.model_router import Priority
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStrategy
from openbad.cognitive.reasoning.tree_of_thoughts import (
    ThoughtNode,
    ThoughtTree,
    TreeOfThoughts,
    TreeOfThoughtsConfig,
    _parse_candidates,
    _parse_score,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _mock_router(responses: list[str]) -> MagicMock:
    """Build a mock router that returns an adapter cycling through responses."""
    adapter = MagicMock()
    call_idx = {"i": 0}

    async def _complete(prompt: str, model: str = "") -> MagicMock:
        idx = call_idx["i"] % len(responses)
        call_idx["i"] += 1
        result = MagicMock()
        result.content = responses[idx]
        result.tokens_used = 10
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
    def test_parse_candidates(self) -> None:
        text = (
            "CANDIDATE 1: First idea\n"
            "CANDIDATE 2: Second idea\n"
            "CANDIDATE 3: Third idea\n"
        )
        result = _parse_candidates(text, 3)
        assert len(result) == 3
        assert result[0] == "First idea"
        assert result[2] == "Third idea"

    def test_parse_candidates_fallback(self) -> None:
        text = "Line one\nLine two\nLine three"
        result = _parse_candidates(text, 2)
        assert len(result) == 2

    def test_parse_score(self) -> None:
        assert _parse_score("SCORE: 0.85") == 0.85

    def test_parse_score_clamped(self) -> None:
        assert _parse_score("SCORE: 1.5") == 1.0
        assert _parse_score("SCORE: 0.0") == 0.0

    def test_parse_score_default(self) -> None:
        assert _parse_score("no score here") == 0.5


# ------------------------------------------------------------------ #
# ThoughtNode / ThoughtTree
# ------------------------------------------------------------------ #


class TestDataTypes:
    def test_thought_node_defaults(self) -> None:
        node = ThoughtNode(node_id="a", thought="test")
        assert node.score == 0.0
        assert node.depth == 0
        assert node.parent_id is None
        assert node.children == []

    def test_thought_tree_defaults(self) -> None:
        tree = ThoughtTree()
        assert tree.root_nodes == []
        assert tree.best_path == []
        assert tree.total_nodes_explored == 0


# ------------------------------------------------------------------ #
# Reasoning — basic
# ------------------------------------------------------------------ #


class TestReason:
    @pytest.mark.asyncio
    async def test_basic_reasoning(self) -> None:
        responses = [
            # Generate root candidates
            "CANDIDATE 1: idea A\nCANDIDATE 2: idea B\nCANDIDATE 3: idea C\n",
            # Evaluate each root
            "SCORE: 0.8",
            "SCORE: 0.5",
            "SCORE: 0.9",
            # Generate children of surviving roots (depth 1)
            "CANDIDATE 1: A-child1\nCANDIDATE 2: A-child2\nCANDIDATE 3: A-child3\n",
            "SCORE: 0.7",
            "SCORE: 0.6",
            "SCORE: 0.4",
            "CANDIDATE 1: B-child1\nCANDIDATE 2: B-child2\nCANDIDATE 3: B-child3\n",
            "SCORE: 0.5",
            "SCORE: 0.3",
            "SCORE: 0.2",
            "CANDIDATE 1: C-child1\nCANDIDATE 2: C-child2\nCANDIDATE 3: C-child3\n",
            "SCORE: 0.95",
            "SCORE: 0.4",
            "SCORE: 0.3",
            # Depth 2 — continues for surviving children
            "CANDIDATE 1: deep1\nCANDIDATE 2: deep2\nCANDIDATE 3: deep3\n",
            "SCORE: 0.85",
            "SCORE: 0.6",
            "SCORE: 0.5",
            "CANDIDATE 1: deep1\nCANDIDATE 2: deep2\nCANDIDATE 3: deep3\n",
            "SCORE: 0.7",
            "SCORE: 0.6",
            "SCORE: 0.5",
            "CANDIDATE 1: deep1\nCANDIDATE 2: deep2\nCANDIDATE 3: deep3\n",
            "SCORE: 0.7",
            "SCORE: 0.6",
            "SCORE: 0.5",
            "CANDIDATE 1: deep1\nCANDIDATE 2: deep2\nCANDIDATE 3: deep3\n",
            "SCORE: 0.7",
            "SCORE: 0.6",
            "SCORE: 0.5",
            "CANDIDATE 1: deep1\nCANDIDATE 2: deep2\nCANDIDATE 3: deep3\n",
            "SCORE: 0.7",
            "SCORE: 0.6",
            "SCORE: 0.5",
            # Synthesize
            "The answer is 42.",
        ]
        router = _mock_router(responses)
        tot = TreeOfThoughts()
        result = await tot.reason("What is the answer?", "Some context", router)

        assert isinstance(result, ReasoningResult)
        assert result.final_answer
        assert result.total_tokens > 0
        assert result.total_latency_ms >= 0
        assert "tree_nodes_explored" in result.metadata

    @pytest.mark.asyncio
    async def test_pruning_occurs(self) -> None:
        """All candidates score below threshold → everything pruned."""
        responses = [
            "CANDIDATE 1: bad1\nCANDIDATE 2: bad2\nCANDIDATE 3: bad3\n",
            "SCORE: 0.1",
            "SCORE: 0.05",
            "SCORE: 0.2",
            # Synthesize (best leaf from roots if any, else fallback)
            "No good answer found.",
        ]
        router = _mock_router(responses)
        tot = TreeOfThoughts()
        result = await tot.reason("test", "ctx", router)
        assert isinstance(result, ReasoningResult)
        assert result.metadata["tree_nodes_pruned"] > 0

    @pytest.mark.asyncio
    async def test_custom_config(self) -> None:
        config = TreeOfThoughtsConfig(
            priority=Priority.CRITICAL,
            branching_factor=2,
            max_depth=1,
            prune_threshold=0.5,
        )
        responses = [
            "CANDIDATE 1: idea A\nCANDIDATE 2: idea B\n",
            "SCORE: 0.9",
            "SCORE: 0.8",
            "Final answer here.",
        ]
        router = _mock_router(responses)
        tot = TreeOfThoughts(config=config)
        result = await tot.reason("problem", "ctx", router)

        assert result.final_answer
        assert result.metadata["branching_factor"] == 2
        assert result.metadata["max_depth"] == 1
        router.route.assert_called_with(Priority.CRITICAL)


# ------------------------------------------------------------------ #
# ABC conformance
# ------------------------------------------------------------------ #


class TestABCConformance:
    def test_is_reasoning_strategy(self) -> None:
        assert issubclass(TreeOfThoughts, ReasoningStrategy)

    def test_instance(self) -> None:
        tot = TreeOfThoughts()
        assert isinstance(tot, ReasoningStrategy)


# ------------------------------------------------------------------ #
# Latency recording
# ------------------------------------------------------------------ #


class TestLatencyRecording:
    @pytest.mark.asyncio
    async def test_records_latency(self) -> None:
        responses = [
            "CANDIDATE 1: a\nCANDIDATE 2: b\nCANDIDATE 3: c\n",
            "SCORE: 0.1",
            "SCORE: 0.1",
            "SCORE: 0.1",
            "done",
        ]
        router = _mock_router(responses)
        tot = TreeOfThoughts()
        await tot.reason("p", "c", router)
        router.record_latency.assert_called_once()
        args = router.record_latency.call_args[0]
        assert args[0] == "mock"
        assert args[1] >= 0
