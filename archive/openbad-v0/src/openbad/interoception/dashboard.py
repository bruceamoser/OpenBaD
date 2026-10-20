"""HTTP debug dashboard for interoception telemetry.

Exposes a lightweight JSON API (aiohttp) for real-time debugging:

- ``GET /health``      — agent state + last heartbeat
- ``GET /telemetry``   — latest CPU, memory, disk, network, token metrics
- ``GET /thresholds``  — threshold config + active breaches
- ``GET /budget``      — token budget status

The server subscribes to the event bus internally so data stays fresh
without polling.
"""

from __future__ import annotations

import logging
import threading
import time

from aiohttp import web

from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    TokenTelemetry,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9100


# ---------------------------------------------------------------------------
# In-memory state store (updated by event bus subscriptions)
# ---------------------------------------------------------------------------


class DashboardState:
    """Thread-safe store for the latest telemetry values."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cpu: dict = {}
        self._memory: dict = {}
        self._disk: dict = {}
        self._network: dict = {}
        self._tokens: dict = {}
        self._thresholds: dict = {}
        self._breaches: list[dict] = []
        self._budget: dict = {}
        self._agent_state: str = "UNKNOWN"
        self._last_heartbeat: float = 0.0

    # -- updaters (called from event bus callbacks) -------------------------

    def update_cpu(self, payload: bytes) -> None:
        msg = CpuTelemetry()
        msg.ParseFromString(payload)
        with self._lock:
            self._cpu = {
                "usage_percent": msg.usage_percent,
                "system_percent": msg.system_percent,
                "user_percent": msg.user_percent,
                "core_count": msg.core_count,
                "load_avg_1m": msg.load_avg_1m,
                "timestamp": msg.header.timestamp_unix,
            }

    def update_memory(self, payload: bytes) -> None:
        msg = MemoryTelemetry()
        msg.ParseFromString(payload)
        with self._lock:
            self._memory = {
                "usage_percent": msg.usage_percent,
                "used_bytes": msg.used_bytes,
                "total_bytes": msg.total_bytes,
                "available_bytes": msg.available_bytes,
                "swap_percent": msg.swap_percent,
                "timestamp": msg.header.timestamp_unix,
            }

    def update_disk(self, payload: bytes) -> None:
        msg = DiskTelemetry()
        msg.ParseFromString(payload)
        with self._lock:
            self._disk = {
                "usage_percent": msg.usage_percent,
                "read_bytes": msg.read_bytes,
                "write_bytes": msg.write_bytes,
                "io_latency_ms": msg.io_latency_ms,
                "free_bytes": msg.free_bytes,
                "timestamp": msg.header.timestamp_unix,
            }

    def update_network(self, payload: bytes) -> None:
        msg = NetworkTelemetry()
        msg.ParseFromString(payload)
        with self._lock:
            self._network = {
                "bytes_sent": msg.bytes_sent,
                "bytes_recv": msg.bytes_recv,
                "packets_sent": msg.packets_sent,
                "packets_recv": msg.packets_recv,
                "timestamp": msg.header.timestamp_unix,
            }

    def update_tokens(self, payload: bytes) -> None:
        msg = TokenTelemetry()
        msg.ParseFromString(payload)
        with self._lock:
            self._tokens = {
                "tokens_used": msg.tokens_used,
                "budget_ceiling": msg.budget_ceiling,
                "budget_remaining_pct": msg.budget_remaining_pct,
                "cost_per_action_avg": msg.cost_per_action_avg,
                "model_tier": msg.model_tier,
                "timestamp": msg.header.timestamp_unix,
            }

    def set_thresholds(self, config: dict, breaches: list[dict]) -> None:
        with self._lock:
            self._thresholds = config
            self._breaches = list(breaches)

    def set_budget(self, budget: dict) -> None:
        with self._lock:
            self._budget = dict(budget)

    def set_agent_state(self, state: str) -> None:
        with self._lock:
            self._agent_state = state
            self._last_heartbeat = time.time()

    # -- readers (called from HTTP handlers) --------------------------------

    def get_health(self) -> dict:
        with self._lock:
            return {
                "state": self._agent_state,
                "last_heartbeat": self._last_heartbeat,
            }

    def get_telemetry(self) -> dict:
        with self._lock:
            return {
                "cpu": dict(self._cpu),
                "memory": dict(self._memory),
                "disk": dict(self._disk),
                "network": dict(self._network),
                "tokens": dict(self._tokens),
            }

    def get_thresholds(self) -> dict:
        with self._lock:
            return {
                "config": dict(self._thresholds),
                "breaches": list(self._breaches),
            }

    def get_budget(self) -> dict:
        with self._lock:
            return dict(self._budget)


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------


async def handle_health(request: web.Request) -> web.Response:
    state: DashboardState = request.app["dashboard_state"]
    return web.json_response(state.get_health())


async def handle_telemetry(request: web.Request) -> web.Response:
    state: DashboardState = request.app["dashboard_state"]
    return web.json_response(state.get_telemetry())


async def handle_thresholds(request: web.Request) -> web.Response:
    state: DashboardState = request.app["dashboard_state"]
    return web.json_response(state.get_thresholds())


async def handle_budget(request: web.Request) -> web.Response:
    state: DashboardState = request.app["dashboard_state"]
    return web.json_response(state.get_budget())


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(state: DashboardState | None = None) -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application()
    app["dashboard_state"] = state or DashboardState()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/telemetry", handle_telemetry)
    app.router.add_get("/thresholds", handle_thresholds)
    app.router.add_get("/budget", handle_budget)
    return app


def run_dashboard(
    state: DashboardState | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> None:
    """Start the dashboard server (blocking)."""
    app = create_app(state)
    web.run_app(app, host=host, port=port, print=logger.info)
