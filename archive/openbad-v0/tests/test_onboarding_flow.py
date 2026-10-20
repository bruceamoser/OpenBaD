"""Tests for onboarding flow integration (issue #305).

Covers: fresh state detection, partial completion, skip functionality,
and factory reset re-entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openbad.identity.persistence import IdentityPersistence
from openbad.memory.episodic import EpisodicMemory

if TYPE_CHECKING:
    from aiohttp import web


@pytest.fixture
def app_with_onboarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[web.Application, Path]:
    """Create an app with onboarding endpoints and identity persistence."""
    from aiohttp import web

    from openbad.identity.personality_modulator import PersonalityModulator
    from openbad.wui.server import (
        _get_onboarding_status,
        _post_assistant_interview_complete,
        _post_onboarding_skip,
        _post_user_interview_complete,
    )

    # Create identity config
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(
        """assistant:
  name: OpenBaD
  persona_summary: self-aware Linux agent
user:
  name: User
"""
    )

    # Create memory directory for LTM shadow
    ltm_dir = tmp_path / "ltm"
    ltm_dir.mkdir()

    episodic = EpisodicMemory(storage_path=ltm_dir / "episodic.db")
    persistence = IdentityPersistence(cfg_path, episodic)
    modulator = PersonalityModulator(persistence.assistant)

    app = web.Application()
    app["identity_persistence"] = persistence
    app["personality_modulator"] = modulator
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(tmp_path))

    app.router.add_get("/api/onboarding/status", _get_onboarding_status)
    app.router.add_post(
        "/api/onboarding/assistant/complete",
        _post_assistant_interview_complete,
    )
    app.router.add_post(
        "/api/onboarding/user/complete",
        _post_user_interview_complete,
    )
    app.router.add_post("/api/onboarding/skip", _post_onboarding_skip)

    return app, tmp_path


# ------------------------------------------------------------------ #
# Fresh state detection (default profiles)
# ------------------------------------------------------------------ #


async def test_fresh_install_shows_incomplete(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """Fresh install with default profiles should show onboarding incomplete."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    # No providers, no sleep config, default identity
    resp = await client.get("/api/onboarding/status")
    assert resp.status == 200
    data = await resp.json()

    assert data["onboarding_complete"] is False
    assert data["providers_complete"] is False
    assert data["sleep_complete"] is False
    assert data["assistant_identity_complete"] is False
    assert data["user_profile_complete"] is False
    assert data["next_step"] == "providers"
    assert data["redirect_to"] == "/providers?wizard=1"


# ------------------------------------------------------------------ #
# Partial completion
# ------------------------------------------------------------------ #


