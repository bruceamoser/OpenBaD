"""Tests for Episodic Long-Term Memory store."""

from __future__ import annotations

import time
from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory

# ------------------------------------------------------------------ #
# Basic CRUD
# ------------------------------------------------------------------ #


class TestEpisodicBasicOps:
    def test_write_and_read(self) -> None:
        mem = EpisodicMemory()
        entry = MemoryEntry(key="e1", value="event one", tier=MemoryTier.EPISODIC)
        eid = mem.write(entry)
        assert eid == entry.entry_id
        result = mem.read("e1")
        assert result is not None
        assert result.value == "event one"
        assert result.access_count == 1

    def test_read_missing(self) -> None:
        mem = EpisodicMemory()
        assert mem.read("nope") is None

    def test_delete(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        assert mem.delete("k")
        assert mem.read("k") is None
        assert not mem.delete("k")

    def test_overwrite_key(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="k", value="old", tier=MemoryTier.EPISODIC))
        mem.write(MemoryEntry(key="k", value="new", tier=MemoryTier.EPISODIC))
        result = mem.read("k")
        assert result is not None
        assert result.value == "new"
        assert mem.size() == 1

    def test_size(self) -> None:
        mem = EpisodicMemory()
        assert mem.size() == 0
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        assert mem.size() == 1


# ------------------------------------------------------------------ #
# Query
# ------------------------------------------------------------------ #


class TestEpisodicQuery:
    def test_query_prefix(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="task/1", value="a", tier=MemoryTier.EPISODIC))
        mem.write(MemoryEntry(key="task/2", value="b", tier=MemoryTier.EPISODIC))
        mem.write(MemoryEntry(key="other/1", value="c", tier=MemoryTier.EPISODIC))
        results = mem.query("task/")
        assert len(results) == 2

    def test_list_keys_chronological(self) -> None:
        mem = EpisodicMemory()
        for i in range(5):
            mem.write(MemoryEntry(key=f"k{i}", value=str(i), tier=MemoryTier.EPISODIC))
        keys = mem.list_keys()
        assert keys == ["k0", "k1", "k2", "k3", "k4"]


# ------------------------------------------------------------------ #
# Time range queries
# ------------------------------------------------------------------ #


class TestEpisodicTimeRange:
    def test_query_time_range(self) -> None:
        mem = EpisodicMemory()
        base = 1000.0
        for i in range(5):
            mem.write(MemoryEntry(
                key=f"e{i}", value=str(i), tier=MemoryTier.EPISODIC,
                created_at=base + i * 100,
            ))
        results = mem.query_time_range(1100.0, 1300.0)
        assert len(results) == 3
        assert [r.key for r in results] == ["e1", "e2", "e3"]

    def test_empty_time_range(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(
            key="e", value="v", tier=MemoryTier.EPISODIC, created_at=1000.0,
        ))
        results = mem.query_time_range(2000.0, 3000.0)
        assert results == []


# ------------------------------------------------------------------ #
# Task-ID filtering
# ------------------------------------------------------------------ #


class TestEpisodicTaskFilter:
    def test_query_by_task(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(
            key="a", value="v", tier=MemoryTier.EPISODIC,
            metadata={"task_id": "t1"},
        ))
        mem.write(MemoryEntry(
            key="b", value="v", tier=MemoryTier.EPISODIC,
            metadata={"task_id": "t2"},
        ))
        mem.write(MemoryEntry(
            key="c", value="v", tier=MemoryTier.EPISODIC,
            metadata={"task_id": "t1"},
        ))
        results = mem.query_by_task("t1")
        assert len(results) == 2
        assert [r.key for r in results] == ["a", "c"]

    def test_query_by_task_empty(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="a", value="v", tier=MemoryTier.EPISODIC))
        assert mem.query_by_task("t99") == []


# ------------------------------------------------------------------ #
# Recent entries
# ------------------------------------------------------------------ #


class TestEpisodicRecent:
    def test_recent_default(self) -> None:
        mem = EpisodicMemory()
        for i in range(15):
            mem.write(MemoryEntry(key=f"k{i}", value=str(i), tier=MemoryTier.EPISODIC))
        recent = mem.recent()
        assert len(recent) == 10
        assert recent[0].key == "k5"
        assert recent[-1].key == "k14"

    def test_recent_fewer_than_n(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        recent = mem.recent(5)
        assert len(recent) == 1


# ------------------------------------------------------------------ #
# Tier enforcement
# ------------------------------------------------------------------ #


class TestEpisodicTier:
    def test_entry_tier_set_to_episodic(self) -> None:
        mem = EpisodicMemory()
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        mem.write(entry)
        assert entry.tier is MemoryTier.EPISODIC


# ------------------------------------------------------------------ #
# JSON persistence
# ------------------------------------------------------------------ #


class TestEpisodicPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "episodic.json"
        mem = EpisodicMemory(storage_path=path)
        mem.write(MemoryEntry(
            key="k1", value="hello", tier=MemoryTier.EPISODIC,
            created_at=1000.0, metadata={"task_id": "t1"},
        ))
        mem.write(MemoryEntry(
            key="k2", value="world", tier=MemoryTier.EPISODIC,
            created_at=2000.0,
        ))

        # Reload from disk
        mem2 = EpisodicMemory(storage_path=path)
        assert mem2.size() == 2
        assert mem2.list_keys() == ["k1", "k2"]
        r = mem2.read("k1")
        assert r is not None
        assert r.value == "hello"
        assert r.metadata == {"task_id": "t1"}

    def test_no_storage_path(self) -> None:
        mem = EpisodicMemory()
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        # Should not raise when auto_persist is True but no path
        mem.save()

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        mem = EpisodicMemory(storage_path=path)
        assert mem.size() == 0

    def test_persist_on_delete(self, tmp_path: Path) -> None:
        path = tmp_path / "episodic.json"
        mem = EpisodicMemory(storage_path=path)
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        mem.delete("k")

        mem2 = EpisodicMemory(storage_path=path)
        assert mem2.size() == 0

    def test_auto_persist_off(self, tmp_path: Path) -> None:
        path = tmp_path / "episodic.json"
        mem = EpisodicMemory(storage_path=path, auto_persist=False)
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC))
        assert not path.exists()
        mem.save()
        assert path.exists()

    def test_created_at_auto_set(self) -> None:
        mem = EpisodicMemory()
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC)
        mem.write(entry)
        assert entry.created_at > 0.0
        assert abs(entry.created_at - time.time()) < 2.0
