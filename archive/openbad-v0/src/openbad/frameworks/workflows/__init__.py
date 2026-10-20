"""LangGraph workflow graphs for OpenBaD task patterns.

Each workflow maps to a ``TaskKind`` and replaces the linear
``NodeTemplate`` DAG chains with ``StateGraph`` definitions that
support conditional retry edges and structured state transitions.

Public API
----------
``get_workflow(kind)``
    Return a compiled ``StateGraph`` for the given ``TaskKind``.
``AgentState``
    TypedDict flowing through all workflow nodes.
"""

from __future__ import annotations

from openbad.frameworks.workflows.registry import get_workflow
from openbad.frameworks.workflows.state import AgentState

__all__ = ["AgentState", "get_workflow"]
