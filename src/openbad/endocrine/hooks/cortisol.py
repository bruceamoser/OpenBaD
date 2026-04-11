"""Cortisol hooks — map sustained stress to cortisol triggers.

Cortisol fires under sustained negative conditions:
- Prolonged high resource utilization
- Repeated task failures
- Persistent high surprise (unresolved anomalies)
- Error accumulation
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.endocrine.controller import EndocrineController


@dataclass
class CortisolEvent:
    """Describes a cortisol-triggering event."""

    source: str
    reason: str
    intensity: float = 1.0


class CortisolHooks:
    """Translates sustained-stress events into cortisol triggers."""

    def __init__(self, controller: EndocrineController) -> None:
        self._controller = controller

    def on_sustained_load(self, duration_seconds: float = 0.0) -> float:
        """Fire cortisol for prolonged high resource utilization."""
        amount = self._controller._config.cortisol.increment  # noqa: SLF001
        if duration_seconds > 60:
            amount *= min(1.0 + duration_seconds / 300.0, 2.0)
        return self._controller.trigger("cortisol", amount)

    def on_repeated_failure(self, failure_count: int = 1) -> float:
        """Fire cortisol from repeated task failures."""
        amount = self._controller._config.cortisol.increment  # noqa: SLF001
        amount *= min(float(failure_count), 3.0)
        return self._controller.trigger("cortisol", amount)

    def on_persistent_surprise(self, surprise_level: float = 0.0) -> float:
        """Fire cortisol when high surprise persists unresolved."""
        amount = self._controller._config.cortisol.increment  # noqa: SLF001
        if surprise_level > 0:
            amount *= min(1.0 + surprise_level, 2.0)
        return self._controller.trigger("cortisol", amount)

    def on_error_accumulation(self, error_count: int = 1) -> float:
        """Fire cortisol from accumulating errors."""
        amount = self._controller._config.cortisol.increment  # noqa: SLF001
        amount *= min(float(error_count), 3.0)
        return self._controller.trigger("cortisol", amount)

    def on_tool_degraded(self, tool_name: str, reason: str = "") -> float:
        """Fire cortisol when a sensory tool transitions to DEGRADED."""
        amount = self._controller._config.cortisol.increment  # noqa: SLF001
        return self._controller.trigger("cortisol", amount)

    def fire(self, event: CortisolEvent) -> float:
        """Generic cortisol trigger from a ``CortisolEvent``."""
        amount = (
            self._controller._config.cortisol.increment  # noqa: SLF001
            * event.intensity
        )
        return self._controller.trigger("cortisol", amount)
