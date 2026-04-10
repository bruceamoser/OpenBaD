"""Tests for CognitiveEventLoop and CognitiveOrchestrator."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.context_manager import (
    CompressedContext,
    CompressionStrategy,
    ContextBudget,
    ContextWindowManager,
)
from openbad.cognitive.event_loop import (
    CognitiveEventLoop,
    CognitiveRequest,
    CognitiveResponse,
    _parse_system,
)
from openbad.cognitive.model_router import (
    FallbackChain,
    ModelRouter,
    Priority,
    RouteStep,
    RoutingDecision,
)
from openbad.cognitive.orchestrator import CognitiveOrchestrator
from openbad.cognitive.providers.base import CompletionResult, HealthStatus, ProviderAdapter
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStrategy

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _mock_router() -> ModelRouter:
    adapter = AsyncMock()
    adapter.complete = AsyncMock(
        return_value=CompletionResult(
            content="answer-42",
            model_id="llama3.2",
            provider="ollama",
            tokens_used=50,
        )
    )
    adapter.health_check = AsyncMock(
        return_value=HealthStatus(
            provider="ollama", available=True, latency_ms=10,
        )
    )
    decision = RoutingDecision(
        priority=Priority.MEDIUM,
        provider="ollama",
        model_id="llama3.2",
        fallback_index=0,
    )
    router = MagicMock(spec=ModelRouter)
    router.route = AsyncMock(return_value=(adapter, "llama3.2", decision))
    router.record_latency = MagicMock()
    return router


def _mock_adapter(
    *,
    provider: str,
    content: str = "answer-42",
    model_id: str = "llama3.2",
    healthy: bool = True,
) -> ProviderAdapter:
    adapter = AsyncMock(spec=ProviderAdapter)
    adapter.complete = AsyncMock(
        return_value=CompletionResult(
            content=content,
            model_id=model_id,
            provider=provider,
            tokens_used=50,
        )
    )
    adapter.health_check = AsyncMock(
        return_value=HealthStatus(
            provider=provider,
            available=healthy,
            latency_ms=10,
        )
    )
    return adapter


def _mock_ctx() -> ContextWindowManager:
    ctx = MagicMock(spec=ContextWindowManager)
    ctx.allocate = MagicMock(
        return_value=ContextBudget(
            max_tokens=8192,
            system_tokens=1200,
            context_tokens=4900,
            response_tokens=2048,
        )
    )
    ctx.compress = MagicMock(
        return_value=CompressedContext(
            text="compressed ctx",
            original_tokens=100,
            compressed_tokens=100,
            strategy=CompressionStrategy.TRUNCATE,
        )
    )
    ctx.track_usage = MagicMock()
    return ctx


def _mock_strategy() -> ReasoningStrategy:
    strategy = AsyncMock(spec=ReasoningStrategy)
    strategy.reason = AsyncMock(
        return_value=ReasoningResult(
            final_answer="cot-answer", total_tokens=75,
        )
    )
    return strategy


def _event_loop(
    strategies: dict[Priority | CognitiveSystem, ReasoningStrategy] | None = None,
    publish_fn: AsyncMock | None = None,
    validate_fn: MagicMock | None = None,
) -> CognitiveEventLoop:
    return CognitiveEventLoop(
        model_router=_mock_router(),
        context_manager=_mock_ctx(),
        strategies=strategies or {},
        publish_fn=publish_fn,
        validate_fn=validate_fn,
    )


# ------------------------------------------------------------------ #
# Tests — basic processing
# ------------------------------------------------------------------ #


class TestHandleRequest:
    async def test_direct_call(self) -> None:
        loop = _event_loop()
        req = CognitiveRequest(
            request_id="r1", prompt="hello", priority=Priority.LOW,
        )
        resp = await loop.handle_request(req)
        assert isinstance(resp, CognitiveResponse)
        assert resp.answer == "answer-42"
        assert resp.strategy == "direct"
        assert resp.tokens_used == 50
        assert resp.request_id == "r1"

    async def test_request_defaults_to_chat_system(self) -> None:
        req = CognitiveRequest(request_id="r0", prompt="hello")
        assert req.system is CognitiveSystem.CHAT

    async def test_strategy_used(self) -> None:
        strategies = {Priority.MEDIUM: _mock_strategy()}
        loop = _event_loop(strategies=strategies)
        req = CognitiveRequest(
            request_id="r2", prompt="think", priority=Priority.MEDIUM,
        )
        resp = await loop.handle_request(req)
        assert resp.answer == "cot-answer"
        assert resp.tokens_used == 75

    async def test_system_strategy_overrides_priority_strategy(self) -> None:
        system_strategy = _mock_strategy()
        priority_strategy = _mock_strategy()
        loop = _event_loop(
            strategies={
                CognitiveSystem.REACTIONS: system_strategy,
                Priority.HIGH: priority_strategy,
            }
        )
        req = CognitiveRequest(
            request_id="r2b",
            prompt="react",
            priority=Priority.HIGH,
            system=CognitiveSystem.REACTIONS,
        )

        resp = await loop.handle_request(req)

        assert resp.answer == "cot-answer"
        system_strategy.reason.assert_awaited_once()
        priority_strategy.reason.assert_not_awaited()

    async def test_routes_with_system_assignment(self) -> None:
        loop = _event_loop()
        req = CognitiveRequest(
            request_id="r2c",
            prompt="think",
            priority=Priority.HIGH,
            system=CognitiveSystem.REASONING,
        )

        await loop.handle_request(req)

        loop._router.route.assert_awaited_once_with(  # noqa: SLF001
            Priority.HIGH,
            system=CognitiveSystem.REASONING,
            cortisol=0.0,
        )

    async def test_publishes_result(self) -> None:
        pub = AsyncMock()
        loop = _event_loop(publish_fn=pub)
        req = CognitiveRequest(request_id="r3", prompt="hi")
        await loop.handle_request(req)
        pub.assert_awaited_once()
        topic, payload = pub.call_args[0]
        assert topic == "agent/cognitive/response"
        assert payload["request_id"] == "r3"


# ------------------------------------------------------------------ #
# Tests — validation
# ------------------------------------------------------------------ #


class TestValidation:
    async def test_rejected_by_immune(self) -> None:
        validate = MagicMock(return_value=False)
        loop = _event_loop(validate_fn=validate)
        req = CognitiveRequest(request_id="r4", prompt="bad")
        resp = await loop.handle_request(req)
        assert resp.error == "Request rejected by immune system"
        assert resp.answer == ""

    async def test_accepted_by_immune(self) -> None:
        validate = MagicMock(return_value=True)
        loop = _event_loop(validate_fn=validate)
        req = CognitiveRequest(request_id="r5", prompt="ok")
        resp = await loop.handle_request(req)
        assert resp.answer == "answer-42"


# ------------------------------------------------------------------ #
# Tests — timeout
# ------------------------------------------------------------------ #


class TestTimeout:
    async def test_timeout_enforced(self) -> None:
        async def slow_complete(*_a, **_kw):
            await asyncio.sleep(10)
            return CompletionResult(
                content="late", model_id="m", provider="p", tokens_used=0,
            )

        router = _mock_router()
        adapter = (await router.route(Priority.LOW))[0]
        adapter.complete = slow_complete

        loop = CognitiveEventLoop(
            model_router=router,
            context_manager=_mock_ctx(),
            strategies={},
        )
        req = CognitiveRequest(
            request_id="t1", prompt="slow", priority=Priority.LOW,
        )
        resp = await loop.handle_request(req)
        assert resp.timed_out is True
        assert "Timed out" in resp.error


# ------------------------------------------------------------------ #
# Tests — MQTT message handler
# ------------------------------------------------------------------ #


class TestOnMessage:
    async def test_valid_message(self) -> None:
        loop = _event_loop()
        await loop.start()
        payload = json.dumps({
            "request_id": "m1",
            "prompt": "test",
            "priority": 2,
        }).encode()
        await loop.on_message("agent/cognitive/request", payload)
        # Give time for task
        await asyncio.sleep(0.1)
        await loop.stop()

    async def test_message_defaults_system_to_chat(self) -> None:
        loop = _event_loop()
        loop.submit = MagicMock()

        payload = json.dumps({
            "request_id": "m-chat",
            "prompt": "test",
            "priority": 2,
        }).encode()
        await loop.on_message("agent/cognitive/request", payload)

        submitted = loop.submit.call_args.args[0]
        assert submitted.system is CognitiveSystem.CHAT

    async def test_message_parses_explicit_system(self) -> None:
        loop = _event_loop()
        loop.submit = MagicMock()

        payload = json.dumps({
            "request_id": "m-reason",
            "prompt": "test",
            "priority": 3,
            "system": "reasoning",
        }).encode()
        await loop.on_message("agent/cognitive/request", payload)

        submitted = loop.submit.call_args.args[0]
        assert submitted.system is CognitiveSystem.REASONING

    async def test_invalid_json(self) -> None:
        loop = _event_loop()
        await loop.on_message("agent/cognitive/request", b"not json")
        # Should not raise


# ------------------------------------------------------------------ #
# Tests — submit (fire-and-forget)
# ------------------------------------------------------------------ #


class TestSubmit:
    async def test_submit_processes(self) -> None:
        loop = _event_loop()
        await loop.start()
        req = CognitiveRequest(request_id="s1", prompt="async")
        loop.submit(req)
        await asyncio.sleep(0.1)
        await loop.stop()


# ------------------------------------------------------------------ #
# Tests — lifecycle
# ------------------------------------------------------------------ #


class TestLifecycle:
    async def test_start_stop(self) -> None:
        loop = _event_loop()
        await loop.start()
        await loop.stop()


# ------------------------------------------------------------------ #
# Tests — orchestrator
# ------------------------------------------------------------------ #


class TestOrchestrator:
    async def test_start_stop(self) -> None:
        reg = ProviderRegistry()
        router = _mock_router()
        ctx = _mock_ctx()
        orch = CognitiveOrchestrator(
            registry=reg, router=router, context_manager=ctx,
        )
        await orch.start()
        assert orch.event_loop is not None
        await orch.stop()

    async def test_event_loop_accessible(self) -> None:
        reg = ProviderRegistry()
        router = _mock_router()
        ctx = _mock_ctx()
        orch = CognitiveOrchestrator(
            registry=reg, router=router, context_manager=ctx,
        )
        assert isinstance(orch.event_loop, CognitiveEventLoop)


class TestSystemRoutingIntegration:
    async def test_fallback_flow_returns_response(self) -> None:
        registry = ProviderRegistry()
        registry.register(
            "anthropic",
            _mock_adapter(
                provider="anthropic",
                model_id="claude-opus-4",
                healthy=False,
            ),
        )
        registry.register(
            "ollama",
            _mock_adapter(
                provider="ollama",
                content="fallback-answer",
                model_id="bonsai-8b",
            ),
        )
        router = ModelRouter(
            registry=registry,
            system_assignments={
                CognitiveSystem.REASONING: RouteStep("anthropic", "claude-opus-4")
            },
            default_fallback_chain=FallbackChain(
                steps=(RouteStep("ollama", "bonsai-8b"),)
            ),
            health_ttl_s=0,
        )
        ctx = _mock_ctx()
        loop = CognitiveEventLoop(
            model_router=router,
            context_manager=ctx,
            strategies={},
        )

        response = await loop.handle_request(
            CognitiveRequest(
                request_id="integration-1",
                prompt="analyze",
                context="ctx",
                priority=Priority.HIGH,
                system=CognitiveSystem.REASONING,
            )
        )

        assert response.answer == "fallback-answer"
        assert response.provider == "ollama"
        assert response.model_id == "bonsai-8b"
        telemetry = router.get_fallback_telemetry()
        assert telemetry.fallback_count == 1
        assert telemetry.consecutive_fallback_count == 1


class TestParseSystem:
    def test_invalid_value_defaults_to_chat(self) -> None:
        assert _parse_system("unknown") is CognitiveSystem.CHAT

    def test_enum_passthrough(self) -> None:
        assert _parse_system(CognitiveSystem.SLEEP) is CognitiveSystem.SLEEP
