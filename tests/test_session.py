"""Tests for openbad.identity — session lifecycle and marker management."""

from __future__ import annotations

import time

import pytest

from openbad.identity.marker import (
    create_marker,
    generate_secret,
    load_secret,
    read_marker_file,
    save_marker_file,
    verify_marker,
)
from openbad.identity.session import Session, SessionManager

# ===================================================================
# Marker tests
# ===================================================================


class TestGenerateSecret:
    def test_default_length(self) -> None:
        s = generate_secret()
        assert len(s) == 32

    def test_custom_length(self) -> None:
        s = generate_secret(64)
        assert len(s) == 64

    def test_two_secrets_differ(self) -> None:
        assert generate_secret() != generate_secret()


class TestCreateAndVerifyMarker:
    def test_round_trip(self) -> None:
        secret = generate_secret()
        marker = create_marker("test-data", secret)
        assert verify_marker("test-data", marker, secret)

    def test_wrong_data(self) -> None:
        secret = generate_secret()
        marker = create_marker("test-data", secret)
        assert not verify_marker("wrong-data", marker, secret)

    def test_wrong_secret(self) -> None:
        s1 = generate_secret()
        s2 = generate_secret()
        marker = create_marker("data", s1)
        assert not verify_marker("data", marker, s2)

    def test_hex_string(self) -> None:
        secret = generate_secret()
        marker = create_marker("data", secret)
        # HMAC-SHA256 produces a 64-char hex string
        assert len(marker) == 64
        int(marker, 16)  # Should not raise


class TestMarkerFile:
    def test_save_and_read(self, tmp_path: pytest.TempPathFactory) -> None:
        path = tmp_path / "markers" / "session.marker"
        save_marker_file("abc123", path)
        assert read_marker_file(path) == "abc123"

    def test_permissions_not_error(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """save_marker_file should not raise even on Windows."""
        path = tmp_path / "m.marker"
        save_marker_file("test", path)
        assert path.exists()


class TestLoadSecret:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hex_key = generate_secret().hex()
        monkeypatch.setenv("OPENBAD_IDENTITY_SECRET", hex_key)
        secret = load_secret(yaml_path="nonexistent.yaml")
        assert secret == bytes.fromhex(hex_key)

    def test_from_yaml(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        yaml_path = tmp_path / "identity.yaml"
        hex_key = generate_secret().hex()
        yaml_path.write_text(
            f"identity:\n  secret_hex: \"{hex_key}\"\n"
        )
        secret = load_secret(
            yaml_path=yaml_path,
            env_var="NONEXISTENT_VAR",
        )
        assert secret == bytes.fromhex(hex_key)

    def test_ephemeral_fallback(self) -> None:
        secret = load_secret(
            yaml_path="nonexistent.yaml",
            env_var="NONEXISTENT_VAR",
        )
        assert len(secret) == 32


# ===================================================================
# Session tests
# ===================================================================


class TestSessionDataclass:
    def test_fields(self) -> None:
        s = Session(
            session_id="abc",
            user_identity="user1",
            created_at=1.0,
            expires_at=2.0,
            marker="deadbeef",
        )
        assert s.session_id == "abc"
        assert s.user_identity == "user1"

    def test_frozen(self) -> None:
        s = Session(
            session_id="x",
            user_identity="u",
            created_at=0,
            expires_at=0,
            marker="m",
        )
        with pytest.raises(AttributeError):
            s.marker = "new"  # type: ignore[misc]


class TestCreateSession:
    def test_creates_valid_session(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        assert s.user_identity == "alice"
        assert s.session_id
        assert s.marker
        assert s.expires_at > s.created_at

    def test_unique_ids(self) -> None:
        mgr = SessionManager()
        s1 = mgr.create_session("alice")
        s2 = mgr.create_session("bob")
        assert s1.session_id != s2.session_id

    def test_different_markers(self) -> None:
        mgr = SessionManager()
        s1 = mgr.create_session("alice")
        s2 = mgr.create_session("alice")
        # Even the same user gets different markers (different session_id + time)
        assert s1.marker != s2.marker


class TestValidateSession:
    def test_valid_session(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        assert mgr.validate_session(s.session_id) is not None

    def test_unknown_session(self) -> None:
        mgr = SessionManager()
        assert mgr.validate_session("nonexistent") is None

    def test_expired_session(self) -> None:
        mgr = SessionManager(default_ttl=0.0)
        s = mgr.create_session("alice")
        time.sleep(0.01)
        assert mgr.validate_session(s.session_id) is None

    def test_expired_session_removed(self) -> None:
        mgr = SessionManager(default_ttl=0.0)
        s = mgr.create_session("alice")
        time.sleep(0.01)
        mgr.validate_session(s.session_id)
        assert mgr.active_sessions == 0


class TestRotateMarker:
    def test_rotation_changes_marker(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        old_marker = s.marker
        rotated = mgr.rotate_marker(s.session_id)
        assert rotated is not None
        assert rotated.marker != old_marker

    def test_rotation_preserves_identity(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        rotated = mgr.rotate_marker(s.session_id)
        assert rotated is not None
        assert rotated.session_id == s.session_id
        assert rotated.user_identity == s.user_identity
        assert rotated.created_at == s.created_at
        assert rotated.expires_at == s.expires_at

    def test_rotate_expired_returns_none(self) -> None:
        mgr = SessionManager(default_ttl=0.0)
        s = mgr.create_session("alice")
        time.sleep(0.01)
        assert mgr.rotate_marker(s.session_id) is None

    def test_rotate_unknown_returns_none(self) -> None:
        mgr = SessionManager()
        assert mgr.rotate_marker("nonexistent") is None


class TestEndSession:
    def test_end_existing(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        assert mgr.end_session(s.session_id) is True
        assert mgr.validate_session(s.session_id) is None

    def test_end_unknown(self) -> None:
        mgr = SessionManager()
        assert mgr.end_session("nonexistent") is False

    def test_active_count_decreases(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        assert mgr.active_sessions == 1
        mgr.end_session(s.session_id)
        assert mgr.active_sessions == 0


class TestNeedsRotation:
    def test_fresh_session_no_rotation(self) -> None:
        mgr = SessionManager(rotation_interval=3600.0)
        s = mgr.create_session("alice")
        assert mgr.needs_rotation(s) is False

    def test_old_session_needs_rotation(self) -> None:
        mgr = SessionManager(rotation_interval=0.01)
        s = mgr.create_session("alice")
        time.sleep(0.02)
        assert mgr.needs_rotation(s) is True


class TestVerifySessionMarker:
    def test_verify_initial_marker(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        assert mgr.verify_session_marker(s) is True

    def test_tampered_marker_fails(self) -> None:
        mgr = SessionManager()
        s = mgr.create_session("alice")
        tampered = Session(
            session_id=s.session_id,
            user_identity=s.user_identity,
            created_at=s.created_at,
            expires_at=s.expires_at,
            marker="0" * 64,
        )
        assert mgr.verify_session_marker(tampered) is False
