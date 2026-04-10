"""Disk I/O and network telemetry collectors and publishers.

Publishes :class:`DiskTelemetry` and :class:`NetworkTelemetry` protobuf
messages to ``agent/telemetry/disk`` and ``agent/telemetry/network`` at
a configurable interval (default 5 s).

On Linux the collectors read from ``/proc/diskstats`` and
``/proc/net/dev``.  On other platforms they return zeroed snapshots so
that the rest of the pipeline can still be exercised in tests.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.telemetry_pb2 import (
    DiskTelemetry,
    NetworkTelemetry,
)

logger = logging.getLogger(__name__)

_IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiskSnapshot:
    usage_percent: float
    read_bytes: int
    write_bytes: int
    io_latency_ms: float
    free_bytes: int


@dataclass(frozen=True)
class NetworkSnapshot:
    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int


# ---------------------------------------------------------------------------
# Collectors — Linux /proc readers with cross-platform fallbacks
# ---------------------------------------------------------------------------


def _read_diskstats() -> tuple[int, int]:
    """Return cumulative (read_bytes, write_bytes) from /proc/diskstats."""
    total_read = 0
    total_write = 0
    with open("/proc/diskstats") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue
            # fields: major minor name reads_completed ... sectors_read
            #         writes_completed ... sectors_written ...
            sectors_read = int(parts[5])
            sectors_written = int(parts[9])
            total_read += sectors_read * 512
            total_write += sectors_written * 512
    return total_read, total_write


def _read_statvfs() -> tuple[float, int]:
    """Return (usage_percent, free_bytes) for the root filesystem."""
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        usage = ((total - free) / total * 100) if total else 0.0
        return round(usage, 2), free
    except (OSError, AttributeError):
        return 0.0, 0


def collect_disk() -> DiskSnapshot:
    """Collect a disk I/O metric snapshot."""
    if _IS_LINUX:
        read_b, write_b = _read_diskstats()
        usage_pct, free_b = _read_statvfs()
        # io_latency_ms requires eBPF or /sys/block; approximate as 0
        io_lat = 0.0
    else:
        read_b = write_b = free_b = 0
        usage_pct = io_lat = 0.0

    return DiskSnapshot(
        usage_percent=usage_pct,
        read_bytes=read_b,
        write_bytes=write_b,
        io_latency_ms=io_lat,
        free_bytes=free_b,
    )


def _read_proc_net_dev() -> tuple[int, int, int, int]:
    """Return (bytes_recv, packets_recv, bytes_sent, packets_sent)."""
    total_recv = total_sent = 0
    total_pkt_recv = total_pkt_sent = 0
    with open("/proc/net/dev") as f:
        for line in f:
            if ":" not in line:
                continue
            _, data = line.split(":", 1)
            fields = data.split()
            if len(fields) < 10:
                continue
            total_recv += int(fields[0])
            total_pkt_recv += int(fields[1])
            total_sent += int(fields[8])
            total_pkt_sent += int(fields[9])
    return total_recv, total_pkt_recv, total_sent, total_pkt_sent


def collect_network() -> NetworkSnapshot:
    """Collect a network I/O metric snapshot."""
    if _IS_LINUX:
        recv, pkt_recv, sent, pkt_sent = _read_proc_net_dev()
    else:
        recv = sent = pkt_recv = pkt_sent = 0

    return NetworkSnapshot(
        bytes_sent=sent,
        bytes_recv=recv,
        packets_sent=pkt_sent,
        packets_recv=pkt_recv,
    )


# ---------------------------------------------------------------------------
# Protobuf builders
# ---------------------------------------------------------------------------


def disk_to_proto(snap: DiskSnapshot) -> DiskTelemetry:
    """Convert a :class:`DiskSnapshot` into a protobuf message."""
    return DiskTelemetry(
        header=Header(timestamp_unix=time.time()),
        usage_percent=snap.usage_percent,
        read_bytes=snap.read_bytes,
        write_bytes=snap.write_bytes,
        io_latency_ms=snap.io_latency_ms,
        free_bytes=snap.free_bytes,
    )


def network_to_proto(snap: NetworkSnapshot) -> NetworkTelemetry:
    """Convert a :class:`NetworkSnapshot` into a protobuf message."""
    return NetworkTelemetry(
        header=Header(timestamp_unix=time.time()),
        bytes_sent=snap.bytes_sent,
        bytes_recv=snap.bytes_recv,
        packets_sent=snap.packets_sent,
        packets_recv=snap.packets_recv,
    )


# ---------------------------------------------------------------------------
# DiskNetworkMonitor — daemon thread
# ---------------------------------------------------------------------------


class DiskNetworkMonitor:
    """Periodically collects disk/network telemetry and publishes.

    Parameters
    ----------
    client:
        Object with ``publish(topic, payload)`` method.
    interval:
        Seconds between collection cycles (default 5.0).
    disk_collector:
        Callable returning a :class:`DiskSnapshot`.
    network_collector:
        Callable returning a :class:`NetworkSnapshot`.
    """

    def __init__(
        self,
        client: object,
        *,
        interval: float = 5.0,
        disk_collector: object | None = None,
        network_collector: object | None = None,
    ) -> None:
        self._client = client
        self._interval = interval
        self._disk_collector = disk_collector or collect_disk
        self._network_collector = network_collector or collect_network
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                disk_snap = self._disk_collector()
                net_snap = self._network_collector()
                disk_msg = disk_to_proto(disk_snap)
                net_msg = network_to_proto(net_snap)
                self._client.publish(
                    "agent/telemetry/disk",
                    disk_msg,
                )
                self._client.publish(
                    "agent/telemetry/network",
                    net_msg,
                )
            except Exception:
                logger.exception("Disk/network telemetry error")
            self._stop.wait(self._interval)

    def start(self) -> None:
        """Start the background collection loop."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="disk-net-telemetry",
        )
        self._thread.start()
        logger.info(
            "Disk/network monitor started (interval=%.1fs)",
            self._interval,
        )

    def stop(self) -> None:
        """Stop the background loop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Disk/network monitor stopped")

    def collect_once(
        self,
    ) -> tuple[DiskTelemetry, NetworkTelemetry]:
        """Run a single collection cycle (useful for testing)."""
        disk_snap = self._disk_collector()
        net_snap = self._network_collector()
        return disk_to_proto(disk_snap), network_to_proto(net_snap)