async def test_partial_completion_assistant_only(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """With assistant configured but not user, should show partial complete."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    persistence: IdentityPersistence = app["identity_persistence"]
    persistence.update_assistant(name="Cortex", persona_summary="helpful agent")

    resp = await client.get("/api/onboarding/status")
    assert resp.status == 200
    data = await resp.json()

    assert data["onboarding_complete"] is False
    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is False
    assert data["next_step"] == "providers"


async def test_partial_completion_user_only(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """With user configured but not assistant, should show partial complete."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    persistence: IdentityPersistence = app["identity_persistence"]
    persistence.update_user(name="Alice", expertise_domains=["Python"])

    resp = await client.get("/api/onboarding/status")
    assert resp.status == 200
    data = await resp.json()

    assert data["onboarding_complete"] is False
    assert data["assistant_identity_complete"] is False
    assert data["user_profile_complete"] is True
    assert data["next_step"] == "providers"


async def test_partial_completion_with_providers_and_sleep(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """With providers/sleep but not identity, should show partial complete."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    # Create cognitive config with non-default provider
    cog_cfg = tmp_path / "cognitive.yaml"
    cog_cfg.write_text(
        """cognitive:
  providers:
    - name: custom
      base_url: https://api.example.com
      model: claude-sonnet-4-20250514
      enabled: true
  systems:
    chat:
      provider: custom
      model: claude-sonnet-4-20250514
"""
    )

    # Create memory config with non-default sleep
    mem_cfg = tmp_path / "memory.yaml"
    mem_cfg.write_text(
        """sleep:
  idle_timeout_minutes: 30
  enabled: true
"""
    )

    resp = await client.get("/api/onboarding/status")
    assert resp.status == 200
    data = await resp.json()

    assert data["providers_complete"] is True
    assert data["sleep_complete"] is True
    assert data["onboarding_complete"] is False
    assert data["assistant_identity_complete"] is False
    assert data["user_profile_complete"] is False
    assert data["next_step"] == "assistant_identity"
    assert data["redirect_to"] == "/chat?onboarding=assistant"


# ------------------------------------------------------------------ #
# Skip functionality
# ------------------------------------------------------------------ #


async def test_skip_onboarding_returns_success(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """POST /api/onboarding/skip should return success."""
    app, _ = app_with_onboarding
    client = await aiohttp_client(app)

    resp = await client.post("/api/onboarding/skip")
    assert resp.status == 200
    data = await resp.json()

    assert data["success"] is True
    assert data["skipped"] is True


# ------------------------------------------------------------------ #
# Full completion
# ------------------------------------------------------------------ #


async def test_full_completion_all_configured(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """With all identity fields configured, onboarding should be complete."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    persistence: IdentityPersistence = app["identity_persistence"]
    persistence.update_assistant(name="Cortex", persona_summary="helpful agent")
    persistence.update_user(name="Alice", expertise_domains=["Python"])

    # NOTE: Provider and sleep checks still return False without full config files
    # but identity checks should pass
    resp = await client.get("/api/onboarding/status")
    assert resp.status == 200
    data = await resp.json()

    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is True


# ------------------------------------------------------------------ #
# Factory reset re-entry
# ------------------------------------------------------------------ #


async def test_factory_reset_retriggers_onboarding(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """After reset to defaults, onboarding should be incomplete again."""
    app, tmp_path = app_with_onboarding
    client = await aiohttp_client(app)

    persistence: IdentityPersistence = app["identity_persistence"]

    # Configure everything
    persistence.update_assistant(name="Cortex", persona_summary="helpful agent")
    persistence.update_user(name="Alice", expertise_domains=["Python"])

    resp = await client.get("/api/onboarding/status")
    data = await resp.json()
    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is True

    # Factory reset - reset to seed defaults
    persistence.reset_to_seed()

    resp = await client.get("/api/onboarding/status")
    data = await resp.json()
    assert data["onboarding_complete"] is False
    assert data["assistant_identity_complete"] is False
    assert data["user_profile_complete"] is False


# ------------------------------------------------------------------ #
# Interview completion flow
# ------------------------------------------------------------------ #


async def test_interview_completion_updates_status(
    app_with_onboarding: tuple[web.Application, Path],
    aiohttp_client,
) -> None:
    """Completing interview should update onboarding status."""
    app, _ = app_with_onboarding
    client = await aiohttp_client(app)

    # Initially incomplete
    resp = await client.get("/api/onboarding/status")
    data = await resp.json()
    assert data["assistant_identity_complete"] is False

    # Complete assistant interview
    interview_text = """
```json
{
  "name": "Cortex",
  "persona_summary": "helpful coding assistant",
  "learning_focus": ["Python", "Docker"],
  "worldview": "pragmatic",
  "boundaries": "never give unethical advice",
  "openness": 0.8,
  "conscientiousness": 0.7,
  "extraversion": 0.6,
  "agreeableness": 0.9,
  "stability": 0.75,
  "rhetorical_style": {
    "tone": "friendly",
    "sentence_pattern": "concise",
    "challenge_mode": "gentle",
    "explanation_depth": "medium"
  }
}
```
"""
    resp = await client.post(
        "/api/onboarding/assistant/complete",
        json={"interview_text": interview_text},
    )
    assert resp.status == 200

    # Now assistant should be complete
    resp = await client.get("/api/onboarding/status")
    data = await resp.json()
    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is False

    # Complete user interview
    user_interview = """
```json
{
  "name": "Alice",
  "preferred_name": "Alice",
  "expertise_domains": ["Python", "DevOps"],
  "active_projects": ["OpenBaD"],
  "interests": ["AI", "automation"],
  "communication_style": "direct",
  "pet_peeves": ["verbose explanations"],
  "worldview": "pragmatic",
  "timezone": "America/New_York",
  "work_hours": [9, 17]
}
```
"""
    resp = await client.post(
        "/api/onboarding/user/complete",
        json={"interview_text": user_interview},
    )
    assert resp.status == 200

    # Now both should be complete
    resp = await client.get("/api/onboarding/status")
    data = await resp.json()
    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is True
