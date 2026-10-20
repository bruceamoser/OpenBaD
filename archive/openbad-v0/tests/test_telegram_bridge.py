"""Tests for the Telegram polling bridge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.peripherals.telegram_bridge import TelegramBridge


def _make_mqtt() -> MagicMock:
    """Create a mock NervousSystemClient."""
    client = MagicMock()
    client.publish_bytes = MagicMock()
    client.subscribe = MagicMock()
    client.unsubscribe = MagicMock()
    return client


class TestTelegramBridge:
    def test_init(self) -> None:
        bridge = TelegramBridge("tok123", _make_mqtt())  # noqa: S106
        assert bridge._token == "tok123"  # noqa: S105
        assert bridge._running is False

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106
        # Patch _poll_loop to avoid real HTTP calls
        with patch.object(bridge, "_poll_loop", new_callable=AsyncMock):
            await bridge.start()
            assert bridge._running is True
            assert bridge._session is not None
            mqtt.subscribe.assert_called_once()

            await bridge.stop()
            assert bridge._running is False
            assert bridge._session is None
            mqtt.unsubscribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_update_publishes_mqtt(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        update = {
            "update_id": 42,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Bruce"},
                "text": "Hello OpenBaD",
            },
        }
        await bridge._handle_update(update)

        assert bridge._offset == 43
        mqtt.publish_bytes.assert_called_once()
        topic, payload_bytes = mqtt.publish_bytes.call_args[0][:2]
        assert topic == "sensory/external/telegram/inbound"
        payload = json.loads(payload_bytes)
        assert payload["platform"] == "telegram"
        assert payload["data"]["sender"] == "12345"
        assert payload["data"]["content"] == "Hello OpenBaD"
        assert payload["data"]["sender_name"] == "Bruce"

    @pytest.mark.asyncio
    async def test_handle_update_skips_no_text(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        update = {
            "update_id": 10,
            "message": {
                "chat": {"id": 1},
                "from": {"first_name": "X"},
                "text": "",
            },
        }
        await bridge._handle_update(update)
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_update_skips_non_message(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        update = {"update_id": 11, "edited_message": {}}
        await bridge._handle_update(update)
        mqtt.publish_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_success(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"ok": True})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_ctx)
        bridge._session = mock_session

        result = await bridge.send_message(123, "Hi there")
        assert result is True

    @pytest.mark.asyncio
    async def test_send_message_failure(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "ok": False, "description": "Bad token",
        })
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_ctx)
        bridge._session = mock_session

        result = await bridge.send_message(123, "Hi")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_no_session(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106
        result = await bridge.send_message(123, "Hi")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_updates_success(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "ok": True,
            "result": [{"update_id": 1, "message": {"text": "hi"}}],
        })
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)
        bridge._session = mock_session

        updates = await bridge._get_updates()
        assert len(updates) == 1
        assert updates[0]["update_id"] == 1

    @pytest.mark.asyncio
    async def test_get_updates_error(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "ok": False, "description": "Unauthorized",
        })
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)
        bridge._session = mock_session

        updates = await bridge._get_updates()
        assert updates == []

    def test_on_outbound_schedules_send(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        payload = json.dumps({"chat_id": 42, "text": "reply"}).encode()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        bridge._loop = mock_loop
        bridge._on_outbound("motor/external/telegram/outbound", payload)
        mock_loop.call_soon_threadsafe.assert_called_once()

    def test_on_outbound_missing_fields(self) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge("tok", mqtt)  # noqa: S106

        payload = json.dumps({"chat_id": 42}).encode()
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        bridge._loop = mock_loop
        bridge._on_outbound("topic", payload)
        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_from_credentials(self, tmp_path) -> None:
        creds = tmp_path / "telegram.json"
        creds.write_text('{"bot_token": "real-tok"}')
        mqtt = _make_mqtt()

        bridge = TelegramBridge.from_credentials(
            mqtt, credentials_dir=tmp_path,
        )
        assert bridge is not None
        assert bridge._token == "real-tok"  # noqa: S105

    def test_from_credentials_missing_file(self, tmp_path) -> None:
        mqtt = _make_mqtt()
        bridge = TelegramBridge.from_credentials(
            mqtt, credentials_dir=tmp_path,
        )
        assert bridge is None

    def test_from_credentials_missing_token(self, tmp_path) -> None:
        creds = tmp_path / "telegram.json"
        creds.write_text('{"other": "val"}')
        mqtt = _make_mqtt()

        bridge = TelegramBridge.from_credentials(
            mqtt, credentials_dir=tmp_path,
        )
        assert bridge is None
