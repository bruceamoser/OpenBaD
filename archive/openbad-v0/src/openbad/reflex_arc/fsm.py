"""Finite state machine for agent operational states.

Uses the ``transitions`` library (MIT) to model the agent lifecycle:

    IDLE → ACTIVE → THROTTLED → SLEEP → EMERGENCY

Transitions are triggered by event-bus messages and publish the new
state to ``agent/reflex/state`` after every transition.
"""

from __future__ import annotations

import logging
import threading
import time

from transitions import Machine, MachineError  # type: ignore[import-untyped]

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert
from openbad.nervous_system.schemas.reflex_pb2 import ReflexState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATES = [
    "IDLE",
    "ACTIVE",
    "RESEARCHING",
    "EXECUTING_TASK",
    "DIAGNOSING",
    "THROTTLED",
    "SLEEP",
    "EMERGENCY",
]

# States where the agent is actively doing work and should not accept
# new overlapping work requests.
BUSY_STATES: frozenset[str] = frozenset({
    "RESEARCHING",
    "EXECUTING_TASK",
    "DIAGNOSING",
})

TRANSITIONS = [
    # Normal flow
    {"trigger": "activate", "source": "IDLE", "dest": "ACTIVE"},
    {"trigger": "deactivate", "source": "ACTIVE", "dest": "IDLE"},
    # Work states — entered when the agent begins a blocking work unit
    {"trigger": "begin_research", "source": "IDLE", "dest": "RESEARCHING"},
    {"trigger": "begin_research", "source": "ACTIVE", "dest": "RESEARCHING"},
    {"trigger": "begin_task", "source": "IDLE", "dest": "EXECUTING_TASK"},
    {"trigger": "begin_task", "source": "ACTIVE", "dest": "EXECUTING_TASK"},
    {"trigger": "begin_diagnose", "source": "IDLE", "dest": "DIAGNOSING"},
    {"trigger": "begin_diagnose", "source": "ACTIVE", "dest": "DIAGNOSING"},
    {"trigger": "complete_work", "source": "RESEARCHING", "dest": "IDLE"},
    {"trigger": "complete_work", "source": "EXECUTING_TASK", "dest": "IDLE"},
    {"trigger": "complete_work", "source": "DIAGNOSING", "dest": "IDLE"},
    # Throttle
    {"trigger": "throttle", "source": "ACTIVE", "dest": "THROTTLED"},
    {"trigger": "throttle", "source": "IDLE", "dest": "THROTTLED"},
    {"trigger": "recover_throttle", "source": "THROTTLED", "dest": "IDLE"},
    # Sleep / consolidation
    {"trigger": "sleep", "source": "ACTIVE", "dest": "SLEEP"},
    {"trigger": "sleep", "source": "IDLE", "dest": "SLEEP"},
    {"trigger": "wake", "source": "SLEEP", "dest": "IDLE"},
    # Emergency — overrides any state including busy states
    {"trigger": "emergency", "source": "*", "dest": "EMERGENCY"},
    {"trigger": "recover_emergency", "source": "EMERGENCY", "dest": "IDLE"},
]

# Map event-bus topics/conditions to FSM trigger names.
TOPIC_TRIGGER_MAP: dict[str, str] = {
    "agent/endocrine/cortisol": "throttle",
    "agent/endocrine/adrenaline": "emergency",
    "agent/endocrine/endorphin": "sleep",
    "agent/immune/alert": "emergency",
}

TOPIC_MESSAGE_MAP: dict[str, type[EndocrineEvent | ImmuneAlert]] = {
    "agent/endocrine/cortisol": EndocrineEvent,
    "agent/endocrine/adrenaline": EndocrineEvent,
    "agent/endocrine/endorphin": EndocrineEvent,
    "agent/immune/alert": ImmuneAlert,
}


# ---------------------------------------------------------------------------
# AgentFSM
# ---------------------------------------------------------------------------


