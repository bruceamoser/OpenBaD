"""Tests for ModelRouter — priority routing, fallback, cortisol, budget."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.model_router import (
    FallbackChain,
    FallbackTelemetry,
    ModelRouter,
    Priority,
    RouteStep,
    configure_chains,
    load_routing_config,
)
from openbad.cognitive.providers.base import HealthStatus, ProviderAdapter
from openbad.cognitive.providers.registry import ProviderRegistry

# ---------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------- #


def _mock_adapter(healthy: bool = True, latency: float = 50.0) -> ProviderAdapter:
    adapter = AsyncMock(spec=ProviderAdapter)
    adapter.health_check = AsyncMock(
        return_value=HealthStatus(provider="mock", available=healthy, latency_ms=latency)
    )
    return adapter


def _build_router(
    providers: dict[str, ProviderAdapter] | None = None,
    chains: dict[Priority, FallbackChain] | None = None,
    system_assignments: dict[CognitiveSystem, RouteStep] | None = None,
    default_fallback_chain: FallbackChain | None = None,
    cortisol_threshold: float = 0.8,
    budget_limit: float = 0,
    health_ttl_s: float = 0,
    endocrine_controller: object | None = None,
    escalation_gateway: object | None = None,
    fallback_release_per_step: float = 0.1,
    fallback_escalation_after: int = 5,
) -> ModelRouter:
    reg = ProviderRegistry()
    for name, adapter in (providers or {}).items():
        reg.register(name, adapter)
    return ModelRouter(
        registry=reg,
        chains=chains,
        system_assignments=system_assignments,
        default_fallback_chain=default_fallback_chain,
        cortisol_threshold=cortisol_threshold,
        budget_limit=budget_limit,
        health_ttl_s=health_ttl_s,
        endocrine_controller=endocrine_controller,
        escalation_gateway=escalation_gateway,
        fallback_release_per_step=fallback_release_per_step,
        fallback_escalation_after=fallback_escalation_after,
    )


# ---------------------------------------------------------------- #
# Priority routing
# ---------------------------------------------------------------- #


class TestPriorityRouting:
    async def test_critical_uses_first_chain(self) -> None:
        chains = {
            Priority.CRITICAL: FallbackChain(
                steps=(RouteStep("anthropic", "claude"), RouteStep("ollama", "llama"))
            ),
        }
        router = _build_router(
            providers={"anthropic": _mock_adapter(), "ollama": _mock_adapter()},
            chains=chains,
        )
        adapter, model, decision = await router.route(Priority.CRITICAL)
        assert decision.provider == "anthropic"
        assert model == "claude"
        assert decision.fallback_index == 0

    async def test_low_routes_to_ollama(self) -> None:
        chains = {
            Priority.LOW: FallbackChain(
                steps=(RouteStep("ollama", "llama"),)
            ),
        }
        router = _build_router(
            providers={"ollama": _mock_adapter()}, chains=chains,
        )
        adapter, model, decision = await router.route(Priority.LOW)
        assert decision.provider == "ollama"
        assert model == "llama"

    async def test_medium_prefers_ollama_then_openai(self) -> None:
        chains = {
            Priority.MEDIUM: FallbackChain(
                steps=(RouteStep("ollama", "llama"), RouteStep("openai", "gpt"))
            ),
        }
        router = _build_router(
            providers={"ollama": _mock_adapter(), "openai": _mock_adapter()},
            chains=chains,
        )
        _, model, decision = await router.route(Priority.MEDIUM)
        assert decision.provider == "ollama"


# ---------------------------------------------------------------- #
# Fallback
# ---------------------------------------------------------------- #


class TestFallback:
    async def test_fallback_when_primary_unhealthy(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("openai", "gpt"), RouteStep("ollama", "llama"))
            ),
        }
        router = _build_router(
            providers={
                "openai": _mock_adapter(healthy=False),
                "ollama": _mock_adapter(),
            },
            chains=chains,
        )
        _, model, decision = await router.route(Priority.HIGH)
        assert decision.provider == "ollama"
        assert decision.fallback_index == 1

    async def test_fallback_when_provider_not_registered(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("missing", "x"), RouteStep("ollama", "llama"))
            ),
        }
        router = _build_router(
            providers={"ollama": _mock_adapter()}, chains=chains,
        )
        _, _, decision = await router.route(Priority.HIGH)
        assert decision.provider == "ollama"

    async def test_no_provider_raises(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("missing", "x"),)
            ),
        }
        router = _build_router(providers={}, chains=chains)
        with pytest.raises(RuntimeError, match="No available provider"):
            await router.route(Priority.HIGH)

    async def test_system_assignment_uses_shared_fallback_chain(self) -> None:
        endocrine = MagicMock()
        router = _build_router(
            providers={
                "anthropic": _mock_adapter(healthy=False),
                "ollama": _mock_adapter(),
            },
            system_assignments={
                CognitiveSystem.REASONING: RouteStep("anthropic", "claude-opus-4")
            },
            default_fallback_chain=FallbackChain(
                steps=(RouteStep("ollama", "bonsai-8b"),)
            ),
            endocrine_controller=endocrine,
            fallback_release_per_step=0.2,
        )

        _, model, decision = await router.route(
            Priority.HIGH,
            system=CognitiveSystem.REASONING,
        )

        assert model == "bonsai-8b"
        assert decision.provider == "ollama"
        assert decision.system == "reasoning"
        assert decision.fallback_index == 1
        assert decision.fallback_count == 1
        assert decision.consecutive_fallback_count == 1
        endocrine.trigger.assert_called_once_with("cortisol", 0.2)

    async def test_system_primary_resets_consecutive_fallbacks(self) -> None:
        router = _build_router(
            providers={
                "anthropic": _mock_adapter(healthy=False),
                "ollama": _mock_adapter(),
            },
            system_assignments={
                CognitiveSystem.CHAT: RouteStep("anthropic", "claude-sonnet-4")
            },
            default_fallback_chain=FallbackChain(
                steps=(RouteStep("ollama", "llama3.2"),)
            ),
        )

        await router.route(Priority.MEDIUM, system=CognitiveSystem.CHAT)
        telemetry = router.get_fallback_telemetry()
        assert telemetry == FallbackTelemetry(
            fallback_count=1,
            consecutive_fallback_count=1,
            last_fallback_time=telemetry.last_fallback_time,
        )

        router = _build_router(
            providers={"anthropic": _mock_adapter()},
            system_assignments={
                CognitiveSystem.CHAT: RouteStep("anthropic", "claude-sonnet-4")
            },
            default_fallback_chain=FallbackChain(
                steps=(RouteStep("ollama", "llama3.2"),)
            ),
        )

        _, _, decision = await router.route(Priority.MEDIUM, system=CognitiveSystem.CHAT)
        assert decision.fallback_index == 0
        assert router.get_fallback_telemetry().consecutive_fallback_count == 0

    async def test_consecutive_fallbacks_escalate_at_threshold(self) -> None:
        endocrine = MagicMock()
        escalation = MagicMock()
        router = _build_router(
            providers={
                "anthropic": _mock_adapter(healthy=False),
                "ollama": _mock_adapter(),
            },
            system_assignments={
                CognitiveSystem.REACTIONS: RouteStep("anthropic", "claude-haiku-4")
            },
            default_fallback_chain=FallbackChain(
                steps=(RouteStep("ollama", "llama3.2"),)
            ),
            endocrine_controller=endocrine,
            escalation_gateway=escalation,
            fallback_escalation_after=2,
        )

        await router.route(Priority.HIGH, system=CognitiveSystem.REACTIONS)
        escalation.escalate.assert_not_called()

        await router.route(Priority.HIGH, system=CognitiveSystem.REACTIONS)
        escalation.escalate.assert_called_once()
        telemetry = router.get_fallback_telemetry()
        assert telemetry.fallback_count == 2
        assert telemetry.consecutive_fallback_count == 2


# ---------------------------------------------------------------- #
# Cortisol downgrade
# ---------------------------------------------------------------- #


class TestCortisolDowngrade:
    async def test_cortisol_downgrades_priority(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("openai", "gpt"),)
            ),
            Priority.MEDIUM: FallbackChain(
                steps=(RouteStep("ollama", "llama"),)
            ),
        }
        router = _build_router(
            providers={"openai": _mock_adapter(), "ollama": _mock_adapter()},
            chains=chains,
            cortisol_threshold=0.5,
        )
        _, _, decision = await router.route(Priority.HIGH, cortisol=0.9)
        assert decision.cortisol_downgrade is True
        assert decision.provider == "ollama"

    async def test_no_downgrade_below_threshold(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("openai", "gpt"),)
            ),
        }
        router = _build_router(
            providers={"openai": _mock_adapter()},
            chains=chains,
            cortisol_threshold=0.8,
        )
        _, _, decision = await router.route(Priority.HIGH, cortisol=0.3)
        assert decision.cortisol_downgrade is False
        assert decision.provider == "openai"

    async def test_low_cannot_downgrade_further(self) -> None:
        chains = {
            Priority.LOW: FallbackChain(
                steps=(RouteStep("ollama", "llama"),)
            ),
        }
        router = _build_router(
            providers={"ollama": _mock_adapter()},
            chains=chains,
            cortisol_threshold=0.5,
        )
        _, _, decision = await router.route(Priority.LOW, cortisol=0.9)
        assert decision.cortisol_downgrade is False
        assert decision.provider == "ollama"


# ---------------------------------------------------------------- #
# Budget exhaustion
# ---------------------------------------------------------------- #


class TestBudgetExhaustion:
    async def test_budget_forces_low_priority(self) -> None:
        chains = {
            Priority.CRITICAL: FallbackChain(
                steps=(RouteStep("openai", "gpt"),)
            ),
            Priority.LOW: FallbackChain(
                steps=(RouteStep("ollama", "llama"),)
            ),
        }
        router = _build_router(
            providers={"openai": _mock_adapter(), "ollama": _mock_adapter()},
            chains=chains,
            budget_limit=10.0,
        )
        router.record_spend(15.0)
        _, _, decision = await router.route(Priority.CRITICAL)
        assert decision.budget_exhausted is True
        assert decision.provider == "ollama"

    async def test_within_budget_routes_normally(self) -> None:
        chains = {
            Priority.CRITICAL: FallbackChain(
                steps=(RouteStep("openai", "gpt"),)
            ),
        }
        router = _build_router(
            providers={"openai": _mock_adapter()},
            chains=chains,
            budget_limit=100.0,
        )
        router.record_spend(5.0)
        _, _, decision = await router.route(Priority.CRITICAL)
        assert decision.budget_exhausted is False
        assert decision.provider == "openai"


# ---------------------------------------------------------------- #
# Latency tracking
# ---------------------------------------------------------------- #


class TestLatencyTracking:
    def test_record_and_retrieve(self) -> None:
        router = _build_router()
        router.record_latency("openai", 100.0)
        router.record_latency("openai", 200.0)
        assert router.get_avg_latency("openai") == 150.0

    def test_unknown_provider_returns_zero(self) -> None:
        router = _build_router()
        assert router.get_avg_latency("unknown") == 0.0


# ---------------------------------------------------------------- #
# Health caching
# ---------------------------------------------------------------- #


class TestHealthCache:
    async def test_cached_unhealthy_skipped(self) -> None:
        chains = {
            Priority.HIGH: FallbackChain(
                steps=(RouteStep("openai", "gpt"), RouteStep("ollama", "llama"))
            ),
        }
        router = _build_router(
            providers={"openai": _mock_adapter(), "ollama": _mock_adapter()},
            chains=chains,
            health_ttl_s=300,
        )
        router.mark_unhealthy("openai")
        _, _, decision = await router.route(Priority.HIGH)
        assert decision.provider == "ollama"


# ---------------------------------------------------------------- #
# YAML config
# ---------------------------------------------------------------- #


class TestYamlConfig:
    def test_load_and_parse(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "routing.yaml"
        cfg_file.write_text(
            "chains:\n"
            "  high:\n"
            "    - provider: openai\n"
            "      model: gpt-4o\n"
            "  low:\n"
            "    - provider: ollama\n"
            "      model: llama3.2\n"
        )
        config = load_routing_config(str(cfg_file))
        chains = configure_chains(config)
        assert Priority.HIGH in chains
        assert Priority.LOW in chains
        assert chains[Priority.HIGH].steps[0].provider == "openai"

    def test_default_config_loads(self) -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "model_routing.yaml"
        if cfg_path.exists():
            config = load_routing_config(str(cfg_path))
            chains = configure_chains(config)
            assert Priority.CRITICAL in chains
            assert len(chains[Priority.CRITICAL]) >= 2


# ---------------------------------------------------------------- #
# FallbackChain
# ---------------------------------------------------------------- #


class TestFallbackChain:
    def test_len_and_iter(self) -> None:
        chain = FallbackChain(
            steps=(RouteStep("a", "m1"), RouteStep("b", "m2"))
        )
        assert len(chain) == 2
        names = [s.provider for s in chain]
        assert names == ["a", "b"]
