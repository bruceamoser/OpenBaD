"""Tests for sensory health integration — Issue #231."""

from __future__ import annotations

import json

from openbad.proprioception.registry import HealthStatus, ToolRegistry
from openbad.sensory.health import (
    SensoryHealthEvent,
    check_sensory_health,
    register_sensory_tools,
)

# ── Registration ──────────────────────────────────────────────────── #


class TestRegistration:
    def test_registers_three_tools(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(registry)
        names = {t.name for t in registry.get_all_tools()}
        assert names == {"sensory.vision", "sensory.hearing", "sensory.speech"}

    def test_all_available_after_registration(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(registry)
        for tool in registry.get_all_tools():
            assert tool.status is HealthStatus.AVAILABLE

    def test_health_checks_attached(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(
            registry,
            vision_check=lambda: True,
            hearing_check=lambda: True,
            speech_check=lambda: True,
        )
        for tool in registry.get_all_tools():
            assert tool.health_check is not None


# ── Heartbeat ─────────────────────────────────────────────────────── #


class TestHeartbeat:
    def test_heartbeat_updates_timestamp(self) -> None:
        import time

        registry = ToolRegistry()
        register_sensory_tools(registry)
        old_ts = registry.get_all_tools()[0].last_heartbeat
        time.sleep(0.01)
        registry.heartbeat("sensory.vision")
        new_ts = next(t for t in registry.get_all_tools() if t.name == "sensory.vision")
        assert new_ts.last_heartbeat > old_ts

    def test_heartbeat_revives_degraded(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(registry)
        registry.mark_degraded("sensory.vision", "test")
        assert next(
            t for t in registry.get_all_tools() if t.name == "sensory.vision"
        ).status is HealthStatus.DEGRADED
        registry.heartbeat("sensory.vision")
        assert next(
            t for t in registry.get_all_tools() if t.name == "sensory.vision"
        ).status is HealthStatus.AVAILABLE


# ── Degradation ───────────────────────────────────────────────────── #


class TestDegradation:
    def test_mark_degraded(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(registry)
        changed = registry.mark_degraded("sensory.hearing", "model missing")
        assert changed
        tool = next(t for t in registry.get_all_tools() if t.name == "sensory.hearing")
        assert tool.status is HealthStatus.DEGRADED
        assert tool.metadata["degraded_reason"] == "model missing"

    def test_mark_degraded_idempotent(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(registry)
        registry.mark_degraded("sensory.hearing", "reason")
        changed = registry.mark_degraded("sensory.hearing", "reason")
        assert not changed

    def test_mark_degraded_nonexistent(self) -> None:
        registry = ToolRegistry()
        assert not registry.mark_degraded("nonexistent", "reason")

    def test_health_check_failure_marks_degraded(self) -> None:
        registry = ToolRegistry()
        register_sensory_tools(
            registry,
            vision_check=lambda: False,
            hearing_check=lambda: True,
            speech_check=lambda: True,
        )
        degraded = registry.run_health_checks()
        assert "sensory.vision" in degraded
        tool = next(t for t in registry.get_all_tools() if t.name == "sensory.vision")
        assert tool.status is HealthStatus.DEGRADED

    def test_health_check_recovery(self) -> None:
        healthy = [False]
        registry = ToolRegistry()
        register_sensory_tools(registry, vision_check=lambda: healthy[0])
        registry.run_health_checks()
        tool = next(t for t in registry.get_all_tools() if t.name == "sensory.vision")
        assert tool.status is HealthStatus.DEGRADED
        healthy[0] = True
        registry.run_health_checks()
        tool = next(t for t in registry.get_all_tools() if t.name == "sensory.vision")
        assert tool.status is HealthStatus.AVAILABLE

    def test_health_check_exception_marks_degraded(self) -> None:
        def bad_check() -> bool:
            msg = "boom"
            raise RuntimeError(msg)

        registry = ToolRegistry()
        register_sensory_tools(registry, vision_check=bad_check)
        degraded = registry.run_health_checks()
        assert "sensory.vision" in degraded


# ── Sensory health event publishing ──────────────────────────────── #


class TestSensoryHealthEvent:
    def test_event_serialization(self) -> None:
        evt = SensoryHealthEvent(modality="vision", status="DEGRADED", reason="test")
        data = json.loads(evt.to_bytes())
        assert data["modality"] == "vision"
        assert data["status"] == "DEGRADED"
        assert data["timestamp"] > 0

    def test_check_publishes_event(self) -> None:
        published: list[tuple[str, bytes]] = []

        def pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        registry = ToolRegistry(publish_fn=pub)
        register_sensory_tools(registry, vision_check=lambda: False)
        degraded = check_sensory_health(registry, publish_fn=pub)
        assert "sensory.vision" in degraded
        sensory_pubs = [
            (t, p) for t, p in published if "sensory_health" in t
        ]
        assert len(sensory_pubs) >= 1
        data = json.loads(sensory_pubs[0][1])
        assert data["modality"] == "vision"
        assert data["status"] == "DEGRADED"


# ── Cortisol response ────────────────────────────────────────────── #


class TestCortisolResponse:
    def test_cortisol_hook_called_on_degradation(self) -> None:
        calls: list[tuple[str, str]] = []

        def hook(name: str, reason: str) -> float:
            calls.append((name, reason))
            return 0.1

        registry = ToolRegistry()
        register_sensory_tools(registry, speech_check=lambda: False)
        check_sensory_health(registry, cortisol_hook=hook)
        assert len(calls) == 1
        assert calls[0][0] == "sensory.speech"

    def test_cortisol_hook_not_called_when_healthy(self) -> None:
        calls: list[tuple[str, str]] = []

        def hook(name: str, reason: str) -> float:
            calls.append((name, reason))
            return 0.1

        registry = ToolRegistry()
        register_sensory_tools(
            registry,
            vision_check=lambda: True,
            hearing_check=lambda: True,
            speech_check=lambda: True,
        )
        check_sensory_health(registry, cortisol_hook=hook)
        assert len(calls) == 0

    def test_cortisol_hooks_integration(self) -> None:
        from unittest.mock import MagicMock

        from openbad.endocrine.hooks.cortisol import CortisolHooks

        controller = MagicMock()
        controller._config.cortisol.increment = 0.1
        hooks = CortisolHooks(controller)
        result = hooks.on_tool_degraded("sensory.vision", "PipeWire disconnected")
        controller.trigger.assert_called_once_with("cortisol", 0.1)
        assert result == controller.trigger.return_value
