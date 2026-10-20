"""Reusable node functions and retry-edge builder for workflows."""

from __future__ import annotations

import logging
from typing import Any

from openbad.frameworks.workflows.state import AgentState

log = logging.getLogger(__name__)

# Default max retries per node type — matches existing NodeTemplate defaults.
DEFAULT_MAX_RETRIES: dict[str, int] = {
    "clarify": 0,
    "plan": 1,
    "execute": 2,
    "review": 0,
    "gather": 2,
    "analyse": 1,
    "summarise": 0,
}


def _make_node(node_type: str):
    """Return a node function that records its type in the result list.

    Each node function:
    1. Logs entry.
    2. Appends a result dict with ``node_type`` and ``status``.
    3. Returns updated ``AgentState``.

    In a future iteration nodes will delegate to LangChain chains or
    CrewAI agents.  For now they are stubs that ensure the graph
    structure, retry logic, and state transitions are correct.
    """

    def _node(state: AgentState) -> dict[str, Any]:
        task_id = state.get("task_metadata", {}).get("task_id", "?")
        log.info("Workflow node '%s' running for task %s", node_type, task_id)

        results = list(state.get("results", []))
        results.append({"node": node_type, "status": "done"})
        return {"results": results, "status": "running"}

    _node.__name__ = node_type  # LangGraph uses __name__ as the node key
    _node.__qualname__ = node_type
    return _node


def should_retry(node_type: str, max_retries: int | None = None):
    """Return a conditional-edge function for retry logic.

    The returned function checks ``state["retry_counts"][node_type]``
    against *max_retries* and routes to either the node (retry) or
    the next step.
    """
    limit = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES.get(node_type, 0)

    def _decide(state: AgentState) -> str:
        counts = state.get("retry_counts", {})
        current = counts.get(node_type, 0)
        error = state.get("error", "")
        if error and current < limit:
            return "retry"
        return "continue"

    return _decide
