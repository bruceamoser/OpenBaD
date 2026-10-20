"""Tests for Memory Bridge — library ref annotation and recall()."""

from __future__ import annotations

from pathlib import Path

import pytest

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController, _annotate_library_refs
from openbad.skills.memory_tool import MemoryToolAdapter


@pytest.fixture()
def mc(tmp_path: Path) -> MemoryController:
    return MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))


# ------------------------------------------------------------------
# _annotate_library_refs helper
# ------------------------------------------------------------------


class TestAnnotateLibraryRefs:
    def test_no_refs_no_annotation(self) -> None:
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.SEMANTIC)
        item: dict = {"key": "k", "value": "v", "tier": "semantic", "score": 1.0, "metadata": {}}
        _annotate_library_refs(item, entry)
        assert "library_annotations" not in item

    def test_empty_refs_no_annotation(self) -> None:
        entry = MemoryEntry(
            key="k", value="v", tier=MemoryTier.SEMANTIC, metadata={"library_refs": []}
        )
        item: dict = {
            "key": "k", "value": "v", "tier": "semantic",
            "score": 1.0, "metadata": entry.metadata,
        }
        _annotate_library_refs(item, entry)
        assert "library_annotations" not in item

    def test_single_ref_produces_annotation(self) -> None:
        entry = MemoryEntry(
            key="topic",
            value="summary of topic",
            tier=MemoryTier.SEMANTIC,
            metadata={"library_refs": ["book-123"]},
        )
        item: dict = {
            "key": "topic", "value": "summary of topic",
            "tier": "semantic", "score": 0.9, "metadata": entry.metadata,
        }
        _annotate_library_refs(item, entry)
        assert len(item["library_annotations"]) == 1
        assert "book-123" in item["library_annotations"][0]
        assert "Knowledge Node" in item["library_annotations"][0]

    def test_multiple_refs(self) -> None:
        entry = MemoryEntry(
            key="topic",
            value="summary",
            tier=MemoryTier.SEMANTIC,
            metadata={"library_refs": ["book-a", "book-b"]},
        )
        item: dict = {
            "key": "topic", "value": "summary",
            "tier": "semantic", "score": 0.9, "metadata": entry.metadata,
        }
        _annotate_library_refs(item, entry)
        assert len(item["library_annotations"]) == 2


# ------------------------------------------------------------------
# MemoryController.recall()
# ------------------------------------------------------------------


class TestControllerRecall:
    def test_recall_empty(self, mc: MemoryController) -> None:
        results = mc.recall("anything")
        assert results == []

    def test_recall_semantic_results(self, mc: MemoryController) -> None:
        mc.write_semantic("python-gc", "Python uses reference counting and GC")
        results = mc.recall("python-gc")
        assert len(results) >= 1
        assert results[0]["key"] == "python-gc"
        assert results[0]["tier"] == "semantic"

    def test_recall_with_library_refs(self, mc: MemoryController) -> None:
        mc.write_semantic(
            "api-design",
            "REST API best practices",
            metadata={"library_refs": ["book-uuid-1"]},
        )
        results = mc.recall("api-design")
        assert len(results) >= 1
        ref_result = results[0]
        assert "library_annotations" in ref_result
        assert "book-uuid-1" in ref_result["library_annotations"][0]

    def test_recall_without_refs_has_no_annotations(self, mc: MemoryController) -> None:
        mc.write_semantic("plain-fact", "The sky is blue")
        results = mc.recall("plain-fact")
        assert len(results) >= 1
        assert "library_annotations" not in results[0]

    def test_recall_respects_top_k(self, mc: MemoryController) -> None:
        for i in range(10):
            mc.write_semantic(f"fact-{i}", f"Fact number {i}")
        results = mc.recall("fact", top_k=3)
        assert len(results) <= 3


# ------------------------------------------------------------------
# MemoryToolAdapter backward compatibility
# ------------------------------------------------------------------


class TestMemoryToolAdapterRecall:
    def test_recall_delegates_to_controller(self, mc: MemoryController) -> None:
        mc.write_semantic("topic-x", "Some knowledge")
        adapter = MemoryToolAdapter(mc)
        results = adapter.recall("topic-x")
        assert len(results) >= 1
        assert results[0].key == "topic-x"

    def test_recall_includes_annotations_in_value(self, mc: MemoryController) -> None:
        mc.write_semantic(
            "topic-y",
            "Summary of Y",
            metadata={"library_refs": ["book-abc"]},
        )
        adapter = MemoryToolAdapter(mc)
        results = adapter.recall("topic-y")
        assert len(results) >= 1
        assert "book-abc" in results[0].value
        assert "Knowledge Node" in results[0].value

    def test_recall_without_refs_no_extra_text(self, mc: MemoryController) -> None:
        mc.write_semantic("topic-z", "Plain fact")
        adapter = MemoryToolAdapter(mc)
        results = adapter.recall("topic-z")
        assert len(results) >= 1
        assert results[0].value == "Plain fact"
