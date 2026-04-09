"""Identity grounding — multi-source operator identity resolution."""

from __future__ import annotations

import getpass
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

import bcrypt

# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class SourceType(Enum):
    """Categories of identity sources."""

    HARDWARE_TOKEN = "hardware_token"  # noqa: S105
    BIOMETRIC = "biometric"
    PASSPHRASE = "passphrase"  # noqa: S105
    ENVIRONMENT = "environment"


# Strength weights used for confidence scoring (0.0–1.0 contribution each).
_SOURCE_STRENGTH: dict[SourceType, float] = {
    SourceType.HARDWARE_TOKEN: 1.0,
    SourceType.BIOMETRIC: 0.9,
    SourceType.PASSPHRASE: 0.7,
    SourceType.ENVIRONMENT: 0.4,
}


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a single identity-source verification."""

    verified: bool
    source_type: SourceType
    user_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class GroundedIdentity:
    """An operator identity established from multiple sources."""

    identity_id: str
    user_id: str
    sources_used: list[SourceType] = field(default_factory=list)
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# IdentitySource protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentitySource(Protocol):
    """Interface that every identity source must implement."""

    @property
    def source_type(self) -> SourceType: ...

    def verify(self) -> VerificationResult: ...


# ---------------------------------------------------------------------------
# Concrete sources
# ---------------------------------------------------------------------------


class HardwareTokenSource:
    """Stub for USB/TPM hardware token (interface only in Phase 3)."""

    source_type = SourceType.HARDWARE_TOKEN

    def verify(self) -> VerificationResult:
        return VerificationResult(
            verified=False,
            source_type=self.source_type,
            detail="Hardware token verification not implemented",
        )


class BiometricSource:
    """Stub for biometric proxy (voice fingerprint, etc.)."""

    source_type = SourceType.BIOMETRIC

    def verify(self) -> VerificationResult:
        return VerificationResult(
            verified=False,
            source_type=self.source_type,
            detail="Biometric verification not implemented",
        )


class PassphraseSource:
    """Passphrase-based identity source (bcrypt-hashed)."""

    source_type = SourceType.PASSPHRASE

    def __init__(
        self,
        passphrase_hash: bytes,
        user_id: str,
        *,
        passphrase_input: str | None = None,
    ) -> None:
        self._hash = passphrase_hash
        self._user_id = user_id
        self._input = passphrase_input

    @staticmethod
    def hash_passphrase(passphrase: str) -> bytes:
        """Return a bcrypt hash of *passphrase*."""
        return bcrypt.hashpw(passphrase.encode(), bcrypt.gensalt())

    def verify(self) -> VerificationResult:
        if self._input is None:
            return VerificationResult(
                verified=False,
                source_type=self.source_type,
                detail="No passphrase provided",
            )
        ok = bcrypt.checkpw(self._input.encode(), self._hash)
        return VerificationResult(
            verified=ok,
            source_type=self.source_type,
            user_id=self._user_id if ok else "",
            detail="" if ok else "Passphrase mismatch",
        )


class EnvironmentSource:
    """Identity from login-user / SSH / sudo context."""

    source_type = SourceType.ENVIRONMENT

    def __init__(self, *, expected_user: str | None = None) -> None:
        self._expected = expected_user

    def verify(self) -> VerificationResult:
        current_user = os.environ.get("USER") or getpass.getuser()
        if self._expected is not None and current_user != self._expected:
            return VerificationResult(
                verified=False,
                source_type=self.source_type,
                detail=f"Expected user '{self._expected}', got '{current_user}'",
            )
        return VerificationResult(
            verified=True,
            source_type=self.source_type,
            user_id=current_user,
        )


# ---------------------------------------------------------------------------
# IdentityGrounder
# ---------------------------------------------------------------------------


class IdentityGrounder:
    """Establishes operator identity from multiple signals.

    Parameters
    ----------
    min_sources:
        Minimum number of verified sources required (default 2).
    """

    def __init__(self, *, min_sources: int = 2) -> None:
        if min_sources < 1:
            raise ValueError("min_sources must be >= 1")
        self._min_sources = min_sources

    @property
    def min_sources(self) -> int:
        return self._min_sources

    def ground_identity(
        self,
        sources: list[IdentitySource],
    ) -> GroundedIdentity | None:
        """Attempt to establish a grounded identity from *sources*.

        Returns ``None`` if fewer than *min_sources* verify successfully.
        """
        verified: list[VerificationResult] = []
        for src in sources:
            result = src.verify()
            if result.verified:
                verified.append(result)

        if len(verified) < self._min_sources:
            return None

        # Resolve user_id: pick from the first source that supplied one.
        user_id = ""
        for v in verified:
            if v.user_id:
                user_id = v.user_id
                break

        confidence = self._compute_confidence(verified)
        return GroundedIdentity(
            identity_id=uuid.uuid4().hex,
            user_id=user_id,
            sources_used=[v.source_type for v in verified],
            confidence=confidence,
        )

    @staticmethod
    def _compute_confidence(verified: list[VerificationResult]) -> float:
        """Compute a 0.0–1.0 confidence from verified source strengths."""
        if not verified:
            return 0.0
        total = sum(
            _SOURCE_STRENGTH.get(v.source_type, 0.5) for v in verified
        )
        max_possible = sum(_SOURCE_STRENGTH.values())
        return min(total / max_possible, 1.0)
