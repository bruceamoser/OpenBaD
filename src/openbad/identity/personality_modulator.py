"""OCEAN personality → endocrine modulation mapping.

Computes modulation factors from OCEAN slider values and injects them
into the endocrine system to shape agent behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.identity.assistant_profile import AssistantProfile


@dataclass
class ModulationFactors:
    """Endocrine modulation factors derived from OCEAN personality values."""

    exploration_budget_multiplier: float
    max_reasoning_depth_multiplier: float
    proactive_suggestion_threshold: float
    challenge_probability: float
    cortisol_decay_multiplier: float


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
        return ModulationFactors(
            exploration_budget_multiplier=0.5 + profile.openness,
            max_reasoning_depth_multiplier=0.5 + profile.conscientiousness,
            proactive_suggestion_threshold=1.0 - profile.extraversion,
            challenge_probability=1.0 - profile.agreeableness,
            cortisol_decay_multiplier=0.5 + profile.stability,
        )
