"""Abstract base class and shared types for LLM/SLM provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompletionResult:
    """Result of a completion request."""

    content: str
    model_id: str
    provider: str
    tokens_used: int = 0
    latency_ms: float = 0.0
    finish_reason: str = ""


@dataclass(frozen=True)
class ModelInfo:
    """Metadata about a model available from a provider."""

    model_id: str
    provider: str
    context_window: int = 0
    capabilities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HealthStatus:
    """Health check result for a provider."""

    provider: str
    available: bool
    latency_ms: float = 0.0
    models_available: int = 0


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for a single provider adapter."""

    name: str
    base_url: str = ""
    api_key_env: str = ""
    default_model: str = ""
    timeout_ms: int = 30_000
    max_retries: int = 2


class ProviderAdapter(ABC):
    """Abstract base class that all LLM/SLM provider adapters must implement."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Run a completion and return the full result."""

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream completion tokens."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List models available from this provider."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check provider availability."""
