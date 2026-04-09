"""Cognitive orchestrator — wires event loop + router + context manager + strategies."""

from __future__ import annotations

import logging
from typing import Any

from openbad.cognitive.context_manager import ContextWindowManager
from openbad.cognitive.event_loop import CognitiveEventLoop
from openbad.cognitive.model_router import ModelRouter, Priority
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.cognitive.reasoning.base import ReasoningStrategy

log = logging.getLogger(__name__)


class CognitiveOrchestrator:
    """High-level coordinator for the cognitive module.

    Wires together the model router, context manager, reasoning strategies,
    and the event loop.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        router: ModelRouter,
        context_manager: ContextWindowManager,
        strategies: dict[Priority, ReasoningStrategy] | None = None,
        publish_fn: Any = None,
        validate_fn: Any = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._ctx = context_manager
        self._strategies = strategies or {}
        self._event_loop = CognitiveEventLoop(
            model_router=router,
            context_manager=context_manager,
            strategies=self._strategies,
            publish_fn=publish_fn,
            validate_fn=validate_fn,
        )

    @property
    def event_loop(self) -> CognitiveEventLoop:
        return self._event_loop

    async def start(self) -> None:
        """Start the cognitive module."""
        await self._event_loop.start()
        log.info("CognitiveOrchestrator started")

    async def stop(self) -> None:
        """Stop the cognitive module."""
        await self._event_loop.stop()
        log.info("CognitiveOrchestrator stopped")
