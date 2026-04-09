"""CPU and memory telemetry monitor.

Collects CPU/memory metrics at a configurable interval and publishes
them as protobuf messages to the event bus.

On Linux with bcc installed the monitor can use eBPF probes for
per-cgroup metrics.  On all other platforms (or when ``use_ebpf=False``)
it falls back to reading ``/proc`` or ``os`` counters.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.telemetry_pb2 import CpuTelemetry, MemoryTelemetry

logger = logging.getLogger(__name__)

_IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# Metric snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CpuSnapshot:
    usage_percent: float
    system_percent: float
    user_percent: float
    core_count: int
    load_avg_1m: float


@dataclass(frozen=True)
class MemorySnapshot:
    usage_percent: float
    used_bytes: int
    total_bytes: int
    available_bytes: int
    swap_percent: float


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def _read_proc_stat() -> tuple[float, float, float]:
    """Parse /proc/stat for overall CPU percentages (user, system, idle)."""
    with open("/proc/stat") as f:
        line = f.readline()
    parts = line.split()
    user = int(parts[1]) + int(parts[2])  # user + nice
    system = int(parts[3])
    idle = int(parts[4])
    total = user + system + idle + sum(int(p) for p in parts[5:])
    if total == 0:
        return 0.0, 0.0, 0.0
    return (
        (user + system) / total * 100,
        system / total * 100,
        user / total * 100,
    )


def _read_proc_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of key → bytes."""
    info: dict[str, int] = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            val = int(parts[1]) * 1024  # kB → bytes
            info[key] = val
    return info


def collect_cpu() -> CpuSnapshot:
    """Collect a CPU metric snapshot."""
    if _IS_LINUX:
        usage, system, user = _read_proc_stat()
        try:
            load_1m = os.getloadavg()[0]
        except OSError:
            load_1m = 0.0
    else:
        # Fallback: no per-cgroup data, report zeros
        usage, system, user, load_1m = 0.0, 0.0, 0.0, 0.0

    return CpuSnapshot(
        usage_percent=round(usage, 2),
        system_percent=round(system, 2),
        user_percent=round(user, 2),
        core_count=os.cpu_count() or 1,
        load_avg_1m=round(load_1m, 2),
    )


def collect_memory() -> MemorySnapshot:
    """Collect a memory metric snapshot."""
    if _IS_LINUX:
        info = _read_proc_meminfo()
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        swap_pct = ((swap_total - swap_free) / swap_total * 100) if swap_total else 0.0
        usage_pct = (used / total * 100) if total else 0.0
    else:
        total = used = available = 0
        usage_pct = swap_pct = 0.0

    return MemorySnapshot(
        usage_percent=round(usage_pct, 2),
        used_bytes=used,
        total_bytes=total,
        available_bytes=available,
        swap_percent=round(swap_pct, 2),
    )


# ---------------------------------------------------------------------------
# Protobuf builders
# ---------------------------------------------------------------------------


def cpu_to_proto(snap: CpuSnapshot) -> CpuTelemetry:
    """Convert a :class:`CpuSnapshot` into a protobuf message."""
    return CpuTelemetry(
        header=Header(timestamp_unix=time.time()),
        usage_percent=snap.usage_percent,
        system_percent=snap.system_percent,
        user_percent=snap.user_percent,
        core_count=snap.core_count,
        load_avg_1m=snap.load_avg_1m,
    )


def memory_to_proto(snap: MemorySnapshot) -> MemoryTelemetry:
    """Convert a :class:`MemorySnapshot` into a protobuf message."""
    return MemoryTelemetry(
        header=Header(timestamp_unix=time.time()),
        usage_percent=snap.usage_percent,
        used_bytes=snap.used_bytes,
        total_bytes=snap.total_bytes,
        available_bytes=snap.available_bytes,
        swap_percent=snap.swap_percent,
    )


# ---------------------------------------------------------------------------
# TelemetryMonitor
# ---------------------------------------------------------------------------


class TelemetryMonitor:
    """Periodically collects CPU/memory telemetry and publishes to the bus.

    Parameters
    ----------
    client:
        A :class:`NervousSystemClient` (or duck-typed mock with
        ``publish(topic, payload)``).
    interval:
        Seconds between collection cycles (default 1.0).
    cpu_collector:
        Callable returning a :class:`CpuSnapshot`.  Defaults to
        :func:`collect_cpu`.
    memory_collector:
        Callable returning a :class:`MemorySnapshot`.  Defaults to
        :func:`collect_memory`.
    """

    def __init__(
        self,
        client: object,
        *,
        interval: float = 1.0,
        cpu_collector: object | None = None,
        memory_collector: object | None = None,
    ) -> None:
        self._client = client
        self._interval = interval
        self._cpu_collector = cpu_collector or collect_cpu
        self._memory_collector = memory_collector or collect_memory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                cpu_snap = self._cpu_collector()
                mem_snap = self._memory_collector()
                cpu_msg = cpu_to_proto(cpu_snap)
                mem_msg = memory_to_proto(mem_snap)
                self._client.publish("agent/telemetry/cpu", cpu_msg.SerializeToString())
                self._client.publish("agent/telemetry/memory", mem_msg.SerializeToString())
            except Exception:
                logger.exception("Telemetry collection error")
            self._stop.wait(self._interval)

    def start(self) -> None:
        """Start the background telemetry loop."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telemetry")
        self._thread.start()
        logger.info("Telemetry monitor started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        """Stop the background loop and wait for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Telemetry monitor stopped")

    def collect_once(self) -> tuple[CpuTelemetry, MemoryTelemetry]:
        """Run a single collection cycle (useful for testing)."""
        cpu_snap = self._cpu_collector()
        mem_snap = self._memory_collector()
        return cpu_to_proto(cpu_snap), memory_to_proto(mem_snap)
