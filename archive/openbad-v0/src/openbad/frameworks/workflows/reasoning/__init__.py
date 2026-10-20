"""LangGraph-based reasoning strategies.

Replaces custom reasoning implementations with LangChain chains
and LangGraph graphs:

``ChainOfThoughtGraph``
    LangChain prompt chain for step-by-step reasoning.
``TreeOfThoughtsGraph``
    LangGraph branching graph: generate → evaluate → select.
``MCTSGraph``
    LangGraph looping graph: select → expand → simulate → backpropagate.

All implement the ``reason(state) → state`` interface and are
selectable by priority level.
"""

from __future__ import annotations

from openbad.frameworks.workflows.reasoning.chain_of_thought import (
    ChainOfThoughtGraph,
)
from openbad.frameworks.workflows.reasoning.mcts import MCTSGraph
from openbad.frameworks.workflows.reasoning.state import ReasoningState
from openbad.frameworks.workflows.reasoning.tree_of_thoughts import (
    TreeOfThoughtsGraph,
)

__all__ = [
    "ChainOfThoughtGraph",
    "MCTSGraph",
    "ReasoningState",
    "TreeOfThoughtsGraph",
]
