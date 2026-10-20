"""Tests for McpGovernor — Phase 10, Issue #419."""

from __future__ import annotations

from unittest.mock import MagicMock

from openbad.interoception.monitor import CpuSnapshot, MemorySnapshot
from openbad.nervous_system import topics
from openbad.tasks.models import TaskPriority
from openbad.toolbelt.mcp_bridge.mcp_governor import (
    DEFAULT_CPU_THRESHOLD_PCT,
    DEFAULT_RAM_THRESHOLD_PCT,
    GovernorDecision,
    McpGovernor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cpu(usage: float = 10.0) -> CpuSnapshot:
    return CpuSnapshot(
        usage_percent=usage,
        system_percent=2.0,
        user_percent=8.0,
        core_count=4,
        load_avg_1m=0.5,
    )


def _mem(usage: float = 50.0) -> MemorySnapshot:
    total = 8 * 1024**3  # 8 GiB
    used = int(total * usage / 100)
    return MemorySnapshot(
        usage_percent=usage,
        used_bytes=used,
        total_bytes=total,
        available_bytes=total - used,
        swap_percent=0.0,
    )


def _governor(
    *,
    ram: float = 50.0,
    cpu: float = 10.0,
    mqtt: MagicMock | None = None,
    ram_threshold: float = DEFAULT_RAM_THRESHOLD_PCT,
    cpu_threshold: float = DEFAULT_CPU_THRESHOLD_PCT,
) -> McpGovernor:
    return McpGovernor(
        mqtt=mqtt,
        ram_threshold_pct=ram_threshold,
        cpu_threshold_pct=cpu_threshold,
        cpu_collector=lambda: _cpu(cpu),
        memory_collector=lambda: _mem(ram),
    )


# ---------------------------------------------------------------------------
# Healthy system
# ---------------------------------------------------------------------------


class TestHealthySystem:
    def test_allows_launch_when_resources_healthy(self) -> None:
        gov = _governor(ram=50.0, cpu=20.0)
        result = gov.check()
        assert result.decision == GovernorDecision.ALLOW

    def test_no_endocrine_event_on_allow(self) -> None:
        mqtt = MagicMock()
        gov = _governor(ram=30.0, cpu=15.0, mqtt=mqtt)
        gov.check()
        mqtt.publish_bytes.assert_not_called()

    def test_allow_result_has_empty_reason(self) -> None:
        gov = _governor(ram=50.0, cpu=30.0)
        result = gov.check()
        assert result.reason == ""


# ---------------------------------------------------------------------------
# RAM saturation
# ---------------------------------------------------------------------------


class TestRamSaturation:
    def test_defers_when_ram_at_threshold(self) -> None:
        gov = _governor(ram=DEFAULT_RAM_THRESHOLD_PCT, cpu=10.0)
        result = gov.check()
        assert result.decision == GovernorDecision.DEFERRED

    def test_defers_when_ram_above_threshold(self) -> None:
        gov = _governor(ram=95.0, cpu=10.0)
        result = gov.check()
        assert result.decision == GovernorDecision.DEFERRED

    def test_deferred_reason_mentions_ram(self) -> None:
        gov = _governor(ram=92.0, cpu=10.0)
        result = gov.check()
        assert "RAM" in result.reason

    def test_non_critical_triggers_cortisol(self) -> None:
        mqtt = MagicMock()
        gov = _governor(ram=92.0, cpu=10.0, mqtt=mqtt)
        gov.check(task_priority=TaskPriority.NORMAL)
        mqtt.publish_bytes.assert_called_once()
        topic_arg = mqtt.publish_bytes.call_args[0][0]
        assert topic_arg == topics.ENDOCRINE_CORTISOL


# ---------------------------------------------------------------------------
# CPU saturation
# ---------------------------------------------------------------------------


class TestCpuSaturation:
    def test_defers_when_cpu_at_threshold(self) -> None:
        gov = _governor(ram=50.0, cpu=DEFAULT_CPU_THRESHOLD_PCT)
        result = gov.check()
        assert result.decision == GovernorDecision.DEFERRED

    def test_deferred_reason_mentions_cpu(self) -> None:
        gov = _governor(ram=50.0, cpu=98.0)
        result = gov.check()
        assert "CPU" in result.reason


# ---------------------------------------------------------------------------
# CRITICAL priority → Adrenaline
# ---------------------------------------------------------------------------


class TestCriticalPriority:
    def test_critical_triggers_adrenaline(self) -> None:
        mqtt = MagicMock()
        gov = _governor(ram=92.0, cpu=10.0, mqtt=mqtt)
        gov.check(task_priority=TaskPriority.CRITICAL)
        mqtt.publish_bytes.assert_called_once()
        topic_arg = mqtt.publish_bytes.call_args[0][0]
        assert topic_arg == topics.ENDOCRINE_ADRENALINE

    def test_adrenaline_not_triggered_on_allow(self) -> None:
        mqtt = MagicMock()
        gov = _governor(ram=50.0, cpu=10.0, mqtt=mqtt)
        gov.check(task_priority=TaskPriority.CRITICAL)
        mqtt.publish_bytes.assert_not_called()


# ---------------------------------------------------------------------------
# Miscellaneous
# ---------------------------------------------------------------------------


class TestGovernorMisc:
    def test_no_mqtt_does_not_raise(self) -> None:
        gov = _governor(ram=95.0, cpu=10.0, mqtt=None)
        result = gov.check()
        assert result.decision == GovernorDecision.DEFERRED

    def test_result_includes_snapshots(self) -> None:
        gov = _governor(ram=60.0, cpu=20.0)
        result = gov.check()
        assert isinstance(result.cpu_snapshot, CpuSnapshot)
        assert isinstance(result.memory_snapshot, MemorySnapshot)

    def test_custom_thresholds_respected(self) -> None:
        gov = _governor(ram=75.0, cpu=10.0, ram_threshold=70.0)
        result = gov.check()
        assert result.decision == GovernorDecision.DEFERRED
