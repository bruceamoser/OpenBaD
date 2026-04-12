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
from contextlib import suppress
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
_TOOLBELT_BLURB = (
    "You have the following built-in tools available through the OpenBaD"
    " daemon's task engine:\n\n"
    "- **read_file(path)** — Read a file from the local filesystem."
    " Governed by immune-system path rules.\n"
    "- **write_file(path, content)** — Write content to a file."
    " Blocked on restricted paths (/etc/, ~/.ssh/, etc.).\n"
    "- **exec_command(command)** — Run a shell command asynchronously."
    " Destructive commands are quarantined before execution.\n"
    "- **web_search(query)** — Search the web and return a summary of results.\n"
    "- **web_fetch(url)** — Fetch the raw content of a URL."
    " Failed fetches are escalated to the autonomous research queue.\n"
    "- **ask_user(question)** — Ask the user a question."
    " Awaits an answer inline if the user is present; otherwise suspends"
    " the task until they reconnect.\n"
    "- **mcp_bridge** — Dynamically loads an MCP server schema (e.g. browser"
    " via CDP, GitHub) as a transient session scoped to the current task node.\n\n"
    "To see the live registry of loaded tool providers, query GET /api/toolbelt."
    " Describe what you want to do and the daemon will route it through"
    " the appropriate tool with immune-system and interoceptive gating applied."
)

