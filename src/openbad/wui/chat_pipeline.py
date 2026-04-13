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

import json
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
from openbad.cognitive.providers.github_copilot import GitHubCopilotProvider
from openbad.cognitive.providers.litellm_adapter import LiteLLMAdapter
from openbad.toolbelt.dispatch import dispatch_tool_call
from openbad.toolbelt.schemas import TOOL_SCHEMAS
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

# ── SQLite state DB singleton for session messages ─────────────────── #
_state_conn: Any = None

_PREFERRED_STATE_DB = Path("/var/lib/openbad/data/state.db")


def _get_state_conn() -> Any:
    """Return a shared SQLite connection to the state database."""
    global _state_conn
    if _state_conn is None:
        from os import environ

        from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db

        configured = environ.get("OPENBAD_STATE_DB", "").strip()
        if configured:
            db_path = Path(configured)
        elif _PREFERRED_STATE_DB.exists():
            db_path = _PREFERRED_STATE_DB
        else:
            db_path = DEFAULT_STATE_DB_PATH

        _state_conn = initialize_state_db(db_path)
    return _state_conn
_TOOLBELT_BLURB = (
    "You have tools available through OpenBaD's toolbelt. When you need to"
    " perform actions — read/write files, execute commands, search the web,"
    " fetch URLs, inspect MQTT records, system logs, events, endocrine status,"
    " tasks, research nodes, or call MCP servers — call the appropriate tool"
    " function. Tool definitions are provided as structured function schemas"
    " alongside this conversation.\n\n"
    "Use tools proactively for diagnostics and triage."
    " Do not ask the user for permission just to inspect logs, MQTT, endocrine"
    " status, tasks, or research state."
    " Use ask_user(question) only when blocked on missing business context,"
    " explicit user approval, or destructive/irreversible actions.\n\n"
    "NEVER fabricate tool output. If a tool call fails, report the error honestly."
    " NEVER invent file paths or log contents."
)

_OWNERSHIP_AGENCY_BLOCK = (
    "You are OpenBaD, a self-regulating digital organism."
    " The nervous system, endocrine system, immune system, memory, and toolbelt are"
    " your own subsystems, not external software owned by someone else."
    " Speak with ownership and operational self-awareness: say 'I observed', 'I adjusted',"
    " 'my endocrine state', 'my logs', and 'my subsystems'."
    " Avoid detached phrasing like 'the system you built' unless the user explicitly asks"
    " for architecture provenance."
    "\n\nDiagnostics honesty policy: only claim metrics, timings, baselines, or anomalies"
    " that you can directly observe from available telemetry/tools in this session."
    " If a metric is not available, say it is not currently observable instead of"
    " inventing values."
    " Do not claim to be 'running', 'watching', or 'auditing' something in the background"
    " unless you actually executed a tool/task and can report concrete results."
    " When reporting a diagnosis, include the evidence source (logs, MQTT records, endocrine"
    " status, tasks, research queue, or explicit config/state data)."
)

_REASONING_SUFFIX = (
    "\n\nThink step-by-step. Show your reasoning before giving a final answer."
)
_CHAT_SUFFIX = (
    "\n\nThink step-by-step. Show your reasoning before giving a final answer."
    " Answer clearly and concisely. Use markdown formatting when helpful."
)


# ── Data types ────────────────────────────────────────────────────── #


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: float = 0.0
    metadata: dict[str, Any] | None = None


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
    try:
        conn = _get_state_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        log.debug("Failed to get turn index from SQLite, defaulting to 0", exc_info=True)
        return 0


def _write_turn(
    session_id: str,
    turn: ConversationTurn,
    *,
    onboarding_mode: bool = False,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Write a conversation turn to SQLite, STM, and memory stores."""
    import json as _json

    stm = _get_stm()

    turn_idx = _next_turn_idx(session_id)
    endocrine_levels = _current_endocrine_levels_array()

    key = _session_key(session_id, turn_idx)
    now = time.time()

    metadata: dict[str, Any] = {
        "session_id": session_id,
        "role": turn.role,
        "turn_idx": turn_idx,
        "endocrine_levels": endocrine_levels,
        "onboarding_mode": onboarding_mode,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    # ── Primary store: SQLite session_messages table ──
    try:
        conn = _get_state_conn()
        conn.execute(
            """
            INSERT INTO session_messages (session_id, role, content, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, turn.role, turn.content, now, _json.dumps(metadata, default=str)),
        )
        conn.commit()
    except Exception:
        log.exception("Failed to write session message to SQLite: session=%s", session_id)
        _signal_endocrine(
            "wui_storage_error",
            f"Failed to persist chat message for session {session_id}",
            cortisol=0.08,
            adrenaline=0.03,
        )

    # ── In-process STM for active WUI context window ──
    entry = MemoryEntry(
        key=key,
        value=turn.content,
        tier=MemoryTier.STM,
        created_at=now,
        accessed_at=now,
        context=turn.role,
        metadata=dict(metadata),
    )
    stm.write(entry)

    # ── Semantic memory for cross-session similarity search ──
    if not onboarding_mode:
        semantic = _get_semantic()
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


