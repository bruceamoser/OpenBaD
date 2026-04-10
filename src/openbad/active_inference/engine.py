"""Exploration engine — orchestrates poll → surprise → action cycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from openbad.active_inference.budget import ExplorationBudget
from openbad.active_inference.config import ActiveInferenceConfig
from openbad.active_inference.plugin_interface import ObservationPlugin
from openbad.active_inference.surprise import aggregate_surprise
from openbad.active_inference.world_model import WorldModel

logger = logging.getLogger(__name__)


@dataclass
class ExplorationEvent:
    """Result of one exploration cycle iteration."""

    source_id: str
    surprise: float
    explored: bool
    errors: dict[str, float] = field(default_factory=dict)


class ExplorationEngine:
    """Polls plugins, computes surprise, and decides whether to explore."""

    def __init__(
        self,
        config: ActiveInferenceConfig,
        world_model: WorldModel,
        budget: ExplorationBudget,
        plugins: list[ObservationPlugin] | None = None,
    ) -> None:
        self._config = config
        self._world_model = world_model
        self._budget = budget
        self._plugins: list[ObservationPlugin] = plugins or []
        self._suppressed_states: set[str] = set(config.suppressed_in_states)
        self._current_state: str = "NOMINAL"

    # -- Plugin management ------------------------------------------------- #

    def add_plugin(self, plugin: ObservationPlugin) -> None:
        self._plugins.append(plugin)
        self._world_model.register_source(
            plugin.source_id,
            plugin.default_predictions(),
        )

    # -- State ------------------------------------------------------------- #

    def set_state(self, state: str) -> None:
        self._current_state = state

    @property
    def is_suppressed(self) -> bool:
        return self._current_state in self._suppressed_states

    # -- Core cycle -------------------------------------------------------- #

    async def poll_plugin(
        self,
        plugin: ObservationPlugin,
    ) -> ExplorationEvent:
        """Poll a single plugin and decide whether to explore."""
        result = await plugin.observe()
        errors = self._world_model.update(plugin.source_id, result.metrics)
        surprise = aggregate_surprise(errors)

        explored = False
        if (
            surprise >= self._config.surprise_threshold
            and not self.is_suppressed
            and self._budget.can_spend()
        ):
            self._budget.spend()
            explored = True

        return ExplorationEvent(
            source_id=plugin.source_id,
            surprise=surprise,
            explored=explored,
            errors=errors,
        )

    async def run_cycle(self) -> list[ExplorationEvent]:
        """Run one pass over all registered plugins (sequentially)."""
        events: list[ExplorationEvent] = []
        for plugin in self._plugins:
            try:
                event = await self.poll_plugin(plugin)
                events.append(event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Plugin %s failed during poll cycle",
                    plugin.source_id,
                    exc_info=True,
                )
        return events

    async def run_loop(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Continuously poll plugins at their configured interval.

        Respects ``max_concurrent`` from config (runs plugins sequentially
        when set to 1).
        """
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            await self.run_cycle()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=min(
                        (p.poll_interval_seconds for p in self._plugins),
                        default=60.0,
                    ),
                )
