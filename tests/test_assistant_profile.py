"""Tests for AssistantProfile schema and loader (#242)."""

from __future__ import annotations

import pytest
import yaml

from openbad.identity.assistant_profile import (
    AssistantProfile,
    load_assistant_profile,
)


class TestAssistantProfile:
    def test_defaults(self) -> None:
        p = AssistantProfile()
        assert p.name == "OpenBaD"
        assert p.openness == 0.7
        assert p.conscientiousness == 0.8
        assert p.extraversion == 0.5
        assert p.agreeableness == 0.4
        assert p.stability == 0.6
        assert p.learning_focus == []

    def test_custom_values(self) -> None:
        p = AssistantProfile(
            name="Agent",
            openness=0.9,
            conscientiousness=0.1,
            stability=1.0,
            learning_focus=["rust", "eBPF"],
        )
        assert p.name == "Agent"
        assert p.openness == 0.9
        assert p.conscientiousness == 0.1
        assert p.learning_focus == ["rust", "eBPF"]

    def test_clamping_high(self) -> None:
        p = AssistantProfile(openness=1.5, stability=99.0)
        assert p.openness == 1.0
        assert p.stability == 1.0

    def test_clamping_low(self) -> None:
        p = AssistantProfile(agreeableness=-0.5, extraversion=-100.0)
        assert p.agreeableness == 0.0
        assert p.extraversion == 0.0


class TestLoadAssistantProfile:
    def test_load_valid(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({
            "assistant": {
                "name": "Bot",
                "persona_summary": "Helpful",
                "learning_focus": ["Python"],
                "ocean": {
                    "openness": 0.3,
                    "conscientiousness": 0.9,
                    "extraversion": 0.2,
                    "agreeableness": 0.8,
                    "stability": 0.5,
                },
            },
        }))
        p = load_assistant_profile(cfg)
        assert p.name == "Bot"
        assert p.openness == 0.3
        assert p.conscientiousness == 0.9

    def test_load_defaults_when_ocean_missing(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({
            "assistant": {"name": "Min"},
        }))
        p = load_assistant_profile(cfg)
        assert p.openness == 0.7
        assert p.stability == 0.6

    def test_load_missing_section(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({"identity": {}}))
        with pytest.raises(ValueError, match="assistant"):
            load_assistant_profile(cfg)

    def test_load_clamping(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({
            "assistant": {
                "name": "Extreme",
                "ocean": {"openness": 5.0, "stability": -1.0},
            },
        }))
        p = load_assistant_profile(cfg)
        assert p.openness == 1.0
        assert p.stability == 0.0

    def test_load_from_real_config(self) -> None:
        p = load_assistant_profile("config/identity.yaml")
        assert p.name == "OpenBaD"
        assert 0.0 <= p.openness <= 1.0
