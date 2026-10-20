"""Tests for the peripheral chat router."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from openbad.peripherals.chat_router import PeripheralChatRouter
from openbad.wui.chat_pipeline import StreamChunk


def _make_mqtt() -> MagicMock:
    client = MagicMock()
    client.publish_bytes = MagicMock()
    client.subscribe = MagicMock()
    client.unsubscribe = MagicMock()
    return client


def _resolver(model=None, model_id="test/model", provider="test"):
    if model is None:
        model = MagicMock()
    return lambda: (model, model_id, provider)


class TestPeripheralChatRouter:

    def test_start_subscribes(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())
        router.start()
        mqtt.subscribe.assert_called_once()
        assert router._running is True

    def test_stop_unsubscribes(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())
        router.start()
        router.stop()
        mqtt.unsubscribe.assert_called_once()
        assert router._running is False

    def test_start_idempotent(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())
        router.start()
        router.start()
        assert mqtt.subscribe.call_count == 1

    @pytest.mark.asyncio
    async def test_handle_inbound_routes_and_replies(self) -> None:
        mqtt = _make_mqtt()
        model = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield StreamChunk(token="Hello ")  # noqa: S106
            yield StreamChunk(token="World")  # noqa: S106
            yield StreamChunk(done=True)

        router = PeripheralChatRouter(mqtt, _resolver(model))

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {
                "sender": "42",
                "content": "hi there",
                "sender_name": "Bruce",
            },
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=fake_stream,
        ):
            await router._handle_inbound(
                "sensory/external/telegram/inbound", payload,
            )

        mqtt.publish_bytes.assert_called_once()
        topic, reply_bytes = mqtt.publish_bytes.call_args[0][:2]
        assert topic == "motor/external/telegram/outbound"
        reply = json.loads(reply_bytes)
        assert reply["chat_id"] == "42"
        assert reply["text"] == "Hello World"

    @pytest.mark.asyncio
    async def test_handle_inbound_error_chunk(self) -> None:
        mqtt = _make_mqtt()

        async def error_stream(*args, **kwargs):
            yield StreamChunk(error="boom", done=True)

        router = PeripheralChatRouter(mqtt, _resolver())

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {"sender": "1", "content": "test", "sender_name": "X"},
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=error_stream,
        ):
            await router._handle_inbound(
                "sensory/external/telegram/inbound", payload,
            )

        mqtt.publish_bytes.assert_called_once()
        reply = json.loads(mqtt.publish_bytes.call_args[0][1])
        assert "[Error: boom]" in reply["text"]

    @pytest.mark.asyncio
    async def test_handle_inbound_no_model(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(
            mqtt, lambda: (None, None, ""),
        )

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {"sender": "1", "content": "hi", "sender_name": "X"},
        }).encode()

        await router._handle_inbound(
            "sensory/external/telegram/inbound", payload,
        )
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_skips_non_message_event(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())

        payload = json.dumps({
            "platform": "telegram",
            "event": "typing",
            "data": {"sender": "1", "content": "hi"},
        }).encode()

        await router._handle_inbound(
            "sensory/external/telegram/inbound", payload,
        )
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_skips_empty_content(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {"sender": "1", "content": ""},
        }).encode()

        await router._handle_inbound(
            "sensory/external/telegram/inbound", payload,
        )
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_empty_reply_not_sent(self) -> None:
        mqtt = _make_mqtt()

        async def empty_stream(*args, **kwargs):
            yield StreamChunk(done=True)

        router = PeripheralChatRouter(mqtt, _resolver())

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {"sender": "1", "content": "test", "sender_name": "X"},
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=empty_stream,
        ):
            await router._handle_inbound(
                "sensory/external/telegram/inbound", payload,
            )

        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_inbound_invalid_json(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())

        await router._handle_inbound("topic", b"not-json")
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_id_format(self) -> None:
        mqtt = _make_mqtt()
        model = MagicMock()
        calls = []

        async def capture_stream(*args, **kwargs):
            calls.append(kwargs.get("session_id") or args[3])
            yield StreamChunk(token="ok", done=True)  # noqa: S106

        router = PeripheralChatRouter(mqtt, _resolver(model))

        payload = json.dumps({
            "platform": "discord",
            "event": "message",
            "data": {"sender": "99", "content": "test"},
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=capture_stream,
        ):
            await router._handle_inbound(
                "sensory/external/discord/inbound", payload,
            )

        # stream_chat is called with positional args: model, model_id, msg, session_id
        # session_id is the 4th positional arg
        assert calls[0] == "peripheral:discord:99"

    def test_on_inbound_dispatches_to_loop(self) -> None:
        mqtt = _make_mqtt()
        router = PeripheralChatRouter(mqtt, _resolver())

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        router._loop = mock_loop
        router._on_inbound(
            "topic",
            json.dumps(
                {"platform": "t", "event": "message",
                 "data": {"content": "hi", "sender": "1"}},
            ).encode(),
        )
        mock_loop.call_soon_threadsafe.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_inbound_passes_identity_context(self) -> None:
        mqtt = _make_mqtt()
        model = MagicMock()
        user_p = MagicMock()
        asst_p = MagicMock()
        mod = MagicMock()
        persist = MagicMock()
        pmod = MagicMock()
        identity_fn = lambda: (user_p, asst_p, mod, persist, pmod)

        captured_kwargs: dict = {}

        async def spy_stream(*args, **kwargs):
            captured_kwargs.update(kwargs)
            yield StreamChunk(token="ok", done=True)

        router = PeripheralChatRouter(
            mqtt, _resolver(model), identity_resolver=identity_fn,
        )

        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {"sender": "1", "content": "hi", "sender_name": "X"},
        }).encode()

        with patch(
            "openbad.peripherals.chat_router.stream_chat",
            side_effect=spy_stream,
        ):
            await router._handle_inbound(
                "sensory/external/telegram/inbound", payload,
            )

        assert captured_kwargs["user_profile"] is user_p
        assert captured_kwargs["assistant_profile"] is asst_p
        assert captured_kwargs["modulation"] is mod
        assert captured_kwargs["identity_persistence"] is persist
        assert captured_kwargs["personality_modulator"] is pmod
