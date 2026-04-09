"""Tests for the sleep cycle orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.sleep.orchestrator import (
    MemoryPruner,
    SleepOrchestrator,
    SleepPhase,
    SleepReport,
)
from openbad.memory.sleep.rem import RapidEyeMovement
from openbad.memory.sleep.sws import SlowWaveSleep


def _setup(tmp_path: Path) -> tuple[MemoryController, SleepOrchestrator]:
    mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
    sws = SlowWaveSleep(mc)
    rem = RapidEyeMovement(mc)
    pruner = MemoryPruner(memory_controller=mc)
    orch = SleepOrchestrator(sws=sws, rem=rem, pruner=pruner)
    return mc, orch


# ------------------------------------------------------------------ #
# Idle detection
# ------------------------------------------------------------------ #


class TestIdleDetection:
    def test_not_idle_when_recent(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        assert not orch.is_idle(time.time())

    def test_idle_after_threshold(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        assert orch.is_idle(time.time() - 400)

    def test_custom_threshold(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        sws = SlowWaveSleep(mc)
        rem = RapidEyeMovement(mc)
        pruner = MemoryPruner(memory_controller=mc)
        orch = SleepOrchestrator(
            sws=sws, rem=rem, pruner=pruner, idle_threshold_seconds=10.0,
        )
        assert not orch.is_idle(time.time() - 5)
        assert orch.is_idle(time.time() - 15)


# ------------------------------------------------------------------ #
# Phase transitions
# ------------------------------------------------------------------ #


class TestPhaseTransitions:
    def test_starts_awake(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        assert orch.phase == SleepPhase.AWAKE

    def test_publishes_phase_transitions(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        sws = SlowWaveSleep(mc)
        rem = RapidEyeMovement(mc)
        pruner = MemoryPruner(memory_controller=mc)
        phases: list[str] = []
        orch = SleepOrchestrator(
            sws=sws, rem=rem, pruner=pruner,
            publish_fn=lambda t, p: phases.append(p.decode()),
        )
        orch.run_cycle()
        assert phases == ["sws", "rem", "pruning", "complete", "awake"]

    def test_returns_to_awake(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        orch.run_cycle()
        assert orch.phase == SleepPhase.AWAKE


# ------------------------------------------------------------------ #
# Run cycle
# ------------------------------------------------------------------ #


class TestRunCycle:
    def test_empty_cycle(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        report = orch.run_cycle()
        assert report.constraints_extracted == 0
        assert report.skills_created == 0
        assert report.entries_pruned == 0
        assert report.finished_at >= report.started_at

    def test_cycle_with_failures_and_successes(self, tmp_path: Path) -> None:
        mc, orch = _setup(tmp_path)
        # Add failures
        mc.stm.write(MemoryEntry(
            key="f1", value="error: crash", tier=MemoryTier.STM,
            metadata={"status": "error", "action": "deploy"},
        ))
        # Add successes
        mc.stm.write(MemoryEntry(
            key="s1", value="ok", tier=MemoryTier.STM,
            metadata={"status": "success", "action": "build"},
        ))
        report = orch.run_cycle()
        assert report.constraints_extracted >= 1
        assert report.skills_created >= 1

    def test_cycle_reports_phase_durations(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        report = orch.run_cycle()
        assert "sws" in report.phase_durations
        assert "rem" in report.phase_durations
        assert "pruning" in report.phase_durations


# ------------------------------------------------------------------ #
# SleepReport
# ------------------------------------------------------------------ #


class TestSleepReport:
    def test_to_dict(self) -> None:
        r = SleepReport(
            started_at=100.0,
            finished_at=110.0,
            constraints_extracted=3,
            skills_created=1,
            entries_pruned=5,
        )
        d = r.to_dict()
        assert d["duration_seconds"] == 10.0
        assert d["constraints_extracted"] == 3

    def test_defaults(self) -> None:
        r = SleepReport()
        assert r.started_at == 0.0
        assert r.phase_durations == {}


# ------------------------------------------------------------------ #
# MemoryPruner
# ------------------------------------------------------------------ #


class TestMemoryPruner:
    def test_prunes_decayed_entries(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        # Write old episodic entry
        mc.episodic.write(MemoryEntry(
            key="old", value="data", tier=MemoryTier.EPISODIC,
            created_at=time.time() - 100000 * 3600,
        ))
        # Write fresh episodic entry
        mc.episodic.write(MemoryEntry(
            key="fresh", value="data", tier=MemoryTier.EPISODIC,
        ))
        pruner = MemoryPruner(memory_controller=mc)
        count = pruner.run()
        assert count >= 1
        assert mc.episodic.read("fresh") is not None

    def test_no_pruning_when_all_fresh(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.episodic.write(MemoryEntry(
            key="a", value="data", tier=MemoryTier.EPISODIC,
        ))
        pruner = MemoryPruner(memory_controller=mc)
        assert pruner.run() == 0


# ------------------------------------------------------------------ #
# Background task
# ------------------------------------------------------------------ #


class TestBackground:
    async def test_background_cancellation(self, tmp_path: Path) -> None:
        mc, orch = _setup(tmp_path)

        async def run_bg() -> None:
            await orch.start_background(
                get_last_activity=lambda: time.time() - 600,
                check_interval=0.05,
            )

        task = asyncio.create_task(run_bg())
        await asyncio.sleep(0.15)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_stop_method(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        # stop on a non-started orchestrator should be safe
        orch.stop()
        assert orch.phase == SleepPhase.AWAKE


# ------------------------------------------------------------------ #
# SleepPhase enum
# ------------------------------------------------------------------ #


class TestSleepPhaseEnum:
    def test_values(self) -> None:
        assert SleepPhase.AWAKE.value == "awake"
        assert SleepPhase.SWS.value == "sws"
        assert SleepPhase.REM.value == "rem"
        assert SleepPhase.PRUNING.value == "pruning"
        assert SleepPhase.COMPLETE.value == "complete"
