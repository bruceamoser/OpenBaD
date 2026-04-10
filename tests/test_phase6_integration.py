"""Phase 6 integration tests (CLI + TUI + WUI surfaces)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from openbad.cli import main
from openbad.wui.server import create_app


@pytest.mark.integration
class TestPhase6CliSurface:
    def test_main_help_contains_phase6_commands(self):
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "setup" in result.output
        assert "tui" in result.output
        assert "wui" in result.output

    def test_setup_help_has_new_flags(self):
        result = CliRunner().invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "--non-interactive" in result.output
        assert "--check" in result.output

    def test_tui_help_is_accessible(self):
        result = CliRunner().invoke(main, ["tui", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output

    def test_wui_help_is_accessible(self):
        result = CliRunner().invoke(main, ["wui", "--help"])
        assert result.exit_code == 0
        assert "--mqtt-host" in result.output
        assert "--mqtt-port" in result.output


@pytest.mark.integration
class TestPhase6WuiRuntime:
    @pytest.mark.asyncio
    async def test_wui_health_and_root_routes(self, aiohttp_client):
        app = create_app(enable_mqtt=False)
        client = await aiohttp_client(app)

        root = await client.get("/")
        assert root.status == 200
        html = await root.text()
        assert "OpenBaD Live Dashboard" in html

        health = await client.get("/health")
        assert health.status == 200
        data = await health.json()
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_websocket_hello_frame(self, aiohttp_client):
        app = create_app(enable_mqtt=False)
        client = await aiohttp_client(app)

        ws = await client.ws_connect("/ws")
        msg = await ws.receive_json()
        assert msg["type"] == "hello"
        assert "connected" in msg["message"]
        await ws.close()


@pytest.mark.integration
class TestPhase6BridgeFanout:
    @pytest.mark.asyncio
    async def test_bridge_broadcasts_json_event(self, aiohttp_client):
        app = create_app(enable_mqtt=False)
        client = await aiohttp_client(app)

        ws = await client.ws_connect("/ws")
        _hello = await ws.receive_json()

        bridge = app["bridge"]
        await bridge._broadcast(  # noqa: SLF001
            {
                "type": "event",
                "ts": "2026-01-01T00:00:00+00:00",
                "topic": "agent/reflex/state",
                "payload": {"current_state": "ACTIVE"},
            }
        )

        raw = await ws.receive_str()
        data = json.loads(raw)
        assert data["topic"] == "agent/reflex/state"
        assert data["payload"]["current_state"] == "ACTIVE"
        await ws.close()
