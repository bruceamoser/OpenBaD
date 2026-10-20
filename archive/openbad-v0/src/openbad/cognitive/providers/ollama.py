"""Ollama provider adapter — local SLM inference via the Ollama HTTP API."""

from __future__ import annotations

import json
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

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2"
_DEFAULT_TIMEOUT_S = 30
_MAX_RETRIES = 2
_RETRY_BACKOFF_S = 0.5


class OllamaProvider(ProviderAdapter):
    """Adapter for the Ollama local inference server.

    Parameters
    ----------
    base_url:
        Ollama HTTP endpoint (default ``http://localhost:11434``).
    default_model:
        Model to use when none is specified (default ``llama3.2``).
    timeout_s:
        HTTP request timeout in seconds.
    max_retries:
        Number of retry attempts for transient errors.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        default_model: str = _DEFAULT_MODEL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._max_retries = max_retries

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
            "prompt": prompt,
            "stream": False,
            **kwargs,
        }
        t0 = time.monotonic()
        data = await self._post("/api/generate", payload)
        latency_ms = (time.monotonic() - t0) * 1000

        return CompletionResult(
            content=data.get("response", ""),
            model_id=model,
            provider="ollama",
            tokens_used=data.get("eval_count", 0),
            latency_ms=latency_ms,
            finish_reason=data.get("done_reason", ""),
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
            "prompt": prompt,
            "stream": True,
            **kwargs,
        }
        url = f"{self._base_url}/api/generate"
        async with (
            aiohttp.ClientSession(timeout=self._timeout) as session,
            session.post(url, json=payload) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.content:
                text = line.decode().strip()
                if not text:
                    continue
                chunk = json.loads(text)
                token = chunk.get("response", "")
                if token:
                    yield token

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get("/api/tags")
        models: list[ModelInfo] = []
        for m in data.get("models", []):
            models.append(
                ModelInfo(
                    model_id=m.get("name", ""),
                    provider="ollama",
                    context_window=m.get("details", {}).get(
                        "context_length", 0
                    ),
                )
            )
        return models

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            models = await self.list_models()
            latency_ms = (time.monotonic() - t0) * 1000
            return HealthStatus(
                provider="ollama",
                available=True,
                latency_ms=latency_ms,
                models_available=len(models),
            )
        except (aiohttp.ClientError, TimeoutError, OSError):
            return HealthStatus(provider="ollama", available=False)

    async def embed(
        self, texts: list[str], model_id: str | None = None
    ) -> list[list[float]]:
        model = model_id or "nomic-embed-text"
        data = await self._post("/api/embed", {"model": model, "input": texts})
        return data["embeddings"]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST with retry logic."""
        url = f"{self._base_url}{path}"
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                async with (
                    aiohttp.ClientSession(timeout=self._timeout) as session,
                    session.post(url, json=payload) as resp,
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
        """GET with retry logic."""
        url = f"{self._base_url}{path}"
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                async with (
                    aiohttp.ClientSession(timeout=self._timeout) as session,
                    session.get(url) as resp,
                ):
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt <= self._max_retries:
                    import asyncio

                    await asyncio.sleep(_RETRY_BACKOFF_S * attempt)
        raise last_exc  # type: ignore[misc]
