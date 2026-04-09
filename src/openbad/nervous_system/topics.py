"""MQTT topic namespace for OpenBaD inter-module communication.

Topic Naming Conventions
========================
- All topics are rooted under ``agent/``.
- Hierarchy uses ``/`` as the level separator (standard MQTT).
- Static segments are lower-case kebab-style identifiers.
- Dynamic segments are represented as ``{placeholder}`` in template strings
  and substituted at runtime via :func:`topic_for`.
- Wildcard subscriptions follow MQTT v5 rules:
  - ``+`` matches exactly one level  (e.g. ``agent/telemetry/+``)
  - ``#`` matches zero or more levels (e.g. ``agent/#``)
- No trailing slashes.  No leading slashes.
- Characters restricted to ``[a-z0-9/_{}+-]`` (MQTT UTF-8 safe subset).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Telemetry (interoception → endocrine / dashboard)
# ---------------------------------------------------------------------------
TELEMETRY_CPU = "agent/telemetry/cpu"
TELEMETRY_MEMORY = "agent/telemetry/memory"
TELEMETRY_DISK = "agent/telemetry/disk"
TELEMETRY_NETWORK = "agent/telemetry/network"
TELEMETRY_TOKENS = "agent/telemetry/tokens"

# Wildcard: subscribe to all telemetry
TELEMETRY_ALL = "agent/telemetry/+"

# ---------------------------------------------------------------------------
# Reflex arc (FSM triggers and results)
# ---------------------------------------------------------------------------
REFLEX_TRIGGER = "agent/reflex/{reflex_id}/trigger"
REFLEX_RESULT = "agent/reflex/{reflex_id}/result"
REFLEX_STATE = "agent/reflex/state"

# Wildcard: all reflex traffic
REFLEX_ALL = "agent/reflex/#"

# ---------------------------------------------------------------------------
# Sensory inputs
# ---------------------------------------------------------------------------
SENSORY_VISION = "agent/sensory/vision/{source_id}"
SENSORY_VISION_PARSED = "agent/sensory/vision/{source_id}/parsed"
SENSORY_AUDIO = "agent/sensory/audio/{source_id}"
SENSORY_AUDIO_TTS_COMPLETE = "agent/sensory/audio/tts/complete"
SENSORY_ATTENTION_TRIGGER = "agent/reflex/attention/trigger"

# Wildcard: all sensory data
SENSORY_ALL = "agent/sensory/#"
# Wildcard: all vision data
SENSORY_VISION_ALL = "agent/sensory/vision/#"
# Wildcard: all audio data
SENSORY_AUDIO_ALL = "agent/sensory/audio/#"

# ---------------------------------------------------------------------------
# Cognitive (System 2 escalation interface)
# ---------------------------------------------------------------------------
COGNITIVE_ESCALATION = "agent/cognitive/escalation"
COGNITIVE_RESULT = "agent/cognitive/result"
COGNITIVE_REQUEST = "agent/cognitive/request"
COGNITIVE_RESPONSE = "agent/cognitive/response"
COGNITIVE_HEALTH = "agent/cognitive/health"
COGNITIVE_CONTEXT = "agent/cognitive/context"

# Wildcard: all cognitive traffic
COGNITIVE_ALL = "agent/cognitive/#"

# ---------------------------------------------------------------------------
# Immune system
# ---------------------------------------------------------------------------
IMMUNE_SCAN = "agent/immune/scan"
IMMUNE_THREAT = "agent/immune/threat"
IMMUNE_ALERT = "agent/immune/alert"
IMMUNE_QUARANTINE = "agent/immune/quarantine"
IMMUNE_CLEARED = "agent/immune/cleared"

# Wildcard: all immune events
IMMUNE_ALL = "agent/immune/+"

# ---------------------------------------------------------------------------
# Endocrine (hormone channels)
# ---------------------------------------------------------------------------
ENDOCRINE = "agent/endocrine/{hormone}"
ENDOCRINE_CORTISOL = "agent/endocrine/cortisol"
ENDOCRINE_ADRENALINE = "agent/endocrine/adrenaline"
ENDOCRINE_ENDORPHIN = "agent/endocrine/endorphin"

# Wildcard: all endocrine events
ENDOCRINE_ALL = "agent/endocrine/+"

# ---------------------------------------------------------------------------
# Memory subsystem
# ---------------------------------------------------------------------------
MEMORY_STM_WRITE = "agent/memory/stm/write"
MEMORY_LTM_CONSOLIDATE = "agent/memory/ltm/consolidate"

# Wildcard: all memory events
MEMORY_ALL = "agent/memory/#"

# ---------------------------------------------------------------------------
# Sleep / maintenance cycles
# ---------------------------------------------------------------------------
SLEEP = "agent/sleep/{phase}"
SLEEP_ALL = "agent/sleep/+"

# ---------------------------------------------------------------------------
# Proprioception (tool / MCP server awareness)
# ---------------------------------------------------------------------------
PROPRIOCEPTION_HEARTBEAT = "agent/proprioception/{tool_id}/heartbeat"
PROPRIOCEPTION_ALL = "agent/proprioception/#"


# ---------------------------------------------------------------------------
# Helper: resolve topic templates
# ---------------------------------------------------------------------------
def topic_for(template: str, /, **kwargs: str) -> str:
    """Substitute placeholders in a topic template.

    >>> topic_for(REFLEX_TRIGGER, reflex_id="thermal-throttle")
    'agent/reflex/thermal-throttle/trigger'
    """
    return template.format(**kwargs)


# ---------------------------------------------------------------------------
# All static (non-template) topics for programmatic enumeration
# ---------------------------------------------------------------------------
STATIC_TOPICS: tuple[str, ...] = (
    TELEMETRY_CPU,
    TELEMETRY_MEMORY,
    TELEMETRY_DISK,
    TELEMETRY_NETWORK,
    TELEMETRY_TOKENS,
    REFLEX_STATE,
    COGNITIVE_ESCALATION,
    COGNITIVE_RESULT,
    IMMUNE_SCAN,
    IMMUNE_THREAT,
    IMMUNE_ALERT,
    IMMUNE_QUARANTINE,
    IMMUNE_CLEARED,
    ENDOCRINE_CORTISOL,
    ENDOCRINE_ADRENALINE,
    ENDOCRINE_ENDORPHIN,
    MEMORY_STM_WRITE,
    MEMORY_LTM_CONSOLIDATE,
    SENSORY_AUDIO_TTS_COMPLETE,
    SENSORY_ATTENTION_TRIGGER,
)

TEMPLATE_TOPICS: tuple[str, ...] = (
    REFLEX_TRIGGER,
    REFLEX_RESULT,
    SENSORY_VISION,
    SENSORY_VISION_PARSED,
    SENSORY_AUDIO,
    ENDOCRINE,
    SLEEP,
    PROPRIOCEPTION_HEARTBEAT,
)

WILDCARD_TOPICS: tuple[str, ...] = (
    TELEMETRY_ALL,
    REFLEX_ALL,
    SENSORY_ALL,
    SENSORY_VISION_ALL,
    SENSORY_AUDIO_ALL,
    IMMUNE_ALL,
    ENDOCRINE_ALL,
    MEMORY_ALL,
    SLEEP_ALL,
    PROPRIOCEPTION_ALL,
)
