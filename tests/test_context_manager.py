"""Tests for ContextWindowManager — budgets, compression, usage tracking."""

from __future__ import annotations

from openbad.cognitive.context_manager import (
    CompressedContext,
    CompressionStrategy,
    ContextBudget,
    ContextWindowManager,
    estimate_tokens,
)

# ------------------------------------------------------------------ #
# Token estimation
# ------------------------------------------------------------------ #


class TestEstimateTokens:
    def test_empty(self) -> None:
        assert estimate_tokens("") == 0

    def test_short_text(self) -> None:
        assert estimate_tokens("abcd") == 1

    def test_longer_text(self) -> None:
        assert estimate_tokens("a" * 100) == 25

    def test_minimum_one(self) -> None:
        assert estimate_tokens("a") == 1


# ------------------------------------------------------------------ #
# Budget allocation
# ------------------------------------------------------------------ #


class TestAllocate:
    def test_known_model(self) -> None:
        mgr = ContextWindowManager(model_limits={"gpt-4o": 128_000})
        budget = mgr.allocate("gpt-4o")
        assert budget.max_tokens == 128_000
        total = budget.system_tokens + budget.context_tokens + budget.response_tokens
        assert total <= budget.max_tokens

    def test_default_limit(self) -> None:
        mgr = ContextWindowManager(default_limit=4096)
        budget = mgr.allocate("unknown-model")
        assert budget.max_tokens == 4096

    def test_system_prompt_overrides_ratio(self) -> None:
        mgr = ContextWindowManager(default_limit=8192)
        big_prompt = "x" * 4000  # ~1000 tokens
        budget = mgr.allocate("test", system_prompt=big_prompt)
        assert budget.system_tokens >= estimate_tokens(big_prompt)

    def test_budget_components_sum(self) -> None:
        mgr = ContextWindowManager(default_limit=10000)
        budget = mgr.allocate("test")
        assert (
            budget.system_tokens + budget.context_tokens + budget.response_tokens
            <= budget.max_tokens
        )


# ------------------------------------------------------------------ #
# Compression
# ------------------------------------------------------------------ #


class TestCompression:
    def test_no_compression_needed(self) -> None:
        mgr = ContextWindowManager()
        result = mgr.compress("short text", target_tokens=100)
        assert isinstance(result, CompressedContext)
        assert result.text == "short text"
        assert result.compressed_tokens == result.original_tokens

    def test_truncation(self) -> None:
        mgr = ContextWindowManager()
        long_text = "A" * 1000  # ~250 tokens
        result = mgr.compress(long_text, target_tokens=50)
        assert result.compressed_tokens <= 50
        # Truncation keeps the tail (most recent)
        assert result.text == long_text[-200:]

    def test_strategy_recorded(self) -> None:
        mgr = ContextWindowManager()
        result = mgr.compress("short", target_tokens=100)
        assert result.strategy == CompressionStrategy.TRUNCATE

    def test_summarize_fallback(self) -> None:
        mgr = ContextWindowManager()
        long_text = "B" * 1000
        result = mgr.compress(
            long_text, target_tokens=50, strategy=CompressionStrategy.SUMMARIZE,
        )
        assert result.compressed_tokens <= 50
        assert result.strategy == CompressionStrategy.SUMMARIZE


# ------------------------------------------------------------------ #
# Usage tracking
# ------------------------------------------------------------------ #


class TestUsageTracking:
    def test_track_provider(self) -> None:
        mgr = ContextWindowManager()
        mgr.track_usage("openai", 100)
        mgr.track_usage("openai", 200)
        usage = mgr.get_provider_usage("openai")
        assert usage.total_tokens == 300
        assert usage.request_count == 2

    def test_track_request(self) -> None:
        mgr = ContextWindowManager()
        mgr.track_usage("openai", 50, request_id="r1")
        mgr.track_usage("openai", 30, request_id="r1")
        assert mgr.get_request_usage("r1") == 80

    def test_unknown_provider_returns_zero(self) -> None:
        mgr = ContextWindowManager()
        usage = mgr.get_provider_usage("nope")
        assert usage.total_tokens == 0
        assert usage.request_count == 0

    def test_unknown_request_returns_zero(self) -> None:
        mgr = ContextWindowManager()
        assert mgr.get_request_usage("nope") == 0


# ------------------------------------------------------------------ #
# fits()
# ------------------------------------------------------------------ #


class TestFits:
    def test_fits_true(self) -> None:
        mgr = ContextWindowManager()
        budget = ContextBudget(
            max_tokens=1000, system_tokens=150, context_tokens=600, response_tokens=250,
        )
        assert mgr.fits("hello world", budget) is True

    def test_fits_false(self) -> None:
        mgr = ContextWindowManager()
        budget = ContextBudget(
            max_tokens=100, system_tokens=15, context_tokens=60, response_tokens=25,
        )
        long_text = "x" * 1000
        assert mgr.fits(long_text, budget) is False


# ------------------------------------------------------------------ #
# Overflow handling
# ------------------------------------------------------------------ #


class TestOverflow:
    def test_context_exceeding_budget_compresses(self) -> None:
        mgr = ContextWindowManager(default_limit=1000)
        budget = mgr.allocate("test")
        big_context = "C" * (budget.context_tokens * 5 * 4)
        result = mgr.compress(big_context, target_tokens=budget.context_tokens)
        assert result.compressed_tokens <= budget.context_tokens
        assert mgr.fits(result.text, budget)
