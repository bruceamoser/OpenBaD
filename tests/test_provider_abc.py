"""Tests for the cognitive provider ABC, shared types, and registry."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ModelInfo,
    ProviderAdapter,
    ProviderConfig,
)
from openbad.cognitive.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Concrete stub for ABC contract tests
# ---------------------------------------------------------------------------


class _StubProvider(ProviderAdapter):
    """Minimal concrete implementation for testing."""

    async def complete(
        self, prompt: str, model_id: str | None = None, **kwargs: Any
    ) -> CompletionResult:
        return CompletionResult(
            content="stub", model_id=model_id or "m", provider="stub"
        )

    async def stream(
        self, prompt: str, model_id: str | None = None, **kwargs: Any
    ) -> AsyncIterator[str]:
        yield "chunk"

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(model_id="m", provider="stub")]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(provider="stub", available=True)


# ---------------------------------------------------------------------------
# CompletionResult
# ---------------------------------------------------------------------------


class TestCompletionResult:
    def test_fields(self) -> None:
        r = CompletionResult("hi", "m1", "ollama", tokens_used=10, latency_ms=5.0)
        assert r.content == "hi"
        assert r.model_id == "m1"
        assert r.tokens_used == 10
        assert r.finish_reason == ""

    def test_frozen(self) -> None:
        r = CompletionResult("x", "m", "p")
        with pytest.raises(AttributeError):
            r.content = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------


class TestModelInfo:
    def test_defaults(self) -> None:
        m = ModelInfo(model_id="llama3", provider="ollama")
        assert m.context_window == 0
        assert m.capabilities == []

    def test_capabilities(self) -> None:
        m = ModelInfo("m", "p", capabilities=["chat", "code"])
        assert "chat" in m.capabilities


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatus:
    def test_available(self) -> None:
        h = HealthStatus("ollama", True, latency_ms=12.5, models_available=3)
        assert h.available is True
        assert h.models_available == 3


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
    def test_defaults(self) -> None:
        c = ProviderConfig(name="ollama")
        assert c.timeout_ms == 30_000
        assert c.max_retries == 2

    def test_custom(self) -> None:
        c = ProviderConfig("openai", base_url="https://api.openai.com", max_retries=5)
        assert c.max_retries == 5


# ---------------------------------------------------------------------------
# ABC contract enforcement
# ---------------------------------------------------------------------------


class TestProviderAdapterABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            ProviderAdapter()  # type: ignore[abstract]

    def test_incomplete_subclass_fails(self) -> None:
        class _Incomplete(ProviderAdapter):
            async def complete(self, prompt, model_id=None, **kw):
                return CompletionResult("", "", "")

        with pytest.raises(TypeError):
            _Incomplete()  # type: ignore[abstract]

    async def test_stub_complete(self) -> None:
        p = _StubProvider()
        r = await p.complete("hello")
        assert r.content == "stub"

    async def test_stub_stream(self) -> None:
        p = _StubProvider()
        chunks = [c async for c in p.stream("hello")]
        assert chunks == ["chunk"]

    async def test_stub_list_models(self) -> None:
        p = _StubProvider()
        models = await p.list_models()
        assert len(models) == 1

    async def test_stub_health_check(self) -> None:
        p = _StubProvider()
        h = await p.health_check()
        assert h.available is True


# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        reg = ProviderRegistry()
        adapter = _StubProvider()
        reg.register("stub", adapter)
        assert reg.get("stub") is adapter

    def test_get_missing(self) -> None:
        reg = ProviderRegistry()
        assert reg.get("nonexistent") is None

    def test_list_providers(self) -> None:
        reg = ProviderRegistry()
        reg.register("b_provider", _StubProvider())
        reg.register("a_provider", _StubProvider())
        assert reg.list_providers() == ["a_provider", "b_provider"]

    def test_unregister(self) -> None:
        reg = ProviderRegistry()
        reg.register("x", _StubProvider())
        assert reg.unregister("x") is True
        assert reg.get("x") is None

    def test_unregister_missing(self) -> None:
        reg = ProviderRegistry()
        assert reg.unregister("nope") is False


# ---------------------------------------------------------------------------
# resolve() — provider/model notation
# ---------------------------------------------------------------------------


class TestResolve:
    def test_valid_notation(self) -> None:
        reg = ProviderRegistry()
        adapter = _StubProvider()
        reg.register("ollama", adapter)
        resolved_adapter, model_id = reg.resolve("ollama/llama3.3")
        assert resolved_adapter is adapter
        assert model_id == "llama3.3"

    def test_model_with_slashes(self) -> None:
        reg = ProviderRegistry()
        reg.register("hf", _StubProvider())
        adapter, model_id = reg.resolve("hf/meta-llama/Llama-3-8B")
        assert model_id == "meta-llama/Llama-3-8B"

    def test_missing_provider(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.resolve("unknown/model")

    def test_invalid_notation_no_slash(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="Invalid"):
            reg.resolve("just-a-model")

    def test_overwrite_provider(self) -> None:
        reg = ProviderRegistry()
        a1 = _StubProvider()
        a2 = _StubProvider()
        reg.register("x", a1)
        reg.register("x", a2)
        assert reg.get("x") is a2
