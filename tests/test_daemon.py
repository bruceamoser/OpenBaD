"""Tests for daemon lifecycle (start / stop / dry-run)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from openbad.daemon import Daemon


class TestDaemonInit:
    """Construction and default state."""

    def test_defaults(self):
        d = Daemon()
        assert d._mqtt_host == "localhost"
        assert d._mqtt_port == 1883
        assert not d._dry_run
        assert not d.is_running

    def test_custom_params(self):
        d = Daemon(mqtt_host="10.0.0.1", mqtt_port=8883, dry_run=True)
        assert d._mqtt_host == "10.0.0.1"
        assert d._mqtt_port == 8883
        assert d._dry_run


class TestDaemonDryRun:
    """Dry-run validates config without blocking."""

    @pytest.fixture
    def _mock_mqtt(self):
        with patch("openbad.daemon.NervousSystemClient") as cls:
            instance = MagicMock()
            cls.get_instance.return_value = instance
            yield instance

    async def test_dry_run_starts_and_stops(self, _mock_mqtt):
        d = Daemon(dry_run=True)
        await d.start()
        # After dry-run, daemon should have stopped itself
        assert not d.is_running
        assert d._client is None

    async def test_dry_run_connects_mqtt(self, _mock_mqtt):
        d = Daemon(dry_run=True)
        await d.start()
        _mock_mqtt.connect.assert_called_once()


class TestDaemonStartStop:
    """Normal start/stop lifecycle."""

    @pytest.fixture
    def mock_client(self):
        with patch("openbad.daemon.NervousSystemClient") as cls:
            instance = MagicMock()
            cls.get_instance.return_value = instance
            yield instance

    async def test_start_then_stop(self, mock_client):
        d = Daemon()
        # Start in background, let it initialise, then stop
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.05)  # let startup complete
        assert d.is_running
        assert d.fsm is not None
        assert d.endocrine is not None
        assert d.client is mock_client

        await d.stop()
        await task
        assert not d.is_running
        assert d._client is None
        mock_client.disconnect.assert_called_once()

    async def test_request_stop_unblocks_loop(self, mock_client):
        d = Daemon()
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.05)
        d.request_stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert not d.is_running

    async def test_stop_idempotent(self, mock_client):
        d = Daemon()
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.05)
        await d.stop()
        await d.stop()  # second stop should not raise
        await task


class TestDaemonSubsystems:
    """Subsystem wiring."""

    @pytest.fixture
    def mock_client(self):
        with patch("openbad.daemon.NervousSystemClient") as cls:
            instance = MagicMock()
            cls.get_instance.return_value = instance
            yield instance

    async def test_fsm_initialised_with_client(self, mock_client):
        d = Daemon()
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.05)
        assert d.fsm is not None
        assert d.fsm._client is mock_client  # noqa: SLF001
        await d.stop()
        await task

    async def test_endocrine_controller_created(self, mock_client):
        d = Daemon()
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.05)
        assert d.endocrine is not None
        state = d.endocrine.get_state()
        assert state.dopamine == 0.0
        await d.stop()
        await task

    async def test_start_publishes_initial_dashboard_topics(self, mock_client):
        d = Daemon()
        task = asyncio.create_task(d.start())
        await asyncio.sleep(0.1)

        published_topics = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "agent/reflex/state" in published_topics
        assert "agent/endocrine/dopamine" in published_topics
        assert "agent/endocrine/adrenaline" in published_topics
        assert "agent/endocrine/cortisol" in published_topics
        assert "agent/endocrine/endorphin" in published_topics
        assert "agent/telemetry/cpu" in published_topics
        assert "agent/telemetry/memory" in published_topics
        assert "agent/telemetry/disk" in published_topics
        assert "agent/telemetry/network" in published_topics
        assert "agent/telemetry/tokens" in published_topics
        assert "agent/cognitive/health" in published_topics

        await d.stop()
        await task
