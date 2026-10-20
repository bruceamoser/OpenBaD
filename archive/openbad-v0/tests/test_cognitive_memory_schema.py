"""Tests for the cognitive memory migration (0007)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from openbad.state.db import initialize_state_db


@pytest.fixture()
def conn(tmp_path):
    """Fresh state DB with all migrations applied."""
    db = initialize_state_db(tmp_path / "state.db")
    yield db
    db.close()


class TestEngramsTable:
    """Verify engrams table schema and constraints."""

    def test_insert_and_read(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "episodic", "greeting", "user greeted", "hello world", now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM engrams WHERE engram_id = ?", ("e1",)
        ).fetchone()
        assert row is not None
        assert row["tier"] == "episodic"
        assert row["key"] == "greeting"
        assert row["confidence"] == 0.5  # default
        assert row["access_count"] == 0  # default
        assert row["state"] == "active"  # default

    def test_all_tiers_accepted(self, conn):
        now = time.time()
        for tier in ("stm", "episodic", "semantic", "procedural"):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"e-{tier}", tier, f"k-{tier}", now, now),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM engrams").fetchone()[0]
        assert count == 4

    def test_invalid_tier_rejected(self, conn):
        now = time.time()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("e-bad", "invalid_tier", "k", now, now),
            )

    def test_invalid_state_rejected(self, conn):
        now = time.time()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at, state)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("e-bad", "stm", "k", now, now, "deleted"),
            )

    def test_state_values_accepted(self, conn):
        now = time.time()
        for i, state in enumerate(("active", "archived", "soft_deleted")):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at, state)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (f"e-{i}", "episodic", f"k-{i}", now, now, state),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM engrams").fetchone()[0]
        assert count == 3

    def test_metadata_defaults_to_empty_json(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "semantic", "k1", now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT metadata FROM engrams WHERE engram_id = ?", ("e1",)
        ).fetchone()
        assert row["metadata"] == "{}"

    def test_ttl_nullable(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at, ttl_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("e1", "stm", "k1", now, now, 300.0),
        )
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e2", "episodic", "k2", now, now),
        )
        conn.commit()
        r1 = conn.execute(
            "SELECT ttl_seconds FROM engrams WHERE engram_id = ?", ("e1",)
        ).fetchone()
        r2 = conn.execute(
            "SELECT ttl_seconds FROM engrams WHERE engram_id = ?", ("e2",)
        ).fetchone()
        assert r1["ttl_seconds"] == 300.0
        assert r2["ttl_seconds"] is None


class TestAssociationsTable:
    """Verify Hebbian association edges."""

    def test_create_association(self, conn):
        now = time.time()
        # Create two engrams
        for eid in ("e1", "e2"):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (eid, "semantic", f"k-{eid}", now, now),
            )
        conn.execute(
            """INSERT INTO engram_associations
               (source_id, target_id, weight, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "e2", 0.3, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM engram_associations WHERE source_id = ? AND target_id = ?",
            ("e1", "e2"),
        ).fetchone()
        assert row is not None
        assert row["weight"] == 0.3
        assert row["co_activation_count"] == 1  # default

    def test_bidirectional_associations(self, conn):
        now = time.time()
        for eid in ("e1", "e2"):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (eid, "semantic", f"k-{eid}", now, now),
            )
        # Both directions
        conn.execute(
            """INSERT INTO engram_associations
               (source_id, target_id, weight, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "e2", 0.3, now, now),
        )
        conn.execute(
            """INSERT INTO engram_associations
               (source_id, target_id, weight, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e2", "e1", 0.3, now, now),
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM engram_associations"
        ).fetchone()[0]
        assert count == 2

    def test_cascade_delete(self, conn):
        now = time.time()
        for eid in ("e1", "e2"):
            conn.execute(
                """INSERT INTO engrams
                   (engram_id, tier, key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (eid, "semantic", f"k-{eid}", now, now),
            )
        conn.execute(
            """INSERT INTO engram_associations
               (source_id, target_id, weight, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "e2", 0.5, now, now),
        )
        conn.commit()
        conn.execute("DELETE FROM engrams WHERE engram_id = ?", ("e1",))
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM engram_associations"
        ).fetchone()[0]
        assert count == 0


