"""Tests for GitHubCopilotProvider — all HTTP and OAuth calls mocked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
)
from openbad.cognitive.providers.github_copilot import (
    CopilotAuthError,
    GitHubCopilotProvider,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

_CHAT_RESPONSE: dict[str, Any] = {
    "model": "gpt-4o",
    "choices": [
        {
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"total_tokens": 20},
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
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> GitHubCopilotProvider:
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "ghp-test-token")  # noqa: S106
    return GitHubCopilotProvider(max_retries=0, token_file=tmp_path / "token.json")


# ------------------------------------------------------------------ #
# Tests — token management
# ------------------------------------------------------------------ #


class TestTokenManagement:
    def test_env_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "ghp-env")  # noqa: S106
        p = GitHubCopilotProvider(token_file=tmp_path / "t.json")
        assert p._get_token() == "ghp-env"  # noqa: S105

    def test_missing_token_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        p = GitHubCopilotProvider(token_file=tmp_path / "t.json")
        with pytest.raises(CopilotAuthError, match="No Copilot token"):
            p._get_token()

    def test_save_and_load_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        token_file = tmp_path / "token.json"
        p = GitHubCopilotProvider(token_file=token_file)
        p._save_token("ghp-saved", 3600)  # noqa: S106
        loaded = p._load_token()
        assert loaded == "ghp-saved"  # noqa: S105

    def test_expired_token_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        token_file = tmp_path / "token.json"
        token_file.write_text(
            json.dumps({"access_token": "ghp-old", "expires_at": 0})
        )
        p = GitHubCopilotProvider(token_file=token_file)
        assert p._load_token() is None


# ------------------------------------------------------------------ #
# Tests — complete()
# ------------------------------------------------------------------ #


class TestComplete:
    async def test_basic(self, provider: GitHubCopilotProvider) -> None:
        resp = _json_response(_CHAT_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await provider.complete("Say hi")
        assert isinstance(result, CompletionResult)
        assert result.content == "Hello!"
        assert result.provider == "github-copilot"
        assert result.tokens_used == 20

    async def test_missing_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        p = GitHubCopilotProvider(token_file=tmp_path / "nope.json")
        with pytest.raises(CopilotAuthError):
            await p.complete("fail")

    async def test_server_error(self, provider: GitHubCopilotProvider) -> None:
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
    async def test_stream_tokens(self, provider: GitHubCopilotProvider) -> None:
        events = [
            'data: {"choices":[{"delta":{"content":"Hi"}}]}\n',
            'data: {"choices":[{"delta":{"content":"!"}}]}\n',
            "data: [DONE]\n",
        ]
        resp = _sse_response(events)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            chunks = [c async for c in provider.stream("hello")]
        assert chunks == ["Hi", "!"]

    async def test_stream_missing_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        p = GitHubCopilotProvider(token_file=tmp_path / "nope.json")
        with pytest.raises(CopilotAuthError):
            async for _ in p.stream("fail"):
                pass  # pragma: no cover


# ------------------------------------------------------------------ #
# Tests — list_models()
# ------------------------------------------------------------------ #


class TestListModels:
    async def test_known_models(self, provider: GitHubCopilotProvider) -> None:
        models = await provider.list_models()
        assert len(models) >= 4
        assert all(isinstance(m, ModelInfo) for m in models)
        ids = [m.model_id for m in models]
        assert "gpt-4o" in ids
        assert all(m.provider == "github-copilot" for m in models)


# ------------------------------------------------------------------ #
# Tests — health_check()
# ------------------------------------------------------------------ #


class TestHealthCheck:
    async def test_healthy(self, provider: GitHubCopilotProvider) -> None:
        resp = _json_response(_CHAT_RESPONSE)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            status = await provider.health_check()
        assert isinstance(status, HealthStatus)
        assert status.available is True

    async def test_unhealthy_no_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
        p = GitHubCopilotProvider(token_file=tmp_path / "nope.json")
        status = await p.health_check()
        assert status.available is False


# ------------------------------------------------------------------ #
# Tests — device flow (mocked)
# ------------------------------------------------------------------ #


class TestDeviceFlow:
    async def test_request_device_code(
        self, provider: GitHubCopilotProvider
    ) -> None:
        body = {
            "device_code": "dc-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        }
        resp = _json_response(body)
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            dc = await provider.request_device_code()
        assert dc.user_code == "ABCD-1234"
        assert dc.device_code == "dc-123"

    async def test_poll_success(self, provider: GitHubCopilotProvider) -> None:
        token_resp = _json_response(
            {"access_token": "ghp-new", "expires_in": 3600}
        )
        session = _make_session(token_resp)
        with patch("aiohttp.ClientSession", return_value=session):
            token = await provider.poll_for_token("dc-123", interval=0)
        assert token == "ghp-new"  # noqa: S105

    async def test_poll_auth_error(self, provider: GitHubCopilotProvider) -> None:
        error_resp = _json_response(
            {"error": "access_denied", "error_description": "User denied"}
        )
        session = _make_session(error_resp)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            pytest.raises(CopilotAuthError, match="access_denied"),
        ):
            await provider.poll_for_token("dc-123", interval=0)
