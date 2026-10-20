"""Adrenaline hooks — map urgent/threat events to adrenaline triggers.

Adrenaline fires when the system faces urgency or threat:
- High surprise (unexpected anomaly)
- Security threat detected
- Resource critical (CPU/memory spike)
- Deadline pressure
"""

from __future__ import annotations

from dataclasses import dataclass

from openbad.endocrine.controller import EndocrineController


@dataclass
class AdrenalineEvent:
    """Describes an adrenaline-triggering event."""

    source: str
    reason: str
    intensity: float = 1.0


class AdrenalineHooks:
    """Translates urgency/threat events into adrenaline triggers."""

    def __init__(self, controller: EndocrineController) -> None:
        self._controller = controller

    def on_high_surprise(self, surprise_level: float = 0.0) -> float:
        """Fire adrenaline when surprise exceeds threshold."""
        amount = self._controller._config.adrenaline.increment  # noqa: SLF001
        if surprise_level > 0:
            amount *= min(1.0 + surprise_level, 2.0)
        return self._controller.trigger("adrenaline", amount)

    def on_security_threat(self, severity: float = 1.0) -> float:
        """Fire adrenaline for a security threat detection."""
        amount = (
            self._controller._config.adrenaline.increment  # noqa: SLF001
            * min(severity, 3.0)
        )
        return self._controller.trigger("adrenaline", amount)

    def on_resource_critical(self, utilization: float = 0.0) -> float:
        """Fire adrenaline when a resource hits critical utilization."""
        amount = self._controller._config.adrenaline.increment  # noqa: SLF001
        if utilization > 0.9:
            amount *= 1.5
        return self._controller.trigger("adrenaline", amount)

    def on_deadline_pressure(self) -> float:
        """Fire adrenaline from deadline/time pressure."""
        return self._controller.trigger("adrenaline")

    def fire(self, event: AdrenalineEvent) -> float:
        """Generic adrenaline trigger from an ``AdrenalineEvent``."""
        amount = (
            self._controller._config.adrenaline.increment  # noqa: SLF001
            * event.intensity
        )
        return self._controller.trigger("adrenaline", amount)
