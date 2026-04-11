"""MQTT to WebSocket bridge for the OpenBaD Web UI.

This module subscribes to selected MQTT topics and forwards decoded protobuf
payloads to connected WebSocket clients as JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from google.protobuf.json_format import MessageToDict

from openbad.cognitive.config import ProviderConfig, load_cognitive_config
from openbad.cognitive.providers.anthropic import AnthropicProvider
from openbad.cognitive.providers.github_copilot import GitHubCopilotProvider
from openbad.cognitive.providers.ollama import OllamaProvider
from openbad.cognitive.providers.openai_compat import (
    custom_provider,
    groq_provider,
    mistral_provider,
    openai_codex_provider,
    openai_provider,
    openrouter_provider,
    xai_provider,
)
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


# ---------------------------------------------------------------------------
# User presence tracking
# ---------------------------------------------------------------------------


@dataclass
class UserSession:
    """Singleton that tracks whether a human user is actively connected.

    ``is_active`` is ``True`` whenever at least one WebSocket or SSE client
    is connected.  ``last_seen`` records the most recent connect/disconnect
    time so callers can reason about staleness.
    """

    is_active: bool = False
    last_seen: datetime | None = None

    def mark_connected(self) -> None:
        self.is_active = True
        self.last_seen = datetime.now(tz=UTC)

    def mark_disconnected(self) -> None:
        self.is_active = False
        self.last_seen = datetime.now(tz=UTC)


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
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _clients: set[web.WebSocketResponse] = field(default_factory=set, init=False, repr=False)
    _configured_provider_count: int = field(default=0, init=False, repr=False)
    _latest_messages: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _event_clients: dict[web.StreamResponse, asyncio.Lock] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _session: UserSession = field(default_factory=UserSession, init=False, repr=False)

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/events", self._sse_handler)
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
        self._loop = asyncio.get_running_loop()
        self._configured_provider_count = await _count_operational_providers()
        self._mqtt = NervousSystemClient.get_instance(host=self.mqtt_host, port=self.mqtt_port)
        self._mqtt.connect(timeout=5.0)

        for topic, proto_type in TOPIC_PROTO_MAP.items():
            self._mqtt.subscribe(topic, proto_type, self._on_mqtt)

    async def _on_shutdown(self, _app: web.Application) -> None:
        for ws in list(self._clients):
            await ws.close(code=1001, message=b"server shutdown")
        self._clients.clear()

        for stream in list(self._event_clients):
            with contextlib.suppress(Exception):
                await stream.write_eof()
        self._event_clients.clear()

        if self._mqtt is not None:
            self._mqtt.disconnect()
            NervousSystemClient.reset_instance()
            self._mqtt = None

        self._loop = None

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "mqtt_connected": bool(self._mqtt and self._mqtt.is_connected),
                "clients": len(self._clients) + len(self._event_clients),
                "websocket_clients": len(self._clients),
                "event_stream_clients": len(self._event_clients),
            }
        )

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        lock = asyncio.Lock()
        self._event_clients[response] = lock
        self._update_presence(connected=True)
        logger.info("Event stream client connected")

        await self._send_sse(
            response,
            lock,
            {
                "type": "hello",
                "ts": _now_iso(),
                "message": "openbad event stream connected",
            },
        )
        await self._replay_latest_to_event_stream(response, lock)

        try:
            while True:
                transport = request.transport
                if transport is None or transport.is_closing():
                    break
                await asyncio.sleep(5)
                await self._send_sse_comment(response, lock, "keepalive")
        except Exception:
            logger.exception("Event stream handler failed")
        finally:
            self._event_clients.pop(response, None)
            self._update_presence(connected=False)
            logger.info("Event stream client disconnected")
            with contextlib.suppress(Exception):
                await response.write_eof()

        return response

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        self._update_presence(connected=True)
        logger.info("WebSocket client connected")

        await ws.send_json(
            {
                "type": "hello",
                "ts": _now_iso(),
                "message": "openbad websocket bridge connected",
            }
        )
        await self._replay_latest_to_websocket(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data.lower() == "ping":
                        await ws.send_str("pong")
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        except Exception:
            logger.exception("WebSocket handler failed")
        finally:
            self._clients.discard(ws)
            self._update_presence(connected=False)
            logger.info("WebSocket client disconnected (close_code=%s)", ws.close_code)

        return ws

    @property
    def user_session(self) -> UserSession:
        """Return the live user-presence session object."""
        return self._session

    def _update_presence(self, *, connected: bool) -> None:
        """Update :attr:`_session` based on current client count and publish.

        A user is considered *present* when at least one WS or SSE client is
        connected.  The method publishes to :data:`topics.WUI_PRESENCE` when
        the active state changes.
        """
        total = len(self._clients) + len(self._event_clients)
        was_active = self._session.is_active

        if connected:
            self._session.mark_connected()
        else:
            # Client has already been removed from the set before this call in
            # the WS handler, but SSE handler removes it just before calling
            # here too, so total already reflects the post-disconnect state.
            if total > 0:
                self._session.mark_connected()
            else:
                self._session.mark_disconnected()

        if self._session.is_active != was_active and self._mqtt is not None:
            payload = json.dumps({
                "active": self._session.is_active,
                "ts": self._session.last_seen.isoformat() if self._session.last_seen else None,
            }).encode()
            try:
                self._mqtt.publish_bytes(topics.WUI_PRESENCE, payload)
            except Exception:
                logger.debug("Could not publish WUI_PRESENCE, MQTT not ready")

    def _on_mqtt(self, topic: str, payload: Any) -> None:
        """MQTT callback: schedule async fanout onto the aiohttp event loop."""
        message = {
            "type": "event",
            "ts": _now_iso(),
            "topic": topic,
            "payload": self._payload_to_jsonable(topic, payload),
        }
        self._latest_messages[topic] = message
        if self._loop is None:
            logger.debug("No aiohttp event loop available for MQTT fanout")
            return

        future = asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)
        future.add_done_callback(self._log_broadcast_result)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients and not self._event_clients:
            return

        dead_ws: list[web.WebSocketResponse] = []
        for ws in self._clients:
            try:
                await ws.send_str(json.dumps(message))
            except Exception:
                logger.exception("WebSocket broadcast failed; pruning client")
                dead_ws.append(ws)

        for ws in dead_ws:
            self._clients.discard(ws)

        dead_streams: list[web.StreamResponse] = []
        for stream, lock in self._event_clients.items():
            try:
                await self._send_sse(stream, lock, message)
            except Exception:
                logger.exception("Event stream broadcast failed; pruning client")
                dead_streams.append(stream)

        for stream in dead_streams:
            self._event_clients.pop(stream, None)

    async def _replay_latest_to_event_stream(
        self,
        stream: web.StreamResponse,
        lock: asyncio.Lock,
    ) -> None:
        for message in self._latest_messages.values():
            await self._send_sse(stream, lock, message)

    async def _replay_latest_to_websocket(self, ws: web.WebSocketResponse) -> None:
        for message in self._latest_messages.values():
            await ws.send_str(json.dumps(message))


    async def _send_sse(
        self,
        stream: web.StreamResponse,
        lock: asyncio.Lock,
        message: dict[str, Any],
    ) -> None:
        payload = json.dumps(message)
        async with lock:
            await stream.write(f"data: {payload}\n\n".encode())

    async def _send_sse_comment(
        self,
        stream: web.StreamResponse,
        lock: asyncio.Lock,
        comment: str,
    ) -> None:
        async with lock:
            await stream.write(f": {comment}\n\n".encode())

    @staticmethod
    def _log_broadcast_result(future: asyncio.Future[None]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            error = future.exception()
            if error is not None:
                logger.exception("MQTT fanout task failed", exc_info=error)

    def _payload_to_jsonable(self, topic: str, payload: Any) -> dict[str, Any] | str:
        result = _payload_to_jsonable(payload)
        if topic == topics.COGNITIVE_HEALTH and isinstance(result, dict):
            result["configured_provider_count"] = self._configured_provider_count
        return result


def _payload_to_jsonable(payload: Any) -> dict[str, Any] | str:
    if hasattr(payload, "DESCRIPTOR"):
        return MessageToDict(
            payload,
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=True,
        )
    return str(payload)


async def _count_operational_providers() -> int:
    for path in _candidate_cognitive_config_paths():
        if not path.exists():
            continue
        try:
            config = load_cognitive_config(path)
            return await _count_operational_providers_from_config(config.providers)
        except Exception:
            logger.exception("Failed to load cognitive config from %s", path)
            return 0
    return 0


async def _count_operational_providers_from_config(
    providers: list[ProviderConfig],
) -> int:
    count = 0
    for provider in providers:
        if not provider.enabled:
            continue

        adapter = _build_provider_adapter(provider)
        if adapter is None:
            continue

        try:
            status = await adapter.health_check()
        except Exception:
            logger.exception("Failed health check for provider %s", provider.name)
            continue

        if status.available:
            count += 1

    return count


def _build_provider_adapter(provider: ProviderConfig) -> Any | None:
    timeout_s = max(1.0, min(provider.timeout_ms / 1000, 2.0))
    verification_model = {
        "openai": "gpt-4o-mini",
        "openai-codex": "codex",
        "openrouter": "openai/gpt-4o-mini",
        "groq": "llama-3.1-8b-instant",
        "xai": "grok-3-mini",
        "mistral": "mistral-small-latest",
        "anthropic": "claude-sonnet-4-20250514",
        "ollama": "llama3.2",
        "github-copilot": "gpt-4o",
        "custom": "",
    }.get(provider.name, "")
    common = {
        "base_url": provider.base_url,
        "default_model": verification_model,
        "timeout_s": timeout_s,
    }

    if provider.name == "ollama":
        return OllamaProvider(**common)
    if provider.name == "openai":
        return openai_provider(api_key_env=provider.api_key_env or "OPENAI_API_KEY", **common)
    if provider.name == "openai-codex":
        return openai_codex_provider(
            api_key_env=provider.api_key_env or "OPENAI_CODEX_TOKEN",
            **common,
        )
    if provider.name == "openrouter":
        return openrouter_provider(
            api_key_env=provider.api_key_env or "OPENROUTER_API_KEY",
            **common,
        )
    if provider.name == "groq":
        return groq_provider(api_key_env=provider.api_key_env or "GROQ_API_KEY", **common)
    if provider.name == "xai":
        return xai_provider(api_key_env=provider.api_key_env or "XAI_API_KEY", **common)
    if provider.name == "mistral":
        return mistral_provider(
            api_key_env=provider.api_key_env or "MISTRAL_API_KEY",
            **common,
        )
    if provider.name == "anthropic":
        return AnthropicProvider(
            base_url=provider.base_url,
            api_key_env=provider.api_key_env or "ANTHROPIC_API_KEY",
            default_model=verification_model,
            timeout_s=timeout_s,
        )
    if provider.name == "github-copilot":
        return GitHubCopilotProvider(
            default_model=verification_model or "gpt-4o",
            timeout_s=timeout_s,
        )
    if provider.name == "custom":
        return custom_provider(
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            default_model=verification_model,
            timeout_s=timeout_s,
        )

    logger.debug("Unsupported provider for WUI readiness count: %s", provider.name)
    return None


def _candidate_cognitive_config_paths() -> list[Path]:
    return [
        Path("/etc/openbad/cognitive.yaml"),
        Path.home() / ".config" / "openbad" / "cognitive.yaml",
        Path("config/cognitive.yaml"),
    ]


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
