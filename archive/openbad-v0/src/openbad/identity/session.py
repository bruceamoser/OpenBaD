"""Session lifecycle management with HMAC-SHA256 session markers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from openbad.identity.marker import create_marker, generate_secret, verify_marker


@dataclass(frozen=True)
class Session:
    """An authenticated session bound to a user identity."""

    session_id: str
    user_identity: str
    created_at: float
    expires_at: float
    marker: str


# Default rotation interval: 1 hour (seconds)
DEFAULT_ROTATION_INTERVAL: float = 3600.0


class SessionManager:
    """Manages session lifecycle: create, validate, rotate, end.

    Parameters
    ----------
    secret:
        HMAC secret for marker generation.  If *None* an ephemeral
        key is generated (dev/test only).
    rotation_interval:
        Seconds between marker rotations (default 1 hour).
    default_ttl:
        Default session time-to-live in seconds (default 8 hours).
    """

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        rotation_interval: float = DEFAULT_ROTATION_INTERVAL,
        default_ttl: float = 8 * 3600.0,
    ) -> None:
        self._secret = secret or generate_secret()
        self._rotation_interval = rotation_interval
        self._default_ttl = default_ttl
        self._sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self, user_identity: str) -> Session:
        """Create a new session for *user_identity*."""
        session_id = uuid.uuid4().hex
        now = time.time()
        marker_data = f"{session_id}:{user_identity}:{now}"
        marker = create_marker(marker_data, self._secret)

        session = Session(
            session_id=session_id,
            user_identity=user_identity,
            created_at=now,
            expires_at=now + self._default_ttl,
            marker=marker,
        )
        self._sessions[session_id] = session
        return session

    def validate_session(self, session_id: str) -> Session | None:
        """Return the session if valid and not expired, else ``None``."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() > session.expires_at:
            # Expired — remove it
            del self._sessions[session_id]
            return None
        return session

    def rotate_marker(self, session_id: str) -> Session | None:
        """Rotate the HMAC marker for session *session_id*.

        Returns the updated session or ``None`` if the session is
        invalid / expired.
        """
        session = self.validate_session(session_id)
        if session is None:
            return None

        now = time.time()
        marker_data = f"{session.session_id}:{session.user_identity}:{now}"
        new_marker = create_marker(marker_data, self._secret)

        rotated = Session(
            session_id=session.session_id,
            user_identity=session.user_identity,
            created_at=session.created_at,
            expires_at=session.expires_at,
            marker=new_marker,
        )
        self._sessions[session.session_id] = rotated
        return rotated

    def end_session(self, session_id: str) -> bool:
        """End and remove a session.  Returns ``True`` if it existed."""
        return self._sessions.pop(session_id, None) is not None

    def needs_rotation(self, session: Session) -> bool:
        """Return ``True`` if the session marker needs rotation."""
        # The marker encodes the time it was created in its data;
        # we check whether enough time has passed since the last rotation.
        # For simplicity we check age of the marker relative to created_at
        # vs the rotation interval.
        age = time.time() - session.created_at
        # Number of rotations that should have happened
        expected_rotations = int(age / self._rotation_interval)
        return expected_rotations > 0

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    def verify_session_marker(self, session: Session) -> bool:
        """Verify a session's marker against its identity data."""
        return verify_marker(
            f"{session.session_id}:{session.user_identity}:{session.created_at}",
            session.marker,
            self._secret,
        )
