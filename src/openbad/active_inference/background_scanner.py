"""Background scanning scheduler with FSM state awareness."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from openbad.active_inference.exploration_actions import ExplorationActionGenerator

if TYPE_CHECKING:
    from openbad.active_inference.engine import ExplorationEngine
    from openbad.active_inference.plugin_interface import ObservationPlugin

logger = logging.getLogger(__name__)


class BackgroundScanner:
    """Manages periodic observation plugin polling with FSM-aware scheduling."""

    def __init__(
        self,
        exploration_engine: ExplorationEngine,
        action_generator: ExplorationActionGenerator,
    ) -> None:
        self._engine = exploration_engine
        self._action_generator = action_generator
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._current_state = "IDLE"

    def set_state(self, state: str) -> None:
        """Update FSM state to adjust scanning behavior."""
        self._current_state = state
        self._engine.set_state(state)

    async def start(self) -> None:
        """Start background scanning."""
        self._running = True
        logger.info("BackgroundScanner started")

    async def stop(self) -> None:
        """Stop all background scanning tasks."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("BackgroundScanner stopped")

    async def register_plugin(self, plugin: ObservationPlugin) -> None:
        """Register a plugin and start its polling task."""
        self._engine.add_plugin(plugin)
        if self._running:
            task = asyncio.create_task(self._poll_loop(plugin))
            self._tasks[plugin.source_id] = task

    async def _poll_loop(self, plugin: ObservationPlugin) -> None:
        """Periodic polling loop for a single plugin."""
        interval = plugin.poll_interval_seconds

        while self._running:
            if self._should_scan():
                try:
                    event = await self._engine.poll_plugin(plugin)
                    if event.explored:
                        await self._action_generator.process_high_surprise(
                            source_id=event.source_id,
                            surprise=event.surprise,
                            errors=event.errors,
                        )
                except Exception:
                    logger.exception(
                        "Error polling plugin %s",
                        plugin.source_id,
                    )

            adjusted_interval = self._get_interval_for_state(interval)
            await asyncio.sleep(adjusted_interval)

    def _should_scan(self) -> bool:
        """Determine if scanning should occur in current state."""
        return self._current_state != "SLEEP"

    def _get_interval_for_state(self, base_interval: int) -> int:
        """Adjust polling interval based on FSM state."""
        if self._current_state == "IDLE":
            return base_interval
        if self._current_state == "ACTIVE":
            return base_interval * 3
        if self._current_state == "SLEEP":
            return base_interval * 10
        return base_interval
