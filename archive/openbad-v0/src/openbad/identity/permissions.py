"""Action permissioning — tier-based access control for agent operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from openbad.identity.session import SessionManager


class ActionTier(Enum):
    """Permission tier for agent actions."""

    READ = "READ"
    WRITE = "WRITE"
    PUBLISH = "PUBLISH"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class Permission:
    """Describes the permission requirements for an action."""

    action: str
    tier: ActionTier
    requires_identity: bool
    requires_confirmation: bool
    requires_elevated_auth: bool


@dataclass(frozen=True)
class PermissionResult:
    """Outcome of a permission check."""

    allowed: bool
    action: str
    tier: ActionTier
    reason: str = ""


# Tier → requirement flags
_TIER_REQUIREMENTS: dict[ActionTier, dict[str, bool]] = {
    ActionTier.READ: {
        "requires_identity": False,
        "requires_confirmation": False,
        "requires_elevated_auth": False,
    },
    ActionTier.WRITE: {
        "requires_identity": True,
        "requires_confirmation": False,
        "requires_elevated_auth": False,
    },
    ActionTier.PUBLISH: {
        "requires_identity": True,
        "requires_confirmation": True,
        "requires_elevated_auth": False,
    },
    ActionTier.SYSTEM: {
        "requires_identity": True,
        "requires_confirmation": True,
        "requires_elevated_auth": True,
    },
}


def load_action_mappings(
    yaml_path: str | Path = "config/permissions.yaml",
) -> dict[str, ActionTier]:
    """Load action → tier mappings from a YAML file."""
    path = Path(yaml_path)
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    mappings: dict[str, ActionTier] = {}
    for tier_name, actions in data.get("permissions", {}).items():
        tier = ActionTier(tier_name.upper())
        if isinstance(actions, list):
            for action in actions:
                mappings[action] = tier
    return mappings


class PermissionClassifier:
    """Classifies agent actions into permission tiers.

    Parameters
    ----------
    action_mappings:
        Explicit action → tier overrides.
    yaml_path:
        Path to YAML file with additional mappings.
    default_tier:
        Default tier when an action is not explicitly mapped.
    """

    def __init__(
        self,
        *,
        action_mappings: dict[str, ActionTier] | None = None,
        yaml_path: str | Path | None = None,
        default_tier: ActionTier = ActionTier.WRITE,
    ) -> None:
        self._mappings: dict[str, ActionTier] = {}
        self._default_tier = default_tier

        if yaml_path is not None:
            self._mappings.update(load_action_mappings(yaml_path))
        if action_mappings is not None:
            self._mappings.update(action_mappings)

    def classify(self, action_name: str) -> Permission:
        """Classify *action_name* into a :class:`Permission`."""
        tier = self._mappings.get(action_name, self._default_tier)
        reqs = _TIER_REQUIREMENTS[tier]
        return Permission(
            action=action_name,
            tier=tier,
            requires_identity=reqs["requires_identity"],
            requires_confirmation=reqs["requires_confirmation"],
            requires_elevated_auth=reqs["requires_elevated_auth"],
        )

    def check_permission(
        self,
        session_manager: SessionManager,
        session_id: str | None,
        action_name: str,
        *,
        user_confirmed: bool = False,
        elevated_auth: bool = False,
    ) -> PermissionResult:
        """Check whether *action_name* is permitted for the given session.

        Parameters
        ----------
        session_manager:
            Manager that validates session tokens.
        session_id:
            Session identifier (may be ``None`` for READ actions).
        action_name:
            The action being attempted.
        user_confirmed:
            Whether the user has confirmed the action.
        elevated_auth:
            Whether elevated authentication has been provided.
        """
        perm = self.classify(action_name)

        # READ — always allowed
        if perm.tier is ActionTier.READ:
            return PermissionResult(
                allowed=True,
                action=action_name,
                tier=perm.tier,
            )

        # Identity required (WRITE, PUBLISH, SYSTEM)
        if perm.requires_identity:
            if session_id is None:
                return PermissionResult(
                    allowed=False,
                    action=action_name,
                    tier=perm.tier,
                    reason="Valid session required",
                )
            session = session_manager.validate_session(session_id)
            if session is None:
                return PermissionResult(
                    allowed=False,
                    action=action_name,
                    tier=perm.tier,
                    reason="Session invalid or expired",
                )

        # Confirmation required (PUBLISH, SYSTEM)
        if perm.requires_confirmation and not user_confirmed:
            return PermissionResult(
                allowed=False,
                action=action_name,
                tier=perm.tier,
                reason="User confirmation required",
            )

        # Elevated auth required (SYSTEM)
        if perm.requires_elevated_auth and not elevated_auth:
            return PermissionResult(
                allowed=False,
                action=action_name,
                tier=perm.tier,
                reason="Elevated authentication required",
            )

        return PermissionResult(
            allowed=True,
            action=action_name,
            tier=perm.tier,
        )
