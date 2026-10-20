"""Exploration engine — orchestrates poll → surprise → action cycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

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
        *,
        memory_controller: Any | None = None,
        task_store: Any | None = None,
    ) -> None:
        self._config = config
        self._world_model = world_model
        self._budget = budget
        self._plugins: list[ObservationPlugin] = plugins or []
        self._suppressed_states: set[str] = set(config.suppressed_in_states)
        self._current_state: str = "NOMINAL"
        self._memory_controller = memory_controller
        self._task_store = task_store

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
            self._check_reconciliation(plugin.source_id, surprise)

        return ExplorationEvent(
            source_id=plugin.source_id,
            surprise=surprise,
            explored=explored,
            errors=errors,
        )

    def _check_reconciliation(self, source_id: str, surprise: float) -> None:
        """Check if high surprise warrants library reconciliation."""
        if self._memory_controller is None or self._task_store is None:
            return

        from openbad.active_inference.reconciliation import (
            check_library_reconciliation,
            create_reconciliation_task,
        )

        try:
            entries = self._memory_controller.semantic.query(source_id)
            for entry in entries:
                book_ids = check_library_reconciliation(entry, surprise)
                for book_id in book_ids:
                    create_reconciliation_task(
                        self._task_store,
                        book_id,
                        new_fact=str(entry.value),
                        reason=f"surprise={surprise:.2f} on {source_id}",
                    )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Reconciliation check failed for %s",
                source_id,
                exc_info=True,
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
