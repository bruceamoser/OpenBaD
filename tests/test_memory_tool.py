"""Tests for the memory tool adapter (MemoryToolAdapter)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from openbad.memory.base import MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.proprioception.registry import ToolRegistry, ToolRole
from openbad.toolbelt.memory_tool import MemoryToolAdapter, RecallResult


def _make_controller(
    tmp: Path | None = None,
    embed_fn=None,
) -> MemoryController:
    """Create a MemoryController with an isolated temp directory."""
    if tmp is None:
        tmp = Path(tempfile.mkdtemp())
    cfg = MemoryConfig(ltm_storage_dir=tmp)
    return MemoryController(config=cfg, publish_fn=None, embed_fn=embed_fn)


class TestRecall:
    def test_recall_episodic_by_prefix(self) -> None:
        ctrl = _make_controller()
        ctrl.write_episodic("weather-today", "sunny and warm")
        ctrl.write_episodic("weather-tomorrow", "rain expected")
        ctrl.write_episodic("groceries", "eggs,milk")

        adapter = MemoryToolAdapter(ctrl)
        results = adapter.recall("weather")
        assert len(results) >= 1
        assert all(isinstance(r, RecallResult) for r in results)
        keys = [r.key for r in results]
        assert any("weather" in k for k in keys)

    def test_recall_semantic_returns_scored(self) -> None:
        # Use hash_embedding so semantic search works without a real model
        from openbad.memory.semantic import hash_embedding

        ctrl = _make_controller(embed_fn=hash_embedding)
        ctrl.write_semantic("fact-py", "Python is a programming language")
        ctrl.write_semantic("fact-rust", "Rust is a systems language")

        adapter = MemoryToolAdapter(ctrl)
        results = adapter.recall("programming language")
        assert len(results) >= 1
        assert all(isinstance(r, RecallResult) for r in results)
        # Semantic results should have a score
        semantic = [r for r in results if r.tier == "semantic"]
        assert len(semantic) >= 1

    def test_recall_respects_top_k(self) -> None:
        ctrl = _make_controller()
        for i in range(10):
            ctrl.write_episodic(f"item-{i}", f"data {i}")

        adapter = MemoryToolAdapter(ctrl)
        results = adapter.recall("item", top_k=3)
        assert len(results) <= 3

    def test_recall_empty_returns_empty(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        results = adapter.recall("nonexistent")
        assert results == []


class TestStore:
    def test_store_episodic(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        entry_id = adapter.store("hello world", tier="episodic", key="greet")
        assert entry_id is not None
        entry = ctrl.read("greet")
        assert entry is not None
        assert entry.value == "hello world"
        assert entry.tier is MemoryTier.EPISODIC

    def test_store_semantic(self) -> None:
        from openbad.memory.semantic import hash_embedding

        ctrl = _make_controller(embed_fn=hash_embedding)
        adapter = MemoryToolAdapter(ctrl)
        entry_id = adapter.store("fact", tier="semantic", key="k1")
        assert entry_id is not None
        entry = ctrl.read("k1")
        assert entry is not None
        assert entry.tier is MemoryTier.SEMANTIC

    def test_store_stm(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        entry_id = adapter.store("temp data", tier="stm", key="tmp1")
        assert entry_id is not None
        entry = ctrl.read("tmp1")
        assert entry is not None
        assert entry.tier is MemoryTier.STM

    def test_store_auto_key(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        entry_id = adapter.store("auto keyed content", tier="episodic")
        assert entry_id is not None

    def test_store_with_metadata(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        adapter.store(
            "event happened",
            tier="episodic",
            key="ev1",
            metadata={"source": "test"},
        )
        entry = ctrl.read("ev1")
        assert entry is not None
        assert entry.metadata.get("source") == "test"


class TestForget:
    def test_forget_marks_entry(self) -> None:
        ctrl = _make_controller()
        ctrl.write_episodic("rm-me", "old data")
        adapter = MemoryToolAdapter(ctrl)
        assert adapter.forget("rm-me") is True
        entry = ctrl.read("rm-me")
        assert entry is not None
        assert entry.metadata.get("forget_requested") is True

    def test_forget_missing_key_returns_false(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        assert adapter.forget("no-such-key") is False


class TestHealthCheck:
    def test_healthy(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        assert adapter.health_check() is True

    def test_unhealthy_on_error(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        # Break stats to simulate failure
        ctrl.stats = lambda: (_ for _ in ()).throw(RuntimeError("broken"))  # type: ignore[assignment]
        assert adapter.health_check() is False


class TestRegistration:
    def test_registers_under_memory_role(self) -> None:
        ctrl = _make_controller()
        adapter = MemoryToolAdapter(ctrl)
        registry = ToolRegistry(timeout=30.0)
        registry.register(
            "memory",
            role=ToolRole.MEMORY,
            health_check=adapter.health_check,
        )
        cabinet = registry.cabinet
        assert ToolRole.MEMORY in cabinet
        assert any(t.name == "memory" for t in cabinet[ToolRole.MEMORY])
