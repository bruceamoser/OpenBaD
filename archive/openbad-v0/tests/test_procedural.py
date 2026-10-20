"""Tests for Procedural Long-Term Memory (skill library)."""

from __future__ import annotations

from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.procedural import ProceduralMemory, Skill

# ------------------------------------------------------------------ #
# Skill dataclass
# ------------------------------------------------------------------ #


class TestSkill:
    def test_defaults(self) -> None:
        s = Skill(name="s1", description="test skill")
        assert s.confidence == 0.5
        assert s.capabilities == []
        assert s.code == ""
        assert s.success_count == 0
        assert s.failure_count == 0

    def test_update_confidence_success(self) -> None:
        s = Skill(name="s", description="d")
        s.update_confidence(success=True)
        # Bayesian: (1+1)/(1+2) = 2/3
        assert abs(s.confidence - 2 / 3) < 1e-6
        assert s.success_count == 1

    def test_update_confidence_failure(self) -> None:
        s = Skill(name="s", description="d")
        s.update_confidence(success=False)
        # Bayesian: (0+1)/(1+2) = 1/3
        assert abs(s.confidence - 1 / 3) < 1e-6
        assert s.failure_count == 1

    def test_update_confidence_multiple(self) -> None:
        s = Skill(name="s", description="d")
        for _ in range(3):
            s.update_confidence(success=True)
        s.update_confidence(success=False)
        # (3+1)/(4+2) = 4/6 = 2/3
        assert abs(s.confidence - 2 / 3) < 1e-6

    def test_serialization(self) -> None:
        s = Skill(
            name="s1",
            description="desc",
            capabilities=["parse", "run"],
            code="print('hi')",
            confidence=0.8,
        )
        d = s.to_dict()
        s2 = Skill.from_dict(d)
        assert s2.name == "s1"
        assert s2.capabilities == ["parse", "run"]
        assert s2.code == "print('hi')"
        assert s2.confidence == 0.8


# ------------------------------------------------------------------ #
# Basic CRUD
# ------------------------------------------------------------------ #


