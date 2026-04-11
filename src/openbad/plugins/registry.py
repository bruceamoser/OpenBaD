"""Capability registry with permission enforcement for Phase 9 plugin management.

:class:`CapabilityRegistry` maintains an in-memory inventory of registered
:class:`~openbad.plugins.manifest.CapabilityEntry` objects.  Registration is
gated by a :class:`PermissionPolicy` that validates each requested permission
against an allowlist derived from :file:`config/permissions.yaml`.

System 1 (fast-reflex) capabilities are tracked separately so callers can
inspect the restricted inventory without exposing higher-tier capabilities.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from openbad.plugins.manifest import CapabilityManifest


# ---------------------------------------------------------------------------
# Permission policy
# ---------------------------------------------------------------------------


class PermissionPolicy:
    """Validates capability permissions against a YAML allowlist.

    Parameters
    ----------
    allowed_permissions:
        Flat set of allowed permission strings (e.g. ``{"file.read", "db.insert"}``).
        Pass ``None`` to allow everything (useful in tests).
    """

    def __init__(self, allowed_permissions: set[str] | None) -> None:
        self._allowed = allowed_permissions

    def is_allowed(self, permission: str) -> bool:
        """Return ``True`` if *permission* is in the allowlist."""
        if self._allowed is None:
            return True
        return permission in self._allowed

    def check_permissions(self, permissions: list[str]) -> list[str]:
        """Return the subset of *permissions* that are *not* allowed."""
        return [p for p in permissions if not self.is_allowed(p)]

    @classmethod
    def from_yaml(cls, path: str | Path) -> PermissionPolicy:
        """Build a policy by flattening all tiers from *path*.

        Parameters
        ----------
        path:
            Path to a ``permissions.yaml`` file whose top-level key is
            ``permissions`` mapping tier names to lists of permission strings.
        """
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        tiers: dict = config.get("permissions", {})
        allowed: set[str] = set()
        for perms in tiers.values():
            allowed.update(perms)
        return cls(allowed)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RegistrationError(ValueError):
    """Raised when capability registration fails permission checks."""


@dataclasses.dataclass
class RegistryEntry:
    """A registered capability with its source manifest metadata."""

    capability_id: str
    permissions: list[str]
    description: str
    plugin_name: str
    plugin_version: str
    system1: bool = False

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class CapabilityRegistry:
    """In-memory inventory of approved capabilities.

    Parameters
    ----------
    policy:
        A :class:`PermissionPolicy` used to gate registration.
    system1_prefixes:
        Capability ID prefixes treated as System 1 (fast-reflex) capabilities.
        Defaults to ``("core_triage.", "reflex.")``.
    """

    def __init__(
        self,
        policy: PermissionPolicy,
        system1_prefixes: tuple[str, ...] = ("core_triage.", "reflex."),
    ) -> None:
        self._policy = policy
        self._system1_prefixes = system1_prefixes
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, manifest: CapabilityManifest) -> list[RegistryEntry]:
        """Register all capabilities from *manifest*.

        Capabilities whose permission set includes any disallowed permission
        are rejected.  Allowed capabilities are added to the registry.

        Returns
        -------
        list[RegistryEntry]
            The newly registered entries.

        Raises
        ------
        RegistrationError
            If any capability has a disallowed permission.  No capabilities
            from the manifest are registered on error (fail-closed).
        """
        pending: list[RegistryEntry] = []

        for cap in manifest.capabilities:
            violations = self._policy.check_permissions(cap.permissions)
            if violations:
                raise RegistrationError(
                    f"Capability {cap.id!r} in plugin {manifest.name!r}"
                    f" requests disallowed permissions: {violations}"
                )
            is_s1 = any(cap.id.startswith(prefix) for prefix in self._system1_prefixes)
            pending.append(
                RegistryEntry(
                    capability_id=cap.id,
                    permissions=list(cap.permissions),
                    description=cap.description,
                    plugin_name=manifest.name,
                    plugin_version=manifest.version,
                    system1=is_s1,
                )
            )

        # Commit only after all capabilities pass
        for entry in pending:
            self._entries[entry.capability_id] = entry

        return pending

    def get(self, capability_id: str) -> RegistryEntry | None:
        """Return the entry for *capability_id*, or ``None``."""
        return self._entries.get(capability_id)

    def list_all(self) -> list[RegistryEntry]:
        """Return all registered capabilities."""
        return list(self._entries.values())

    def list_system1(self) -> list[RegistryEntry]:
        """Return only System 1 (fast-reflex) capabilities."""
        return [e for e in self._entries.values() if e.system1]
