"""Model router — priority-based provider selection with fallback chains."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import yaml

from openbad.cognitive.providers.base import ProviderAdapter
from openbad.cognitive.providers.registry import ProviderRegistry


class Priority(IntEnum):
    """Request priority levels."""

    CRITICAL = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1


@dataclass(frozen=True)
class RouteStep:
    """A single step in a fallback chain: provider name + model ID."""

    provider: str
    model_id: str


@dataclass(frozen=True)
class FallbackChain:
    """Ordered list of provider/model pairs to try."""

    steps: tuple[RouteStep, ...]

    def __iter__(self):
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)


@dataclass
class RoutingDecision:
    """Record of a routing decision for observability."""

    priority: Priority
    provider: str
    model_id: str
    fallback_index: int
    cortisol_downgrade: bool = False
    budget_exhausted: bool = False
    latency_ms: float = 0.0


@dataclass
class ProviderHealth:
    """Cached health state for a provider."""

    available: bool = True
    last_check: float = 0.0
    avg_latency_ms: float = 0.0
    failure_count: int = 0


class ModelRouter:
    """Routes requests to providers based on priority, health, and budget.

    Parameters
    ----------
    registry:
        Provider registry to look up adapters.
    chains:
        Priority → FallbackChain mapping.
    cortisol_threshold:
        When cortisol exceeds this, downgrade to cheaper models.
    budget_limit:
        Maximum spend in arbitrary units; 0 = unlimited.
    health_ttl_s:
        How long a health check result is cached.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        chains: dict[Priority, FallbackChain] | None = None,
        cortisol_threshold: float = 0.8,
        budget_limit: float = 0,
        health_ttl_s: float = 60,
    ) -> None:
        self._registry = registry
        self._chains = chains or _default_chains()
        self._cortisol_threshold = cortisol_threshold
        self._budget_limit = budget_limit
        self._health_ttl_s = health_ttl_s
        self._health: dict[str, ProviderHealth] = {}
        self._spend: float = 0.0
        self._latencies: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def route(
        self,
        priority: Priority,
        cortisol: float = 0.0,
    ) -> tuple[ProviderAdapter, str, RoutingDecision]:
        """Select the best provider/model for the given priority.

        Returns (adapter, model_id, decision).
        Raises ``RuntimeError`` if no provider is available.
        """
        effective_priority = priority
        cortisol_downgrade = False
        budget_exhausted = self._is_budget_exhausted()

        if budget_exhausted:
            effective_priority = Priority.LOW
        elif cortisol > self._cortisol_threshold:
            effective_priority = Priority(max(priority - 1, Priority.LOW))
            cortisol_downgrade = priority != effective_priority

        fallback = self._chains.get(Priority.LOW, FallbackChain(steps=()))
        chain = self._chains.get(effective_priority, fallback)

        for idx, step in enumerate(chain):
            adapter = self._registry.get(step.provider)
            if adapter is None:
                continue
            if not await self._is_healthy(step.provider, adapter):
                continue

            decision = RoutingDecision(
                priority=priority,
                provider=step.provider,
                model_id=step.model_id,
                fallback_index=idx,
                cortisol_downgrade=cortisol_downgrade,
                budget_exhausted=budget_exhausted,
            )
            return adapter, step.model_id, decision

        msg = f"No available provider for priority {priority.name}"
        raise RuntimeError(msg)

    def record_latency(self, provider: str, latency_ms: float) -> None:
        """Record a latency observation for a provider."""
        self._latencies.setdefault(provider, [])
        self._latencies[provider].append(latency_ms)
        # Keep last 100 observations
        if len(self._latencies[provider]) > 100:
            self._latencies[provider] = self._latencies[provider][-100:]
        # Update health record
        h = self._health.get(provider)
        if h:
            h.avg_latency_ms = sum(self._latencies[provider]) / len(
                self._latencies[provider]
            )

    def record_spend(self, amount: float) -> None:
        """Record spend towards the budget."""
        self._spend += amount

    def mark_unhealthy(self, provider: str) -> None:
        """Mark a provider as unhealthy."""
        h = self._health.setdefault(provider, ProviderHealth())
        h.available = False
        h.failure_count += 1
        h.last_check = time.monotonic()

    def get_avg_latency(self, provider: str) -> float:
        """Return average latency for a provider, 0 if no data."""
        obs = self._latencies.get(provider, [])
        return sum(obs) / len(obs) if obs else 0.0

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _is_budget_exhausted(self) -> bool:
        return self._budget_limit > 0 and self._spend >= self._budget_limit

    async def _is_healthy(self, name: str, adapter: ProviderAdapter) -> bool:
        h = self._health.get(name)
        now = time.monotonic()
        if h and (now - h.last_check) < self._health_ttl_s:
            return h.available

        status = await adapter.health_check()
        self._health[name] = ProviderHealth(
            available=status.available,
            last_check=now,
            avg_latency_ms=status.latency_ms,
        )
        return status.available


# ------------------------------------------------------------------ #
# Default chains
# ------------------------------------------------------------------ #


def _default_chains() -> dict[Priority, FallbackChain]:
    return {
        Priority.CRITICAL: FallbackChain(
            steps=(
                RouteStep("anthropic", "claude-sonnet-4-20250514"),
                RouteStep("openai", "gpt-4o"),
                RouteStep("ollama", "llama3.2"),
            )
        ),
        Priority.HIGH: FallbackChain(
            steps=(
                RouteStep("openai", "gpt-4o-mini"),
                RouteStep("ollama", "llama3.2"),
            )
        ),
        Priority.MEDIUM: FallbackChain(
            steps=(
                RouteStep("ollama", "llama3.2"),
                RouteStep("openai", "gpt-4o-mini"),
            )
        ),
        Priority.LOW: FallbackChain(
            steps=(RouteStep("ollama", "llama3.2"),)
        ),
    }


# ------------------------------------------------------------------ #
# YAML config loader
# ------------------------------------------------------------------ #


def load_routing_config(path: str) -> dict[str, Any]:
    """Load routing configuration from a YAML file."""
    with open(path) as f:  # noqa: PTH123
        return yaml.safe_load(f)


def configure_chains(config: dict[str, Any]) -> dict[Priority, FallbackChain]:
    """Build fallback chains from a config dict."""
    chains: dict[Priority, FallbackChain] = {}
    priority_map = {p.name.lower(): p for p in Priority}
    for name, steps_list in config.get("chains", {}).items():
        pri = priority_map.get(name.lower())
        if pri is None:
            continue
        steps = tuple(
            RouteStep(provider=s["provider"], model_id=s["model"])
            for s in steps_list
        )
        chains[pri] = FallbackChain(steps=steps)
    return chains
