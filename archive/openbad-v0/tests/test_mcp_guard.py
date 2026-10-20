from __future__ import annotations

import pytest

from openbad.plugins.mcp_guard import (
    ExecutionContext,
    MCPAccessDeniedError,
    MCPAccessGuard,
)
from openbad.plugins.mcp_policy import MCPPolicy, MCPSessionManager, SessionStatus


@pytest.fixture()
def guard() -> MCPAccessGuard:
    return MCPAccessGuard(MCPSessionManager())


@pytest.fixture()
def policy() -> MCPPolicy:
    return MCPPolicy()


# ---------------------------------------------------------------------------
# Allowed contexts
# ---------------------------------------------------------------------------


def test_task_context_allowed(guard: MCPAccessGuard, policy: MCPPolicy) -> None:
    session = guard.open_session(ExecutionContext.TASK, policy)
    assert session.status == SessionStatus.OPEN


def test_research_context_allowed(guard: MCPAccessGuard, policy: MCPPolicy) -> None:
    session = guard.open_session(ExecutionContext.RESEARCH, policy)
    assert session.status == SessionStatus.OPEN


def test_task_context_with_explicit_policy(guard: MCPAccessGuard) -> None:
    pol = MCPPolicy(max_calls=5)
    session = guard.open_session(ExecutionContext.TASK, pol)
    assert session.policy.max_calls == 5


# ---------------------------------------------------------------------------
# Denied contexts
# ---------------------------------------------------------------------------


def test_heartbeat_context_denied(guard: MCPAccessGuard, policy: MCPPolicy) -> None:
    with pytest.raises(MCPAccessDeniedError, match="heartbeat"):
        guard.open_session(ExecutionContext.HEARTBEAT, policy)


def test_reflex_context_denied(guard: MCPAccessGuard, policy: MCPPolicy) -> None:
    with pytest.raises(MCPAccessDeniedError, match="reflex"):
        guard.open_session(ExecutionContext.REFLEX, policy)


def test_background_context_denied(guard: MCPAccessGuard, policy: MCPPolicy) -> None:
    with pytest.raises(MCPAccessDeniedError, match="background"):
        guard.open_session(ExecutionContext.BACKGROUND, policy)


# ---------------------------------------------------------------------------
# is_allowed helper
# ---------------------------------------------------------------------------


def test_is_allowed_task() -> None:
    assert MCPAccessGuard.is_allowed(ExecutionContext.TASK) is True


def test_is_allowed_research() -> None:
    assert MCPAccessGuard.is_allowed(ExecutionContext.RESEARCH) is True


def test_is_allowed_heartbeat_false() -> None:
    assert MCPAccessGuard.is_allowed(ExecutionContext.HEARTBEAT) is False


def test_is_allowed_reflex_false() -> None:
    assert MCPAccessGuard.is_allowed(ExecutionContext.REFLEX) is False


def test_is_allowed_background_false() -> None:
    assert MCPAccessGuard.is_allowed(ExecutionContext.BACKGROUND) is False
