"""MQTT data feed for the TUI.

Bridges the NervousSystemClient subscription model into Textual message
posting so widgets can react to live data without touching MQTT directly.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from textual.message import Message

if TYPE_CHECKING:
    from textual.app import App

log = logging.getLogger(__name__)

# ── Textual messages ────────────────────────────────────────────────


class MqttConnected(Message):
    """Posted when the MQTT connection succeeds."""


class MqttDisconnected(Message):
    """Posted when the MQTT connection is lost."""


class MqttPayload(Message):
    """Posted when a message arrives on a subscribed topic."""

    def __init__(self, topic: str, payload: Any) -> None:  # noqa: ANN401
        super().__init__()
        self.topic = topic
        self.payload = payload


# ── Feed ────────────────────────────────────────────────────────────


@dataclass
class MqttFeed:
    """Manages a single MQTT connection and fans messages to a Textual App."""

    host: str = "localhost"
    port: int = 1883
    _app: App | None = field(default=None, repr=False)
    _client: Any = field(default=None, repr=False)
    _connected: bool = field(default=False, repr=False)

    # ── lifecycle ────────────────────────────────────────────────

    async def connect(self, app: App) -> None:
        """Connect to the MQTT broker and store the Textual app reference."""
        self._app = app
        try:
            from openbad.nervous_system.client import NervousSystemClient

            self._client = NervousSystemClient.get_instance(
                host=self.host, port=self.port
            )
            self._client.connect(timeout=5.0)
            self._connected = True
            self._post(MqttConnected())
            log.info("MQTT feed connected to %s:%s", self.host, self.port)
        except Exception:
            log.exception("MQTT feed connection failed")
            self._connected = False
            self._post(MqttDisconnected())

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                log.exception("Error during MQTT disconnect")
            finally:
                from openbad.nervous_system.client import NervousSystemClient

                NervousSystemClient.reset_instance()
                self._client = None
        self._connected = False

    # ── subscriptions ────────────────────────────────────────────

    def subscribe(self, topic: str, proto_type: type | None = None) -> None:
        """Subscribe to *topic* and post ``MqttPayload`` messages to the app.

        If *proto_type* is ``None`` the raw payload bytes are forwarded.
        """
        if self._client is None:
            log.warning("subscribe() called before connect()")
            return

        def _on_message(topic_str: str, msg: Any) -> None:  # noqa: ANN401
            payload = msg if proto_type is not None else msg
            self._post(MqttPayload(topic=topic_str, payload=payload))

        if proto_type is not None:
            self._client.subscribe(topic, proto_type, _on_message)
        else:
            # Raw subscribe — use low-level paho callback if available
            self._client.subscribe(topic, None, _on_message)

    # ── helpers ──────────────────────────────────────────────────

    def _post(self, message: Message) -> None:
        if self._app is not None:
            with contextlib.suppress(Exception):
                self._app.post_message(message)

    @property
    def is_connected(self) -> bool:
        return self._connected
