"""Tests for MQTT -> WebSocket bridge (#184)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.cognitive.config import ProviderConfig
from openbad.wui.bridge import (
    MqttWebSocketBridge,
    _count_operational_providers_from_config,
    _payload_to_jsonable,
)


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

    def test_proto_payload_includes_zero_default_fields(self):
        from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent

        payload = EndocrineEvent(hormone="dopamine", level=0.0)
        as_json = _payload_to_jsonable(payload)
        assert isinstance(as_json, dict)
        assert as_json["level"] == 0.0

    def test_bridge_enriches_cognitive_health_with_provider_count(self):
        from openbad.nervous_system import topics
        from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus

        bridge = MqttWebSocketBridge()
        bridge._configured_provider_count = 2

        as_json = bridge._payload_to_jsonable(
            topics.COGNITIVE_HEALTH,
            ModelHealthStatus(provider="inactive", model_id="none", available=False),
        )

        assert isinstance(as_json, dict)
        assert as_json["configured_provider_count"] == 2


class TestConfigLoading:
    @pytest.mark.asyncio
    async def test_count_operational_providers_only_counts_healthy_enabled(self):
        healthy = AsyncMock()
        healthy.health_check.return_value.available = True

        unhealthy = AsyncMock()
        unhealthy.health_check.return_value.available = False

        providers = [
            ProviderConfig(
                name="ollama",
                base_url="http://localhost:11434",
                model="llama3.2",
                enabled=True,
            ),
            ProviderConfig(
                name="openai",
                base_url="https://api.openai.com/v1",
                model="gpt-4o-mini",
                api_key_env="OPENAI_API_KEY",
                enabled=True,
            ),
            ProviderConfig(
                name="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-sonnet-4-20250514",
                enabled=False,
            ),
        ]

        with patch(
            "openbad.wui.bridge._build_provider_adapter",
            side_effect=[healthy, unhealthy],
        ):
            assert await _count_operational_providers_from_config(providers) == 1



class TestBridgeLifecycle:
    @pytest.mark.asyncio
    async def test_create_app_routes(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()
        paths = sorted(route.resource.canonical for route in app.router.routes())
        assert "/events" in paths
        assert "/health" in paths
        assert "/ws" in paths

    @pytest.mark.asyncio
    async def test_startup_connects_and_subscribes(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()

        mock_client = MagicMock()
        with patch("openbad.wui.bridge.NervousSystemClient") as cls:
            cls.get_instance.return_value = mock_client
            with patch(
                "openbad.wui.bridge._count_operational_providers",
                AsyncMock(return_value=0),
            ):
                await bridge._on_startup(app)

        mock_client.connect.assert_called_once()
        assert mock_client.subscribe.call_count >= 5

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_and_closes_clients(self):
        bridge = MqttWebSocketBridge()
        app = bridge.create_app()

        ws = AsyncMock()
        bridge._clients.add(ws)
        stream = AsyncMock()
        bridge._event_clients[stream] = asyncio.Lock()
        mqtt = MagicMock()
        bridge._mqtt = mqtt

        with patch("openbad.wui.bridge.NervousSystemClient") as cls:
            await bridge._on_shutdown(app)
            cls.reset_instance.assert_called_once()

        ws.close.assert_awaited_once()
        stream.write_eof.assert_awaited_once()
        mqtt.disconnect.assert_called_once()
        assert len(bridge._clients) == 0
        assert len(bridge._event_clients) == 0


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

    @pytest.mark.asyncio
    async def test_broadcast_to_event_stream_clients(self):
        bridge = MqttWebSocketBridge()
        stream = AsyncMock()
        bridge._event_clients[stream] = asyncio.Lock()

        await bridge._broadcast({"type": "event", "topic": "agent/test", "payload": {}})

        stream.write.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_replay_latest_to_event_stream_clients(self):
        bridge = MqttWebSocketBridge()
        stream = AsyncMock()
        lock = asyncio.Lock()
        bridge._latest_messages = {
            "agent/reflex/state": {
                "type": "event",
                "topic": "agent/reflex/state",
                "payload": {"current_state": "IDLE"},
            },
            "agent/endocrine/dopamine": {
                "type": "event",
                "topic": "agent/endocrine/dopamine",
                "payload": {"level": 0.0},
            },
        }

        await bridge._replay_latest_to_event_stream(stream, lock)

        assert stream.write.await_count == 2

    @pytest.mark.asyncio
    async def test_replay_latest_to_websocket_clients(self):
        bridge = MqttWebSocketBridge()
        ws = AsyncMock()
        bridge._latest_messages = {
            "agent/reflex/state": {
                "type": "event",
                "topic": "agent/reflex/state",
                "payload": {"current_state": "IDLE"},
            }
        }

        await bridge._replay_latest_to_websocket(ws)

        ws.send_str.assert_awaited_once()
        sent = json.loads(ws.send_str.call_args.args[0])
        assert sent["topic"] == "agent/reflex/state"


class TestMqttCallback:
    @pytest.mark.asyncio
    async def test_on_mqtt_schedules_broadcast(self):
        bridge = MqttWebSocketBridge()
        bridge._loop = asyncio.get_running_loop()
        bridge._broadcast = AsyncMock()  # type: ignore[method-assign]

        with patch("openbad.wui.bridge.asyncio.run_coroutine_threadsafe") as submit:
            bridge._on_mqtt("agent/test", {"k": "v"})

        submit.assert_called_once()
        submit.call_args.args[0].close()

    def test_on_mqtt_without_loop_is_noop(self):
        bridge = MqttWebSocketBridge()

        with patch("openbad.wui.bridge.asyncio.run_coroutine_threadsafe") as submit:
            bridge._on_mqtt("agent/test", {"k": "v"})

        submit.assert_not_called()
