"""Tests for the Ebbinghaus forgetting curve and memory pruning."""

from __future__ import annotations

import math
import time

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.forgetting import prune_store, rank_by_retention, retention_score
from openbad.memory.stm import ShortTermMemory


def _entry(
    key: str = "k",
    access_count: int = 0,
    accessed_at: float = 0.0,
    created_at: float | None = None,
) -> MemoryEntry:
    if created_at is None:
        created_at = time.time()
    return MemoryEntry(
        key=key,
        value="v",
        tier=MemoryTier.STM,
        created_at=created_at,
        accessed_at=accessed_at,
        access_count=access_count,
    )


# ------------------------------------------------------------------ #
# retention_score
# ------------------------------------------------------------------ #


class TestRetentionScore:
    def test_fresh_entry_has_full_retention(self) -> None:
        now = time.time()
        e = _entry(created_at=now, access_count=0)
        assert retention_score(e, now=now) > 0.99

    def test_old_entry_decays(self) -> None:
        now = time.time()
        e = _entry(created_at=now - 168 * 3600, access_count=0)
        # After one half_life (default 168h), R = e^(-1) ≈ 0.368
        score = retention_score(e, now=now, half_life_hours=168.0)
        assert 0.30 < score < 0.42

    def test_accesses_reinforce(self) -> None:
        now = time.time()
        e0 = _entry(created_at=now - 168 * 3600, access_count=0)
        e5 = _entry(created_at=now - 168 * 3600, access_count=5)
        s0 = retention_score(e0, now=now)
        s5 = retention_score(e5, now=now)
        assert s5 > s0

    def test_recent_access_resets_decay(self) -> None:
        now = time.time()
        e = _entry(created_at=now - 1000 * 3600, accessed_at=now, access_count=0)
        assert retention_score(e, now=now) > 0.99

    def test_zero_elapsed_is_one(self) -> None:
        now = time.time()
        e = _entry(created_at=now, accessed_at=now, access_count=0)
        assert retention_score(e, now=now) == 1.0

    def test_custom_half_life(self) -> None:
        now = time.time()
        e = _entry(created_at=now - 24 * 3600, access_count=0)
        # 24h old with 24h half_life → R = e^(-1) ≈ 0.368
        score = retention_score(e, now=now, half_life_hours=24.0)
        assert abs(score - math.exp(-1)) < 0.01

    def test_very_old_entry_approaches_zero(self) -> None:
        now = time.time()
        e = _entry(created_at=now - 10000 * 3600, access_count=0)
        score = retention_score(e, now=now)
        assert score < 0.01

    def test_many_accesses_extend_life(self) -> None:
        now = time.time()
        e = _entry(created_at=now - 168 * 3600, access_count=100)
        score = retention_score(e, now=now)
        # ln(1+100) ≈ 4.62, so strength ≈ 168 * 5.62 ≈ 944h
        # R = e^(-168/944) ≈ 0.837
        assert score > 0.7


# ------------------------------------------------------------------ #
# prune_store
# ------------------------------------------------------------------ #


class TestPruneStore:
    def test_prune_removes_decayed(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        stm.write(_entry(key="old", created_at=now - 10000 * 3600))
        stm.write(_entry(key="fresh", created_at=now))
        pruned = prune_store(stm, threshold=0.1, now=now)
        assert "old" in pruned
        assert "fresh" not in pruned
        assert stm.read("old") is None
        assert stm.read("fresh") is not None

    def test_prune_nothing_when_all_fresh(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        stm.write(_entry(key="a", created_at=now))
        stm.write(_entry(key="b", created_at=now))
        pruned = prune_store(stm, threshold=0.1, now=now)
        assert pruned == []
        assert stm.size() == 2

    def test_prune_all_when_threshold_high(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        stm.write(_entry(key="a", created_at=now - 3600))
        stm.write(_entry(key="b", created_at=now - 7200))
        # threshold=1.0 means everything below perfect is pruned
        pruned = prune_store(stm, threshold=1.0, now=now)
        assert len(pruned) == 2
        assert stm.size() == 0

    def test_prune_respects_access_count(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        old_ts = now - 500 * 3600
        stm.write(_entry(key="unvisited", created_at=old_ts, access_count=0))
        stm.write(_entry(key="popular", created_at=old_ts, access_count=50))
        pruned = prune_store(stm, threshold=0.1, now=now)
        assert "unvisited" in pruned
        assert "popular" not in pruned


# ------------------------------------------------------------------ #
# rank_by_retention
# ------------------------------------------------------------------ #


class TestRankByRetention:
    def test_ranking_order(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        stm.write(_entry(key="oldest", created_at=now - 5000 * 3600))
        stm.write(_entry(key="middle", created_at=now - 100 * 3600))
        stm.write(_entry(key="newest", created_at=now))
        ranked = rank_by_retention(stm, now=now)
        keys = [k for k, _ in ranked]
        assert keys == ["oldest", "middle", "newest"]

    def test_ranking_scores_are_ascending(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        stm.write(_entry(key="a", created_at=now - 3600))
        stm.write(_entry(key="b", created_at=now - 7200))
        stm.write(_entry(key="c", created_at=now))
        ranked = rank_by_retention(stm, now=now)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores)

    def test_empty_store_ranks_empty(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        assert rank_by_retention(stm) == []
