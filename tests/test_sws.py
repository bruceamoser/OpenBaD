"""Tests for SWS sleep phase — negative constraint extraction."""

from __future__ import annotations

import time
from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.sleep.sws import NegativeConstraint, SlowWaveSleep


def _mc(tmp_path: Path) -> MemoryController:
    return MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))


def _failure_entry(key: str = "fail/1", **meta: object) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value="error: something went wrong",
        tier=MemoryTier.STM,
        metadata={"status": "error", **meta},
    )


def _ok_entry(key: str = "ok/1") -> MemoryEntry:
    return MemoryEntry(key=key, value="all good", tier=MemoryTier.STM)


# ------------------------------------------------------------------ #
# extract_failures
# ------------------------------------------------------------------ #


class TestExtractFailures:
    def test_finds_failures_by_status(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_failure_entry("f1"))
        mc.stm.write(_ok_entry("ok1"))
        sws = SlowWaveSleep(mc)
        failures = sws.extract_failures()
        assert len(failures) == 1
        assert failures[0].key == "f1"

    def test_finds_failures_by_metadata_tag(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        entry = MemoryEntry(
            key="f2", value="data", tier=MemoryTier.STM,
            metadata={"error": "something broke"},
        )
        mc.stm.write(entry)
        sws = SlowWaveSleep(mc)
        assert len(sws.extract_failures()) == 1

    def test_finds_failures_by_value_pattern(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        entry = MemoryEntry(
            key="f3", value="failed: connection timeout", tier=MemoryTier.STM,
        )
        mc.stm.write(entry)
        sws = SlowWaveSleep(mc)
        assert len(sws.extract_failures()) == 1

    def test_filters_by_context(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_failure_entry("f1", context="taskA"))
        mc.stm.write(_failure_entry("f2", context="taskB"))
        sws = SlowWaveSleep(mc)
        failures = sws.extract_failures(context="taskA")
        assert len(failures) == 1
        assert failures[0].key == "f1"

    def test_empty_stm(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        assert sws.extract_failures() == []


# ------------------------------------------------------------------ #
# analyze
# ------------------------------------------------------------------ #


class TestAnalyze:
    def test_heuristic_analysis(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        failures = [_failure_entry("f1", action="network_call", error_type="timeout")]
        constraints = sws.analyze(failures)
        assert len(constraints) == 1
        c = constraints[0]
        assert c.action == "network_call"
        assert c.error_type == "timeout"
        assert "Avoid:" in c.description

    def test_custom_classify_fn(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)

        def custom_fn(entry: MemoryEntry) -> list[NegativeConstraint]:
            return [NegativeConstraint(action="custom", error_type="custom", description="no")]

        sws = SlowWaveSleep(mc, classify_fn=custom_fn)
        constraints = sws.analyze([_failure_entry()])
        assert len(constraints) == 1
        assert constraints[0].action == "custom"

    def test_empty_failures(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        assert sws.analyze([]) == []

    def test_multiple_failures(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        failures = [_failure_entry(f"f{i}") for i in range(3)]
        constraints = sws.analyze(failures)
        assert len(constraints) == 3


# ------------------------------------------------------------------ #
# consolidate
# ------------------------------------------------------------------ #


class TestConsolidate:
    def test_writes_to_episodic(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        constraints = [
            NegativeConstraint(
                action="act", error_type="err", description="avoid X",
            ),
        ]
        ids = sws.consolidate(constraints)
        assert len(ids) == 1
        # Verify in episodic store
        entries = mc.episodic.query("sws/")
        assert len(entries) == 1
        assert entries[0].metadata["context"] == "sws_constraint"

    def test_empty_constraints(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        sws = SlowWaveSleep(mc)
        assert sws.consolidate([]) == []


# ------------------------------------------------------------------ #
# run (full pipeline)
# ------------------------------------------------------------------ #


class TestRun:
    def test_full_pipeline(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_failure_entry("f1", action="api_call", error_type="500"))
        mc.stm.write(_failure_entry("f2", action="db_query", error_type="timeout"))
        mc.stm.write(_ok_entry("ok1"))
        sws = SlowWaveSleep(mc)
        count = sws.run()
        assert count == 2
        # Verify episodic has 2 constraint entries
        entries = mc.episodic.query("sws/")
        assert len(entries) == 2

    def test_run_with_no_failures(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_ok_entry("ok1"))
        sws = SlowWaveSleep(mc)
        assert sws.run() == 0

    def test_run_with_context_filter(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(_failure_entry("f1", context="taskA"))
        mc.stm.write(_failure_entry("f2", context="taskB"))
        sws = SlowWaveSleep(mc)
        count = sws.run(context="taskA")
        assert count == 1


# ------------------------------------------------------------------ #
# NegativeConstraint dataclass
# ------------------------------------------------------------------ #


class TestNegativeConstraint:
    def test_to_dict(self) -> None:
        now = time.time()
        c = NegativeConstraint(
            constraint_id="abc",
            source_entry_id="src1",
            action="act",
            error_type="err",
            description="desc",
            created_at=now,
        )
        d = c.to_dict()
        assert d["constraint_id"] == "abc"
        assert d["action"] == "act"
        assert d["created_at"] == now

    def test_defaults(self) -> None:
        c = NegativeConstraint()
        assert len(c.constraint_id) == 12
        assert c.action == ""
        assert c.created_at > 0
