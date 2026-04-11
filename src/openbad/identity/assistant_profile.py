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

import yaml

logger = logging.getLogger(__name__)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class AssistantProfile:
    """Agent personality and identity — seeded from config.

    Each OCEAN slider is clamped to [0.0, 1.0].
    """

    name: str = "OpenBaD"
    persona_summary: str = ""
    learning_focus: list[str] = field(default_factory=list)
    openness: float = 0.7
    conscientiousness: float = 0.8
    extraversion: float = 0.5
    agreeableness: float = 0.4
    stability: float = 0.6

    def __post_init__(self) -> None:
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
        msg = "identity.yaml must contain an 'assistant' mapping"
        raise ValueError(msg)

    ocean = data.get("ocean", {})
    if not isinstance(ocean, dict):
        ocean = {}

    return AssistantProfile(
        name=data.get("name", "OpenBaD"),
        persona_summary=data.get("persona_summary", ""),
        learning_focus=data.get("learning_focus", []),
        openness=ocean.get("openness", 0.7),
        conscientiousness=ocean.get("conscientiousness", 0.8),
        extraversion=ocean.get("extraversion", 0.5),
        agreeableness=ocean.get("agreeableness", 0.4),
        stability=ocean.get("stability", 0.6),
    )
