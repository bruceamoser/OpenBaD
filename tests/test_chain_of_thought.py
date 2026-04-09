"""Tests for Chain-of-Thought reasoning strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.cognitive.model_router import ModelRouter, Priority, RoutingDecision
from openbad.cognitive.providers.base import CompletionResult
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStep, ReasoningStrategy
from openbad.cognitive.reasoning.chain_of_thought import (
    ChainOfThought,
    ChainOfThoughtConfig,
    _parse_final_answer,
    _parse_steps,
)

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

_MOCK_COT_RESPONSE = (
    "STEP 1:\n"
    "THOUGHT: The problem asks for 2+2.\n"
    "CONCLUSION: Basic addition needed.\n\n"
    "STEP 2:\n"
    "THOUGHT: 2+2 equals 4.\n"
    "CONCLUSION: The answer is 4.\n\n"
    "FINAL ANSWER: 4"
)


def _mock_router(response_text: str = _MOCK_COT_RESPONSE) -> ModelRouter:
    adapter = AsyncMock()
    adapter.complete = AsyncMock(
        return_value=CompletionResult(
            content=response_text,
            model_id="llama3.2",
            provider="ollama",
            tokens_used=100,
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


# ------------------------------------------------------------------ #
# Tests — parsing
# ------------------------------------------------------------------ #


class TestParsing:
    def test_parse_steps(self) -> None:
        steps = _parse_steps(_MOCK_COT_RESPONSE, max_steps=10)
        assert len(steps) == 2
        assert steps[0].step_number == 1
        assert "2+2" in steps[0].thought
        assert steps[1].conclusion == "The answer is 4."

    def test_parse_steps_max(self) -> None:
        steps = _parse_steps(_MOCK_COT_RESPONSE, max_steps=1)
        assert len(steps) == 1

    def test_parse_final_answer(self) -> None:
        answer = _parse_final_answer(_MOCK_COT_RESPONSE)
        assert answer == "4"

    def test_parse_final_answer_fallback(self) -> None:
        answer = _parse_final_answer("Some rambling text\nThe result is 42")
        assert answer == "The result is 42"

    def test_parse_final_answer_empty(self) -> None:
        answer = _parse_final_answer("")
        assert answer == ""


# ------------------------------------------------------------------ #
# Tests — reasoning
# ------------------------------------------------------------------ #


class TestChainOfThought:
    async def test_basic_reasoning(self) -> None:
        cot = ChainOfThought()
        router = _mock_router()
        result = await cot.reason("What is 2+2?", "math context", router)

        assert isinstance(result, ReasoningResult)
        assert result.final_answer == "4"
        assert len(result.steps) == 2
        assert result.total_tokens == 100
        assert result.total_latency_ms > 0
        assert result.metadata["provider"] == "ollama"

    async def test_custom_priority(self) -> None:
        config = ChainOfThoughtConfig(priority=Priority.HIGH)
        cot = ChainOfThought(config=config)
        router = _mock_router()
        await cot.reason("test", "ctx", router)
        router.route.assert_awaited_once_with(Priority.HIGH)

    async def test_records_latency(self) -> None:
        cot = ChainOfThought()
        router = _mock_router()
        await cot.reason("test", "ctx", router)
        router.record_latency.assert_called_once()
        args = router.record_latency.call_args
        assert args[0][0] == "ollama"
        assert args[0][1] > 0

    async def test_no_steps_still_returns_answer(self) -> None:
        cot = ChainOfThought()
        router = _mock_router("Just the answer: 42")
        result = await cot.reason("test", "ctx", router)
        assert result.final_answer == "Just the answer: 42"
        assert len(result.steps) == 0

    async def test_max_steps_config(self) -> None:
        config = ChainOfThoughtConfig(max_steps=1)
        cot = ChainOfThought(config=config)
        router = _mock_router()
        result = await cot.reason("test", "ctx", router)
        assert len(result.steps) == 1


# ------------------------------------------------------------------ #
# Tests — ABC conformance
# ------------------------------------------------------------------ #


class TestABC:
    def test_is_reasoning_strategy(self) -> None:
        assert issubclass(ChainOfThought, ReasoningStrategy)

    def test_reasoning_step_frozen(self) -> None:
        s = ReasoningStep(step_number=1, thought="t", conclusion="c")
        with pytest.raises(AttributeError):
            s.thought = "x"  # type: ignore[misc]

    def test_reasoning_result_frozen(self) -> None:
        r = ReasoningResult(final_answer="a")
        with pytest.raises(AttributeError):
            r.final_answer = "b"  # type: ignore[misc]
