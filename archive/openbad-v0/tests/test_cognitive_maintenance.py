"""Tests for cognitive sleep maintenance (Hebbian decay, activation pruning, etc.)."""

from __future__ import annotations

import json
import time

import pytest

from openbad.memory.sleep.cognitive_maintenance import (
    CognitiveMaintenanceReport,
    run_cognitive_maintenance,
)
from openbad.state.db import initialize_state_db


@pytest.fixture()
def conn(tmp_path):
    db = initialize_state_db(tmp_path / "state.db")
    yield db
    db.close()


def _insert_engram(conn, eid, tier="semantic", key=None, **kwargs):
    now = kwargs.pop("now", time.time())
    key = key or f"k-{eid}"
    metadata = json.dumps(kwargs.pop("metadata", {}))
    conn.execute(
        """INSERT INTO engrams
           (engram_id, tier, key, concept, content, created_at, updated_at,
            metadata, state)
           VALUES (?, ?, ?, '', '', ?, ?, ?, 'active')""",
        (eid, tier, key, now, now, metadata),
    )


def _insert_assoc(conn, src, tgt, weight=0.5, updated_at=None):
    now = updated_at or time.time()
    conn.execute(
        """INSERT INTO engram_associations
           (source_id, target_id, weight, co_activation_count,
            created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (src, tgt, weight, now, now),
    )


# ------------------------------------------------------------------
# Hebbian decay
# ------------------------------------------------------------------


class TestHebbianDecay:
    """Association weights decay over time; weak ones get pruned."""

    def test_weights_decay(self, conn):
        _insert_engram(conn, "e1")
        _insert_engram(conn, "e2")
        # Association last updated 1 week ago
        _insert_assoc(conn, "e1", "e2", weight=1.0, updated_at=time.time() - 168 * 3600)
        conn.commit()

        report = run_cognitive_maintenance(conn, half_life_hours=168.0)
        assert report.associations_decayed >= 1

        row = conn.execute(
            "SELECT weight FROM engram_associations WHERE source_id = 'e1'"
        ).fetchone()
        assert row is not None
        assert row["weight"] < 1.0
        # At exactly 1 half-life, weight ≈ 0.5
        assert abs(row["weight"] - 0.5) < 0.05

    def test_very_weak_associations_pruned(self, conn):
        _insert_engram(conn, "e1")
        _insert_engram(conn, "e2")
        # Very old, will decay below threshold
        _insert_assoc(
            conn, "e1", "e2", weight=0.01,
            updated_at=time.time() - 10000 * 3600,
        )
        conn.commit()

        report = run_cognitive_maintenance(conn, decay_threshold=0.01)
        assert report.associations_pruned >= 1
        count = conn.execute(
            "SELECT COUNT(*) FROM engram_associations"
        ).fetchone()[0]
        assert count == 0

    def test_fresh_associations_unchanged(self, conn):
        _insert_engram(conn, "e1")
        _insert_engram(conn, "e2")
        _insert_assoc(conn, "e1", "e2", weight=0.5)  # just created
        conn.commit()

        report = run_cognitive_maintenance(conn)
        # Should not decay (updated_at == now)
        assert report.associations_decayed == 0
        assert report.associations_pruned == 0


# ------------------------------------------------------------------
# Activation log pruning
# ------------------------------------------------------------------


class TestActivationLogPruning:
    """Ring buffer capped at configured limit."""

    def test_prunes_excess(self, conn):
        _insert_engram(conn, "e1")
        now = time.time()
        for i in range(20):
            conn.execute(
                "INSERT INTO activation_log (engram_id, activated_at) VALUES (?, ?)",
                ("e1", now + i),
            )
        conn.commit()

        report = run_cognitive_maintenance(conn, activation_log_limit=10)
        assert report.activation_entries_pruned == 10
        remaining = conn.execute(
            "SELECT COUNT(*) FROM activation_log"
        ).fetchone()[0]
        assert remaining == 10

    def test_no_prune_under_limit(self, conn):
        _insert_engram(conn, "e1")
        conn.execute(
            "INSERT INTO activation_log (engram_id, activated_at) VALUES (?, ?)",
            ("e1", time.time()),
        )
        conn.commit()

        report = run_cognitive_maintenance(conn, activation_log_limit=500)
        assert report.activation_entries_pruned == 0


# ------------------------------------------------------------------
# Source linking
# ------------------------------------------------------------------


class TestSourceLinking:
    """Semantic → episodic associations from source_episodic_keys metadata."""

    def test_creates_links(self, conn):
        _insert_engram(conn, "ep1", tier="episodic", key="event-a")
        _insert_engram(
            conn, "sem1", tier="semantic",
            metadata={"source_episodic_keys": ["event-a"]},
        )
        conn.commit()

        report = run_cognitive_maintenance(conn)
        assert report.source_links_created == 1

        # Bidirectional
        count = conn.execute(
            "SELECT COUNT(*) FROM engram_associations"
        ).fetchone()[0]
        assert count == 2

    def test_no_duplicate_links(self, conn):
        _insert_engram(conn, "ep1", tier="episodic", key="event-a")
        _insert_engram(
            conn, "sem1", tier="semantic",
            metadata={"source_episodic_keys": ["event-a"]},
        )
        conn.commit()

        # Run twice
        run_cognitive_maintenance(conn)
        report = run_cognitive_maintenance(conn)
        assert report.source_links_created == 0

    def test_skips_missing_episodic(self, conn):
        _insert_engram(
            conn, "sem1", tier="semantic",
            metadata={"source_episodic_keys": ["nonexistent"]},
        )
        conn.commit()

        report = run_cognitive_maintenance(conn)
        assert report.source_links_created == 0


# ------------------------------------------------------------------
# Soft-delete purge
# ------------------------------------------------------------------


class TestSoftDeletePurge:
    """Permanently remove old soft-deleted engrams."""

    def test_purges_old_soft_deleted(self, conn):
        now = time.time()
        old = now - 10 * 86400  # 10 days ago
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at, state)
               VALUES (?, ?, ?, ?, ?, 'soft_deleted')""",
            ("old1", "episodic", "k-old", old, old),
        )
        conn.commit()

        report = run_cognitive_maintenance(conn, soft_delete_retention_days=7.0)
        assert report.soft_deleted_purged == 1

    def test_keeps_recent_soft_deleted(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at, state)
               VALUES (?, ?, ?, ?, ?, 'soft_deleted')""",
            ("new1", "episodic", "k-new", now, now),
        )
        conn.commit()

        report = run_cognitive_maintenance(conn, soft_delete_retention_days=7.0)
        assert report.soft_deleted_purged == 0

    def test_active_entries_untouched(self, conn):
        _insert_engram(conn, "active1", now=time.time() - 30 * 86400)
        conn.commit()

        report = run_cognitive_maintenance(conn)
        assert report.soft_deleted_purged == 0
        count = conn.execute(
            "SELECT COUNT(*) FROM engrams WHERE state = 'active'"
        ).fetchone()[0]
        assert count == 1


# ------------------------------------------------------------------
# Expired STM cleanup
# ------------------------------------------------------------------


class TestExpiredSTMCleanup:
    """STM entries with expired TTL are removed."""

    def test_cleans_expired(self, conn):
        old = time.time() - 7200  # 2 hours ago
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at, ttl_seconds, state)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            ("stm1", "stm", "temp", old, old, 3600.0),
        )
        conn.commit()

        report = run_cognitive_maintenance(conn)
        assert report.expired_stm_cleaned == 1

    def test_keeps_non_expired(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at, ttl_seconds, state)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            ("stm1", "stm", "temp", now, now, 9999.0),
        )
        conn.commit()

        report = run_cognitive_maintenance(conn)
        assert report.expired_stm_cleaned == 0


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------


class TestReport:
    """Report dataclass serialisation."""

    def test_to_dict(self):
        r = CognitiveMaintenanceReport(
            associations_decayed=5,
            associations_pruned=2,
            activation_entries_pruned=10,
        )
        d = r.to_dict()
        assert d["associations_decayed"] == 5
        assert d["associations_pruned"] == 2
        assert d["activation_entries_pruned"] == 10

    def test_publish_fn_called(self, conn):
        published = []

        def capture(topic, payload):
            published.append((topic, payload))

        run_cognitive_maintenance(conn, publish_fn=capture)
        assert len(published) == 1
        assert published[0][0] == "agent/sleep/consolidation"
