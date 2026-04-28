"""Observation plugin for external inbound signals (Corsair webhooks)."""

from __future__ import annotations

import threading

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class ExternalSignalPlugin(ObservationPlugin):
    """Tracks inbound external messages from Corsair webhook bridge.

    The daemon's ``_on_external_inbound`` handler calls :meth:`record` for
    every message received on ``sensory/external/+/inbound``.  Each
    :meth:`observe` call returns the count accumulated since the last
    observation and resets the counter.

    Default prediction is **0 messages per poll interval** so any inbound
    traffic produces surprise, which feeds the explore pipeline.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count: int = 0

    # -- recording (called from daemon MQTT handler) -------------------- #

    def record(self) -> None:
        """Increment the inbound message counter (thread-safe)."""
        with self._lock:
            self._count += 1

    # -- ObservationPlugin ABC ------------------------------------------ #

    @property
    def source_id(self) -> str:
        return "external_signals"

    async def observe(self) -> ObservationResult:
        """Return message count since last observation, then reset."""
        with self._lock:
            count = self._count
            self._count = 0
        return ObservationResult(metrics={"message_count": count})

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {"message_count": {"expected": 0.0, "tolerance": 1.0}}
