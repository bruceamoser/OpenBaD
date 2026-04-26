"""Tests for the OpenBaDChatModel LangChain wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from openbad.cognitive.model_router import (
    FallbackChain,
    ModelRouter,
    Priority,
    RouteStep,
)
from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ProviderAdapter,
)
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.frameworks.langchain_model import OpenBaDChatModel, _messages_to_prompt

# ── Helpers ───────────────────────────────────────────────────────────── #


class FakeAdapter(ProviderAdapter):
    """Minimal provider adapter for testing."""

    def __init__(self, content: str = "Hello!", tokens: int = 10) -> None:
        self._content = content
        self._tokens = tokens

    async def complete(
        self, prompt: str, model_id: str | None = None, **kw: Any,
    ) -> CompletionResult:
        return CompletionResult(
            content=self._content,
            model_id=model_id or "test-model",
            provider="fake",
            tokens_used=self._tokens,
            latency_ms=42.0,
        )

    async def stream(
        self, prompt: str, model_id: str | None = None, **kw: Any,
    ) -> AsyncIterator[str]:
        for token in self._content.split():
            yield token

    async def list_models(self) -> list:
        return []

    async def health_check(self) -> HealthStatus:
        return HealthStatus(provider="fake", available=True)


class UnhealthyAdapter(FakeAdapter):
    """Adapter that reports unhealthy."""

    async def health_check(self) -> HealthStatus:
        return HealthStatus(provider="unhealthy", available=False)


def _make_registry(*adapters: tuple[str, ProviderAdapter]) -> ProviderRegistry:
    """Build a ProviderRegistry with pre-registered adapters."""
    registry = ProviderRegistry()
    for name, adapter in adapters:
        registry.register(name, adapter)
    return registry


def _make_router(
    registry: ProviderRegistry,
    chains: dict[Priority, FallbackChain] | None = None,
    cortisol_threshold: float = 0.8,
) -> ModelRouter:
    return ModelRouter(
        registry=registry,
        chains=chains,
        cortisol_threshold=cortisol_threshold,
    )


def _make_model(
    router: ModelRouter,
    priority: int = Priority.MEDIUM,
    cortisol: float = 0.0,
    system_name: str = "test",
) -> OpenBaDChatModel:
    return OpenBaDChatModel(
        router=router,
        priority=priority,
        cortisol=cortisol,
        system_name=system_name,
    )


# ── Tests ─────────────────────────────────────────────────────────────── #


class TestMessageConversion:
    def test_flatten_messages(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        msgs = [
            SystemMessage(content="Be helpful."),
            HumanMessage(content="Hi"),
            AIMessage(content="Hello!"),
        ]
        prompt = _messages_to_prompt(msgs)
        assert "[system] Be helpful." in prompt
        assert "[user] Hi" in prompt
        assert "[assistant] Hello!" in prompt


class TestLLMType:
    def test_llm_type(self) -> None:
        registry = _make_registry(("fake", FakeAdapter()))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("fake", "m1"),)),
        })
        model = _make_model(router)
        assert model._llm_type == "openbad"


class TestRoutingDelegation:
    @pytest.mark.asyncio
    async def test_routes_to_provider(self) -> None:
        adapter = FakeAdapter(content="Routed response", tokens=15)
        registry = _make_registry(("primary", adapter))
        router = _make_router(registry, chains={
            Priority.HIGH: FallbackChain(steps=(RouteStep("primary", "gpt-4"),)),
        })
        model = _make_model(router, priority=Priority.HIGH)

        from langchain_core.messages import HumanMessage

        result = await model.ainvoke([HumanMessage(content="test")])
        assert result.content == "Routed response"

    @pytest.mark.asyncio
    async def test_priority_override_via_kwargs(self) -> None:
        """Priority can be overridden per-call via model kwargs."""
        fast_adapter = FakeAdapter(content="fast", tokens=5)
        slow_adapter = FakeAdapter(content="slow", tokens=50)
        registry = _make_registry(("fast", fast_adapter), ("slow", slow_adapter))
        router = _make_router(registry, chains={
            Priority.LOW: FallbackChain(steps=(RouteStep("fast", "small"),)),
            Priority.HIGH: FallbackChain(steps=(RouteStep("slow", "large"),)),
        })
        model = _make_model(router, priority=Priority.LOW)

        from langchain_core.messages import HumanMessage

        # Default priority is LOW → fast adapter
        result = await model.ainvoke([HumanMessage(content="q")])
        assert result.content == "fast"

        # Override to HIGH → slow adapter
        result = await model.ainvoke([HumanMessage(content="q")], priority=Priority.HIGH)
        assert result.content == "slow"


class TestCortisolDowngrade:
    @pytest.mark.asyncio
    async def test_cortisol_downgrades_priority(self) -> None:
        """High cortisol should downgrade routing to cheaper models."""
        cheap_adapter = FakeAdapter(content="cheap", tokens=3)
        expensive_adapter = FakeAdapter(content="expensive", tokens=100)
        registry = _make_registry(("cheap", cheap_adapter), ("expensive", expensive_adapter))
        router = _make_router(
            registry,
            chains={
                Priority.HIGH: FallbackChain(steps=(RouteStep("expensive", "big"),)),
                Priority.MEDIUM: FallbackChain(steps=(RouteStep("cheap", "small"),)),
            },
            cortisol_threshold=0.5,
        )

        # No cortisol → HIGH priority → expensive
        model = _make_model(router, priority=Priority.HIGH, cortisol=0.0)
        from langchain_core.messages import HumanMessage

        result = await model.ainvoke([HumanMessage(content="q")])
        assert result.content == "expensive"

        # High cortisol → downgraded to MEDIUM → cheap
        model = _make_model(router, priority=Priority.HIGH, cortisol=0.9)
        result = await model.ainvoke([HumanMessage(content="q")])
        assert result.content == "cheap"


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_falls_back_on_unhealthy(self) -> None:
        """When primary is unhealthy, falls back to next in chain."""
        unhealthy = UnhealthyAdapter(content="unreachable")
        fallback = FakeAdapter(content="fallback", tokens=8)
        registry = _make_registry(("primary", unhealthy), ("backup", fallback))
        router = _make_router(registry, chains={
            Priority.HIGH: FallbackChain(steps=(
                RouteStep("primary", "m1"),
                RouteStep("backup", "m2"),
            )),
        })
        model = _make_model(router, priority=Priority.HIGH)

        from langchain_core.messages import HumanMessage

        result = await model.ainvoke([HumanMessage(content="test")])
        assert result.content == "fallback"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_astream_yields_chunks(self) -> None:
        adapter = FakeAdapter(content="Hello world from OpenBaD", tokens=4)
        registry = _make_registry(("streamer", adapter))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("streamer", "m1"),)),
        })
        model = _make_model(router)

        from langchain_core.messages import HumanMessage

        chunks = []
        async for chunk in model.astream([HumanMessage(content="test")]):
            chunks.append(chunk.content)

        assert len(chunks) == 4
        assert "Hello" in chunks[0]
        assert "OpenBaD" in chunks[3]


class TestUsageRecording:
    @pytest.mark.asyncio
    async def test_records_usage(self) -> None:
        adapter = FakeAdapter(content="tracked", tokens=25)
        registry = _make_registry(("tracked", adapter))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("tracked", "m1"),)),
        })
        model = _make_model(router, system_name="test-system")

        from langchain_core.messages import HumanMessage

        with patch("openbad.frameworks.langchain_model.record_usage_event") as mock_record:
            await model.ainvoke([HumanMessage(content="test")])
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs["provider"] == "tracked"
            assert call_kwargs["tokens"] == 25
            assert call_kwargs["system"] == "test-system"


class TestIdentifyingParams:
    def test_identifying_params(self) -> None:
        registry = _make_registry(("fake", FakeAdapter()))
        router = _make_router(registry)
        model = _make_model(router, priority=Priority.HIGH, cortisol=0.5, system_name="chat")
        params = model._identifying_params
        assert params["priority"] == Priority.HIGH
        assert params["cortisol"] == 0.5
        assert params["system_name"] == "chat"


class TestGenerationInfo:
    @pytest.mark.asyncio
    async def test_generation_info_includes_routing(self) -> None:
        adapter = FakeAdapter(content="info-test", tokens=12)
        registry = _make_registry(("prov", adapter))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("prov", "m1"),)),
        })
        model = _make_model(router)

        from langchain_core.messages import HumanMessage

        result = await model._agenerate([HumanMessage(content="test")])
        gen_info = result.generations[0].generation_info
        assert gen_info["provider"] == "prov"
        assert gen_info["model_id"] == "m1"
        assert gen_info["tokens_used"] == 12
        assert gen_info["cortisol_downgrade"] is False
        assert gen_info["fallback_index"] == 0
