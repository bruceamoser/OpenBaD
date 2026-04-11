"""Tests for ReadinessGate — Issue #235."""

from __future__ import annotations

import json
import threading

from openbad.proprioception.readiness import ReadinessGate, ReadinessStatus
from openbad.proprioception.registry import ToolRegistry, ToolRole


class TestGatePass:
    def test_ready_when_all_roles_equipped(self) -> None:
        reg = ToolRegistry()
        reg.register("cli", role=ToolRole.CLI)
        reg.register("mem", role=ToolRole.MEMORY)
        reg.equip(ToolRole.CLI, "cli")
        reg.equip(ToolRole.MEMORY, "mem")

        gate = ReadinessGate(reg, required_roles=[ToolRole.CLI, ToolRole.MEMORY])
        result = gate.wait_ready()
        assert result is ReadinessStatus.READY
        assert gate.status is ReadinessStatus.READY

    def test_ready_with_no_requirements(self) -> None:
        reg = ToolRegistry()
        gate = ReadinessGate(reg, required_roles=[])
        assert gate.wait_ready() is ReadinessStatus.READY

    def test_ready_with_none_requirements(self) -> None:
        reg = ToolRegistry()
        gate = ReadinessGate(reg, required_roles=None)
        assert gate.wait_ready() is ReadinessStatus.READY

    def test_check_returns_true(self) -> None:
        reg = ToolRegistry()
        reg.register("cli", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "cli")
        gate = ReadinessGate(reg, required_roles=[ToolRole.CLI])
        assert gate.check()

    def test_check_returns_false(self) -> None:
        reg = ToolRegistry()
        gate = ReadinessGate(reg, required_roles=[ToolRole.CLI])
        assert not gate.check()


class TestGateTimeout:
    def test_timeout_yields_degraded(self) -> None:
        reg = ToolRegistry()
        gate = ReadinessGate(
            reg,
            required_roles=[ToolRole.CLI],
            timeout=0.1,
            poll_interval=0.05,
        )
        result = gate.wait_ready()
        assert result is ReadinessStatus.DEGRADED
        assert gate.status is ReadinessStatus.DEGRADED

    def test_cortisol_bumped_on_timeout(self) -> None:
        calls: list[bool] = []

        def cortisol() -> float:
            calls.append(True)
            return 0.3

        reg = ToolRegistry()
        gate = ReadinessGate(
            reg,
            required_roles=[ToolRole.MEMORY],
            timeout=0.1,
            poll_interval=0.05,
            cortisol_hook=cortisol,
        )
        gate.wait_ready()
        assert len(calls) == 1


class TestDegradedStartup:
    def test_publishes_waiting_then_degraded(self) -> None:
        published: list[tuple[str, bytes]] = []

        def pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        reg = ToolRegistry()
        gate = ReadinessGate(
            reg,
            required_roles=[ToolRole.CLI],
            timeout=0.1,
            poll_interval=0.05,
            publish_fn=pub,
        )
        gate.wait_ready()

        readiness_pubs = [json.loads(p)["status"] for t, p in published if "readiness" in t]
        assert "waiting" in readiness_pubs
        assert "degraded" in readiness_pubs

    def test_publishes_waiting_then_ready(self) -> None:
        published: list[tuple[str, bytes]] = []

        def pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        reg = ToolRegistry(publish_fn=pub)
        reg.register("cli", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "cli")

        gate = ReadinessGate(
            reg,
            required_roles=[ToolRole.CLI],
            publish_fn=pub,
        )
        gate.wait_ready()
        readiness_pubs = [json.loads(p)["status"] for t, p in published if "readiness" in t]
        assert "waiting" in readiness_pubs
        assert "ready" in readiness_pubs


class TestLateFulfillment:
    def test_roles_equipped_during_wait(self) -> None:
        reg = ToolRegistry()
        reg.register("cli", role=ToolRole.CLI)
        gate = ReadinessGate(
            reg,
            required_roles=[ToolRole.CLI],
            timeout=5.0,
            poll_interval=0.05,
        )

        def equip_later() -> None:
            import time
            time.sleep(0.15)
            reg.equip(ToolRole.CLI, "cli")

        t = threading.Thread(target=equip_later)
        t.start()
        result = gate.wait_ready()
        t.join()
        assert result is ReadinessStatus.READY
