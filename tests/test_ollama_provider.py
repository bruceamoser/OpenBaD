"""Tests for OllamaProvider — all HTTP calls are mocked."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
)
from openbad.cognitive.providers.ollama import _DEFAULT_BASE_URL, OllamaProvider

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _json_response(data: dict[str, Any], status: int = 200) -> AsyncMock:
    """Create a mock aiohttp response returning *data* as JSON."""
    resp = AsyncMock()
    resp.status = status
    resp.raise_for_status = MagicMock(
        side_effect=None
        if status < 400
        else aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=status,
            message="error",
        )
    )
    resp.json = AsyncMock(return_value=data)
    # streaming
    resp.content = _async_iter_bytes(data)
    # context-manager support
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


async def _aiter_lines(lines: list[bytes]):
    for line in lines:
        yield line


def _async_iter_bytes(data: dict[str, Any]):
    """Return an async-iterable of bytes representing a single JSON chunk."""
    raw = json.dumps(data).encode() + b"\n"
    return _aiter_lines([raw])


def _make_session(resp: AsyncMock) -> AsyncMock:
    """Create a mock aiohttp.ClientSession that returns *resp* for any verb."""
    session = AsyncMock()
    session.post = MagicMock(return_value=resp)
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ------------------------------------------------------------------ #
# Tests — construction
# ------------------------------------------------------------------ #


class TestInit:
    def test_defaults(self) -> None:
        p = OllamaProvider()
        assert p._base_url == _DEFAULT_BASE_URL
        assert p._default_model == "llama3.2"
        assert p._max_retries == 2

    def test_custom(self) -> None:
        p = OllamaProvider(
            base_url="http://myhost:9999/",
            default_model="phi3",
            timeout_s=5,
            max_retries=0,
        )
        assert p._base_url == "http://myhost:9999"
        assert p._default_model == "phi3"
        assert p._max_retries == 0


# ------------------------------------------------------------------ #
# Tests — complete()
# ------------------------------------------------------------------ #


class TestComplete:
    @pytest.fixture()
    def provider(self) -> OllamaProvider:
        return OllamaProvider(max_retries=0)

    async def test_basic_complete(self, provider: OllamaProvider) -> None:
        body = {"response": "Hello!", "eval_count": 10, "done_reason": "stop"}
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("Say hi")
        assert isinstance(result, CompletionResult)
        assert result.content == "Hello!"
        assert result.model_id == "llama3.2"
        assert result.provider == "ollama"
        assert result.tokens_used == 10
        assert result.finish_reason == "stop"
        assert result.latency_ms > 0

    async def test_complete_custom_model(self, provider: OllamaProvider) -> None:
        body = {"response": "done", "eval_count": 1, "done_reason": "stop"}
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("test", model_id="phi3")
        assert result.model_id == "phi3"

    async def test_complete_missing_fields(self, provider: OllamaProvider) -> None:
        """Missing optional fields should produce safe defaults."""
        body: dict[str, Any] = {}
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("test")
        assert result.content == ""
        assert result.tokens_used == 0
        assert result.finish_reason == ""

    async def test_complete_server_error_raises(
        self, provider: OllamaProvider
    ) -> None:
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
    @pytest.fixture()
    def provider(self) -> OllamaProvider:
        return OllamaProvider(max_retries=0)

    async def test_stream_tokens(self, provider: OllamaProvider) -> None:
        lines = [
            json.dumps({"response": "Hello"}).encode() + b"\n",
            json.dumps({"response": " world"}).encode() + b"\n",
            json.dumps({"response": "", "done": True}).encode() + b"\n",
        ]
        resp = AsyncMock()
        resp.raise_for_status = MagicMock()
        resp.content = _aiter_lines(lines)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in provider.stream("Say hi")]
        assert chunks == ["Hello", " world"]

    async def test_stream_skips_blank_lines(self, provider: OllamaProvider) -> None:
        lines = [b"\n", json.dumps({"response": "ok"}).encode() + b"\n", b"  \n"]
        resp = AsyncMock()
        resp.raise_for_status = MagicMock()
        resp.content = _aiter_lines(lines)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in provider.stream("hi")]
        assert chunks == ["ok"]


# ------------------------------------------------------------------ #
# Tests — list_models()
# ------------------------------------------------------------------ #


class TestListModels:
    @pytest.fixture()
    def provider(self) -> OllamaProvider:
        return OllamaProvider(max_retries=0)

    async def test_list_models(self, provider: OllamaProvider) -> None:
        body = {
            "models": [
                {
                    "name": "llama3.2",
                    "details": {"context_length": 8192},
                },
                {
                    "name": "phi3",
                    "details": {},
                },
            ]
        }
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            models = await provider.list_models()
        assert len(models) == 2
        assert all(isinstance(m, ModelInfo) for m in models)
        assert models[0].model_id == "llama3.2"
        assert models[0].context_window == 8192
        assert models[1].context_window == 0

    async def test_list_models_empty(self, provider: OllamaProvider) -> None:
        resp = _json_response({"models": []})
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            models = await provider.list_models()
        assert models == []


# ------------------------------------------------------------------ #
# Tests — health_check()
# ------------------------------------------------------------------ #


class TestHealthCheck:
    @pytest.fixture()
    def provider(self) -> OllamaProvider:
        return OllamaProvider(max_retries=0)

    async def test_healthy(self, provider: OllamaProvider) -> None:
        body = {"models": [{"name": "llama3.2", "details": {}}]}
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            status = await provider.health_check()
        assert isinstance(status, HealthStatus)
        assert status.available is True
        assert status.models_available == 1
        assert status.latency_ms > 0

    async def test_unhealthy(self, provider: OllamaProvider) -> None:
        with patch(
            "aiohttp.ClientSession",
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("refused")
            ),
        ):
            status = await provider.health_check()
        assert status.available is False
        assert status.models_available == 0


# ------------------------------------------------------------------ #
# Tests — retry logic
# ------------------------------------------------------------------ #


class TestRetry:
    async def test_retries_transient_failure(self) -> None:
        """Should succeed on second attempt after a transient error."""
        p = OllamaProvider(max_retries=1)
        good_resp = _json_response(
            {"response": "ok", "eval_count": 1, "done_reason": "stop"}
        )
        bad_resp = _json_response({}, status=500)

        call_count = 0

        def _session_factory(*_a: Any, **_kw: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            return _make_session(bad_resp if call_count == 1 else good_resp)

        with (
            patch("aiohttp.ClientSession", side_effect=_session_factory),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await p.complete("retry me")
        assert result.content == "ok"
        assert call_count == 2

    async def test_exhausts_retries(self) -> None:
        """Should raise after all retries fail."""
        p = OllamaProvider(max_retries=1)
        bad_resp = _json_response({}, status=500)
        session = _make_session(bad_resp)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(aiohttp.ClientResponseError),
        ):
            await p.complete("fail forever")
