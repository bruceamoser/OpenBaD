"""Chat pipeline — wires immune scanning, memory, context, providers, and consolidation.

This module provides the full chat processing pipeline for the WUI,
engaging the subsystems that make OpenBaD more than a pass-through to an LLM:

1. Immune scan (rules engine) on inbound message
2. Memory retrieval (STM conversation history + episodic + semantic)
3. Context assembly and compression to fit token budget
4. Streaming response from the assigned provider
5. Post-completion: write exchange to STM + episodic memory
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbad.cognitive.config import (
    CognitiveSystem,
)
from openbad.cognitive.context_manager import (
    ContextWindowManager,
    estimate_tokens,
)
from openbad.cognitive.providers.base import ProviderAdapter
from openbad.identity.onboarding import (
    INTERVIEW_SYSTEM_PROMPT,
    USER_INTERVIEW_SYSTEM_PROMPT,
    is_assistant_configured,
    is_user_configured,
)
from openbad.immune_system.rules_engine import RulesEngine, ScanReport
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory
from openbad.nervous_system import topics

log = logging.getLogger(__name__)

# Type hint for nervous system client (avoid circular import)
if TYPE_CHECKING:
    from openbad.nervous_system.client import NervousSystemClient

    NervousSystemClient_T = NervousSystemClient
else:
    NervousSystemClient_T = Any


# ── Configuration ─────────────────────────────────────────────────── #

_DATA_DIR = Path("/var/lib/openbad")
_MEMORY_DIR = _DATA_DIR / "memory"
_MAX_CONVERSATION_TURNS = 50  # max turns to keep in STM
_SEMANTIC_TOP_K = 3  # top-k results from semantic search
_SYSTEM_PROMPT_CHAT = (
    "You are OpenBaD, a helpful AI assistant. "
    "Answer clearly and concisely. Use markdown formatting when helpful."
)
_SYSTEM_PROMPT_REASONING = (
    "You are OpenBaD, an analytical reasoning assistant. "
    "Think step-by-step. Show your reasoning process before giving a final answer."
)


# ── Data types ────────────────────────────────────────────────────── #


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: float = 0.0


@dataclass
class ChatContext:
    """Assembled context for a chat request."""

    system_prompt: str
    conversation_history: list[ConversationTurn]
    supporting_context: str = ""
    total_tokens: int = 0


@dataclass
class StreamChunk:
    """A single chunk emitted during streaming."""

    token: str = ""
    reasoning: str = ""
    tokens_used: int = 0
    error: str = ""
    done: bool = False


# ── Pipeline singleton state ──────────────────────────────────────── #

_stm: ShortTermMemory | None = None
_episodic: EpisodicMemory | None = None
_semantic: SemanticMemory | None = None
_rules_engine: RulesEngine | None = None
_ctx_manager: ContextWindowManager | None = None


def _get_stm() -> ShortTermMemory:
    global _stm
    if _stm is None:
        _stm = ShortTermMemory(max_tokens=65_536, default_ttl=7200.0)
    return _stm


def _get_episodic() -> EpisodicMemory:
    global _episodic
    if _episodic is None:
        storage = _MEMORY_DIR / "episodic"
        storage.mkdir(parents=True, exist_ok=True)
        _episodic = EpisodicMemory(storage_path=storage / "chat.json")
    return _episodic


def _get_semantic() -> SemanticMemory:
    global _semantic
    if _semantic is None:
        storage = _MEMORY_DIR / "semantic"
        storage.mkdir(parents=True, exist_ok=True)
        _semantic = SemanticMemory(storage_path=storage / "chat.json")
    return _semantic


def _get_rules_engine() -> RulesEngine:
    global _rules_engine
    if _rules_engine is None:
        _rules_engine = RulesEngine(include_builtins=True)
    return _rules_engine


def _get_ctx_manager() -> ContextWindowManager:
    global _ctx_manager
    if _ctx_manager is None:
        _ctx_manager = ContextWindowManager(default_limit=8_192)
    return _ctx_manager


# ── Immune scanning ──────────────────────────────────────────────── #


def scan_input(text: str) -> ScanReport:
    """Run the rules engine on an inbound message. Fast (<50ms)."""
    return _get_rules_engine().scan(text)


# ── Conversation memory ──────────────────────────────────────────── #

# Use a simple session-keyed approach: STM holds recent turns,
# episodic stores the full history for long-term retrieval.

_SESSION_PREFIX = "chat:session:"


def _session_key(session_id: str, turn_idx: int) -> str:
    return f"{_SESSION_PREFIX}{session_id}:{turn_idx:06d}"


def _semantic_key(session_id: str, turn_idx: int) -> str:
    return f"chat:semantic:{session_id}:{turn_idx:06d}"


def _next_turn_idx(session_id: str) -> int:
    episodic = _get_episodic()
    entries = episodic.query(f"{_SESSION_PREFIX}{session_id}:")
    if not entries:
        return 0
    return max(int(entry.metadata.get("turn_idx", 0)) for entry in entries) + 1


def _write_turn(
    session_id: str,
    turn: ConversationTurn,
    *,
    onboarding_mode: bool = False,
) -> None:
    """Write a conversation turn to STM and episodic memory."""
    stm = _get_stm()
    episodic = _get_episodic()
    semantic = _get_semantic()

    turn_idx = _next_turn_idx(session_id)

    key = _session_key(session_id, turn_idx)
    now = time.time()

    entry = MemoryEntry(
        key=key,
        value=turn.content,
        tier=MemoryTier.STM,
        created_at=now,
        accessed_at=now,
        context=turn.role,  # store role in context field
        metadata={
            "session_id": session_id,
            "role": turn.role,
            "turn_idx": turn_idx,
            "onboarding_mode": onboarding_mode,
        },
    )
    stm.write(entry)

    # Also persist to episodic for long-term recall
    ep_entry = MemoryEntry(
        key=key,
        value=turn.content,
        tier=MemoryTier.EPISODIC,
        created_at=now,
        accessed_at=now,
        context=turn.role,
        metadata={
            "session_id": session_id,
            "role": turn.role,
            "turn_idx": turn_idx,
            "task_id": session_id,
            "onboarding_mode": onboarding_mode,
        },
    )
    episodic.write(ep_entry)

    if not onboarding_mode:
        semantic.write(
            MemoryEntry(
                key=_semantic_key(session_id, turn_idx),
                value=turn.content,
                tier=MemoryTier.SEMANTIC,
                created_at=now,
                accessed_at=now,
                context=turn.role,
                metadata={
                    "session_id": session_id,
                    "role": turn.role,
                    "turn_idx": turn_idx,
                    "tags": [turn.role, session_id],
                    "onboarding_mode": onboarding_mode,
                },
            )
        )


def _get_conversation_history(session_id: str) -> list[ConversationTurn]:
    """Retrieve recent conversation from persisted episodic memory."""
    episodic = _get_episodic()
    entries = episodic.query(f"{_SESSION_PREFIX}{session_id}:")

    # Sort by turn index
    entries.sort(key=lambda e: e.metadata.get("turn_idx", 0))

    # Keep last N turns
    entries = entries[-_MAX_CONVERSATION_TURNS:]

    return [
        ConversationTurn(
            role=e.metadata.get("role", e.context),
            content=str(e.value),
            timestamp=e.created_at,
        )
        for e in entries
    ]


def get_conversation_history(
    session_id: str,
    *,
    limit: int = _MAX_CONVERSATION_TURNS,
) -> list[ConversationTurn]:
    """Return persisted conversation history for a session."""
    if limit <= 0:
        return []
    return _get_conversation_history(session_id)[-limit:]


def _get_episodic_context(session_id: str, query: str) -> str:
    """Retrieve relevant episodic memories (from prior sessions)."""
    episodic = _get_episodic()
    try:
        recent = episodic.recent(n=20)
        # Filter to exclude current session (we already have that in STM)
        prior = [
            e for e in recent
            if e.metadata.get("session_id") != session_id
            and not e.metadata.get("onboarding_mode", False)
        ]
        if not prior:
            return ""

        # Format as context summary
        lines = []
        for e in prior[-5:]:  # Last 5 from prior sessions
            role = e.metadata.get("role", "unknown")
            content = str(e.value)[:200]  # Truncate long entries
            lines.append(f"[{role}] {content}")

        return "Prior conversation context:\n" + "\n".join(lines)
    except Exception:
        log.debug("Episodic retrieval failed", exc_info=True)
        return ""


def _get_semantic_context(session_id: str, query: str) -> str:
    """Retrieve semantically similar memories from prior sessions."""
    semantic = _get_semantic()
    try:
        matches = semantic.search(query, top_k=_SEMANTIC_TOP_K)
    except Exception:
        log.debug("Semantic retrieval failed", exc_info=True)
        return ""

    filtered = [
        (entry, score)
        for entry, score in matches
        if entry.metadata.get("session_id") != session_id
        and not entry.metadata.get("onboarding_mode", False)
    ]
    if not filtered:
        return ""

    lines = []
    for entry, score in filtered:
        role = entry.metadata.get("role", entry.context or "unknown")
        content = str(entry.value).strip()
        if not content:
            continue
        lines.append(f"[{role} relevance={score:.2f}] {content[:200]}")
    if not lines:
        return ""
    return "Relevant prior memories:\n" + "\n".join(lines)


def _build_identity_prompt(
    user_profile: Any | None,
    assistant_profile: Any | None,
    modulation: Any | None,
) -> str:
    """Render user and assistant identity state into the system prompt."""
    lines: list[str] = []

    if assistant_profile is not None:
        assistant_name = getattr(assistant_profile, "name", "")
        persona_summary = getattr(assistant_profile, "persona_summary", "")
        learning_focus = getattr(assistant_profile, "learning_focus", []) or []
        if assistant_name:
            lines.append(f"Assistant identity: {assistant_name}")
        if persona_summary:
            lines.append(f"Assistant persona: {persona_summary}")
        if learning_focus:
            lines.append(
                "Assistant learning focus: " + ", ".join(str(item) for item in learning_focus)
            )

    if user_profile is not None:
        user_name = (
            getattr(user_profile, "preferred_name", "")
            or getattr(user_profile, "name", "")
        )
        communication_style = getattr(user_profile, "communication_style", "")
        expertise_domains = getattr(user_profile, "expertise_domains", []) or []
        interaction_history_summary = getattr(user_profile, "interaction_history_summary", "")
        if user_name:
            lines.append(f"User identity: {user_name}")
        if communication_style:
            style_value = getattr(communication_style, "value", communication_style)
            lines.append(f"Preferred communication style: {style_value}")
        if expertise_domains:
            lines.append(
                "User expertise domains: " + ", ".join(str(item) for item in expertise_domains)
            )
        if interaction_history_summary:
            lines.append(f"User history summary: {interaction_history_summary}")

    if modulation is not None:
        modulation_fields = [
            ("exploration_budget_multiplier", "Exploration budget multiplier"),
            ("max_reasoning_depth_multiplier", "Reasoning depth multiplier"),
            ("proactive_suggestion_threshold", "Proactive suggestion threshold"),
            ("challenge_probability", "Challenge probability"),
            ("cortisol_decay_multiplier", "Cortisol decay multiplier"),
        ]
        rendered = []
        for field_name, label in modulation_fields:
            value = getattr(modulation, field_name, None)
            if value is not None:
                rendered.append(f"{label}: {value:.2f}")
        if rendered:
            lines.append("Behavior modulation: " + "; ".join(rendered))

    if not lines:
        return ""
    return "\n\nIdentity context:\n" + "\n".join(lines)


# ── Context assembly ──────────────────────────────────────────────── #


def assemble_context(
    session_id: str,
    message: str,
    system: CognitiveSystem,
    model_id: str,
    *,
    user_profile: Any | None = None,
    assistant_profile: Any | None = None,
    modulation: Any | None = None,
) -> ChatContext:
    """Assemble full context: system prompt + history + episodic, compressed to fit."""
    ctx = _get_ctx_manager()
    budget = ctx.allocate(model_id)

    # Check if we're in onboarding interview mode
    assistant_needs_config = (
        assistant_profile is not None
        and not is_assistant_configured(assistant_profile)
    )
    user_needs_config = (
        user_profile is not None
        and not is_user_configured(user_profile)
    )

    if assistant_needs_config:
        # Assistant identity interview mode (first priority)
        system_prompt = INTERVIEW_SYSTEM_PROMPT
    elif user_needs_config:
        # User profile interview mode (second priority, after assistant)
        system_prompt = USER_INTERVIEW_SYSTEM_PROMPT
    else:
        # Normal mode: use chat or reasoning prompt
        base_system_prompt = (
            _SYSTEM_PROMPT_REASONING
            if system == CognitiveSystem.REASONING
            else _SYSTEM_PROMPT_CHAT
        )
        system_prompt = (
            base_system_prompt
            + _build_identity_prompt(user_profile, assistant_profile, modulation)
        )

    onboarding_mode = assistant_needs_config or user_needs_config

    # Retrieve conversation history
    history = _get_conversation_history(session_id)

    # Retrieve long-term context from prior sessions
    episodic_ctx = ""
    semantic_ctx = ""
    if not onboarding_mode:
        episodic_ctx = _get_episodic_context(session_id, message)
        semantic_ctx = _get_semantic_context(session_id, message)

    # Calculate tokens for each piece
    system_tokens = estimate_tokens(system_prompt)
    message_tokens = estimate_tokens(message)

    # Reserve tokens: system + current message + response headroom
    available = max(0, budget.context_tokens - system_tokens - message_tokens)

    supporting_context = ""
    supporting_tokens = 0
    combined_support = "\n\n".join(
        context for context in (episodic_ctx, semantic_ctx) if context
    )
    if combined_support and available > 0:
        support_budget = max(available // 3, 1)
        compressed_support = ctx.compress(combined_support, support_budget)
        supporting_context = compressed_support.text
        supporting_tokens = compressed_support.compressed_tokens
        available = max(0, available - supporting_tokens)

    # Fit conversation history (newest first, trim oldest)
    fitted_history: list[ConversationTurn] = []
    used = 0
    for turn in reversed(history):
        turn_tokens = estimate_tokens(turn.content) + 10  # overhead for role label
        if used + turn_tokens > available:
            break
        fitted_history.insert(0, turn)
        used += turn_tokens

    total = system_tokens + used + supporting_tokens + message_tokens

    return ChatContext(
        system_prompt=system_prompt,
        conversation_history=fitted_history,
        supporting_context=supporting_context,
        total_tokens=total,
    )


def _build_messages(
    context: ChatContext,
    current_message: str,
) -> list[dict[str, str]]:
    """Build the messages array for the provider (OpenAI chat format)."""
    messages: list[dict[str, str]] = []

    # System prompt
    messages.append({"role": "system", "content": context.system_prompt})

    if context.supporting_context:
        messages.append({"role": "system", "content": context.supporting_context})

    # Conversation history
    for turn in context.conversation_history:
        messages.append({"role": turn.role, "content": turn.content})

    # Current user message
    messages.append({"role": "user", "content": current_message})

    return messages


def _flatten_messages(messages: list[dict[str, str]]) -> str:
    """Flatten messages array to a single prompt string for providers that expect it."""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            parts.append(f"[System] {content}")
        elif role == "user":
            parts.append(f"[User] {content}")
        elif role == "assistant":
            parts.append(f"[Assistant] {content}")
    return "\n\n".join(parts)


# ── Streaming pipeline ────────────────────────────────────────────── #


async def stream_chat(
    adapter: ProviderAdapter,
    model_id: str,
    message: str,
    session_id: str,
    system: CognitiveSystem = CognitiveSystem.CHAT,
    *,
    provider_name: str = "",
    user_profile: Any | None = None,
    assistant_profile: Any | None = None,
    modulation: Any | None = None,
    usage_tracker: Any | None = None,
    nervous_system_client: NervousSystemClient_T | None = None,
) -> AsyncIterator[StreamChunk]:
    """Full chat pipeline: scan → assemble → stream → consolidate.

    Yields StreamChunk objects as tokens arrive. The final chunk has done=True.

    If a nervous_system_client is provided, publishes events to the MQTT bus:
    - COGNITIVE_INPUT on user message received
    - COGNITIVE_OUTPUT on successful completion
    - COGNITIVE_ERROR on failures
    """
    request_id = uuid.uuid4().hex[:12]
    start_timestamp = time.time()
    onboarding_mode = (
        (assistant_profile is not None and not is_assistant_configured(assistant_profile))
        or (user_profile is not None and not is_user_configured(user_profile))
    )

    # ── 1. Immune scan ──
    report = scan_input(message)
    _blocking = [m for m in report.matches if m.severity in {"critical", "high"}]
    if _blocking:
        threat_names = ", ".join(m.rule_name for m in _blocking)
        log.warning("Immune scan blocked message (request=%s): %s", request_id, threat_names)
        _publish_error(
            nervous_system_client,
            source="wui",
            error_type="immune_scan_blocked",
            message_hash=_hash_message(message),
            timestamp=start_timestamp,
        )
        yield StreamChunk(
            error=f"Message blocked by security scan: {threat_names}",
            done=True,
        )
        return
    elif report.is_threat:
        # Medium/low severity — flag but allow
        threat_names = ", ".join(m.rule_name for m in report.matches)
        log.info(
            "Immune scan flagged message (non-blocking, request=%s): %s",
            request_id,
            threat_names,
        )

    # ── 2. Assemble context ──
    context = assemble_context(
        session_id,
        message,
        system,
        model_id,
        user_profile=user_profile,
        assistant_profile=assistant_profile,
        modulation=modulation,
    )
    messages = _build_messages(context, message)
    prompt = _flatten_messages(messages)

    # ── 3. Record user message in memory ──
    _write_turn(
        session_id,
        ConversationTurn(role="user", content=message, timestamp=time.time()),
        onboarding_mode=onboarding_mode,
    )

    # Publish cognitive input event
    _publish_input(
        nervous_system_client,
        source="wui",
        user_id=session_id,
        message_hash=_hash_message(message),
        timestamp=start_timestamp,
    )

    log.info(
        "Chat request=%s system=%s model=%s context_tokens=%d history_turns=%d",
        request_id, system.value, model_id,
        context.total_tokens, len(context.conversation_history),
    )

    # ── 4. Stream from provider ──
    full_response = []
    tokens_used = 0
    t0 = time.monotonic()

    try:
        async for token in adapter.stream(prompt, model_id=model_id):
            tokens_used += 1
            full_response.append(token)
            yield StreamChunk(token=token, tokens_used=tokens_used)
    except Exception as e:
        log.exception("Stream error request=%s", request_id)
        _publish_error(
            nervous_system_client,
            source="wui",
            error_type=type(e).__name__,
            message_hash=_hash_message(message),
            timestamp=time.time(),
        )
        status = getattr(e, "status", None)
        detail = getattr(e, "message", "") or str(e)
        provider_label = provider_name or "provider"
        if status is not None:
            error_text = f"{provider_label} returned {status}"
            if detail:
                error_text += f": {detail}"
        elif detail:
            error_text = f"{provider_label} request failed: {detail}"
        else:
            error_text = f"{provider_label} request failed"
        yield StreamChunk(error=error_text, done=True)
        return

    latency_ms = (time.monotonic() - t0) * 1000

    # ── 5. Track usage ──
    _get_ctx_manager().track_usage(provider_name or model_id, tokens_used, request_id)
    if usage_tracker is not None:
        usage_tracker.record(
            provider=provider_name or "unknown",
            model=model_id,
            system=system.value,
            tokens=tokens_used,
            request_id=request_id,
            session_id=session_id,
        )

    # ── 6. Consolidate: record assistant response in memory ──
    response_text = "".join(full_response)
    _write_turn(
        session_id,
        ConversationTurn(role="assistant", content=response_text, timestamp=time.time()),
        onboarding_mode=onboarding_mode,
    )

    # Publish cognitive output event
    _publish_output(
        nervous_system_client,
        source="wui",
        tokens_used=tokens_used,
        model=model_id,
        latency_ms=latency_ms,
        timestamp=time.time(),
    )

    log.info(
        "Chat complete request=%s tokens=%d latency=%.0fms",
        request_id, tokens_used, latency_ms,
    )

    yield StreamChunk(done=True, tokens_used=tokens_used)


# ──────────────────────────────────────────────────────────────────────────────
# Nervous system event publishing helpers
# ──────────────────────────────────────────────────────────────────────────────


def _hash_message(message: str) -> str:
    """Create a short hash of the message for event correlation."""
    import hashlib
    return hashlib.blake2b(message.encode(), digest_size=8).hexdigest()


def _publish_input(
    client: NervousSystemClient_T | None,
    source: str,
    user_id: str,
    message_hash: str,
    timestamp: float,
) -> None:
    """Publish COGNITIVE_INPUT event on user message received."""
    if client is None or not client.is_connected:
        return

    try:
        payload = {
            "source": source,
            "user_id": user_id,
            "message_hash": message_hash,
            "timestamp": timestamp,
        }
        import json
        client.publish(topics.COGNITIVE_INPUT, json.dumps(payload).encode())
    except Exception:
        log.debug("Failed to publish cognitive input event", exc_info=True)


def _publish_output(
    client: NervousSystemClient_T | None,
    source: str,
    tokens_used: int,
    model: str,
    latency_ms: float,
    timestamp: float,
) -> None:
    """Publish COGNITIVE_OUTPUT event on LLM response complete."""
    if client is None or not client.is_connected:
        return

    try:
        payload = {
            "source": source,
            "tokens_used": tokens_used,
            "model": model,
            "latency_ms": latency_ms,
            "timestamp": timestamp,
        }
        import json
        client.publish(topics.COGNITIVE_OUTPUT, json.dumps(payload).encode())
    except Exception:
        log.debug("Failed to publish cognitive output event", exc_info=True)


def _publish_error(
    client: NervousSystemClient_T | None,
    source: str,
    error_type: str,
    message_hash: str,
    timestamp: float,
) -> None:
    """Publish COGNITIVE_ERROR event on chat error."""
    if client is None or not client.is_connected:
        return

    try:
        payload = {
            "source": source,
            "error_type": error_type,
            "message_hash": message_hash,
            "timestamp": timestamp,
        }
        import json
        client.publish(topics.COGNITIVE_ERROR, json.dumps(payload).encode())
    except Exception:
        log.debug("Failed to publish cognitive error event", exc_info=True)
