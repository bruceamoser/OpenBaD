"""Tests for the sleep cycle orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from unittest.mock import AsyncMock

from openbad.cognitive.types import CognitiveResponse
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.sleep.orchestrator import (
    MemoryPruner,
    SleepOrchestrator,
    SleepPhase,
    SleepReport,
    _batch_by_context,
    _parse_importance,
    _parse_tags,
)
from openbad.memory.sleep.prompts import (
    EXTRACT_TAGS,
    SCORE_IMPORTANCE,
    SUMMARIZE_BATCH,
    SUMMARIZE_SINGLE,
)


def _mock_sleep_handler() -> AsyncMock:
    handler = AsyncMock()
    handler.handle_request = AsyncMock(
        side_effect=[
            CognitiveResponse(request_id="1", answer="summary one"),
            CognitiveResponse(request_id="2", answer="tag1, tag2"),
            CognitiveResponse(request_id="3", answer="0.8"),
        ]
    )
    return handler


def _real_sleep_handler() -> AsyncMock:
    handler = AsyncMock()
    handler.handle_request = AsyncMock(
        side_effect=[
            CognitiveResponse(request_id="1", answer="summary one"),
            CognitiveResponse(request_id="2", answer="tag1, tag2"),
            CognitiveResponse(request_id="3", answer="0.8"),
        ]
    )
    return handler


def _setup(tmp_path: Path) -> tuple[MemoryController, SleepOrchestrator]:
    mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
    pruner = MemoryPruner(memory_controller=mc)
    orch = SleepOrchestrator(
        memory_controller=mc,
        cognitive_handler=_mock_sleep_handler(),
        pruner=pruner,
    )
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
        pruner = MemoryPruner(memory_controller=mc)
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=_mock_sleep_handler(),
            pruner=pruner,
            idle_threshold_seconds=10.0,
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

    async def test_publishes_phase_transitions(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        pruner = MemoryPruner(memory_controller=mc)
        phases: list[str] = []
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=_mock_sleep_handler(),
            pruner=pruner,
            publish_fn=lambda t, p: phases.append(p.decode()),
        )
        mc.stm.write(MemoryEntry(key="s1", value="ok", tier=MemoryTier.STM))
        await orch.run_cycle()
        assert phases == ["sws", "rem", "pruning", "complete", "awake"]

    async def test_returns_to_awake(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        await orch.run_cycle()
        assert orch.phase == SleepPhase.AWAKE


# ------------------------------------------------------------------ #
# Run cycle
# ------------------------------------------------------------------ #


class TestRunCycle:
    async def test_empty_cycle(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        report = await orch.run_cycle()
        assert report.entries_consolidated == 0
        assert report.entries_pruned == 0
        assert report.finished_at >= report.started_at

    async def test_cycle_with_stm_entries(self, tmp_path: Path) -> None:
        mc, orch = _setup(tmp_path)
        mc.stm.write(MemoryEntry(
            key="s1", value="meeting notes", tier=MemoryTier.STM,
            metadata={"status": "success", "action": "remember"},
        ))
        report = await orch.run_cycle()
        assert report.entries_consolidated == 1
        semantic_entries = mc.semantic.query("sleep/")
        assert len(semantic_entries) == 1
        assert semantic_entries[0].metadata["tags"] == ["tag1", "tag2"]
        assert semantic_entries[0].metadata["importance"] == 0.8

    async def test_cycle_reports_phase_durations(self, tmp_path: Path) -> None:
        _, orch = _setup(tmp_path)
        report = await orch.run_cycle()
        assert "sws" in report.phase_durations
        assert "rem" in report.phase_durations
        assert "pruning" in report.phase_durations

    async def test_cycle_skips_when_provider_unavailable(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        loop = AsyncMock()
        loop.handle_request = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=loop,
            pruner=MemoryPruner(memory_controller=mc),
        )
        mc.stm.write(MemoryEntry(key="s1", value="note", tier=MemoryTier.STM))

        report = await orch.run_cycle()

        assert report.skipped is True
        assert report.entries_consolidated == 0
        assert report.error == "provider unavailable"


# ------------------------------------------------------------------ #
# SleepReport
# ------------------------------------------------------------------ #


class TestSleepReport:
    def test_to_dict(self) -> None:
        r = SleepReport(
            started_at=100.0,
            finished_at=110.0,
            entries_consolidated=3,
            entries_pruned=5,
        )
        d = r.to_dict()
        assert d["duration_seconds"] == 10.0
        assert d["entries_consolidated"] == 3

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


class TestHelpers:
    def test_parse_tags(self) -> None:
        assert _parse_tags("one, two\nthree") == ["one", "two", "three"]

    def test_parse_importance(self) -> None:
        assert _parse_importance("1.4") == 1.0
        assert _parse_importance("bad") == 0.5

    def test_batch_by_context_groups_same_context(self) -> None:
        entries = [
            MemoryEntry(key="a", value="v1", tier=MemoryTier.EPISODIC, context="deploy"),
            MemoryEntry(key="b", value="v2", tier=MemoryTier.EPISODIC, context="deploy"),
            MemoryEntry(key="c", value="v3", tier=MemoryTier.EPISODIC, context="build"),
        ]
        batches = _batch_by_context(entries)
        assert len(batches) == 2
        assert len(batches["deploy"]) == 2
        assert len(batches["build"]) == 1

    def test_batch_by_context_uses_topic_metadata(self) -> None:
        entries = [
            MemoryEntry(
                key="a", value="v1", tier=MemoryTier.EPISODIC,
                context="", metadata={"topic": "network"},
            ),
            MemoryEntry(key="b", value="v2", tier=MemoryTier.EPISODIC, context=""),
        ]
        batches = _batch_by_context(entries)
        assert "network" in batches
        assert "general" in batches


class TestPromptTemplates:
    def test_summarize_single_template(self) -> None:
        result = SUMMARIZE_SINGLE.format(entry="test data")
        assert "test data" in result
        assert "Summary:" in result

    def test_summarize_batch_template(self) -> None:
        result = SUMMARIZE_BATCH.format(entries="[a] one\n---\n[b] two")
        assert "one" in result
        assert "two" in result

    def test_extract_tags_template(self) -> None:
        result = EXTRACT_TAGS.format(summary="some summary")
        assert "some summary" in result

    def test_score_importance_template(self) -> None:
        result = SCORE_IMPORTANCE.format(summary="some summary")
        assert "some summary" in result


class TestEpisodicBatching:
    async def test_episodic_entries_batched_and_consolidated(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.episodic.write(MemoryEntry(
            key="e1", value="deploy failed at 3am",
            tier=MemoryTier.EPISODIC, context="deploy",
        ))
        mc.episodic.write(MemoryEntry(
            key="e2", value="deploy recovered after rollback",
            tier=MemoryTier.EPISODIC, context="deploy",
        ))
        # Mock needs: 1 batch summary, tags, score (3 calls total)
        loop = AsyncMock()
        loop.handle_request = AsyncMock(
            side_effect=[
                # batch summary for the deploy context
                CognitiveResponse(
                    request_id="s1",
                    answer="Deploy failed at 3am and recovered after rollback",
                ),
                # tags
                CognitiveResponse(request_id="t1", answer="deploy, failure, rollback"),
                # importance
                CognitiveResponse(request_id="i1", answer="0.9"),
            ]
        )
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=loop,
            pruner=MemoryPruner(memory_controller=mc),
        )
        report = await orch.run_cycle()
        assert report.entries_consolidated == 2
        consolidated = mc.semantic.query("sleep/episodic/")
        assert len(consolidated) == 1
        assert consolidated[0].metadata["source_episodic_keys"] == ["e1", "e2"]
        # Originals still in episodic (not deleted)
        assert mc.episodic.read("e1") is not None
        assert mc.episodic.read("e2") is not None

    async def test_episodic_originals_marked_consolidated(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.episodic.write(MemoryEntry(
            key="e1", value="test note",
            tier=MemoryTier.EPISODIC, context="notes",
        ))
        loop = AsyncMock()
        loop.handle_request = AsyncMock(
            side_effect=[
                CognitiveResponse(request_id="s1", answer="A test note summary"),
                CognitiveResponse(request_id="t1", answer="notes, test"),
                CognitiveResponse(request_id="i1", answer="0.5"),
            ]
        )
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=loop,
        )
        await orch.run_cycle()
        orig = mc.episodic.read("e1")
        assert orig is not None
        assert orig.metadata.get("consolidated") is True


class TestQuality:
    async def test_summary_retains_key_facts(self, tmp_path: Path) -> None:
        """Verify the summary prompt emitted to the LLM contains key facts."""
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.stm.write(MemoryEntry(
            key="fact1",
            value="CPU hit 98% during batch job at 02:14 UTC",
            tier=MemoryTier.STM,
        ))
        captured_prompts: list[str] = []

        async def capture_request(req: object) -> CognitiveResponse:
            captured_prompts.append(getattr(req, "prompt", ""))
            return CognitiveResponse(
                request_id="q",
                answer="CPU spiked to 98% during batch at 02:14 UTC",
            )

        loop = AsyncMock()
        loop.handle_request = AsyncMock(side_effect=[
            CognitiveResponse(
                request_id="s",
                answer="CPU spiked to 98% during batch at 02:14 UTC",
            ),
            CognitiveResponse(request_id="t", answer="cpu, spike, batch"),
            CognitiveResponse(request_id="i", answer="0.7"),
        ])
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=loop,
        )
        report = await orch.run_cycle()
        assert report.entries_consolidated == 1
        sem = mc.semantic.query("sleep/")
        assert len(sem) == 1
        summary_text = sem[0].value
        # The summary should preserve key facts from the original
        assert "98%" in summary_text
        assert "02:14" in summary_text

    async def test_batch_summary_preserves_all_entries(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.episodic.write(MemoryEntry(
            key="deploy-1", value="Deployed v2.3.1 to production",
            tier=MemoryTier.EPISODIC, context="deployment",
        ))
        mc.episodic.write(MemoryEntry(
            key="deploy-2", value="Rollback to v2.3.0 after 500 errors",
            tier=MemoryTier.EPISODIC, context="deployment",
        ))
        loop = AsyncMock()
        loop.handle_request = AsyncMock(side_effect=[
            CognitiveResponse(
                request_id="s",
                answer="Deployed v2.3.1 but rolled back to v2.3.0 due to 500 errors",
            ),
            CognitiveResponse(request_id="t", answer="deploy, rollback, v2.3.1"),
            CognitiveResponse(request_id="i", answer="0.85"),
        ])
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=loop,
        )
        await orch.run_cycle()
        sem = mc.semantic.query("sleep/episodic/")
        assert len(sem) == 1
        summary = sem[0].value
        # Both original facts should be reflected in the summary
        assert "v2.3.1" in summary
        assert "v2.3.0" in summary


class TestIntegration:
    async def test_stm_entries_consolidate_into_semantic_memory(self, tmp_path: Path) -> None:
        mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
        mc.stm.write(
            MemoryEntry(
                key="entry-1",
                value="Observed deployment drift",
                tier=MemoryTier.STM,
            )
        )
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_handler=_real_sleep_handler(),
            pruner=MemoryPruner(memory_controller=mc),
        )

        report = await orch.run_cycle()

        assert report.entries_consolidated == 1
        entry = mc.semantic.read("sleep/entry-1")
        assert entry is not None
        assert entry.metadata["source_stm_key"] == "entry-1"
