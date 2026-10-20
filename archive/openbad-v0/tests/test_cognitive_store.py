"""Tests for CognitiveMemoryStore (MemoryStore ABC + cognitive retrieval)."""

from __future__ import annotations

import time

import pytest

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.cognitive_store import ActivationResult, CognitiveMemoryStore
from openbad.state.db import initialize_state_db


@pytest.fixture()
def conn(tmp_path):
    """Fresh state DB with all migrations applied."""
    db = initialize_state_db(tmp_path / "state.db")
    yield db
    db.close()


@pytest.fixture()
def store(conn):
    """Semantic-tier cognitive store."""
    return CognitiveMemoryStore(conn, MemoryTier.SEMANTIC)


@pytest.fixture()
def stm_store(conn):
    """STM-tier store for TTL tests."""
    return CognitiveMemoryStore(conn, MemoryTier.STM)


def _make_entry(key: str, value: str = "test", **kwargs) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        tier=kwargs.pop("tier", MemoryTier.SEMANTIC),
        context=kwargs.pop("context", ""),
        **kwargs,
    )


# ------------------------------------------------------------------
# Basic CRUD (MemoryStore ABC)
# ------------------------------------------------------------------


class TestCRUD:
    """MemoryStore ABC contract tests."""

    def test_write_and_read(self, store):
        entry = _make_entry("greeting", "hello world")
        eid = store.write(entry)
        assert eid
        result = store.read("greeting")
        assert result is not None
        assert result.key == "greeting"
        assert result.value == "hello world"

    def test_read_nonexistent(self, store):
        assert store.read("no-such-key") is None

    def test_write_upsert(self, store):
        e1 = _make_entry("k", "v1")
        eid = store.write(e1)
        e2 = MemoryEntry(
            entry_id=eid,
            key="k",
            value="v2",
            tier=MemoryTier.SEMANTIC,
        )
        store.write(e2)
        result = store.read("k")
        assert result is not None
        assert result.value == "v2"

    def test_delete(self, store):
        store.write(_make_entry("del-me", "gone"))
        assert store.delete("del-me")
        assert store.read("del-me") is None

    def test_delete_nonexistent(self, store):
        assert not store.delete("nope")

    def test_list_keys(self, store):
        store.write(_make_entry("a", "1"))
        store.write(_make_entry("b", "2"))
        keys = store.list_keys()
        assert sorted(keys) == ["a", "b"]

    def test_size(self, store):
        assert store.size() == 0
        store.write(_make_entry("x", "y"))
        assert store.size() == 1

    def test_query_by_prefix(self, store):
        store.write(_make_entry("memory-actr", "actr stuff"))
        store.write(_make_entry("memory-hebb", "hebb stuff"))
        store.write(_make_entry("other", "other stuff"))
        results = store.query("memory-")
        assert len(results) == 2
        keys = {r.key for r in results}
        assert keys == {"memory-actr", "memory-hebb"}

    def test_query_empty_prefix(self, store):
        store.write(_make_entry("a", "1"))
        store.write(_make_entry("b", "2"))
        results = store.query("")
        assert len(results) == 2

    def test_read_updates_access_count(self, store):
        store.write(_make_entry("k", "v"))
        store.read("k")
        store.read("k")
        # Query doesn't touch — use it to inspect
        entries = store.query("k")
        assert entries[0].access_count >= 2


class TestTierIsolation:
    """Stores for different tiers don't see each other's data."""

    def test_tiers_isolated(self, conn):
        sem = CognitiveMemoryStore(conn, MemoryTier.SEMANTIC)
        epi = CognitiveMemoryStore(conn, MemoryTier.EPISODIC)
        sem.write(_make_entry("k", "semantic", tier=MemoryTier.SEMANTIC))
        epi.write(_make_entry("k", "episodic", tier=MemoryTier.EPISODIC))
        assert sem.size() == 1
        assert epi.size() == 1
        sem_val = sem.read("k")
        assert sem_val is not None
        assert sem_val.value == "semantic"


class TestSTMTTL:
    """STM entries respect ttl_seconds."""

    def test_expired_entry_not_returned(self, stm_store, conn):
        entry = _make_entry("ephemeral", "gone soon", tier=MemoryTier.STM, ttl_seconds=1.0)
        stm_store.write(entry)

        # Force the created_at into the past
        conn.execute(
            "UPDATE engrams SET created_at = ? WHERE key = ?",
            (time.time() - 100, "ephemeral"),
        )
        conn.commit()

        assert stm_store.read("ephemeral") is None

    def test_non_expired_entry_returned(self, stm_store):
        entry = _make_entry(
            "fresh", "still here", tier=MemoryTier.STM, ttl_seconds=9999.0,
        )
        stm_store.write(entry)
        assert stm_store.read("fresh") is not None


# ------------------------------------------------------------------
# Cognitive retrieval
# ------------------------------------------------------------------


