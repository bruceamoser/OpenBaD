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
    {"id": "gpt-4o", "context_window": 128_000},
    {"id": "gpt-4o-mini", "context_window": 128_000},
    {"id": "claude-sonnet-4-20250514", "context_window": 200_000},
    {"id": "claude-3-5-haiku-20241022", "context_window": 200_000},
]

_TOKEN_DIR = Path.home() / ".openbad"
_TOKEN_FILE = _TOKEN_DIR / "copilot_token.json"


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

    # ------------------------------------------------------------------ #
    # Token management
    # ------------------------------------------------------------------ #

    def _load_token(self) -> str | None:
        """Load token from disk."""
        if not self._token_file.exists():
            return None
        data = json.loads(self._token_file.read_text())
        expires_at = data.get("expires_at", 0)
        if time.time() >= expires_at:
            return None
        self._token_expires_at = expires_at
        return data.get("access_token", "")

    def _save_token(self, access_token: str, expires_in: int) -> None:
        """Save token to disk with restricted permissions."""
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in,
        }
        self._token_file.write_text(json.dumps(data))
        with contextlib.suppress(OSError):
            self._token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def _get_token(self) -> str:
        """Return a valid token or raise."""
        # Check env var first
        env_token = os.environ.get("GITHUB_COPILOT_TOKEN", "")
        if env_token:
            return env_token

        if self._cached_token and time.time() < self._token_expires_at:
            return self._cached_token

        token = self._load_token()
        if token:
            self._cached_token = token
            return token

        msg = (
            "No Copilot token available. Set GITHUB_COPILOT_TOKEN env var "
            "or run device-flow authentication."
        )
        raise CopilotAuthError(msg)

    def _headers(self) -> dict[str, str]:
        token = self._get_token()
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

        url = _GITHUB_OAUTH_TOKEN_URL
        payload = {
            "client_id": _COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            while True:
                async with session.post(
                    url,
                    data=payload,
                    headers={"Accept": "application/json"},
                ) as resp:
                    data = await resp.json(content_type=None)

                if "access_token" in data:
                    token = data["access_token"]
                    self._save_token(token, data.get("expires_in", 28800))
                    self._cached_token = token
                    self._token_expires_at = time.time() + data.get(
                        "expires_in", 28800
                    )
                    return token

                error = data.get("error", "")
                if error == "authorization_pending":
                    await asyncio.sleep(interval)
                    continue
                if error == "slow_down":
                    interval += 5
                    await asyncio.sleep(interval)
                    continue
                msg = f"OAuth error: {error} — {data.get('error_description', '')}"
                raise CopilotAuthError(msg)

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
            self._get_token()
            # Lightweight completion to verify connectivity
            payload: dict[str, Any] = {
                "model": self._default_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            await self._post("/chat/completions", payload)
            latency_ms = (time.monotonic() - t0) * 1000
            return HealthStatus(
                provider="github-copilot",
                available=True,
                latency_ms=latency_ms,
                models_available=len(_KNOWN_MODELS),
            )
        except (aiohttp.ClientError, TimeoutError, OSError, CopilotAuthError):
            return HealthStatus(provider="github-copilot", available=False)

    # ------------------------------------------------------------------ #
    # HTTP with retry
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{_COPILOT_API_URL}{path}"
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
