"""Tests for dual-layer identity persistence (#244)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openbad.identity.persistence import IdentityPersistence
from openbad.identity.user_profile import CommunicationStyle
from openbad.memory.episodic import EpisodicMemory


def _write_config(tmp: Path) -> Path:
    """Create a minimal identity.yaml in *tmp* and return its path."""
    cfg = tmp / "identity.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "identity": {"secret_hex": "abc"},
                "user": {
                    "name": "Alice",
                    "preferred_name": "Ali",
                    "communication_style": "formal",
                    "expertise_domains": ["python"],
                    "interaction_history_summary": "",
                },
                "assistant": {
                    "name": "OpenBaD",
                    "persona_summary": "Helpful agent",
                    "learning_focus": [],
                    "ocean": {
                        "openness": 0.7,
                        "conscientiousness": 0.8,
                        "extraversion": 0.5,
                        "agreeableness": 0.4,
                        "stability": 0.6,
                    },
                },
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture()
def env(tmp_path: Path):
    """Yield (config_path, episodic, persistence) triple."""
    cfg = _write_config(tmp_path)
    ep = EpisodicMemory(
        storage_path=tmp_path / "episodic.json",
        auto_persist=True,
    )
    ip = IdentityPersistence(cfg, ep)
    return cfg, ep, ip


# ------------------------------------------------------------------ #
# Startup overlay
# ------------------------------------------------------------------ #


class TestStartupOverlay:
    def test_loads_seed_when_no_shadow(self, env) -> None:
        _, _, ip = env
        assert ip.user.name == "Alice"
        assert ip.user.communication_style == CommunicationStyle.FORMAL
        assert ip.assistant.openness == pytest.approx(0.7)

    def test_overlays_shadow_on_startup(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path)
        ep = EpisodicMemory(
            storage_path=tmp_path / "episodic.json",
            auto_persist=True,
        )
        # Pre-populate a shadow before creating persistence.
        ip1 = IdentityPersistence(cfg, ep)
        ip1.update_user(preferred_name="AliceV2")
        ip1.update_assistant(openness=0.3)

        # New persistence instance should overlay the shadow.
        ip2 = IdentityPersistence(cfg, ep)
        assert ip2.user.preferred_name == "AliceV2"
        assert ip2.assistant.openness == pytest.approx(0.3)


# ------------------------------------------------------------------ #
# Runtime updates
# ------------------------------------------------------------------ #


class TestRuntimeUpdates:
    def test_update_user_stores_in_ltm(self, env) -> None:
        _, ep, ip = env
        ip.update_user(preferred_name="Bob")
        assert ip.user.preferred_name == "Bob"
        entry = ep.read("identity/user_shadow")
        assert entry is not None
        assert entry.value["preferred_name"] == "Bob"

    def test_update_assistant_stores_in_ltm(self, env) -> None:
        _, ep, ip = env
        ip.update_assistant(stability=0.9)
        assert ip.assistant.stability == pytest.approx(0.9)
        entry = ep.read("identity/assistant_shadow")
        assert entry is not None
        assert entry.value["stability"] == pytest.approx(0.9)

    def test_update_user_bad_field_raises(self, env) -> None:
        _, _, ip = env
        with pytest.raises(AttributeError, match="no_such_field"):
            ip.update_user(no_such_field="x")

    def test_update_assistant_bad_field_raises(self, env) -> None:
        _, _, ip = env
        with pytest.raises(AttributeError, match="no_such_field"):
            ip.update_assistant(no_such_field="x")


# ------------------------------------------------------------------ #
# Sleep consolidation
# ------------------------------------------------------------------ #


class TestConsolidation:
    def test_consolidate_no_shadow_returns_none(self, env) -> None:
        _, _, ip = env
        assert ip.consolidate() is None

    def test_consolidate_writes_back_config(self, env) -> None:
        cfg, _, ip = env
        ip.update_user(preferred_name="Consolidated")
        ip.update_assistant(openness=0.1)

        backup = ip.consolidate()
        assert backup is not None
        assert backup.exists()

        # Re-read identity.yaml for verification.
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["user"]["preferred_name"] == "Consolidated"
        assert raw["assistant"]["ocean"]["openness"] == pytest.approx(0.1)

    def test_consolidate_preserves_identity_section(self, env) -> None:
        cfg, _, ip = env
        ip.update_user(preferred_name="X")
        ip.consolidate()

        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["identity"]["secret_hex"] == "abc"  # noqa: S105

    def test_backup_is_timestamped(self, env) -> None:
        _, _, ip = env
        ip.update_user(preferred_name="Y")
        backup = ip.consolidate()
        assert backup is not None
        assert ".bak" in backup.suffix or backup.name.endswith(".bak")


# ------------------------------------------------------------------ #
# Reset to seed
# ------------------------------------------------------------------ #


class TestResetToSeed:
    def test_reset_discards_shadow(self, env) -> None:
        _, ep, ip = env
        ip.update_user(preferred_name="Modified")
        ip.update_assistant(openness=0.1)

        ip.reset_to_seed()

        assert ip.user.preferred_name == "Ali"
        assert ip.assistant.openness == pytest.approx(0.7)
        assert ep.read("identity/user_shadow") is None
        assert ep.read("identity/assistant_shadow") is None

    def test_reset_survives_new_instance(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path)
        ep = EpisodicMemory(
            storage_path=tmp_path / "episodic.json",
            auto_persist=True,
        )
        ip1 = IdentityPersistence(cfg, ep)
        ip1.update_user(preferred_name="Changed")
        ip1.reset_to_seed()

        ip2 = IdentityPersistence(cfg, ep)
        assert ip2.user.preferred_name == "Ali"
