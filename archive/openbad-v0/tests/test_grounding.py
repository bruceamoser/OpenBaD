"""Tests for openbad.identity.grounding — multi-source identity resolution."""

from __future__ import annotations

import pytest

from openbad.identity.grounding import (
    BiometricSource,
    EnvironmentSource,
    GroundedIdentity,
    HardwareTokenSource,
    IdentityGrounder,
    IdentitySource,
    PassphraseSource,
    SourceType,
    VerificationResult,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight stubs for tests
# ---------------------------------------------------------------------------


class _AlwaysOk:
    """Source that always verifies."""

    source_type = SourceType.PASSPHRASE

    def __init__(self, user_id: str = "test") -> None:
        self._user_id = user_id

    def verify(self) -> VerificationResult:
        return VerificationResult(
            verified=True,
            source_type=self.source_type,
            user_id=self._user_id,
        )


class _AlwaysFail:
    """Source that always fails."""

    source_type = SourceType.HARDWARE_TOKEN

    def verify(self) -> VerificationResult:
        return VerificationResult(
            verified=False,
            source_type=self.source_type,
            detail="always fails",
        )


# ---------------------------------------------------------------------------
# SourceType / VerificationResult
# ---------------------------------------------------------------------------


class TestSourceType:
    def test_values(self) -> None:
        assert SourceType.HARDWARE_TOKEN.value == "hardware_token"
        assert SourceType.BIOMETRIC.value == "biometric"
        assert SourceType.PASSPHRASE.value == "passphrase"
        assert SourceType.ENVIRONMENT.value == "environment"


class TestVerificationResult:
    def test_frozen(self) -> None:
        v = VerificationResult(True, SourceType.PASSPHRASE, "alice")
        with pytest.raises(AttributeError):
            v.verified = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GroundedIdentity
# ---------------------------------------------------------------------------


class TestGroundedIdentity:
    def test_defaults(self) -> None:
        gi = GroundedIdentity(identity_id="abc", user_id="alice")
        assert gi.sources_used == []
        assert gi.confidence == 0.0
        assert gi.timestamp > 0


# ---------------------------------------------------------------------------
# HardwareTokenSource (stub)
# ---------------------------------------------------------------------------


class TestHardwareTokenSource:
    def test_not_implemented(self) -> None:
        src = HardwareTokenSource()
        result = src.verify()
        assert result.verified is False
        assert result.source_type is SourceType.HARDWARE_TOKEN
        assert "not implemented" in result.detail.lower()


# ---------------------------------------------------------------------------
# BiometricSource (stub)
# ---------------------------------------------------------------------------


class TestBiometricSource:
    def test_not_implemented(self) -> None:
        src = BiometricSource()
        result = src.verify()
        assert result.verified is False
        assert result.source_type is SourceType.BIOMETRIC


# ---------------------------------------------------------------------------
# PassphraseSource
# ---------------------------------------------------------------------------


class TestPassphraseSource:
    def test_correct_passphrase(self) -> None:
        pw = "hunter2"
        hashed = PassphraseSource.hash_passphrase(pw)
        src = PassphraseSource(hashed, "alice", passphrase_input=pw)
        result = src.verify()
        assert result.verified is True
        assert result.user_id == "alice"

    def test_wrong_passphrase(self) -> None:
        hashed = PassphraseSource.hash_passphrase("correct")
        src = PassphraseSource(hashed, "alice", passphrase_input="wrong")  # noqa: S106
        result = src.verify()
        assert result.verified is False
        assert result.user_id == ""

    def test_no_input(self) -> None:
        hashed = PassphraseSource.hash_passphrase("x")
        src = PassphraseSource(hashed, "alice")
        result = src.verify()
        assert result.verified is False
        assert "no passphrase" in result.detail.lower()

    def test_hash_is_bytes(self) -> None:
        h = PassphraseSource.hash_passphrase("test")
        assert isinstance(h, bytes)
        assert h.startswith(b"$2")

    def test_conforms_to_protocol(self) -> None:
        hashed = PassphraseSource.hash_passphrase("x")
        src = PassphraseSource(hashed, "a", passphrase_input="x")  # noqa: S106
        assert isinstance(src, IdentitySource)


# ---------------------------------------------------------------------------
# EnvironmentSource
# ---------------------------------------------------------------------------


class TestEnvironmentSource:
    def test_no_expected_user(self) -> None:
        src = EnvironmentSource()
        result = src.verify()
        assert result.verified is True
        assert result.user_id != ""

    def test_expected_user_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "testuser")
        src = EnvironmentSource(expected_user="testuser")
        result = src.verify()
        assert result.verified is True
        assert result.user_id == "testuser"

    def test_expected_user_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "someone_else")
        src = EnvironmentSource(expected_user="alice")
        result = src.verify()
        assert result.verified is False

    def test_conforms_to_protocol(self) -> None:
        assert isinstance(EnvironmentSource(), IdentitySource)


