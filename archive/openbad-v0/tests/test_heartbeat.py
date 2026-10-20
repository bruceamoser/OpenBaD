"""Tests for openbad.tasks.heartbeat (issue #326)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from openbad.tasks.heartbeat import HeartbeatState, HeartbeatStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store() -> HeartbeatStore:
    conn = sqlite3.connect(":memory:")
    store = HeartbeatStore(conn)
    store.initialize()
    return store


# ---------------------------------------------------------------------------
# HeartbeatState
# ---------------------------------------------------------------------------


class TestHeartbeatState:
    def test_defaults_are_zero(self) -> None:
        state = HeartbeatState()
        assert state.last_heartbeat_at == 0.0
        assert state.last_triage_at == 0.0
        assert state.last_context_required_dispatch_at == 0.0
        assert state.last_research_review_at == 0.0
        assert state.last_sleep_cycle_at == 0.0
        assert state.last_maintenance_at == 0.0
        assert state.silent_skip_count == 0


# ---------------------------------------------------------------------------
# HeartbeatStore – initialization and load
# ---------------------------------------------------------------------------


class TestHeartbeatStoreInit:
    def test_initialize_creates_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        store = HeartbeatStore(conn)
        store.initialize()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='heartbeat_state'"
        ).fetchone()
        assert row is not None

    def test_load_inserts_default_row(self) -> None:
        store = make_store()
        state = store.load()
        assert state.last_heartbeat_at == 0.0
        assert state.silent_skip_count == 0

    def test_load_is_idempotent(self) -> None:
        store = make_store()
        store.load()
        store.load()  # Should not raise or duplicate
        state = store.load()
        assert state.silent_skip_count == 0

    def test_state_survives_reconnect(self) -> None:
        """Persisted state should be readable after store object is recreated."""
        conn = sqlite3.connect(":memory:")
        store = HeartbeatStore(conn)
        store.initialize()
        store.load()
        ts = 1_700_000_000.0
        store.record_heartbeat(ts)

        # Same connection, new store object
        store2 = HeartbeatStore(conn)
        state = store2.load()
        assert state.last_heartbeat_at == pytest.approx(ts)


# ---------------------------------------------------------------------------
# Timestamp recording
# ---------------------------------------------------------------------------


class TestTimestampRecording:
    def test_record_heartbeat_updates_field(self) -> None:
        store = make_store()
        store.load()
        ts = 1_710_000_000.0
        store.record_heartbeat(ts)
        assert store.load().last_heartbeat_at == pytest.approx(ts)

    def test_record_heartbeat_defaults_to_now(self) -> None:
        store = make_store()
        store.load()
        before = time.time()
        store.record_heartbeat()
        after = time.time()
        state = store.load()
        assert before <= state.last_heartbeat_at <= after

    def test_record_triage(self) -> None:
        store = make_store()
        store.load()
        ts = 1_720_000_000.0
        store.record_triage(ts)
        assert store.load().last_triage_at == pytest.approx(ts)

    def test_record_context_required_dispatch(self) -> None:
        store = make_store()
        store.load()
        ts = 1_720_100_000.0
        store.record_context_required_dispatch(ts)
        assert store.load().last_context_required_dispatch_at == pytest.approx(ts)

    def test_record_research_review(self) -> None:
        store = make_store()
        store.load()
        ts = 1_720_200_000.0
        store.record_research_review(ts)
        assert store.load().last_research_review_at == pytest.approx(ts)

    def test_record_sleep_cycle(self) -> None:
        store = make_store()
        store.load()
        ts = 1_720_300_000.0
        store.record_sleep_cycle(ts)
        assert store.load().last_sleep_cycle_at == pytest.approx(ts)

    def test_record_maintenance(self) -> None:
        store = make_store()
        store.load()
        ts = 1_720_400_000.0
        store.record_maintenance(ts)
        assert store.load().last_maintenance_at == pytest.approx(ts)


# ---------------------------------------------------------------------------
# Silent skip counter
# ---------------------------------------------------------------------------


class TestSilentSkipCounter:
    def test_increment_starts_at_one(self) -> None:
        store = make_store()
        store.load()
        result = store.increment_silent_skip()
        assert result == 1

    def test_increment_accumulates(self) -> None:
        store = make_store()
        store.load()
        for _ in range(5):
            store.increment_silent_skip()
        assert store.load().silent_skip_count == 5

    def test_reset_clears_count(self) -> None:
        store = make_store()
        store.load()
        store.increment_silent_skip()
        store.increment_silent_skip()
        store.reset_silent_skip()
        assert store.load().silent_skip_count == 0


# ---------------------------------------------------------------------------
# Topics constant validation
# ---------------------------------------------------------------------------


class TestTopicsConstants:
    def test_required_topics_present(self) -> None:
        from openbad.nervous_system import topics

        assert hasattr(topics, "TASK_CONTEXT_REQUIRED")
        assert hasattr(topics, "TASK_ISOLATED")
        assert hasattr(topics, "TASK_EVENTS")
        assert hasattr(topics, "RESEARCH_DEEP_DIVE")
        assert hasattr(topics, "SCHEDULER_WAKE")
        assert hasattr(topics, "SCHEDULER_SLEEP_WINDOW")
        assert hasattr(topics, "SCHEDULER_MAINTENANCE")

    def test_topic_values_match_spec(self) -> None:
        from openbad.nervous_system import topics

        assert topics.TASK_CONTEXT_REQUIRED == "agent/tasks/context_required"
        assert topics.TASK_ISOLATED == "agent/tasks/isolated"
        assert topics.TASK_EVENTS == "agent/tasks/events"
        assert topics.RESEARCH_DEEP_DIVE == "agent/research/deep_dive"
        assert topics.SCHEDULER_WAKE == "agent/scheduler/wake"
        assert topics.SCHEDULER_SLEEP_WINDOW == "agent/scheduler/sleep_window"
        assert topics.SCHEDULER_MAINTENANCE == "agent/scheduler/maintenance"
