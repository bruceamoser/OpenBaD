"""Tests for background scanner with FSM state awareness."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.active_inference.background_scanner import BackgroundScanner
from openbad.active_inference.engine import ExplorationEngine, ExplorationEvent
from openbad.active_inference.exploration_actions import ExplorationActionGenerator
from openbad.active_inference.insight_queue import InsightQueue
from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class MockPlugin(ObservationPlugin):
    """Mock observation plugin for testing."""

    def __init__(self, source_id: str, poll_interval: int = 1) -> None:
        self._source_id = source_id
        self._poll_interval = poll_interval
        self.observe_count = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    async def observe(self) -> ObservationResult:
        self.observe_count += 1
        return ObservationResult(metrics={"count": self.observe_count})

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {"count": {"expected": 0.0, "tolerance": 1.0}}

    @property
    def poll_interval_seconds(self) -> int:
        return self._poll_interval


@pytest.mark.asyncio
async def test_scanner_start_stop():
    """Test scanner lifecycle."""
    engine_mock = MagicMock(spec=ExplorationEngine)
    generator_mock = MagicMock(spec=ExplorationActionGenerator)

    scanner = BackgroundScanner(engine_mock, generator_mock)

    await scanner.start()
    assert scanner._running is True

    await scanner.stop()
    assert scanner._running is False


@pytest.mark.asyncio
async def test_scanner_respects_sleep_state():
    """Test scanning paused during SLEEP state."""
    engine_mock = MagicMock(spec=ExplorationEngine)
    engine_mock.set_state = MagicMock()
    engine_mock.poll_plugin = AsyncMock(
        return_value=ExplorationEvent(
            source_id="test",
            surprise=0.3,
            explored=False,
        )
    )

    generator_mock = MagicMock(spec=ExplorationActionGenerator)
    scanner = BackgroundScanner(engine_mock, generator_mock)

    scanner.set_state("SLEEP")
    assert scanner._should_scan() is False

    scanner.set_state("IDLE")
    assert scanner._should_scan() is True


@pytest.mark.asyncio
async def test_register_plugin():
    """Test plugin registration."""
    engine_mock = MagicMock(spec=ExplorationEngine)
    engine_mock.add_plugin = MagicMock()
    engine_mock.poll_plugin = AsyncMock(
        return_value=ExplorationEvent(
            source_id="test",
            surprise=0.3,
            explored=False,
        )
    )

    generator_mock = MagicMock(spec=ExplorationActionGenerator)
    scanner = BackgroundScanner(engine_mock, generator_mock)

    plugin = MockPlugin("test_plugin")
    await scanner.start()
    await scanner.register_plugin(plugin)

    engine_mock.add_plugin.assert_called_once_with(plugin)
    assert "test_plugin" in scanner._tasks

    await scanner.stop()


@pytest.mark.asyncio
async def test_interval_adjustment_by_state():
    """Test polling interval adjusted by FSM state."""
    scanner = BackgroundScanner(MagicMock(), MagicMock())

    base_interval = 10

    scanner.set_state("IDLE")
    assert scanner._get_interval_for_state(base_interval) == 10

    scanner.set_state("ACTIVE")
    assert scanner._get_interval_for_state(base_interval) == 30

    scanner.set_state("SLEEP")
    assert scanner._get_interval_for_state(base_interval) == 100


@pytest.mark.asyncio
async def test_high_surprise_triggers_action():
    """Test high surprise events trigger exploration actions."""
    engine_mock = MagicMock(spec=ExplorationEngine)
    engine_mock.add_plugin = MagicMock()
    engine_mock.poll_plugin = AsyncMock(
        return_value=ExplorationEvent(
            source_id="test",
            surprise=0.85,
            explored=True,
            errors={"metric": 5.0},
        )
    )

    queue = InsightQueue()
    generator = ExplorationActionGenerator(queue)
    generator.process_high_surprise = AsyncMock()

    scanner = BackgroundScanner(engine_mock, generator)

    plugin = MockPlugin("test_plugin", poll_interval=1)
    await scanner.start()
    await scanner.register_plugin(plugin)

    await asyncio.sleep(0.1)
    await scanner.stop()

    generator.process_high_surprise.assert_called()


@pytest.mark.asyncio
async def test_plugin_error_handling():
    """Test plugin errors don't crash scanner."""

    class FailingPlugin(MockPlugin):
        async def observe(self) -> ObservationResult:
            raise RuntimeError("Plugin error")

    engine_mock = MagicMock(spec=ExplorationEngine)
    engine_mock.add_plugin = MagicMock()
    engine_mock.poll_plugin = AsyncMock(side_effect=RuntimeError("Plugin error"))

    generator_mock = MagicMock(spec=ExplorationActionGenerator)
    scanner = BackgroundScanner(engine_mock, generator_mock)

    plugin = FailingPlugin("failing_plugin", poll_interval=1)
    await scanner.start()
    await scanner.register_plugin(plugin)

    await asyncio.sleep(0.1)
    await scanner.stop()


@pytest.mark.asyncio
async def test_low_surprise_no_action():
    """Test low surprise doesn't trigger exploration."""
    engine_mock = MagicMock(spec=ExplorationEngine)
    engine_mock.add_plugin = MagicMock()
    engine_mock.poll_plugin = AsyncMock(
        return_value=ExplorationEvent(
            source_id="test",
            surprise=0.2,
            explored=False,
        )
    )

    generator_mock = MagicMock(spec=ExplorationActionGenerator)
    generator_mock.process_high_surprise = AsyncMock()

    scanner = BackgroundScanner(engine_mock, generator_mock)

    plugin = MockPlugin("test_plugin", poll_interval=1)
    await scanner.start()
    await scanner.register_plugin(plugin)

    await asyncio.sleep(0.1)
    await scanner.stop()

    generator_mock.process_high_surprise.assert_not_called()
