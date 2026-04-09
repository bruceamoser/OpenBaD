"""Context window manager — token budgets, compression, usage tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

# ------------------------------------------------------------------ #
# Data types
# ------------------------------------------------------------------ #

class CompressionStrategy(Enum):
    """How context is compressed when it exceeds the budget."""

    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"


@dataclass(frozen=True)
class ContextBudget:
    """Token allocation for a single request."""

    max_tokens: int
    system_tokens: int
    context_tokens: int
    response_tokens: int


@dataclass(frozen=True)
class CompressedContext:
    """Result of context compression."""

    text: str
    original_tokens: int
    compressed_tokens: int
    strategy: CompressionStrategy


@dataclass
class UsageRecord:
    """Cumulative token usage for a provider."""

    total_tokens: int = 0
    request_count: int = 0
    last_update: float = 0.0


# ------------------------------------------------------------------ #
# Token counting
# ------------------------------------------------------------------ #

_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count (chars / 4 heuristic)."""
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


# ------------------------------------------------------------------ #
# Default model limits
# ------------------------------------------------------------------ #

_DEFAULT_LIMITS: dict[str, int] = {
    "slm": 8_192,
    "llm": 32_768,
}


# ------------------------------------------------------------------ #
# ContextWindowManager
# ------------------------------------------------------------------ #


class ContextWindowManager:
    """Manages token budgets, context compression, and usage tracking.

    Parameters
    ----------
    model_limits:
        Mapping of model_id (or category) to max context window tokens.
    default_limit:
        Fallback context window size for unknown models.
    system_budget_ratio:
        Fraction of context reserved for the system prompt.
    response_budget_ratio:
        Fraction of context reserved for the response.
    """

    def __init__(
        self,
        model_limits: dict[str, int] | None = None,
        default_limit: int = 8_192,
        system_budget_ratio: float = 0.15,
        response_budget_ratio: float = 0.25,
    ) -> None:
        self._limits = {**_DEFAULT_LIMITS, **(model_limits or {})}
        self._default_limit = default_limit
        self._sys_ratio = system_budget_ratio
        self._resp_ratio = response_budget_ratio
        self._usage: dict[str, UsageRecord] = {}
        self._request_usage: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Budget allocation
    # ------------------------------------------------------------------ #

    def allocate(self, model_id: str, system_prompt: str = "") -> ContextBudget:
        """Calculate token budget for a request to *model_id*."""
        max_tokens = self._limits.get(model_id, self._default_limit)
        system_tokens = max(
            estimate_tokens(system_prompt),
            int(max_tokens * self._sys_ratio),
        )
        response_tokens = int(max_tokens * self._resp_ratio)
        context_tokens = max(0, max_tokens - system_tokens - response_tokens)
        return ContextBudget(
            max_tokens=max_tokens,
            system_tokens=system_tokens,
            context_tokens=context_tokens,
            response_tokens=response_tokens,
        )

    # ------------------------------------------------------------------ #
    # Compression
    # ------------------------------------------------------------------ #

    def compress(
        self,
        context: str,
        target_tokens: int,
        strategy: CompressionStrategy = CompressionStrategy.TRUNCATE,
    ) -> CompressedContext:
        """Compress *context* to fit within *target_tokens*."""
        original_tokens = estimate_tokens(context)
        if original_tokens <= target_tokens:
            return CompressedContext(
                text=context,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                strategy=strategy,
            )

        if strategy == CompressionStrategy.TRUNCATE:
            text = self._truncate(context, target_tokens)
        else:
            # Summarize falls back to truncation (no live model call here)
            text = self._truncate(context, target_tokens)

        return CompressedContext(
            text=text,
            original_tokens=original_tokens,
            compressed_tokens=estimate_tokens(text),
            strategy=strategy,
        )

    # ------------------------------------------------------------------ #
    # Usage tracking
    # ------------------------------------------------------------------ #

    def track_usage(
        self, provider: str, tokens_used: int, request_id: str = ""
    ) -> None:
        """Record token usage for a provider (and optionally per request)."""
        rec = self._usage.setdefault(provider, UsageRecord())
        rec.total_tokens += tokens_used
        rec.request_count += 1
        rec.last_update = time.monotonic()
        if request_id:
            self._request_usage[request_id] = (
                self._request_usage.get(request_id, 0) + tokens_used
            )

    def get_provider_usage(self, provider: str) -> UsageRecord:
        """Return cumulative usage for a provider."""
        return self._usage.get(provider, UsageRecord())

    def get_request_usage(self, request_id: str) -> int:
        """Return tokens used by a specific request."""
        return self._request_usage.get(request_id, 0)

    def fits(self, text: str, budget: ContextBudget) -> bool:
        """Check whether *text* fits within the context portion of a budget."""
        return estimate_tokens(text) <= budget.context_tokens

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _truncate(context: str, target_tokens: int) -> str:
        """Keep the most recent text (tail) that fits within *target_tokens*.

        Context priority: recent > historical, so we drop from the front.
        """
        target_chars = target_tokens * _CHARS_PER_TOKEN
        if len(context) <= target_chars:
            return context
        return context[-target_chars:]
