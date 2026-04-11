"""Tests for UserProfile schema and loader (#241)."""

from __future__ import annotations

import pytest
import yaml

from openbad.identity.user_profile import (
    CommunicationStyle,
    UserProfile,
    load_user_profile,
)


class TestUserProfile:
    def test_valid_construction(self) -> None:
        p = UserProfile(name="Alice")
        assert p.name == "Alice"
        assert p.preferred_name == ""
        assert p.communication_style is CommunicationStyle.CASUAL
        assert p.expertise_domains == []
        assert p.interaction_history_summary == ""
        assert p.worldview == []
        assert p.preferred_feedback_style == "balanced"
        assert p.work_hours == (9, 17)

    def test_all_fields(self) -> None:
        p = UserProfile(
            name="Bob",
            preferred_name="Bobby",
            communication_style=CommunicationStyle.FORMAL,
            expertise_domains=["python", "ML"],
            interaction_history_summary="Long-time user",
        )
        assert p.preferred_name == "Bobby"
        assert p.communication_style is CommunicationStyle.FORMAL
        assert p.expertise_domains == ["python", "ML"]

    def test_enriched_fields(self) -> None:
        p = UserProfile(
            name="Bob",
            worldview=["Evidence matters"],
            interests=["robotics"],
            pet_peeves=["verbosity"],
            preferred_feedback_style="direct",
            active_projects=["OpenBaD"],
            timezone="UTC",
            work_hours=[8, 16],  # type: ignore[arg-type]
        )
        assert p.worldview == ["Evidence matters"]
        assert p.interests == ["robotics"]
        assert p.pet_peeves == ["verbosity"]
        assert p.preferred_feedback_style == "direct"
        assert p.active_projects == ["OpenBaD"]
        assert p.timezone == "UTC"
        assert p.work_hours == (8, 16)

    def test_name_required(self) -> None:
        with pytest.raises(ValueError, match="name is required"):
            UserProfile(name="")

    def test_style_from_string(self) -> None:
        p = UserProfile(name="X", communication_style="terse")  # type: ignore[arg-type]
        assert p.communication_style is CommunicationStyle.TERSE

    def test_style_from_string_case_insensitive(self) -> None:
        p = UserProfile(name="X", communication_style="FORMAL")  # type: ignore[arg-type]
        assert p.communication_style is CommunicationStyle.FORMAL


class TestCommunicationStyle:
    def test_enum_values(self) -> None:
        assert CommunicationStyle.FORMAL.value == "formal"
        assert CommunicationStyle.CASUAL.value == "casual"
        assert CommunicationStyle.TERSE.value == "terse"


class TestLoadUserProfile:
    def test_load_valid(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "user": {
                        "name": "Jane",
                        "preferred_name": "J",
                        "communication_style": "formal",
                        "expertise_domains": ["devops"],
                        "interaction_history_summary": "New user",
                    },
                }
            )
        )
        p = load_user_profile(cfg)
        assert p.name == "Jane"
        assert p.preferred_name == "J"
        assert p.communication_style is CommunicationStyle.FORMAL
        assert p.expertise_domains == ["devops"]

    def test_load_enriched_fields(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "user": {
                        "name": "Jane",
                        "worldview": ["Measure outcomes"],
                        "interests": ["systems"],
                        "pet_peeves": ["verbosity"],
                        "preferred_feedback_style": "challenge me",
                        "active_projects": ["OpenBaD"],
                        "timezone": "UTC",
                        "work_hours": [7, 15],
                    },
                }
            )
        )
        p = load_user_profile(cfg)
        assert p.worldview == ["Measure outcomes"]
        assert p.interests == ["systems"]
        assert p.pet_peeves == ["verbosity"]
        assert p.preferred_feedback_style == "challenge me"
        assert p.active_projects == ["OpenBaD"]
        assert p.timezone == "UTC"
        assert p.work_hours == (7, 15)

    def test_load_defaults(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({"user": {"name": "Min"}}))
        p = load_user_profile(cfg)
        assert p.name == "Min"
        assert p.communication_style is CommunicationStyle.CASUAL

    def test_load_missing_user_section(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({"identity": {}}))
        with pytest.raises(ValueError, match="user"):
            load_user_profile(cfg)

    def test_load_invalid_style(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "user": {"name": "X", "communication_style": "aggressive"},
                }
            )
        )
        with pytest.raises(ValueError, match="Invalid communication_style"):
            load_user_profile(cfg)

    def test_load_empty_name(self, tmp_path) -> None:
        cfg = tmp_path / "identity.yaml"
        cfg.write_text(yaml.safe_dump({"user": {"name": ""}}))
        with pytest.raises(ValueError, match="name is required"):
            load_user_profile(cfg)

    def test_load_from_real_config(self) -> None:
        p = load_user_profile("config/identity.yaml")
        assert p.name == "User"
        assert p.communication_style is CommunicationStyle.CASUAL
