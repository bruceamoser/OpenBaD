"""User-request workflow: Clarify → Plan → Execute → Review.

Maps to ``TaskKind.USER_REQUESTED``.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from openbad.frameworks.workflows.nodes import _make_node, should_retry
from openbad.frameworks.workflows.state import AgentState


def build_user_request_graph() -> StateGraph:
    """Return an uncompiled ``StateGraph`` for user-requested tasks."""
    graph = StateGraph(AgentState)

    graph.add_node("clarify", _make_node("clarify"))
    graph.add_node("plan", _make_node("plan"))
    graph.add_node("execute", _make_node("execute"))
    graph.add_node("review", _make_node("review"))

    graph.set_entry_point("clarify")

    # clarify has 0 retries → always continues.
    graph.add_edge("clarify", "plan")

    # plan: retry up to 1 time.
    graph.add_conditional_edges(
        "plan",
        should_retry("plan"),
        {"retry": "plan", "continue": "execute"},
    )

    # execute: retry up to 2 times.
    graph.add_conditional_edges(
        "execute",
        should_retry("execute"),
        {"retry": "execute", "continue": "review"},
    )

    # review has 0 retries → always finishes.
    graph.add_edge("review", END)

    return graph
