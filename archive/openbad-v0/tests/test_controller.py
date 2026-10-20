"""Tests for the Memory Controller."""

from __future__ import annotations

from pathlib import Path

from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.procedural import Skill


def _make_controller(tmp_path: Path) -> MemoryController:
    """Create a MemoryController backed by a temp directory."""
    cfg = MemoryConfig(ltm_storage_dir=tmp_path)
    return MemoryController(config=cfg)


# ------------------------------------------------------------------ #
# Unified writes
# ------------------------------------------------------------------ #


class TestControllerWrite:
    def test_write_stm(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        eid = mc.write_stm("k1", "hello")
        assert eid is not None
        r = mc.stm.read("k1")
        assert r is not None
        assert r.value == "hello"

    def test_write_episodic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        eid = mc.write_episodic("e1", "event data")
        assert eid is not None
        r = mc.episodic.read("e1")
        assert r is not None
        assert r.value == "event data"

    def test_write_semantic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        eid = mc.write_semantic("s1", "concept data")
        assert eid is not None
        r = mc.semantic.read("s1")
        assert r is not None
        assert r.value == "concept data"

    def test_write_procedural_skill(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        skill = Skill(name="greet", description="Greet user", capabilities=["interact"])
        eid = mc.write_procedural("greet", skill)
        assert eid is not None
        s = mc.procedural.get_skill("greet")
        assert s is not None
        assert s.capabilities == ["interact"]

    def test_write_procedural_dict(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_procedural("k", {"name": "k", "description": "d"})
        assert mc.procedural.get_skill("k") is not None


# ------------------------------------------------------------------ #
# Tier promotion
# ------------------------------------------------------------------ #


class TestControllerPromotion:
    def test_promote_to_episodic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("k", "data")
        eid = mc.promote_to_episodic("k")
        assert eid is not None
        # Gone from STM
        assert mc.stm.read("k") is None
        # Present in episodic
        r = mc.episodic.read("k")
        assert r is not None
        assert r.value == "data"
        assert r.metadata.get("promoted_from") == "stm"

    def test_promote_to_semantic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("k", "concept")
        eid = mc.promote_to_semantic("k")
        assert eid is not None
        assert mc.stm.read("k") is None
        r = mc.semantic.read("k")
        assert r is not None
        assert r.value == "concept"

    def test_promote_missing_key(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        assert mc.promote_to_episodic("nope") is None
        assert mc.promote_to_semantic("nope") is None


# ------------------------------------------------------------------ #
# Unified read
# ------------------------------------------------------------------ #


class TestControllerRead:
    def test_read_from_stm(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("k", "stm_val")
        r = mc.read("k")
        assert r is not None
        assert r.value == "stm_val"

    def test_read_from_episodic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_episodic("k", "ep_val")
        r = mc.read("k")
        assert r is not None
        assert r.value == "ep_val"

    def test_read_from_semantic(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_semantic("k", "sem_val")
        r = mc.read("k")
        assert r is not None
        assert r.value == "sem_val"

    def test_read_from_procedural(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_procedural("k", "skill_val")
        r = mc.read("k")
        assert r is not None

    def test_read_missing(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        assert mc.read("nope") is None

    def test_stm_takes_priority(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("k", "from_stm")
        mc.write_episodic("k", "from_ep")
        r = mc.read("k")
        assert r is not None
        assert r.value == "from_stm"


# ------------------------------------------------------------------ #
# Search all
# ------------------------------------------------------------------ #


class TestControllerSearchAll:
    def test_search_across_tiers(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("task/1", "a")
        mc.write_episodic("task/2", "b")
        mc.write_semantic("task/3", "c")
        mc.write_procedural("task/4", "d")
        results = mc.search_all("task/")
        assert len(results["stm"]) == 1
        assert len(results["episodic"]) == 1
        assert len(results["semantic"]) == 1
        assert len(results["procedural"]) == 1

    def test_search_no_results(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        results = mc.search_all("nothing/")
        assert all(len(v) == 0 for v in results.values())


# ------------------------------------------------------------------ #
# Stats
# ------------------------------------------------------------------ #


class TestControllerStats:
    def test_stats_structure(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("k", "v")
        mc.write_episodic("e", "v")
        s = mc.stats()
        assert "stm" in s
        assert "episodic" in s
        assert "semantic" in s
        assert "procedural" in s
        assert "timestamp" in s
        assert s["stm"]["entry_count"] == 1
        assert s["episodic"]["entry_count"] == 1


# ------------------------------------------------------------------ #
# Flush
# ------------------------------------------------------------------ #


class TestControllerFlush:
    def test_flush_stm(self, tmp_path: Path) -> None:
        mc = _make_controller(tmp_path)
        mc.write_stm("a", "1")
        mc.write_stm("b", "2")
        flushed = mc.flush_stm()
        assert sorted(flushed) == ["a", "b"]
        assert mc.stm.size() == 0


# ------------------------------------------------------------------ #
# Publish callback
# ------------------------------------------------------------------ #


class TestControllerPublish:
    def test_publish_fn_wired_to_stm(self, tmp_path: Path) -> None:
        published: list[tuple[str, bytes]] = []
        cfg = MemoryConfig(ltm_storage_dir=tmp_path)
        mc = MemoryController(
            config=cfg,
            publish_fn=lambda t, p: published.append((t, p)),
        )
        mc.write_stm("k", "v")
        assert len(published) == 1
        assert published[0][0] == "agent/memory/stm/write"


# ------------------------------------------------------------------ #
# Default config
# ------------------------------------------------------------------ #


class TestControllerDefaultConfig:
    def test_default_config(self) -> None:
        mc = MemoryController()
        assert mc.stm is not None
        assert mc.episodic is not None
        assert mc.semantic is not None
        assert mc.procedural is not None
