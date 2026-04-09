"""Cognitive engine configuration — model defaults, context budgets, provider settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ProviderConfig:
    """Settings for an individual LLM/SLM provider."""

    name: str = ""
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout_ms: int = 30_000
    enabled: bool = True


@dataclass(frozen=True)
class ContextBudgetConfig:
    """Token budget limits for different model tiers."""

    slm_max_tokens: int = 8_192
    llm_max_tokens: int = 32_768
    reserved_system_tokens: int = 512


@dataclass(frozen=True)
class ReasoningDefaults:
    """Default parameters for reasoning requests."""

    default_max_tokens: int = 2_048
    default_temperature: float = 0.7
    critical_timeout_ms: int = 30_000
    high_timeout_ms: int = 15_000
    medium_timeout_ms: int = 10_000
    low_timeout_ms: int = 5_000


@dataclass(frozen=True)
class CognitiveConfig:
    """Top-level cognitive engine configuration."""

    providers: list[ProviderConfig] = field(default_factory=list)
    context_budget: ContextBudgetConfig = field(
        default_factory=ContextBudgetConfig
    )
    reasoning: ReasoningDefaults = field(
        default_factory=ReasoningDefaults
    )
    default_provider: str = "ollama"
    enabled: bool = True


def load_cognitive_config(
    yaml_path: str | Path = "config/cognitive.yaml",
) -> CognitiveConfig:
    """Load cognitive config from YAML, falling back to defaults."""
    path = Path(yaml_path)
    if not path.exists():
        return CognitiveConfig()

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    cog = data.get("cognitive", {})

    providers_data = cog.get("providers", [])
    providers = [ProviderConfig(**p) for p in providers_data]

    budget_data = cog.get("context_budget", {})
    budget = ContextBudgetConfig(**budget_data) if budget_data else ContextBudgetConfig()

    reasoning_data = cog.get("reasoning", {})
    reasoning = (
        ReasoningDefaults(**reasoning_data) if reasoning_data else ReasoningDefaults()
    )

    return CognitiveConfig(
        providers=providers,
        context_budget=budget,
        reasoning=reasoning,
        default_provider=cog.get("default_provider", "ollama"),
        enabled=cog.get("enabled", True),
    )
