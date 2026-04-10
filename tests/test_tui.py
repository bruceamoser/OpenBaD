"""Tests for the TUI scaffold (app, mqtt_feed, CLI command)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openbad.tui.app import OpenBaDApp, PlaceholderPanel, Sidebar, StatusPanel
from openbad.tui.mqtt_feed import MqttConnected, MqttDisconnected, MqttFeed, MqttPayload

# ── MqttFeed unit tests ─────────────────────────────────────────────


class TestMqttFeed:
    def test_defaults(self):
        feed = MqttFeed()
        assert feed.host == "localhost"
        assert feed.port == 1883
        assert feed.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_success(self):
        app = MagicMock()
        app.post_message = MagicMock()
        mock_client = MagicMock()

        with patch(
            "openbad.nervous_system.client.NervousSystemClient"
        ) as cls:
            cls.get_instance.return_value = mock_client
            feed = MqttFeed(host="test", port=9999)
            await feed.connect(app)

        assert feed.is_connected is True
        cls.get_instance.assert_called_once_with(host="test", port=9999)
        mock_client.connect.assert_called_once_with(timeout=5.0)
        # Should have posted MqttConnected
        posted = [c[0][0] for c in app.post_message.call_args_list]
        assert any(isinstance(m, MqttConnected) for m in posted)

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        app = MagicMock()
        app.post_message = MagicMock()

        with patch(
            "openbad.nervous_system.client.NervousSystemClient"
        ) as cls:
            cls.get_instance.return_value = MagicMock(
                connect=MagicMock(side_effect=ConnectionError("nope"))
            )
            feed = MqttFeed()
            await feed.connect(app)

        assert feed.is_connected is False
        posted = [c[0][0] for c in app.post_message.call_args_list]
        assert any(isinstance(m, MqttDisconnected) for m in posted)

    @pytest.mark.asyncio
    async def test_disconnect(self):
        app = MagicMock()
        mock_client = MagicMock()

        with patch(
            "openbad.nervous_system.client.NervousSystemClient"
        ) as cls:
            cls.get_instance.return_value = mock_client
            feed = MqttFeed()
            await feed.connect(app)
            assert feed.is_connected is True

            await feed.disconnect()
            assert feed.is_connected is False
            mock_client.disconnect.assert_called_once()
            cls.reset_instance.assert_called_once()

    def test_subscribe_without_connect(self):
        feed = MqttFeed()
        # Should not raise, just warn
        feed.subscribe("agent/telemetry/cpu")

    @pytest.mark.asyncio
    async def test_subscribe_with_proto(self):
        app = MagicMock()
        mock_client = MagicMock()
        proto_type = MagicMock()

        with patch(
            "openbad.nervous_system.client.NervousSystemClient"
        ) as cls:
            cls.get_instance.return_value = mock_client
            feed = MqttFeed()
            await feed.connect(app)
            feed.subscribe("agent/telemetry/cpu", proto_type)

        mock_client.subscribe.assert_called_once()
        call_args = mock_client.subscribe.call_args
        assert call_args[0][0] == "agent/telemetry/cpu"
        assert call_args[0][1] is proto_type


# ── MqttPayload message ─────────────────────────────────────────────


class TestMqttPayload:
    def test_attributes(self):
        msg = MqttPayload(topic="agent/reflex/state", payload={"state": "IDLE"})
        assert msg.topic == "agent/reflex/state"
        assert msg.payload == {"state": "IDLE"}


# ── Widget instantiation ────────────────────────────────────────────


class TestWidgets:
    def test_status_panel_instance(self):
        panel = StatusPanel()
        assert isinstance(panel, StatusPanel)

    def test_placeholder_panel_instance(self):
        panel = PlaceholderPanel("test content")
        assert isinstance(panel, PlaceholderPanel)

    def test_sidebar_instance(self):
        sidebar = Sidebar()
        assert isinstance(sidebar, Sidebar)


# ── App instantiation ───────────────────────────────────────────────


class TestOpenBaDApp:
    def test_app_creation(self):
        app = OpenBaDApp(mqtt_host="testhost", mqtt_port=9999)
        assert app.feed.host == "testhost"
        assert app.feed.port == 9999
        assert app.TITLE == "OpenBaD"

    def test_app_default_params(self):
        app = OpenBaDApp()
        assert app.feed.host == "localhost"
        assert app.feed.port == 1883

    def test_app_has_bindings(self):
        app = OpenBaDApp()
        binding_keys = [b.key for b in app.BINDINGS]
        assert "q" in binding_keys
        assert "d" in binding_keys
        assert "r" in binding_keys


# ── CLI command ──────────────────────────────────────────────────────


class TestTuiCliCommand:
    def test_tui_command_exists(self):
        from click.testing import CliRunner

        from openbad.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["tui", "--help"])
        assert result.exit_code == 0
        assert "terminal ui" in result.output.lower()

    def test_tui_command_accepts_host_port(self):
        from click.testing import CliRunner

        from openbad.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["tui", "--help"])
        assert "--host" in result.output
        assert "--port" in result.output