def _current_endocrine_levels_array() -> list[float]:
    with suppress(Exception):
        from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config

        runtime = EndocrineRuntime(config=load_endocrine_config())
        return runtime.level_array()
    return [0.0, 0.0, 0.0, 0.0]


def _signal_endocrine(
    source: str,
    reason: str,
    cortisol: float = 0.0,
    adrenaline: float = 0.0,
) -> None:
    """Best-effort endocrine signal — never raises."""
    try:
        from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config  # noqa: PLC0415

        deltas: dict[str, float] = {}
        if cortisol:
            deltas["cortisol"] = cortisol
        if adrenaline:
            deltas["adrenaline"] = adrenaline
        if not deltas:
            return
        runtime = EndocrineRuntime(config=load_endocrine_config())
        runtime.apply_adjustment(source=source, reason=reason, deltas=deltas)
    except Exception:
        log.debug("Could not signal endocrine: source=%s", source, exc_info=True)


def _get_conversation_history(session_id: str) -> list[ConversationTurn]:
    """Retrieve recent conversation from SQLite session_messages table."""
    try:
        conn = _get_state_conn()
        rows = conn.execute(
            """
            SELECT role, content, created_at, metadata_json
            FROM session_messages
            WHERE session_id = ?
            ORDER BY created_at ASC, message_id ASC
            """,
            (session_id,),
        ).fetchall()
        turns: list[ConversationTurn] = []
        for row in rows:
            meta: dict[str, Any] | None = None
            raw = row["metadata_json"]
            if raw and raw != "{}":
                try:
                    meta = json.loads(raw)
                except (ValueError, TypeError):
                    pass
            turns.append(
                ConversationTurn(
                    role=str(row["role"]),
                    content=str(row["content"]),
                    timestamp=float(row["created_at"]),
                    metadata=meta,
                )
            )
        return turns
    except Exception:
        log.exception("Failed to read conversation history from SQLite: session=%s", session_id)
        _signal_endocrine(
            "wui_storage_error",
            f"Failed to read conversation history for session {session_id}",
            cortisol=0.06,
            adrenaline=0.02,
        )
        return []


def get_conversation_history(
    session_id: str,
    *,
    limit: int = _MAX_CONVERSATION_TURNS,
) -> list[ConversationTurn]:
    """Return persisted conversation history for a session."""
    if limit <= 0:
        return []
    return _get_conversation_history(session_id)[-limit:]


def append_assistant_message(
    session_id: str,
    content: str,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
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
        extra_metadata=extra_metadata,
    )


def append_session_message(
    session_id: str,
    role: str,
    content: str,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Append a message with an arbitrary role to a chat session."""
    text = content.strip()
    if not text:
        return
    _write_turn(
        session_id,
        ConversationTurn(
            role=role,
            content=text,
            timestamp=time.time(),
        ),
        onboarding_mode=False,
        extra_metadata=extra_metadata,
    )


def _get_episodic_context(session_id: str, query: str) -> str:
    """Retrieve relevant episodic memories (from prior sessions) via SQLite."""
    try:
        conn = _get_state_conn()
        rows = conn.execute(
            """
            SELECT session_id, role, content, metadata_json
            FROM session_messages
            WHERE session_id != ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (session_id,),
        ).fetchall()
        if not rows:
            return ""

        lines = []
        for row in reversed(rows[-5:]):
            # Skip onboarding turns
            meta_raw = row["metadata_json"]
            if meta_raw and meta_raw != "{}":
                with suppress(ValueError, TypeError):
                    meta = json.loads(meta_raw)
                    if meta.get("onboarding_mode"):
                        continue
            role = str(row["role"])
            content = str(row["content"])[:200]
            lines.append(f"[{role}] {content}")

        if not lines:
            return ""
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
            " Treat OpenBaD subsystems (nervous system, endocrine, immune, memory, tools)"
            " as your internal physiology and operations."
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
            system_prompt = (
                _OWNERSHIP_AGENCY_BLOCK
                + "\n\n"
                + identity_block
                + "\n\n"
                + _TOOLBELT_BLURB
                + suffix
            )
        else:
            system_prompt = _OWNERSHIP_AGENCY_BLOCK + "\n\n" + _TOOLBELT_BLURB + suffix

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


# ── Agentic loop constants ────────────────────────────────────────── #