class AgentFSM:
    """Thread-safe FSM governing the agent's operational state.

    Parameters
    ----------
    client:
        Optional :class:`NervousSystemClient` used to subscribe to
        trigger topics and publish state transitions.  When *None*,
        the FSM works standalone (useful for testing).
    """

    def __init__(self, client: object | None = None) -> None:
        self._lock = threading.Lock()
        self._client = client
        self.state: str = "IDLE"  # set by transitions library internally
        self._work_deadline: float = 0.0  # monotonic deadline for busy states

        self._machine = Machine(
            model=self,
            states=STATES,
            transitions=TRANSITIONS,
            initial="IDLE",
            send_event=True,
            after_state_change="on_state_change",
        )

    # ------------------------------------------------------------------
    # Busy-state helpers
    # ------------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """Return *True* if the agent is in a work state."""
        return self.state in BUSY_STATES

    def try_begin_work(self, trigger: str, *, timeout_seconds: float = 300.0) -> bool:
        """Attempt to enter a busy state.

        If the FSM is already busy or the transition is invalid, return
        *False*.  On success, a monotonic deadline is recorded so that
        :meth:`check_work_timeout` can recover stuck work states.
        """
        with self._lock:
            if self.state in BUSY_STATES:
                return False
            try:
                self._machine.events[trigger].trigger(self)
            except Exception:
                return False
            self._work_deadline = time.monotonic() + max(30.0, timeout_seconds)
            return True

    def finish_work(self) -> bool:
        """Leave the current busy state, returning to IDLE.

        Safe to call even when not in a busy state (returns *False*).
        """
        self._work_deadline = 0.0
        return self.fire("complete_work")

    def check_work_timeout(self) -> bool:
        """If the agent has been busy past its deadline, force recovery.

        Returns *True* if a timeout recovery occurred.
        """
        if not self.is_busy:
            return False
        if self._work_deadline and time.monotonic() > self._work_deadline:
            logger.warning(
                "FSM: work state %s exceeded deadline — forcing finish_work",
                self.state,
            )
            self._work_deadline = 0.0
            return self.fire("complete_work")
        return False

    # ------------------------------------------------------------------
    # State-change callback
    # ------------------------------------------------------------------

    def on_state_change(self, event: object) -> None:
        """Called by *transitions* after every state change."""
        # ``event`` is a transitions.EventData instance
        src = getattr(event, "transition", None)
        source_name = src.source if src else "?"
        trigger_name = getattr(event, "event", None)
        trigger_str = trigger_name.name if trigger_name else "?"
        logger.info("FSM: %s → %s (trigger=%s)", source_name, self.state, trigger_str)

        if self._client is not None:
            msg = ReflexState(
                header=Header(timestamp_unix=time.time()),
                previous_state=source_name,
                current_state=self.state,
                trigger_event=trigger_str,
            )
            self._client.publish("agent/reflex/state", msg)

    def publish_current_state(self, trigger_event: str = "bootstrap") -> None:
        """Publish the current state without requiring a transition."""
        if self._client is None:
            return
        msg = ReflexState(
            header=Header(timestamp_unix=time.time()),
            previous_state=self.state,
            current_state=self.state,
            trigger_event=trigger_event,
        )
        self._client.publish("agent/reflex/state", msg)

    # ------------------------------------------------------------------
    # Thread-safe trigger dispatch
    # ------------------------------------------------------------------

    def fire(self, trigger_name: str) -> bool:
        """Fire a named trigger atomically.

        Returns *True* if the transition succeeded, *False* if the
        transition is invalid for the current state.
        """
        with self._lock:
            try:
                # Use the machine's event dispatch directly to avoid
                # shadowing the transitions-generated trigger methods.
                self._machine.events[trigger_name].trigger(self)
                return True
            except (MachineError, AttributeError, KeyError):
                logger.warning(
                    "FSM: invalid trigger '%s' in state '%s'",
                    trigger_name,
                    self.state,
                )
                return False

    # ------------------------------------------------------------------
    # Event-bus integration
    # ------------------------------------------------------------------

    def handle_event(self, topic: str, payload: bytes | EndocrineEvent | ImmuneAlert) -> bool:
        """Route an event-bus message to the correct FSM trigger.

        For ``agent/endocrine/cortisol`` and ``agent/endocrine/adrenaline``
        triggers fire only when the message severity is CRITICAL.
        For ``agent/immune/alert`` the trigger also fires only when
        severity is CRITICAL.

        Returns *True* if a transition was fired, *False* otherwise.
        """
        trigger_name = TOPIC_TRIGGER_MAP.get(topic)
        if trigger_name is None:
            return False

        if topic.startswith("agent/endocrine/") and self._extract_level(payload) <= 0.0:
            return False

        # Severity gating for endocrine escalations and immune/alert
        if topic in (
            "agent/endocrine/cortisol",
            "agent/endocrine/adrenaline",
            "agent/immune/alert",
        ):
            severity = self._extract_severity(topic, payload)
            if severity != 3:  # CRITICAL = 3 in the proto enum
                return False

        return self.fire(trigger_name)

    @staticmethod
    def _extract_severity(topic: str, payload: bytes | EndocrineEvent | ImmuneAlert) -> int:
        """Extract the severity int from the protobuf payload."""
        if hasattr(payload, "severity"):
            return int(payload.severity)

        try:
            if topic.startswith("agent/endocrine/"):
                msg = EndocrineEvent()
                msg.ParseFromString(payload)
                return msg.severity
            if topic.startswith("agent/immune/"):
                msg = ImmuneAlert()
                msg.ParseFromString(payload)
                return msg.severity
        except Exception:
            logger.exception("FSM: failed to parse severity from %s", topic)
        return 0

    @staticmethod
    def _extract_level(payload: bytes | EndocrineEvent | ImmuneAlert) -> float:
        if hasattr(payload, "level"):
            return float(payload.level)

        try:
            msg = EndocrineEvent()
            msg.ParseFromString(payload)
            return float(msg.level)
        except Exception:
            return 0.0

    def subscribe_triggers(self) -> None:
        """Subscribe to all trigger topics via the MQTT client."""
        if self._client is None:
            return
        for topic in TOPIC_TRIGGER_MAP:
            self._client.subscribe(topic, TOPIC_MESSAGE_MAP[topic], self.handle_event)
