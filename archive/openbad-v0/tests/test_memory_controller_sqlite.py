"""Tests for MemoryController wired to CognitiveMemoryStore backend."""

from __future__ import annotations

import json
import time

import pytest

from openbad.memory.cognitive_store import CognitiveMemoryStore
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController, _migrate_json_to_sqlite
from openbad.memory.episodic import EpisodicMemory
from openbad.state.db import initialize_state_db


@pytest.fixture()
def cfg(tmp_path):
    """MemoryConfig pointed at a temp directory with sqlite backend."""
    return MemoryConfig(
        ltm_backend="sqlite",
        ltm_storage_dir=tmp_path / "memory",
    )


@pytest.fixture()
def ctrl(cfg, tmp_path):
    """MemoryController with sqlite backend."""
    # Ensure state.db parent exists
    (cfg.ltm_storage_dir).mkdir(parents=True, exist_ok=True)
    return MemoryController(config=cfg)


class TestControllerSqliteInit:
    """Controller initialises CognitiveMemoryStore when backend=sqlite."""

    def test_stores_are_cognitive(self, ctrl):
        assert isinstance(ctrl.episodic, CognitiveMemoryStore)
        assert isinstance(ctrl.semantic, CognitiveMemoryStore)
        assert isinstance(ctrl.procedural, CognitiveMemoryStore)

    def test_cognitive_flag_set(self, ctrl):
        assert ctrl._cognitive is True


class TestControllerCRUD:
    """Basic CRUD via the controller's convenience methods."""

    def test_write_and_read_episodic(self, ctrl):
        eid = ctrl.write_episodic("ep1", "an event happened")
        assert eid
        result = ctrl.read("ep1")
        assert result is not None
        assert result.value == "an event happened"

    def test_write_and_read_semantic(self, ctrl):
        ctrl.write_semantic("fact1", "the sky is blue")
        result = ctrl.read("fact1")
        assert result is not None
        assert result.value == "the sky is blue"

    def test_write_and_read_procedural(self, ctrl):
        ctrl.write_procedural("skill1", "how to tie a knot")
        result = ctrl.read("skill1")
        assert result is not None

    def test_write_stm_and_read(self, ctrl):
        ctrl.write_stm("temp", "volatile data")
        result = ctrl.read("temp")
        assert result is not None
        assert result.value == "volatile data"

    def test_search_all(self, ctrl):
        ctrl.write_episodic("mem-a", "event a")
        ctrl.write_semantic("mem-b", "fact b")
        results = ctrl.search_all("mem-")
        assert len(results["episodic"]) == 1
        assert len(results["semantic"]) == 1


class TestPromotion:
    """Tier promotion still works with cognitive backend."""

    def test_promote_to_episodic(self, ctrl):
        ctrl.write_stm("promo-ep", "event to promote")
        eid = ctrl.promote_to_episodic("promo-ep")
        assert eid is not None
        # Should be in episodic now
        assert ctrl.episodic.read("promo-ep") is not None
        # Should be gone from STM
        assert ctrl.stm.read("promo-ep") is None

    def test_promote_to_semantic(self, ctrl):
        ctrl.write_stm("promo-sem", "fact to promote")
        eid = ctrl.promote_to_semantic("promo-sem")
        assert eid is not None
        assert ctrl.semantic.read("promo-sem") is not None
        assert ctrl.stm.read("promo-sem") is None

    def test_promote_nonexistent_returns_none(self, ctrl):
        assert ctrl.promote_to_episodic("nope") is None


class TestRecallCognitive:
    """recall() uses activate() pipeline with cognitive backend."""

    def test_recall_returns_results(self, ctrl):
        ctrl.write_semantic(
            "temporal-mem", "temporal memory scoring model",
            context="temporal memory",
        )
        results = ctrl.recall("temporal memory")
        assert len(results) >= 1
        assert results[0]["key"] == "temporal-mem"

    def test_recall_has_why(self, ctrl):
        ctrl.write_semantic(
            "scored", "temporal memory with ACT-R",
            context="temporal memory",
        )
        results = ctrl.recall("temporal memory")
        assert results
        assert "why" in results[0]
        assert "BM25" in results[0]["why"]

    def test_recall_library_refs_annotated(self, ctrl):
        ctrl.write_semantic(
            "ref-entry", "temporal memory reference",
            context="temporal memory",
            metadata={"library_refs": ["book-123"]},
        )
        results = ctrl.recall("temporal memory")
        assert results
        # Find the entry with library refs
        annotated = [r for r in results if "library_annotations" in r]
        assert len(annotated) >= 1
        assert "book-123" in annotated[0]["library_annotations"][0]

    def test_recall_empty_query(self, ctrl):
        results = ctrl.recall("")
        assert isinstance(results, list)


class TestStats:
    """Stats still work."""

    def test_stats_structure(self, ctrl):
        s = ctrl.stats()
        assert "stm" in s
        assert "episodic" in s
        assert "semantic" in s
        assert "procedural" in s
        assert "timestamp" in s


# ------------------------------------------------------------------
# JSON → SQLite migration
# ------------------------------------------------------------------


class TestJsonMigration:
    """One-time JSON → SQLite data migration."""

    def test_migrates_episodic_json(self, tmp_path):
        # Create a JSON memory file
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        entries = [
            {
                "entry_id": "abc123",
                "key": "event1",
                "value": "something happened",
                "tier": "episodic",
                "created_at": time.time() - 3600,
                "accessed_at": time.time(),
                "access_count": 3,
                "metadata": {},
            },
        ]
        (mem_dir / "episodic.json").write_text(
            json.dumps({"entries": entries}),
        )

        conn = initialize_state_db(tmp_path / "state.db")
        _migrate_json_to_sqlite(mem_dir, conn)

        # Data should be in SQLite
        count = conn.execute(
            "SELECT COUNT(*) FROM engrams WHERE tier = 'episodic'",
        ).fetchone()[0]
        assert count == 1

        # JSON file should be renamed
        assert not (mem_dir / "episodic.json").exists()
        assert (mem_dir / "episodic.json.migrated").exists()
        conn.close()

    def test_skips_if_engrams_exist(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        entries = [
            {
                "entry_id": "old1",
                "key": "old",
                "value": "old data",
                "tier": "semantic",
                "metadata": {},
            },
        ]
        (mem_dir / "semantic.json").write_text(json.dumps({"entries": entries}))

        conn = initialize_state_db(tmp_path / "state.db")
        # Pre-populate an engram
        now = time.time()
        conn.execute(
            """INSERT INTO engrams (engram_id, tier, key, content,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
            ("existing", "semantic", "existing", "data", now, now),
        )
        conn.commit()

        _migrate_json_to_sqlite(mem_dir, conn)

        # JSON file should NOT be renamed (skipped)
        assert (mem_dir / "semantic.json").exists()
        conn.close()

    def test_no_crash_on_missing_json(self, tmp_path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        conn = initialize_state_db(tmp_path / "state.db")
        # Should not crash even without any JSON files
        _migrate_json_to_sqlite(mem_dir, conn)
        conn.close()


class TestLegacyBackend:
    """json backend still works for backwards compatibility."""

    def test_json_backend_creates_old_stores(self, tmp_path):
        cfg = MemoryConfig(
            ltm_backend="json",
            ltm_storage_dir=tmp_path / "memory",
        )
        (tmp_path / "memory").mkdir()
        ctrl = MemoryController(config=cfg)
        assert not ctrl._cognitive
        assert isinstance(ctrl.episodic, EpisodicMemory)
