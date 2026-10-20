"""Tests for openbad.identity.permissions — action permissioning."""

from __future__ import annotations

import pytest

from openbad.identity.permissions import (
    ActionTier,
    Permission,
    PermissionClassifier,
    PermissionResult,
    load_action_mappings,
)
from openbad.identity.session import SessionManager

# ---------------------------------------------------------------------------
# ActionTier enum
# ---------------------------------------------------------------------------


class TestActionTier:
    def test_values(self) -> None:
        assert ActionTier.READ.value == "READ"
        assert ActionTier.WRITE.value == "WRITE"
        assert ActionTier.PUBLISH.value == "PUBLISH"
        assert ActionTier.SYSTEM.value == "SYSTEM"


# ---------------------------------------------------------------------------
# Permission dataclass
# ---------------------------------------------------------------------------


class TestPermission:
    def test_read_permission(self) -> None:
        p = Permission("file.read", ActionTier.READ, False, False, False)
        assert not p.requires_identity
        assert not p.requires_confirmation
        assert not p.requires_elevated_auth

    def test_frozen(self) -> None:
        p = Permission("x", ActionTier.READ, False, False, False)
        with pytest.raises(AttributeError):
            p.tier = ActionTier.WRITE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_action_mappings
# ---------------------------------------------------------------------------


class TestLoadActionMappings:
    def test_load_from_yaml(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        yaml_path = tmp_path / "perms.yaml"
        yaml_path.write_text(
            "permissions:\n"
            "  READ:\n"
            "    - file.read\n"
            "  SYSTEM:\n"
            "    - ebpf.load\n"
        )
        mappings = load_action_mappings(yaml_path)
        assert mappings["file.read"] is ActionTier.READ
        assert mappings["ebpf.load"] is ActionTier.SYSTEM

    def test_missing_file(self) -> None:
        mappings = load_action_mappings("nonexistent.yaml")
        assert mappings == {}


# ---------------------------------------------------------------------------
# PermissionClassifier.classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_read_action(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.read": ActionTier.READ}
        )
        p = pc.classify("file.read")
        assert p.tier is ActionTier.READ
        assert not p.requires_identity

    def test_write_action(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.write": ActionTier.WRITE}
        )
        p = pc.classify("file.write")
        assert p.tier is ActionTier.WRITE
        assert p.requires_identity
        assert not p.requires_confirmation

    def test_publish_action(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"mqtt.publish": ActionTier.PUBLISH}
        )
        p = pc.classify("mqtt.publish")
        assert p.tier is ActionTier.PUBLISH
        assert p.requires_identity
        assert p.requires_confirmation
        assert not p.requires_elevated_auth

    def test_system_action(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"ebpf.load": ActionTier.SYSTEM}
        )
        p = pc.classify("ebpf.load")
        assert p.tier is ActionTier.SYSTEM
        assert p.requires_identity
        assert p.requires_confirmation
        assert p.requires_elevated_auth

    def test_unknown_action_gets_default(self) -> None:
        pc = PermissionClassifier(default_tier=ActionTier.WRITE)
        p = pc.classify("unknown.action")
        assert p.tier is ActionTier.WRITE

    def test_custom_default_tier(self) -> None:
        pc = PermissionClassifier(default_tier=ActionTier.READ)
        p = pc.classify("anything")
        assert p.tier is ActionTier.READ

    def test_yaml_mappings(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        yaml_path = tmp_path / "p.yaml"
        yaml_path.write_text(
            "permissions:\n"
            "  PUBLISH:\n"
            "    - api.call\n"
        )
        pc = PermissionClassifier(yaml_path=yaml_path)
        p = pc.classify("api.call")
        assert p.tier is ActionTier.PUBLISH


# ---------------------------------------------------------------------------
# check_permission — READ (always allowed)
# ---------------------------------------------------------------------------


class TestCheckPermissionRead:
    def test_read_no_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.read": ActionTier.READ}
        )
        mgr = SessionManager()
        r = pc.check_permission(mgr, None, "file.read")
        assert r.allowed is True
        assert r.tier is ActionTier.READ

    def test_read_with_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.read": ActionTier.READ}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(mgr, s.session_id, "file.read")
        assert r.allowed is True


# ---------------------------------------------------------------------------
# check_permission — WRITE (identity required)
# ---------------------------------------------------------------------------


class TestCheckPermissionWrite:
    def test_write_with_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.write": ActionTier.WRITE}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(mgr, s.session_id, "file.write")
        assert r.allowed is True

    def test_write_no_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.write": ActionTier.WRITE}
        )
        mgr = SessionManager()
        r = pc.check_permission(mgr, None, "file.write")
        assert r.allowed is False
        assert "session required" in r.reason.lower()

    def test_write_invalid_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"file.write": ActionTier.WRITE}
        )
        mgr = SessionManager()
        r = pc.check_permission(mgr, "bogus", "file.write")
        assert r.allowed is False


# ---------------------------------------------------------------------------
# check_permission — PUBLISH (identity + confirmation)
# ---------------------------------------------------------------------------


class TestCheckPermissionPublish:
    def test_publish_fully_authorised(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"mqtt.publish": ActionTier.PUBLISH}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(
            mgr, s.session_id, "mqtt.publish",
            user_confirmed=True,
        )
        assert r.allowed is True

    def test_publish_no_confirmation(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"mqtt.publish": ActionTier.PUBLISH}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(
            mgr, s.session_id, "mqtt.publish",
            user_confirmed=False,
        )
        assert r.allowed is False
        assert "confirmation" in r.reason.lower()

    def test_publish_no_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"mqtt.publish": ActionTier.PUBLISH}
        )
        mgr = SessionManager()
        r = pc.check_permission(mgr, None, "mqtt.publish")
        assert r.allowed is False


# ---------------------------------------------------------------------------
# check_permission — SYSTEM (identity + confirmation + elevated)
# ---------------------------------------------------------------------------


class TestCheckPermissionSystem:
    def test_system_fully_authorised(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"ebpf.load": ActionTier.SYSTEM}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(
            mgr, s.session_id, "ebpf.load",
            user_confirmed=True,
            elevated_auth=True,
        )
        assert r.allowed is True

    def test_system_no_elevated(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"ebpf.load": ActionTier.SYSTEM}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(
            mgr, s.session_id, "ebpf.load",
            user_confirmed=True,
            elevated_auth=False,
        )
        assert r.allowed is False
        assert "elevated" in r.reason.lower()

    def test_system_no_confirmation(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"ebpf.load": ActionTier.SYSTEM}
        )
        mgr = SessionManager()
        s = mgr.create_session("alice")
        r = pc.check_permission(
            mgr, s.session_id, "ebpf.load",
            user_confirmed=False,
            elevated_auth=True,
        )
        assert r.allowed is False
        assert "confirmation" in r.reason.lower()

    def test_system_no_session(self) -> None:
        pc = PermissionClassifier(
            action_mappings={"ebpf.load": ActionTier.SYSTEM}
        )
        mgr = SessionManager()
        r = pc.check_permission(mgr, None, "ebpf.load")
        assert r.allowed is False


# ---------------------------------------------------------------------------
# PermissionResult
# ---------------------------------------------------------------------------


class TestPermissionResult:
    def test_allowed_result(self) -> None:
        r = PermissionResult(True, "file.read", ActionTier.READ)
        assert r.allowed
        assert r.reason == ""

    def test_denied_result(self) -> None:
        r = PermissionResult(
            False, "ebpf.load", ActionTier.SYSTEM, "Denied"
        )
        assert not r.allowed
        assert r.reason == "Denied"
