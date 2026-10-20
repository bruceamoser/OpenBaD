"""Base ABC and shared types for reasoning strategies."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbad.cognitive.model_router import ModelRouter


@dataclass(frozen=True)
class ReasoningStep:
    """A single step in a reasoning trace."""

    step_number: int
    thought: str
    conclusion: str


@dataclass(frozen=True)
class ReasoningResult:
    """Output of a reasoning strategy."""

    final_answer: str
    steps: tuple[ReasoningStep, ...] = ()
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class ReasoningStrategy(abc.ABC):
    """Abstract base for all reasoning strategies."""

    @abc.abstractmethod
    async def reason(
        self,
        prompt: str,
        context: str,
        model_router: ModelRouter,
    ) -> ReasoningResult:
        """Execute the reasoning strategy and return a result."""
