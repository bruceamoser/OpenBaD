"""Shared state for reasoning workflow graphs."""

from __future__ import annotations

from typing import Any, TypedDict


class ReasoningState(TypedDict, total=False):
    """State flowing through reasoning workflow nodes.

    Attributes
    ----------
    prompt:
        The problem/question to reason about.
    context:
        Supporting context for reasoning.
    steps:
        Accumulated reasoning steps.
    branches:
        Parallel reasoning branches (ToT / MCTS).
    scores:
        Evaluation scores for branches.
    best_branch:
        Index of the selected best branch.
    final_answer:
        The concluded answer.
    total_tokens:
        Cumulative token usage.
    iteration:
        Current iteration counter (MCTS loop).
    max_iterations:
        Max iterations for looping strategies.
    error:
        Error message if any.
    """

    prompt: str
    context: str
    steps: list[dict[str, Any]]
    branches: list[dict[str, Any]]
    scores: list[float]
    best_branch: int
    final_answer: str
    total_tokens: int
    iteration: int
    max_iterations: int
    error: str
