"""Tests for idle dispatch of Maintenance Crew in scheduler_worker."""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from openbad.autonomy import scheduler_worker


@pytest.fixture
def _reset_idle_ts():
    """Reset the global idle dispatch timestamp between tests."""
    scheduler_worker._last_idle_dispatch_ts = 0.0
    yield
    scheduler_worker._last_idle_dispatch_ts = 0.0


@pytest.fixture
def mock_conn():
    """In-memory SQLite with session_messages table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE session_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("test-session", "user", "Tell me about quantum computing", time.time()),
    )
    conn.commit()
    return conn


@pytest.fixture
def mock_endocrine():
    """Endocrine runtime mock in IDLE state with low cortisol."""
    runtime = MagicMock()
    runtime.fsm_state = "IDLE"
    runtime.levels = {"cortisol": 0.1, "dopamine": 0.3, "adrenaline": 0.0}
    return runtime


def test_idle_dispatch_runs_maintenance_crew(
    mock_conn, mock_endocrine, _reset_idle_ts,
):
    """When system is idle, maintenance crew is dispatched."""
    post_session = MagicMock()
    adjust = MagicMock()

    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = MagicMock(raw="Exploration findings: discovered X.")

    with (
        patch(
            "openbad.autonomy.scheduler_worker._read_providers_config",
            return_value=("/fake/path", MagicMock()),
        ),
        patch(
            "openbad.autonomy.scheduler_worker._resolve_chat_adapter",
            return_value=(None, None, None, None, None, MagicMock()),
        ),
        patch(
            "openbad.frameworks.crews.maintenance.create_maintenance_crew",
            return_value=mock_crew,
        ) as mock_create,
        patch(
            "openbad.autonomy.scheduler_worker._persist_research_to_library",
        ),
    ):
        scheduler_worker._idle_dispatch_maintenance(
            endocrine_runtime=mock_endocrine,
            conn=mock_conn,
            research_session_id="research-session",
            post_session=post_session,
            adjust=adjust,
        )

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["cortisol"] == 0.1
    assert call_kwargs["dopamine"] == 0.3
    assert call_kwargs["fsm_state"] == "IDLE"

    mock_crew.kickoff.assert_called_once()
    post_session.assert_called_once()
    assert "Exploration findings" in post_session.call_args[0][1]

    adjust.assert_called_once()
    assert adjust.call_args[0][0] == "maintenance-crew"


def test_idle_dispatch_skipped_when_not_idle(
    mock_conn, mock_endocrine, _reset_idle_ts,
):
    """When FSM state is not IDLE, no dispatch."""
    mock_endocrine.fsm_state = "ACTIVE"
    post_session = MagicMock()
    adjust = MagicMock()

    scheduler_worker._idle_dispatch_maintenance(
        endocrine_runtime=mock_endocrine,
        conn=mock_conn,
        research_session_id="research-session",
        post_session=post_session,
        adjust=adjust,
    )

    post_session.assert_not_called()


def test_idle_dispatch_skipped_high_cortisol(
    mock_conn, mock_endocrine, _reset_idle_ts,
):
    """When cortisol exceeds disable threshold, no dispatch."""
    mock_endocrine.levels = {"cortisol": 0.85, "dopamine": 0.3}
    post_session = MagicMock()
    adjust = MagicMock()

    scheduler_worker._idle_dispatch_maintenance(
        endocrine_runtime=mock_endocrine,
        conn=mock_conn,
        research_session_id="research-session",
        post_session=post_session,
        adjust=adjust,
    )

    post_session.assert_not_called()


def test_idle_dispatch_cooldown(
    mock_conn, mock_endocrine, _reset_idle_ts,
):
    """Cooldown prevents too-frequent dispatches."""
    # Pretend we just dispatched
    scheduler_worker._last_idle_dispatch_ts = time.time()
    post_session = MagicMock()
    adjust = MagicMock()

    scheduler_worker._idle_dispatch_maintenance(
        endocrine_runtime=mock_endocrine,
        conn=mock_conn,
        research_session_id="research-session",
        post_session=post_session,
        adjust=adjust,
    )

    post_session.assert_not_called()


def test_idle_dispatch_topic_from_recent_messages(mock_conn):
    """Exploration topic is derived from recent user messages."""
    topic = scheduler_worker._get_exploration_topic(mock_conn)
    assert "quantum computing" in topic


def test_idle_dispatch_topic_fallback():
    """When no messages exist, a generic topic is used."""
    conn = sqlite3.connect(":memory:")
    topic = scheduler_worker._get_exploration_topic(conn)
    assert "explore" in topic.lower() or "system environment" in topic.lower()


def test_idle_dispatch_handles_crew_failure(
    mock_conn, mock_endocrine, _reset_idle_ts,
):
    """Crew failure triggers cortisol adjustment."""
    post_session = MagicMock()
    adjust = MagicMock()

    mock_crew = MagicMock()
    mock_crew.kickoff.side_effect = RuntimeError("LLM timeout")

    with (
        patch(
            "openbad.autonomy.scheduler_worker._read_providers_config",
            return_value=("/fake/path", MagicMock()),
        ),
        patch(
            "openbad.autonomy.scheduler_worker._resolve_chat_adapter",
            return_value=(None, None, None, None, None, MagicMock()),
        ),
        patch(
            "openbad.frameworks.crews.maintenance.create_maintenance_crew",
            return_value=mock_crew,
        ),
    ):
        scheduler_worker._idle_dispatch_maintenance(
            endocrine_runtime=mock_endocrine,
            conn=mock_conn,
            research_session_id="research-session",
            post_session=post_session,
            adjust=adjust,
        )

    post_session.assert_not_called()
    adjust.assert_called_once()
    assert "cortisol" in adjust.call_args[0][2]
