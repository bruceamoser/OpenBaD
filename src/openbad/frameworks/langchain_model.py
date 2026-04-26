"""LangChain ``BaseChatModel`` wrapper around OpenBaD's ``ModelRouter``.

``OpenBaDChatModel`` presents a standard LangChain chat model interface
while delegating provider selection, cortisol-based downgrade, and fallback
chains to the existing :class:`~openbad.cognitive.model_router.ModelRouter`.
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
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from openbad.cognitive.model_router import ModelRouter, Priority
from openbad.cognitive.providers.base import ProviderAdapter
from openbad.usage_recorder import record_usage_event

log = logging.getLogger(__name__)


def _messages_to_prompt(messages: list[BaseMessage]) -> str:
    """Flatten LangChain messages into a single prompt string.

    This is used when the underlying ``ProviderAdapter`` only exposes a
    plain-text ``complete()`` / ``stream()`` interface.
    """
    parts: list[str] = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if isinstance(msg, SystemMessage):
            parts.append(f"[system] {content}")
        elif isinstance(msg, HumanMessage):
            parts.append(f"[user] {content}")
        elif isinstance(msg, AIMessage):
            parts.append(f"[assistant] {content}")
        else:
            parts.append(content)
    return "\n\n".join(parts)


def _extract_system_and_user(messages: list[BaseMessage]) -> tuple[str, str]:
    """Split messages into a system prompt and the remaining user prompt."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if isinstance(msg, SystemMessage):
            system_parts.append(content)
        else:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            user_parts.append(f"[{role}] {content}")
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


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
        """Async generation using the model router."""
        priority = Priority(kwargs.pop("priority", self.priority))
        cortisol = kwargs.pop("cortisol", self.cortisol)

        adapter, model_id, decision = await self._route(priority, cortisol)
        prompt = _messages_to_prompt(messages)

        result = await adapter.complete(prompt, model_id, **kwargs)

        self._record_usage(
            provider=decision.provider,
            model=model_id,
            tokens=result.tokens_used,
        )

        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content=result.content),
                    generation_info={
                        "provider": decision.provider,
                        "model_id": model_id,
                        "tokens_used": result.tokens_used,
                        "latency_ms": result.latency_ms,
                        "cortisol_downgrade": decision.cortisol_downgrade,
                        "fallback_index": decision.fallback_index,
                    },
                )
            ],
            llm_output={
                "provider": decision.provider,
                "model_id": model_id,
                "tokens_used": result.tokens_used,
            },
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Async streaming using the model router."""
        priority = Priority(kwargs.pop("priority", self.priority))
        cortisol = kwargs.pop("cortisol", self.cortisol)

        adapter, model_id, decision = await self._route(priority, cortisol)
        prompt = _messages_to_prompt(messages)

        total_tokens = 0
        async for token in adapter.stream(prompt, model_id, **kwargs):
            total_tokens += 1
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(content=token),
                generation_info={"provider": decision.provider, "model_id": model_id},
            )
            if run_manager:
                await run_manager.on_llm_new_token(token)
            yield chunk

        self._record_usage(
            provider=decision.provider,
            model=model_id,
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
    ) -> tuple[ProviderAdapter, str, Any]:
        """Delegate to the model router."""
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
