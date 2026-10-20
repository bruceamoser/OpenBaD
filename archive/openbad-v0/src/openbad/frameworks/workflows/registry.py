"""Registry mapping TaskKind to compiled workflow graphs."""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from openbad.frameworks.workflows.research_workflow import build_research_graph
from openbad.frameworks.workflows.scheduled_workflow import build_scheduled_graph
from openbad.frameworks.workflows.system_workflow import build_system_graph
from openbad.frameworks.workflows.user_request_workflow import (
    build_user_request_graph,
)

# Maps TaskKind string values to graph builder functions.
_BUILDERS: dict[str, Any] = {
    "user_requested": build_user_request_graph,
    "research": build_research_graph,
    "system": build_system_graph,
    "scheduled": build_scheduled_graph,
}


def get_workflow(
    kind: str,
    *,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Return a compiled workflow graph for the given task kind.

    Parameters
    ----------
    kind:
        A ``TaskKind`` value (e.g. ``"user_requested"``).
    checkpointer:
        Optional ``BaseCheckpointSaver`` for state persistence.
    """
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown task kind: {kind!r}")
    graph: StateGraph = builder()
    return graph.compile(checkpointer=checkpointer)
