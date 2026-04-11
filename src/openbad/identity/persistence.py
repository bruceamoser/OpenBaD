"""Dual-layer identity persistence — config seed + episodic LTM evolution.

Layer 1: ``identity.yaml`` provides factory-default values.
Layer 2: Episodic LTM stores a shadow copy that evolves at runtime.
During sleep consolidation, the shadow is reconciled with the live profile
and written back to ``identity.yaml`` (with a timestamped backup).
"""

from __future__ import annotations

import copy
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from openbad.identity.assistant_profile import (
    AssistantProfile,
    load_assistant_profile,
)
from openbad.identity.user_profile import (
    CommunicationStyle,
    UserProfile,
    load_user_profile,
)
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory

logger = logging.getLogger(__name__)

_USER_SHADOW_KEY = "identity/user_shadow"
_ASSISTANT_SHADOW_KEY = "identity/assistant_shadow"


def _user_to_dict(profile: UserProfile) -> dict[str, Any]:
    d = asdict(profile)
    d["communication_style"] = profile.communication_style.value
    return d


def _dict_to_user(data: dict[str, Any]) -> UserProfile:
    style = data.get("communication_style", "casual")
    if isinstance(style, str):
        style = CommunicationStyle(style.lower())
    return UserProfile(
        name=data.get("name", ""),
        preferred_name=data.get("preferred_name", ""),
        communication_style=style,
        expertise_domains=data.get("expertise_domains", []),
        interaction_history_summary=data.get(
            "interaction_history_summary", "",
        ),
    )


def _assistant_to_dict(profile: AssistantProfile) -> dict[str, Any]:
    return asdict(profile)


def _dict_to_assistant(data: dict[str, Any]) -> AssistantProfile:
    return AssistantProfile(**data)


class IdentityPersistence:
    """Manages dual-layer persistence for user and assistant profiles.

    Parameters
    ----------
    config_path:
        Path to ``identity.yaml`` (Layer 1 seed).
    episodic:
        Episodic memory store for Layer 2 shadow copies.
    """

    def __init__(
        self,
        config_path: str | Path,
        episodic: EpisodicMemory,
    ) -> None:
        self._config_path = Path(config_path)
        self._episodic = episodic

        # Layer 1 — immutable seed
        self._user_seed = load_user_profile(self._config_path)
        self._assistant_seed = load_assistant_profile(self._config_path)

        # Live profiles — start from seed, overlay shadow
        self._user = copy.deepcopy(self._user_seed)
        self._assistant = copy.deepcopy(self._assistant_seed)
        self._overlay_shadow()

    # -------------------------------------------------------------- #
    # Public properties
    # -------------------------------------------------------------- #

    @property
    def user(self) -> UserProfile:
        return self._user

    @property
    def assistant(self) -> AssistantProfile:
        return self._assistant

    # -------------------------------------------------------------- #
    # Runtime evolution — store changes in LTM shadow
    # -------------------------------------------------------------- #

    def update_user(self, **changes: Any) -> UserProfile:
        """Apply runtime changes to the user profile and persist to LTM."""
        for key, value in changes.items():
            if not hasattr(self._user, key):
                msg = f"UserProfile has no field {key!r}"
                raise AttributeError(msg)
            setattr(self._user, key, value)
        self._user.__post_init__()
        self._write_shadow(_USER_SHADOW_KEY, _user_to_dict(self._user))
        return self._user

    def update_assistant(self, **changes: Any) -> AssistantProfile:
        """Apply runtime changes to the assistant profile and persist to LTM."""
        for key, value in changes.items():
            if not hasattr(self._assistant, key):
                msg = f"AssistantProfile has no field {key!r}"
                raise AttributeError(msg)
            setattr(self._assistant, key, value)
        self._assistant.__post_init__()
        self._write_shadow(
            _ASSISTANT_SHADOW_KEY,
            _assistant_to_dict(self._assistant),
        )
        return self._assistant

    # -------------------------------------------------------------- #
    # Sleep consolidation
    # -------------------------------------------------------------- #

    def consolidate(self) -> Path | None:
        """Reconcile LTM shadow → live profile → write back to config.

        Returns the backup path if a write-back occurred, else ``None``.
        """
        user_entry = self._episodic.read(_USER_SHADOW_KEY)
        assistant_entry = self._episodic.read(_ASSISTANT_SHADOW_KEY)

        if user_entry is None and assistant_entry is None:
            return None

        backup = self._backup_config()
        self._write_config()
        return backup

    # -------------------------------------------------------------- #
    # Reset to seed (factory defaults)
    # -------------------------------------------------------------- #

    def reset_to_seed(self) -> None:
        """Discard LTM shadow and reload from config seed."""
        self._episodic.delete(_USER_SHADOW_KEY)
        self._episodic.delete(_ASSISTANT_SHADOW_KEY)
        self._user = copy.deepcopy(self._user_seed)
        self._assistant = copy.deepcopy(self._assistant_seed)

    # -------------------------------------------------------------- #
    # Internal helpers
    # -------------------------------------------------------------- #

    def _overlay_shadow(self) -> None:
        """Overlay LTM shadow (if any) onto the live profile."""
        user_entry = self._episodic.read(_USER_SHADOW_KEY)
        if user_entry is not None and isinstance(user_entry.value, dict):
            self._user = _dict_to_user(user_entry.value)

        assistant_entry = self._episodic.read(_ASSISTANT_SHADOW_KEY)
        if assistant_entry is not None and isinstance(
            assistant_entry.value, dict,
        ):
            self._assistant = _dict_to_assistant(assistant_entry.value)

    def _write_shadow(self, key: str, data: dict[str, Any]) -> None:
        entry = MemoryEntry(
            key=key,
            value=data,
            tier=MemoryTier.EPISODIC,
            metadata={"source": "identity_persistence"},
        )
        self._episodic.write(entry)

    def _backup_config(self) -> Path:
        ts = time.strftime("%Y%m%dT%H%M%S")
        backup = self._config_path.with_suffix(f".{ts}.bak")
        shutil.copy2(self._config_path, backup)
        return backup

    def _write_config(self) -> None:
        raw = yaml.safe_load(
            self._config_path.read_text(encoding="utf-8"),
        ) or {}

        raw["user"] = _user_to_dict(self._user)

        assistant_d = _assistant_to_dict(self._assistant)
        raw["assistant"] = {
            "name": assistant_d.pop("name"),
            "persona_summary": assistant_d.pop("persona_summary"),
            "learning_focus": assistant_d.pop("learning_focus"),
            "ocean": {
                "openness": assistant_d.pop("openness"),
                "conscientiousness": assistant_d.pop("conscientiousness"),
                "extraversion": assistant_d.pop("extraversion"),
                "agreeableness": assistant_d.pop("agreeableness"),
                "stability": assistant_d.pop("stability"),
            },
        }

        self._config_path.write_text(
            yaml.safe_dump(raw, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
