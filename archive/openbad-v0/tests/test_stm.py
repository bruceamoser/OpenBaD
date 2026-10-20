"""Tests for Short-Term Memory (STM) rolling buffer."""

from __future__ import annotations

import time

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.stm import ShortTermMemory, _estimate_tokens

# ------------------------------------------------------------------ #
# Token estimation
# ------------------------------------------------------------------ #


class TestTokenEstimation:
    def test_single_word(self) -> None:
        assert _estimate_tokens("hello") >= 1

    def test_longer_text(self) -> None:
        tokens = _estimate_tokens("this is a test sentence with several words")
        assert tokens > 1

    def test_empty_string(self) -> None:
        assert _estimate_tokens("") >= 1


# ------------------------------------------------------------------ #
# Basic CRUD
# ------------------------------------------------------------------ #


class TestSTMBasicOps:
    def test_write_and_read(self) -> None:
        stm = ShortTermMemory()
        entry = MemoryEntry(key="k1", value="hello world", tier=MemoryTier.STM)
        eid = stm.write(entry)
        assert eid == entry.entry_id

        result = stm.read("k1")
        assert result is not None
        assert result.value == "hello world"
        assert result.access_count == 1

    def test_read_missing(self) -> None:
        stm = ShortTermMemory()
        assert stm.read("missing") is None

    def test_delete(self) -> None:
        stm = ShortTermMemory()
        stm.write(MemoryEntry(key="k", value="v", tier=MemoryTier.STM))
        assert stm.delete("k")
        assert stm.read("k") is None
        assert not stm.delete("k")

    def test_overwrite_key(self) -> None:
        stm = ShortTermMemory(max_tokens=1000)
        stm.write(MemoryEntry(key="k", value="old", tier=MemoryTier.STM))
        stm.write(MemoryEntry(key="k", value="new", tier=MemoryTier.STM))
        result = stm.read("k")
        assert result is not None
        assert result.value == "new"
        assert stm.size() == 1

    def test_query(self) -> None:
        stm = ShortTermMemory()
        stm.write(MemoryEntry(key="task/1", value="a", tier=MemoryTier.STM))
        stm.write(MemoryEntry(key="task/2", value="b", tier=MemoryTier.STM))
        stm.write(MemoryEntry(key="other/1", value="c", tier=MemoryTier.STM))
        results = stm.query("task/")
        assert len(results) == 2

    def test_list_keys(self) -> None:
        stm = ShortTermMemory()
        stm.write(MemoryEntry(key="a", value="1", tier=MemoryTier.STM))
        stm.write(MemoryEntry(key="b", value="2", tier=MemoryTier.STM))
        keys = stm.list_keys()
        assert sorted(keys) == ["a", "b"]

    def test_size(self) -> None:
        stm = ShortTermMemory()
        assert stm.size() == 0
        stm.write(MemoryEntry(key="k", value="v", tier=MemoryTier.STM))
        assert stm.size() == 1


# ------------------------------------------------------------------ #
# Token budget & eviction
# ------------------------------------------------------------------ #


class TestSTMEviction:
    def test_evicts_oldest_on_overflow(self) -> None:
        stm = ShortTermMemory(max_tokens=10)
        now = time.time()
        # Write entries with current timestamps so they aren't expired
        stm.write(MemoryEntry(
            key="old", value="word " * 5, tier=MemoryTier.STM,
            created_at=now,
        ))
        stm.write(MemoryEntry(
            key="new", value="word " * 5, tier=MemoryTier.STM,
            created_at=now + 1,
        ))
        # "old" should have been evicted to make room
        assert stm.read("old") is None
        assert stm.read("new") is not None

    def test_usage_stats(self) -> None:
        stm = ShortTermMemory(max_tokens=1000)
        stm.write(MemoryEntry(key="k", value="some text", tier=MemoryTier.STM))
        usage = stm.usage()
        assert usage["tokens_max"] == 1000
        assert usage["tokens_used"] > 0
        assert usage["entry_count"] == 1


# ------------------------------------------------------------------ #
# TTL expiry
# ------------------------------------------------------------------ #


class TestSTMTTL:
    def test_expired_entry_not_readable(self) -> None:
        stm = ShortTermMemory(default_ttl=60.0)
        entry = MemoryEntry(
            key="k", value="v", tier=MemoryTier.STM,
            created_at=time.time() - 120.0,  # 2 minutes ago
            ttl_seconds=60.0,
        )
        stm.write(entry)
        assert stm.read("k") is None

    def test_expired_entries_list(self) -> None:
        stm = ShortTermMemory(default_ttl=60.0)
        stm.write(MemoryEntry(
            key="exp", value="v", tier=MemoryTier.STM,
            created_at=time.time() - 120.0, ttl_seconds=60.0,
        ))
        # Check immediately — before another write triggers eviction
        expired = stm.expired_entries()
        assert len(expired) == 1
        assert expired[0].key == "exp"

    def test_evict_expired(self) -> None:
        stm = ShortTermMemory(default_ttl=60.0)
        # Write the valid entry first so it isn't evicted
        stm.write(MemoryEntry(key="ok", value="v", tier=MemoryTier.STM))
        # Then add an expired entry (evict_expired in write sees only "ok")
        stm.write(MemoryEntry(
            key="exp", value="v", tier=MemoryTier.STM,
            created_at=time.time() - 120.0, ttl_seconds=60.0,
        ))
        count = stm.evict_expired()
        assert count == 1
        assert stm.size() == 1


# ------------------------------------------------------------------ #
# Flush
# ------------------------------------------------------------------ #


class TestSTMFlush:
    def test_flush_clears_all(self) -> None:
        stm = ShortTermMemory()
        stm.write(MemoryEntry(key="a", value="1", tier=MemoryTier.STM))
        stm.write(MemoryEntry(key="b", value="2", tier=MemoryTier.STM))
        flushed = stm.flush()
        assert sorted(flushed) == ["a", "b"]
        assert stm.size() == 0
        assert stm.usage()["tokens_used"] == 0


# ------------------------------------------------------------------ #
# Publish callback
# ------------------------------------------------------------------ #


class TestSTMPublish:
    def test_publish_fn_called(self) -> None:
        published: list[tuple[str, bytes]] = []
        stm = ShortTermMemory(publish_fn=lambda t, p: published.append((t, p)))
        stm.write(MemoryEntry(key="k", value="v", tier=MemoryTier.STM))
        assert len(published) == 1
        assert published[0][0] == "agent/memory/stm/write"
        assert published[0][1] == b"k"

    def test_no_publish_fn(self) -> None:
        stm = ShortTermMemory()
        # Should not raise
        stm.write(MemoryEntry(key="k", value="v", tier=MemoryTier.STM))


# ------------------------------------------------------------------ #
# Tier assignment
# ------------------------------------------------------------------ #


class TestSTMTierEnforcement:
    def test_entry_tier_set_to_stm(self) -> None:
        stm = ShortTermMemory()
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.EPISODIC)
        stm.write(entry)
        assert entry.tier is MemoryTier.STM
