"""Research workflow: Gather → Analyse → Summarise.

Maps to ``TaskKind.RESEARCH``.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from openbad.frameworks.workflows.nodes import _make_node, should_retry
from openbad.frameworks.workflows.state import AgentState


def build_research_graph() -> StateGraph:
    """Return an uncompiled ``StateGraph`` for research tasks."""
    graph = StateGraph(AgentState)

    graph.add_node("gather", _make_node("gather"))
    graph.add_node("analyse", _make_node("analyse"))
    graph.add_node("summarise", _make_node("summarise"))

    graph.set_entry_point("gather")

    # gather: retry up to 2 times.
    graph.add_conditional_edges(
        "gather",
        should_retry("gather"),
        {"retry": "gather", "continue": "analyse"},
    )

    # analyse: retry up to 1 time.
    graph.add_conditional_edges(
        "analyse",
        should_retry("analyse"),
        {"retry": "analyse", "continue": "summarise"},
    )

    # summarise has 0 retries → always finishes.
    graph.add_edge("summarise", END)

    return graph