class TestActivate:
    """activate() pipeline tests."""

    def test_activate_returns_results(self, store):
        store.write(_make_entry("tm", "temporal memory ACT-R scoring", context="temporal memory"))
        results = store.activate("temporal memory")
        assert len(results) >= 1
        assert isinstance(results[0], ActivationResult)
        assert results[0].entry.key == "tm"

    def test_activate_returns_why(self, store):
        store.write(_make_entry("tm", "temporal memory model", context="temporal"))
        results = store.activate("temporal memory")
        assert results
        assert "BM25" in results[0].why
        assert "act_r" in results[0].why

    def test_frequent_beats_rare(self, store, conn):
        """A frequently-accessed engram ranks above a rarely-accessed one
        with identical content (AC from issue)."""
        now = time.time()
        store.write(_make_entry(
            "freq", "temporal memory scoring system", context="temporal memory",
        ))
        store.write(_make_entry(
            "rare", "temporal memory scoring system", context="temporal memory",
        ))

        # Simulate high access on 'freq'
        conn.execute(
            """UPDATE engrams SET access_count = 20, last_access_at = ?
               WHERE key = 'freq'""",
            (now,),
        )
        conn.execute(
            """UPDATE engrams SET access_count = 1, last_access_at = ?
               WHERE key = 'rare'""",
            (now - 86400 * 30,),
        )
        conn.commit()

        results = store.activate("temporal memory")
        assert len(results) >= 2
        keys = [r.entry.key for r in results]
        assert keys.index("freq") < keys.index("rare")

    def test_activate_empty_query(self, store):
        store.write(_make_entry("k", "content"))
        results = store.activate("")
        # empty FTS match may return nothing — should not crash
        assert isinstance(results, list)

    def test_activate_no_matches(self, store):
        store.write(_make_entry("k", "bananas"))
        results = store.activate("xyzzy nonexistent topic")
        assert results == []

    def test_activate_respects_limit(self, store):
        for i in range(20):
            store.write(_make_entry(f"mem-{i}", f"temporal memory item {i}", context="temporal"))
        results = store.activate("temporal memory", limit=5)
        assert len(results) <= 5

    def test_activate_expired_stm_excluded(self, conn):
        stm = CognitiveMemoryStore(conn, MemoryTier.STM)
        stm.write(_make_entry(
            "exp", "temporal memory expired", tier=MemoryTier.STM,
            ttl_seconds=1.0, context="temporal memory",
        ))
        # Force old created_at
        conn.execute(
            "UPDATE engrams SET created_at = ? WHERE key = 'exp'",
            (time.time() - 100,),
        )
        conn.commit()
        results = stm.activate("temporal memory")
        assert all(r.entry.key != "exp" for r in results)


class TestHebbianLearning:
    """Hebbian weight evolution through co-activation."""

    def test_co_activation_creates_associations(self, store, conn):
        store.write(_make_entry("a", "temporal memory research", context="temporal memory"))
        store.write(_make_entry("b", "temporal memory model", context="temporal memory"))
        store.activate("temporal memory")

        count = conn.execute(
            "SELECT COUNT(*) FROM engram_associations"
        ).fetchone()[0]
        assert count >= 2  # bidirectional

    def test_repeated_co_activation_strengthens(self, store, conn):
        store.write(_make_entry("x", "temporal memory alpha", context="temporal memory"))
        store.write(_make_entry("y", "temporal memory beta", context="temporal memory"))

        # First activation
        store.activate("temporal memory")
        row1 = conn.execute(
            "SELECT weight FROM engram_associations LIMIT 1"
        ).fetchone()
        w1 = row1["weight"]

        # Second + third
        store.activate("temporal memory")
        store.activate("temporal memory")
        row3 = conn.execute(
            "SELECT weight FROM engram_associations LIMIT 1"
        ).fetchone()
        w3 = row3["weight"]

        assert w3 > w1

    def test_co_activation_count_increments(self, store, conn):
        store.write(_make_entry("p", "temporal memory p", context="temporal memory"))
        store.write(_make_entry("q", "temporal memory q", context="temporal memory"))
        store.activate("temporal memory")
        store.activate("temporal memory")

        row = conn.execute(
            "SELECT co_activation_count FROM engram_associations LIMIT 1"
        ).fetchone()
        assert row["co_activation_count"] >= 2


class TestActivationLog:
    """Activation log ring buffer."""

    def test_activation_logged(self, store, conn):
        store.write(_make_entry("k", "temporal memory activation", context="temporal memory"))
        store.activate("temporal memory")

        count = conn.execute(
            "SELECT COUNT(*) FROM activation_log"
        ).fetchone()[0]
        assert count >= 1

    def test_activation_log_query_context(self, store, conn):
        store.write(_make_entry("k", "temporal memory ctx", context="temporal memory"))
        store.activate("temporal memory")

        row = conn.execute(
            "SELECT query_context FROM activation_log ORDER BY log_id DESC LIMIT 1"
        ).fetchone()
        assert row["query_context"] == "temporal memory"
