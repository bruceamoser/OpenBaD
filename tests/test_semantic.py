"""Tests for Semantic Long-Term Memory store."""

from __future__ import annotations

from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.semantic import (
    SemanticMemory,
    cosine_similarity,
    hash_embedding,
)

# ------------------------------------------------------------------ #
# Utility functions
# ------------------------------------------------------------------ #


class TestHashEmbedding:
    def test_returns_list_of_floats(self) -> None:
        vec = hash_embedding("hello")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    def test_deterministic(self) -> None:
        assert hash_embedding("test") == hash_embedding("test")

    def test_different_inputs_different_vectors(self) -> None:
        assert hash_embedding("a") != hash_embedding("b")

    def test_custom_dim(self) -> None:
        vec = hash_embedding("x", dim=8)
        assert len(vec) == 8

    def test_normalized(self) -> None:
        vec = hash_embedding("hello", dim=32)
        import math

        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 0.0, 0.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


# ------------------------------------------------------------------ #
# Basic CRUD
# ------------------------------------------------------------------ #


class TestSemanticBasicOps:
    def test_write_and_read(self) -> None:
        mem = SemanticMemory()
        entry = MemoryEntry(key="s1", value="concept A", tier=MemoryTier.SEMANTIC)
        eid = mem.write(entry)
        assert eid == entry.entry_id
        result = mem.read("s1")
        assert result is not None
        assert result.value == "concept A"
        assert result.access_count == 1

    def test_read_missing(self) -> None:
        mem = SemanticMemory()
        assert mem.read("nope") is None

    def test_delete(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.SEMANTIC))
        assert mem.delete("k")
        assert mem.read("k") is None
        assert mem.get_vector("k") is None
        assert not mem.delete("k")

    def test_size(self) -> None:
        mem = SemanticMemory()
        assert mem.size() == 0
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.SEMANTIC))
        assert mem.size() == 1

    def test_list_keys(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="a", value="1", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="b", value="2", tier=MemoryTier.SEMANTIC))
        assert sorted(mem.list_keys()) == ["a", "b"]

    def test_query_prefix(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="concept/x", value="1", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="concept/y", value="2", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="other/z", value="3", tier=MemoryTier.SEMANTIC))
        results = mem.query("concept/")
        assert len(results) == 2


# ------------------------------------------------------------------ #
# Similarity search
# ------------------------------------------------------------------ #


class TestSemanticSearch:
    def test_search_returns_ranked_results(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="dog", value="dog", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="cat", value="cat", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="fish", value="fish", tier=MemoryTier.SEMANTIC))
        results = mem.search("dog", top_k=3)
        assert len(results) > 0
        # First result should be dog (identical text → max similarity)
        assert results[0][0].key == "dog"
        assert results[0][1] > 0.5

    def test_search_top_k(self) -> None:
        mem = SemanticMemory()
        for i in range(10):
            mem.write(MemoryEntry(key=f"k{i}", value=f"item {i}", tier=MemoryTier.SEMANTIC))
        results = mem.search("item 5", top_k=3)
        assert len(results) == 3

    def test_search_with_threshold(self) -> None:
        mem = SemanticMemory(similarity_threshold=0.99)
        mem.write(MemoryEntry(key="a", value="hello", tier=MemoryTier.SEMANTIC))
        mem.write(MemoryEntry(key="b", value="world", tier=MemoryTier.SEMANTIC))
        # Only exact match should pass high threshold
        results = mem.search("hello", top_k=10)
        assert all(s >= 0.99 for _, s in results)

    def test_get_vector(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="k", value="hello", tier=MemoryTier.SEMANTIC))
        vec = mem.get_vector("k")
        assert vec is not None
        assert isinstance(vec, list)
        assert len(vec) > 0

    def test_get_vector_missing(self) -> None:
        mem = SemanticMemory()
        assert mem.get_vector("nope") is None


# ------------------------------------------------------------------ #
# Custom embedding function
# ------------------------------------------------------------------ #


class TestSemanticCustomEmbed:
    def test_custom_embed_fn(self) -> None:
        calls: list[str] = []

        def my_embed(text: str) -> list[float]:
            calls.append(text)
            return [1.0, 0.0, 0.0]

        mem = SemanticMemory(embed_fn=my_embed)
        mem.write(MemoryEntry(key="k", value="test", tier=MemoryTier.SEMANTIC))
        assert len(calls) == 1
        assert calls[0] == "test"


# ------------------------------------------------------------------ #
# Tier enforcement
# ------------------------------------------------------------------ #


class TestSemanticTier:
    def test_entry_tier_set_to_semantic(self) -> None:
        mem = SemanticMemory()
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        mem.write(entry)
        assert entry.tier is MemoryTier.SEMANTIC


# ------------------------------------------------------------------ #
# JSON persistence
# ------------------------------------------------------------------ #


class TestSemanticPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "semantic.json"
        mem = SemanticMemory(storage_path=path)
        mem.write(MemoryEntry(
            key="k1", value="hello", tier=MemoryTier.SEMANTIC,
            created_at=1000.0,
        ))
        mem.write(MemoryEntry(
            key="k2", value="world", tier=MemoryTier.SEMANTIC,
            created_at=2000.0,
        ))

        mem2 = SemanticMemory(storage_path=path)
        assert mem2.size() == 2
        assert mem2.get_vector("k1") is not None
        r = mem2.read("k1")
        assert r is not None
        assert r.value == "hello"

    def test_no_storage_path(self) -> None:
        mem = SemanticMemory()
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.SEMANTIC))
        mem.save()  # Should not raise

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        mem = SemanticMemory(storage_path=path)
        assert mem.size() == 0

    def test_auto_persist_off(self, tmp_path: Path) -> None:
        path = tmp_path / "semantic.json"
        mem = SemanticMemory(storage_path=path, auto_persist=False)
        mem.write(MemoryEntry(key="k", value="v", tier=MemoryTier.SEMANTIC))
        assert not path.exists()
        mem.save()
        assert path.exists()