class TestActivationLog:
    """Verify co-activation ring buffer."""

    def test_insert_activation(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "episodic", "k1", now, now),
        )
        conn.execute(
            """INSERT INTO activation_log
               (engram_id, activated_at, query_context)
               VALUES (?, ?, ?)""",
            ("e1", now, "test query"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM activation_log WHERE engram_id = ?", ("e1",)
        ).fetchone()
        assert row is not None
        assert row["query_context"] == "test query"

    def test_ring_buffer_query(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "episodic", "k1", now, now),
        )
        # Insert 10 activations
        for i in range(10):
            conn.execute(
                """INSERT INTO activation_log
                   (engram_id, activated_at, query_context)
                   VALUES (?, ?, ?)""",
                ("e1", now + i, f"query-{i}"),
            )
        conn.commit()
        # Get last 5 (ring buffer style)
        rows = conn.execute(
            """SELECT * FROM activation_log
               ORDER BY activated_at DESC LIMIT 5"""
        ).fetchall()
        assert len(rows) == 5
        assert rows[0]["query_context"] == "query-9"

    def test_cascade_delete(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("e1", "episodic", "k1", now, now),
        )
        conn.execute(
            """INSERT INTO activation_log
               (engram_id, activated_at) VALUES (?, ?)""",
            ("e1", now),
        )
        conn.commit()
        conn.execute("DELETE FROM engrams WHERE engram_id = ?", ("e1",))
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM activation_log"
        ).fetchone()[0]
        assert count == 0


class TestFTS5:
    """Verify full-text search index and sync triggers."""

    def test_fts_insert_sync(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "semantic", "k1", "temporal memory", "ACT-R scoring model", now, now),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT engram_id FROM engrams_fts WHERE engrams_fts MATCH ?",
            ("temporal",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["engram_id"] == "e1"

    def test_fts_update_sync(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "semantic", "k1", "old concept", "old content", now, now),
        )
        conn.commit()
        conn.execute(
            """UPDATE engrams SET concept = ?, content = ?, updated_at = ?
               WHERE engram_id = ?""",
            ("Hebbian learning", "weight update formula", now + 1, "e1"),
        )
        conn.commit()
        # Old term should not match
        old = conn.execute(
            "SELECT engram_id FROM engrams_fts WHERE engrams_fts MATCH ?",
            ("old",),
        ).fetchall()
        assert len(old) == 0
        # New term should match
        new = conn.execute(
            "SELECT engram_id FROM engrams_fts WHERE engrams_fts MATCH ?",
            ("Hebbian",),
        ).fetchall()
        assert len(new) == 1

    def test_fts_delete_sync(self, conn):
        now = time.time()
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "semantic", "k1", "temporal memory", "content", now, now),
        )
        conn.commit()
        conn.execute("DELETE FROM engrams WHERE engram_id = ?", ("e1",))
        conn.commit()
        rows = conn.execute(
            "SELECT engram_id FROM engrams_fts WHERE engrams_fts MATCH ?",
            ("temporal",),
        ).fetchall()
        assert len(rows) == 0

    def test_bm25_ranking(self, conn):
        now = time.time()
        # Insert two engrams — one highly relevant, one tangential
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e1", "semantic", "k1", "temporal memory systems",
             "temporal memory uses ACT-R temporal priority scoring for retrieval",
             now, now),
        )
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("e2", "semantic", "k2", "grocery list",
             "buy eggs and milk from the store", now, now),
        )
        conn.commit()
        rows = conn.execute(
            """SELECT engram_id, rank FROM engrams_fts
               WHERE engrams_fts MATCH ?
               ORDER BY rank""",
            ("temporal memory",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["engram_id"] == "e1"


class TestMigrationIdempotent:
    """Verify migration applies cleanly on fresh DB."""

    def test_fresh_db_applies_all_migrations(self, tmp_path):
        conn = initialize_state_db(tmp_path / "fresh.db")
        # Check that engrams table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='engrams'"
        ).fetchall()
        assert len(tables) == 1
        conn.close()

    def test_reopen_db_no_errors(self, tmp_path):
        db_path = tmp_path / "reopen.db"
        conn1 = initialize_state_db(db_path)
        conn1.close()
        # Re-open should not re-apply migrations
        conn2 = initialize_state_db(db_path)
        tables = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='engrams'"
        ).fetchall()
        assert len(tables) == 1
        conn2.close()
