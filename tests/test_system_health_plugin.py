"""Tests for the system health observation plugin."""

from __future__ import annotations

from unittest.mock import patch

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult
from openbad.plugins.observations.system_health import SystemHealthPlugin


class TestSystemHealthPlugin:
    def test_implements_abc(self) -> None:
        p = SystemHealthPlugin()
        assert isinstance(p, ObservationPlugin)

    def test_source_id(self) -> None:
        assert SystemHealthPlugin().source_id == "system_health"

    def test_poll_interval(self) -> None:
        assert SystemHealthPlugin().poll_interval_seconds == 30

    async def test_observe_returns_result(self) -> None:
        p = SystemHealthPlugin()
        with (
            patch(
                "openbad.plugins.observations.system_health._cpu_percent",
                return_value=42.0,
            ),
            patch(
                "openbad.plugins.observations.system_health._memory_percent",
                return_value=65.0,
            ),
            patch(
                "openbad.plugins.observations.system_health._disk_io_latency_ms",
                return_value=3.0,
            ),
            patch(
                "openbad.plugins.observations.system_health._process_count",
                return_value=200,
            ),
            patch(
                "openbad.plugins.observations.system_health._uptime_hours",
                return_value=1.5,
            ),
        ):
            r = await p.observe()

        assert isinstance(r, ObservationResult)
        assert r.metrics["cpu_percent"] == 42.0
        assert r.metrics["memory_percent"] == 65.0
        assert r.metrics["disk_io_latency_ms"] == 3.0
        assert r.metrics["process_count"] == 200
        assert r.metrics["uptime_hours"] == 1.5

    def test_default_predictions_keys(self) -> None:
        preds = SystemHealthPlugin().default_predictions()
        expected_keys = {
            "cpu_percent",
            "memory_percent",
            "disk_io_latency_ms",
            "process_count",
            "uptime_hours",
        }
        assert set(preds.keys()) == expected_keys

    def test_default_predictions_structure(self) -> None:
        preds = SystemHealthPlugin().default_predictions()
        for _metric, vals in preds.items():
            assert "expected" in vals
            assert "tolerance" in vals
            assert vals["tolerance"] > 0

    async def test_observe_handles_collector_failure(self) -> None:
        """_cpu_percent catches exceptions from the underlying collector."""
        with patch(
            "openbad.interoception.monitor.collect_cpu",
            side_effect=RuntimeError("boom"),
        ):
            from openbad.plugins.observations.system_health import _cpu_percent

            assert _cpu_percent() == 0.0
