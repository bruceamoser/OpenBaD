"""UserProfile schema and config-seeded loader.

Defines the layer-1 user entity loaded from ``config/identity.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


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

    def __post_init__(self) -> None:
        if not self.name:
            msg = "UserProfile.name is required"
            raise ValueError(msg)
        if isinstance(self.communication_style, str):
            self.communication_style = CommunicationStyle(
                self.communication_style.lower(),
            )


def load_user_profile(path: str | Path) -> UserProfile:
    """Load a :class:`UserProfile` from a YAML file.

    Expects a ``user:`` top-level key with profile fields.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    user_data = raw.get("user")
    if not isinstance(user_data, dict):
        msg = "identity.yaml must contain a 'user' mapping"
        raise ValueError(msg)

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
            "interaction_history_summary", "",
        ),
    )
