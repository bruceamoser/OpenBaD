"""Monte Carlo Tree Search reasoning as a LangGraph looping graph.

Select → Expand → Simulate → Backpropagate, looped for N iterations.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from openbad.frameworks.workflows.reasoning.state import ReasoningState

log = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 10
_DEFAULT_EXPLORATION_CONSTANT = 1.414


def select(state: ReasoningState) -> dict[str, Any]:
    """Select the most promising node to explore (UCB1)."""
    branches = state.get("branches", [])
    scores = state.get("scores", [])

    if not branches:
        return {"best_branch": 0}

    # Stub: in production, uses UCB1 formula.
    best_idx = 0
    if scores:
        best_idx = scores.index(max(scores))
    return {"best_branch": best_idx}


def expand(state: ReasoningState) -> dict[str, Any]:
    """Expand the selected node with a new child."""
    branches = list(state.get("branches", []))
    iteration = state.get("iteration", 0)
    prompt = state.get("prompt", "")

    # Stub: in production, calls LLM to generate expansion.
    branches.append(
        {
            "branch_id": len(branches),
            "thought": f"Expansion {iteration}: {prompt}",
            "iteration": iteration,
        },
    )
    return {"branches": branches}


def simulate(state: ReasoningState) -> dict[str, Any]:
    """Simulate a random rollout from the expanded node."""
    branches = state.get("branches", [])
    scores = list(state.get("scores", []))

    # Stub: in production, calls LLM to simulate outcome.
    # Pad scores to match branches.
    while len(scores) < len(branches):
        scores.append(0.5)
    # Update latest score.
    if scores:
        scores[-1] = 0.5 + (len(scores) * 0.05)
    return {"scores": scores}


def backpropagate(state: ReasoningState) -> dict[str, Any]:
    """Update statistics along the path."""
    iteration = state.get("iteration", 0) + 1
    steps = list(state.get("steps", []))
    steps.append(
        {
            "step": iteration,
            "thought": f"MCTS iteration {iteration}",
            "conclusion": f"Backpropagated scores for {len(state.get('branches', []))} branches.",
        },
    )
    return {"iteration": iteration, "steps": steps}


def _should_continue(state: ReasoningState) -> str:
    """Check if we should continue iterating."""
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", _DEFAULT_MAX_ITERATIONS)
    if iteration >= max_iter:
        return "done"
    return "continue"


def finalize(state: ReasoningState) -> dict[str, Any]:
    """Select the final answer from the best branch."""
    branches = state.get("branches", [])
    scores = state.get("scores", [])

    if not branches:
        return {"final_answer": "No branches explored."}

    best_idx = 0
    if scores:
        best_idx = scores.index(max(scores))

    best = branches[best_idx] if best_idx < len(branches) else {}
    return {
        "best_branch": best_idx,
        "final_answer": best.get("thought", "No answer."),
    }


class MCTSGraph:
    """MCTS reasoning implemented as a LangGraph looping workflow."""

    def __init__(
        self,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        exploration_constant: float = _DEFAULT_EXPLORATION_CONSTANT,
    ) -> None:
        self._max_iterations = max_iterations
        self._exploration_constant = exploration_constant
        self._compiled = self._build().compile()

    def _build(self) -> StateGraph:
        graph = StateGraph(ReasoningState)

        graph.add_node("select", select)
        graph.add_node("expand", expand)
        graph.add_node("simulate", simulate)
        graph.add_node("backpropagate", backpropagate)
        graph.add_node("finalize", finalize)

        graph.set_entry_point("select")
        graph.add_edge("select", "expand")
        graph.add_edge("expand", "simulate")
        graph.add_edge("simulate", "backpropagate")

        # Loop or finish.
        graph.add_conditional_edges(
            "backpropagate",
            _should_continue,
            {"continue": "select", "done": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph

    def reason(self, state: ReasoningState) -> ReasoningState:
        """Execute MCTS reasoning."""
        if "max_iterations" not in state:
            state = {**state, "max_iterations": self._max_iterations}
        return self._compiled.invoke(state)

    @property
    def compiled(self) -> CompiledStateGraph:
        return self._compiled
