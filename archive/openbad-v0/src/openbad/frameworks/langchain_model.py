"""LangChain ``BaseChatModel`` wrapper around OpenBaD's ``ModelRouter``.

``OpenBaDChatModel`` presents a standard LangChain chat model interface
while delegating provider selection, cortisol-based downgrade, and fallback
chains to the existing :class:`~openbad.cognitive.model_router.ModelRouter`.

The router now returns a ``BaseChatModel`` per-provider, so this wrapper
delegates generation to the underlying model — messages are NOT flattened.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from openbad.cognitive.model_router import ModelRouter, Priority
from openbad.usage_recorder import record_usage_event

log = logging.getLogger(__name__)


class OpenBaDChatModel(BaseChatModel):
    """LangChain chat model backed by OpenBaD's ``ModelRouter``.

    Parameters
    ----------
    router:
        The :class:`~openbad.cognitive.model_router.ModelRouter` that
        selects provider/model based on priority, cortisol, and budget.
    priority:
        Default routing priority.  Can be overridden per-call via
        ``model_kwargs={"priority": Priority.CRITICAL}``.
    cortisol:
        Current cortisol level (0.0–1.0).  When above the router's
        threshold the model is downgraded to cheaper alternatives.
    system_name:
        Identifier used for usage-recording attribution.
    """

    # Pydantic v2 fields — LangChain requires these to be class-level.
    router: Any  # ModelRouter (Any to avoid pydantic issues with complex types)
    priority: int = Priority.MEDIUM
    cortisol: float = 0.0
    system_name: str = "chat"

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Required BaseChatModel overrides
    # ------------------------------------------------------------------

    @property
    def _llm_type(self) -> str:
        return "openbad"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "cortisol": self.cortisol,
            "system_name": self.system_name,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Synchronous generation — delegates to async via event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._agenerate(messages, stop=stop, **kwargs))
                return future.result()
        return asyncio.run(self._agenerate(messages, stop=stop, **kwargs))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation — delegates to the underlying BaseChatModel."""
        priority = Priority(kwargs.pop("priority", self.priority))
        cortisol = kwargs.pop("cortisol", self.cortisol)

        chat_model, _crew_llm, decision = await self._route(priority, cortisol)

        result = await chat_model.agenerate([messages], stop=stop, **kwargs)

        # Extract token count from the underlying model result.
        tokens_used = 0
        if result.llm_output and isinstance(result.llm_output, dict):
            usage = result.llm_output.get("token_usage", {})
            if isinstance(usage, dict):
                tokens_used = int(usage.get("total_tokens", 0))

        self._record_usage(
            provider=decision.provider,
            model=decision.model_id,
            tokens=tokens_used,
        )

        # Re-wrap into a ChatResult with routing metadata.
        generations = []
        for gen_list in result.generations:
            for gen in gen_list:
                msg = gen.message if hasattr(gen, "message") else AIMessage(content=gen.text)
                generations.append(
                    ChatGeneration(
                        message=msg,
                        generation_info={
                            "provider": decision.provider,
                            "model_id": decision.model_id,
                            "tokens_used": tokens_used,
                            "cortisol_downgrade": decision.cortisol_downgrade,
                            "fallback_index": decision.fallback_index,
                        },
                    )
                )
        return ChatResult(
            generations=generations,
            llm_output={
                "provider": decision.provider,
                "model_id": decision.model_id,
                "tokens_used": tokens_used,
            },
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Async streaming — delegates to the underlying BaseChatModel."""
        priority = Priority(kwargs.pop("priority", self.priority))
        cortisol = kwargs.pop("cortisol", self.cortisol)

        chat_model, _crew_llm, decision = await self._route(priority, cortisol)

        total_tokens = 0
        async for event in chat_model.astream(messages, stop=stop, **kwargs):
            content = event.content if hasattr(event, "content") else str(event)
            total_tokens += 1
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(content=content),
                generation_info={"provider": decision.provider, "model_id": decision.model_id},
            )
            if run_manager:
                await run_manager.on_llm_new_token(content)
            yield chunk

        self._record_usage(
            provider=decision.provider,
            model=decision.model_id,
            tokens=total_tokens,
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Synchronous streaming — wraps async stream."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _collect() -> list[ChatGenerationChunk]:
            chunks: list[ChatGenerationChunk] = []
            async for chunk in self._astream(messages, stop=stop, **kwargs):
                chunks.append(chunk)
            return chunks

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _collect())
                yield from future.result()
        else:
            yield from asyncio.run(_collect())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _route(
        self,
        priority: Priority,
        cortisol: float,
    ) -> tuple[Any, Any, Any]:
        """Delegate to the model router.

        Returns ``(chat_model, crew_llm, decision)``.
        """
        router: ModelRouter = self.router
        return await router.route(priority, cortisol=cortisol)

    def _record_usage(
        self,
        *,
        provider: str,
        model: str,
        tokens: int,
    ) -> None:
        """Record token usage for observability."""
        if tokens > 0:
            try:
                record_usage_event(
                    provider=provider,
                    model=model,
                    system=self.system_name,
                    tokens=tokens,
                )
            except Exception:
                log.debug("Failed to record usage", exc_info=True)