# ---------------------------------------------------------------------------
# IdentityGrounder — basic
# ---------------------------------------------------------------------------


class TestIdentityGrounder:
    def test_min_sources_default(self) -> None:
        g = IdentityGrounder()
        assert g.min_sources == 2

    def test_min_sources_invalid(self) -> None:
        with pytest.raises(ValueError, match="min_sources"):
            IdentityGrounder(min_sources=0)

    def test_two_ok_sources(self) -> None:
        g = IdentityGrounder(min_sources=2)
        result = g.ground_identity([_AlwaysOk("alice"), _AlwaysOk("alice")])
        assert result is not None
        assert result.user_id == "alice"
        assert len(result.sources_used) == 2
        assert result.confidence > 0.0

    def test_one_ok_insufficient(self) -> None:
        g = IdentityGrounder(min_sources=2)
        result = g.ground_identity([_AlwaysOk("alice")])
        assert result is None

    def test_all_fail(self) -> None:
        g = IdentityGrounder(min_sources=1)
        result = g.ground_identity([_AlwaysFail(), _AlwaysFail()])
        assert result is None

    def test_mixed_sources(self) -> None:
        g = IdentityGrounder(min_sources=1)
        result = g.ground_identity([_AlwaysFail(), _AlwaysOk("bob")])
        assert result is not None
        assert result.user_id == "bob"
        assert len(result.sources_used) == 1

    def test_single_source_grounding(self) -> None:
        g = IdentityGrounder(min_sources=1)
        result = g.ground_identity([_AlwaysOk("alice")])
        assert result is not None

    def test_identity_id_is_unique(self) -> None:
        g = IdentityGrounder(min_sources=1)
        r1 = g.ground_identity([_AlwaysOk("a")])
        r2 = g.ground_identity([_AlwaysOk("a")])
        assert r1 is not None and r2 is not None
        assert r1.identity_id != r2.identity_id


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_stronger_sources_higher_confidence(self) -> None:
        g = IdentityGrounder(min_sources=1)

        class _HwOk:
            source_type = SourceType.HARDWARE_TOKEN

            def verify(self) -> VerificationResult:
                return VerificationResult(True, self.source_type, "u")

        class _EnvOk:
            source_type = SourceType.ENVIRONMENT

            def verify(self) -> VerificationResult:
                return VerificationResult(True, self.source_type, "u")

        hw = g.ground_identity([_HwOk()])
        env = g.ground_identity([_EnvOk()])
        assert hw is not None and env is not None
        assert hw.confidence > env.confidence

    def test_more_sources_higher_confidence(self) -> None:
        g = IdentityGrounder(min_sources=1)
        r1 = g.ground_identity([_AlwaysOk("a")])
        r2 = g.ground_identity([_AlwaysOk("a"), _AlwaysOk("a")])
        assert r1 is not None and r2 is not None
        assert r2.confidence > r1.confidence

    def test_all_sources_cap_at_one(self) -> None:
        g = IdentityGrounder(min_sources=1)

        class _FullStrength:
            source_type = SourceType.HARDWARE_TOKEN

            def verify(self) -> VerificationResult:
                return VerificationResult(True, self.source_type, "u")

        # Even with redundant max-strength sources, capped at 1.0.
        r = g.ground_identity(
            [_FullStrength(), _FullStrength(), _FullStrength(), _FullStrength()]
        )
        assert r is not None
        assert r.confidence <= 1.0


# ---------------------------------------------------------------------------
# Integration-style: passphrase + environment
# ---------------------------------------------------------------------------


class TestGroundingWithRealSources:
    def test_passphrase_plus_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "alice")
        pw = "s3cret"
        hashed = PassphraseSource.hash_passphrase(pw)
        sources: list[IdentitySource] = [
            PassphraseSource(hashed, "alice", passphrase_input=pw),
            EnvironmentSource(expected_user="alice"),
        ]
        g = IdentityGrounder(min_sources=2)
        result = g.ground_identity(sources)
        assert result is not None
        assert result.user_id == "alice"
        assert SourceType.PASSPHRASE in result.sources_used
        assert SourceType.ENVIRONMENT in result.sources_used

    def test_passphrase_wrong_and_env_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USER", "alice")
        hashed = PassphraseSource.hash_passphrase("correct")
        sources: list[IdentitySource] = [
            PassphraseSource(hashed, "alice", passphrase_input="wrong"),  # noqa: S106
            EnvironmentSource(expected_user="alice"),
        ]
        g = IdentityGrounder(min_sources=2)
        result = g.ground_identity(sources)
        assert result is None  # Only 1 of 2 verified
