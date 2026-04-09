"""MQTT client wrapper for the OpenBaD nervous system.

Provides singleton connection management, typed publish/subscribe helpers
backed by protobuf serialization, and dead-letter routing.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import paho.mqtt.client as mqtt
from google.protobuf.message import DecodeError, Message

from openbad.nervous_system.qos import qos_for, should_retain

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Message)

DEAD_LETTER_TOPIC = "agent/dead-letter"

# Default broker settings
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 1883
_DEFAULT_KEEPALIVE = 60


class NervousSystemClient:
    """Singleton MQTT client for inter-module communication.

    Usage::

        client = NervousSystemClient.get_instance()
        client.connect()
        client.publish("agent/telemetry/cpu", cpu_msg)
        client.subscribe("agent/telemetry/cpu", CpuTelemetry, handler)
    """

    _instance: NervousSystemClient | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        keepalive: int = _DEFAULT_KEEPALIVE,
        client_id: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._keepalive = keepalive
        self._mqtt = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )
        self._subscriptions: dict[str, list[tuple[type[Message], Callable[..., Any]]]] = {}
        self._connected = threading.Event()
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_disconnect = self._on_disconnect
        self._retry_delay = 1.0
        self._max_retry_delay = 30.0

    @classmethod
    def get_instance(
        cls,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        **kwargs: Any,
    ) -> NervousSystemClient:
        """Return the singleton client instance, creating it if necessary."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(host=host, port=port, **kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Tear down the singleton (primarily for testing)."""
        with cls._lock:
            if cls._instance is not None:
                with contextlib.suppress(Exception):
                    cls._instance.disconnect()
                cls._instance = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, timeout: float = 5.0) -> None:
        """Connect to the MQTT broker with retry."""
        delay = self._retry_delay
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._mqtt.connect(self._host, self._port, self._keepalive)
                self._mqtt.loop_start()
                if self._connected.wait(timeout=min(2.0, deadline - time.monotonic())):
                    logger.info("Connected to MQTT broker at %s:%d", self._host, self._port)
                    return
            except OSError:
                logger.warning(
                    "Broker unreachable at %s:%d, retrying in %.1fs",
                    self._host,
                    self._port,
                    delay,
                )
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
                delay = min(delay * 2, self._max_retry_delay)
        msg = f"Could not connect to MQTT broker at {self._host}:{self._port}"
        raise ConnectionError(msg)

    def disconnect(self) -> None:
        """Cleanly disconnect from the broker."""
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        self._connected.clear()
        logger.info("Disconnected from MQTT broker")

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # Publish / Subscribe
    # ------------------------------------------------------------------

    def publish(
        self,
        topic: str,
        message: Message,
        qos: int | None = None,
        retain: bool | None = None,
    ) -> None:
        """Serialize a protobuf message and publish to *topic*.

        If *qos* or *retain* are not specified, the values are determined
        automatically from the topic-based QoS/retention policies.
        """
        if qos is None:
            qos = qos_for(topic)
        if retain is None:
            retain = should_retain(topic)
        payload = message.SerializeToString()
        info = self._mqtt.publish(topic, payload, qos=qos, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error("Publish failed on %s: rc=%d", topic, info.rc)

    def subscribe(
        self,
        topic: str,
        message_type: type[T],
        callback: Callable[[str, T], Any],
        qos: int | None = None,
    ) -> None:
        """Subscribe to *topic* with typed deserialization.

        *callback* receives ``(topic, parsed_message)``.
        If *qos* is not specified, uses the topic-based QoS policy.
        """
        if qos is None:
            qos = qos_for(topic)
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []
            self._mqtt.subscribe(topic, qos=qos)
        self._subscriptions[topic].append((message_type, callback))

    def unsubscribe(self, topic: str) -> None:
        """Remove all callbacks for *topic*."""
        self._subscriptions.pop(topic, None)
        self._mqtt.unsubscribe(topic)

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected.set()
        # Re-subscribe on reconnect
        for topic in self._subscriptions:
            client.subscribe(topic)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected.clear()
        if reason_code != 0:
            logger.warning("Unexpected disconnect (rc=%s), will auto-reconnect", reason_code)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        handlers = self._subscriptions.get(msg.topic, [])

        # Also check wildcard subscriptions
        for pattern, pattern_handlers in self._subscriptions.items():
            if pattern != msg.topic and mqtt.topic_matches_sub(pattern, msg.topic):
                handlers = handlers + pattern_handlers

        if not handlers:
            self._route_to_dead_letter(msg)
            return

        for message_type, callback in handlers:
            try:
                parsed = message_type()
                parsed.ParseFromString(msg.payload)
                callback(msg.topic, parsed)
            except DecodeError:
                logger.error(
                    "Failed to deserialize message on %s as %s",
                    msg.topic,
                    message_type.__name__,
                )
                self._route_to_dead_letter(msg)

    def _route_to_dead_letter(self, msg: mqtt.MQTTMessage) -> None:
        """Forward undeliverable messages to the dead-letter topic."""
        logger.warning("Dead-letter: topic=%s payload_size=%d", msg.topic, len(msg.payload))
        self._mqtt.publish(
            DEAD_LETTER_TOPIC,
            msg.payload,
            qos=1,
        )
