"""Tests for daemon lifecycle (start / stop / dry-run)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

from openbad.daemon import Daemon
from openbad.nervous_system import topics


@pytest.fixture
def mock_client():
    with patch("openbad.daemon.NervousSystemClient") as cls:
        instance = MagicMock()
        cls.get_instance.return_value = instance
        yield instance


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
        _mock_mqtt.subscribe.assert_any_call(topics.SCHEDULER_TICK, bytes, d._on_scheduler_tick)
        _mock_mqtt.subscribe.assert_any_call(topics.TASK_WORK_REQUEST, bytes, d._on_task_work_request)
        _mock_mqtt.subscribe.assert_any_call(topics.RESEARCH_WORK_REQUEST, bytes, d._on_research_work_request)
        _mock_mqtt.subscribe.assert_any_call(topics.DOCTOR_CALL, bytes, d._on_doctor_call)


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
    """Subsystem provider composition."""

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

    def test_scheduler_tick_dispatches_worker(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps({"eligible_task_id": "task-1", "eligible_research_id": None}).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_pending_autonomy_work") as worker:
            d._on_scheduler_tick(topics.SCHEDULER_TICK, payload)
        worker.assert_called_once_with()

    def test_scheduler_tick_without_work_dispatches_worker_for_maintenance(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps(
            {
                "eligible_task_id": None,
                "eligible_research_id": None,
                "queued_task_id": None,
                "queued_research_id": None,
            }
        ).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_pending_autonomy_work") as worker:
            d._on_scheduler_tick(topics.SCHEDULER_TICK, payload)
        worker.assert_called_once_with()

    def test_scheduler_tick_with_queued_ids_does_not_dispatch_worker(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps(
            {
                "eligible_task_id": None,
                "eligible_research_id": None,
                "queued_task_id": "task-1",
                "queued_research_id": "research-1",
            }
        ).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_pending_autonomy_work") as worker:
            d._on_scheduler_tick(topics.SCHEDULER_TICK, payload)
        worker.assert_not_called()

    def test_doctor_call_dispatches_worker(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps(
            {"source": "heartbeat", "reason": "endocrine activation detected"}
        ).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_doctor_call") as worker:
            d._on_doctor_call(topics.DOCTOR_CALL, payload)
        worker.assert_called_once_with({"source": "heartbeat", "reason": "endocrine activation detected"})

    def test_task_work_request_dispatches_worker(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps({"mode": "specific", "task_id": "task-1", "source": "chat"}).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_task_call") as worker:
            d._on_task_work_request(topics.TASK_WORK_REQUEST, payload)
        worker.assert_called_once_with({"mode": "specific", "task_id": "task-1", "source": "chat"})

    def test_research_work_request_dispatches_worker(self, mock_client):
        d = Daemon(dry_run=True)
        payload = json.dumps({"mode": "next", "source": "chat"}).encode("utf-8")
        with patch("openbad.autonomy.scheduler_worker.process_research_call") as worker:
            d._on_research_work_request(topics.RESEARCH_WORK_REQUEST, payload)
        worker.assert_called_once_with({"mode": "next", "source": "chat"})


class TestHardwareTelemetryConfig:
    def test_load_hardware_telemetry_interval_default_when_missing(self, tmp_path, monkeypatch):
        import openbad.daemon as daemon

        monkeypatch.setattr(daemon, "_TELEMETRY_CONFIG_PATH", tmp_path / "telemetry.yaml")

        assert daemon._load_hardware_telemetry_interval() == 5.0

    def test_load_hardware_telemetry_interval_from_config(self, tmp_path, monkeypatch):
        import openbad.daemon as daemon

        cfg_path = tmp_path / "telemetry.yaml"
        cfg_path.write_text(yaml.safe_dump({"interval_seconds": 12}))
        monkeypatch.setattr(daemon, "_TELEMETRY_CONFIG_PATH", cfg_path)

        assert daemon._load_hardware_telemetry_interval() == 12.0

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
