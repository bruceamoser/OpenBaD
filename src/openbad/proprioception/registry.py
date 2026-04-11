"""Proprioception registry — live tool/subsystem tracking with heartbeat.

Each registered tool must publish periodic heartbeats.  If a heartbeat is
not received within the configurable timeout window the tool is marked
``UNAVAILABLE``.  On every status transition a registry snapshot is
published to ``agent/proprioception/state``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

HEARTBEAT_TOPIC_PREFIX = "agent/proprioception/"
HEARTBEAT_TOPIC_SUFFIX = "/heartbeat"
STATE_TOPIC = "agent/proprioception/state"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class HealthStatus(Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ToolStatus:
    """Live status entry for a single tool / subsystem."""

    name: str
    status: HealthStatus = HealthStatus.AVAILABLE
    last_heartbeat: float = field(default_factory=time.time)
    metadata: dict[str, str] = field(default_factory=dict)
    health_check: Callable[[], bool] | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Thread-safe live tool registry with heartbeat-based liveness.

    Parameters
    ----------
    timeout:
        Seconds after which a tool with no heartbeat is UNAVAILABLE.
    publish_fn:
        Optional callable ``(topic, payload)`` used to emit snapshots.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        publish_fn: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._timeout = timeout
        self._publish_fn = publish_fn
        self._lock = threading.Lock()
        self._tools: dict[str, ToolStatus] = {}
        self._reaper: _ReaperThread | None = None

    # -- registration -------------------------------------------------------

    def register(
        self,
        name: str,
        metadata: dict[str, str] | None = None,
        health_check: Callable[[], bool] | None = None,
    ) -> ToolStatus:
        """Register a tool/subsystem.  Idempotent for the same *name*."""
        with self._lock:
            if name in self._tools:
                entry = self._tools[name]
                entry.status = HealthStatus.AVAILABLE
                entry.last_heartbeat = time.time()
                if metadata:
                    entry.metadata.update(metadata)
                if health_check is not None:
                    entry.health_check = health_check
            else:
                entry = ToolStatus(
                    name=name,
                    metadata=metadata or {},
                    health_check=health_check,
                )
                self._tools[name] = entry
        self._emit_snapshot()
        return entry

    def unregister(self, name: str) -> bool:
        """Remove a tool.  Returns ``True`` if it existed."""
        with self._lock:
            removed = self._tools.pop(name, None) is not None
        if removed:
            self._emit_snapshot()
        return removed

    # -- heartbeat ----------------------------------------------------------

    def heartbeat(self, name: str) -> None:
        """Record a heartbeat for *name*.

        If the tool was previously UNAVAILABLE it transitions back to
        AVAILABLE and a snapshot is published.
        """
        changed = False
        with self._lock:
            entry = self._tools.get(name)
            if entry is None:
                return
            entry.last_heartbeat = time.time()
            if entry.status is not HealthStatus.AVAILABLE:
                entry.status = HealthStatus.AVAILABLE
                changed = True
        if changed:
            self._emit_snapshot()

    def mark_degraded(self, name: str, reason: str = "") -> bool:
        """Transition a tool to DEGRADED status.

        Returns ``True`` if the status actually changed.
        """
        changed = False
        with self._lock:
            entry = self._tools.get(name)
            if entry is None:
                return False
            if entry.status is not HealthStatus.DEGRADED:
                entry.status = HealthStatus.DEGRADED
                if reason:
                    entry.metadata["degraded_reason"] = reason
                changed = True
        if changed:
            self._emit_snapshot()
        return changed

    def run_health_checks(self) -> list[str]:
        """Run registered health checks and mark failing tools DEGRADED.

        Returns list of tool names that transitioned to DEGRADED.
        """
        degraded: list[str] = []
        with self._lock:
            tools_to_check = [
                (t.name, t.health_check)
                for t in self._tools.values()
                if t.health_check is not None and t.status is not HealthStatus.UNAVAILABLE
            ]
        for name, check in tools_to_check:
            try:
                healthy = check()
            except Exception:
                logger.exception("Health check failed for %s", name)
                healthy = False
            if not healthy:
                if self.mark_degraded(name, reason="health check failed"):
                    degraded.append(name)
            else:
                # Recover from DEGRADED if check passes
                with self._lock:
                    entry = self._tools.get(name)
                    if entry and entry.status is HealthStatus.DEGRADED:
                        entry.status = HealthStatus.AVAILABLE
                        degraded_cleared = True
                    else:
                        degraded_cleared = False
                if degraded_cleared:
                    self._emit_snapshot()
        return degraded

    # -- querying -----------------------------------------------------------

    def get_available_tools(self) -> list[ToolStatus]:
        """Return tools currently healthy (AVAILABLE)."""
        with self._lock:
            return [t for t in self._tools.values() if t.status is HealthStatus.AVAILABLE]

    def get_all_tools(self) -> list[ToolStatus]:
        """Return all registered tools regardless of status."""
        with self._lock:
            return list(self._tools.values())

    # -- staleness reaper ---------------------------------------------------

    def reap_stale(self) -> int:
        """Mark tools whose heartbeat is older than *timeout* as UNAVAILABLE.

        Returns the number of tools whose status changed.
        """
        now = time.time()
        changed = 0
        with self._lock:
            for entry in self._tools.values():
                if (
                    entry.status is HealthStatus.AVAILABLE
                    and now - entry.last_heartbeat > self._timeout
                ):
                    entry.status = HealthStatus.UNAVAILABLE
                    changed += 1
        if changed:
            self._emit_snapshot()
        return changed

    # -- MQTT integration ---------------------------------------------------

    def subscribe_heartbeats(self, client: object) -> None:
        """Subscribe to heartbeat topics via an MQTT *client*.

        Expects ``client.subscribe(topic, callback)`` where callback
        receives ``(topic, payload)``.
        """
        topic = HEARTBEAT_TOPIC_PREFIX + "+/heartbeat"
        client.subscribe(topic, self._on_heartbeat_message)  # type: ignore[union-attr]

    def _on_heartbeat_message(self, topic: str, _payload: bytes) -> None:
        """Handle an incoming heartbeat MQTT message."""
        # topic: agent/proprioception/<tool_id>/heartbeat
        parts = topic.split("/")
        if len(parts) >= 3:  # noqa: PLR2004
            tool_id = parts[2]
            self.heartbeat(tool_id)

    # -- background reaper --------------------------------------------------

    def start_reaper(self, interval: float | None = None) -> None:
        """Start a background daemon thread that calls :meth:`reap_stale`."""
        if self._reaper is not None:
            return
        self._reaper = _ReaperThread(
            registry=self,
            interval=interval or self._timeout / 2,
        )
        self._reaper.start()

    def stop_reaper(self) -> None:
        """Stop the background reaper thread."""
        if self._reaper is not None:
            self._reaper.stop()
            self._reaper = None

    # -- snapshot -----------------------------------------------------------

    def snapshot(self) -> list[dict]:
        """Return a JSON-serialisable snapshot of all tools."""
        with self._lock:
            return [
                {
                    "name": t.name,
                    "status": t.status.value,
                    "last_heartbeat": t.last_heartbeat,
                    "metadata": t.metadata,
                }
                for t in self._tools.values()
            ]

    def _emit_snapshot(self) -> None:
        if self._publish_fn is not None:
            try:
                data = json.dumps(self.snapshot()).encode()
                self._publish_fn(STATE_TOPIC, data)
            except Exception:
                logger.exception("Failed to publish registry snapshot")


# ---------------------------------------------------------------------------
# Reaper thread
# ---------------------------------------------------------------------------


class _ReaperThread(threading.Thread):
    """Daemon thread that periodically calls ``registry.reap_stale()``."""

    def __init__(self, registry: ToolRegistry, interval: float) -> None:
        super().__init__(daemon=True)
        self._registry = registry
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._registry.reap_stale()
            self._stop_event.wait(self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=self._interval * 2)
