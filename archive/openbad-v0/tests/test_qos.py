"""Tests for openbad.nervous_system.qos — QoS and retention policies."""

from __future__ import annotations

import pytest

from openbad.nervous_system.qos import qos_for, should_retain

# ── QoS level assignments ─────────────────────────────────────────


class TestQosFor:
    """Verify per-topic QoS assignments match the spec."""

    # -- QoS 0: fire-and-forget (telemetry) -------------------------

    @pytest.mark.parametrize(
        "topic",
        [
            "agent/telemetry/cpu",
            "agent/telemetry/memory",
            "agent/telemetry/disk",
            "agent/telemetry/tokens",
        ],
    )
    def test_telemetry_is_qos0(self, topic: str):
        assert qos_for(topic) == 0

    # -- QoS 1: at-least-once (operational messages) ----------------

    @pytest.mark.parametrize(
        "topic",
        [
            "agent/reflex/thermal/trigger",
            "agent/reflex/state",
            "agent/cognitive/escalation",
            "agent/cognitive/result",
            "agent/immune/alert",
            "agent/immune/quarantine",
            "agent/proprioception/tool-a/heartbeat",
            "agent/sensory/vision/cam-1",
        ],
    )
    def test_operational_is_qos1(self, topic: str):
        assert qos_for(topic) == 1

    # -- QoS 2: exactly-once (state transitions) --------------------

    @pytest.mark.parametrize(
        "topic",
        [
            "agent/endocrine/cortisol",
            "agent/endocrine/adrenaline",
            "agent/endocrine/endorphin",
            "agent/memory/stm/write",
            "agent/memory/ltm/consolidate",
            "agent/sleep/rem",
            "agent/sleep/deep",
        ],
    )
    def test_state_transitions_are_qos2(self, topic: str):
        assert qos_for(topic) == 2

    # -- Default QoS ------------------------------------------------

    def test_unknown_topic_gets_default_qos1(self):
        assert qos_for("agent/unknown/something") == 1

    def test_completely_foreign_topic(self):
        assert qos_for("other/system/topic") == 1


# ── Retention policy ──────────────────────────────────────────────


class TestShouldRetain:
    """Verify retained-message policy for state topics."""

    @pytest.mark.parametrize(
        "topic",
        [
            "agent/reflex/state",
            "agent/endocrine/dopamine",
            "agent/endocrine/cortisol",
            "agent/telemetry/cpu",
            "agent/telemetry/memory",
            "agent/telemetry/disk",
            "agent/telemetry/tokens",
            "agent/cognitive/health",
        ],
    )
    def test_state_topics_are_retained(self, topic: str):
        assert should_retain(topic) is True

    @pytest.mark.parametrize(
        "topic",
        [
            "agent/reflex/thermal/trigger",
            "agent/cognitive/escalation",
            "agent/cognitive/response",
            "agent/immune/alert",
            "agent/memory/stm/write",
            "agent/proprioception/tool-a/heartbeat",
        ],
    )
    def test_non_state_topics_are_not_retained(self, topic: str):
        assert should_retain(topic) is False


# ── Client integration ────────────────────────────────────────────


class TestClientAutoQos:
    """Verify NervousSystemClient uses qos/retention policies automatically."""

    def test_publish_uses_policy_qos(self):
        from unittest.mock import patch

        from openbad.nervous_system.client import NervousSystemClient
        from openbad.nervous_system.schemas import CpuTelemetry

        NervousSystemClient.reset_instance()
        with patch("openbad.nervous_system.client.mqtt.Client"):
            client = NervousSystemClient.get_instance()
            msg = CpuTelemetry(usage_percent=50.0)

            client.publish("agent/telemetry/cpu", msg)

            call_args = client._mqtt.publish.call_args
            # publish(topic, payload, qos=0, retain=True) for telemetry
            assert call_args.kwargs["qos"] == 0
            assert call_args.kwargs["retain"] is True
            NervousSystemClient.reset_instance()

    def test_publish_allows_explicit_qos_override(self):
        from unittest.mock import patch

        from openbad.nervous_system.client import NervousSystemClient
        from openbad.nervous_system.schemas import CpuTelemetry

        NervousSystemClient.reset_instance()
        with patch("openbad.nervous_system.client.mqtt.Client"):
            client = NervousSystemClient.get_instance()
            msg = CpuTelemetry(usage_percent=50.0)

            # Override QoS 0 → QoS 2
            client.publish("agent/telemetry/cpu", msg, qos=2, retain=False)

            call_args = client._mqtt.publish.call_args
            assert call_args.kwargs["qos"] == 2
            assert call_args.kwargs["retain"] is False
            NervousSystemClient.reset_instance()


# ── Broker config ─────────────────────────────────────────────────


class TestBrokerConfig:
    """Verify broker config supports persistent sessions."""

    def test_session_expiry_configured(self):
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[1] / "config" / "broker.conf"
        content = config_path.read_text()
        assert "session_expiry_interval" in content

    def test_max_inflight_configured(self):
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[1] / "config" / "broker.conf"
        content = config_path.read_text()
        assert "max_inflight_window" in content
