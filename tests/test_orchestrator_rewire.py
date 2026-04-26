"""Tests for #524 — Orchestrator and daemon framework integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openbad.frameworks.langchain_model import OpenBaDChatModel

# ── CognitiveOrchestrator framework wiring ───────────────────────────── #


class TestOrchestratorFrameworkLayer:
    @pytest.fixture()
    def orchestrator(self):
        with (
            patch("openbad.cognitive.orchestrator.UsageRecorder"),
            patch("openbad.cognitive.orchestrator.CognitiveEventLoop"),
        ):
            from openbad.cognitive.orchestrator import CognitiveOrchestrator

            registry = MagicMock()
            router = MagicMock()
            ctx = MagicMock()
            return CognitiveOrchestrator(registry, router, ctx)

    def test_has_chat_model(self, orchestrator) -> None:
        assert isinstance(orchestrator.chat_model, OpenBaDChatModel)

    def test_no_callbacks_by_default(self, orchestrator) -> None:
        assert orchestrator.callbacks == []

    def test_callbacks_injectable(self):
        with (
            patch("openbad.cognitive.orchestrator.UsageRecorder"),
            patch("openbad.cognitive.orchestrator.CognitiveEventLoop"),
        ):
            from openbad.cognitive.orchestrator import CognitiveOrchestrator

            mock_cb = MagicMock()
            orch = CognitiveOrchestrator(
                MagicMock(), MagicMock(), MagicMock(), callbacks=[mock_cb]
            )
            assert mock_cb in orch.callbacks

    def test_callbacks_returns_copy(self, orchestrator) -> None:
        cb1 = orchestrator.callbacks
        cb2 = orchestrator.callbacks
        assert cb1 is not cb2

    def test_event_loop_still_available(self, orchestrator) -> None:
        assert orchestrator.event_loop is not None


# ── Daemon framework integration ─────────────────────────────────────── #


class TestDaemonCrewBridge:
    def test_bridge_property_none_before_start(self) -> None:
        from openbad.daemon import Daemon

        daemon = Daemon(dry_run=True)
        assert daemon.crew_bridge is None

    @pytest.mark.asyncio()
    async def test_bridge_initialized_on_start(self) -> None:
        from openbad.daemon import Daemon

        daemon = Daemon(dry_run=True)

        mock_client = MagicMock()
        mock_client.subscribe = MagicMock()
        mock_client.connect = MagicMock()
        mock_client.disconnect = MagicMock()
        mock_client.publish = MagicMock()

        mock_bridge = MagicMock()

        with (
            patch(
                "openbad.daemon.NervousSystemClient.get_instance",
                return_value=mock_client,
            ),
            patch("openbad.daemon.NervousSystemClient.reset_instance"),
            patch("openbad.daemon.AgentFSM") as mock_fsm_cls,
            patch("openbad.daemon.TelemetryMonitor") as mock_tel,
            patch("openbad.daemon.DiskNetworkMonitor") as mock_dn,
            patch(
                "openbad.daemon._load_hardware_telemetry_interval",
                return_value=5.0,
            ),
            patch(
                "openbad.daemon.CrewMQTTBridge",
                return_value=mock_bridge,
            ) as mock_bridge_cls,
        ):
            mock_fsm = MagicMock()
            mock_fsm_cls.return_value = mock_fsm

            mock_tel_inst = MagicMock()
            mock_tel.return_value = mock_tel_inst

            mock_dn_inst = MagicMock()
            mock_dn.return_value = mock_dn_inst

            await daemon.start()

            # CrewMQTTBridge was constructed and subscribe() was called
            mock_bridge_cls.assert_called_once()
            mock_bridge.subscribe.assert_called_once()

    @pytest.mark.asyncio()
    async def test_bridge_cleared_on_stop(self) -> None:
        from openbad.daemon import Daemon

        daemon = Daemon(dry_run=True)

        mock_client = MagicMock()
        mock_client.subscribe = MagicMock()
        mock_client.connect = MagicMock()
        mock_client.disconnect = MagicMock()
        mock_client.publish = MagicMock()

        with (
            patch(
                "openbad.daemon.NervousSystemClient.get_instance",
                return_value=mock_client,
            ),
            patch("openbad.daemon.NervousSystemClient.reset_instance"),
            patch("openbad.daemon.AgentFSM") as mock_fsm_cls,
            patch("openbad.daemon.TelemetryMonitor") as mock_tel,
            patch("openbad.daemon.DiskNetworkMonitor") as mock_dn,
            patch(
                "openbad.daemon._load_hardware_telemetry_interval",
                return_value=5.0,
            ),
        ):
            mock_fsm = MagicMock()
            mock_fsm_cls.return_value = mock_fsm

            mock_tel.return_value = MagicMock()
            mock_dn.return_value = MagicMock()

            await daemon.start()  # dry_run=True → calls stop() automatically

            # After dry-run stop, bridge should be None
            assert daemon.crew_bridge is None
