"""Tests for openbad.interoception.monitor — CPU/memory telemetry."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openbad.interoception.monitor import (
    CpuSnapshot,
    MemorySnapshot,
    TelemetryMonitor,
    cpu_to_proto,
    memory_to_proto,
)
from openbad.nervous_system.schemas.telemetry_pb2 import CpuTelemetry, MemoryTelemetry

# ── Snapshot → Proto ──────────────────────────────────────────────


class TestCpuToProto:
    def test_fields_mapped(self):
        snap = CpuSnapshot(
            usage_percent=42.5,
            system_percent=10.0,
            user_percent=32.5,
            core_count=4,
            load_avg_1m=1.23,
        )
        msg = cpu_to_proto(snap)
        assert isinstance(msg, CpuTelemetry)
        assert msg.usage_percent == 42.5
        assert msg.system_percent == 10.0
        assert msg.user_percent == 32.5
        assert msg.core_count == 4
        assert msg.load_avg_1m == 1.23
        assert msg.header.timestamp_unix > 0

    def test_round_trip_serialization(self):
        snap = CpuSnapshot(75.0, 25.0, 50.0, 8, 2.5)
        data = cpu_to_proto(snap).SerializeToString()
        parsed = CpuTelemetry()
        parsed.ParseFromString(data)
        assert parsed.usage_percent == 75.0


class TestMemoryToProto:
    def test_fields_mapped(self):
        snap = MemorySnapshot(
            usage_percent=60.0,
            used_bytes=6_000_000_000,
            total_bytes=10_000_000_000,
            available_bytes=4_000_000_000,
            swap_percent=5.0,
        )
        msg = memory_to_proto(snap)
        assert isinstance(msg, MemoryTelemetry)
        assert msg.usage_percent == 60.0
        assert msg.used_bytes == 6_000_000_000
        assert msg.total_bytes == 10_000_000_000
        assert msg.available_bytes == 4_000_000_000
        assert msg.swap_percent == 5.0

    def test_round_trip_serialization(self):
        snap = MemorySnapshot(80.0, 8_000, 10_000, 2_000, 10.0)
        data = memory_to_proto(snap).SerializeToString()
        parsed = MemoryTelemetry()
        parsed.ParseFromString(data)
        assert parsed.total_bytes == 10_000


# ── TelemetryMonitor ──────────────────────────────────────────────


def _fake_cpu() -> CpuSnapshot:
    return CpuSnapshot(50.0, 15.0, 35.0, 4, 1.0)


def _fake_memory() -> MemorySnapshot:
    return MemorySnapshot(40.0, 4_000, 10_000, 6_000, 2.0)


@pytest.fixture
def client_mock():
    return MagicMock()


@pytest.fixture
def monitor(client_mock):
    return TelemetryMonitor(
        client_mock,
        interval=0.05,
        cpu_collector=_fake_cpu,
        memory_collector=_fake_memory,
    )


class TestCollectOnce:
    def test_returns_protobuf_pair(self, monitor: TelemetryMonitor):
        cpu_msg, mem_msg = monitor.collect_once()
        assert isinstance(cpu_msg, CpuTelemetry)
        assert isinstance(mem_msg, MemoryTelemetry)
        assert cpu_msg.usage_percent == 50.0
        assert mem_msg.usage_percent == 40.0


class TestPublishing:
    def test_publishes_cpu_and_memory(self, monitor: TelemetryMonitor, client_mock):
        monitor.start()
        time.sleep(0.15)  # long enough for at least 1 cycle
        monitor.stop()

        topics = [c.args[0] for c in client_mock.publish.call_args_list]
        assert "agent/telemetry/cpu" in topics
        assert "agent/telemetry/memory" in topics

    def test_messages_are_protobuf(self, monitor: TelemetryMonitor, client_mock):
        monitor.start()
        time.sleep(0.15)
        monitor.stop()

        for call_obj in client_mock.publish.call_args_list:
            topic, payload = call_obj.args
            if topic == "agent/telemetry/cpu":
                msg = CpuTelemetry()
                msg.ParseFromString(payload)
                assert msg.usage_percent == 50.0
            elif topic == "agent/telemetry/memory":
                msg = MemoryTelemetry()
                msg.ParseFromString(payload)
                assert msg.usage_percent == 40.0


class TestStartStop:
    def test_stop_is_idempotent(self, monitor: TelemetryMonitor):
        monitor.stop()  # no error even if never started

    def test_start_is_idempotent(self, monitor: TelemetryMonitor):
        monitor.start()
        monitor.start()  # second start is a no-op
        monitor.stop()

    def test_collector_error_does_not_crash_loop(self, client_mock):
        call_count = {"n": 0}

        def failing_cpu() -> CpuSnapshot:
            call_count["n"] += 1
            if call_count["n"] == 1:
                msg = "simulated failure"
                raise RuntimeError(msg)
            return _fake_cpu()

        mon = TelemetryMonitor(
            client_mock,
            interval=0.05,
            cpu_collector=failing_cpu,
            memory_collector=_fake_memory,
        )
        mon.start()
        time.sleep(0.2)
        mon.stop()
        # Should have recovered and published after the first failure
        assert client_mock.publish.call_count >= 2
