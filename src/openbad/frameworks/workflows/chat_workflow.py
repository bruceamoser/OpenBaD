"""LangGraph streaming chat workflow.

Replaces the sequential ``stream_chat()`` pipeline with a five-node
``StateGraph``:

1. ``immune_scan`` — scan user input via immune rules engine.
2. ``memory_retrieval`` — query STM + episodic + semantic memory.
3. ``context_assembly`` — assemble context within token budget.
4. ``llm_stream`` — invoke the LLM via ``OpenBaDChatModel``.
5. ``memory_persist`` — write turns to STM + episodic.

If the immune scan detects a threat the graph short-circuits to an
error node and never calls the LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

log = logging.getLogger(__name__)


# ── Chat State ────────────────────────────────────────────────────────── #


class ChatState(TypedDict, total=False):
    """State flowing through the chat workflow graph."""

    # Input
    user_message: str
    session_id: str
    model_id: str
    system: str  # CognitiveSystem value, e.g. "chat"

    # Pipeline data
    immune_ok: bool
    immune_error: str
    memory_context: str
    conversation_history: list[dict[str, str]]
    system_prompt: str
    assembled_messages: list[dict[str, str]]

    # Output
    response_text: str
    tokens_used: int
    provider: str
    error: str
    done: bool


# ── Node functions ────────────────────────────────────────────────────── #


def immune_scan(state: ChatState) -> dict[str, Any]:
    """Scan user input through the immune rules engine."""
    from openbad.immune_system.rules_engine import RulesEngine

    message = state.get("user_message", "")
    engine = RulesEngine(include_builtins=True)
    report = engine.scan(message)

    blocking = [m for m in report.matches if m.severity in {"critical", "high"}]
    if blocking:
        names = ", ".join(m.rule_name for m in blocking)
        log.warning("Chat immune scan blocked: %s", names)
        return {
            "immune_ok": False,
            "immune_error": f"Message blocked by security scan: {names}",
        }
    return {"immune_ok": True, "immune_error": ""}


def memory_retrieval(state: ChatState) -> dict[str, Any]:
    """Query STM, episodic, and semantic memory for relevant context."""
    from openbad.wui.chat_pipeline import (
        _get_conversation_history,
        _get_episodic_context,
        _get_semantic_context,
    )

    session_id = state.get("session_id", "")
    user_message = state.get("user_message", "")

    # Retrieve conversation history from SQLite
    history = _get_conversation_history(session_id)
    history_dicts = [
        {"role": t.role, "content": t.content}
        for t in history
    ]

    # Retrieve cross-session context
    episodic_ctx = _get_episodic_context(session_id, user_message)
    semantic_ctx = _get_semantic_context(session_id, user_message)

    parts = [p for p in (episodic_ctx, semantic_ctx) if p]
    memory_context = "\n\n".join(parts)

    return {
        "memory_context": memory_context,
        "conversation_history": history_dicts,
    }


def context_assembly(state: ChatState) -> dict[str, Any]:
    """Assemble context into LLM-ready messages."""
    messages: list[dict[str, str]] = []

    system_prompt = state.get("system_prompt", "")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    memory_ctx = state.get("memory_context", "")
    if memory_ctx:
        messages.append({"role": "system", "content": memory_ctx})

    for turn in state.get("conversation_history", []):
        messages.append(turn)

    user_msg = state.get("user_message", "")
    if user_msg:
        messages.append({"role": "user", "content": user_msg})

    return {"assembled_messages": messages}


def llm_stream(state: ChatState) -> dict[str, Any]:
    """Invoke the LLM and collect the response.

    In production this node streams via ``OpenBaDChatModel``.  For now
    it produces a stub response so the graph structure is testable
    without API keys.
    """
    messages = state.get("assembled_messages", [])
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    return {
        "response_text": f"[LLM response to: {user_msg}]",
        "tokens_used": 0,
        "provider": "stub",
    }


def memory_persist(state: ChatState) -> dict[str, Any]:
    """Persist user and assistant turns to STM + episodic + semantic memory."""
    from openbad.wui.chat_pipeline import ConversationTurn, _write_turn

    session_id = state.get("session_id", "")
    user_message = state.get("user_message", "")
    response_text = state.get("response_text", "")

    if session_id and user_message:
        _write_turn(
            session_id,
            ConversationTurn(
                role="user",
                content=user_message,
                timestamp=time.time(),
            ),
        )

    if session_id and response_text:
        _write_turn(
            session_id,
            ConversationTurn(
                role="assistant",
                content=response_text,
                timestamp=time.time(),
            ),
        )

    return {"done": True}


def immune_blocked(state: ChatState) -> dict[str, Any]:
    """Terminal node when immune scan blocks the input."""
    return {
        "error": state.get("immune_error", "Blocked"),
        "response_text": "",
        "done": True,
    }


# ── Routing ───────────────────────────────────────────────────────────── #


def _route_after_immune(state: ChatState) -> str:
    if state.get("immune_ok", True):
        return "continue"
    return "blocked"


# ── Graph builder ─────────────────────────────────────────────────────── #


def build_chat_graph() -> StateGraph:
    """Build the streaming chat workflow graph (uncompiled)."""
    graph = StateGraph(ChatState)

    graph.add_node("immune_scan", immune_scan)
    graph.add_node("memory_retrieval", memory_retrieval)
    graph.add_node("context_assembly", context_assembly)
    graph.add_node("llm_stream", llm_stream)
    graph.add_node("memory_persist", memory_persist)
    graph.add_node("immune_blocked", immune_blocked)

    graph.set_entry_point("immune_scan")

    graph.add_conditional_edges(
        "immune_scan",
        _route_after_immune,
        {"continue": "memory_retrieval", "blocked": "immune_blocked"},
    )
    graph.add_edge("memory_retrieval", "context_assembly")
    graph.add_edge("context_assembly", "llm_stream")
    graph.add_edge("llm_stream", "memory_persist")
    graph.add_edge("memory_persist", END)
    graph.add_edge("immune_blocked", END)

    return graph


def get_chat_workflow(
    *,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Return a compiled chat workflow graph."""
    return build_chat_graph().compile(checkpointer=checkpointer)
