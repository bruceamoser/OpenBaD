"""Security lockdown reflex handler.

Subscribes to ``agent/immune/alert`` and reacts to **critical** immune
alerts by isolating the flagged subsystem, blocking external tool
invocations, transitioning the FSM to EMERGENCY, and publishing a
:class:`ReflexResult`.

Recovery requires explicit operator clearance — the handler cannot
auto-recover.

Pure Python — no LLM calls, no external service dependencies.
"""

from __future__ import annotations

import logging
import threading
import time

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult

logger = logging.getLogger(__name__)

# Topics
IMMUNE_ALERT_TOPIC = "agent/immune/alert"
RESULT_TOPIC = "agent/reflex/security/result"

# Severity enum value for CRITICAL in the proto
_CRITICAL = 3


class SecurityGuard:
    """Thread-safe security lockdown controller.

    Parameters
    ----------
    fsm:
        Optional :class:`AgentFSM` instance — fires ``emergency`` trigger.
    publish_fn:
        Optional callable ``(topic, data)`` for publishing results.
    """

    def __init__(
        self,
        fsm: object | None = None,
        publish_fn: callable | None = None,  # type: ignore[valid-type]
    ) -> None:
        self._lock = threading.Lock()
        self._locked_down = False
        self._isolated_sources: set[str] = set()
        self._fsm = fsm
        self._publish_fn = publish_fn

    # -- public state -------------------------------------------------------

    @property
    def locked_down(self) -> bool:
        return self._locked_down

    @property
    def isolated_sources(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._isolated_sources)

    def is_tool_allowed(self, source_id: str | None = None) -> bool:
        """Return ``False`` if external tool invocations are blocked.

        If *source_id* is given, also checks whether that specific
        subsystem is isolated.
        """
        if self._locked_down:
            return False
        return not (source_id and source_id in self._isolated_sources)

    # -- event handling -----------------------------------------------------

    def handle_alert(self, payload: bytes) -> bool:
        """Evaluate an immune alert and trigger lockdown if critical.

        Returns ``True`` if the handler fired.
        """
        alert = ImmuneAlert()
        alert.ParseFromString(payload)

        if alert.severity != _CRITICAL:
            return False

        self._lockdown(alert)
        return True

    # -- recovery -----------------------------------------------------------

    def clear(self, operator: str = "operator") -> bool:
        """Explicit operator clearance required to lift the lockdown.

        Returns ``True`` if state actually changed.
        """
        with self._lock:
            if not self._locked_down:
                return False
            self._locked_down = False
            self._isolated_sources.clear()

        logger.info("Security lockdown cleared by %s", operator)
        return True

    # -- MQTT integration ---------------------------------------------------

    def subscribe(self, client: object) -> None:
        """Subscribe to immune alert events via an MQTT *client*."""

        def _callback(_topic: str, payload: bytes) -> None:
            self.handle_alert(payload)

        client.subscribe(IMMUNE_ALERT_TOPIC, _callback)  # type: ignore[union-attr]

    # -- internals ----------------------------------------------------------

    def _lockdown(self, alert: ImmuneAlert) -> None:
        with self._lock:
            self._locked_down = True
            if alert.source_id:
                self._isolated_sources.add(alert.source_id)

        # FSM → EMERGENCY
        if self._fsm is not None:
            try:
                self._fsm.fire("emergency")  # type: ignore[union-attr]
            except Exception:
                logger.exception("FSM emergency trigger failed")

        # Publish result
        if self._publish_fn is not None:
            result = ReflexResult(
                header=Header(timestamp_unix=time.time()),
                reflex_id="security_lockdown",
                handled=True,
                action_taken=(
                    f"Locked down: isolated source={alert.source_id!r}, "
                    f"threat={alert.threat_type!r}"
                ),
            )
            try:
                self._publish_fn(RESULT_TOPIC, result.SerializeToString())
            except Exception:
                logger.exception("Failed to publish security result")

        logger.warning(
            "Security lockdown: source=%s threat=%s",
            alert.source_id,
            alert.threat_type,
        )
