"""Tests for OpenAI-compatible provider adapters — all HTTP mocked."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
)
from openbad.cognitive.providers.openai_compat import (
    OpenAICompatProvider,
    ProviderUnavailableError,
    custom_provider,
    groq_provider,
    mistral_provider,
    openai_codex_provider,
    openai_provider,
    openrouter_provider,
    xai_provider,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

_CHAT_RESPONSE: dict[str, Any] = {
    "model": "gpt-4o-mini",
    "choices": [
        {
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"total_tokens": 15},
}

_MODELS_RESPONSE: dict[str, Any] = {
    "data": [
        {"id": "gpt-4o-mini"},
        {"id": "gpt-4o"},
    ]
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
    """Mock streaming SSE response."""
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


def _provider(**kw: Any) -> OpenAICompatProvider:
    """Create a test provider with a fake key env var."""
    defaults: dict[str, Any] = {
        "provider_name": "test",
        "base_url": "https://api.test.com",
        "api_key_env": "",
        "default_model": "test-model",
        "max_retries": 0,
    }
    defaults.update(kw)
    return OpenAICompatProvider(**defaults)


# ------------------------------------------------------------------ #
# Tests — construction & API key resolution
# ------------------------------------------------------------------ #


class TestInit:
    def test_defaults(self) -> None:
        p = _provider()
        assert p._provider_name == "test"
        assert p._base_url == "https://api.test.com"

    def test_missing_api_key_raises(self) -> None:
        p = _provider(api_key_env="MISSING_KEY_12345")
        with pytest.raises(ProviderUnavailableError, match="MISSING_KEY_12345"):
            p._resolve_api_key()

    def test_api_key_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_KEY_ABC", "sk-test")  # noqa: S106
        p = _provider(api_key_env="TEST_KEY_ABC")
        assert p._resolve_api_key() == "sk-test"  # noqa: S105

    def test_no_key_env_returns_empty(self) -> None:
        p = _provider(api_key_env="")
        assert p._resolve_api_key() == ""


# ------------------------------------------------------------------ #
# Tests — complete()
# ------------------------------------------------------------------ #


class TestComplete:
    async def test_basic_complete(self) -> None:
        p = _provider()
        resp = _json_response(_CHAT_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await p.complete("Say hi")
        assert isinstance(result, CompletionResult)
        assert result.content == "Hello!"
        assert result.provider == "test"
        assert result.tokens_used == 15
        assert result.finish_reason == "stop"
        assert result.latency_ms > 0

    async def test_complete_custom_model(self) -> None:
        p = _provider()
        resp = _json_response(_CHAT_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await p.complete("test", model_id="custom-model")
        # The response model comes from server JSON
        assert result.model_id == "gpt-4o-mini"

    async def test_complete_empty_choices(self) -> None:
        body: dict[str, Any] = {"choices": [], "usage": {}}
        resp = _json_response(body)
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            result = await p.complete("test")
        assert result.content == ""
        assert result.tokens_used == 0

    async def test_complete_missing_key_raises(self) -> None:
        p = _provider(api_key_env="NO_SUCH_KEY_99")
        with pytest.raises(ProviderUnavailableError):
            await p.complete("test")

    async def test_complete_server_error(self) -> None:
        p = _provider()
        resp = _json_response({}, status=500)
        session = _make_session(resp)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            pytest.raises(aiohttp.ClientResponseError),
        ):
            await p.complete("fail")


# ------------------------------------------------------------------ #
# Tests — stream()
# ------------------------------------------------------------------ #


class TestStream:
    async def test_stream_tokens(self) -> None:
        events = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            "data: [DONE]\n",
        ]
        resp = _sse_response(events)
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in p.stream("hi")]
        assert chunks == ["Hello", " world"]

    async def test_stream_skips_non_data_lines(self) -> None:
        events = [
            ": keep-alive\n",
            "\n",
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            "data: [DONE]\n",
        ]
        resp = _sse_response(events)
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in p.stream("hi")]
        assert chunks == ["ok"]

    async def test_stream_missing_key(self) -> None:
        p = _provider(api_key_env="MISSING_KEY_STREAM")
        with pytest.raises(ProviderUnavailableError):
            async for _ in p.stream("fail"):
                pass  # pragma: no cover


# ------------------------------------------------------------------ #
# Tests — list_models()
# ------------------------------------------------------------------ #


class TestListModels:
    async def test_list(self) -> None:
        resp = _json_response(_MODELS_RESPONSE)
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            models = await p.list_models()
        assert len(models) == 2
        assert all(isinstance(m, ModelInfo) for m in models)
        assert models[0].model_id == "gpt-4o-mini"

    async def test_list_empty(self) -> None:
        resp = _json_response({"data": []})
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            models = await p.list_models()
        assert models == []


# ------------------------------------------------------------------ #
# Tests — health_check()
# ------------------------------------------------------------------ #


class TestHealthCheck:
    async def test_healthy(self) -> None:
        resp = _json_response(_MODELS_RESPONSE)
        session = _make_session(resp)
        p = _provider()
        with patch("aiohttp.ClientSession", return_value=session):
            status = await p.health_check()
        assert isinstance(status, HealthStatus)
        assert status.available is True
        assert status.models_available == 2

    async def test_unhealthy_network(self) -> None:
        p = _provider()
        with patch(
            "aiohttp.ClientSession",
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("refused"),
            ),
        ):
            status = await p.health_check()
        assert status.available is False

    async def test_unhealthy_missing_key(self) -> None:
        """Missing key → ProviderUnavailable → health reports unavailable."""
        p = _provider(api_key_env="MISSING_KEY_HEALTH")
        status = await p.health_check()
        assert status.available is False


# ------------------------------------------------------------------ #
# Tests — factory functions
# ------------------------------------------------------------------ #


class TestFactories:
    def test_openai(self) -> None:
        p = openai_provider()
        assert p._provider_name == "openai"
        assert "openai.com" in p._base_url
        assert p._api_key_env == "OPENAI_API_KEY"

    def test_openai_codex(self) -> None:
        p = openai_codex_provider()
        assert p._provider_name == "openai-codex"
        assert p._default_model == "codex"

    def test_openrouter(self) -> None:
        p = openrouter_provider()
        assert p._provider_name == "openrouter"
        assert "openrouter.ai" in p._base_url

    def test_groq(self) -> None:
        p = groq_provider()
        assert p._provider_name == "groq"
        assert "groq.com" in p._base_url

    def test_xai(self) -> None:
        p = xai_provider()
        assert p._provider_name == "xai"
        assert "x.ai" in p._base_url

    def test_mistral(self) -> None:
        p = mistral_provider()
        assert p._provider_name == "mistral"
        assert "mistral.ai" in p._base_url

    def test_custom(self) -> None:
        p = custom_provider(base_url="http://myhost:8080")
        assert p._provider_name == "custom"
        assert p._base_url == "http://myhost:8080"

    def test_factory_override(self) -> None:
        p = openai_provider(default_model="gpt-4o", max_retries=5)
        assert p._default_model == "gpt-4o"
        assert p._max_retries == 5
