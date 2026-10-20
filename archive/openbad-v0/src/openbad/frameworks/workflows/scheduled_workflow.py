"""Scheduled workflow: single Execute node.

Maps to ``TaskKind.SCHEDULED``.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from openbad.frameworks.workflows.nodes import _make_node, should_retry
from openbad.frameworks.workflows.state import AgentState


def build_scheduled_graph() -> StateGraph:
    """Return an uncompiled ``StateGraph`` for scheduled tasks."""
    graph = StateGraph(AgentState)

    graph.add_node("execute", _make_node("execute"))

    graph.set_entry_point("execute")

    # execute: retry up to 1 time (matches existing SCHEDULED template).
    graph.add_conditional_edges(
        "execute",
        should_retry("execute", max_retries=1),
        {"retry": "execute", "continue": END},
    )

    return graph
