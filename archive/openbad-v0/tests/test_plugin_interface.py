"""Tests for ObservationPlugin interface and ObservationResult."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openbad.active_inference.plugin_interface import (
    ObservationPlugin,
    ObservationResult,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


class _GoodPlugin(ObservationPlugin):
    """Concrete plugin for testing."""

    @property
    def source_id(self) -> str:
        return "test_source"

    async def observe(self) -> ObservationResult:
        return ObservationResult(metrics={"cpu": 50.0})

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {"cpu": {"expected": 30.0, "tolerance": 20.0}}


class _CustomPollPlugin(_GoodPlugin):
    @property
    def poll_interval_seconds(self) -> int:
        return 120


# ------------------------------------------------------------------ #
# ObservationResult
# ------------------------------------------------------------------ #


class TestObservationResult:
    def test_default_timestamp(self) -> None:
        r = ObservationResult(metrics={"a": 1})
        assert isinstance(r.timestamp, datetime)
        assert r.timestamp.tzinfo == UTC

    def test_explicit_timestamp(self) -> None:
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        r = ObservationResult(metrics={"a": 1}, timestamp=ts)
        assert r.timestamp == ts

    def test_raw_data_default_none(self) -> None:
        r = ObservationResult(metrics={})
        assert r.raw_data is None

    def test_raw_data_custom(self) -> None:
        r = ObservationResult(metrics={}, raw_data={"detail": True})
        assert r.raw_data == {"detail": True}

    def test_metrics_various_types(self) -> None:
        r = ObservationResult(metrics={"f": 1.5, "i": 42, "s": "ok"})
        assert r.metrics["f"] == 1.5
        assert r.metrics["i"] == 42
        assert r.metrics["s"] == "ok"


# ------------------------------------------------------------------ #
# ObservationPlugin ABC
# ------------------------------------------------------------------ #


class TestObservationPlugin:
    def test_concrete_implementation(self) -> None:
        p = _GoodPlugin()
        assert p.source_id == "test_source"

    async def test_observe_returns_result(self) -> None:
        p = _GoodPlugin()
        r = await p.observe()
        assert isinstance(r, ObservationResult)
        assert r.metrics["cpu"] == 50.0

    def test_default_predictions(self) -> None:
        p = _GoodPlugin()
        preds = p.default_predictions()
        assert "cpu" in preds
        assert preds["cpu"]["expected"] == 30.0

    def test_default_poll_interval(self) -> None:
        p = _GoodPlugin()
        assert p.poll_interval_seconds == 60

    def test_custom_poll_interval(self) -> None:
        p = _CustomPollPlugin()
        assert p.poll_interval_seconds == 120

    def test_abstract_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ObservationPlugin()  # type: ignore[abstract]

    def test_missing_source_id_raises(self) -> None:
        class _Bad(ObservationPlugin):
            async def observe(self) -> ObservationResult:
                return ObservationResult(metrics={})

            def default_predictions(self) -> dict:
                return {}

        with pytest.raises(TypeError):
            _Bad()  # type: ignore[abstract]

    def test_missing_observe_raises(self) -> None:
        class _Bad(ObservationPlugin):
            @property
            def source_id(self) -> str:
                return "x"

            def default_predictions(self) -> dict:
                return {}

        with pytest.raises(TypeError):
            _Bad()  # type: ignore[abstract]
