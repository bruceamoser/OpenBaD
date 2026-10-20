"""Tests for the OpenBaDChatModel LangChain wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
)
from langchain_core.outputs import ChatGeneration, LLMResult

from openbad.cognitive.model_router import (
    FallbackChain,
    ModelRouter,
    Priority,
    RouteStep,
)
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.frameworks.langchain_model import OpenBaDChatModel

# ── Helpers ───────────────────────────────────────────────────────────── #


def _fake_chat_model(content: str = "Hello!", tokens: int = 10) -> MagicMock:
    """Build a mock BaseChatModel that returns canned results."""
    mock = AsyncMock()
    # agenerate returns LLMResult with generations
    llm_result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=content))]],
        llm_output={"token_usage": {"total_tokens": tokens}},
    )
    mock.agenerate = AsyncMock(return_value=llm_result)
    # astream yields AIMessageChunk objects
    async def _astream(messages, **kw):
        for word in content.split():
            yield AIMessageChunk(content=word)
    mock.astream = _astream
    return mock


def _make_registry(*models: tuple[str, MagicMock]) -> ProviderRegistry:
    """Build a ProviderRegistry with pre-registered models."""
    registry = ProviderRegistry()
    for provider_model, chat_model in models:
        registry.register_models(provider_model, chat_model, MagicMock())
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


class TestLLMType:
    def test_llm_type(self) -> None:
        registry = _make_registry(("fake/m1", _fake_chat_model()))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("fake", "m1"),)),
        })
        model = _make_model(router)
        assert model._llm_type == "openbad"


class TestRoutingDelegation:
    @pytest.mark.asyncio
    async def test_routes_to_provider(self) -> None:
        cm = _fake_chat_model(content="Routed response", tokens=15)
        registry = _make_registry(("primary/gpt-4", cm))
        router = _make_router(registry, chains={
            Priority.HIGH: FallbackChain(steps=(RouteStep("primary", "gpt-4"),)),
        })
        model = _make_model(router, priority=Priority.HIGH)

        result = await model.ainvoke([HumanMessage(content="test")])
        assert result.content == "Routed response"

    @pytest.mark.asyncio
    async def test_priority_override_via_kwargs(self) -> None:
        """Priority can be overridden per-call via model kwargs."""
        fast_cm = _fake_chat_model(content="fast", tokens=5)
        slow_cm = _fake_chat_model(content="slow", tokens=50)
        registry = _make_registry(("fast/small", fast_cm), ("slow/large", slow_cm))
        router = _make_router(registry, chains={
            Priority.LOW: FallbackChain(steps=(RouteStep("fast", "small"),)),
            Priority.HIGH: FallbackChain(steps=(RouteStep("slow", "large"),)),
        })
        model = _make_model(router, priority=Priority.LOW)

        # Default priority is LOW → fast model
        result = await model.ainvoke([HumanMessage(content="q")])
        assert result.content == "fast"

        # Override to HIGH → slow model
        result = await model.ainvoke([HumanMessage(content="q")], priority=Priority.HIGH)
        assert result.content == "slow"


class TestCortisolDowngrade:
    @pytest.mark.asyncio
    async def test_cortisol_downgrades_priority(self) -> None:
        """High cortisol should downgrade routing to cheaper models."""
        cheap_cm = _fake_chat_model(content="cheap", tokens=3)
        expensive_cm = _fake_chat_model(content="expensive", tokens=100)
        registry = _make_registry(("cheap/small", cheap_cm), ("expensive/big", expensive_cm))
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
        primary_cm = _fake_chat_model(content="unreachable")
        fallback_cm = _fake_chat_model(content="fallback", tokens=8)
        registry = _make_registry(("primary/m1", primary_cm), ("backup/m2", fallback_cm))
        router = _make_router(registry, chains={
            Priority.HIGH: FallbackChain(steps=(
                RouteStep("primary", "m1"),
                RouteStep("backup", "m2"),
            )),
        })
        router.mark_unhealthy("primary")
        model = _make_model(router, priority=Priority.HIGH)

        result = await model.ainvoke([HumanMessage(content="test")])
        assert result.content == "fallback"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_astream_yields_chunks(self) -> None:
        cm = _fake_chat_model(content="Hello world from OpenBaD", tokens=4)
        registry = _make_registry(("streamer/m1", cm))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("streamer", "m1"),)),
        })
        model = _make_model(router)

        chunks = []
        async for chunk in model.astream([HumanMessage(content="test")]):
            chunks.append(chunk.content)

        assert len(chunks) == 4
        assert "Hello" in chunks[0]
        assert "OpenBaD" in chunks[3]


class TestUsageRecording:
    @pytest.mark.asyncio
    async def test_records_usage(self) -> None:
        cm = _fake_chat_model(content="tracked", tokens=25)
        registry = _make_registry(("tracked/m1", cm))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("tracked", "m1"),)),
        })
        model = _make_model(router, system_name="test-system")

        with patch("openbad.frameworks.langchain_model.record_usage_event") as mock_record:
            await model.ainvoke([HumanMessage(content="test")])
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args.kwargs
            assert call_kwargs["provider"] == "tracked"
            assert call_kwargs["tokens"] == 25
            assert call_kwargs["system"] == "test-system"


class TestIdentifyingParams:
    def test_identifying_params(self) -> None:
        registry = _make_registry(("fake/m1", _fake_chat_model()))
        router = _make_router(registry)
        model = _make_model(router, priority=Priority.HIGH, cortisol=0.5, system_name="chat")
        params = model._identifying_params
        assert params["priority"] == Priority.HIGH
        assert params["cortisol"] == 0.5
        assert params["system_name"] == "chat"


class TestGenerationInfo:
    @pytest.mark.asyncio
    async def test_generation_info_includes_routing(self) -> None:
        cm = _fake_chat_model(content="info-test", tokens=12)
        registry = _make_registry(("prov/m1", cm))
        router = _make_router(registry, chains={
            Priority.MEDIUM: FallbackChain(steps=(RouteStep("prov", "m1"),)),
        })
        model = _make_model(router)

        result = await model._agenerate([HumanMessage(content="test")])
        gen_info = result.generations[0].generation_info
        assert gen_info["provider"] == "prov"
        assert gen_info["model_id"] == "m1"
        assert gen_info["tokens_used"] == 12
        assert gen_info["cortisol_downgrade"] is False
        assert gen_info["fallback_index"] == 0
