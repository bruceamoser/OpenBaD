"""MCP access boundary enforcement for Phase 9.

Only ``TaskExecutor`` and ``Research`` execution contexts are permitted to
open MCP sessions.  Heartbeat, background, and reflex contexts are explicitly
denied.

The :class:`MCPAccessGuard` is a thin check layer meant to be called at MCP
bridge entry points before :class:`~openbad.plugins.mcp_policy.MCPSessionManager`
is used.
"""

from __future__ import annotations

from enum import auto

from openbad.plugins.mcp_policy import MCPPolicy, MCPSession, MCPSessionManager
from openbad.tasks.models import StrEnum

# ---------------------------------------------------------------------------
# Execution context taxonomy
# ---------------------------------------------------------------------------


class ExecutionContext(StrEnum):
    """Runtime context in which an MCP session is requested."""

    TASK = auto()
    RESEARCH = auto()
    HEARTBEAT = auto()
    REFLEX = auto()
    BACKGROUND = auto()


# Contexts that may open MCP sessions
_ALLOWED_CONTEXTS: frozenset[ExecutionContext] = frozenset(
    {ExecutionContext.TASK, ExecutionContext.RESEARCH}
)

# Contexts explicitly denied (informational — _ALLOWED_CONTEXTS is the gate)
_DENIED_CONTEXTS: frozenset[ExecutionContext] = frozenset(
    {ExecutionContext.HEARTBEAT, ExecutionContext.REFLEX, ExecutionContext.BACKGROUND}
)


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class MCPAccessDeniedError(PermissionError):
    """Raised when a disallowed execution context requests MCP access."""


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class MCPAccessGuard:
    """Entry-point guard that enforces context-based MCP access boundaries.

    Parameters
    ----------
    session_manager:
        The :class:`~openbad.plugins.mcp_policy.MCPSessionManager` to delegate
        session creation to when access is permitted.
    """

    def __init__(self, session_manager: MCPSessionManager) -> None:
        self._manager = session_manager

    def open_session(
        self,
        context: ExecutionContext,
        policy: MCPPolicy,
        *,
        session_id: str | None = None,
    ) -> MCPSession:
        """Open an MCP session, enforcing context allow-list.

        Parameters
        ----------
        context:
            The execution context requesting access.
        policy:
            The session policy to apply.
        session_id:
            Optional explicit session identifier.

        Returns
        -------
        MCPSession
            The newly created session.

        Raises
        ------
        MCPAccessDeniedError
            If *context* is not in the allowed set.
        """
        if context not in _ALLOWED_CONTEXTS:
            raise MCPAccessDeniedError(
                f"Execution context {context.value!r} is not permitted to open MCP sessions"
            )
        return self._manager.create_session(policy, session_id=session_id)

    @staticmethod
    def is_allowed(context: ExecutionContext) -> bool:
        """Return ``True`` if *context* may open MCP sessions."""
        return context in _ALLOWED_CONTEXTS
