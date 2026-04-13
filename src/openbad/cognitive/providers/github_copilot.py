"""GitHub Copilot Chat API provider adapter with device-flow OAuth."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
    ProviderAdapter,
)

_COPILOT_API_URL = "https://api.githubcopilot.com"
_GITHUB_OAUTH_DEVICE_URL = "https://github.com/login/device/code"
_GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_RETRY_BACKOFF_S = 0.5

_KNOWN_MODELS: list[dict[str, Any]] = [
    {"id": "claude-opus-4.6", "context_window": 200_000},
    {"id": "claude-sonnet-4.6", "context_window": 200_000},
    {"id": "claude-haiku-4.5", "context_window": 200_000},
    {"id": "claude-opus-4.5", "context_window": 200_000},
    {"id": "claude-sonnet-4", "context_window": 200_000},
    {"id": "claude-sonnet-4.5", "context_window": 200_000},
    {"id": "gpt-5.3-codex", "context_window": 128_000},
    {"id": "gpt-5.4", "context_window": 128_000},
    {"id": "gpt-5.4-mini", "context_window": 128_000},
    {"id": "gpt-5.2", "context_window": 128_000},
    {"id": "gpt-5.2-codex", "context_window": 128_000},
    {"id": "gpt-5.1", "context_window": 128_000},
    {"id": "gpt-5-mini", "context_window": 128_000},
    {"id": "gpt-4o", "context_window": 128_000},
    {"id": "gpt-4o-mini", "context_window": 128_000},
    {"id": "gpt-4.1", "context_window": 128_000},
    {"id": "gemini-2.5-pro", "context_window": 1_000_000},
    {"id": "gemini-3.1-pro-preview", "context_window": 1_000_000},
    {"id": "gemini-3-flash-preview", "context_window": 1_000_000},
    {"id": "grok-code-fast-1", "context_window": 128_000},
    {"id": "raptor-mini-preview", "context_window": 128_000},
]
_MODEL_DISCOVERY_PATHS = ("/models", "/chat/models", "/v1/models")

_TOKEN_DIR = Path.home() / ".openbad"
_TOKEN_FILE = _TOKEN_DIR / "copilot_token.json"
_GITHUB_REFRESH_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105


@dataclass
class DeviceCodeResponse:
    """Response from the device code request."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class CopilotAuthError(Exception):
    """Raised when Copilot authentication fails."""


