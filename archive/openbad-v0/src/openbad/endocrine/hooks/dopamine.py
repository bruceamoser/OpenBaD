"""Dopamine hooks — map positive outcomes to dopamine triggers.

Dopamine fires when the system achieves something positive:
- Task completion
- Successful exploration (surprise resolved)
- Positive user feedback
- Learning (world model improvement)
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.endocrine.controller import EndocrineController


@dataclass
class DopamineEvent:
    """Describes a dopamine-triggering event."""

    source: str
    reason: str
    intensity: float = 1.0  # multiplier on configured increment


class DopamineHooks:
    """Translates positive-outcome events into dopamine triggers."""

    def __init__(self, controller: EndocrineController) -> None:
        self._controller = controller

    def on_task_complete(self, task_id: str = "") -> float:
        """Fire dopamine for a completed task."""
        return self._controller.trigger("dopamine")

    def on_exploration_success(self, surprise_delta: float = 0.0) -> float:
        """Fire dopamine when exploration resolves surprise."""
        amount = self._controller._config.dopamine.increment  # noqa: SLF001
        if surprise_delta > 0:
            amount *= min(1.0 + surprise_delta, 2.0)
        return self._controller.trigger("dopamine", amount)

    def on_positive_feedback(self) -> float:
        """Fire dopamine from positive user feedback."""
        amount = self._controller._config.dopamine.increment * 1.5  # noqa: SLF001
        return self._controller.trigger("dopamine", amount)

    def on_learning(self, improvement: float = 0.0) -> float:
        """Fire dopamine when the world model improves."""
        amount = self._controller._config.dopamine.increment  # noqa: SLF001
        if improvement > 0:
            amount *= min(1.0 + improvement, 1.5)
        return self._controller.trigger("dopamine", amount)

    def fire(self, event: DopamineEvent) -> float:
        """Generic dopamine trigger from a ``DopamineEvent``."""
        amount = (
            self._controller._config.dopamine.increment * event.intensity  # noqa: SLF001
        )
        return self._controller.trigger("dopamine", amount)
