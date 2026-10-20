"""Capability manifest schema and parser for Phase 9 plugin management.

An ``openbad.plugin.json`` file declares the capabilities exposed by a plugin.
:func:`parse_manifest` validates and parses such files into :class:`CapabilityManifest`
typed structures, failing closed on any validation error.

Manifest schema
---------------
.. code-block:: json

    {
        "name": "my_plugin",
        "version": "1.0.0",
        "module": "openbad.plugins.my_plugin",
        "description": "Optional description",
        "capabilities": [
            {
                "id": "my_plugin.do_thing",
                "description": "Does the thing",
                "permissions": ["file.read"]
            }
        ]
    }

Required top-level fields: ``name``, ``version``, ``module``, ``capabilities``.
Each capability entry requires ``id`` and ``permissions``; ``description`` is optional.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Typed structures
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CapabilityEntry:
    """A single capability declared in a manifest."""

    id: str
    permissions: list[str]
    description: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CapabilityEntry:
        return cls(
            id=data["id"],
            permissions=list(data["permissions"]),
            description=data.get("description", ""),
        )


@dataclasses.dataclass(frozen=True)
class CapabilityManifest:
    """A parsed and validated ``openbad.plugin.json`` manifest."""

    name: str
    version: str
    module: str
    capabilities: list[CapabilityEntry]
    description: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["capabilities"] = [c.to_dict() for c in self.capabilities]
        return d


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ManifestError(ValueError):
    """Raised when a manifest fails validation."""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_REQUIRED_TOP: tuple[str, ...] = ("name", "version", "module", "capabilities")
_REQUIRED_CAP: tuple[str, ...] = ("id", "permissions")


def parse_manifest(source: str | bytes | dict | Path) -> CapabilityManifest:
    """Parse and validate a capability manifest.

    Parameters
    ----------
    source:
        A JSON string, raw bytes, a ``dict``, or a :class:`~pathlib.Path` to a
        JSON file.

    Returns
    -------
    CapabilityManifest
        The validated manifest.

    Raises
    ------
    ManifestError
        If the manifest is missing required fields, has wrong types, or
        contains invalid capability entries.
    """
    if isinstance(source, Path):
        try:
            raw = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"Cannot read manifest file: {exc}") from exc
        data = _parse_json(raw)
    elif isinstance(source, (str, bytes)):
        data = _parse_json(source)
    elif isinstance(source, dict):
        data = source
    else:
        raise ManifestError(f"Unsupported manifest source type: {type(source).__name__!r}")

    # Validate top-level required fields
    for field in _REQUIRED_TOP:
        if field not in data:
            raise ManifestError(f"Manifest missing required field: {field!r}")

    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ManifestError("Manifest 'name' must be a non-empty string")
    if not isinstance(data["version"], str) or not data["version"].strip():
        raise ManifestError("Manifest 'version' must be a non-empty string")
    if not isinstance(data["module"], str) or not data["module"].strip():
        raise ManifestError("Manifest 'module' must be a non-empty string")
    if not isinstance(data["capabilities"], list):
        raise ManifestError("Manifest 'capabilities' must be a list")

    capabilities: list[CapabilityEntry] = []
    for i, cap in enumerate(data["capabilities"]):
        if not isinstance(cap, dict):
            raise ManifestError(f"Capability at index {i} must be an object")
        for field in _REQUIRED_CAP:
            if field not in cap:
                raise ManifestError(
                    f"Capability at index {i} missing required field: {field!r}"
                )
        if not isinstance(cap["id"], str) or not cap["id"].strip():
            raise ManifestError(f"Capability at index {i}: 'id' must be a non-empty string")
        if not isinstance(cap["permissions"], list):
            raise ManifestError(f"Capability at index {i}: 'permissions' must be a list")
        for perm in cap["permissions"]:
            if not isinstance(perm, str):
                raise ManifestError(
                    f"Capability {cap['id']!r}: permission {perm!r} must be a string"
                )

        capabilities.append(CapabilityEntry.from_dict(cap))

    return CapabilityManifest(
        name=data["name"].strip(),
        version=data["version"].strip(),
        module=data["module"].strip(),
        description=data.get("description", ""),
        capabilities=capabilities,
    )


def _parse_json(raw: str | bytes) -> dict:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON in manifest: {exc}") from exc
    if not isinstance(result, dict):
        raise ManifestError("Manifest must be a JSON object")
    return result
