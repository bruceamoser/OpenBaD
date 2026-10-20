"""Tests for daemon peripheral wiring (#603)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.daemon import Daemon


@pytest.fixture
def daemon() -> Daemon:
    return Daemon(mqtt_host="localhost", mqtt_port=1883, dry_run=True)


class TestDaemonPeripherals:

    def test_init_has_peripheral_attrs(self, daemon: Daemon) -> None:
        assert daemon._telegram_bridge is None
        assert daemon._chat_router is None

    @pytest.mark.asyncio
    async def test_start_peripherals_no_plugins(self, daemon: Daemon) -> None:
        daemon._client = MagicMock()
        with patch(
            "openbad.daemon.load_peripherals_config",
        ) as mock_cfg:
            from openbad.peripherals.config import CorsairConfig
            mock_cfg.return_value = CorsairConfig(plugins=[])
            await daemon._start_peripherals()

        assert daemon._telegram_bridge is None
        assert daemon._chat_router is None

    @pytest.mark.asyncio
    async def test_start_peripherals_telegram_enabled(
        self, daemon: Daemon,
    ) -> None:
        daemon._client = MagicMock()

        from openbad.peripherals.config import CorsairConfig, PluginConfig

        cfg = CorsairConfig(plugins=[
            PluginConfig(name="telegram", enabled=True),
        ])
        mock_bridge = MagicMock()
        mock_bridge.start = AsyncMock()

        with (
            patch("openbad.daemon.load_peripherals_config", return_value=cfg),
            patch(
                "openbad.daemon.TelegramBridge.from_credentials",
                return_value=mock_bridge,
            ),
        ):
            await daemon._start_peripherals()

        mock_bridge.start.assert_awaited_once()
        assert daemon._telegram_bridge is mock_bridge
        assert daemon._chat_router is not None

    @pytest.mark.asyncio
    async def test_start_peripherals_telegram_no_creds(
        self, daemon: Daemon,
    ) -> None:
        daemon._client = MagicMock()

        from openbad.peripherals.config import CorsairConfig, PluginConfig

        cfg = CorsairConfig(plugins=[
            PluginConfig(name="telegram", enabled=True),
        ])

        with (
            patch("openbad.daemon.load_peripherals_config", return_value=cfg),
            patch(
                "openbad.daemon.TelegramBridge.from_credentials",
                return_value=None,
            ),
        ):
            await daemon._start_peripherals()

        assert daemon._telegram_bridge is None
        # Chat router still created because plugin is enabled
        assert daemon._chat_router is not None

    @pytest.mark.asyncio
    async def test_stop_peripherals(self, daemon: Daemon) -> None:
        mock_bridge = MagicMock()
        mock_bridge.stop = AsyncMock()
        mock_router = MagicMock()

        daemon._telegram_bridge = mock_bridge
        daemon._chat_router = mock_router

        await daemon._stop_peripherals()

        mock_bridge.stop.assert_awaited_once()
        mock_router.stop.assert_called_once()
        assert daemon._telegram_bridge is None
        assert daemon._chat_router is None

    @pytest.mark.asyncio
    async def test_stop_peripherals_noop_when_none(
        self, daemon: Daemon,
    ) -> None:
        await daemon._stop_peripherals()
        assert daemon._telegram_bridge is None
        assert daemon._chat_router is None

    def test_resolve_chat_model_success(self, daemon: Daemon) -> None:
        mock_model = MagicMock()
        with patch("openbad.wui.server._read_providers_config") as mock_read, \
             patch("openbad.wui.server._resolve_chat_adapter") as mock_resolve:
            mock_read.return_value = (MagicMock(), MagicMock())
            mock_resolve.return_value = (
                mock_model, "test/model", "test-provider", False, None, None,
            )
            result = daemon._resolve_chat_model()

        assert result[0] is mock_model
        assert result[1] == "test/model"
        assert result[2] == "test-provider"

    def test_resolve_chat_model_failure(self, daemon: Daemon) -> None:
        with patch(
            "openbad.wui.server._read_providers_config",
            side_effect=Exception("no config"),
        ):
            result = daemon._resolve_chat_model()

        assert result == (None, None, "")

    def test_resolve_identity_success(self, daemon: Daemon) -> None:
        mock_user = MagicMock()
        mock_asst = MagicMock()
        mock_factors = MagicMock()
        mock_persist = MagicMock()
        mock_persist.user = mock_user
        mock_persist.assistant = mock_asst
        mock_modulator = MagicMock()
        mock_modulator.factors = mock_factors

        daemon._identity_persistence = mock_persist
        daemon._personality_modulator = mock_modulator

        result = daemon._resolve_identity()

        assert result[0] is mock_user
        assert result[1] is mock_asst
        assert result[2] is mock_factors
        assert result[3] is mock_persist
        assert result[4] is mock_modulator

    def test_resolve_identity_no_persistence(
        self, daemon: Daemon,
    ) -> None:
        result = daemon._resolve_identity()

        assert result == (None, None, None, None, None)
