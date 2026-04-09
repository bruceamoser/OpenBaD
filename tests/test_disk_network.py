"""Tests for openbad.interoception.disk_network — disk/network telemetry."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openbad.interoception.disk_network import (
    DiskNetworkMonitor,
    DiskSnapshot,
    NetworkSnapshot,
    collect_disk,
    collect_network,
    disk_to_proto,
    network_to_proto,
)
from openbad.nervous_system.schemas.telemetry_pb2 import (
    DiskTelemetry,
    NetworkTelemetry,
)

# ── snapshot constructors ─────────────────────────────────────────


def _disk_snap() -> DiskSnapshot:
    return DiskSnapshot(
        usage_percent=42.5,
        read_bytes=1024,
        write_bytes=2048,
        io_latency_ms=1.5,
        free_bytes=500_000,
    )


def _net_snap() -> NetworkSnapshot:
    return NetworkSnapshot(
        bytes_sent=100_000,
        bytes_recv=200_000,
        packets_sent=50,
        packets_recv=80,
    )


# ── collector fallback (non-Linux) ───────────────────────────────


class TestCollectors:
    def test_collect_disk_returns_snapshot(self):
        snap = collect_disk()
        assert isinstance(snap, DiskSnapshot)

    def test_collect_network_returns_snapshot(self):
        snap = collect_network()
        assert isinstance(snap, NetworkSnapshot)


# ── protobuf schema correctness ──────────────────────────────────


class TestProtoSchema:
    def test_disk_to_proto(self):
        msg = disk_to_proto(_disk_snap())
        assert isinstance(msg, DiskTelemetry)
        assert msg.usage_percent == pytest.approx(42.5)
        assert msg.read_bytes == 1024
        assert msg.write_bytes == 2048
        assert msg.io_latency_ms == pytest.approx(1.5)
        assert msg.free_bytes == 500_000
        assert msg.header.timestamp_unix > 0

    def test_network_to_proto(self):
        msg = network_to_proto(_net_snap())
        assert isinstance(msg, NetworkTelemetry)
        assert msg.bytes_sent == 100_000
        assert msg.bytes_recv == 200_000
        assert msg.packets_sent == 50
        assert msg.packets_recv == 80
        assert msg.header.timestamp_unix > 0

    def test_disk_roundtrip(self):
        msg = disk_to_proto(_disk_snap())
        raw = msg.SerializeToString()
        parsed = DiskTelemetry()
        parsed.ParseFromString(raw)
        assert parsed.usage_percent == pytest.approx(42.5)

    def test_network_roundtrip(self):
        msg = network_to_proto(_net_snap())
        raw = msg.SerializeToString()
        parsed = NetworkTelemetry()
        parsed.ParseFromString(raw)
        assert parsed.bytes_sent == 100_000


# ── monitor daemon ────────────────────────────────────────────────


class TestDiskNetworkMonitor:
    def test_collect_once(self):
        client = MagicMock()
        mon = DiskNetworkMonitor(
            client,
            disk_collector=_disk_snap,
            network_collector=_net_snap,
        )
        disk_msg, net_msg = mon.collect_once()
        assert isinstance(disk_msg, DiskTelemetry)
        assert isinstance(net_msg, NetworkTelemetry)

    def test_publishes_both_topics(self):
        client = MagicMock()
        mon = DiskNetworkMonitor(
            client,
            interval=0.05,
            disk_collector=_disk_snap,
            network_collector=_net_snap,
        )
        mon.start()
        time.sleep(0.2)
        mon.stop()
        topics = [call.args[0] for call in client.publish.call_args_list]
        assert "agent/telemetry/disk" in topics
        assert "agent/telemetry/network" in topics

    def test_stop_idempotent(self):
        client = MagicMock()
        mon = DiskNetworkMonitor(client, disk_collector=_disk_snap)
        mon.stop()  # no-op, never started

    def test_start_idempotent(self):
        client = MagicMock()
        mon = DiskNetworkMonitor(
            client,
            interval=0.05,
            disk_collector=_disk_snap,
            network_collector=_net_snap,
        )
        mon.start()
        mon.start()  # second call is no-op
        mon.stop()


# ── mock data sources for non-Linux ──────────────────────────────


class TestMockDataSources:
    def test_custom_disk_collector(self):
        custom = DiskSnapshot(
            usage_percent=99.0,
            read_bytes=999,
            write_bytes=888,
            io_latency_ms=50.0,
            free_bytes=100,
        )
        client = MagicMock()
        mon = DiskNetworkMonitor(
            client,
            disk_collector=lambda: custom,
            network_collector=_net_snap,
        )
        disk_msg, _ = mon.collect_once()
        assert disk_msg.usage_percent == pytest.approx(99.0)
        assert disk_msg.io_latency_ms == pytest.approx(50.0)

    def test_custom_network_collector(self):
        custom = NetworkSnapshot(
            bytes_sent=1,
            bytes_recv=2,
            packets_sent=3,
            packets_recv=4,
        )
        client = MagicMock()
        mon = DiskNetworkMonitor(
            client,
            disk_collector=_disk_snap,
            network_collector=lambda: custom,
        )
        _, net_msg = mon.collect_once()
        assert net_msg.bytes_sent == 1
        assert net_msg.packets_recv == 4
