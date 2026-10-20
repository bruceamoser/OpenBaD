"""Chain-of-Thought reasoning as a LangChain prompt chain.

Simple sequential strategy: decompose → step through → conclude.
No graph needed — implemented as a linear chain of node functions.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from openbad.frameworks.workflows.reasoning.state import ReasoningState

log = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 10


def decompose(state: ReasoningState) -> dict[str, Any]:
    """Break the problem into sub-steps."""
    prompt = state.get("prompt", "")
    return {
        "steps": [
            {
                "step": 1,
                "thought": f"Decompose: {prompt}",
                "conclusion": "Problem decomposed into sub-steps.",
            },
        ],
    }


def step_through(state: ReasoningState) -> dict[str, Any]:
    """Execute each reasoning step sequentially."""
    steps = list(state.get("steps", []))
    prompt = state.get("prompt", "")
    context = state.get("context", "")
    # Stub: in production, each step calls the LLM.
    steps.append(
        {
            "step": len(steps) + 1,
            "thought": f"Reason about: {prompt} with context: {context}",
            "conclusion": "Step completed.",
        },
    )
    return {"steps": steps}


def conclude(state: ReasoningState) -> dict[str, Any]:
    """Synthesize final answer from reasoning steps."""
    steps = state.get("steps", [])
    conclusions = [s.get("conclusion", "") for s in steps]
    answer = " → ".join(conclusions) if conclusions else "No conclusion."
    return {"final_answer": answer}


class ChainOfThoughtGraph:
    """Chain-of-Thought reasoning implemented as a LangGraph workflow."""

    def __init__(self, max_steps: int = _DEFAULT_MAX_STEPS) -> None:
        self._max_steps = max_steps
        self._compiled = self._build().compile()

    def _build(self) -> StateGraph:
        graph = StateGraph(ReasoningState)
        graph.add_node("decompose", decompose)
        graph.add_node("step_through", step_through)
        graph.add_node("conclude", conclude)

        graph.set_entry_point("decompose")
        graph.add_edge("decompose", "step_through")
        graph.add_edge("step_through", "conclude")
        graph.add_edge("conclude", END)
        return graph

    def reason(self, state: ReasoningState) -> ReasoningState:
        """Execute chain-of-thought reasoning."""
        return self._compiled.invoke(state)

    @property
    def compiled(self) -> CompiledStateGraph:
        return self._compiled
