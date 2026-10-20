"""Tests for the Ebbinghaus forgetting curve and memory pruning."""

from __future__ import annotations

import math
import time

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.forgetting import (
    prune_consolidated_episodic,
    prune_store,
    rank_by_retention,
    retention_score,
)
from openbad.memory.stm import ShortTermMemory


def _entry(
    key: str = "k",
    access_count: int = 0,
    accessed_at: float = 0.0,
    created_at: float | None = None,
    metadata: dict | None = None,
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
        metadata=metadata or {},
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
# importance integration
# ------------------------------------------------------------------ #


class TestImportanceIntegration:
    def test_high_importance_extends_retention(self) -> None:
        now = time.time()
        old = now - 168 * 3600
        low = _entry(key="lo", created_at=old, metadata={"importance": 0.0})
        high = _entry(key="hi", created_at=old, metadata={"importance": 1.0})
        s_low = retention_score(low, now=now)
        s_high = retention_score(high, now=now)
        assert s_high > s_low

    def test_no_importance_is_neutral(self) -> None:
        now = time.time()
        old = now - 168 * 3600
        without = _entry(key="no_imp", created_at=old)
        with_half = _entry(
            key="half", created_at=old, metadata={"importance": 0.5},
        )
        s_without = retention_score(without, now=now)
        s_with_half = retention_score(with_half, now=now)
        assert abs(s_without - s_with_half) < 0.01

    def test_zero_importance_decays_faster(self) -> None:
        now = time.time()
        old = now - 168 * 3600
        neutral = _entry(key="n", created_at=old)
        zero = _entry(key="z", created_at=old, metadata={"importance": 0.0})
        assert retention_score(zero, now=now) < retention_score(neutral, now=now)

    def test_importance_clamped(self) -> None:
        """Out-of-range importance values are clamped to [0, 1]."""
        now = time.time()
        old = now - 168 * 3600
        over = _entry(key="over", created_at=old, metadata={"importance": 5.0})
        one = _entry(key="one", created_at=old, metadata={"importance": 1.0})
        assert retention_score(over, now=now) == retention_score(one, now=now)

    def test_prune_respects_importance(self) -> None:
        """Low-importance entries are pruned before high-importance ones."""
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        now = time.time()
        old = now - 500 * 3600
        stm.write(_entry(
            key="unimportant", created_at=old,
            metadata={"importance": 0.0},
        ))
        stm.write(_entry(
            key="important", created_at=old,
            metadata={"importance": 1.0},
        ))
        pruned = prune_store(stm, threshold=0.1, now=now)
        assert "unimportant" in pruned
        assert "important" not in pruned

    def test_unparseable_importance_defaults_safe(self) -> None:
        """Non-numeric importance in metadata defaults to neutral (1.0)."""
        now = time.time()
        e = _entry(
            key="bad", created_at=now - 168 * 3600,
            metadata={"importance": "not-a-number"},
        )
        # Should not crash — ValueError caught by float()
        score = retention_score(e, now=now)
        # Falls back to importance_factor=1.0 (neutral)
        neutral = _entry(key="n", created_at=now - 168 * 3600)
        assert abs(score - retention_score(neutral, now=now)) < 0.01


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


# ------------------------------------------------------------------ #
# prune_consolidated_episodic
# ------------------------------------------------------------------ #


class TestPruneConsolidatedEpisodic:
    def test_consolidated_within_window_survives(self) -> None:
        store = EpisodicMemory()
        now = time.time()
        # 3 days old, consolidated — within default 7-day window
        store.write(_entry(
            key="recent",
            created_at=now - 3 * 86400,
            metadata={"consolidated": True},
        ))
        pruned = prune_consolidated_episodic(store, retention_days=7.0, now=now)
        assert pruned == []
        assert store.size() == 1

    def test_consolidated_past_window_deleted(self) -> None:
        store = EpisodicMemory()
        now = time.time()
        # 10 days old, consolidated — past 7-day window
        store.write(_entry(
            key="old",
            created_at=now - 10 * 86400,
            metadata={"consolidated": True},
        ))
        pruned = prune_consolidated_episodic(store, retention_days=7.0, now=now)
        assert "old" in pruned
        assert store.size() == 0

    def test_unconsolidated_old_entry_untouched(self) -> None:
        store = EpisodicMemory()
        now = time.time()
        # 10 days old but NOT consolidated — must survive
        store.write(_entry(
            key="not_consolidated",
            created_at=now - 10 * 86400,
        ))
        pruned = prune_consolidated_episodic(store, retention_days=7.0, now=now)
        assert pruned == []
        assert store.size() == 1

    def test_custom_retention_period(self) -> None:
        store = EpisodicMemory()
        now = time.time()
        # 2 days old, consolidated — survives with 7-day window
        store.write(_entry(
            key="a",
            created_at=now - 2 * 86400,
            metadata={"consolidated": True},
        ))
        assert prune_consolidated_episodic(store, retention_days=7.0, now=now) == []
        # But NOT with 1-day window
        pruned = prune_consolidated_episodic(store, retention_days=1.0, now=now)
        assert "a" in pruned

    def test_mixed_entries(self) -> None:
        store = EpisodicMemory()
        now = time.time()
        old = now - 10 * 86400
        store.write(_entry(
            key="old_consolidated",
            created_at=old,
            metadata={"consolidated": True},
        ))
        store.write(_entry(
            key="old_raw",
            created_at=old,
        ))
        store.write(_entry(
            key="fresh_consolidated",
            created_at=now,
            metadata={"consolidated": True},
        ))
        pruned = prune_consolidated_episodic(store, retention_days=7.0, now=now)
        assert pruned == ["old_consolidated"]
        assert store.size() == 2

    def test_empty_store_ranks_empty(self) -> None:
        stm = ShortTermMemory(max_tokens=100000, default_ttl=None)
        assert rank_by_retention(stm) == []
