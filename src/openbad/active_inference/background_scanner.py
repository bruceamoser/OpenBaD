"""Background scanning scheduler with endocrine-driven behavior."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from openbad.active_inference.exploration_actions import ExplorationActionGenerator

if TYPE_CHECKING:
    from openbad.active_inference.engine import ExplorationEngine
    from openbad.active_inference.plugin_interface import ObservationPlugin
    from openbad.endocrine.controller import EndocrineController

logger = logging.getLogger(__name__)

# Endocrine thresholds for scanning modulation
_CORTISOL_PAUSE_THRESHOLD = 0.5
_ADRENALINE_SUSPEND_THRESHOLD = 0.6
_DOPAMINE_BOOST_THRESHOLD = 0.3  # Below this → increase polling (boredom)
_DOPAMINE_BOOST_FACTOR = 0.5  # Poll at 50% of base interval when bored


class BackgroundScanner:
    """Manages periodic observation plugin polling with endocrine-driven scheduling.

    Behavior modulation:
    - Low dopamine (boredom): faster polling to seek novelty
    - High cortisol (stress): pauses exploration to conserve resources
    - Adrenaline spike: suspends entirely (emergency handling)
    - SLEEP/EMERGENCY FSM states: no scanning
    """

    def __init__(
        self,
        exploration_engine: ExplorationEngine,
        action_generator: ExplorationActionGenerator,
        endocrine: EndocrineController | None = None,
    ) -> None:
        self._engine = exploration_engine
        self._action_generator = action_generator
        self._endocrine = endocrine
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

            adjusted_interval = self._compute_interval(interval)
            await asyncio.sleep(adjusted_interval)

    def _should_scan(self) -> bool:
        """Determine if scanning should occur given FSM state and endocrine levels."""
        # FSM suppression: no scanning in SLEEP or EMERGENCY
        if self._current_state in ("SLEEP", "EMERGENCY"):
            return False

        if self._endocrine is None:
            return True

        # Adrenaline spike → suspend entirely (all resources to threat)
        if self._endocrine.level("adrenaline") >= _ADRENALINE_SUSPEND_THRESHOLD:
            return False

        # High cortisol → pause exploration (conserve resources)
        return self._endocrine.level("cortisol") < _CORTISOL_PAUSE_THRESHOLD

    def _compute_interval(self, base_interval: int) -> float:
        """Compute polling interval modulated by endocrine state.

        Low dopamine (boredom) → faster polling to seek novelty.
        FSM ACTIVE state → slower polling to yield resources.
        """
        interval = float(base_interval)

        # FSM state multiplier
        if self._current_state == "ACTIVE":
            interval *= 3.0
        elif self._current_state == "SLEEP":
            interval *= 10.0

        # Endocrine modulation: low dopamine = boredom = seek novelty faster
        if self._endocrine is not None:
            dopamine = self._endocrine.level("dopamine")
            if dopamine < _DOPAMINE_BOOST_THRESHOLD:
                interval *= _DOPAMINE_BOOST_FACTOR

        return interval
