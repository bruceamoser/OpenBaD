"""Endocrine telemetry — observability for the hormone subsystem."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from openbad.endocrine.controller import HORMONES, EndocrineController


@dataclass
class HormoneChangeEvent:
    """A logged change to a hormone level."""

    hormone: str
    old_level: float
    new_level: float
    trigger_event: str
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class HormoneStats:
    """Aggregate statistics for a single hormone over a tracking window."""

    activation_count: int = 0
    escalation_count: int = 0
    total_time_above_threshold: float = 0.0
    last_trigger_time: float | None = None


class EndocrineTelemetry:
    """Collects, logs, and reports on endocrine system activity.

    Wraps an :class:`EndocrineController` and observes every ``trigger``
    call that produces a level change above *min_change_delta*.
    """

    def __init__(
        self,
        controller: EndocrineController,
        *,
        min_change_delta: float = 0.05,
        max_history: int = 200,
    ) -> None:
        self._controller = controller
        self._min_delta = min_change_delta
        self._max_history = max_history
        self._change_log: deque[HormoneChangeEvent] = deque(maxlen=max_history)
        self._stats: dict[str, HormoneStats] = {h: HormoneStats() for h in HORMONES}
        self._last_snapshot: dict[str, float] = {h: 0.0 for h in HORMONES}
        self._last_snapshot_time: float = time.monotonic()

    # -- Recording --------------------------------------------------------- #

    def record_trigger(
        self,
        hormone: str,
        old_level: float,
        new_level: float,
        trigger_event: str = "",
    ) -> None:
        """Log a hormone change if it exceeds the minimum delta."""
        delta = abs(new_level - old_level)
        if delta < self._min_delta:
            return

        evt = HormoneChangeEvent(
            hormone=hormone,
            old_level=old_level,
            new_level=new_level,
            trigger_event=trigger_event,
        )
        self._change_log.append(evt)
        self._stats[hormone].last_trigger_time = evt.timestamp

    def update_activation_stats(self) -> None:
        """Update activation/escalation counters based on current controller state.

        Should be called periodically (e.g. after each decay tick).
        """
        now = time.monotonic()
        dt = now - self._last_snapshot_time

        for hormone in HORMONES:
            stats = self._stats[hormone]
            if self._controller.is_active(hormone):
                stats.total_time_above_threshold += dt
                # Count transitions from below to above threshold.
                cfg = self._controller._hormone_config(hormone)  # noqa: SLF001
                prev = self._last_snapshot[hormone]
                curr = self._controller.level(hormone)
                if prev <= cfg.activation_threshold < curr:
                    stats.activation_count += 1
            if self._controller.is_escalated(hormone):
                cfg = self._controller._hormone_config(hormone)  # noqa: SLF001
                if (
                    cfg.escalation_threshold is not None
                    and self._last_snapshot[hormone]
                    <= cfg.escalation_threshold
                    < self._controller.level(hormone)
                ):
                    stats.escalation_count += 1

            self._last_snapshot[hormone] = self._controller.level(hormone)

        self._last_snapshot_time = now

    # -- Status ------------------------------------------------------------ #

    def status(self) -> dict:
        """Build a status report of the full endocrine system."""
        now = time.monotonic()
        state = self._controller.get_state()
        report: dict = {
            "levels": state.to_dict(),
            "hormones": {},
        }
        for hormone in HORMONES:
            stats = self._stats[hormone]
            entry: dict = {
                "level": getattr(state, hormone),
                "active": self._controller.is_active(hormone),
                "escalated": self._controller.is_escalated(hormone),
                "time_above_threshold": round(stats.total_time_above_threshold, 2),
                "activation_count": stats.activation_count,
                "escalation_count": stats.escalation_count,
            }
            if stats.last_trigger_time is not None:
                entry["seconds_since_last_trigger"] = round(now - stats.last_trigger_time, 2)
            else:
                entry["seconds_since_last_trigger"] = None

            # Recent changes for this hormone.
            entry["recent_changes"] = [
                {
                    "old": round(e.old_level, 4),
                    "new": round(e.new_level, 4),
                    "trigger": e.trigger_event,
                }
                for e in self._change_log
                if e.hormone == hormone
            ][-5:]  # Last 5 changes.

            report["hormones"][hormone] = entry

        return report

    def summary(self) -> dict:
        """Compact summary suitable for MQTT publishing."""
        state = self._controller.get_state()
        return {
            "levels": state.to_dict(),
            "stats": {
                h: {
                    "activations": self._stats[h].activation_count,
                    "escalations": self._stats[h].escalation_count,
                    "time_above": round(self._stats[h].total_time_above_threshold, 2),
                }
                for h in HORMONES
            },
        }

    @property
    def change_log(self) -> list[HormoneChangeEvent]:
        return list(self._change_log)

    def reset(self) -> None:
        """Clear all telemetry data."""
        self._change_log.clear()
        self._stats = {h: HormoneStats() for h in HORMONES}
        self._last_snapshot = {h: 0.0 for h in HORMONES}
        self._last_snapshot_time = time.monotonic()
