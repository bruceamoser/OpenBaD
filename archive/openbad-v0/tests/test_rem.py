"""Tests for REM sleep phase — skill abstraction."""

from __future__ import annotations

from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.procedural import Skill
from openbad.memory.sleep.rem import RapidEyeMovement


def _mc(tmp_path: Path) -> MemoryController:
    return MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))


def _success(key: str = "s/1", action: str = "fetch", **meta: object) -> MemoryEntry:
    return MemoryEntry(
        key=key, value=f"success doing {action}", tier=MemoryTier.STM,
        metadata={"status": "success", "action": action, **meta},
    )


def _neutral(key: str = "n/1") -> MemoryEntry:
    return MemoryEntry(key=key, value="neutral", tier=MemoryTier.STM)


# ------------------------------------------------------------------ #
# extract_successes
# ------------------------------------------------------------------ #


class TestExtractSuccesses:
    def test_finds_successes(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_success("s1"))
        mc.stm.write(_neutral("n1"))
        rem = RapidEyeMovement(mc)
        successes = rem.extract_successes()
        assert len(successes) == 1
        assert successes[0].key == "s1"

    def test_finds_by_meta_tag(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        entry = MemoryEntry(
            key="s2", value="data", tier=MemoryTier.STM,
            metadata={"success": True},
        )
        mc.stm.write(entry)
        rem = RapidEyeMovement(mc)
        assert len(rem.extract_successes()) == 1

    def test_filters_by_context(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_success("s1", context="taskA"))
        mc.stm.write(_success("s2", context="taskB"))
        rem = RapidEyeMovement(mc)
        results = rem.extract_successes(context="taskA")
        assert len(results) == 1
        assert results[0].key == "s1"

    def test_empty_stm(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        assert rem.extract_successes() == []


# ------------------------------------------------------------------ #
# abstract_to_skill
# ------------------------------------------------------------------ #


class TestAbstractToSkill:
    def test_heuristic_abstraction(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        entries = [_success("s1", action="deploy"), _success("s2", action="deploy")]
        skill = rem.abstract_to_skill(entries)
        assert skill is not None
        assert skill.name == "deploy"
        assert skill.success_count == 2
        assert skill.confidence > 0.5

    def test_custom_abstract_fn(self, tmp_path: Path) -> None:
        def custom_fn(entries: list[MemoryEntry]) -> Skill:
            return Skill(name="custom", description="from LLM")

        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc, abstract_fn=custom_fn)
        skill = rem.abstract_to_skill([_success()])
        assert skill is not None
        assert skill.name == "custom"

    def test_empty_entries(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        assert rem.abstract_to_skill([]) is None

    def test_capabilities_from_metadata(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        entries = [
            MemoryEntry(
                key="s1", value="v", tier=MemoryTier.STM,
                metadata={"status": "success", "action": "a", "capability": "http"},
            ),
        ]
        skill = rem.abstract_to_skill(entries)
        assert skill is not None
        assert "http" in skill.capabilities


# ------------------------------------------------------------------ #
# consolidate
# ------------------------------------------------------------------ #


class TestConsolidate:
    def test_creates_new_skill(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        skill = Skill(name="new_skill", description="d", capabilities=["c"])
        keys = rem.consolidate([skill])
        assert len(keys) == 1
        assert keys[0] == "rem/new_skill"
        assert mc.procedural.get_skill("rem/new_skill") is not None

    def test_updates_existing_skill(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        # Pre-populate a skill
        existing = Skill(name="fetch", description="d", confidence=0.5)
        mc.write_procedural("rem/fetch", existing)
        old_confidence = mc.procedural.get_skill("rem/fetch").confidence

        rem = RapidEyeMovement(mc)
        # Consolidate should update, not duplicate
        keys = rem.consolidate([Skill(name="fetch", description="d2")])
        assert len(keys) == 1
        assert keys[0] == "rem/fetch"
        new_confidence = mc.procedural.get_skill("rem/fetch").confidence
        assert new_confidence != old_confidence  # Updated via record_outcome

    def test_empty_skills(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        rem = RapidEyeMovement(mc)
        assert rem.consolidate([]) == []

    def test_dedup_case_insensitive(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.write_procedural("rem/Deploy", Skill(name="Deploy", description="d"))
        rem = RapidEyeMovement(mc)
        keys = rem.consolidate([Skill(name="deploy", description="d2")])
        assert keys[0] == "rem/Deploy"  # Found existing
        assert mc.procedural.size() == 1  # No duplicate


# ------------------------------------------------------------------ #
# run (full pipeline)
# ------------------------------------------------------------------ #


class TestRun:
    def test_full_pipeline(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_success("s1", action="api_call"))
        mc.stm.write(_success("s2", action="api_call"))
        mc.stm.write(_success("s3", action="db_query"))
        mc.stm.write(_neutral("n1"))
        rem = RapidEyeMovement(mc)
        count = rem.run()
        assert count == 2  # Two distinct actions
        assert mc.procedural.size() == 2

    def test_run_no_successes(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_neutral("n1"))
        rem = RapidEyeMovement(mc)
        assert rem.run() == 0

    def test_run_with_context(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_success("s1", action="build", context="ci"))
        mc.stm.write(_success("s2", action="deploy", context="prod"))
        rem = RapidEyeMovement(mc)
        count = rem.run(context="ci")
        assert count == 1

    def test_run_merges_existing(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.write_procedural("rem/fetch", Skill(name="fetch", description="d"))
        mc.stm.write(_success("s1", action="fetch"))
        rem = RapidEyeMovement(mc)
        count = rem.run()
        assert count == 1
        # Should not create a duplicate
        assert mc.procedural.size() == 1
