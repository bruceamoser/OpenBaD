"""Tests for openbad.frameworks.workflows.reasoning strategies."""

from __future__ import annotations

import pytest

from openbad.frameworks.workflows.reasoning import (
    ChainOfThoughtGraph,
    MCTSGraph,
    ReasoningState,
    TreeOfThoughtsGraph,
)
from openbad.frameworks.workflows.reasoning.chain_of_thought import (
    conclude,
    decompose,
    step_through,
)
from openbad.frameworks.workflows.reasoning.mcts import (
    backpropagate,
    expand,
    finalize,
    select,
    simulate,
)
from openbad.frameworks.workflows.reasoning.tree_of_thoughts import (
    evaluate_branches,
    generate_branches,
    select_best,
)

# ── Helpers ───────────────────────────────────────────────────────────── #


def _state(**overrides) -> ReasoningState:
    base: ReasoningState = {
        "prompt": "What is 2 + 2?",
        "context": "",
        "steps": [],
        "branches": [],
        "scores": [],
        "best_branch": 0,
        "final_answer": "",
        "total_tokens": 0,
        "iteration": 0,
        "max_iterations": 3,
        "error": "",
    }
    base.update(overrides)
    return base


# ── Chain of Thought ─────────────────────────────────────────────────── #


class TestChainOfThought:
    def test_decompose(self) -> None:
        result = decompose(_state())
        assert len(result["steps"]) == 1
        assert "Decompose" in result["steps"][0]["thought"]

    def test_step_through(self) -> None:
        result = step_through(_state(steps=[{"step": 1, "thought": "x", "conclusion": "y"}]))
        assert len(result["steps"]) == 2

    def test_conclude(self) -> None:
        result = conclude(
            _state(
                steps=[
                    {"conclusion": "step 1 done"},
                    {"conclusion": "step 2 done"},
                ],
            ),
        )
        assert "step 1 done" in result["final_answer"]
        assert "step 2 done" in result["final_answer"]

    def test_full_graph(self) -> None:
        cot = ChainOfThoughtGraph()
        result = cot.reason(_state())
        assert result["final_answer"] != ""
        assert len(result["steps"]) >= 2

    def test_compiled_property(self) -> None:
        cot = ChainOfThoughtGraph()
        assert cot.compiled is not None


# ── Tree of Thoughts ─────────────────────────────────────────────────── #


class TestTreeOfThoughts:
    def test_generate_branches(self) -> None:
        result = generate_branches(_state())
        assert len(result["branches"]) == 3

    def test_evaluate_branches(self) -> None:
        branches = [{"branch_id": i} for i in range(3)]
        result = evaluate_branches(_state(branches=branches))
        assert len(result["scores"]) == 3

    def test_select_best(self) -> None:
        branches = [
            {"branch_id": 0, "thought": "A"},
            {"branch_id": 1, "thought": "B"},
            {"branch_id": 2, "thought": "C"},
        ]
        result = select_best(_state(branches=branches, scores=[0.3, 0.9, 0.5]))
        assert result["best_branch"] == 1
        assert result["final_answer"] == "B"

    def test_select_best_empty(self) -> None:
        result = select_best(_state())
        assert result["final_answer"] == "No branches to select."

    def test_full_graph(self) -> None:
        tot = TreeOfThoughtsGraph()
        result = tot.reason(_state())
        assert result["final_answer"] != ""
        assert len(result["branches"]) == 3
        assert len(result["scores"]) == 3

    def test_compiled_property(self) -> None:
        tot = TreeOfThoughtsGraph()
        assert tot.compiled is not None


# ── MCTS ─────────────────────────────────────────────────────────────── #


class TestMCTS:
    def test_select_no_branches(self) -> None:
        result = select(_state())
        assert result["best_branch"] == 0

    def test_expand(self) -> None:
        result = expand(_state(iteration=0))
        assert len(result["branches"]) == 1

    def test_simulate(self) -> None:
        result = simulate(_state(branches=[{"branch_id": 0}]))
        assert len(result["scores"]) == 1

    def test_backpropagate(self) -> None:
        result = backpropagate(_state(iteration=0))
        assert result["iteration"] == 1
        assert len(result["steps"]) == 1

    def test_finalize(self) -> None:
        branches = [
            {"thought": "A"},
            {"thought": "B"},
        ]
        result = finalize(_state(branches=branches, scores=[0.3, 0.8]))
        assert result["final_answer"] == "B"
        assert result["best_branch"] == 1

    def test_finalize_empty(self) -> None:
        result = finalize(_state())
        assert "No branches" in result["final_answer"]

    def test_full_graph_loops(self) -> None:
        mcts = MCTSGraph(max_iterations=3)
        result = mcts.reason(_state(max_iterations=3))
        assert result["iteration"] == 3
        assert len(result["branches"]) == 3
        assert result["final_answer"] != ""

    def test_full_graph_respects_max(self) -> None:
        mcts = MCTSGraph(max_iterations=2)
        result = mcts.reason(_state(max_iterations=2))
        assert result["iteration"] == 2

    def test_compiled_property(self) -> None:
        mcts = MCTSGraph()
        assert mcts.compiled is not None


# ── Common interface ─────────────────────────────────────────────────── #


class TestCommonInterface:
    @pytest.mark.parametrize(
        "cls",
        [ChainOfThoughtGraph, TreeOfThoughtsGraph, MCTSGraph],
    )
    def test_reason_returns_state(self, cls) -> None:
        instance = cls()
        result = instance.reason(_state())
        assert "final_answer" in result
        assert result["final_answer"] != ""

    @pytest.mark.parametrize(
        "cls",
        [ChainOfThoughtGraph, TreeOfThoughtsGraph, MCTSGraph],
    )
    def test_has_compiled(self, cls) -> None:
        instance = cls()
        assert instance.compiled is not None
