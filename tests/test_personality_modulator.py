"""Tests for OCEAN personality → endocrine modulation mapping (#243)."""

from __future__ import annotations

import pytest

from openbad.identity.assistant_profile import AssistantProfile
from openbad.identity.personality_modulator import (
    PersonalityModulator,
)


class TestModulationFactors:
    def test_default_ocean(self) -> None:
        """Default OCEAN: O=0.7, C=0.8, E=0.5, A=0.4, S=0.6."""
        p = AssistantProfile()
        m = PersonalityModulator(p)
        f = m.factors
        assert f.exploration_budget_multiplier == pytest.approx(1.2)
        assert f.max_reasoning_depth_multiplier == pytest.approx(1.3)
        assert f.proactive_suggestion_threshold == pytest.approx(0.5)
        assert f.challenge_probability == pytest.approx(0.6)
        assert f.cortisol_decay_multiplier == pytest.approx(1.1)
        assert f.response_tone == "direct"
        assert f.explanation_depth == "balanced"
        assert f.disagreement_style == "steel-man first"
        assert f.anti_pattern_guard == []

    def test_openness_zero(self) -> None:
        p = AssistantProfile(openness=0.0)
        f = PersonalityModulator(p).factors
        assert f.exploration_budget_multiplier == pytest.approx(0.5)

    def test_openness_one(self) -> None:
        p = AssistantProfile(openness=1.0)
        f = PersonalityModulator(p).factors
        assert f.exploration_budget_multiplier == pytest.approx(1.5)

    def test_openness_mid(self) -> None:
        p = AssistantProfile(openness=0.5)
        f = PersonalityModulator(p).factors
        assert f.exploration_budget_multiplier == pytest.approx(1.0)

    def test_conscientiousness_zero(self) -> None:
        p = AssistantProfile(conscientiousness=0.0)
        f = PersonalityModulator(p).factors
        assert f.max_reasoning_depth_multiplier == pytest.approx(0.5)

    def test_conscientiousness_one(self) -> None:
        p = AssistantProfile(conscientiousness=1.0)
        f = PersonalityModulator(p).factors
        assert f.max_reasoning_depth_multiplier == pytest.approx(1.5)

    def test_conscientiousness_mid(self) -> None:
        p = AssistantProfile(conscientiousness=0.5)
        f = PersonalityModulator(p).factors
        assert f.max_reasoning_depth_multiplier == pytest.approx(1.0)

    def test_extraversion_zero(self) -> None:
        p = AssistantProfile(extraversion=0.0)
        f = PersonalityModulator(p).factors
        assert f.proactive_suggestion_threshold == pytest.approx(1.0)

    def test_extraversion_one(self) -> None:
        p = AssistantProfile(extraversion=1.0)
        f = PersonalityModulator(p).factors
        assert f.proactive_suggestion_threshold == pytest.approx(0.0)

    def test_extraversion_mid(self) -> None:
        p = AssistantProfile(extraversion=0.5)
        f = PersonalityModulator(p).factors
        assert f.proactive_suggestion_threshold == pytest.approx(0.5)

    def test_agreeableness_zero(self) -> None:
        p = AssistantProfile(agreeableness=0.0)
        f = PersonalityModulator(p).factors
        assert f.challenge_probability == pytest.approx(1.0)

    def test_agreeableness_one(self) -> None:
        p = AssistantProfile(agreeableness=1.0)
        f = PersonalityModulator(p).factors
        assert f.challenge_probability == pytest.approx(0.0)

    def test_agreeableness_mid(self) -> None:
        p = AssistantProfile(agreeableness=0.5)
        f = PersonalityModulator(p).factors
        assert f.challenge_probability == pytest.approx(0.5)

    def test_stability_zero(self) -> None:
        p = AssistantProfile(stability=0.0)
        f = PersonalityModulator(p).factors
        assert f.cortisol_decay_multiplier == pytest.approx(0.5)

    def test_stability_one(self) -> None:
        p = AssistantProfile(stability=1.0)
        f = PersonalityModulator(p).factors
        assert f.cortisol_decay_multiplier == pytest.approx(1.5)

    def test_stability_mid(self) -> None:
        p = AssistantProfile(stability=0.5)
        f = PersonalityModulator(p).factors
        assert f.cortisol_decay_multiplier == pytest.approx(1.0)


class TestUpdate:
    def test_update_recalculates(self) -> None:
        p = AssistantProfile(openness=0.2)
        m = PersonalityModulator(p)
        assert m.factors.exploration_budget_multiplier == pytest.approx(0.7)

        p2 = AssistantProfile(openness=0.9)
        f = m.update(p2)
        assert f.exploration_budget_multiplier == pytest.approx(1.4)
        assert m.factors.exploration_budget_multiplier == pytest.approx(1.4)

    def test_update_reflects_rhetorical_style(self) -> None:
        p = AssistantProfile(
            anti_patterns=["Avoid flattery"],
            rhetorical_style={
                "tone": "warm",
                "sentence_pattern": "mixed",
                "challenge_mode": "socratic",
                "explanation_depth": "thorough",
            },
        )
        f = PersonalityModulator(p).factors
        assert f.response_tone == "warm"
        assert f.explanation_depth == "thorough"
        assert f.disagreement_style == "socratic"
        assert f.anti_pattern_guard == ["Avoid flattery"]


class TestCortisolIntegration:
    def test_stability_affects_cortisol_decay(self) -> None:
        """High stability → higher cortisol decay multiplier → faster recovery."""
        low = AssistantProfile(stability=0.0)
        high = AssistantProfile(stability=1.0)

        low_factors = PersonalityModulator(low).factors
        high_factors = PersonalityModulator(high).factors

        assert high_factors.cortisol_decay_multiplier > low_factors.cortisol_decay_multiplier
        assert low_factors.cortisol_decay_multiplier == pytest.approx(0.5)
        assert high_factors.cortisol_decay_multiplier == pytest.approx(1.5)
