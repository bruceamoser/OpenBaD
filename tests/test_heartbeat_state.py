from __future__ import annotations

import time
from pathlib import Path

import pytest

from openbad.proprioception.heartbeat_state import HeartbeatState, HeartbeatStateStore
from openbad.state.db import initialize_state_db


@pytest.fixture()
def store(tmp_path: Path) -> HeartbeatStateStore:
    conn = initialize_state_db(tmp_path / "state.db")
    return HeartbeatStateStore(conn)


# ---------------------------------------------------------------------------
# Heartbeat state persistence
# ---------------------------------------------------------------------------


def test_fresh_load_returns_defaults(store: HeartbeatStateStore) -> None:
    state = store.load()

    assert isinstance(state, HeartbeatState)
    assert state.last_heartbeat_at is None
    assert state.silent_skip_count == 0


def test_load_is_idempotent(store: HeartbeatStateStore) -> None:
    first = store.load()
    second = store.load()

    assert first.silent_skip_count == second.silent_skip_count


def test_heartbeat_persists_across_reload(tmp_path: Path) -> None:
    """State survives opening a new connection to the same file."""
    db_path = tmp_path / "state.db"

    conn1 = initialize_state_db(db_path)
    s1 = HeartbeatStateStore(conn1)
    s1.record_heartbeat()
    conn1.close()

    conn2 = initialize_state_db(db_path)
    s2 = HeartbeatStateStore(conn2)
    state = s2.load()
    conn2.close()

    assert state.last_heartbeat_at is not None


# ---------------------------------------------------------------------------
# Silent skip counter
# ---------------------------------------------------------------------------


def test_silent_skip_increments_correctly(store: HeartbeatStateStore) -> None:
    count1 = store.increment_silent_skip()
    count2 = store.increment_silent_skip()
    count3 = store.increment_silent_skip()

    assert count1 == 1
    assert count2 == 2
    assert count3 == 3


def test_record_heartbeat_resets_silent_skip(store: HeartbeatStateStore) -> None:
    store.increment_silent_skip()
    store.increment_silent_skip()

    store.record_heartbeat()

    state = store.load()
    assert state.silent_skip_count == 0


def test_silent_skip_reflected_in_load(store: HeartbeatStateStore) -> None:
    store.increment_silent_skip()
    store.increment_silent_skip()

    state = store.load()
    assert state.silent_skip_count == 2


# ---------------------------------------------------------------------------
# Dispatch timestamps update on publish
# ---------------------------------------------------------------------------


def test_record_triage_updates_timestamp(store: HeartbeatStateStore) -> None:
    before = time.time()
    store.record_triage()
    state = store.load()

    assert state.last_triage_at is not None
    assert state.last_triage_at >= before


def test_record_context_dispatch_updates_timestamp(store: HeartbeatStateStore) -> None:
    before = time.time()
    store.record_context_dispatch()
    state = store.load()

    assert state.last_context_required_dispatch_at is not None
    assert state.last_context_required_dispatch_at >= before


def test_record_research_review_updates_timestamp(store: HeartbeatStateStore) -> None:
    before = time.time()
    store.record_research_review()
    state = store.load()

    assert state.last_research_review_at is not None
    assert state.last_research_review_at >= before


def test_record_sleep_cycle_updates_timestamp(store: HeartbeatStateStore) -> None:
    before = time.time()
    store.record_sleep_cycle()
    state = store.load()

    assert state.last_sleep_cycle_at is not None
    assert state.last_sleep_cycle_at >= before


def test_record_maintenance_updates_timestamp(store: HeartbeatStateStore) -> None:
    before = time.time()
    store.record_maintenance()
    state = store.load()

    assert state.last_maintenance_at is not None
    assert state.last_maintenance_at >= before