class GitHubCopilotProvider(ProviderAdapter):
    """Adapter for the GitHub Copilot Chat API.

    Parameters
    ----------
    default_model:
        Model to use when none specified.
    timeout_s:
        HTTP timeout in seconds.
    max_retries:
        Retry attempts for transient errors.
    token_file:
        Path to store the encrypted token.
    """

    def __init__(
        self,
        *,
        default_model: str = "gpt-4o",
        timeout_s: float = 30,
        max_retries: int = 2,
        token_file: Path = _TOKEN_FILE,
    ) -> None:
        self._default_model = default_model
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._max_retries = max_retries
        self._token_file = token_file
        self._cached_token: str = ""
        self._token_expires_at: float = 0
        self._cached_refresh_token: str = ""

    # ------------------------------------------------------------------ #
    # Token management
    # ------------------------------------------------------------------ #

    def _load_token(self) -> tuple[str | None, str]:
        """Load token from disk. Returns (access_token_or_None, refresh_token)."""
        if not self._token_file.exists():
            return None, ""
        data = json.loads(self._token_file.read_text())
        refresh_token = data.get("refresh_token", "")
        expires_at = data.get("expires_at", 0)
        if time.time() >= expires_at:
            # Access token expired but return refresh_token so caller can renew
            return None, refresh_token
        self._token_expires_at = expires_at
        return data.get("access_token", ""), refresh_token

    def _save_token(
        self,
        access_token: str,
        expires_in: int,
        refresh_token: str = "",
    ) -> None:
        """Save token to disk with restricted permissions."""
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in,
        }
        if refresh_token:
            data["refresh_token"] = refresh_token
        elif self._token_file.exists():
            # Preserve existing refresh_token if we have one
            try:
                existing = json.loads(self._token_file.read_text())
                if existing.get("refresh_token"):
                    data["refresh_token"] = existing["refresh_token"]
            except (OSError, json.JSONDecodeError):
                pass
        self._token_file.write_text(json.dumps(data))
        with contextlib.suppress(OSError):
            self._token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    async def _refresh_access_token(self, refresh_token: str) -> str:
        """Exchange a refresh token for a new access token and persist it."""
        payload = {
            "client_id": _COPILOT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session, session.post(
            _GITHUB_REFRESH_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        if "error" in data:
            msg = (
                f"Token refresh failed: {data.get('error')} — "
                f"{data.get('error_description', '')}"
            )
            raise CopilotAuthError(msg)

        new_token = data.get("access_token", "")
        if not new_token:
            raise CopilotAuthError("Token refresh returned no access_token")

        expires_in = int(data.get("expires_in", 28800))
        new_refresh = data.get("refresh_token", refresh_token)  # GitHub may rotate it
        self._save_token(new_token, expires_in, new_refresh)
        self._cached_token = new_token
        self._cached_refresh_token = new_refresh
        self._token_expires_at = time.time() + expires_in
        return new_token

    async def _get_token_async(self) -> str:
        """Return a valid token, auto-refreshing if expired."""
        # Env var always wins
        env_token = os.environ.get("GITHUB_COPILOT_TOKEN", "")
        if env_token:
            return env_token

        # In-memory cache still valid
        if self._cached_token and time.time() < self._token_expires_at:
            return self._cached_token

        # Try loading from disk
        token, refresh_token = self._load_token()
        if token:
            self._cached_token = token
            if refresh_token:
                self._cached_refresh_token = refresh_token
            return token

        # Token expired — attempt refresh if we have a refresh_token
        if not refresh_token:
            refresh_token = self._cached_refresh_token

        if refresh_token:
            try:
                return await self._refresh_access_token(refresh_token)
            except (aiohttp.ClientError, CopilotAuthError):
                pass  # fall through to hard error

        msg = (
            "No Copilot token available. Set GITHUB_COPILOT_TOKEN env var "
            "or run device-flow authentication."
        )
        raise CopilotAuthError(msg)

    def _get_token(self) -> str:
        """Sync token accessor — only valid when token is cached/env-var."""
        env_token = os.environ.get("GITHUB_COPILOT_TOKEN", "")
        if env_token:
            return env_token
        if self._cached_token and time.time() < self._token_expires_at:
            return self._cached_token
        token, refresh_token = self._load_token()
        if token:
            self._cached_token = token
            if refresh_token:
                self._cached_refresh_token = refresh_token
            return token
        msg = (
            "No Copilot token available. Set GITHUB_COPILOT_TOKEN env var "
            "or run device-flow authentication."
        )
        raise CopilotAuthError(msg)

    def token_ttl_seconds(self) -> float | None:
        """Return seconds until the stored token expires, or None if no token."""
        if os.environ.get("GITHUB_COPILOT_TOKEN", ""):
            return float("inf")  # env var tokens are managed externally
        if not self._token_file.exists():
            return None
        try:
            data = json.loads(self._token_file.read_text())
            return data.get("expires_at", 0) - time.time()
        except (OSError, json.JSONDecodeError):
            return None

    def _headers(self) -> dict[str, str]:
        token = self._get_token()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Editor-Version": "openbad/0.1.0",
        }

    async def _headers_async(self) -> dict[str, str]:
        token = await self._get_token_async()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Editor-Version": "openbad/0.1.0",
        }

    # ------------------------------------------------------------------ #
    # Device-flow OAuth
    # ------------------------------------------------------------------ #

    async def request_device_code(self) -> DeviceCodeResponse:
        """Request a device code for user authorization."""
        url = _GITHUB_OAUTH_DEVICE_URL
        payload = {"client_id": _COPILOT_CLIENT_ID, "scope": "copilot"}
        async with aiohttp.ClientSession(timeout=self._timeout) as session, session.post(
            url,
            data=payload,
            headers={"Accept": "application/json"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return DeviceCodeResponse(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            interval=data.get("interval", 5),
            expires_in=data.get("expires_in", 900),
        )

    async def poll_for_token(self, device_code: str, interval: int = 5) -> str:
        """Poll GitHub OAuth for access token after user authorizes."""
        import asyncio

        while True:
            result = await self.poll_for_token_once(device_code)
            state = result.get("state", "error")

            if state == "authorized":
                return str(result["access_token"])
            if state == "authorization_pending":
                await asyncio.sleep(interval)
                continue
            if state == "slow_down":
                interval = int(result.get("interval", interval + 5))
                await asyncio.sleep(interval)
                continue

            msg = f"OAuth error: {result.get('error', '')} — {result.get('error_description', '')}"
            raise CopilotAuthError(msg)

    async def poll_for_token_once(self, device_code: str) -> dict[str, Any]:
        """Check the OAuth device-code state once.

        Returns a dict with a ``state`` field. Possible states are:
        ``authorized``, ``authorization_pending``, ``slow_down``, and ``error``.
        """
        url = _GITHUB_OAUTH_TOKEN_URL
        payload = {
            "client_id": _COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }

        async with aiohttp.ClientSession(timeout=self._timeout) as session, session.post(
            url,
            data=payload,
            headers={"Accept": "application/json"},
        ) as resp:
            data = await resp.json(content_type=None)

        if "access_token" in data:
            token = data["access_token"]
            expires_in = data.get("expires_in", 28800)
            refresh_token = data.get("refresh_token", "")
            self._save_token(token, expires_in, refresh_token)
            self._cached_token = token
            self._cached_refresh_token = refresh_token
            self._token_expires_at = time.time() + expires_in
            return {
                "state": "authorized",
                "access_token": token,
                "expires_in": expires_in,
            }

        error = data.get("error", "")
        if error == "authorization_pending":
            return {"state": "authorization_pending"}
        if error == "slow_down":
            return {
                "state": "slow_down",
                "interval": data.get("interval", 10),
            }

        return {
            "state": "error",
            "error": error,
            "error_description": data.get("error_description", ""),
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
            "stream": False,
            **kwargs,
        }
        t0 = time.monotonic()
        data = await self._post("/chat/completions", payload)
        latency_ms = (time.monotonic() - t0) * 1000

        choice = data.get("choices", [{}])[0] if data.get("choices") else {}
        usage = data.get("usage", {})

        return CompletionResult(
            content=choice.get("message", {}).get("content", ""),
            model_id=data.get("model", model),
            provider="github-copilot",
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
        url = f"{_COPILOT_API_URL}/chat/completions"
        headers = await self._headers_async()
        try:
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
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    first_choice = choices[0]
                    if not isinstance(first_choice, dict):
                        continue
                    delta_payload = first_choice.get("delta", {})
                    if not isinstance(delta_payload, dict):
                        continue
                    delta = delta_payload.get("content", "")
                    if isinstance(delta, str) and delta:
                        yield delta
                return
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, IndexError):
            completion = await self.complete(prompt, model_id=model, **kwargs)
            if completion.content:
                yield completion.content

    async def list_models(self) -> list[ModelInfo]:
        discovered = await self._discover_models()
        if discovered:
            return discovered

        return [
            ModelInfo(
                model_id=m["id"],
                provider="github-copilot",
                context_window=m.get("context_window", 0),
            )
            for m in _KNOWN_MODELS
        ]

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            await self._get_token_async()
            # Lightweight completion to verify connectivity
            payload: dict[str, Any] = {
                "model": self._default_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            await self._post("/chat/completions", payload)
            latency_ms = (time.monotonic() - t0) * 1000
            models = await self.list_models()
            return HealthStatus(
                provider="github-copilot",
                available=True,
                latency_ms=latency_ms,
                models_available=len(models),
            )
        except (aiohttp.ClientError, TimeoutError, OSError, CopilotAuthError):
            return HealthStatus(provider="github-copilot", available=False)

    async def _discover_models(self) -> list[ModelInfo]:
        for path in _MODEL_DISCOVERY_PATHS:
            try:
                data = await self._get(path)
            except (aiohttp.ClientError, TimeoutError, OSError, CopilotAuthError):
                continue

            models = self._parse_models_payload(data)
            if models:
                return models

        return []

    def _parse_models_payload(self, data: dict[str, Any]) -> list[ModelInfo]:
        raw_models = data.get("data")
        if not isinstance(raw_models, list):
            raw_models = data.get("models")
        if not isinstance(raw_models, list):
            return []

        models: list[ModelInfo] = []
        seen: set[str] = set()
        for item in raw_models:
            if not isinstance(item, dict):
                continue

            model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            if not model_id or model_id in seen:
                continue

            context_window = item.get("context_window") or item.get("max_context_length") or 0
            try:
                parsed_context_window = int(context_window)
            except (TypeError, ValueError):
                parsed_context_window = 0

            models.append(
                ModelInfo(
                    model_id=model_id,
                    provider="github-copilot",
                    context_window=parsed_context_window,
                )
            )
            seen.add(model_id)

        return models

    # ------------------------------------------------------------------ #
    # Agentic completion (tool-calling support)
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

        Returns a ``litellm.ModelResponse``-compatible object so the
        agentic loop in ``chat_pipeline`` can process tool calls
        identically regardless of provider.
        """
        from litellm.types.utils import (
            Choices,
            Message,
            ModelResponse,
            Usage,
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            **kwargs,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = await self._post("/chat/completions", payload)

        # Parse into litellm ModelResponse so the agentic loop can use
        # .choices[0].message.tool_calls / .content / .model_dump().
        return ModelResponse(**data)

    # ------------------------------------------------------------------ #
    # HTTP with retry
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{_COPILOT_API_URL}{path}"
        headers = await self._headers_async()
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
        url = f"{_COPILOT_API_URL}{path}"
        headers = await self._headers_async()
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
