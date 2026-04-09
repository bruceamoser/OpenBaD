"""Phase 1 end-to-end integration tests.

**Requires a running MQTT broker at ``localhost:1883``** (e.g. NanoMQ
or Mosquitto).  Excluded from the default ``pytest`` run.

Run with::

    pytest -m integration
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import pytest

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.reflex_arc.escalation import ESCALATION_TOPIC, EscalationGateway
from openbad.reflex_arc.fsm import AgentFSM
from openbad.reflex_arc.handlers.budget import BudgetGuard
from openbad.reflex_arc.handlers.proprioceptive import (
    DEGRADATION_TOPIC,
    ProprioceptiveHandler,
)
from openbad.reflex_arc.handlers.proprioceptive import (
    RESULT_TOPIC as PROPRIO_RESULT_TOPIC,
)
from openbad.reflex_arc.handlers.security import SecurityGuard
from openbad.reflex_arc.handlers.thermal import (
    RESULT_TOPIC as THERMAL_RESULT_TOPIC,
)
from openbad.reflex_arc.handlers.thermal import (
    SUSPEND_TOPIC,
)
from openbad.reflex_arc.handlers.thermal import (
    handle_cortisol as thermal_handle,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# MQTT test harness
# ---------------------------------------------------------------------------

CORTISOL_TOPIC = "agent/endocrine/cortisol"
IMMUNE_ALERT_TOPIC = "agent/immune/alert"
PROPRIOCEPTION_STATE_TOPIC = "agent/proprioception/state"


class MQTTHarness:
    """Wraps a real paho-mqtt connection with helper methods for E2E tests."""

    def __init__(self) -> None:
        uid = uuid.uuid4().hex[:8]
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"openbad-e2e-{uid}",
            protocol=mqtt.MQTTv5,
        )
        self._lock = threading.Lock()
        self._handlers: dict[str, list] = {}
        self._collected: dict[str, list[bytes]] = {}
        self._client.on_message = self._on_message

    def connect(self) -> None:
        self._client.connect("localhost", 1883)
        self._client.loop_start()
        time.sleep(0.3)

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def subscribe_handler(self, topic: str, callback: callable) -> None:  # type: ignore[valid-type]
        """Subscribe to *topic* and dispatch to *callback(topic, payload)*."""
        with self._lock:
            self._handlers.setdefault(topic, []).append(callback)
        self._client.subscribe(topic, qos=1)

    def subscribe_collect(self, topic: str) -> None:
        """Subscribe to *topic* and collect messages for later assertion."""
        with self._lock:
            self._collected.setdefault(topic, [])
        self._client.subscribe(topic, qos=1)

    def publish(self, topic: str, data: bytes) -> None:
        self._client.publish(topic, data, qos=1)

    def wait_for(self, topic: str, count: int = 1, timeout: float = 3.0) -> list[bytes]:
        """Block until *count* messages have been collected on *topic*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                msgs = self._collected.get(topic, [])
                if len(msgs) >= count:
                    return list(msgs[:count])
            time.sleep(0.05)
        with self._lock:
            return list(self._collected.get(topic, []))

    def _on_message(self, _client: object, _userdata: object, msg: mqtt.MQTTMessage) -> None:
        with self._lock:
            cbs = list(self._handlers.get(msg.topic, []))
            if msg.topic in self._collected:
                self._collected[msg.topic].append(msg.payload)
        for cb in cbs:
            with contextlib.suppress(Exception):
                cb(msg.topic, msg.payload)


@pytest.fixture()
def harness():
    """Yield a connected :class:`MQTTHarness`, skip if no broker."""
    h = MQTTHarness()
    try:
        h.connect()
    except (OSError, ConnectionRefusedError):
        pytest.skip("No MQTT broker at localhost:1883")
    yield h
    h.close()


# ---------------------------------------------------------------------------
# Scenario 1 – Thermal spike
# ---------------------------------------------------------------------------


class TestThermalSpike:
    """High CPU → cortisol → thermal reflex fires → FSM enters THROTTLED."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()

        # Collect output topics
        harness.subscribe_collect(THERMAL_RESULT_TOPIC)
        harness.subscribe_collect(SUSPEND_TOPIC)

        # Wire cortisol handler through broker
        def on_cortisol(topic: str, payload: bytes) -> None:
            fsm.handle_event(topic, payload)
            thermal_handle(payload, harness.publish)

        harness.subscribe_handler(CORTISOL_TOPIC, on_cortisol)
        time.sleep(0.3)

        # Inject critical cortisol event for cpu_percent
        event = EndocrineEvent(
            header=Header(timestamp_unix=time.time()),
            severity=3,
            metric_name="cpu_percent",
            metric_value=95.0,
        )
        harness.publish(CORTISOL_TOPIC, event.SerializeToString())

        # Verify reflex result via broker
        results = harness.wait_for(THERMAL_RESULT_TOPIC)
        assert len(results) >= 1
        result = ReflexResult()
        result.ParseFromString(results[0])
        assert result.handled
        assert result.reflex_id == "thermal_throttle"

        # Verify FSM transitioned to THROTTLED
        time.sleep(0.2)
        assert fsm.state == "THROTTLED"

        # Verify suspend directive published
        suspends = harness.wait_for(SUSPEND_TOPIC)
        assert len(suspends) >= 1


# ---------------------------------------------------------------------------
# Scenario 2 – Budget exhaustion
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    """Token budget at 0% → cortisol → budget reflex → API blocked → THROTTLED."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        guard = BudgetGuard(fsm=fsm, publish_fn=harness.publish)

        # Collect output
        harness.subscribe_collect("agent/reflex/budget/result")

        # Wire cortisol handler through broker
        def on_cortisol(_topic: str, payload: bytes) -> None:
            guard.handle_cortisol(payload)

        harness.subscribe_handler(CORTISOL_TOPIC, on_cortisol)
        time.sleep(0.3)

        # Inject critical cortisol for budget
        event = EndocrineEvent(
            header=Header(timestamp_unix=time.time()),
            severity=3,
            metric_name="token_budget_remaining_pct",
            metric_value=0.0,
        )
        harness.publish(CORTISOL_TOPIC, event.SerializeToString())

        # Verify budget guard blocked API calls
        time.sleep(0.3)
        assert guard.blocked
        assert not guard.is_call_allowed()

        # Verify FSM transitioned to THROTTLED
        assert fsm.state == "THROTTLED"

        # Verify reflex result via broker
        results = harness.wait_for("agent/reflex/budget/result")
        assert len(results) >= 1
        result = ReflexResult()
        result.ParseFromString(results[0])
        assert result.handled
        assert result.reflex_id == "budget_exhaustion"


