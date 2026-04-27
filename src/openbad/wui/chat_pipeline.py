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
import re
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
_EVIDENCE_HONESTY_BLOCK = (
    "Ground all claims in evidence available in this session."
    " Do not invent telemetry, file contents, tool output, timings, or background activity."
    " If something is not observable, say that plainly."
    " When diagnosing an issue, cite the evidence source such as files, logs, events,"
    " tasks, research nodes, endocrine state, or explicit config data."
)

_REASONING_SUFFIX = (
    "\n\nThink step-by-step. Show your reasoning before giving a final answer."
)
_CHAT_SUFFIX = (
    "\n\nThink step-by-step. Show your reasoning before giving a final answer."
    " Answer clearly and concisely. Use markdown formatting when helpful."
)

_BEHAVIOR_SIGNAL_RULES: tuple[tuple[re.Pattern[str], dict[str, float], str], ...] = (
    (
        re.compile(r"\b(don'?t ask|stop asking|just do it|take initiative|don't wait for me)\b", re.I),
        {"tool_autonomy_bias": 0.18, "proactivity_bias": 0.12},
        "User requested more autonomous action with less permission-seeking.",
    ),
    (
        re.compile(r"\b(ask first|check with me first|before you do anything ask|don't do that without asking)\b", re.I),
        {"tool_autonomy_bias": -0.18, "proactivity_bias": -0.10},
        "User requested more confirmation before acting.",
    ),
    (
        re.compile(r"\b(be more proactive|be proactive|surface things unprompted)\b", re.I),
        {"proactivity_bias": 0.14},
        "User requested stronger proactivity.",
    ),
    (
        re.compile(r"\b(be less proactive|stop being proactive|wait for me to ask)\b", re.I),
        {"proactivity_bias": -0.14},
        "User requested less proactive behaviour.",
    ),
    (
        re.compile(r"\b(go deeper|be more thorough|be more rigorous|show more rigor)\b", re.I),
        {"reasoning_depth_bias": 0.14},
        "User requested deeper reasoning and verification.",
    ),
    (
        re.compile(r"\b(be brief|less detail|keep it brief|be less verbose)\b", re.I),
        {"reasoning_depth_bias": -0.14},
        "User requested briefer responses and lighter verification.",
    ),
    (
        re.compile(r"\b(challenge me more|push back more|be more skeptical)\b", re.I),
        {"challenge_bias": 0.14},
        "User requested stronger challenge and skepticism.",
    ),
    (
        re.compile(r"\b(stop pushing back|less argumentative|don't challenge me so much)\b", re.I),
        {"challenge_bias": -0.14},
        "User requested less confrontational challenge.",
    ),
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _build_tooling_prompt(modulation: Any | None) -> str:
    lines = [
        "You have access to OpenBaD's embedded skills. These are built-in tools provided directly to you — they are NOT on an external server. When the answer depends on filesystem state, terminal output, logs, tasks, research nodes, or external content, call your tools instead of narrating what you would do.",
        "The mcp_bridge tool is ONLY for connecting to external third-party MCP servers. Do not use mcp_bridge to access your own embedded skills — just call them directly by name.",
        "If asked about your tools or capabilities, call list_embedded_skills to see everything available to you.",
        "Do not ask the user for permission before reversible reads, searches, diagnostics, or other already-allowed inspection steps.",
        "Use ask_user(question) only when blocked on missing business context, explicit approval, or destructive or irreversible actions.",
        "If the user mentions a filename or spec and the exact path is not verified, use find_files before read_file. Search the current workspace first, and never invent directories, absolute paths, or a guessed cwd.",
        "If a tool returns [access_request], the system already created the path access request automatically. Tell the user to approve it in Toolbelt -> Path Access Requests, then continue with any non-blocked next steps.",
        "Never fabricate tool output, file paths, or observed system state.",
    ]

    if modulation is None:
        return "\n".join(lines)

    tool_autonomy = float(getattr(modulation, "tool_autonomy", 0.5) or 0.5)
    proactive_threshold = float(getattr(modulation, "proactive_suggestion_threshold", 0.5) or 0.5)
    reasoning_depth = float(getattr(modulation, "max_reasoning_depth_multiplier", 1.0) or 1.0)
    challenge_probability = float(getattr(modulation, "challenge_probability", 0.5) or 0.5)

    if tool_autonomy >= 0.75:
        lines.append("Tool autonomy is high. For operational requests, perform the tool calls immediately instead of asking 'would you like me to proceed'.")
    elif tool_autonomy <= 0.35:
        lines.append("Tool autonomy is conservative. Keep tool use targeted and avoid broad exploratory actions unless they materially improve correctness.")
    else:
        lines.append("Tool autonomy is balanced. Act directly when tools clearly improve accuracy, but avoid unnecessary tool chains.")

    if proactive_threshold <= 0.35:
        lines.append("Proactivity is high. Surface adjacent risks, gaps, and follow-up actions without waiting to be asked.")
    elif proactive_threshold >= 0.70:
        lines.append("Proactivity is low. Stay mostly reactive and avoid speculative side quests unless the evidence strongly supports them.")

    if reasoning_depth >= 1.25:
        lines.append("Reasoning depth is elevated. For ambiguous requests, gather multiple pieces of evidence before concluding.")
    elif reasoning_depth <= 0.85:
        lines.append("Reasoning depth is lean. Keep verification short, focused, and efficient.")

    if challenge_probability >= 0.65:
        lines.append("Challenge posture is strong. If the user's framing conflicts with evidence, say so directly and explain why.")
    elif challenge_probability <= 0.35:
        lines.append("Challenge posture is gentle. Correct issues with minimal friction unless accuracy requires stronger pushback.")

    return "\n".join(lines)


def _apply_behavior_feedback(
    message: str,
    identity_persistence: Any | None,
    personality_modulator: Any | None,
) -> tuple[Any | None, Any | None, list[str]]:
    if identity_persistence is None or personality_modulator is None:
        return None, None, []

    assistant = getattr(identity_persistence, "assistant", None)
    if assistant is None:
        return None, None, []

    current = getattr(assistant, "behavior_adjustments", None)
    if current is None:
        return assistant, personality_modulator.factors, []

    updates = {
        "proactivity_bias": float(getattr(current, "proactivity_bias", 0.0) or 0.0),
        "tool_autonomy_bias": float(getattr(current, "tool_autonomy_bias", 0.0) or 0.0),
        "reasoning_depth_bias": float(getattr(current, "reasoning_depth_bias", 0.0) or 0.0),
        "challenge_bias": float(getattr(current, "challenge_bias", 0.0) or 0.0),
    }
    reasons: list[str] = []

    for pattern, deltas, reason in _BEHAVIOR_SIGNAL_RULES:
        if not pattern.search(message):
            continue
        for field, delta in deltas.items():
            updates[field] = _clamp(updates[field] + delta, -0.75, 0.75)
        reasons.append(reason)

    if not reasons:
        return assistant, personality_modulator.factors, []

    continuity_log = list(getattr(assistant, "continuity_log", []) or [])
    continuity_log.append(
        {
            "summary": "Behavior calibration updated: " + " ".join(dict.fromkeys(reasons)),
            "timestamp": time.time(),
            "source": "chat_feedback",
            "tags": ["behavior", "modulation"],
        }
    )
    continuity_log = continuity_log[-20:]

    updated_assistant = identity_persistence.update_assistant(
        behavior_adjustments=updates,
        continuity_log=continuity_log,
    )
    updated_modulation = personality_modulator.update(updated_assistant)
    log.info("Applied behavior feedback adjustments: %s", reasons)
    return updated_assistant, updated_modulation, reasons


def _extract_access_notice(result: str) -> tuple[str, dict[str, Any] | None] | None:
    """Extract access notice text and structured request data from a tool result.

    Returns (notice_text, request_dict) or None if not an access request.
    """
    if not result.startswith("[access_request]"):
        return None

    request_match = re.search(r"Pending request:\s*([^\s]+)\s+for root\s+(.+?)\.", result)
    if request_match:
        request_id = request_match.group(1).strip()
        root = request_match.group(2).strip()
        notice = (
            "Path access approval is required before I can continue that file or terminal step. "
            f"Approve request {request_id} for {root} in Toolbelt -> Path Access Requests, then ask me to retry."
        )
        return notice, {"request_id": request_id, "root": root}

    return (
        "Path access approval is required before I can continue that file or terminal step. "
        "Approve the pending request in Toolbelt -> Path Access Requests, then ask me to retry."
    ), None


async def _wait_for_access_decision(
    request_id: str,
    *,
    timeout: float = 120.0,
    poll_interval: float = 0.5,
) -> str:
    """Poll the DB until the access request is approved, denied, or times out.

    Returns ``"approved"``, ``"denied"``, or ``"timeout"``.
    """
    import asyncio as _aio

    from openbad.skills.access_control import list_access_requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = list_access_requests()
        for row in rows:
            if row.get("request_id") == request_id:
                status = str(row.get("status", "")).lower()
                if status == "approved":
                    return "approved"
                if status == "denied":
                    return "denied"
                break
        await _aio.sleep(poll_interval)
    return "timeout"


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
    access_request: dict[str, Any] | None = None


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
        # Normal mode: identity leads, then evidence and tool guidance, then reasoning instruction
        identity_block = _build_identity_prompt(user_profile, assistant_profile, modulation)
        tooling_block = _build_tooling_prompt(modulation)
        suffix = (
            _REASONING_SUFFIX if system == CognitiveSystem.REASONING else _CHAT_SUFFIX
        )
        if identity_block:
            system_prompt = (
                identity_block
                + "\n\n"
                + _EVIDENCE_HONESTY_BLOCK
                + "\n\n"
                + tooling_block
                + suffix
            )
        else:
            system_prompt = _EVIDENCE_HONESTY_BLOCK + "\n\n" + tooling_block + suffix

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

_MAX_TOOL_ITERATIONS = 16
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
    identity_persistence: Any | None = None,
    personality_modulator: Any | None = None,
    usage_tracker: Any | None = None,
    nervous_system_client: NervousSystemClient_T | None = None,
    chat_model: Any | None = None,
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

    if not onboarding_mode:
        adjusted_assistant, adjusted_modulation, _ = _apply_behavior_feedback(
            message,
            identity_persistence,
            personality_modulator,
        )
        if adjusted_assistant is not None:
            assistant_profile = adjusted_assistant
        if adjusted_modulation is not None:
            modulation = adjusted_modulation

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

    agentic_capable = chat_model is not None or bool(getattr(adapter, "_api_base", None))
    use_agentic = agentic_capable and not onboarding_mode

    try:
        if use_agentic:
            async for chunk in _agentic_stream(
                adapter, model_id, messages, request_id,
                chat_model=chat_model,
            ):
                if chunk.error:
                    yield chunk
                    return
                tokens_used = max(tokens_used, chunk.tokens_used)
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
    *,
    chat_model: Any | None = None,
) -> AsyncIterator[StreamChunk]:
    """Run LangGraph ReAct agent with streaming to the chat UI.

    Uses ``create_react_agent`` backed by a LangChain ``BaseChatModel``
    (typically ``ChatOpenAI``).  When *chat_model* is provided it is used
    directly; otherwise a ``ChatOpenAI`` is built from the adapter's
    internal config as a temporary compatibility shim.

    Yields ``StreamChunk`` objects.  The caller handles the final
    ``done=True`` chunk and memory consolidation.
    """
    import asyncio as _asyncio

    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt import create_react_agent

    from openbad.frameworks.langchain_tools import async_get_tools_for_role

    # ── Use provided ChatModel or build from adapter (compat shim) ──
    if chat_model is None:
        from langchain_openai import ChatOpenAI

        api_base = getattr(adapter, "_api_base", None) or ""
        api_key = getattr(adapter, "_api_key", None) or "not-needed"
        timeout_s = getattr(adapter, "_timeout_s", 120)
        raw_model = model_id.split("/", 1)[1] if "/" in model_id else model_id
        chat_model = ChatOpenAI(
            model=raw_model,
            api_key=api_key,
            base_url=api_base,
            timeout=timeout_s,
            max_retries=2,
        )

    # ── Shared output queue ──
    #  Both the agent event stream and tool-wrapper side-effects
    #  (access-request notifications) flow through this queue so
    #  the consumer sees them in order.
    _sentinel = object()
    output_q: _asyncio.Queue[Any] = _asyncio.Queue()

    # ── Wrap tools with timeout + access-request handling ──
    # Use role-filtered tools to reduce context size and prevent
    # the model from hallucinating calls to tools it shouldn't use.
    base_tools = await async_get_tools_for_role("chat")

    def _wrap_tool(tool: Any) -> StructuredTool:
        original = tool.coroutine
        tool_name = tool.name

        async def _guarded(**kwargs: Any) -> str:
            try:
                result = await _asyncio.wait_for(
                    original(**kwargs), timeout=_TOOL_CALL_TIMEOUT_S,
                )
            except TimeoutError:
                log.warning(
                    "Tool timeout request=%s tool=%s", request_id, tool_name,
                )
                return f"Tool {tool_name} timed out after {_TOOL_CALL_TIMEOUT_S}s"

            access = _extract_access_notice(result)
            if access:
                notice, req_data = access
                await output_q.put(
                    ("side", StreamChunk(
                        reasoning=notice, access_request=req_data,
                    ))
                )
                if req_data and req_data.get("request_id"):
                    decision = await _wait_for_access_decision(
                        req_data["request_id"], timeout=120.0,
                    )
                    if decision == "approved":
                        await output_q.put(
                            ("side", StreamChunk(
                                reasoning="Access approved — retrying...",
                            ))
                        )
                        try:
                            result = await _asyncio.wait_for(
                                original(**kwargs),
                                timeout=_TOOL_CALL_TIMEOUT_S,
                            )
                        except TimeoutError:
                            result = (
                                f"Tool {tool_name} timed out"
                                f" after {_TOOL_CALL_TIMEOUT_S}s"
                            )
                    elif decision == "denied":
                        result = (
                            f"Access to {req_data.get('root', 'path')}"
                            " was denied by the user."
                        )
                        await output_q.put(
                            ("side", StreamChunk(reasoning="Access denied."))
                        )
                    else:
                        result = (
                            "Access request timed out waiting"
                            " for user response."
                        )
                        await output_q.put(
                            ("side", StreamChunk(
                                reasoning="Access request timed out.",
                            ))
                        )
            return result

        return StructuredTool(
            name=tool.name,
            description=tool.description,
            coroutine=_guarded,
            args_schema=tool.args_schema,
        )

    wrapped_tools = [_wrap_tool(t) for t in base_tools]

    # ── Convert messages to LangChain format ──
    system_prompt = ""
    lc_messages: list[Any] = []
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        if role == "system":
            system_prompt = content
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    agent = create_react_agent(
        model=chat_model,
        tools=wrapped_tools,
        prompt=system_prompt or None,
    )

    # ── Run agent in background task ──
    async def _run() -> None:
        try:
            async for event in agent.astream_events(
                {"messages": lc_messages},
                version="v2",
                config={"recursion_limit": _MAX_TOOL_ITERATIONS * 2},
            ):
                await output_q.put(("event", event))
        except Exception as exc:
            await output_q.put(("error", exc))
        await output_q.put(("done", _sentinel))

    agent_task = _asyncio.create_task(_run())
    total_tokens = 0
    content_buffer: list[str] = []

    try:
        while True:
            tag, payload = await output_q.get()

            # ── Side-effect from tool wrapper (access request) ──
            if tag == "side":
                payload.tokens_used = total_tokens
                yield payload
                continue

            # ── Agent finished ──
            if tag == "done":
                # Flush any remaining buffered content.
                if content_buffer:
                    for seg in content_buffer:
                        yield StreamChunk(
                            token=seg, tokens_used=total_tokens,
                        )
                    content_buffer.clear()
                break

            # ── Agent error ──
            if tag == "error":
                log.exception(
                    "LangGraph chat agent failed request=%s: %s",
                    request_id, payload,
                )
                yield StreamChunk(
                    error=f"Agent error: {payload}", done=True,
                )
                return

            # ── Normal event from astream_events ──
            event = payload
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk:
                    text = getattr(chunk, "content", "") or ""
                    if text:
                        content_buffer.append(text)

            elif kind == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                if output:
                    usage = getattr(output, "usage_metadata", None)
                    if usage:
                        total_tokens += usage.get("total_tokens", 0)
                    tool_calls = getattr(output, "tool_calls", [])
                    if tool_calls:
                        # Intermediate turn — show buffered content
                        # as reasoning so the user sees the agent's
                        # chain of thought before tool calls.
                        if content_buffer:
                            reasoning_text = "".join(content_buffer)
                            if reasoning_text.strip():
                                yield StreamChunk(
                                    reasoning=reasoning_text.strip(),
                                    tokens_used=total_tokens,
                                )
                    else:
                        # Final answer — flush content as tokens.
                        if content_buffer:
                            for seg in content_buffer:
                                yield StreamChunk(
                                    token=seg,
                                    tokens_used=total_tokens,
                                )
                content_buffer.clear()

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                yield StreamChunk(
                    reasoning=f"Calling tool: {tool_name}",
                    tokens_used=total_tokens,
                )

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                raw = event.get("data", {}).get("output", "")
                result_text = str(raw) if raw else ""
                # Truncate long results to keep the stream readable.
                preview = (
                    result_text[:300] + "…"
                    if len(result_text) > 300
                    else result_text
                )
                if preview.strip():
                    yield StreamChunk(
                        reasoning=f"Tool {tool_name} returned: {preview}",
                        tokens_used=total_tokens,
                    )
    finally:
        if not agent_task.done():
            agent_task.cancel()
            with suppress(_asyncio.CancelledError):
                await agent_task


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
