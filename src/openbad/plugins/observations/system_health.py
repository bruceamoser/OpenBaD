"""System-health observation plugin — zero external dependencies.

Monitors CPU percentage, memory percentage, disk I/O latency (ms),
active process count, and uptime hours.  Reuses the interoception
monitor where possible so metrics are not double-collected.
"""

from __future__ import annotations

import os
import platform
import time

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


def _cpu_percent() -> float:
    """Return overall CPU usage percentage."""
    try:
        from openbad.interoception.monitor import collect_cpu

        snap = collect_cpu()
        return snap.usage_percent
    except Exception:  # noqa: BLE001
        return 0.0


def _memory_percent() -> float:
    """Return overall memory usage percentage."""
    try:
        from openbad.interoception.monitor import collect_memory

        snap = collect_memory()
        return snap.usage_percent
    except Exception:  # noqa: BLE001
        return 0.0


def _disk_io_latency_ms() -> float:
    """Best-effort disk I/O latency estimate (ms)."""
    try:
        from openbad.interoception.disk_network import sample_disk_io

        snap = sample_disk_io()
        return snap.latency_ms
    except Exception:  # noqa: BLE001
        return 0.0


def _process_count() -> int:
    """Number of running processes."""
    if platform.system() == "Linux":
        try:
            return len(os.listdir("/proc")) - 2  # rough count
        except OSError:
            pass
    # Fallback: use os.cpu_count as a proxy (not great but safe).
    return os.cpu_count() or 1


_BOOT = time.monotonic()


def _uptime_hours() -> float:
    """Agent uptime in hours (since module load)."""
    return (time.monotonic() - _BOOT) / 3600.0


class SystemHealthPlugin(ObservationPlugin):
    """Built-in plugin that monitors core system-health indicators."""

    @property
    def source_id(self) -> str:
        return "system_health"

    async def observe(self) -> ObservationResult:
        return ObservationResult(
            metrics={
                "cpu_percent": _cpu_percent(),
                "memory_percent": _memory_percent(),
                "disk_io_latency_ms": _disk_io_latency_ms(),
                "process_count": _process_count(),
                "uptime_hours": _uptime_hours(),
            },
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "cpu_percent": {"expected": 30.0, "tolerance": 20.0},
            "memory_percent": {"expected": 50.0, "tolerance": 15.0},
            "disk_io_latency_ms": {"expected": 5.0, "tolerance": 10.0},
            "process_count": {"expected": 150.0, "tolerance": 50.0},
            "uptime_hours": {"expected": 1.0, "tolerance": 24.0},
        }

    @property
    def poll_interval_seconds(self) -> int:
        return 30
