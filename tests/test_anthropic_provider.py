"""Tests for AnthropicProvider — all HTTP calls mocked."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openbad.cognitive.providers.anthropic import (
    AnthropicKeyMissingError,
    AnthropicProvider,
)
from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

_MESSAGES_RESPONSE: dict[str, Any] = {
    "model": "claude-sonnet-4-20250514",
    "content": [{"type": "text", "text": "Hello!"}],
    "usage": {"input_tokens": 5, "output_tokens": 10},
    "stop_reason": "end_turn",
}


def _json_response(data: dict[str, Any], status: int = 200) -> AsyncMock:
    resp = AsyncMock()
    resp.status = status
    resp.raise_for_status = MagicMock(
        side_effect=None
        if status < 400
        else aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=status, message="err",
        )
    )
    resp.json = AsyncMock(return_value=data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _sse_response(events: list[str]) -> AsyncMock:
    async def _aiter():
        for ev in events:
            yield ev.encode()
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.content = _aiter()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(resp: AsyncMock) -> AsyncMock:
    session = AsyncMock()
    session.post = MagicMock(return_value=resp)
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> AnthropicProvider:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # noqa: S106
    return AnthropicProvider(max_retries=0)


# ------------------------------------------------------------------ #
# Tests — init and key resolution
# ------------------------------------------------------------------ #


class TestInit:
    def test_defaults(self, provider: AnthropicProvider) -> None:
        assert provider._default_model == "claude-sonnet-4-20250514"
        assert "anthropic.com" in provider._base_url

    def test_missing_key_raises(self) -> None:
        p = AnthropicProvider(api_key_env="MISSING_ANT_KEY_XYZ")
        with pytest.raises(AnthropicKeyMissingError, match="MISSING_ANT_KEY_XYZ"):
            p._resolve_api_key()

    def test_headers_include_version(self, provider: AnthropicProvider) -> None:
        h = provider._headers()
        assert h["anthropic-version"] == "2023-06-01"
        assert h["x-api-key"] == "sk-ant-test"  # noqa: S105


# ------------------------------------------------------------------ #
# Tests — complete()
# ------------------------------------------------------------------ #


class TestComplete:
    async def test_basic(self, provider: AnthropicProvider) -> None:
        resp = _json_response(_MESSAGES_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("Say hi")
        assert isinstance(result, CompletionResult)
        assert result.content == "Hello!"
        assert result.provider == "anthropic"
        assert result.tokens_used == 15
        assert result.finish_reason == "end_turn"
        assert result.latency_ms > 0

    async def test_custom_model(self, provider: AnthropicProvider) -> None:
        resp = _json_response(_MESSAGES_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("test", model_id="claude-3-5-haiku-20241022")
        assert result.model_id == "claude-sonnet-4-20250514"  # from response

    async def test_empty_content(self, provider: AnthropicProvider) -> None:
        body: dict[str, Any] = {"content": [], "usage": {}, "stop_reason": ""}
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("test")
        assert result.content == ""
        assert result.tokens_used == 0

    async def test_missing_key(self) -> None:
        p = AnthropicProvider(api_key_env="NO_KEY_HERE_123")
        with pytest.raises(AnthropicKeyMissingError):
            await p.complete("fail")

    async def test_server_error(self, provider: AnthropicProvider) -> None:
        resp = _json_response({}, status=500)
        session = _make_session(resp)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            pytest.raises(aiohttp.ClientResponseError),
        ):
            await provider.complete("fail")


# ------------------------------------------------------------------ #
# Tests — stream()
# ------------------------------------------------------------------ #


class TestStream:
    async def test_stream_content_block_delta(
        self, provider: AnthropicProvider
    ) -> None:
        events = [
            'data: {"type": "content_block_start"}\n',
            'data: {"type": "content_block_delta", "delta": {"text": "Hello"}}\n',
            'data: {"type": "content_block_delta", "delta": {"text": " world"}}\n',
            'data: {"type": "message_stop"}\n',
        ]
        resp = _sse_response(events)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in provider.stream("hi")]
        assert chunks == ["Hello", " world"]

    async def test_stream_skips_non_data(self, provider: AnthropicProvider) -> None:
        events = [
            "event: ping\n",
            "\n",
            'data: {"type": "content_block_delta", "delta": {"text": "ok"}}\n',
        ]
        resp = _sse_response(events)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in provider.stream("hi")]
        assert chunks == ["ok"]

    async def test_stream_missing_key(self) -> None:
        p = AnthropicProvider(api_key_env="NO_KEY_STREAM_ANT")
        with pytest.raises(AnthropicKeyMissingError):
            async for _ in p.stream("fail"):
                pass  # pragma: no cover


# ------------------------------------------------------------------ #
# Tests — list_models()
# ------------------------------------------------------------------ #


class TestListModels:
    async def test_known_models(self, provider: AnthropicProvider) -> None:
        models = await provider.list_models()
        assert len(models) >= 4
        assert all(isinstance(m, ModelInfo) for m in models)
        ids = [m.model_id for m in models]
        assert "claude-sonnet-4-20250514" in ids
        assert all(m.provider == "anthropic" for m in models)


# ------------------------------------------------------------------ #
# Tests — health_check()
# ------------------------------------------------------------------ #


class TestHealthCheck:
    async def test_healthy(self, provider: AnthropicProvider) -> None:
        resp = _json_response(_MESSAGES_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            status = await provider.health_check()
        assert isinstance(status, HealthStatus)
        assert status.available is True
        assert status.models_available >= 4

    async def test_unhealthy_network(self, provider: AnthropicProvider) -> None:
        with patch(
            "aiohttp.ClientSession",
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("refused"),
            ),
        ):
            status = await provider.health_check()
        assert status.available is False

    async def test_unhealthy_missing_key(self) -> None:
        p = AnthropicProvider(api_key_env="MISSING_ANT_HEALTH")
        status = await p.health_check()
        assert status.available is False
