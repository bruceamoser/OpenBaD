"""Proprioception readiness gate — blocks until belt minimum is met.

The cognitive event loop should call ``gate.wait_ready()`` before
accepting requests.  If the timeout expires, the agent starts in
degraded mode.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from enum import Enum

from openbad.nervous_system.topics import TELEMETRY_READINESS
from openbad.proprioception.registry import ToolRegistry, ToolRole

logger = logging.getLogger(__name__)


class ReadinessStatus(Enum):
    WAITING = "waiting"
    READY = "ready"
    DEGRADED = "degraded"


class ReadinessGate:
    """Block until the belt has the required roles equipped and healthy.

    Parameters
    ----------
    registry:
        The tool registry to check belt status against.
    required_roles:
        Roles that must be equipped for the gate to pass.
    timeout:
        Seconds to wait before starting in degraded mode.
    publish_fn:
        Optional ``(topic, payload)`` publisher for readiness events.
    cortisol_hook:
        Optional callback fired on degraded-mode start.
    poll_interval:
        Seconds between belt checks while waiting.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        required_roles: list[ToolRole] | None = None,
        timeout: float = 30.0,
        publish_fn: Callable[[str, bytes], None] | None = None,
        cortisol_hook: Callable[[], float] | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self._registry = registry
        self._required = set(required_roles or [])
        self._timeout = timeout
        self._publish_fn = publish_fn
        self._cortisol_hook = cortisol_hook
        self._poll_interval = poll_interval
        self._status = ReadinessStatus.WAITING
        self._lock = threading.Lock()

    @property
    def status(self) -> ReadinessStatus:
        with self._lock:
            return self._status

    def check(self) -> bool:
        """Return ``True`` if all required roles are equipped."""
        belt = self._registry.get_belt()
        return self._required.issubset(belt.keys())

    def wait_ready(self) -> ReadinessStatus:
        """Block until ready or timeout.

        Returns the final status: ``READY`` or ``DEGRADED``.
        """
        self._set_status(ReadinessStatus.WAITING)

        if not self._required:
            self._set_status(ReadinessStatus.READY)
            return ReadinessStatus.READY

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if self.check():
                self._set_status(ReadinessStatus.READY)
                return ReadinessStatus.READY
            time.sleep(self._poll_interval)

        # Timeout — degraded mode
        self._set_status(ReadinessStatus.DEGRADED)
        if self._cortisol_hook is not None:
            try:
                self._cortisol_hook()
            except Exception:
                logger.exception("Cortisol hook failed on readiness timeout")
        return ReadinessStatus.DEGRADED

    def _set_status(self, status: ReadinessStatus) -> None:
        with self._lock:
            self._status = status
        self._publish_status(status)

    def _publish_status(self, status: ReadinessStatus) -> None:
        if self._publish_fn is None:
            return
        payload = json.dumps({"status": status.value}).encode()
        try:
            self._publish_fn(TELEMETRY_READINESS, payload)
        except Exception:
            logger.exception("Failed to publish readiness status")
