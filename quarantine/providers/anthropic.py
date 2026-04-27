"""Anthropic Messages API provider adapter for Claude models."""

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

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_ANTHROPIC_VERSION = "2023-06-01"
_RETRY_BACKOFF_S = 0.5

# Known Anthropic models (no discovery endpoint available).
_KNOWN_MODELS: list[dict[str, Any]] = [
    {"id": "claude-sonnet-4-20250514", "context_window": 200_000},
    {"id": "claude-opus-4-20250514", "context_window": 200_000},
    {"id": "claude-3-5-haiku-20241022", "context_window": 200_000},
    {"id": "claude-3-5-sonnet-20241022", "context_window": 200_000},
]


class AnthropicKeyMissingError(Exception):
    """Raised when the Anthropic API key is not set."""


class AnthropicProvider(ProviderAdapter):
    """Adapter for the Anthropic Messages API.

    Parameters
    ----------
    base_url:
        API root (default ``https://api.anthropic.com``).
    api_key_env:
        Environment variable holding the API key.
    default_model:
        Model to use when none specified.
    timeout_s:
        HTTP timeout in seconds.
    max_retries:
        Retry attempts for transient errors.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = "",
        api_key_env: str = "ANTHROPIC_API_KEY",
        default_model: str = _DEFAULT_MODEL,
        timeout_s: float = 30,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._default_model = default_model
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._max_retries = max_retries

    # ------------------------------------------------------------------ #
    # Key / header helpers
    # ------------------------------------------------------------------ #

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        key = os.environ.get(self._api_key_env, "")
        if not key:
            msg = (
                f"Anthropic provider requires env var "
                f"'{self._api_key_env}' but it is not set."
            )
            raise AnthropicKeyMissingError(msg)
        return key

    def _headers(self) -> dict[str, str]:
        key = self._resolve_api_key()
        return {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

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
            "max_tokens": kwargs.pop("max_tokens", 4096),
            **kwargs,
        }
        t0 = time.monotonic()
        data = await self._post("/v1/messages", payload)
        latency_ms = (time.monotonic() - t0) * 1000

        content_blocks = data.get("content", [])
        text = "".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        )
        usage = data.get("usage", {})

        return CompletionResult(
            content=text,
            model_id=data.get("model", model),
            provider="anthropic",
            tokens_used=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            finish_reason=data.get("stop_reason", ""),
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
            "max_tokens": kwargs.pop("max_tokens", 4096),
            "stream": True,
            **kwargs,
        }
        url = f"{self._base_url}/v1/messages"
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
                if not data_str:
                    continue
                chunk = json.loads(data_str)
                if chunk.get("type") == "content_block_delta":
                    delta_text = chunk.get("delta", {}).get("text", "")
                    if delta_text:
                        yield delta_text

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                model_id=m["id"],
                provider="anthropic",
                context_window=m.get("context_window", 0),
            )
            for m in _KNOWN_MODELS
        ]

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            self._resolve_api_key()
            # Light-weight ping: send a minimal messages request that will
            # return quickly.  We use max_tokens=1 to keep cost minimal.
            payload: dict[str, Any] = {
                "model": self._default_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            await self._post("/v1/messages", payload)
            latency_ms = (time.monotonic() - t0) * 1000
            return HealthStatus(
                provider="anthropic",
                available=True,
                latency_ms=latency_ms,
                models_available=len(_KNOWN_MODELS),
            )
        except (
            aiohttp.ClientError,
            TimeoutError,
            OSError,
            AnthropicKeyMissingError,
        ):
            return HealthStatus(provider="anthropic", available=False)

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
