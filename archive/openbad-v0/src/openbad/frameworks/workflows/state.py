"""Shared workflow state definition."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """State flowing through every LangGraph workflow node.

    Attributes
    ----------
    messages:
        Accumulated conversation messages (user, assistant, system).
    context:
        Additional context gathered during clarification / research.
    memory_refs:
        Keys of relevant memory entries pulled from STM/episodic.
    task_metadata:
        Task model fields: task_id, kind, priority, owner, etc.
    results:
        Outputs collected from execution nodes.
    retry_counts:
        Per-node retry counters: ``{"clarify": 0, "execute": 1, …}``.
    error:
        Last error message (set by nodes on failure).
    status:
        Overall workflow status: ``"running"``, ``"done"``, ``"failed"``.
    """

    messages: list[dict[str, Any]]
    context: str
    memory_refs: list[str]
    task_metadata: dict[str, Any]
    results: list[dict[str, Any]]
    retry_counts: dict[str, int]
    error: str
    status: str
