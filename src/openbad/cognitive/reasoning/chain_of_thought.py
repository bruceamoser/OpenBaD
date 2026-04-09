"""Chain-of-Thought (CoT) reasoning strategy."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbad.cognitive.model_router import Priority
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStep, ReasoningStrategy

if TYPE_CHECKING:
    from openbad.cognitive.model_router import ModelRouter


_COT_SYSTEM_PROMPT = (
    "You are a careful analytical reasoner. "
    "Think step-by-step. For each step, output exactly:\n"
    "STEP <n>:\nTHOUGHT: <your reasoning>\nCONCLUSION: <what you determined>\n\n"
    "After all steps, output:\nFINAL ANSWER: <your final answer>"
)

_STEP_PATTERN = re.compile(
    r"STEP\s+(\d+)\s*:\s*\n"
    r"THOUGHT:\s*(.+?)\n"
    r"CONCLUSION:\s*(.+?)(?=\nSTEP|\nFINAL|\Z)",
    re.DOTALL,
)

_FINAL_PATTERN = re.compile(r"FINAL ANSWER:\s*(.+)", re.DOTALL)


@dataclass
class ChainOfThoughtConfig:
    """Configuration for CoT reasoning."""

    priority: Priority = Priority.MEDIUM
    max_steps: int = 10
    system_prompt: str = _COT_SYSTEM_PROMPT


class ChainOfThought(ReasoningStrategy):
    """Chain-of-Thought: sequential step-by-step reasoning."""

    def __init__(self, config: ChainOfThoughtConfig | None = None) -> None:
        self._config = config or ChainOfThoughtConfig()

    async def reason(
        self,
        prompt: str,
        context: str,
        model_router: ModelRouter,
    ) -> ReasoningResult:
        """Send a step-by-step prompt to the model and parse the trace."""
        adapter, model_id, decision = await model_router.route(
            self._config.priority,
        )

        full_prompt = (
            f"{self._config.system_prompt}\n\n"
            f"Context:\n{context}\n\n"
            f"Problem:\n{prompt}"
        )

        t0 = time.monotonic()
        result = await adapter.complete(full_prompt, model=model_id)
        latency = (time.monotonic() - t0) * 1000

        model_router.record_latency(decision.provider, latency)

        steps = _parse_steps(result.content, self._config.max_steps)
        final = _parse_final_answer(result.content)

        return ReasoningResult(
            final_answer=final,
            steps=tuple(steps),
            total_tokens=result.tokens_used,
            total_latency_ms=latency,
            metadata={
                "model_id": model_id,
                "provider": decision.provider,
                "raw_response": result.content,
            },
        )


def _parse_steps(text: str, max_steps: int) -> list[ReasoningStep]:
    """Extract structured reasoning steps from the model response."""
    steps: list[ReasoningStep] = []
    for match in _STEP_PATTERN.finditer(text):
        if len(steps) >= max_steps:
            break
        steps.append(
            ReasoningStep(
                step_number=int(match.group(1)),
                thought=match.group(2).strip(),
                conclusion=match.group(3).strip(),
            )
        )
    return steps


def _parse_final_answer(text: str) -> str:
    """Extract the final answer from the model response."""
    match = _FINAL_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    # If no structured answer, return the last non-empty line
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""
