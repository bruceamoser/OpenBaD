"""AssistantProfile with OCEAN personality sliders and config seed.

The OCEAN model maps to agent behaviours:
- **Openness** → Exploration Drive
- **Conscientiousness** → Research Rigor
- **Extraversion** → Engagement Style
- **Agreeableness** → Challenge Posture
- **Stability** → Stress Tolerance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clamp_signed(value: float, limit: float = 0.75) -> float:
    limit = abs(float(limit))
    return max(-limit, min(limit, float(value)))


def _list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _dict_of_str(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _dict_of_str_list(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _list_of_str(item) for key, item in value.items() if str(key).strip()}


@dataclass
class RhetoricalStyle:
    """Structured communication style directives for the assistant."""

    tone: str = "direct"
    sentence_pattern: str = "concise"
    challenge_mode: str = "steel-man first"
    explanation_depth: str = "balanced"


@dataclass
class BehaviorAdjustments:
    """Runtime-tunable behaviour offsets persisted in the assistant entity."""

    proactivity_bias: float = 0.0
    tool_autonomy_bias: float = 0.0
    reasoning_depth_bias: float = 0.0
    challenge_bias: float = 0.0

    def __post_init__(self) -> None:
        self.proactivity_bias = _clamp_signed(self.proactivity_bias)
        self.tool_autonomy_bias = _clamp_signed(self.tool_autonomy_bias)
        self.reasoning_depth_bias = _clamp_signed(self.reasoning_depth_bias)
        self.challenge_bias = _clamp_signed(self.challenge_bias)


@dataclass
class ContinuityEntry:
    """A durable cross-session identity event or decision."""

    summary: str
    timestamp: float = 0.0
    source: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.summary = str(self.summary).strip()
        self.source = str(self.source).strip()
        self.timestamp = float(self.timestamp or 0.0)
        self.tags = _list_of_str(self.tags)


def _coerce_rhetorical_style(value: Any) -> RhetoricalStyle:
    if isinstance(value, RhetoricalStyle):
        return value
    if isinstance(value, dict):
        return RhetoricalStyle(
            tone=str(value.get("tone", "direct")),
            sentence_pattern=str(
                value.get("sentence_pattern", "concise"),
            ),
            challenge_mode=str(
                value.get("challenge_mode", "steel-man first"),
            ),
            explanation_depth=str(
                value.get("explanation_depth", "balanced"),
            ),
        )
    return RhetoricalStyle()


def _coerce_behavior_adjustments(value: Any) -> BehaviorAdjustments:
    if isinstance(value, BehaviorAdjustments):
        return value
    if isinstance(value, dict):
        return BehaviorAdjustments(
            proactivity_bias=float(value.get("proactivity_bias", 0.0) or 0.0),
            tool_autonomy_bias=float(value.get("tool_autonomy_bias", 0.0) or 0.0),
            reasoning_depth_bias=float(value.get("reasoning_depth_bias", 0.0) or 0.0),
            challenge_bias=float(value.get("challenge_bias", 0.0) or 0.0),
        )
    return BehaviorAdjustments()


def _coerce_continuity_entries(value: Any) -> list[ContinuityEntry]:
    if not isinstance(value, list):
        return []
    entries: list[ContinuityEntry] = []
    for item in value:
        if isinstance(item, ContinuityEntry):
            entries.append(item)
        elif isinstance(item, dict) and item.get("summary"):
            entries.append(ContinuityEntry(**item))
    return entries


@dataclass
class AssistantProfile:
    """Agent personality and identity — seeded from config.

    Each OCEAN slider is clamped to [0.0, 1.0].
    """

    name: str = "OpenBaD"
    persona_summary: str = ""
    learning_focus: list[str] = field(default_factory=list)
    worldview: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    opinions: dict[str, list[str]] = field(default_factory=dict)
    vocabulary: dict[str, str] = field(default_factory=dict)
    rhetorical_style: RhetoricalStyle = field(default_factory=RhetoricalStyle)
    behavior_adjustments: BehaviorAdjustments = field(default_factory=BehaviorAdjustments)
    influences: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    current_focus: list[str] = field(default_factory=list)
    continuity_log: list[ContinuityEntry] = field(default_factory=list)
    openness: float = 0.7
    conscientiousness: float = 0.8
    extraversion: float = 0.5
    agreeableness: float = 0.4
    stability: float = 0.6

    def __post_init__(self) -> None:
        self.persona_summary = str(self.persona_summary)
        self.learning_focus = _list_of_str(self.learning_focus)
        self.worldview = _list_of_str(self.worldview)
        self.boundaries = _list_of_str(self.boundaries)
        self.opinions = _dict_of_str_list(self.opinions)
        self.vocabulary = _dict_of_str(self.vocabulary)
        self.rhetorical_style = _coerce_rhetorical_style(self.rhetorical_style)
        self.behavior_adjustments = _coerce_behavior_adjustments(self.behavior_adjustments)
        self.influences = _list_of_str(self.influences)
        self.anti_patterns = _list_of_str(self.anti_patterns)
        self.current_focus = _list_of_str(self.current_focus)
        self.continuity_log = _coerce_continuity_entries(self.continuity_log)
        self.openness = _clamp(self.openness)
        self.conscientiousness = _clamp(self.conscientiousness)
        self.extraversion = _clamp(self.extraversion)
        self.agreeableness = _clamp(self.agreeableness)
        self.stability = _clamp(self.stability)


def load_assistant_profile(path: str | Path) -> AssistantProfile:
    """Load an :class:`AssistantProfile` from a YAML file.

    Expects an ``assistant:`` top-level key.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = raw.get("assistant")
    if not isinstance(data, dict):
        logger.warning("identity.yaml is missing 'assistant'; using default assistant profile")
        data = {}

    ocean = data.get("ocean", {})
    if not isinstance(ocean, dict):
        ocean = {}

    return AssistantProfile(
        name=data.get("name", "OpenBaD"),
        persona_summary=data.get("persona_summary", ""),
        learning_focus=data.get("learning_focus", []),
        worldview=data.get("worldview", []),
        boundaries=data.get("boundaries", []),
        opinions=data.get("opinions", {}),
        vocabulary=data.get("vocabulary", {}),
        rhetorical_style=data.get("rhetorical_style", {}),
        behavior_adjustments=data.get("behavior_adjustments", {}),
        influences=data.get("influences", []),
        anti_patterns=data.get("anti_patterns", []),
        current_focus=data.get("current_focus", []),
        continuity_log=data.get("continuity_log", []),
        openness=ocean.get("openness", 0.7),
        conscientiousness=ocean.get("conscientiousness", 0.8),
        extraversion=ocean.get("extraversion", 0.5),
        agreeableness=ocean.get("agreeableness", 0.4),
        stability=ocean.get("stability", 0.6),
    )
