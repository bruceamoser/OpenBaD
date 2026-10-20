"""Immune-system endocrine monitor — pattern detection and proactive alerting.

Subscribes to ``agent/endocrine/+`` topics and tracks hormone levels in a
sliding window.  When sustained or anomalous patterns are detected, the
monitor raises MQTT alerts, creates investigation tasks, and creates
research entries for novel patterns.

Feedback loop:
    Immune scan results bump cortisol via ``publish_fn`` to close the
    endocrine ↔ immune feedback loop.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Thresholds ───────────────────────────────────────────────────────── #

CORTISOL_SUSTAINED_THRESHOLD: float = 0.60
ADRENALINE_SPIKE_THRESHOLD: float = 0.75
ADRENALINE_SPIKE_COUNT: int = 3
WINDOW_SECONDS: float = 300.0  # 5-minute sliding window

# Minimum seconds between alerts for the same pattern
ALERT_COOLDOWN: float = 60.0

# Cortisol bump applied when immune scan detects a threat
IMMUNE_CORTISOL_BUMP: float = 0.10


# ── Data types ───────────────────────────────────────────────────────── #


@dataclass
class HormoneSample:
    """A single hormone reading."""

    hormone: str
    level: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class PatternAlert:
    """An immune alert raised from endocrine pattern detection."""

    pattern_type: str
    detail: str
    hormone: str
    current_level: float
    window_avg: float
    timestamp: float = field(default_factory=time.time)
    novel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "detail": self.detail,
            "hormone": self.hormone,
            "current_level": self.current_level,
            "window_avg": self.window_avg,
            "timestamp": self.timestamp,
            "novel": self.novel,
        }


# ── Monitor ──────────────────────────────────────────────────────────── #


class EndocrineMonitor:
    """Monitors endocrine state and raises immune alerts on anomalies.

    Parameters
    ----------
    publish_fn:
        ``(topic, payload)`` callback for publishing MQTT events.
    task_store:
        Optional task store with ``create_task(task)`` method.
    research_store:
        Optional research store with ``enqueue(title, description)`` method.
    window_seconds:
        Sliding window length for pattern detection.
    """

    def __init__(
        self,
        *,
        publish_fn: Any | None = None,
        task_store: Any | None = None,
        research_store: Any | None = None,
        window_seconds: float = WINDOW_SECONDS,
    ) -> None:
        self._publish_fn = publish_fn
        self._task_store = task_store
        self._research_store = research_store
        self._window = window_seconds
        self._samples: dict[str, deque[HormoneSample]] = {}
        self._last_alert: dict[str, float] = {}
        self._seen_patterns: set[str] = set()
        self._alerts: list[PatternAlert] = []

    @property
    def alerts(self) -> list[PatternAlert]:
        """All alerts raised by this monitor instance."""
        return list(self._alerts)

    # ── Ingest ────────────────────────────────────────────────────────── #

    def record_sample(
        self, hormone: str, level: float, ts: float | None = None,
    ) -> list[PatternAlert]:
        """Record a hormone sample and check for patterns.

        Returns any alerts triggered by this sample.
        """
        now = ts or time.time()
        sample = HormoneSample(hormone=hormone, level=level, timestamp=now)

        if hormone not in self._samples:
            self._samples[hormone] = deque()
        buf = self._samples[hormone]
        buf.append(sample)

        # Prune old samples outside the window
        cutoff = now - self._window
        while buf and buf[0].timestamp < cutoff:
            buf.popleft()

        return self._check_patterns(hormone, level, now)

    def on_mqtt_message(
        self, topic: str, payload: bytes,
    ) -> list[PatternAlert]:
        """Handle an MQTT message from ``agent/endocrine/+``.

        Parses the payload as JSON ``{"level": float}`` or raw float.
        """
        # Extract hormone name from topic
        parts = topic.split("/")
        if len(parts) < 3:
            return []
        hormone = parts[-1]

        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                level = float(data.get("level", data.get("value", 0.0)))
            else:
                level = float(data)
        except (json.JSONDecodeError, ValueError, TypeError):
            try:
                level = float(payload.decode())
            except (ValueError, UnicodeDecodeError):
                return []

        return self.record_sample(hormone, level)

    # ── Immune feedback ───────────────────────────────────────────────── #

    def on_threat_detected(self, threat_type: str) -> None:
        """Feed immune scan results back to endocrine as cortisol bump.

        Called when the interceptor blocks a message.
        """
        if self._publish_fn is not None:
            bump_payload = json.dumps({
                "level_delta": IMMUNE_CORTISOL_BUMP,
                "source": "immune",
                "reason": f"Threat detected: {threat_type}",
            }).encode()
            self._publish_fn(
                "agent/endocrine/cortisol/bump", bump_payload,
            )
            logger.info(
                "Immune→endocrine cortisol bump (+%.2f) for %s",
                IMMUNE_CORTISOL_BUMP,
                threat_type,
            )

    # ── Pattern detection ─────────────────────────────────────────────── #

    def _check_patterns(
        self, hormone: str, level: float, now: float,
    ) -> list[PatternAlert]:
        """Check for known anomalous patterns."""
        alerts: list[PatternAlert] = []

        if hormone == "cortisol":
            alert = self._check_sustained_cortisol(level, now)
            if alert:
                alerts.append(alert)

        if hormone == "adrenaline":
            alert = self._check_adrenaline_spikes(level, now)
            if alert:
                alerts.append(alert)

        # Generic anomaly: any hormone above 0.90
        if level >= 0.90:
            alert = self._check_extreme_level(hormone, level, now)
            if alert:
                alerts.append(alert)

        return alerts

    def _check_sustained_cortisol(
        self, level: float, now: float,
    ) -> PatternAlert | None:
        """Detect sustained high cortisol across the window."""
        buf = self._samples.get("cortisol")
        if not buf or len(buf) < 3:
            return None

        avg = sum(s.level for s in buf) / len(buf)
        if avg < CORTISOL_SUSTAINED_THRESHOLD:
            return None

        return self._raise_alert(
            pattern_type="sustained_cortisol",
            detail=(
                f"Average cortisol {avg:.2f} over {len(buf)} samples "
                f"exceeds threshold {CORTISOL_SUSTAINED_THRESHOLD}"
            ),
            hormone="cortisol",
            current_level=level,
            window_avg=avg,
            now=now,
        )

    def _check_adrenaline_spikes(
        self, level: float, now: float,
    ) -> PatternAlert | None:
        """Detect repeated adrenaline spikes in the window."""
        buf = self._samples.get("adrenaline")
        if not buf:
            return None

        spike_count = sum(
            1 for s in buf if s.level >= ADRENALINE_SPIKE_THRESHOLD
        )
        if spike_count < ADRENALINE_SPIKE_COUNT:
            return None

        avg = sum(s.level for s in buf) / len(buf)
        return self._raise_alert(
            pattern_type="repeated_adrenaline_spikes",
            detail=(
                f"{spike_count} adrenaline spikes ≥{ADRENALINE_SPIKE_THRESHOLD} "
                f"in {self._window}s window"
            ),
            hormone="adrenaline",
            current_level=level,
            window_avg=avg,
            now=now,
        )

    def _check_extreme_level(
        self, hormone: str, level: float, now: float,
    ) -> PatternAlert | None:
        """Alert on any hormone at extreme (≥0.90) level."""
        buf = self._samples.get(hormone, deque())
        avg = sum(s.level for s in buf) / len(buf) if buf else level

        return self._raise_alert(
            pattern_type=f"extreme_{hormone}",
            detail=f"{hormone} at extreme level {level:.2f}",
            hormone=hormone,
            current_level=level,
            window_avg=avg,
            now=now,
        )

    # ── Alert raising ─────────────────────────────────────────────────── #

    def _raise_alert(
        self,
        *,
        pattern_type: str,
        detail: str,
        hormone: str,
        current_level: float,
        window_avg: float,
        now: float,
    ) -> PatternAlert | None:
        """Create and dispatch an alert if not in cooldown."""
        last = self._last_alert.get(pattern_type, 0.0)
        if now - last < ALERT_COOLDOWN:
            return None

        novel = pattern_type not in self._seen_patterns
        self._seen_patterns.add(pattern_type)
        self._last_alert[pattern_type] = now

        alert = PatternAlert(
            pattern_type=pattern_type,
            detail=detail,
            hormone=hormone,
            current_level=current_level,
            window_avg=window_avg,
            timestamp=now,
            novel=novel,
        )
        self._alerts.append(alert)

        # Publish MQTT alert
        if self._publish_fn is not None:
            self._publish_fn(
                "agent/immune/alert",
                json.dumps(alert.to_dict()).encode(),
            )

        # Create investigation task
        if self._task_store is not None:
            try:
                from openbad.tasks.models import (
                    TaskKind,
                    TaskModel,
                    TaskPriority,
                )

                task = TaskModel.new(
                    title=f"Immune: {pattern_type}",
                    description=detail,
                    kind=TaskKind.SYSTEM,
                    priority=int(TaskPriority.NORMAL),
                    owner="immune-monitor",
                )
                self._task_store.create_task(task)
            except Exception:
                logger.exception("Failed to create immune task")

        # Create research entry for novel patterns
        if novel and self._research_store is not None:
            try:
                self._research_store.enqueue(
                    f"Investigate novel pattern: {pattern_type}",
                    description=detail,
                )
            except Exception:
                logger.exception("Failed to create immune research")

        logger.warning(
            "Immune alert: %s — %s (novel=%s)", pattern_type, detail, novel,
        )
        return alert
