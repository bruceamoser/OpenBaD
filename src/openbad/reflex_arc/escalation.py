"""Escalation gateway — System 1 (reflex) → System 2 (cognitive).

Routes problems that deterministic reflex handlers cannot resolve to the
cognitive engine.  Each escalation carries a unique correlation ID, full
context, and state-flap detection.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from openbad.nervous_system.schemas.cognitive_pb2 import EscalationRequest
from openbad.nervous_system.schemas.common_pb2 import Header

logger = logging.getLogger(__name__)

ESCALATION_TOPIC = "agent/cognitive/escalation"

# Default flap detection: N transitions in T seconds
_DEFAULT_FLAP_THRESHOLD = 3
_DEFAULT_FLAP_WINDOW = 10.0


@dataclass(frozen=True)
class EscalationContext:
    """Context bundle sent with each escalation."""

    correlation_id: str
    event_topic: str
    event_payload: bytes
    reason: str
    reflex_id: str
    current_state: str
    telemetry_snapshot: dict = field(default_factory=dict)


class EscalationGateway:
    """Routes unresolved reflex events to the cognitive engine.

    Parameters
    ----------
    publish_fn:
        Callable ``(topic, data)`` used to publish escalation messages.
    flap_threshold:
        Number of state transitions within *flap_window* that triggers
        a flap-based escalation.
    flap_window:
        Time window in seconds for flap detection.
    """

    def __init__(
        self,
        publish_fn: callable | None = None,  # type: ignore[valid-type]
        flap_threshold: int = _DEFAULT_FLAP_THRESHOLD,
        flap_window: float = _DEFAULT_FLAP_WINDOW,
    ) -> None:
        self._publish_fn = publish_fn
        self._flap_threshold = flap_threshold
        self._flap_window = flap_window
        self._lock = threading.Lock()
        self._transitions: deque[float] = deque()
        self._escalation_log: list[EscalationContext] = []

    # -- public API ---------------------------------------------------------

    def escalate(
        self,
        event_topic: str,
        event_payload: bytes,
        reason: str,
        reflex_id: str = "",
        current_state: str = "",
        telemetry_snapshot: dict | None = None,
        priority: int = 0,
    ) -> EscalationContext:
        """Create and publish an escalation request.

        Returns the :class:`EscalationContext` with its unique
        ``correlation_id``.
        """
        ctx = EscalationContext(
            correlation_id=str(uuid.uuid4()),
            event_topic=event_topic,
            event_payload=event_payload,
            reason=reason,
            reflex_id=reflex_id,
            current_state=current_state,
            telemetry_snapshot=telemetry_snapshot or {},
        )

        # Build protobuf
        envelope = json.dumps(
            {
                "correlation_id": ctx.correlation_id,
                "telemetry_snapshot": ctx.telemetry_snapshot,
                "current_state": ctx.current_state,
            }
        ).encode()

        msg = EscalationRequest(
            header=Header(timestamp_unix=time.time()),
            event_topic=ctx.event_topic,
            event_payload=envelope,
            reason=ctx.reason,
            priority=priority,
            reflex_id=ctx.reflex_id,
        )

        if self._publish_fn is not None:
            try:
                self._publish_fn(ESCALATION_TOPIC, msg.SerializeToString())
            except Exception:
                logger.exception("Failed to publish escalation")

        with self._lock:
            self._escalation_log.append(ctx)

        logger.info(
            "Escalation %s: %s (reflex=%s)",
            ctx.correlation_id,
            ctx.reason,
            ctx.reflex_id,
        )
        return ctx

    # -- flap detection -----------------------------------------------------

    def record_transition(self, state_from: str, state_to: str) -> bool:
        """Record an FSM state transition. Returns True if flapping detected.

        If flapping is detected, an escalation is automatically published.
        """
        now = time.time()
        with self._lock:
            self._transitions.append(now)
            # Trim old entries outside the window
            cutoff = now - self._flap_window
            while self._transitions and self._transitions[0] < cutoff:
                self._transitions.popleft()
            count = len(self._transitions)

        if count >= self._flap_threshold:
            self.escalate(
                event_topic="agent/reflex/state",
                event_payload=json.dumps({"from": state_from, "to": state_to}).encode(),
                reason=(f"State flapping detected: {count} transitions in {self._flap_window}s"),
                reflex_id="flap_detector",
                current_state=state_to,
            )
            return True
        return False

    # -- introspection ------------------------------------------------------

    @property
    def escalation_log(self) -> list[EscalationContext]:
        with self._lock:
            return list(self._escalation_log)
