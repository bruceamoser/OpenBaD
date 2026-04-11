from __future__ import annotations

import time
from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.lease import Lease, LeaseError, LeaseStore


@pytest.fixture()
def store(tmp_path: Path) -> LeaseStore:
    conn = initialize_state_db(tmp_path / "state.db")
    return LeaseStore(conn)


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------


def test_acquire_returns_lease(store: LeaseStore) -> None:
    lease = store.acquire("task", "task-1", "worker-A", ttl_seconds=60)

    assert isinstance(lease, Lease)
    assert lease.owner_id == "worker-A"
    assert lease.resource_type == "task"
    assert lease.resource_id == "task-1"
    assert not lease.is_expired


def test_get_active_returns_held_lease(store: LeaseStore) -> None:
    store.acquire("task", "task-1", "worker-A", ttl_seconds=60)

    active = store.get_active("task", "task-1")
    assert active is not None
    assert active.owner_id == "worker-A"


def test_get_active_returns_none_when_no_lease(store: LeaseStore) -> None:
    assert store.get_active("task", "no-task") is None


# ---------------------------------------------------------------------------
# Concurrent acquisition — single winner
# ---------------------------------------------------------------------------


def test_concurrent_acquisition_single_winner(store: LeaseStore) -> None:
    """Two sequential acquire calls on the same live resource: only the first wins."""
    lease_a = store.acquire("task", "shared-task", "worker-A", ttl_seconds=60)
    lease_b = store.acquire("task", "shared-task", "worker-B", ttl_seconds=60)

    assert lease_a is not None
    assert lease_b is None  # B lost the race

    # The active holder is A
    active = store.get_active("task", "shared-task")
    assert active is not None
    assert active.owner_id == "worker-A"


def test_different_resources_can_be_leased_concurrently(store: LeaseStore) -> None:
    lease_a = store.acquire("task", "resource-1", "worker-A", ttl_seconds=60)
    lease_b = store.acquire("task", "resource-2", "worker-B", ttl_seconds=60)

    assert lease_a is not None
    assert lease_b is not None


# ---------------------------------------------------------------------------
# Lease expiry reclaim
# ---------------------------------------------------------------------------


def test_expired_lease_is_reclaimable(store: LeaseStore) -> None:
    """After a lease expires, another owner can acquire the same resource."""
    lease_a = store.acquire("task", "expiring-task", "worker-A", ttl_seconds=0.01)
    assert lease_a is not None

    # Wait for the lease to expire
    time.sleep(0.05)

    lease_b = store.acquire("task", "expiring-task", "worker-B", ttl_seconds=60)
    assert lease_b is not None
    assert lease_b.owner_id == "worker-B"


def test_get_active_returns_none_for_expired_lease(store: LeaseStore) -> None:
    store.acquire("task", "exp-res", "owner", ttl_seconds=0.01)
    time.sleep(0.05)

    assert store.get_active("task", "exp-res") is None


# ---------------------------------------------------------------------------
# Non-owner release denied
# ---------------------------------------------------------------------------


def test_release_by_owner_succeeds(store: LeaseStore) -> None:
    lease = store.acquire("task", "releasable", "owner-A", ttl_seconds=60)
    assert lease is not None

    released = store.release(lease.lease_id, "owner-A")
    assert released is True
    assert store.get_active("task", "releasable") is None


def test_release_by_non_owner_raises_lease_error(store: LeaseStore) -> None:
    lease = store.acquire("task", "guarded", "owner-A", ttl_seconds=60)
    assert lease is not None

    with pytest.raises(LeaseError, match="owner-A"):
        store.release(lease.lease_id, "intruder")


def test_release_nonexistent_lease_returns_false(store: LeaseStore) -> None:
    assert store.release("no-such-lease", "anyone") is False


# ---------------------------------------------------------------------------
# Renewal
# ---------------------------------------------------------------------------


def test_renew_extends_expiry(store: LeaseStore) -> None:
    lease = store.acquire("task", "renewable", "owner-A", ttl_seconds=10)
    assert lease is not None

    renewed = store.renew(lease.lease_id, "owner-A", ttl_seconds=120)

    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at > lease.expires_at


def test_renew_by_non_owner_raises(store: LeaseStore) -> None:
    lease = store.acquire("task", "no-steal", "owner-A", ttl_seconds=60)
    assert lease is not None

    with pytest.raises(LeaseError, match="owner-A"):
        store.renew(lease.lease_id, "thief", ttl_seconds=60)


def test_renew_expired_lease_raises(store: LeaseStore) -> None:
    lease = store.acquire("task", "exp-renew", "owner-A", ttl_seconds=0.01)
    assert lease is not None
    time.sleep(0.05)

    with pytest.raises(LeaseError, match="expired"):
        store.renew(lease.lease_id, "owner-A", ttl_seconds=60)
