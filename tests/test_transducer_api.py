"""Tests for the transducer API endpoints."""

from __future__ import annotations

import json
import stat
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from openbad.peripherals.config import CorsairConfig, PluginConfig
from openbad.wui.transducer_api import setup_transducer_routes


def _sample_config() -> CorsairConfig:
    return CorsairConfig(
        plugins=[
            PluginConfig(name="discord", enabled=True, credentials_file="discord.json"),
            PluginConfig(name="slack", enabled=False),
            PluginConfig(name="gmail", enabled=True),
        ],
    )


def _make_app() -> web.Application:
    app = web.Application()
    setup_transducer_routes(app)
    return app


# ── GET /api/transducers ────────────────────────────────────────── #


class TestGetTransducers:
    @pytest.fixture(autouse=True)
    def _patch_config(self):
        with patch(
            "openbad.wui.transducer_api.load_peripherals_config",
            return_value=_sample_config(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_returns_plugin_list(self, aiohttp_client) -> None:
        client = await aiohttp_client(_make_app())
        resp = await client.get("/api/transducers")
        assert resp.status == 200
        data = await resp.json()
        assert "plugins" in data
        assert len(data["plugins"]) == 3

    @pytest.mark.asyncio
    async def test_plugin_shape(self, aiohttp_client) -> None:
        client = await aiohttp_client(_make_app())
        resp = await client.get("/api/transducers")
        data = await resp.json()
        p = data["plugins"][0]
        assert p["name"] == "discord"
        assert p["enabled"] is True
        assert "health" in p
        assert "has_credentials" in p

    @pytest.mark.asyncio
    async def test_disabled_plugin(self, aiohttp_client) -> None:
        client = await aiohttp_client(_make_app())
        resp = await client.get("/api/transducers")
        data = await resp.json()
        slack = next(p for p in data["plugins"] if p["name"] == "slack")
        assert slack["enabled"] is False


# ── PUT /api/transducers/{plugin} ───────────────────────────────── #


class TestPutTransducer:
    @pytest.fixture(autouse=True)
    def _patch_config(self):
        with patch(
            "openbad.wui.transducer_api.load_peripherals_config",
            return_value=_sample_config(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_not_found(self, aiohttp_client) -> None:
        client = await aiohttp_client(_make_app())
        resp = await client.put(
            "/api/transducers/nonexistent",
            json={"enabled": True},
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_saves_credentials(self, aiohttp_client, tmp_path) -> None:
        with patch(
            "openbad.wui.transducer_api._CREDS_DIR", tmp_path,
        ), patch(
            "openbad.wui.transducer_api._update_plugin_enabled",
        ):
            client = await aiohttp_client(_make_app())
            resp = await client.put(
                "/api/transducers/discord",
                json={"credentials": {"api_key": "test-key-123"}},  # noqa: S106
            )
            assert resp.status == 200

            creds_file = tmp_path / "discord.json"
            assert creds_file.exists()
            creds = json.loads(creds_file.read_text())
            assert creds["api_key"] == "test-key-123"

            # Verify 0600 permissions
            mode = creds_file.stat().st_mode
            assert mode & stat.S_IRUSR  # owner read
            assert mode & stat.S_IWUSR  # owner write
            assert not mode & stat.S_IRGRP  # no group read
            assert not mode & stat.S_IROTH  # no other read

    @pytest.mark.asyncio
    async def test_toggle_enabled(self, aiohttp_client) -> None:
        with patch(
            "openbad.wui.transducer_api._update_plugin_enabled",
        ) as mock_update:
            client = await aiohttp_client(_make_app())
            resp = await client.put(
                "/api/transducers/slack",
                json={"enabled": True},
            )
            assert resp.status == 200
            mock_update.assert_called_once_with("slack", True)


# ── GET /api/transducers/{plugin}/health ────────────────────────── #


class TestGetTransducerHealth:
    @pytest.mark.asyncio
    async def test_unknown_health(self, aiohttp_client) -> None:
        client = await aiohttp_client(_make_app())
        resp = await client.get("/api/transducers/discord/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["plugin"] == "discord"
        assert data["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_cached_health(self, aiohttp_client) -> None:
        app = _make_app()
        app["_transducers_health"] = {"discord": "healthy"}
        client = await aiohttp_client(app)
        resp = await client.get("/api/transducers/discord/health")
        data = await resp.json()
        assert data["status"] == "healthy"


# ── POST /api/transducers/{plugin}/test ─────────────────────────── #


class TestPostTransducerTest:
    @pytest.mark.asyncio
    async def test_test_message(self, aiohttp_client) -> None:
        mock_tool = MagicMock()
        mock_tool.name = "transmit_message"

        async def mock_run(**kwargs):
            return "sent"

        mock_tool.run = mock_run

        mock_server = MagicMock()
        mock_server.list_tools.return_value = [mock_tool]

        with patch(
            "openbad.skills.server.skill_server",
            mock_server,
        ):
            client = await aiohttp_client(_make_app())
            resp = await client.post(
                "/api/transducers/discord/test",
                json={"target": "general", "content": "hello"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
