"""Tree-of-Thoughts reasoning as a LangGraph branching graph.

Generate N branches → evaluate each → select best → expand.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from openbad.frameworks.workflows.reasoning.state import ReasoningState

log = logging.getLogger(__name__)

_DEFAULT_BRANCHING_FACTOR = 3
_DEFAULT_MAX_DEPTH = 3
_DEFAULT_PRUNE_THRESHOLD = 0.3


def generate_branches(state: ReasoningState) -> dict[str, Any]:
    """Generate N candidate reasoning branches."""
    prompt = state.get("prompt", "")
    # Stub: in production, calls LLM N times with different temperatures.
    branches = [
        {"branch_id": i, "thought": f"Branch {i}: approach to {prompt}"}
        for i in range(_DEFAULT_BRANCHING_FACTOR)
    ]
    return {"branches": branches, "scores": []}


def evaluate_branches(state: ReasoningState) -> dict[str, Any]:
    """Score each branch for quality/promise."""
    branches = state.get("branches", [])
    # Stub: in production, calls LLM to evaluate each branch.
    scores = [0.5 + (0.1 * i) for i in range(len(branches))]
    return {"scores": scores}


def select_best(state: ReasoningState) -> dict[str, Any]:
    """Select the highest-scoring branch."""
    scores = state.get("scores", [])
    if not scores:
        return {"best_branch": 0, "final_answer": "No branches to select."}

    best_idx = scores.index(max(scores))
    branches = state.get("branches", [])
    best = branches[best_idx] if best_idx < len(branches) else {}

    steps = list(state.get("steps", []))
    steps.append(
        {
            "step": len(steps) + 1,
            "thought": f"Selected branch {best_idx}",
            "conclusion": best.get("thought", ""),
        },
    )

    return {
        "best_branch": best_idx,
        "final_answer": best.get("thought", "No answer."),
        "steps": steps,
    }


class TreeOfThoughtsGraph:
    """Tree-of-Thoughts reasoning implemented as a LangGraph workflow."""

    def __init__(
        self,
        branching_factor: int = _DEFAULT_BRANCHING_FACTOR,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        prune_threshold: float = _DEFAULT_PRUNE_THRESHOLD,
    ) -> None:
        self._branching_factor = branching_factor
        self._max_depth = max_depth
        self._prune_threshold = prune_threshold
        self._compiled = self._build().compile()

    def _build(self) -> StateGraph:
        graph = StateGraph(ReasoningState)
        graph.add_node("generate_branches", generate_branches)
        graph.add_node("evaluate_branches", evaluate_branches)
        graph.add_node("select_best", select_best)

        graph.set_entry_point("generate_branches")
        graph.add_edge("generate_branches", "evaluate_branches")
        graph.add_edge("evaluate_branches", "select_best")
        graph.add_edge("select_best", END)
        return graph

    def reason(self, state: ReasoningState) -> ReasoningState:
        """Execute tree-of-thoughts reasoning."""
        return self._compiled.invoke(state)

    @property
    def compiled(self) -> CompiledStateGraph:
        return self._compiled
