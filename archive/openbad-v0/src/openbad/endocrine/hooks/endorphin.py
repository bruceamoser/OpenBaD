"""Endorphin hooks — map recovery/resilience events to endorphin triggers.

Endorphin fires during recovery and resilience:
- System recovery from stress
- Successful self-healing
- Stable operation after turbulence
- Maintenance cycle completion
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.endocrine.controller import EndocrineController


@dataclass
class EndorphinEvent:
    """Describes an endorphin-triggering event."""

    source: str
    reason: str
    intensity: float = 1.0


class EndorphinHooks:
    """Translates recovery/resilience events into endorphin triggers."""

    def __init__(self, controller: EndocrineController) -> None:
        self._controller = controller

    def on_recovery(self, stress_level_before: float = 0.0) -> float:
        """Fire endorphin when the system recovers from stress."""
        amount = self._controller._config.endorphin.increment  # noqa: SLF001
        if stress_level_before > 0:
            amount *= min(1.0 + stress_level_before, 2.0)
        return self._controller.trigger("endorphin", amount)

    def on_self_heal(self) -> float:
        """Fire endorphin on successful self-healing action."""
        amount = (
            self._controller._config.endorphin.increment * 1.5  # noqa: SLF001
        )
        return self._controller.trigger("endorphin", amount)

    def on_stability(self, stable_duration_seconds: float = 0.0) -> float:
        """Fire endorphin from sustained stable operation after turbulence."""
        amount = self._controller._config.endorphin.increment  # noqa: SLF001
        if stable_duration_seconds > 60:
            amount *= min(1.0 + stable_duration_seconds / 600.0, 1.5)
        return self._controller.trigger("endorphin", amount)

    def on_maintenance_complete(self) -> float:
        """Fire endorphin when a maintenance/sleep cycle completes."""
        return self._controller.trigger("endorphin")

    def fire(self, event: EndorphinEvent) -> float:
        """Generic endorphin trigger from an ``EndorphinEvent``."""
        amount = (
            self._controller._config.endorphin.increment  # noqa: SLF001
            * event.intensity
        )
        return self._controller.trigger("endorphin", amount)
