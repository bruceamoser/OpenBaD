"""Tests for openbad.reflex_arc — handlers, FSM, and escalation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from openbad.reflex_arc.escalation import EscalationGateway, consume_escalation
from openbad.reflex_arc.handlers.endocrine import (
    ADRENALINE_EMERGENCY,
    CORTISOL_THROTTLE,
    ENDORPHIN_SLEEP,
    handle_adrenaline_spike,
    handle_endorphin_release,
    handle_high_cortisol,
    handle_immune_alert,
)

# ── Endocrine handlers ───────────────────────────────────────────────── #


class TestHighCortisol:
    def test_below_threshold_no_action(self) -> None:
        actions = handle_high_cortisol(0.5)
        assert actions == []

    def test_above_threshold_creates_task(self) -> None:
        store = MagicMock()
        store.create_task.return_value = SimpleNamespace(task_id="t1")
        actions = handle_high_cortisol(
            CORTISOL_THROTTLE + 0.01, task_store=store,
        )
        assert len(actions) >= 1
        assert any(a.action == "task" for a in actions)
        store.create_task.assert_called_once()

    def test_publishes_throttle_event(self) -> None:
        publish = MagicMock()
        actions = handle_high_cortisol(
            0.85, publish_fn=publish,
        )
        assert any(a.action == "mqtt" for a in actions)
        publish.assert_called_once()
        topic = publish.call_args[0][0]
        assert "throttle" in topic


class TestAdrenalineSpike:
    def test_below_threshold_no_action(self) -> None:
        actions = handle_adrenaline_spike(0.5)
        assert actions == []

    def test_above_threshold_creates_task(self) -> None:
        store = MagicMock()
        store.create_task.return_value = SimpleNamespace(task_id="t2")
        actions = handle_adrenaline_spike(
            ADRENALINE_EMERGENCY + 0.01, task_store=store,
        )
        assert any(a.action == "task" for a in actions)
        store.create_task.assert_called_once()

    def test_alerts_doctor(self) -> None:
        publish = MagicMock()
        actions = handle_adrenaline_spike(
            0.9, publish_fn=publish,
        )
        assert any(a.action == "mqtt" for a in actions)
        topic = publish.call_args[0][0]
        assert "doctor" in topic


class TestEndorphinRelease:
    def test_below_threshold_no_action(self) -> None:
        actions = handle_endorphin_release(0.3)
        assert actions == []

    def test_creates_research_entry(self) -> None:
        store = MagicMock()
        store.enqueue.return_value = SimpleNamespace(node_id="r1")
        actions = handle_endorphin_release(
            ENDORPHIN_SLEEP + 0.05, research_store=store,
        )
        assert any(a.action == "research" for a in actions)
        store.enqueue.assert_called_once()


class TestImmuneAlert:
    def test_creates_quarantine_task(self) -> None:
        store = MagicMock()
        store.create_task.return_value = SimpleNamespace(task_id="t3")
        actions = handle_immune_alert(
            "plugin-xyz", "injection", task_store=store,
        )
        assert any(a.action == "task" for a in actions)
        assert "quarantine" in actions[0].title.lower()

    def test_escalates_to_cognitive(self) -> None:
        gw = MagicMock()
        actions = handle_immune_alert(
            "plugin-xyz", "injection", escalation_gw=gw,
        )
        assert any(a.action == "escalate" for a in actions)
        gw.escalate.assert_called_once()


# ── Escalation gateway ───────────────────────────────────────────────── #


class TestEscalationGateway:
    def test_escalate_publishes(self) -> None:
        publish = MagicMock()
        gw = EscalationGateway(publish_fn=publish)
        ctx = gw.escalate(
            event_topic="agent/immune/alert",
            event_payload=b"test",
            reason="Test escalation",
            reflex_id="test",
            current_state="EMERGENCY",
        )
        assert ctx.correlation_id
        publish.assert_called_once()

    def test_flap_detection(self) -> None:
        gw = EscalationGateway(
            publish_fn=MagicMock(),
            flap_threshold=3,
            flap_window=10.0,
        )
        assert gw.record_transition("IDLE", "ACTIVE") is False
        assert gw.record_transition("ACTIVE", "IDLE") is False
        assert gw.record_transition("IDLE", "ACTIVE") is True  # 3rd

    def test_escalation_log(self) -> None:
        gw = EscalationGateway()
        gw.escalate(
            event_topic="t", event_payload=b"",
            reason="test", reflex_id="r",
        )
        assert len(gw.escalation_log) == 1


# ── Escalation consumer ─────────────────────────────────────────────── #


class TestConsumeEscalation:
    def _make_escalation_payload(
        self, reason: str, priority: int = 0
    ) -> bytes:
        from openbad.nervous_system.schemas.cognitive_pb2 import (
            EscalationRequest,
        )
        from openbad.nervous_system.schemas.common_pb2 import Header

        msg = EscalationRequest(
            header=Header(timestamp_unix=1.0),
            event_topic="agent/immune/alert",
            event_payload=b"test",
            reason=reason,
            priority=priority,
            reflex_id="test",
        )
        return msg.SerializeToString()

    def test_high_priority_creates_task(self) -> None:
        store = MagicMock()
        created = SimpleNamespace(task_id="t-esc-1")
        store.create_task.return_value = created
        payload = self._make_escalation_payload(
            "Emergency detected", priority=3,
        )
        result = consume_escalation(
            payload, task_store=store,
        )
        assert result == "t-esc-1"
        store.create_task.assert_called_once()

    def test_low_priority_creates_research(self) -> None:
        store = MagicMock()
        node = SimpleNamespace(node_id="r-esc-1")
        store.enqueue.return_value = node
        payload = self._make_escalation_payload(
            "Minor anomaly", priority=0,
        )
        result = consume_escalation(
            payload, research_store=store,
        )
        assert result == "r-esc-1"
        store.enqueue.assert_called_once()

    def test_invalid_payload_returns_none(self) -> None:
        result = consume_escalation(b"not-a-protobuf")
        assert result is None
