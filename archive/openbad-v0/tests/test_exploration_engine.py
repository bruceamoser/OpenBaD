"""Tests for the exploration engine."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from openbad.active_inference.budget import ExplorationBudget
from openbad.active_inference.config import ActiveInferenceConfig
from openbad.active_inference.engine import ExplorationEngine
from openbad.active_inference.plugin_interface import (
    ObservationPlugin,
    ObservationResult,
)
from openbad.active_inference.world_model import WorldModel


class StubPlugin(ObservationPlugin):
    """Minimal plugin for testing."""

    def __init__(
        self,
        src_id: str = "stub",
        metrics: dict[str, float] | None = None,
    ) -> None:
        self._src = src_id
        self._metrics = metrics or {"val": 50.0}

    @property
    def source_id(self) -> str:
        return self._src

    async def observe(self) -> ObservationResult:
        return ObservationResult(
            metrics=self._metrics,
            timestamp=datetime.now(tz=UTC),
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {"val": {"expected": 50.0, "tolerance": 10.0}}


@pytest.fixture
def cfg() -> ActiveInferenceConfig:
    return ActiveInferenceConfig(
        surprise_threshold=0.6,
        daily_token_budget=100,
        cooldown_seconds=0,
    )


@pytest.fixture
def wm() -> WorldModel:
    return WorldModel()


@pytest.fixture
def budget() -> ExplorationBudget:
    return ExplorationBudget(daily_limit=100, cooldown_seconds=0)


class TestExplorationEngine:
    async def test_poll_no_surprise(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        plugin = StubPlugin(metrics={"val": 50.0})
        engine.add_plugin(plugin)
        event = await engine.poll_plugin(plugin)
        assert event.surprise == pytest.approx(0.0)
        assert not event.explored

    async def test_poll_high_surprise_triggers_explore(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        plugin = StubPlugin(metrics={"val": 100.0})  # big deviation
        engine.add_plugin(plugin)
        event = await engine.poll_plugin(plugin)
        assert event.surprise >= cfg.surprise_threshold
        assert event.explored
        assert budget.remaining == 99

    async def test_suppressed_state_blocks_explore(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        plugin = StubPlugin(metrics={"val": 100.0})
        engine.add_plugin(plugin)
        engine.set_state("THROTTLED")
        event = await engine.poll_plugin(plugin)
        assert event.surprise >= cfg.surprise_threshold
        assert not event.explored  # suppressed

    async def test_budget_exhausted_blocks_explore(
        self, cfg: ActiveInferenceConfig, wm: WorldModel,
    ) -> None:
        empty_budget = ExplorationBudget(daily_limit=0, cooldown_seconds=0)
        engine = ExplorationEngine(cfg, wm, empty_budget)
        plugin = StubPlugin(metrics={"val": 100.0})
        engine.add_plugin(plugin)
        event = await engine.poll_plugin(plugin)
        assert not event.explored

    async def test_run_cycle(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        engine.add_plugin(StubPlugin("a", {"val": 100.0}))
        engine.add_plugin(StubPlugin("b", {"val": 50.0}))
        events = await engine.run_cycle()
        assert len(events) == 2

    async def test_plugin_error_handled(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        bad = StubPlugin()
        bad.observe = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        engine.add_plugin(bad)
        events = await engine.run_cycle()
        assert len(events) == 0  # error swallowed

    async def test_run_loop_stops(
        self, cfg: ActiveInferenceConfig, wm: WorldModel, budget: ExplorationBudget,
    ) -> None:
        engine = ExplorationEngine(cfg, wm, budget)
        engine.add_plugin(StubPlugin())
        stop = asyncio.Event()
        stop.set()
        await engine.run_loop(stop_event=stop)  # Should return immediately
