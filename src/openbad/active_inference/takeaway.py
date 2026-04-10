"""Takeaway generator — distills exploration events into actionable insights.

After an exploration cycle, the takeaway generator produces a structured
summary (a "takeaway") that can be published on MQTT for downstream consumers
(e.g. memory consolidation, dashboard, endocrine hooks).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from openbad.active_inference.engine import ExplorationEvent


@dataclass
class Takeaway:
    """A single actionable insight from exploration."""

    source_id: str
    summary: str
    surprise_level: float
    metrics: dict[str, float] = field(default_factory=dict)
    explored: bool = False
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "summary": self.summary,
            "surprise_level": self.surprise_level,
            "metrics": self.metrics,
            "explored": self.explored,
            "timestamp": self.timestamp,
        }


class TakeawayGenerator:
    """Generates takeaways from exploration events.

    A takeaway is produced when surprise exceeds *threshold* or exploration
    was triggered.
    """

    def __init__(self, surprise_threshold: float = 0.3) -> None:
        self._threshold = surprise_threshold
        self._history: list[Takeaway] = []
        self._max_history: int = 100

    @property
    def history(self) -> list[Takeaway]:
        return list(self._history)

    def process(self, events: list[ExplorationEvent]) -> list[Takeaway]:
        """Process a batch of exploration events, return takeaways for notable ones."""
        takeaways: list[Takeaway] = []
        for event in events:
            if event.surprise < self._threshold and not event.explored:
                continue

            summary = self._build_summary(event)
            t = Takeaway(
                source_id=event.source_id,
                summary=summary,
                surprise_level=event.surprise,
                metrics=dict(event.errors),
                explored=event.explored,
            )
            takeaways.append(t)
            self._history.append(t)

        # Trim history.
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        return takeaways

    def clear_history(self) -> None:
        self._history.clear()

    @staticmethod
    def _build_summary(event: ExplorationEvent) -> str:
        parts: list[str] = []
        parts.append(f"[{event.source_id}]")
        parts.append(f"surprise={event.surprise:.2f}")

        if event.explored:
            parts.append("(explored)")

        top_errors = sorted(event.errors.items(), key=lambda kv: kv[1], reverse=True)
        for metric, error in top_errors[:3]:
            parts.append(f"{metric}={error:.2f}")

        return " ".join(parts)
