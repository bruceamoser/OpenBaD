"""UserProfile schema and config-seeded loader.

Defines the layer-1 user entity loaded from ``config/identity.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _coerce_work_hours(value: Any) -> tuple[int, int]:
    if isinstance(value, tuple) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, list) and len(value) == 2:
        return int(value[0]), int(value[1])
    return 9, 17


class CommunicationStyle(Enum):
    """Supported communication style presets."""

    FORMAL = "formal"
    CASUAL = "casual"
    TERSE = "terse"


@dataclass
class UserProfile:
    """Core user entity — layer 1 (config-seeded).

    Fields are seeded from ``config/identity.yaml`` under the ``user:`` key.
    Layer 2 (episodic LTM evolution) is handled elsewhere.
    """

    name: str
    preferred_name: str = ""
    communication_style: CommunicationStyle = CommunicationStyle.CASUAL
    expertise_domains: list[str] = field(default_factory=list)
    interaction_history_summary: str = ""
    worldview: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    pet_peeves: list[str] = field(default_factory=list)
    preferred_feedback_style: str = "balanced"
    active_projects: list[str] = field(default_factory=list)
    timezone: str = ""
    work_hours: tuple[int, int] = (9, 17)

    def __post_init__(self) -> None:
        if not self.name:
            msg = "UserProfile.name is required"
            raise ValueError(msg)
        if isinstance(self.communication_style, str):
            self.communication_style = CommunicationStyle(
                self.communication_style.lower(),
            )
        self.expertise_domains = _list_of_str(self.expertise_domains)
        self.worldview = _list_of_str(self.worldview)
        self.interests = _list_of_str(self.interests)
        self.pet_peeves = _list_of_str(self.pet_peeves)
        self.active_projects = _list_of_str(self.active_projects)
        self.preferred_feedback_style = str(
            self.preferred_feedback_style or "balanced",
        )
        self.timezone = str(self.timezone or "")
        self.work_hours = _coerce_work_hours(self.work_hours)


def load_user_profile(path: str | Path) -> UserProfile:
    """Load a :class:`UserProfile` from a YAML file.

    Expects a ``user:`` top-level key with profile fields.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    user_data = raw.get("user")
    if not isinstance(user_data, dict):
        logger.warning("identity.yaml is missing 'user'; using default user profile")
        user_data = {"name": "User", "communication_style": "casual"}

    style_raw = user_data.get("communication_style", "casual")
    try:
        style = CommunicationStyle(style_raw.lower())
    except (ValueError, AttributeError) as exc:
        msg = f"Invalid communication_style: {style_raw!r}"
        raise ValueError(msg) from exc

    return UserProfile(
        name=user_data.get("name", ""),
        preferred_name=user_data.get("preferred_name", ""),
        communication_style=style,
        expertise_domains=user_data.get("expertise_domains", []),
        interaction_history_summary=user_data.get(
            "interaction_history_summary",
            "",
        ),
        worldview=user_data.get("worldview", []),
        interests=user_data.get("interests", []),
        pet_peeves=user_data.get("pet_peeves", []),
        preferred_feedback_style=user_data.get(
            "preferred_feedback_style",
            "balanced",
        ),
        active_projects=user_data.get("active_projects", []),
        timezone=user_data.get("timezone", ""),
        work_hours=user_data.get("work_hours", [9, 17]),
    )