# ---------------------------------------------------------------------------
# Scenario 3 – Security alert
# ---------------------------------------------------------------------------


class TestSecurityAlert:
    """Critical immune alert → security lockdown → FSM EMERGENCY → tools blocked."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        guard = SecurityGuard(fsm=fsm, publish_fn=harness.publish)

        # Collect output
        harness.subscribe_collect("agent/reflex/security/result")

        # Wire immune alert handler through broker
        def on_alert(_topic: str, payload: bytes) -> None:
            guard.handle_alert(payload)

        harness.subscribe_handler(IMMUNE_ALERT_TOPIC, on_alert)
        time.sleep(0.3)

        # Inject critical immune alert
        alert = ImmuneAlert(
            header=Header(timestamp_unix=time.time()),
            severity=3,
            threat_type="privilege_escalation",
            source_id="malicious-plugin",
            detail="Detected unauthorised privilege escalation attempt",
        )
        harness.publish(IMMUNE_ALERT_TOPIC, alert.SerializeToString())

        # Verify lockdown
        time.sleep(0.3)
        assert guard.locked_down
        assert not guard.is_tool_allowed()
        assert not guard.is_tool_allowed("malicious-plugin")

        # Verify FSM in EMERGENCY
        assert fsm.state == "EMERGENCY"

        # Verify reflex result
        results = harness.wait_for("agent/reflex/security/result")
        assert len(results) >= 1
        result = ReflexResult()
        result.ParseFromString(results[0])
        assert result.handled
        assert result.reflex_id == "security_lockdown"


# ---------------------------------------------------------------------------
# Scenario 4 – State flap escalation
# ---------------------------------------------------------------------------


class TestStateFlap:
    """Rapid THROTTLED ↔ ACTIVE oscillation → escalation gateway fires."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        # Set up escalation gateway publishing through the broker
        gateway = EscalationGateway(
            publish_fn=harness.publish,
            flap_threshold=3,
            flap_window=10.0,
        )

        # Collect escalation messages
        harness.subscribe_collect(ESCALATION_TOPIC)
        time.sleep(0.3)

        # Simulate rapid state transitions (flap)
        flapped = False
        for _ in range(4):
            result = gateway.record_transition("ACTIVE", "THROTTLED")
            if result:
                flapped = True
            result = gateway.record_transition("THROTTLED", "ACTIVE")
            if result:
                flapped = True

        assert flapped, "Flap detection should have triggered"

        # Verify escalation message published through broker
        escalations = harness.wait_for(ESCALATION_TOPIC)
        assert len(escalations) >= 1

        # Verify escalation log contains entries
        assert len(gateway.escalation_log) >= 1
        ctx = gateway.escalation_log[0]
        assert "flapping" in ctx.reason.lower()


# ---------------------------------------------------------------------------
# Scenario 5 – Proprioceptive loss
# ---------------------------------------------------------------------------


class TestProprioceptiveLoss:
    """Tool heartbeat timeout → proprioceptive reflex → action aborted."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        handler = ProprioceptiveHandler(
            critical_tools={"code-exec"},
            alternatives={"code-exec": ["sandbox-exec"]},
            fsm=fsm,
            publish_fn=harness.publish,
        )

        # Collect output
        harness.subscribe_collect(PROPRIO_RESULT_TOPIC)
        harness.subscribe_collect(DEGRADATION_TOPIC)

        # Wire proprioception state through broker
        def on_state(_topic: str, payload: bytes) -> None:
            handler.handle_state_change(payload)

        harness.subscribe_handler(PROPRIOCEPTION_STATE_TOPIC, on_state)
        time.sleep(0.3)

        # Simulate tool going UNAVAILABLE
        state_payload = json.dumps({"code-exec": "UNAVAILABLE"}).encode()
        harness.publish(PROPRIOCEPTION_STATE_TOPIC, state_payload)

        # Verify reflex result
        time.sleep(0.3)
        results = harness.wait_for(PROPRIO_RESULT_TOPIC)
        assert len(results) >= 1
        result = ReflexResult()
        result.ParseFromString(results[0])
        assert result.handled
        assert result.reflex_id == "proprioceptive_block"

        # Verify in-flight action aborted
        assert "code-exec" in handler.aborted_tools

        # Verify FSM throttled (critical tool)
        assert fsm.state == "THROTTLED"

        # Verify degradation notice published
        degradations = harness.wait_for(DEGRADATION_TOPIC)
        assert len(degradations) >= 1
        deg = json.loads(degradations[0])
        assert deg["lost_tool"] == "code-exec"
        assert "sandbox-exec" in deg["alternatives"]