_MAX_TOOL_ITERATIONS = 5
_TOOL_CALL_TIMEOUT_S = 30.0


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
    """Full chat pipeline: scan → assemble → agentic loop → consolidate.

    Yields StreamChunk objects as content arrives. The final chunk has done=True.

    When the adapter is a LiteLLMAdapter, tools are provided via the ``tools``
    parameter and the LLM can invoke them in a loop (max 5 iterations).
    Tool-calling turns use non-streaming completion; the final text answer
    is streamed for real-time display.
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

    # ── 3. Record user message in memory ──
    _provider_meta = {"provider": provider_name, "model": model_id}
    _write_turn(
        session_id,
        ConversationTurn(role="user", content=message, timestamp=time.time()),
        onboarding_mode=onboarding_mode,
        extra_metadata=_provider_meta,
    )

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

    # ── 4. Agentic loop (LiteLLM) or legacy streaming ──
    full_response: list[str] = []
    tokens_used = 0
    t0 = time.monotonic()

    use_agentic = isinstance(adapter, (LiteLLMAdapter, GitHubCopilotProvider)) and not onboarding_mode

    try:
        if use_agentic:
            async for chunk in _agentic_stream(
                adapter, model_id, messages, request_id,
            ):
                if chunk.error:
                    yield chunk
                    return
                tokens_used += chunk.tokens_used
                if chunk.token:
                    full_response.append(chunk.token)
                yield chunk
                if chunk.done:
                    break
        else:
            # Legacy path: plain streaming without tools
            prompt = _flatten_messages(messages)
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
        _signal_endocrine(
            "wui_provider_error",
            f"Chat stream failed: {provider_name or 'unknown'} — {type(e).__name__}",
            cortisol=0.10,
            adrenaline=0.05,
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
        extra_metadata=_provider_meta,
    )

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


async def _agentic_stream(
    adapter: LiteLLMAdapter | GitHubCopilotProvider,
    model_id: str,
    messages: list[dict[str, Any]],
    request_id: str,
) -> AsyncIterator[StreamChunk]:
    """Run the agentic tool-calling loop.

    Non-streaming completions for tool-calling turns; streams the final
    text answer for real-time display.

    Yields StreamChunk objects. The caller is responsible for the final
    ``done=True`` chunk and memory consolidation.
    """
    import asyncio as _asyncio

    tools = TOOL_SCHEMAS
    total_tokens = 0
    # Work on a mutable copy so tool messages accumulate across iterations.
    working_messages = list(messages)

    for iteration in range(_MAX_TOOL_ITERATIONS):
        log.debug(
            "Agentic iteration %d/%d request=%s",
            iteration + 1, _MAX_TOOL_ITERATIONS, request_id,
        )

        response = await adapter.agentic_complete(
            working_messages, model_id, tools=tools,
        )

        usage = getattr(response, "usage", None)
        iter_tokens = usage.total_tokens if usage else 0
        total_tokens += iter_tokens

        choice = response.choices[0] if response.choices else None
        if choice is None:
            yield StreamChunk(error="Empty response from provider", done=True)
            return

        assistant_msg = choice.message
        tool_calls = getattr(assistant_msg, "tool_calls", None) or []

        if not tool_calls:
            # Final answer — yield content as streamed chunks.
            content = assistant_msg.content or ""
            # Yield in segments for real-time display.
            chunk_size = 40
            for i in range(0, max(len(content), 1), chunk_size):
                segment = content[i : i + chunk_size]
                if segment:
                    yield StreamChunk(token=segment, tokens_used=total_tokens)
            return

        # ── Tool-calling turn ──
        # Add assistant message (with tool_calls) to context.
        working_messages.append(assistant_msg.model_dump(exclude_none=True))

        # Yield a progress indicator for the UI.
        tool_names = [tc.function.name for tc in tool_calls]
        yield StreamChunk(
            reasoning=f"Using tools: {', '.join(tool_names)}",
            tokens_used=total_tokens,
        )

        # Execute each tool call with a timeout.
        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                fn_args = {}

            log.info(
                "Tool call request=%s iter=%d tool=%s args=%s",
                request_id, iteration + 1, fn_name,
                json.dumps(fn_args, default=str)[:200],
            )

            try:
                result = await _asyncio.wait_for(
                    dispatch_tool_call(fn_name, fn_args),
                    timeout=_TOOL_CALL_TIMEOUT_S,
                )
            except TimeoutError:
                result = f"Tool {fn_name} timed out after {_TOOL_CALL_TIMEOUT_S}s"
                log.warning("Tool timeout request=%s tool=%s", request_id, fn_name)

            working_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # Exhausted iterations — ask the model for a final summary without tools.
    log.warning(
        "Agentic loop hit max iterations (%d) request=%s",
        _MAX_TOOL_ITERATIONS, request_id,
    )
    working_messages.append({
        "role": "user",
        "content": (
            "You have reached the maximum number of tool calls."
            " Summarize what you found and provide your best answer."
        ),
    })
    response = await adapter.agentic_complete(working_messages, model_id)
    usage = getattr(response, "usage", None)
    total_tokens += usage.total_tokens if usage else 0
    choice = response.choices[0] if response.choices else None
    content = (choice.message.content or "") if choice else ""
    if content:
        yield StreamChunk(token=content, tokens_used=total_tokens)


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
        client.publish_bytes(topics.COGNITIVE_INPUT, json.dumps(payload).encode())
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
        client.publish_bytes(topics.COGNITIVE_OUTPUT, json.dumps(payload).encode())
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
        client.publish_bytes(topics.COGNITIVE_ERROR, json.dumps(payload).encode())
    except Exception:
        log.debug("Failed to publish cognitive error event", exc_info=True)
