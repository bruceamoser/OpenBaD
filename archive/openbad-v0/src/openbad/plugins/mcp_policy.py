"""MCP Policy and scoped session primitives for Phase 9.

This module provides :class:`MCPPolicy`, which declares per-session resource
limits, and :class:`MCPSession`, which tracks live per-session state.
:class:`MCPSessionManager` manages the full lifecycle (create / close).

Session limits are enforced at the metadata layer: callers check
:meth:`MCPSession.check_limit` before executing tools.  Actual execution is
*not* performed here; this module is intentionally thin.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from enum import auto

from openbad.tasks.models import StrEnum

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class MCPScope(StrEnum):
    """Access scope granted to a session."""

    READ = auto()
    WRITE = auto()
    ADMIN = auto()


@dataclasses.dataclass(frozen=True)
class MCPPolicy:
    """Declared resource limits for an MCP session.

    Parameters
    ----------
    max_tools:
        Maximum number of distinct tool names the session may call.
        ``None`` means unlimited.
    max_calls:
        Maximum total tool calls (across all tools) in the session lifetime.
        ``None`` means unlimited.
    allowed_scopes:
        Scopes the session is permitted to use.  Defaults to ``{READ}``.
    """

    max_tools: int | None = None
    max_calls: int | None = None
    allowed_scopes: frozenset[MCPScope] = dataclasses.field(
        default_factory=lambda: frozenset({MCPScope.READ})
    )

    def allows_scope(self, scope: MCPScope) -> bool:
        """Return ``True`` if *scope* is in :attr:`allowed_scopes`."""
        return scope in self.allowed_scopes

    def to_dict(self) -> dict:
        return {
            "max_tools": self.max_tools,
            "max_calls": self.max_calls,
            "allowed_scopes": sorted(s.value for s in self.allowed_scopes),
        }


class PolicyViolationError(ValueError):
    """Raised when a session limit or scope is violated."""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionStatus(StrEnum):
    OPEN = auto()
    CLOSED = auto()


@dataclasses.dataclass
class MCPSession:
    """Tracks live per-session state against a :class:`MCPPolicy`.

    Parameters
    ----------
    session_id:
        Unique identifier for this session.
    policy:
        The policy that governs limits for this session.
    created_at:
        When the session was opened.
    status:
        Whether the session is :attr:`SessionStatus.OPEN` or
        :attr:`SessionStatus.CLOSED`.
    tools_called:
        Mapping of tool name → call count seen in this session.
    """

    session_id: str
    policy: MCPPolicy
    created_at: datetime
    status: SessionStatus = SessionStatus.OPEN
    tools_called: dict[str, int] = dataclasses.field(default_factory=dict)

    # ------------------------------------------------------------------
    # Limit helpers
    # ------------------------------------------------------------------

    @property
    def total_calls(self) -> int:
        return sum(self.tools_called.values())

    @property
    def distinct_tools(self) -> int:
        return len(self.tools_called)

    def check_limit(self, tool_name: str) -> None:
        """Raise :class:`PolicyViolationError` if a limit would be exceeded.

        This must be called *before* executing a tool.  It does *not* record
        the call; use :meth:`record_call` after execution.

        Parameters
        ----------
        tool_name:
            The tool about to be called.

        Raises
        ------
        PolicyViolationError
            If the session is closed, or a call or tool limit would be hit.
        """
        if self.status == SessionStatus.CLOSED:
            raise PolicyViolationError("Session is closed")

        if self.policy.max_calls is not None and self.total_calls >= self.policy.max_calls:
            raise PolicyViolationError(
                f"call limit {self.policy.max_calls} reached"
            )

        if (
            self.policy.max_tools is not None
            and tool_name not in self.tools_called
            and self.distinct_tools >= self.policy.max_tools
        ):
            raise PolicyViolationError(
                f"tool limit {self.policy.max_tools} reached"
            )

    def record_call(self, tool_name: str) -> None:
        """Record a tool call after successful execution."""
        self.tools_called[tool_name] = self.tools_called.get(tool_name, 0) + 1

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "policy": self.policy.to_dict(),
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "tools_called": dict(self.tools_called),
            "total_calls": self.total_calls,
            "distinct_tools": self.distinct_tools,
        }


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class MCPSessionManager:
    """Manages the lifecycle of :class:`MCPSession` objects.

    Sessions are kept in memory keyed by :attr:`MCPSession.session_id`.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, MCPSession] = {}

    def create_session(
        self,
        policy: MCPPolicy,
        *,
        session_id: str | None = None,
    ) -> MCPSession:
        """Create and return a new open session.

        Parameters
        ----------
        policy:
            The policy to attach to this session.
        session_id:
            Optional explicit ID.  A UUID4 is generated if omitted.
        """
        sid = session_id or str(uuid.uuid4())
        if sid in self._sessions:
            raise ValueError(f"Session {sid!r} already exists")

        session = MCPSession(
            session_id=sid,
            policy=policy,
            created_at=datetime.now(tz=UTC),
        )
        self._sessions[sid] = session
        return session

    def get_session(self, session_id: str) -> MCPSession | None:
        """Return the session with *session_id*, or ``None``."""
        return self._sessions.get(session_id)

    def close_session(self, session_id: str) -> MCPSession:
        """Mark the session as closed.

        Raises
        ------
        KeyError
            If no session with *session_id* exists.
        """
        session = self._sessions[session_id]
        session.status = SessionStatus.CLOSED
        return session

    def list_open(self) -> list[MCPSession]:
        """Return all currently open sessions."""
        return [s for s in self._sessions.values() if s.status == SessionStatus.OPEN]
