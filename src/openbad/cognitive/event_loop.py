"""Cognitive event loop — processes reasoning requests from the nervous system."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.context_manager import ContextWindowManager
from openbad.cognitive.model_router import ModelRouter, Priority
from openbad.cognitive.reasoning.base import ReasoningStrategy

if TYPE_CHECKING:
    from openbad.memory.semantic import SemanticMemory

log = logging.getLogger(__name__)

# Request timeouts per priority (seconds)
_TIMEOUTS: dict[Priority, float] = {
    Priority.CRITICAL: 30.0,
    Priority.HIGH: 30.0,
    Priority.MEDIUM: 10.0,
    Priority.LOW: 5.0,
}


# ------------------------------------------------------------------ #
# Data types
# ------------------------------------------------------------------ #


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


# ------------------------------------------------------------------ #
# Event loop
# ------------------------------------------------------------------ #


class CognitiveEventLoop:
    """Subscribes to reasoning requests, orchestrates provider/strategy/context,
    and publishes results.

    Parameters
    ----------
    model_router:
        Routes requests to the right provider.
    context_manager:
        Manages token budgets and compression.
    strategies:
        Mapping of Priority → ReasoningStrategy to use.
    publish_fn:
        Callback ``(topic, payload_dict) -> Awaitable`` for publishing results.
    validate_fn:
        Optional immune-system validation ``(request) -> bool``.
    """

    def __init__(
        self,
        model_router: ModelRouter,
        context_manager: ContextWindowManager,
        strategies: dict[Priority | CognitiveSystem, ReasoningStrategy],
        publish_fn: Any = None,
        validate_fn: Any = None,
        semantic_memory: SemanticMemory | None = None,
        memory_top_k: int = 3,
    ) -> None:
        self._router = model_router
        self._ctx = context_manager
        self._strategies = strategies
        self._publish = publish_fn or _noop_publish
        self._validate = validate_fn
        self._semantic_memory = semantic_memory
        self._memory_top_k = memory_top_k
        self._running = False
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._running = True
        log.info("CognitiveEventLoop started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("CognitiveEventLoop stopped")

    # ------------------------------------------------------------------ #
    # Request handling
    # ------------------------------------------------------------------ #

    async def handle_request(self, request: CognitiveRequest) -> CognitiveResponse:
        """Process a single reasoning request (can be called directly or via MQTT)."""
        if self._validate and not self._validate(request):
            return CognitiveResponse(
                request_id=request.request_id,
                answer="",
                error="Request rejected by immune system",
            )

        timeout = _TIMEOUTS.get(request.priority, 10.0)

        try:
            response = await asyncio.wait_for(
                self._process(request), timeout=timeout,
            )
        except TimeoutError:
            response = CognitiveResponse(
                request_id=request.request_id,
                answer="",
                timed_out=True,
                error=f"Timed out after {timeout}s",
            )

        await self._publish(
            "agent/cognitive/response", _response_to_dict(response),
        )
        return response

    def submit(self, request: CognitiveRequest) -> None:
        """Submit a request for async processing (fire-and-forget)."""
        task = asyncio.create_task(self.handle_request(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ #
    # MQTT message handler
    # ------------------------------------------------------------------ #

    async def on_message(self, topic: str, payload: bytes) -> None:
        """Handle an incoming MQTT message."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("Invalid payload on %s", topic)
            return

        request = CognitiveRequest(
            request_id=data.get("request_id", ""),
            prompt=data.get("prompt", ""),
            context=data.get("context", ""),
            system=_parse_system(data.get("system")),
            priority=Priority(data.get("priority", Priority.MEDIUM)),
            cortisol=data.get("cortisol", 0.0),
        )
        self.submit(request)

    # ------------------------------------------------------------------ #
    # Core processing
    # ------------------------------------------------------------------ #

    async def _process(self, request: CognitiveRequest) -> CognitiveResponse:
        t0 = time.monotonic()

        # 1. Allocate context budget
        adapter, model_id, decision = await self._router.route(
            request.priority,
            system=request.system,
            cortisol=request.cortisol,
        )
        budget = self._ctx.allocate(model_id)

        # 2. Compress context if needed
        compressed = self._ctx.compress(
            request.context, target_tokens=budget.context_tokens,
        )

        # 2a. Prepend semantic memory recall (honours ContextWindowManager budget)
        enriched_context = self._enrich_with_memory(
            request.prompt, compressed.text, budget.context_tokens
        )

        # 3. Select and execute reasoning strategy
        strategy = self._select_strategy(request)
        if strategy:
            result = await strategy.reason(
                request.prompt, enriched_context, self._router,
            )
            answer = result.final_answer
            tokens = result.total_tokens
            strategy_name = type(strategy).__name__
        else:
            # Direct single-pass call
            completion = await adapter.complete(
                f"{enriched_context}\n\n{request.prompt}", model=model_id,
            )
            answer = completion.content
            tokens = completion.tokens_used
            strategy_name = "direct"

        latency = (time.monotonic() - t0) * 1000

        # 4. Track usage
        self._ctx.track_usage(decision.provider, tokens, request.request_id)
        self._router.record_latency(decision.provider, latency)

        return CognitiveResponse(
            request_id=request.request_id,
            answer=answer,
            provider=decision.provider,
            model_id=model_id,
            tokens_used=tokens,
            latency_ms=latency,
            strategy=strategy_name,
        )

    def _enrich_with_memory(
        self, prompt: str, context: str, budget_tokens: int
    ) -> str:
        """Prepend relevant semantic memory facts to *context*.

        Queries :attr:`_semantic_memory` with *prompt* and prepends the top
        results as a ``## Relevant Memory`` section, truncated so the total
        remains within the token budget (rough estimate: 1 token ≈ 4 chars).
        """
        if self._semantic_memory is None:
            return context

        try:
            hits = self._semantic_memory.search(prompt, top_k=self._memory_top_k)
        except Exception:
            log.debug("Semantic memory search failed; proceeding without recall")
            return context

        if not hits:
            return context

        # Build memory snippet
        facts = "\n".join(
            f"- {entry.value}" for entry, _score in hits if entry.value
        )
        if not facts:
            return context

        memory_block = f"## Relevant Memory\n{facts}\n\n"

        # Rough budget check: ensure prepended block + context fits
        approx_char_budget = budget_tokens * 4
        if len(memory_block) + len(context) > approx_char_budget:
            remaining = max(0, approx_char_budget - len(context))
            memory_block = memory_block[:remaining]

        return memory_block + context

    def _select_strategy(
        self,
        request: CognitiveRequest,
    ) -> ReasoningStrategy | None:
        return self._strategies.get(request.system) or self._strategies.get(
            request.priority
        )


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


async def _noop_publish(_topic: str, _payload: dict[str, Any]) -> None:
    pass


def _parse_system(value: Any) -> CognitiveSystem:
    if isinstance(value, CognitiveSystem):
        return value
    if isinstance(value, str):
        try:
            return CognitiveSystem(value.strip().lower())
        except ValueError:
            return CognitiveSystem.CHAT
    return CognitiveSystem.CHAT


def _response_to_dict(r: CognitiveResponse) -> dict[str, Any]:
    return {
        "request_id": r.request_id,
        "answer": r.answer,
        "provider": r.provider,
        "model_id": r.model_id,
        "tokens_used": r.tokens_used,
        "latency_ms": r.latency_ms,
        "strategy": r.strategy,
        "timed_out": r.timed_out,
        "error": r.error,
    }