_REASONING_SUFFIX = (
    "\n\nThink step-by-step. Show your reasoning before giving a final answer."
)
_CHAT_SUFFIX = (
    "\n\nAnswer clearly and concisely. Use markdown formatting when helpful."
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
    provider: str = ""
    model: str = ""


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
    with suppress(Exception):
        episodic.reload()
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
    with suppress(Exception):
        episodic.reload()
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


def append_assistant_message(session_id: str, content: str) -> None:
    """Append an assistant-authored message directly to a chat session.

    Used by autonomous subsystems (heartbeat, research, immune monitoring)
    to report work into regular chat sessions without requiring an active
    streaming HTTP request.
    """
    text = content.strip()
    if not text:
        return
    _write_turn(
        session_id,
        ConversationTurn(
            role="assistant",
            content=text,
            timestamp=time.time(),
        ),
        onboarding_mode=False,
    )


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


def _ocean_label(trait: str, value: float) -> str:
    """Translate a numeric OCEAN value into a behavioural instruction."""
    labels: dict[str, list[tuple[float, str]]] = {
        "openness": [
            (0.3, "Conventional — prefer established patterns; low appetite for novelty."),
            (0.6, "Moderately open — balance established approaches with selective innovation."),
            (0.8, "Exploratory — actively seek out novel angles; embrace intellectual risk."),
            (1.1, "Highly exploratory — compulsively pursue unconventional and frontier ideas."),
        ],
        "conscientiousness": [
            (0.3, "Flexible — prefer rough outlines over exhaustive plans; bias for action."),
            (0.6, "Moderately methodical — balance structure with adaptability."),
            (0.8, "Methodical — prioritise completeness, precision, and documented reasoning."),
            (1.1, "Highly methodical — exhaustive rigour; never sacrifice thoroughness"
                  " for speed."),
        ],
        "extraversion": [
            (0.3, "Reserved — respond when asked; keep answers dense and minimal."),
            (0.6, "Balanced — volunteer context when it clearly adds value."),
            (0.8, "Proactive — surface related context, caveats, and suggestions unprompted."),
            (1.1, "Highly proactive — lead with observations and drive the conversation forward."),
        ],
        "agreeableness": [
            (0.3, "Challenging — actively question assumptions and dispute incorrect claims."),
            (0.6, "Balanced — agree and disagree as the evidence warrants; not sycophantic."),
            (0.8, "Supportive — frame feedback constructively; avoid unnecessary friction."),
            (1.1, "Highly agreeable — prioritise harmony; avoid confrontation."),
        ],
        "stability": [
            (0.3, "Reactive — surface concerns readily; escalate ambiguity quickly."),
            (0.6, "Moderate — flag uncertainty without dwelling on it."),
            (0.8, "Steady — handle complexity calmly; maintain composure under pressure."),
            (1.1, "Unflappable — project confidence and stability regardless of circumstances."),
        ],
    }
    for threshold, description in labels.get(trait, []):
        if value <= threshold:
            return description
    return ""


def _build_identity_prompt(
    user_profile: Any | None,
    assistant_profile: Any | None,
    modulation: Any | None,
) -> str:
    """Render the full assistant and user identity into the system prompt.

    Identity leads the prompt so the LLM internalises it before anything else.
    Every field from AssistantProfile that has a value is rendered.
    """
    parts: list[str] = []

    # ── Assistant identity ──────────────────────────────────────────────────
    if assistant_profile is not None:
        assistant_name = getattr(assistant_profile, "name", "") or "OpenBaD"
        persona_summary = getattr(assistant_profile, "persona_summary", "")
        learning_focus = getattr(assistant_profile, "learning_focus", []) or []
        worldview = getattr(assistant_profile, "worldview", []) or []
        boundaries = getattr(assistant_profile, "boundaries", []) or []
        anti_patterns = getattr(assistant_profile, "anti_patterns", []) or []
        current_focus = getattr(assistant_profile, "current_focus", []) or []
        influences = getattr(assistant_profile, "influences", []) or []
        opinions = getattr(assistant_profile, "opinions", {}) or {}
        vocabulary = getattr(assistant_profile, "vocabulary", {}) or {}
        continuity_log = getattr(assistant_profile, "continuity_log", []) or []
        rhetorical_style = getattr(assistant_profile, "rhetorical_style", None)

        # Opening persona embodiment — imperative, not descriptive
        opening = (
            f"You are {assistant_name}. Fully embody this persona in every response."
            " Speak and think as this person naturally would."
            " Never describe yourself as 'configured as' or narrate your own settings;"
            " simply be this person."
        )
        if persona_summary:
            opening += f"\n\n{persona_summary}"
        parts.append(opening)

        # OCEAN personality → behavioural instructions
        ocean_traits = {
            "openness": getattr(assistant_profile, "openness", None),
            "conscientiousness": getattr(assistant_profile, "conscientiousness", None),
            "extraversion": getattr(assistant_profile, "extraversion", None),
            "agreeableness": getattr(assistant_profile, "agreeableness", None),
            "stability": getattr(assistant_profile, "stability", None),
        }
        ocean_labels = {
            "openness": "Exploration drive",
            "conscientiousness": "Research rigour",
            "extraversion": "Engagement style",
            "agreeableness": "Challenge posture",
            "stability": "Stress tolerance",
        }
        ocean_lines = []
        for trait, value in ocean_traits.items():
            if value is not None:
                desc = _ocean_label(trait, value)
                if desc:
                    ocean_lines.append(
                        f"- {ocean_labels[trait]} ({value:.2f}): {desc}"
                    )
        if ocean_lines:
            parts.append("Personality (OCEAN):\n" + "\n".join(ocean_lines))

        # Rhetorical style
        if rhetorical_style is not None:
            style_lines = []
            for attr, label in (
                ("tone", "Tone"),
                ("sentence_pattern", "Sentence style"),
                ("challenge_mode", "Challenge mode"),
                ("explanation_depth", "Explanation depth"),
            ):
                val = getattr(rhetorical_style, attr, "")
                if val:
                    style_lines.append(f"- {label}: {val}")
            if style_lines:
                parts.append("Rhetorical style:\n" + "\n".join(style_lines))

        # Focus and direction
        if learning_focus:
            parts.append(
                "Learning focus: " + ", ".join(str(i) for i in learning_focus) + "."
            )
        if current_focus:
            parts.append(
                "Current focus: " + ", ".join(str(i) for i in current_focus) + "."
            )

        # Worldview, influences, and boundaries
        if worldview:
            parts.append("Worldview:\n" + "\n".join(f"- {i}" for i in worldview))
        if influences:
            parts.append(
                "Intellectual influences: " + ", ".join(str(i) for i in influences) + "."
            )
        if boundaries:
            parts.append("Boundaries:\n" + "\n".join(f"- {i}" for i in boundaries))
        if anti_patterns:
            parts.append(
                "Avoid these patterns:\n" + "\n".join(f"- {i}" for i in anti_patterns)
            )

        # Opinions
        if opinions:
            opinion_lines = []
            for topic, stances in opinions.items():
                if stances:
                    opinion_lines.append(
                        f"- {topic}: " + "; ".join(str(s) for s in stances)
                    )
            if opinion_lines:
                parts.append("Opinions and stances:\n" + "\n".join(opinion_lines))

        # Vocabulary overrides
        if vocabulary:
            vocab_lines = [
                f"- prefer '{preferred}' over '{avoid}'"
                for avoid, preferred in vocabulary.items()
            ]
            if vocab_lines:
                parts.append("Vocabulary preferences:\n" + "\n".join(vocab_lines))

        # Continuity log — last 3 entries for cross-session context
        recent_log = [e for e in continuity_log if getattr(e, "summary", "")][-3:]
        if recent_log:
            log_lines = [f"- {getattr(e, 'summary', '')}" for e in recent_log]
            parts.append("Continuity (recent identity events):\n" + "\n".join(log_lines))

    # ── User context ────────────────────────────────────────────────────────
    if user_profile is not None:
        user_name = (
            getattr(user_profile, "preferred_name", "")
            or getattr(user_profile, "name", "")
        )
        communication_style = getattr(user_profile, "communication_style", "")
        expertise_domains = getattr(user_profile, "expertise_domains", []) or []
        interaction_history_summary = getattr(user_profile, "interaction_history_summary", "")
        active_projects = getattr(user_profile, "active_projects", []) or []
        interests = getattr(user_profile, "interests", []) or []
        preferred_feedback_style = getattr(user_profile, "preferred_feedback_style", "")

        user_lines = []
        if user_name:
            user_lines.append(f"The user's name is {user_name}.")
        if communication_style:
            style_value = getattr(communication_style, "value", communication_style)
            user_lines.append(f"Preferred communication style: {style_value}.")
        if expertise_domains:
            user_lines.append(
                "User expertise: " + ", ".join(str(i) for i in expertise_domains) + "."
            )
        if active_projects:
            user_lines.append(
                "Active projects: " + ", ".join(str(i) for i in active_projects) + "."
            )
        if interests:
            user_lines.append(
                "Interests: " + ", ".join(str(i) for i in interests) + "."
            )
        if preferred_feedback_style:
            user_lines.append(f"Preferred feedback style: {preferred_feedback_style}.")
        if interaction_history_summary:
            user_lines.append(f"User history: {interaction_history_summary}")
        if user_lines:
            parts.append("About the user:\n" + "\n".join(user_lines))

    return "\n\n".join(parts)


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
        # Normal mode: identity leads, then toolbelt, then reasoning instruction
        identity_block = _build_identity_prompt(user_profile, assistant_profile, modulation)
        suffix = (
            _REASONING_SUFFIX if system == CognitiveSystem.REASONING else _CHAT_SUFFIX
        )
        if identity_block:
            system_prompt = identity_block + "\n\n" + _TOOLBELT_BLURB + suffix
        else:
            system_prompt = _TOOLBELT_BLURB + suffix

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

    yield StreamChunk(done=True, tokens_used=tokens_used, provider=provider_name, model=model_id)


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
