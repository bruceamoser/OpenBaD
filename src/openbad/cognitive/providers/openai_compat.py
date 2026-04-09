"""OpenAI-compatible provider adapters.

Supports OpenAI, OpenRouter, Groq, xAI, Mistral, OpenAI-Codex, and
arbitrary custom endpoints that speak the Chat Completions API.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
    ProviderAdapter,
)

_RETRY_BACKOFF_S = 0.5


class ProviderUnavailableError(Exception):
    """Raised when a provider cannot be used (e.g. missing API key)."""


class OpenAICompatProvider(ProviderAdapter):
    """Base adapter for any OpenAI Chat-Completions-compatible API.

    Parameters
    ----------
    provider_name:
        Logical name used in logs and ``CompletionResult.provider``.
    base_url:
        API root URL (no trailing slash).
    api_key_env:
        Environment variable that holds the API key.
    default_model:
        Model ID to use when none is specified.
    timeout_s:
        HTTP request timeout in seconds.
    max_retries:
        Retries for transient HTTP errors.
    extra_headers:
        Additional headers merged into every request.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key_env: str = "",
        default_model: str = "",
        timeout_s: float = 30,
        max_retries: int = 2,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env
        self._default_model = default_model
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._max_retries = max_retries
        self._extra_headers = extra_headers or {}

    # ------------------------------------------------------------------ #
    # Key helpers
    # ------------------------------------------------------------------ #

    def _resolve_api_key(self) -> str:
        """Return the API key or raise ``ProviderUnavailable``."""
        if not self._api_key_env:
            return ""
        key = os.environ.get(self._api_key_env, "")
        if not key:
            msg = (
                f"Provider '{self._provider_name}' requires env var "
                f"'{self._api_key_env}' but it is not set."
            )
            raise ProviderUnavailableError(msg)
        return key

    def _headers(self) -> dict[str, str]:
        key = self._resolve_api_key()
        h: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        h.update(self._extra_headers)
        return h

    # ------------------------------------------------------------------ #
    # ProviderAdapter interface
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        model = model_id or self._default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            **kwargs,
        }
        t0 = time.monotonic()
        data = await self._post("/v1/chat/completions", payload)
        latency_ms = (time.monotonic() - t0) * 1000

        choice = data.get("choices", [{}])[0] if data.get("choices") else {}
        usage = data.get("usage", {})

        return CompletionResult(
            content=choice.get("message", {}).get("content", ""),
            model_id=data.get("model", model),
            provider=self._provider_name,
            tokens_used=usage.get("total_tokens", 0),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason", ""),
        )

    async def stream(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        model = model_id or self._default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            **kwargs,
        }
        url = f"{self._base_url}/v1/chat/completions"
        headers = self._headers()
        async with (
            aiohttp.ClientSession(timeout=self._timeout) as session,
            session.post(url, json=payload, headers=headers) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.content:
                text = line.decode().strip()
                if not text or not text.startswith("data:"):
                    continue
                data_str = text[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if delta:
                    yield delta

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get("/v1/models")
        models: list[ModelInfo] = []
        for m in data.get("data", []):
            models.append(
                ModelInfo(
                    model_id=m.get("id", ""),
                    provider=self._provider_name,
                )
            )
        return models

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            models = await self.list_models()
            latency_ms = (time.monotonic() - t0) * 1000
            return HealthStatus(
                provider=self._provider_name,
                available=True,
                latency_ms=latency_ms,
                models_available=len(models),
            )
        except (aiohttp.ClientError, TimeoutError, OSError, ProviderUnavailableError):
            return HealthStatus(provider=self._provider_name, available=False)

    # ------------------------------------------------------------------ #
    # HTTP with retry
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = self._headers()
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                async with (
                    aiohttp.ClientSession(timeout=self._timeout) as session,
                    session.post(url, json=payload, headers=headers) as resp,
                ):
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt <= self._max_retries:
                    import asyncio

                    await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
        raise last_exc  # type: ignore[misc]

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = self._headers()
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                async with (
                    aiohttp.ClientSession(timeout=self._timeout) as session,
                    session.get(url, headers=headers) as resp,
                ):
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt <= self._max_retries:
                    import asyncio

                    await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
        raise last_exc  # type: ignore[misc]


# ------------------------------------------------------------------ #
# Concrete thin adapters
# ------------------------------------------------------------------ #


def openai_provider(**overrides: Any) -> OpenAICompatProvider:
    """Standard OpenAI API."""
    defaults = {
        "provider_name": "openai",
        "base_url": "https://api.openai.com",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def openai_codex_provider(**overrides: Any) -> OpenAICompatProvider:
    """OpenAI Codex (OAuth-token-based)."""
    defaults = {
        "provider_name": "openai-codex",
        "base_url": "https://api.openai.com",
        "api_key_env": "OPENAI_CODEX_TOKEN",
        "default_model": "codex",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def openrouter_provider(**overrides: Any) -> OpenAICompatProvider:
    """OpenRouter aggregator."""
    defaults = {
        "provider_name": "openrouter",
        "base_url": "https://openrouter.ai/api",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def groq_provider(**overrides: Any) -> OpenAICompatProvider:
    """Groq inference API."""
    defaults = {
        "provider_name": "groq",
        "base_url": "https://api.groq.com/openai",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.1-8b-instant",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def xai_provider(**overrides: Any) -> OpenAICompatProvider:
    """xAI (Grok) API."""
    defaults = {
        "provider_name": "xai",
        "base_url": "https://api.x.ai",
        "api_key_env": "XAI_API_KEY",
        "default_model": "grok-3-mini",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def mistral_provider(**overrides: Any) -> OpenAICompatProvider:
    """Mistral AI API."""
    defaults = {
        "provider_name": "mistral",
        "base_url": "https://api.mistral.ai",
        "api_key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-small-latest",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def custom_provider(
    *,
    base_url: str,
    api_key_env: str = "",
    default_model: str = "",
    **overrides: Any,
) -> OpenAICompatProvider:
    """User-configured custom OpenAI-compatible endpoint."""
    defaults: dict[str, Any] = {
        "provider_name": "custom",
        "base_url": base_url,
        "api_key_env": api_key_env,
        "default_model": default_model,
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)
