"""Data types for cognitive processing requests and responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.model_router import Priority


@dataclass
class CognitiveRequest:
    """An incoming reasoning request."""

    request_id: str
    prompt: str
    context: str = ""
    system: CognitiveSystem = CognitiveSystem.CHAT
    priority: Priority = Priority.MEDIUM
    cortisol: float = 0.0


@dataclass
class CognitiveResponse:
    """Result of a cognitive processing cycle."""

    request_id: str
    answer: str
    provider: str = ""
    model_id: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    strategy: str = ""
    timed_out: bool = False
    error: str = ""


class CognitiveHandler(Protocol):
    """Protocol for anything that can handle a cognitive request."""

    async def handle_request(
        self, request: CognitiveRequest
    ) -> CognitiveResponse: ...
