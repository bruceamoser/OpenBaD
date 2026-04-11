from __future__ import annotations

import pytest

from openbad.plugins.mcp_policy import (
    MCPPolicy,
    MCPScope,
    MCPSession,
    MCPSessionManager,
    PolicyViolationError,
    SessionStatus,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager() -> MCPSessionManager:
    return MCPSessionManager()


@pytest.fixture()
def read_policy() -> MCPPolicy:
    return MCPPolicy(max_tools=3, max_calls=10)


# ---------------------------------------------------------------------------
# MCPPolicy
# ---------------------------------------------------------------------------


def test_policy_defaults() -> None:
    policy = MCPPolicy()
    assert policy.max_tools is None
    assert policy.max_calls is None
    assert MCPScope.READ in policy.allowed_scopes


def test_policy_allows_scope() -> None:
    policy = MCPPolicy(allowed_scopes=frozenset({MCPScope.READ, MCPScope.WRITE}))
    assert policy.allows_scope(MCPScope.READ) is True
    assert policy.allows_scope(MCPScope.WRITE) is True
    assert policy.allows_scope(MCPScope.ADMIN) is False


def test_policy_to_dict() -> None:
    policy = MCPPolicy(max_calls=5, max_tools=2)
    d = policy.to_dict()
    assert d["max_calls"] == 5
    assert d["max_tools"] == 2


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_create_session_returns_open_session(
    manager: MCPSessionManager, read_policy: MCPPolicy
) -> None:
    session = manager.create_session(read_policy)
    assert session.status == SessionStatus.OPEN


def test_create_session_with_explicit_id(
    manager: MCPSessionManager, read_policy: MCPPolicy
) -> None:
    session = manager.create_session(read_policy, session_id="explicit-1")
    assert session.session_id == "explicit-1"


def test_duplicate_session_id_raises(manager: MCPSessionManager, read_policy: MCPPolicy) -> None:
    manager.create_session(read_policy, session_id="dup")
    with pytest.raises(ValueError, match="dup"):
        manager.create_session(read_policy, session_id="dup")


def test_get_session(manager: MCPSessionManager, read_policy: MCPPolicy) -> None:
    session = manager.create_session(read_policy)
    retrieved = manager.get_session(session.session_id)
    assert retrieved is session


def test_get_missing_session_returns_none(manager: MCPSessionManager) -> None:
    assert manager.get_session("missing") is None


def test_close_session(manager: MCPSessionManager, read_policy: MCPPolicy) -> None:
    session = manager.create_session(read_policy)
    manager.close_session(session.session_id)
    assert session.status == SessionStatus.CLOSED


def test_close_unknown_session_raises(manager: MCPSessionManager) -> None:
    with pytest.raises(KeyError):
        manager.close_session("unknown")


def test_list_open_sessions(manager: MCPSessionManager, read_policy: MCPPolicy) -> None:
    s1 = manager.create_session(read_policy)
    s2 = manager.create_session(read_policy)
    manager.close_session(s1.session_id)

    open_sessions = manager.list_open()
    ids = {s.session_id for s in open_sessions}
    assert s2.session_id in ids
    assert s1.session_id not in ids


# ---------------------------------------------------------------------------
# Session limit enforcement
# ---------------------------------------------------------------------------


def test_check_limit_allows_within_call_budget() -> None:
    policy = MCPPolicy(max_calls=3)
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    # 2 calls recorded — 1 remaining
    session.record_call("tool_a")
    session.record_call("tool_b")
    session.check_limit("tool_c")  # should not raise


def test_check_limit_raises_when_call_limit_hit() -> None:
    policy = MCPPolicy(max_calls=2)
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    session.record_call("tool_a")
    session.record_call("tool_b")

    with pytest.raises(PolicyViolationError, match="call limit"):
        session.check_limit("tool_c")


def test_check_limit_raises_when_tool_limit_hit() -> None:
    policy = MCPPolicy(max_tools=2)
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    session.record_call("tool_a")
    session.record_call("tool_b")

    with pytest.raises(PolicyViolationError, match="tool limit"):
        session.check_limit("tool_c")


def test_check_limit_allows_repeat_call_to_existing_tool() -> None:
    policy = MCPPolicy(max_tools=1)
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    session.record_call("tool_a")
    session.check_limit("tool_a")  # same tool — no new tool slot used


def test_check_limit_raises_on_closed_session() -> None:
    policy = MCPPolicy()
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
        status=SessionStatus.CLOSED,
    )
    with pytest.raises(PolicyViolationError, match="closed"):
        session.check_limit("tool_a")


def test_record_call_increments_count() -> None:
    policy = MCPPolicy()
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    session.record_call("tool_a")
    session.record_call("tool_a")
    assert session.tools_called["tool_a"] == 2
    assert session.total_calls == 2


def test_session_to_dict_includes_counts() -> None:
    policy = MCPPolicy(max_calls=10)
    session = MCPSession(
        session_id="s1",
        policy=policy,
        created_at=__import__("datetime").datetime.now(),
    )
    session.record_call("tool_x")
    d = session.to_dict()
    assert d["total_calls"] == 1
    assert d["session_id"] == "s1"
