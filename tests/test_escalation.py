"""Tests for openbad.reflex_arc.escalation — escalation gateway."""

from __future__ import annotations

import json
import time

from openbad.nervous_system.schemas.cognitive_pb2 import EscalationRequest
from openbad.reflex_arc.escalation import (
    ESCALATION_TOPIC,
    EscalationGateway,
)

# ── helpers ───────────────────────────────────────────────────────


def _collect_gateway():
    """Return a gateway and a list that captures published messages."""
    messages: list[tuple[str, bytes]] = []
    gw = EscalationGateway(publish_fn=lambda t, d: messages.append((t, d)))
    return gw, messages


# ── escalation message context ────────────────────────────────────


class TestEscalationContext:
    def test_contains_all_required_fields(self):
        gw, _ = _collect_gateway()
        ctx = gw.escalate(
            event_topic="agent/telemetry/cpu",
            event_payload=b"raw",
            reason="no handler matched",
            reflex_id="thermal_throttle",
            current_state="THROTTLED",
            telemetry_snapshot={"cpu_percent": 95.0},
        )
        assert ctx.event_topic == "agent/telemetry/cpu"
        assert ctx.event_payload == b"raw"
        assert ctx.reason == "no handler matched"
        assert ctx.reflex_id == "thermal_throttle"
        assert ctx.current_state == "THROTTLED"
        assert ctx.telemetry_snapshot == {"cpu_percent": 95.0}

    def test_has_correlation_id(self):
        gw, _ = _collect_gateway()
        ctx = gw.escalate(
            event_topic="t",
            event_payload=b"",
            reason="r",
        )
        assert ctx.correlation_id
        assert len(ctx.correlation_id) == 36  # UUID4 string

    def test_default_telemetry_snapshot_empty(self):
        gw, _ = _collect_gateway()
        ctx = gw.escalate(
            event_topic="t",
            event_payload=b"",
            reason="r",
        )
        assert ctx.telemetry_snapshot == {}


# ── unique correlation IDs ────────────────────────────────────────


class TestCorrelationID:
    def test_unique_across_calls(self):
        gw, _ = _collect_gateway()
        ids = set()
        for _ in range(100):
            ctx = gw.escalate(
                event_topic="t",
                event_payload=b"",
                reason="r",
            )
            ids.add(ctx.correlation_id)
        assert len(ids) == 100


# ── publishing ────────────────────────────────────────────────────


class TestPublishing:
    def test_publishes_to_correct_topic(self):
        gw, messages = _collect_gateway()
        gw.escalate(
            event_topic="agent/telemetry/cpu",
            event_payload=b"raw",
            reason="test",
        )
        assert len(messages) == 1
        assert messages[0][0] == ESCALATION_TOPIC

    def test_published_proto_deserialises(self):
        gw, messages = _collect_gateway()
        gw.escalate(
            event_topic="agent/telemetry/cpu",
            event_payload=b"payload",
            reason="test reason",
            reflex_id="test_reflex",
        )
        msg = EscalationRequest()
        msg.ParseFromString(messages[0][1])
        assert msg.event_topic == "agent/telemetry/cpu"
        assert msg.reason == "test reason"
        assert msg.reflex_id == "test_reflex"

    def test_envelope_contains_correlation_id(self):
        gw, messages = _collect_gateway()
        ctx = gw.escalate(
            event_topic="t",
            event_payload=b"",
            reason="r",
            telemetry_snapshot={"mem": 80},
            current_state="ACTIVE",
        )
        msg = EscalationRequest()
        msg.ParseFromString(messages[0][1])
        envelope = json.loads(msg.event_payload)
        assert envelope["correlation_id"] == ctx.correlation_id
        assert envelope["telemetry_snapshot"] == {"mem": 80}
        assert envelope["current_state"] == "ACTIVE"

    def test_no_publish_fn_ok(self):
        gw = EscalationGateway(publish_fn=None)
        ctx = gw.escalate(
            event_topic="t",
            event_payload=b"",
            reason="r",
        )
        assert ctx.correlation_id


# ── flap detection ────────────────────────────────────────────────


class TestFlapDetection:
    def test_no_flap_below_threshold(self):
        gw, messages = _collect_gateway()
        assert gw.record_transition("IDLE", "ACTIVE") is False
        assert gw.record_transition("ACTIVE", "THROTTLED") is False
        assert len(messages) == 0

    def test_flap_at_threshold(self):
        gw = EscalationGateway(
            publish_fn=lambda t, d: None,
            flap_threshold=3,
            flap_window=10.0,
        )
        gw.record_transition("IDLE", "ACTIVE")
        gw.record_transition("ACTIVE", "THROTTLED")
        result = gw.record_transition("THROTTLED", "ACTIVE")
        assert result is True

    def test_flap_publishes_escalation(self):
        gw, messages = _collect_gateway()
        gw._flap_threshold = 2
        gw.record_transition("IDLE", "ACTIVE")
        gw.record_transition("ACTIVE", "THROTTLED")
        assert len(messages) == 1
        msg = EscalationRequest()
        msg.ParseFromString(messages[0][1])
        assert "flapping" in msg.reason.lower()

    def test_flap_window_expires(self):
        gw, messages = _collect_gateway()
        gw._flap_threshold = 3
        gw._flap_window = 0.05  # 50ms window
        gw.record_transition("IDLE", "ACTIVE")
        gw.record_transition("ACTIVE", "IDLE")
        time.sleep(0.1)  # wait out the window
        result = gw.record_transition("IDLE", "ACTIVE")
        assert result is False
        assert len(messages) == 0


# ── escalation log ────────────────────────────────────────────────


class TestEscalationLog:
    def test_log_records_all(self):
        gw, _ = _collect_gateway()
        gw.escalate(event_topic="t", event_payload=b"", reason="a")
        gw.escalate(event_topic="t", event_payload=b"", reason="b")
        assert len(gw.escalation_log) == 2

    def test_log_is_defensive_copy(self):
        gw, _ = _collect_gateway()
        gw.escalate(event_topic="t", event_payload=b"", reason="a")
        log = gw.escalation_log
        log.clear()
        assert len(gw.escalation_log) == 1
