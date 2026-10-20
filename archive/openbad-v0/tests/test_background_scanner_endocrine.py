"""Integration tests for BackgroundScanner endocrine-driven behavior."""

from __future__ import annotations

import asyncio

import pytest

from openbad.active_inference.background_scanner import (
    _ADRENALINE_SUSPEND_THRESHOLD,
    _CORTISOL_PAUSE_THRESHOLD,
    _DOPAMINE_BOOST_FACTOR,
    _DOPAMINE_BOOST_THRESHOLD,
    BackgroundScanner,
)
from openbad.active_inference.budget import ExplorationBudget
from openbad.active_inference.config import ActiveInferenceConfig
from openbad.active_inference.engine import ExplorationEngine
from openbad.active_inference.exploration_actions import ExplorationActionGenerator
from openbad.active_inference.insight_queue import InsightQueue
from openbad.active_inference.world_model import WorldModel
from openbad.endocrine.controller import EndocrineController
from openbad.plugins.observations.external_signals import ExternalSignalPlugin


@pytest.fixture()
def endocrine() -> EndocrineController:
    return EndocrineController()


@pytest.fixture()
def scanner(endocrine: EndocrineController) -> BackgroundScanner:
    config = ActiveInferenceConfig()
    world_model = WorldModel()
    budget = ExplorationBudget()
    engine = ExplorationEngine(config=config, world_model=world_model, budget=budget)
    insight_queue = InsightQueue()
    action_gen = ExplorationActionGenerator(insight_queue=insight_queue)
    return BackgroundScanner(
        exploration_engine=engine,
        action_generator=action_gen,
        endocrine=endocrine,
    )


class TestShouldScan:
    """Tests for _should_scan() endocrine gating."""

    def test_normal_state_allows_scanning(self, scanner: BackgroundScanner) -> None:
        scanner.set_state("IDLE")
        assert scanner._should_scan() is True

    def test_sleep_state_blocks_scanning(self, scanner: BackgroundScanner) -> None:
        scanner.set_state("SLEEP")
        assert scanner._should_scan() is False

    def test_emergency_state_blocks_scanning(self, scanner: BackgroundScanner) -> None:
        scanner.set_state("EMERGENCY")
        assert scanner._should_scan() is False

    def test_high_cortisol_pauses_scanning(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        endocrine.trigger("cortisol", _CORTISOL_PAUSE_THRESHOLD)
        assert scanner._should_scan() is False

    def test_below_cortisol_threshold_allows(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        endocrine.trigger("cortisol", _CORTISOL_PAUSE_THRESHOLD - 0.1)
        assert scanner._should_scan() is True

    def test_adrenaline_spike_suspends(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        endocrine.trigger("adrenaline", _ADRENALINE_SUSPEND_THRESHOLD)
        assert scanner._should_scan() is False

    def test_below_adrenaline_threshold_allows(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        endocrine.trigger("adrenaline", _ADRENALINE_SUSPEND_THRESHOLD - 0.1)
        assert scanner._should_scan() is True

    def test_no_endocrine_always_scans(self) -> None:
        """Scanner without endocrine controller always scans (graceful fallback)."""
        config = ActiveInferenceConfig()
        world_model = WorldModel()
        budget = ExplorationBudget()
        engine = ExplorationEngine(config=config, world_model=world_model, budget=budget)
        action_gen = ExplorationActionGenerator(insight_queue=InsightQueue())
        scanner = BackgroundScanner(
            exploration_engine=engine,
            action_generator=action_gen,
            endocrine=None,
        )
        scanner.set_state("IDLE")
        assert scanner._should_scan() is True


class TestComputeInterval:
    """Tests for _compute_interval() endocrine modulation."""

    def test_idle_normal_dopamine_returns_base(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        # Normal dopamine (above threshold) → no boost
        endocrine.trigger("dopamine", _DOPAMINE_BOOST_THRESHOLD + 0.1)
        scanner.set_state("IDLE")
        assert scanner._compute_interval(60) == 60.0

    def test_low_dopamine_boosts_polling(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        # Dopamine below threshold → boredom → faster polling
        scanner.set_state("IDLE")
        interval = scanner._compute_interval(60)
        assert interval == 60.0 * _DOPAMINE_BOOST_FACTOR

    def test_active_state_slows_polling(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        endocrine.trigger("dopamine", 0.5)  # Above threshold
        scanner.set_state("ACTIVE")
        assert scanner._compute_interval(60) == 180.0

    def test_active_with_low_dopamine(
        self, scanner: BackgroundScanner, endocrine: EndocrineController
    ) -> None:
        # ACTIVE × dopamine_boost both apply
        scanner.set_state("ACTIVE")
        interval = scanner._compute_interval(60)
        assert interval == 60.0 * 3.0 * _DOPAMINE_BOOST_FACTOR


class TestPluginRegistration:
    """Tests for plugin lifecycle with scanner."""

    @pytest.mark.asyncio()
    async def test_register_plugin_starts_poll_loop(
        self, scanner: BackgroundScanner
    ) -> None:
        await scanner.start()
        plugin = ExternalSignalPlugin()
        await scanner.register_plugin(plugin)

        assert "external_signals" in scanner._tasks
        assert not scanner._tasks["external_signals"].done()

        await scanner.stop()
        assert len(scanner._tasks) == 0

    @pytest.mark.asyncio()
    async def test_stop_cancels_all_tasks(self, scanner: BackgroundScanner) -> None:
        await scanner.start()
        plugin = ExternalSignalPlugin()
        await scanner.register_plugin(plugin)

        await scanner.stop()
        assert scanner._running is False
        assert len(scanner._tasks) == 0


class TestEndocrinePollingIntegration:
    """Integration test: verify endocrine state changes affect poll behavior."""

    @pytest.mark.asyncio()
    async def test_cortisol_spike_stops_polling(
        self, endocrine: EndocrineController
    ) -> None:
        """When cortisol spikes mid-scan, the next poll cycle is skipped."""
        config = ActiveInferenceConfig()
        world_model = WorldModel()
        budget = ExplorationBudget(daily_limit=100, cooldown_seconds=0)
        engine = ExplorationEngine(config=config, world_model=world_model, budget=budget)
        insight_queue = InsightQueue()
        action_gen = ExplorationActionGenerator(insight_queue=insight_queue)

        scanner = BackgroundScanner(
            exploration_engine=engine,
            action_generator=action_gen,
            endocrine=endocrine,
        )

        plugin = ExternalSignalPlugin()
        plugin.record()  # 1 message → surprise

        await scanner.start()
        await scanner.register_plugin(plugin)

        # Let one poll complete normally
        await asyncio.sleep(0.05)

        # Spike cortisol → scanning should pause
        endocrine.trigger("cortisol", 0.8)
        plugin.record()  # More messages, but scanner paused

        # Verify scanning is now blocked
        assert scanner._should_scan() is False

        await scanner.stop()