class TestProceduralBasicOps:
    def test_write_and_read_skill(self) -> None:
        mem = ProceduralMemory()
        skill = Skill(name="greet", description="Greet the user")
        entry = MemoryEntry(key="greet", value=skill, tier=MemoryTier.PROCEDURAL)
        eid = mem.write(entry)
        assert eid == entry.entry_id
        result = mem.read("greet")
        assert result is not None
        assert result.access_count == 1

    def test_write_dict_value(self) -> None:
        mem = ProceduralMemory()
        entry = MemoryEntry(
            key="k", tier=MemoryTier.PROCEDURAL,
            value={"name": "k", "description": "d", "capabilities": ["x"]},
        )
        mem.write(entry)
        skill = mem.get_skill("k")
        assert skill is not None
        assert skill.capabilities == ["x"]

    def test_write_bare_value(self) -> None:
        mem = ProceduralMemory()
        entry = MemoryEntry(key="k", value="just a string", tier=MemoryTier.PROCEDURAL)
        mem.write(entry)
        skill = mem.get_skill("k")
        assert skill is not None
        assert skill.description == "just a string"

    def test_read_missing(self) -> None:
        mem = ProceduralMemory()
        assert mem.read("nope") is None

    def test_delete(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        assert mem.delete("k")
        assert mem.read("k") is None
        assert mem.get_skill("k") is None
        assert not mem.delete("k")

    def test_size(self) -> None:
        mem = ProceduralMemory()
        assert mem.size() == 0
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        assert mem.size() == 1

    def test_list_keys(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="a", value=Skill(name="a", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        mem.write(MemoryEntry(
            key="b", value=Skill(name="b", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        assert sorted(mem.list_keys()) == ["a", "b"]

    def test_query_prefix(self) -> None:
        mem = ProceduralMemory()
        for key in ["skill/a", "skill/b", "other/c"]:
            mem.write(MemoryEntry(
                key=key, value=Skill(name=key, description="d"),
                tier=MemoryTier.PROCEDURAL,
            ))
        assert len(mem.query("skill/")) == 2


# ------------------------------------------------------------------ #
# Capability search
# ------------------------------------------------------------------ #


class TestProceduralCapabilitySearch:
    def test_search_by_capability(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="s1", tier=MemoryTier.PROCEDURAL,
            value=Skill(name="s1", description="d", capabilities=["parse", "run"]),
        ))
        mem.write(MemoryEntry(
            key="s2", tier=MemoryTier.PROCEDURAL,
            value=Skill(name="s2", description="d", capabilities=["run"]),
        ))
        mem.write(MemoryEntry(
            key="s3", tier=MemoryTier.PROCEDURAL,
            value=Skill(name="s3", description="d", capabilities=["debug"]),
        ))
        results = mem.search_by_capability("run")
        assert len(results) == 2
        assert {r[0] for r in results} == {"s1", "s2"}

    def test_search_empty(self) -> None:
        mem = ProceduralMemory()
        assert mem.search_by_capability("nonexistent") == []

    def test_search_ranked_by_confidence(self) -> None:
        mem = ProceduralMemory()
        low = Skill(name="low", description="d", capabilities=["x"], confidence=0.2)
        high = Skill(name="high", description="d", capabilities=["x"], confidence=0.9)
        mem.write(MemoryEntry(key="low", value=low, tier=MemoryTier.PROCEDURAL))
        mem.write(MemoryEntry(key="high", value=high, tier=MemoryTier.PROCEDURAL))
        results = mem.search_by_capability("x")
        assert results[0][0] == "high"
        assert results[1][0] == "low"


# ------------------------------------------------------------------ #
# Outcome recording
# ------------------------------------------------------------------ #


class TestProceduralOutcome:
    def test_record_success(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        mem.record_outcome("k", success=True)
        skill = mem.get_skill("k")
        assert skill is not None
        assert skill.success_count == 1
        assert skill.confidence > 0.5

    def test_record_failure(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        mem.record_outcome("k", success=False)
        skill = mem.get_skill("k")
        assert skill is not None
        assert skill.failure_count == 1
        assert skill.confidence < 0.5

    def test_record_outcome_missing(self) -> None:
        mem = ProceduralMemory()
        mem.record_outcome("missing", success=True)  # Should not raise


# ------------------------------------------------------------------ #
# Top skills
# ------------------------------------------------------------------ #


class TestProceduralTopSkills:
    def test_top_skills(self) -> None:
        mem = ProceduralMemory()
        for i, conf in enumerate([0.3, 0.9, 0.5, 0.1, 0.7]):
            mem.write(MemoryEntry(
                key=f"s{i}", tier=MemoryTier.PROCEDURAL,
                value=Skill(name=f"s{i}", description="d", confidence=conf),
            ))
        top = mem.top_skills(3)
        assert len(top) == 3
        assert top[0][0] == "s1"  # confidence 0.9


# ------------------------------------------------------------------ #
# Tier enforcement
# ------------------------------------------------------------------ #


class TestProceduralTier:
    def test_entry_tier_set_to_procedural(self) -> None:
        mem = ProceduralMemory()
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        mem.write(entry)
        assert entry.tier is MemoryTier.PROCEDURAL


# ------------------------------------------------------------------ #
# JSON persistence
# ------------------------------------------------------------------ #


class TestProceduralPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "procedural.json"
        mem = ProceduralMemory(storage_path=path)
        skill = Skill(
            name="s1", description="test",
            capabilities=["parse"], code="x=1", confidence=0.7,
        )
        mem.write(MemoryEntry(
            key="s1", value=skill, tier=MemoryTier.PROCEDURAL,
            created_at=1000.0,
        ))

        mem2 = ProceduralMemory(storage_path=path)
        assert mem2.size() == 1
        s = mem2.get_skill("s1")
        assert s is not None
        assert s.capabilities == ["parse"]
        assert s.code == "x=1"
        assert s.confidence == 0.7

    def test_no_storage_path(self) -> None:
        mem = ProceduralMemory()
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        mem.save()  # Should not raise

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        mem = ProceduralMemory(storage_path=path)
        assert mem.size() == 0

    def test_auto_persist_off(self, tmp_path: Path) -> None:
        path = tmp_path / "procedural.json"
        mem = ProceduralMemory(storage_path=path, auto_persist=False)
        mem.write(MemoryEntry(
            key="k", value=Skill(name="k", description="d"),
            tier=MemoryTier.PROCEDURAL,
        ))
        assert not path.exists()
        mem.save()
        assert path.exists()
