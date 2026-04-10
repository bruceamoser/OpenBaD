"""Tests for the sleep cycle orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.context_manager import (
    CompressedContext,
    CompressionStrategy,
    ContextBudget,
    ContextWindowManager,
)
from openbad.cognitive.event_loop import CognitiveEventLoop, CognitiveResponse
from openbad.cognitive.model_router import FallbackChain, ModelRouter, RouteStep
from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ProviderAdapter,
)
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.sleep.orchestrator import (
    MemoryPruner,
    SleepOrchestrator,
    SleepPhase,
    SleepReport,
    _parse_importance,
    _parse_tags,
)


def _mock_sleep_loop() -> CognitiveEventLoop:
    loop = AsyncMock(spec=CognitiveEventLoop)
    loop.handle_request = AsyncMock(
        side_effect=[
            CognitiveResponse(request_id="1", answer="summary one"),
            CognitiveResponse(request_id="2", answer="tag1, tag2"),
            CognitiveResponse(request_id="3", answer="0.8"),
        ]
    )
    return loop


def _real_sleep_loop() -> CognitiveEventLoop:
    registry = ProviderRegistry()
    adapter = AsyncMock(spec=ProviderAdapter)
    adapter.complete = AsyncMock(
        side_effect=[
            CompletionResult(
                content="summary one",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="tag1, tag2",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="0.8",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=5,
            ),
        ]
    )
    adapter.health_check = AsyncMock(
        return_value=HealthStatus(provider="ollama", available=True, latency_ms=5)
    )
    registry.register("ollama", adapter)
    router = ModelRouter(
        registry=registry,
        system_assignments={
            CognitiveSystem.SLEEP: RouteStep("ollama", "bonsai-8b")
        },
        default_fallback_chain=FallbackChain(steps=(RouteStep("ollama", "bonsai-8b"),)),
    )
    ctx = MagicMock(spec=ContextWindowManager)
    ctx.allocate = MagicMock(
        return_value=ContextBudget(
            max_tokens=8192,
            system_tokens=1000,
            context_tokens=4000,
            response_tokens=2000,
        )
    )
    ctx.compress = MagicMock(
        return_value=CompressedContext(
            text="compressed",
            original_tokens=10,
            compressed_tokens=10,
            strategy=CompressionStrategy.TRUNCATE,
        )
    )
    ctx.track_usage = MagicMock()
    return CognitiveEventLoop(model_router=router, context_manager=ctx, strategies={})


def _setup(tmp_path: Path) -> tuple[MemoryController, SleepOrchestrator]:
    mc = MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path))
    pruner = MemoryPruner(memory_controller=mc)
    orch = SleepOrchestrator(
        memory_controller=mc,
        cognitive_event_loop=_mock_sleep_loop(),
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
            cognitive_event_loop=_mock_sleep_loop(),
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
            cognitive_event_loop=_mock_sleep_loop(),
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
        loop = AsyncMock(spec=CognitiveEventLoop)
        loop.handle_request = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_event_loop=loop,
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
            cognitive_event_loop=_real_sleep_loop(),
            pruner=MemoryPruner(memory_controller=mc),
        )

        report = await orch.run_cycle()

        assert report.entries_consolidated == 1
        entry = mc.semantic.read("sleep/entry-1")
        assert entry is not None
        assert entry.metadata["source_stm_key"] == "entry-1"
