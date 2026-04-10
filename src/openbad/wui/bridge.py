"""MQTT to WebSocket bridge for the OpenBaD Web UI.

This module subscribes to selected MQTT topics and forwards decoded protobuf
payloads to connected WebSocket clients as JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web
from google.protobuf.json_format import MessageToDict

from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient
from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus, ReasoningResponse
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexState
from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    TokenTelemetry,
)

logger = logging.getLogger(__name__)


TOPIC_PROTO_MAP: dict[str, type] = {
    topics.ENDOCRINE_ALL: EndocrineEvent,
    topics.REFLEX_STATE: ReflexState,
    topics.TELEMETRY_CPU: CpuTelemetry,
    topics.TELEMETRY_MEMORY: MemoryTelemetry,
    topics.TELEMETRY_DISK: DiskTelemetry,
    topics.TELEMETRY_NETWORK: NetworkTelemetry,
    topics.TELEMETRY_TOKENS: TokenTelemetry,
    topics.COGNITIVE_HEALTH: ModelHealthStatus,
    topics.COGNITIVE_RESPONSE: ReasoningResponse,
}


@dataclass
class MqttWebSocketBridge:
    """Bridge MQTT telemetry to connected browser clients over WebSockets."""

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    _app: web.Application | None = field(default=None, init=False, repr=False)
    _runner: web.AppRunner | None = field(default=None, init=False, repr=False)
    _site: web.TCPSite | None = field(default=None, init=False, repr=False)
    _mqtt: NervousSystemClient | None = field(default=None, init=False, repr=False)
    _clients: set[web.WebSocketResponse] = field(default_factory=set, init=False, repr=False)

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/ws", self._ws_handler)
        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)
        self._app = app
        return app

    async def start(self, host: str = "127.0.0.1", port: int = 9200) -> None:
        if self._app is None:
            self.create_app()
        assert self._app is not None

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=host, port=port)
        await self._site.start()
        logger.info("WebSocket bridge started at http://%s:%s", host, port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _on_startup(self, _app: web.Application) -> None:
        self._mqtt = NervousSystemClient.get_instance(host=self.mqtt_host, port=self.mqtt_port)
        self._mqtt.connect(timeout=5.0)

        for topic, proto_type in TOPIC_PROTO_MAP.items():
            self._mqtt.subscribe(topic, proto_type, self._on_mqtt)

    async def _on_shutdown(self, _app: web.Application) -> None:
        for ws in list(self._clients):
            await ws.close(code=1001, message=b"server shutdown")
        self._clients.clear()

        if self._mqtt is not None:
            self._mqtt.disconnect()
            NervousSystemClient.reset_instance()
            self._mqtt = None

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "mqtt_connected": bool(self._mqtt and self._mqtt.is_connected),
                "clients": len(self._clients),
            }
        )

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self._clients.add(ws)

        await ws.send_json(
            {
                "type": "hello",
                "ts": _now_iso(),
                "message": "openbad websocket bridge connected",
            }
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data.lower() == "ping":
                        await ws.send_str("pong")
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            self._clients.discard(ws)

        return ws

    def _on_mqtt(self, topic: str, payload: Any) -> None:
        """MQTT callback: schedule async fanout onto current event loop."""
        message = {
            "type": "event",
            "ts": _now_iso(),
            "topic": topic,
            "payload": _payload_to_jsonable(payload),
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(message))
        except RuntimeError:
            logger.debug("No running event loop available for MQTT fanout")

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients:
            return

        dead: list[web.WebSocketResponse] = []
        for ws in self._clients:
            try:
                await ws.send_str(json.dumps(message))
            except Exception:  # noqa: BLE001
                dead.append(ws)

        for ws in dead:
            self._clients.discard(ws)


def _payload_to_jsonable(payload: Any) -> dict[str, Any] | str:
    if hasattr(payload, "DESCRIPTOR"):
        return MessageToDict(payload, preserving_proto_field_name=True)
    return str(payload)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
