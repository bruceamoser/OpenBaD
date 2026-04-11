from __future__ import annotations

from pathlib import Path

import pytest

from openbad.plugins.manifest import CapabilityEntry, CapabilityManifest
from openbad.plugins.registry import (
    CapabilityRegistry,
    PermissionPolicy,
    RegistrationError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALLOWED = {"file.read", "db.insert", "db.update", "mqtt.publish"}

MANIFEST_DATA = CapabilityManifest(
    name="test_plugin",
    version="1.0",
    module="openbad.plugins.test_plugin",
    capabilities=[
        CapabilityEntry(id="test_plugin.read", permissions=["file.read"], description="Read"),
        CapabilityEntry(id="test_plugin.write_db", permissions=["db.insert"], description="DB"),
        CapabilityEntry(id="core_triage.triage", permissions=["file.read"], description="Triage"),
    ],
)


@pytest.fixture()
def registry() -> CapabilityRegistry:
    return CapabilityRegistry(PermissionPolicy(ALLOWED))


# ---------------------------------------------------------------------------
# Registry lists approved capabilities
# ---------------------------------------------------------------------------


def test_register_valid_manifest(registry: CapabilityRegistry) -> None:
    entries = registry.register(MANIFEST_DATA)

    assert len(entries) == 3


def test_registered_capabilities_appear_in_list_all(registry: CapabilityRegistry) -> None:
    registry.register(MANIFEST_DATA)

    ids = {e.capability_id for e in registry.list_all()}
    assert "test_plugin.read" in ids
    assert "test_plugin.write_db" in ids


def test_get_registered_capability(registry: CapabilityRegistry) -> None:
    registry.register(MANIFEST_DATA)

    entry = registry.get("test_plugin.read")
    assert entry is not None
    assert entry.permissions == ["file.read"]


def test_get_unknown_capability_returns_none(registry: CapabilityRegistry) -> None:
    assert registry.get("unknown.cap") is None


def test_registry_empty_initially(registry: CapabilityRegistry) -> None:
    assert registry.list_all() == []


# ---------------------------------------------------------------------------
# Disallowed permissions block registration
# ---------------------------------------------------------------------------


def test_disallowed_permission_raises(registry: CapabilityRegistry) -> None:
    manifest = CapabilityManifest(
        name="bad_plugin",
        version="1.0",
        module="openbad.plugins.bad",
        capabilities=[
            CapabilityEntry(id="bad.elevate", permissions=["identity.elevate"]),
        ],
    )

    with pytest.raises(RegistrationError, match="identity.elevate"):
        registry.register(manifest)


def test_partial_disallowed_blocks_entire_manifest(registry: CapabilityRegistry) -> None:
    """All-or-nothing: if any capability fails, none are registered."""
    manifest = CapabilityManifest(
        name="mixed_plugin",
        version="1.0",
        module="openbad.plugins.mixed",
        capabilities=[
            CapabilityEntry(id="mixed.ok", permissions=["file.read"]),
            CapabilityEntry(id="mixed.banned", permissions=["ebpf.load"]),
        ],
    )

    with pytest.raises(RegistrationError):
        registry.register(manifest)

    assert registry.get("mixed.ok") is None


# ---------------------------------------------------------------------------
# System 1 capability inventory
# ---------------------------------------------------------------------------


def test_system1_capabilities_identified(registry: CapabilityRegistry) -> None:
    registry.register(MANIFEST_DATA)

    s1_ids = {e.capability_id for e in registry.list_system1()}
    assert "core_triage.triage" in s1_ids
    assert "test_plugin.read" not in s1_ids


def test_system1_flag_on_entry(registry: CapabilityRegistry) -> None:
    registry.register(MANIFEST_DATA)

    entry = registry.get("core_triage.triage")
    assert entry is not None
    assert entry.system1 is True


def test_non_system1_flag_is_false(registry: CapabilityRegistry) -> None:
    registry.register(MANIFEST_DATA)

    entry = registry.get("test_plugin.read")
    assert entry is not None
    assert entry.system1 is False


# ---------------------------------------------------------------------------
# Permission policy from YAML
# ---------------------------------------------------------------------------


def test_policy_from_yaml(tmp_path: Path) -> None:
    yaml_content = (
        "permissions:\n"
        "  READ:\n"
        "    - file.read\n"
        "  WRITE:\n"
        "    - db.insert\n"
    )
    f = tmp_path / "permissions.yaml"
    f.write_text(yaml_content)

    policy = PermissionPolicy.from_yaml(f)
    assert policy.is_allowed("file.read") is True
    assert policy.is_allowed("db.insert") is True
    assert policy.is_allowed("ebpf.load") is False


def test_policy_check_permissions_returns_violations() -> None:
    policy = PermissionPolicy({"file.read"})
    violations = policy.check_permissions(["file.read", "ebpf.load"])
    assert violations == ["ebpf.load"]


def test_policy_none_allows_everything() -> None:
    policy = PermissionPolicy(None)
    assert policy.is_allowed("anything") is True


def test_registry_with_real_permissions_yaml() -> None:
    """Integration: load from the actual repo config."""
    here = Path(__file__).parent.parent / "config" / "permissions.yaml"
    if not here.exists():
        pytest.skip("config/permissions.yaml not found")

    policy = PermissionPolicy.from_yaml(here)
    registry = CapabilityRegistry(policy)

    manifest = CapabilityManifest(
        name="approved",
        version="0.1",
        module="openbad.plugins.approved",
        capabilities=[
            CapabilityEntry(id="approved.read", permissions=["file.read"]),
        ],
    )
    entries = registry.register(manifest)
    assert len(entries) == 1
