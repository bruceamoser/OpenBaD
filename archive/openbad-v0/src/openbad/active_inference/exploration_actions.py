"""Generate exploration actions from high-surprise observations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openbad.active_inference.insight_queue import InsightQueue

logger = logging.getLogger(__name__)


@dataclass
class ExplorationAction:
    """An internal cognitive task triggered by curiosity."""

    source_id: str
    trigger: str
    priority: float
    context: dict[str, Any]


class ExplorationActionGenerator:
    """Translates high-surprise observations into exploration actions."""

    def __init__(
        self,
        insight_queue: InsightQueue,
        submit_task_fn: Any = None,
        episodic_memory: Any = None,
    ) -> None:
        self._insight_queue = insight_queue
        self._submit_task_fn = submit_task_fn
        self._episodic_memory = episodic_memory

    async def process_high_surprise(
        self,
        source_id: str,
        surprise: float,
        errors: dict[str, float],
        raw_data: Any = None,
    ) -> ExplorationAction | None:
        """Generate an exploration action from a high-surprise event."""
        priority = min(surprise, 1.0)
        trigger = f"High surprise ({surprise:.2f}) from {source_id}"

        context: dict[str, Any] = {
            "source_id": source_id,
            "surprise": surprise,
            "errors": errors,
        }
        if raw_data:
            context["raw_data"] = raw_data

        action = ExplorationAction(
            source_id=source_id,
            trigger=trigger,
            priority=priority,
            context=context,
        )

        summary = self._generate_summary(source_id, errors, raw_data)

        await self._insight_queue.add(
            source=source_id,
            summary=summary,
            details=context,
            priority=priority,
        )

        if self._submit_task_fn:
            await self._submit_task_fn(action)

        if self._episodic_memory:
            await self._store_discovery(action, summary)

        return action

    def _generate_summary(
        self,
        source_id: str,
        errors: dict[str, float],
        raw_data: Any,
    ) -> str:
        """Generate human-readable summary from observation."""
        top_error = max(errors.items(), key=lambda x: x[1]) if errors else None
        if top_error:
            return (
                f"Unexpected change in {source_id}: "
                f"{top_error[0]} deviated by {top_error[1]:.2f}"
            )
        return f"Novel pattern detected in {source_id}"

    async def _store_discovery(self, action: ExplorationAction, summary: str) -> None:
        """Store discovery in episodic memory."""
        if not self._episodic_memory:
            return

        try:
            entry_data = {
                "type": "discovery",
                "source": action.source_id,
                "summary": summary,
                "priority": action.priority,
                "trigger": action.trigger,
            }
            await self._episodic_memory.append(
                "discovery",
                entry_data,
                tags=["curiosity", "proactive"],
            )
        except Exception:
            logger.exception("Failed to store discovery in episodic memory")
