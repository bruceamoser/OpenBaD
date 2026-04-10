"""Tests for MQTT -> WebSocket bridge (#184)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.wui.bridge import MqttWebSocketBridge, _payload_to_jsonable


class TestPayloadSerialization:
    def test_plain_payload_fallback(self):
        assert _payload_to_jsonable(123) == "123"

    def test_proto_payload_to_dict(self):
        from openbad.nervous_system.schemas.telemetry_pb2 import CpuTelemetry

        payload = CpuTelemetry(usage_percent=12.5, core_count=8)
        as_json = _payload_to_jsonable(payload)
        assert isinstance(as_json, dict)
        assert as_json["usage_percent"] == 12.5
        assert as_json["core_count"] == 8


class TestBridgeLifecycle:
    @pytest.mark.asyncio
    async def test_create_app_routes(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()
        paths = sorted(route.resource.canonical for route in app.router.routes())
        assert "/health" in paths
        assert "/ws" in paths

    @pytest.mark.asyncio
    async def test_startup_connects_and_subscribes(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()

        mock_client = MagicMock()
        with patch("openbad.wui.bridge.NervousSystemClient") as cls:
            cls.get_instance.return_value = mock_client
            await bridge._on_startup(app)

        mock_client.connect.assert_called_once()
        assert mock_client.subscribe.call_count >= 5

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_and_closes_clients(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()

        ws = AsyncMock()
        bridge._clients.add(ws)
        mqtt = MagicMock()
        bridge._mqtt = mqtt

        with patch("openbad.wui.bridge.NervousSystemClient") as cls:
            await bridge._on_shutdown(app)
            cls.reset_instance.assert_called_once()

        ws.close.assert_awaited_once()
        mqtt.disconnect.assert_called_once()
        assert len(bridge._clients) == 0


class TestBroadcasting:
    @pytest.mark.asyncio
    async def test_broadcast_to_clients(self):
        bridge = MqttWebSocketBridge()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        bridge._clients.update({ws1, ws2})

        message = {"type": "event", "topic": "agent/test", "payload": {"ok": True}}
        await bridge._broadcast(message)

        ws1.send_str.assert_awaited_once()
        ws2.send_str.assert_awaited_once()

        sent = ws1.send_str.call_args[0][0]
        decoded = json.loads(sent)
        assert decoded["topic"] == "agent/test"

    @pytest.mark.asyncio
    async def test_broadcast_prunes_dead_clients(self):
        bridge = MqttWebSocketBridge()
        bad = AsyncMock()
        bad.send_str.side_effect = RuntimeError("socket closed")
        good = AsyncMock()
        bridge._clients.update({bad, good})

        await bridge._broadcast({"type": "event", "topic": "x", "payload": {}})

        assert good in bridge._clients
        assert bad not in bridge._clients


class TestMqttCallback:
    @pytest.mark.asyncio
    async def test_on_mqtt_schedules_broadcast(self):
        bridge = MqttWebSocketBridge()
        bridge._broadcast = AsyncMock()  # type: ignore[method-assign]

        task_created = asyncio.Event()

        original_create_task = asyncio.get_running_loop().create_task

        def _wrapped_create_task(coro):
            task_created.set()
            return original_create_task(coro)

        with patch.object(
            asyncio.get_running_loop(),
            "create_task",
            side_effect=_wrapped_create_task,
        ):
            bridge._on_mqtt("agent/test", {"k": "v"})

        await asyncio.wait_for(task_created.wait(), timeout=1.0)
