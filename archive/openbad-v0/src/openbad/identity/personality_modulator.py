"""OCEAN personality → endocrine modulation mapping.

Computes modulation factors from OCEAN slider values and injects them
into the endocrine system to shape agent behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.identity.assistant_profile import AssistantProfile


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


@dataclass
class ModulationFactors:
    """Endocrine modulation factors derived from OCEAN personality values."""

    exploration_budget_multiplier: float
    max_reasoning_depth_multiplier: float
    proactive_suggestion_threshold: float
    challenge_probability: float
    cortisol_decay_multiplier: float
    tool_autonomy: float
    response_tone: str
    explanation_depth: str
    disagreement_style: str
    anti_pattern_guard: list[str]


class PersonalityModulator:
    """Maps OCEAN personality sliders to endocrine modulation factors.

    Formulas:
    - Openness → exploration_budget_multiplier = 0.5 + O
    - Conscientiousness → max_reasoning_depth_multiplier = 0.5 + C
    - Extraversion → proactive_suggestion_threshold = 1.0 - E
    - Agreeableness → challenge_probability = 1.0 - A
    - Stability → cortisol_decay_multiplier = 0.5 + S
    """

    def __init__(self, profile: AssistantProfile) -> None:
        self._profile = profile
        self._factors = self._compute(profile)

    @property
    def factors(self) -> ModulationFactors:
        return self._factors

    def update(self, profile: AssistantProfile) -> ModulationFactors:
        """Recalculate modulation factors from an updated profile."""
        self._profile = profile
        self._factors = self._compute(profile)
        return self._factors

    @staticmethod
    def _compute(profile: AssistantProfile) -> ModulationFactors:
        adjustments = profile.behavior_adjustments
        proactivity = _clamp(profile.extraversion + adjustments.proactivity_bias, 0.0, 1.0)
        reasoning_depth = _clamp(profile.conscientiousness + adjustments.reasoning_depth_bias, 0.0, 1.0)
        challenge_probability = _clamp((1.0 - profile.agreeableness) + adjustments.challenge_bias, 0.0, 1.0)
        tool_autonomy = _clamp(
            (
                profile.openness * 0.35
                + profile.conscientiousness * 0.25
                + profile.extraversion * 0.20
                + (1.0 - profile.agreeableness) * 0.10
                + profile.stability * 0.10
            )
            + adjustments.tool_autonomy_bias,
            0.0,
            1.0,
        )
        return ModulationFactors(
            exploration_budget_multiplier=0.5 + profile.openness,
            max_reasoning_depth_multiplier=_clamp(0.5 + reasoning_depth, 0.5, 1.75),
            proactive_suggestion_threshold=_clamp(1.0 - proactivity, 0.0, 1.0),
            challenge_probability=challenge_probability,
            cortisol_decay_multiplier=0.5 + profile.stability,
            tool_autonomy=tool_autonomy,
            response_tone=profile.rhetorical_style.tone,
            explanation_depth=profile.rhetorical_style.explanation_depth,
            disagreement_style=profile.rhetorical_style.challenge_mode,
            anti_pattern_guard=list(profile.anti_patterns),
        )
