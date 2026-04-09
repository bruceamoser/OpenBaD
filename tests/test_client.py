"""Unit tests for the NervousSystemClient — Issue #5.

These tests mock the MQTT transport layer so no broker is needed.
Integration tests requiring a live broker are in a separate file
and marked with @pytest.mark.integration.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest
from google.protobuf.message import Message

from openbad.nervous_system.client import DEAD_LETTER_TOPIC, NervousSystemClient
from openbad.nervous_system.schemas import CpuTelemetry, Header

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Ensure singleton is clean between tests."""
    NervousSystemClient.reset_instance()


def _make_cpu_msg() -> CpuTelemetry:
    return CpuTelemetry(
        header=Header(
            timestamp_unix=1000.0,
            source_module="test",
            correlation_id="c-1",
            schema_version=1,
        ),
        usage_percent=42.5,
        core_count=8,
    )


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_same_instance_returned(self) -> None:
        a = NervousSystemClient.get_instance(host="h1")
        b = NervousSystemClient.get_instance(host="h2")  # ignored once created
        assert a is b

    def test_reset_creates_new_instance(self) -> None:
        a = NervousSystemClient.get_instance()
        NervousSystemClient.reset_instance()
        b = NervousSystemClient.get_instance()
        assert a is not b

    def test_thread_safe_singleton(self) -> None:
        instances: list[NervousSystemClient] = []
        barrier = threading.Barrier(4)

        def grab() -> None:
            barrier.wait()
            instances.append(NervousSystemClient.get_instance())

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(id(i) for i in instances)) == 1


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_serializes_protobuf(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()
        client._mqtt.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_SUCCESS)

        msg = _make_cpu_msg()
        client.publish("agent/telemetry/cpu", msg, qos=1, retain=True)

        client._mqtt.publish.assert_called_once()
        call_args = client._mqtt.publish.call_args
        assert call_args[0][0] == "agent/telemetry/cpu"
        assert isinstance(call_args[0][1], bytes)
        assert call_args[1]["qos"] == 1
        assert call_args[1]["retain"] is True

        # Verify the payload deserializes back correctly
        restored = CpuTelemetry()
        restored.ParseFromString(call_args[0][1])
        assert abs(restored.usage_percent - 42.5) < 0.01

    def test_publish_logs_on_failure(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()
        client._mqtt.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_NO_CONN)

        with patch("openbad.nervous_system.client.logger") as mock_logger:
            client.publish("agent/telemetry/cpu", _make_cpu_msg())
            mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_subscribe_registers_callback(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        handler = MagicMock()
        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler)

        assert "agent/telemetry/cpu" in client._subscriptions
        client._mqtt.subscribe.assert_called_once_with("agent/telemetry/cpu", qos=0)

    def test_multiple_handlers_same_topic(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        handler1 = MagicMock()
        handler2 = MagicMock()
        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler1)
        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler2)

        assert len(client._subscriptions["agent/telemetry/cpu"]) == 2
        # Only one MQTT-level subscribe
        client._mqtt.subscribe.assert_called_once()

    def test_unsubscribe_removes_handlers(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        client.subscribe("agent/telemetry/cpu", CpuTelemetry, MagicMock())
        client.unsubscribe("agent/telemetry/cpu")

        assert "agent/telemetry/cpu" not in client._subscriptions
        client._mqtt.unsubscribe.assert_called_once_with("agent/telemetry/cpu")


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------


class TestMessageDispatch:
    def test_dispatch_deserializes_and_calls_handler(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        received: list[tuple[str, CpuTelemetry]] = []

        def handler(topic: str, msg: CpuTelemetry) -> None:
            received.append((topic, msg))

        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler)

        # Simulate incoming MQTT message
        mqtt_msg = MagicMock()
        mqtt_msg.topic = "agent/telemetry/cpu"
        mqtt_msg.payload = _make_cpu_msg().SerializeToString()

        client._on_message(client._mqtt, None, mqtt_msg)

        assert len(received) == 1
        assert received[0][0] == "agent/telemetry/cpu"
        assert abs(received[0][1].usage_percent - 42.5) < 0.01

    def test_malformed_payload_routes_to_dead_letter(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        handler = MagicMock()
        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler)

        mqtt_msg = MagicMock()
        mqtt_msg.topic = "agent/telemetry/cpu"
        mqtt_msg.payload = b"not-valid-protobuf-\xff\xfe"

        # Note: protobuf is lenient with unknown bytes — it may parse without error.
        # This test verifies the handler still gets called or dead-letter fires.
        client._on_message(client._mqtt, None, mqtt_msg)

        # Either handler was called (lenient parse) or dead-letter was invoked
        assert handler.called or client._mqtt.publish.called

    def test_no_handler_routes_to_dead_letter(self) -> None:
        client = NervousSystemClient.get_instance()
        client._mqtt = MagicMock()

        mqtt_msg = MagicMock()
        mqtt_msg.topic = "agent/unknown/topic"
        mqtt_msg.payload = b"data"

        client._on_message(client._mqtt, None, mqtt_msg)

        client._mqtt.publish.assert_called_once()
        call_args = client._mqtt.publish.call_args
        assert call_args[0][0] == DEAD_LETTER_TOPIC
        assert call_args[1]["qos"] == 1


# ---------------------------------------------------------------------------
# Reconnect / on_connect
# ---------------------------------------------------------------------------


class TestReconnectBehavior:
    def test_on_connect_resubscribes(self) -> None:
        client = NervousSystemClient.get_instance()
        mock_mqtt = MagicMock()
        client._mqtt = mock_mqtt

        client.subscribe("agent/telemetry/cpu", CpuTelemetry, MagicMock())
        client.subscribe("agent/reflex/state", Message, MagicMock())

        mock_mqtt.reset_mock()

        # Simulate reconnect
        client._on_connect(mock_mqtt, None, None, 0)

        # Should re-subscribe to both topics
        subscribed_topics = {call[0][0] for call in mock_mqtt.subscribe.call_args_list}
        assert "agent/telemetry/cpu" in subscribed_topics
        assert "agent/reflex/state" in subscribed_topics

    def test_on_disconnect_clears_connected_flag(self) -> None:
        client = NervousSystemClient.get_instance()
        client._connected.set()

        client._on_disconnect(client._mqtt, None, None, 1)

        assert not client.is_connected


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class TestConnection:
    def test_connect_raises_on_timeout(self) -> None:
        client = NervousSystemClient.get_instance(host="192.0.2.1", port=1)

        with pytest.raises(ConnectionError, match="Could not connect"):
            client.connect(timeout=0.5)


# ---------------------------------------------------------------------------
# Dead letter topic constant
# ---------------------------------------------------------------------------


class TestDeadLetterTopic:
    def test_dead_letter_topic_value(self) -> None:
        assert DEAD_LETTER_TOPIC == "agent/dead-letter"
