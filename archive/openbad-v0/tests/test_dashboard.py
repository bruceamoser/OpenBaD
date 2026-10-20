"""Tests for openbad.interoception.dashboard — HTTP debug dashboard."""

from __future__ import annotations

import time

import pytest
from aiohttp.test_utils import TestClient

from openbad.interoception.dashboard import (
    DashboardState,
    create_app,
)
from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    TokenTelemetry,
)

# ── DashboardState unit tests ────────────────────────────────────


class TestDashboardState:
    def test_initial_health(self):
        s = DashboardState()
        h = s.get_health()
        assert h["state"] == "UNKNOWN"
        assert h["last_heartbeat"] == 0.0

    def test_set_agent_state(self):
        s = DashboardState()
        s.set_agent_state("ACTIVE")
        h = s.get_health()
        assert h["state"] == "ACTIVE"
        assert h["last_heartbeat"] > 0

    def test_update_cpu(self):
        s = DashboardState()
        msg = CpuTelemetry(
            header=Header(timestamp_unix=1.0),
            usage_percent=55.0,
            core_count=4,
        )
        s.update_cpu(msg.SerializeToString())
        t = s.get_telemetry()
        assert t["cpu"]["usage_percent"] == pytest.approx(55.0)
        assert t["cpu"]["core_count"] == 4

    def test_update_memory(self):
        s = DashboardState()
        msg = MemoryTelemetry(
            header=Header(timestamp_unix=1.0),
            usage_percent=72.0,
            total_bytes=16_000_000,
        )
        s.update_memory(msg.SerializeToString())
        t = s.get_telemetry()
        assert t["memory"]["usage_percent"] == pytest.approx(72.0)

    def test_update_disk(self):
        s = DashboardState()
        msg = DiskTelemetry(
            header=Header(timestamp_unix=1.0),
            io_latency_ms=2.5,
            read_bytes=1024,
        )
        s.update_disk(msg.SerializeToString())
        t = s.get_telemetry()
        assert t["disk"]["io_latency_ms"] == pytest.approx(2.5)

    def test_update_network(self):
        s = DashboardState()
        msg = NetworkTelemetry(
            header=Header(timestamp_unix=1.0),
            bytes_sent=5000,
            bytes_recv=10000,
        )
        s.update_network(msg.SerializeToString())
        t = s.get_telemetry()
        assert t["network"]["bytes_sent"] == 5000

    def test_update_tokens(self):
        s = DashboardState()
        msg = TokenTelemetry(
            header=Header(timestamp_unix=1.0),
            tokens_used=500,
            budget_ceiling=10000,
            budget_remaining_pct=95.0,
        )
        s.update_tokens(msg.SerializeToString())
        t = s.get_telemetry()
        assert t["tokens"]["tokens_used"] == 500

    def test_set_thresholds(self):
        s = DashboardState()
        s.set_thresholds(
            {"cpu_percent": {"warning": 75}},
            [{"metric": "cpu_percent", "severity": "WARNING"}],
        )
        th = s.get_thresholds()
        assert th["config"]["cpu_percent"]["warning"] == 75
        assert len(th["breaches"]) == 1

    def test_set_budget(self):
        s = DashboardState()
        s.set_budget({"remaining_pct": 42.0, "blocked": False})
        b = s.get_budget()
        assert b["remaining_pct"] == 42.0

    def test_empty_telemetry(self):
        s = DashboardState()
        t = s.get_telemetry()
        assert t["cpu"] == {}
        assert t["memory"] == {}


# ── HTTP endpoint integration tests ──────────────────────────────


@pytest.fixture
def populated_state() -> DashboardState:
    s = DashboardState()
    s.set_agent_state("ACTIVE")
    cpu = CpuTelemetry(
        header=Header(timestamp_unix=time.time()),
        usage_percent=33.0,
        core_count=8,
    )
    s.update_cpu(cpu.SerializeToString())
    s.set_budget({"remaining_pct": 80.0})
    s.set_thresholds({"cpu": {"warn": 75}}, [])
    return s


@pytest.fixture
def app(populated_state: DashboardState):
    return create_app(populated_state)


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


class TestHealthEndpoint:
    async def test_returns_json(self, client: TestClient):
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert "state" in data
        assert "last_heartbeat" in data

    async def test_reflects_state(self, client: TestClient):
        resp = await client.get("/health")
        data = await resp.json()
        assert data["state"] == "ACTIVE"


class TestTelemetryEndpoint:
    async def test_returns_json(self, client: TestClient):
        resp = await client.get("/telemetry")
        assert resp.status == 200
        data = await resp.json()
        assert "cpu" in data
        assert "memory" in data
        assert "disk" in data
        assert "network" in data
        assert "tokens" in data

    async def test_reflects_published_data(self, client: TestClient):
        resp = await client.get("/telemetry")
        data = await resp.json()
        assert data["cpu"]["usage_percent"] == pytest.approx(33.0)
        assert data["cpu"]["core_count"] == 8


class TestThresholdsEndpoint:
    async def test_returns_json(self, client: TestClient):
        resp = await client.get("/thresholds")
        assert resp.status == 200
        data = await resp.json()
        assert "config" in data
        assert "breaches" in data


class TestBudgetEndpoint:
    async def test_returns_json(self, client: TestClient):
        resp = await client.get("/budget")
        assert resp.status == 200
        data = await resp.json()
        assert data["remaining_pct"] == 80.0


class TestNotFound:
    async def test_404(self, client: TestClient):
        resp = await client.get("/nonexistent")
        assert resp.status == 404
