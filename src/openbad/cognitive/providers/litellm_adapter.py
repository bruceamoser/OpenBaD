"""LiteLLM-based provider adapter.

Single adapter that replaces per-provider implementations (Ollama, Anthropic,
OpenAI-compat, GitHub Copilot) by delegating to LiteLLM's unified interface.

LiteLLM handles format translation, auth, retries, and streaming for 100+
providers. Model strings follow LiteLLM conventions:
    - ``github_copilot/gpt-4o``
    - ``ollama/llama3.2``
    - ``anthropic/claude-sonnet-4``
    - ``openai/gpt-4o-mini``
    - ``groq/llama-3.1-8b-instant``
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import litellm

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
    ProviderAdapter,
)

log = logging.getLogger(__name__)


# Map internal provider names → LiteLLM model prefix.
# NOTE: ``github-copilot`` is deliberately mapped to ``openai`` so that
# LiteLLM routes through its generic OpenAI-compatible codepath.  The
# built-in ``github_copilot`` provider triggers an interactive OAuth
# device-flow that blocks for minutes — unusable from a headless service.
_PROVIDER_PREFIX: dict[str, str] = {
    "github-copilot": "openai",
    "ollama": "ollama",
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
    "groq": "groq",
    "xai": "xai",
    "mistral": "mistral",
}


def litellm_model_name(provider: str, model: str) -> str:
    """Build a fully-qualified LiteLLM model string.

    If the model already contains a ``/`` prefix (e.g. ``ollama/llama3.2``),
    it is returned as-is.

    Unknown provider names (e.g. ``custom``) are mapped to ``openai`` so
    LiteLLM routes them through its OpenAI-compatible codepath — this is
    the correct behaviour for llama.cpp, vLLM, and similar servers that
    expose an ``/v1/chat/completions`` endpoint.
    """
    if "/" in model:
        return model
    prefix = _PROVIDER_PREFIX.get(provider, "openai")
    return f"{prefix}/{model}"


class LiteLLMAdapter(ProviderAdapter):
    """Unified LLM adapter backed by LiteLLM.

    Parameters
    ----------
    provider_name:
        Logical provider identifier (e.g. ``github-copilot``, ``ollama``).
    default_model:
        LiteLLM model string used when none is passed to calls.
    api_key:
        Explicit API key.  Passed as ``api_key`` kwarg to LiteLLM.
    api_base:
        Custom API base URL (for llama.cpp, local Ollama, etc.).
    timeout_s:
        Request timeout in seconds.
    max_retries:
        Number of retries for transient failures.
    extra_kwargs:
        Additional keyword arguments forwarded to every LiteLLM call
        (e.g. ``{"custom_llm_provider": ...}``).
    """

    def __init__(
        self,
        *,
        provider_name: str = "",
        default_model: str = "",
        api_key: str = "",
        api_base: str = "",
        timeout_s: float = 30,
        max_retries: int = 2,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._default_model = default_model
        self._api_key = api_key or None
        self._api_base = api_base or None
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._extra: dict[str, Any] = extra_kwargs or {}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_model(self, model_id: str | None) -> str:
        return model_id or self._default_model

    def _common_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "timeout": self._timeout_s,
            "num_retries": self._max_retries,
        }
        if self._api_key:
            kw["api_key"] = self._api_key
        if self._api_base:
            kw["api_base"] = self._api_base
        kw.update(self._extra)
        return kw

    # ------------------------------------------------------------------ #
    # ProviderAdapter interface
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        model = self._resolve_model(model_id)
        messages = kwargs.pop("messages", None) or [
            {"role": "user", "content": prompt},
        ]
        common = self._common_kwargs()
        common.update(kwargs)

        t0 = time.monotonic()
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=False,
            **common,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        choice = response.choices[0] if response.choices else None  # type: ignore[union-attr]
        content = choice.message.content or "" if choice else ""
        finish = choice.finish_reason or "" if choice else ""
        usage = getattr(response, "usage", None)
        tokens = usage.total_tokens if usage else 0

        return CompletionResult(
            content=content,
            model_id=getattr(response, "model", model),
            provider=self._provider_name,
            tokens_used=tokens,
            latency_ms=latency_ms,
            finish_reason=finish,
        )

    async def stream(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        model = self._resolve_model(model_id)
        messages = kwargs.pop("messages", None) or [
            {"role": "user", "content": prompt},
        ]
        common = self._common_kwargs()
        common.update(kwargs)

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=True,
            **common,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None  # type: ignore[union-attr]
            if delta and delta.content:
                yield delta.content

    async def list_models(self) -> list[ModelInfo]:
        """List known models for this provider.

        LiteLLM doesn't have a universal ``list_models`` for every backend,
        so we return an empty list.  Model enumeration is handled by the WUI
        provider pages using provider-specific discovery.
        """
        return []

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            model = self._default_model
            common = self._common_kwargs()
            # Lightweight ping: 1 token completion
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
                **common,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            usage = getattr(response, "usage", None)
            tokens = int(usage.total_tokens) if usage else 0
            return HealthStatus(
                provider=self._provider_name,
                available=True,
                latency_ms=latency_ms,
                tokens_used=tokens,
            )
        except Exception:
            log.debug("LiteLLM health check failed for %s", self._provider_name, exc_info=True)
            return HealthStatus(provider=self._provider_name, available=False)

    # ------------------------------------------------------------------ #
    # Agentic completion (non-streaming with tool support)
    # ------------------------------------------------------------------ #

    async def agentic_complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Non-streaming completion with optional tool definitions.

        Returns the raw LiteLLM ``ModelResponse`` so the caller can inspect
        ``tool_calls`` on the assistant message.
        """
        common = self._common_kwargs()
        common.update(kwargs)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            **common,
        }
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = "auto"
        return await litellm.acompletion(**call_kwargs)
