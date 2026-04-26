"""Cognitive orchestrator — wires event loop + router + context manager + strategies.

Also initialises the LangChain / LangGraph / CrewAI framework layer when
available, providing ``OpenBaDChatModel`` and LangGraph workflow dispatch
alongside the legacy ``CognitiveEventLoop``.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from openbad.cognitive.context_manager import ContextWindowManager
from openbad.cognitive.event_loop import CognitiveEventLoop
from openbad.cognitive.model_router import ModelRouter, Priority
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.cognitive.reasoning.base import ReasoningStrategy
from openbad.frameworks.langchain_model import OpenBaDChatModel
from openbad.usage_recorder import UsageRecorder

log = logging.getLogger(__name__)


class CognitiveOrchestrator:
    """High-level coordinator for the cognitive module.

    Wires together the model router, context manager, reasoning strategies,
    the event loop, and the framework layer (LangChain/LangGraph/CrewAI).
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        router: ModelRouter,
        context_manager: ContextWindowManager,
        strategies: dict[Priority, ReasoningStrategy] | None = None,
        publish_fn: Any = None,
        validate_fn: Any = None,
        callbacks: list[BaseCallbackHandler] | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._ctx = context_manager
        self._strategies = strategies or {}
        self._usage_recorder = UsageRecorder()
        self._event_loop = CognitiveEventLoop(
            model_router=router,
            context_manager=context_manager,
            strategies=self._strategies,
            publish_fn=publish_fn,
            validate_fn=validate_fn,
            usage_recorder=self._usage_recorder,
        )

        # ── Framework layer ──
        self._chat_model = OpenBaDChatModel(router=router)
        self._callbacks: list[BaseCallbackHandler] = callbacks or []

    @property
    def event_loop(self) -> CognitiveEventLoop:
        return self._event_loop

    @property
    def chat_model(self) -> OpenBaDChatModel:
        """LangChain-compatible chat model wrapping the ModelRouter."""
        return self._chat_model

    @property
    def callbacks(self) -> list[BaseCallbackHandler]:
        """LangChain callback handlers for endocrine, immune, and telemetry."""
        return list(self._callbacks)

    async def start(self) -> None:
        """Start the cognitive module."""
        await self._event_loop.start()
        log.info("CognitiveOrchestrator started (framework layer active)")

    async def stop(self) -> None:
        """Stop the cognitive module."""
        await self._event_loop.stop()
        self._usage_recorder.close()
        log.info("CognitiveOrchestrator stopped")
