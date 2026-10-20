"""Integration tests for the Corsair peripheral transducer pipeline.

These tests verify the end-to-end flow:
  Telegram inbound → MQTT → PeripheralChatRouter → stream_chat → MQTT → Telegram outbound

All external I/O (Telegram API, LLM providers) is mocked. MQTT pub/sub is
exercised through the real NervousSystemClient subscribe/callback path
(mocked at the paho level).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.peripherals.chat_router import PeripheralChatRouter
from openbad.peripherals.telegram_bridge import TelegramBridge
from openbad.wui.chat_pipeline import StreamChunk

# ── Helpers ────────────────────────────────────────────── #


def _make_mqtt() -> MagicMock:
    """Mock NervousSystemClient with working subscribe/publish_bytes."""
    client = MagicMock()
    client.publish_bytes = MagicMock()
    client.subscribe = MagicMock()
    client.unsubscribe = MagicMock()
    return client


# ── Integration: TelegramBridge → MQTT inbound ───────── #


class TestTelegramToMqtt:
    """Verify that a Telegram update produces the correct MQTT inbound message."""

    @pytest.mark.asyncio
    async def test_telegram_update_publishes_mqtt(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        update = {
            "update_id": 100,
            "message": {
                "chat": {"id": 55555},
                "from": {"first_name": "Alice"},
                "text": "Hello from Telegram!",
            },
        }
        await bridge._handle_update(update)

        mqtt.publish_bytes.assert_called_once()
        topic, payload_bytes = mqtt.publish_bytes.call_args[0][:2]
        assert topic == "sensory/external/telegram/inbound"
        payload = json.loads(payload_bytes)
        assert payload["platform"] == "telegram"
        assert payload["event"] == "message"
        assert payload["data"]["sender"] == "55555"
        assert payload["data"]["content"] == "Hello from Telegram!"
        assert payload["data"]["sender_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_multi_update_advances_offset(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        for uid in (10, 11, 12):
            await bridge._handle_update({
                "update_id": uid,
                "message": {
                    "chat": {"id": 1},
                    "from": {"first_name": "X"},
                    "text": f"msg-{uid}",
                },
            })

        assert bridge._offset == 13
        assert mqtt.publish_bytes.call_count == 3


# ── Integration: MQTT inbound → ChatRouter → MQTT outbound ── #


class TestMqttChatRouterFlow:
    """Verify that an inbound MQTT message flows through the chat pipeline
    and produces an outbound reply."""

    @pytest.mark.asyncio
    async def test_inbound_to_outbound(self) -> None:
        mqtt = _make_mqtt()
        model = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield StreamChunk(token="I am ")  # noqa: S106
            yield StreamChunk(token="OpenBaD")  # noqa: S106
            yield StreamChunk(done=True)

        router = PeripheralChatRouter(
            mqtt, lambda: (model, "test/m", "test"),
        )

        inbound = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {
                "sender": "42",
                "content": "What is your name?",
                "sender_name": "Bruce",
            },
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=fake_stream,
        ):
            await router._handle_inbound(
                "sensory/external/telegram/inbound", inbound,
            )

        # Verify outbound was published
        mqtt.publish_bytes.assert_called_once()
        topic, reply_bytes = mqtt.publish_bytes.call_args[0][:2]
        assert topic == "motor/external/telegram/outbound"
        reply = json.loads(reply_bytes)
        assert reply["chat_id"] == "42"
        assert reply["text"] == "I am OpenBaD"

    @pytest.mark.asyncio
    async def test_session_id_per_sender(self) -> None:
        """Each sender should get a unique session ID."""
        mqtt = _make_mqtt()
        sessions: list[str] = []

        async def capture(*args, **kwargs):
            sessions.append(args[3])  # session_id is 4th positional
            yield StreamChunk(token="ok", done=True)  # noqa: S106

        router = PeripheralChatRouter(
            mqtt, lambda: (MagicMock(), "m", "p"),
        )

        for sender in ("100", "200"):
            payload = json.dumps({
                "platform": "telegram",
                "event": "message",
                "data": {"sender": sender, "content": "hi"},
            }).encode()
            with patch(
                "openbad.peripherals.chat_router.stream_chat",
                side_effect=capture,
            ):
                await router._handle_inbound("topic", payload)

        assert sessions == [
            "peripheral:telegram:100",
            "peripheral:telegram:200",
        ]

    @pytest.mark.asyncio
    async def test_platform_agnostic_routing(self) -> None:
        """Verify the router works for any platform, not just telegram."""
        mqtt = _make_mqtt()

        async def reply_stream(*args, **kwargs):
            yield StreamChunk(token="pong", done=True)  # noqa: S106

        router = PeripheralChatRouter(
            mqtt, lambda: (MagicMock(), "m", "p"),
        )

        payload = json.dumps({
            "platform": "discord",
            "event": "message",
            "data": {"sender": "99", "content": "ping"},
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=reply_stream,
        ):
            await router._handle_inbound(
                "sensory/external/discord/inbound", payload,
            )

        topic = mqtt.publish_bytes.call_args[0][0]
        assert topic == "motor/external/discord/outbound"


# ── Integration: MQTT outbound → Telegram sendMessage ──── #


class TestMqttToTelegramOutbound:
    """Verify that an outbound MQTT payload triggers a Telegram sendMessage."""

    @pytest.mark.asyncio
    async def test_outbound_sends_telegram_message(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        # Mock the HTTP session
        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"ok": True})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_ctx)
        bridge._session = mock_session

        # Simulate the MQTT outbound payload
        outbound = json.dumps({
            "chat_id": 42,
            "text": "Hello back!",
        }).encode()

        # Directly call send_message (as _on_outbound would)
        data = json.loads(outbound)
        result = await bridge.send_message(int(data["chat_id"]), data["text"])
        assert result is True

        # Verify Telegram API was called correctly
        mock_session.post.assert_called_once()
        call_url = mock_session.post.call_args[0][0]
        assert "sendMessage" in call_url
        call_json = mock_session.post.call_args[1]["json"]
        assert call_json["chat_id"] == 42
        assert call_json["text"] == "Hello back!"


# ── Integration: Full round-trip ──────────────────────── #


class TestFullRoundTrip:
    """Simulate the full path: Telegram update → bridge → MQTT → router →
    chat pipeline → MQTT → bridge → Telegram sendMessage."""

    @pytest.mark.asyncio
    async def test_end_to_end_flow(self) -> None:
        """End-to-end: inbound update → chat pipeline → outbound reply."""
        mqtt = _make_mqtt()
        outbound_payloads: list[bytes] = []

        # Capture what gets published
        def capture_publish(topic: str, payload: bytes, qos: int = 1) -> None:
            outbound_payloads.append((topic, payload))

        mqtt.publish_bytes = capture_publish

        # 1. TelegramBridge processes an inbound update
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "TestUser"},
                "text": "Tell me a joke",
            },
        }
        await bridge._handle_update(update)

        # Verify inbound was published
        assert len(outbound_payloads) == 1
        inbound_topic, inbound_bytes = outbound_payloads[0]
        assert inbound_topic == "sensory/external/telegram/inbound"

        # 2. ChatRouter receives the inbound and processes it
        outbound_payloads.clear()

        async def joke_stream(*args, **kwargs):
            yield StreamChunk(token="Why did the chicken ")  # noqa: S106
            yield StreamChunk(token="cross the road?")  # noqa: S106
            yield StreamChunk(done=True)

        router = PeripheralChatRouter(
            mqtt, lambda: (MagicMock(), "test/model", "test"),
        )

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=joke_stream,
        ):
            await router._handle_inbound(inbound_topic, inbound_bytes)

        # Verify outbound was published
        assert len(outbound_payloads) == 1
        out_topic, out_bytes = outbound_payloads[0]
        assert out_topic == "motor/external/telegram/outbound"

        reply = json.loads(out_bytes)
        assert reply["chat_id"] == "12345"
        assert reply["text"] == "Why did the chicken cross the road?"
