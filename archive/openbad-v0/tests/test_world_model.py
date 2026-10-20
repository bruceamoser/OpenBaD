"""Tests for the world model prediction store."""

from __future__ import annotations

import pytest

from openbad.active_inference.world_model import PredictionEntry, WorldModel


class TestPredictionEntry:
    def test_to_dict_round_trip(self) -> None:
        e = PredictionEntry(
            source_id="s", metric_name="m",
            expected_value=10.0, tolerance=5.0,
            prediction_error=0.3,
        )
        d = e.to_dict()
        restored = PredictionEntry.from_dict(d)
        assert restored.expected_value == 10.0
        assert restored.tolerance == 5.0
        assert restored.prediction_error == pytest.approx(0.3)


class TestWorldModelRegistration:
    def test_register_source(self) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        entries = wm.get_predictions("sys")
        assert len(entries) == 1
        assert entries[0].metric_name == "cpu"

    def test_register_idempotent(self) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        wm.update("sys", {"cpu": 50.0})
        # Re-register should NOT overwrite updated entry.
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        e = wm.get_entry("sys", "cpu")
        assert e is not None
        assert e.expected_value != 30.0  # EMA shifted it


class TestWorldModelUpdate:
    def test_prediction_error(self) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        errors = wm.update("sys", {"cpu": 50.0})
        # |50 - 30| / 20 = 1.0
        assert errors["cpu"] == pytest.approx(1.0)

    def test_prediction_error_capped_at_1(self) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 5.0}})
        errors = wm.update("sys", {"cpu": 100.0})
        assert errors["cpu"] == 1.0

    def test_ema_convergence(self) -> None:
        wm = WorldModel(ema_alpha=0.3)
        wm.register_source("sys", {"cpu": {"expected": 0.0, "tolerance": 50.0}})
        # Feed constant value — expected should converge toward it.
        for _ in range(30):
            wm.update("sys", {"cpu": 50.0})
        e = wm.get_entry("sys", "cpu")
        assert e is not None
        assert e.expected_value == pytest.approx(50.0, abs=1.0)

    def test_tolerance_adjusts_from_variance(self) -> None:
        wm = WorldModel(ema_alpha=0.3, history_size=10)
        wm.register_source("sys", {"cpu": {"expected": 50.0, "tolerance": 1.0}})
        # Feed highly variable data.
        for v in [10, 90, 20, 80, 30, 70, 40, 60, 50, 50]:
            wm.update("sys", {"cpu": float(v)})
        e = wm.get_entry("sys", "cpu")
        assert e is not None
        assert e.tolerance > 1.0  # Tolerance grew from variance

    def test_auto_register_unknown_metric(self) -> None:
        wm = WorldModel()
        errors = wm.update("sys", {"new_metric": 42.0})
        assert "new_metric" in errors
        e = wm.get_entry("sys", "new_metric")
        assert e is not None

    def test_skips_non_numeric(self) -> None:
        wm = WorldModel()
        errors = wm.update("sys", {"label": "ok"})
        assert errors == {}

    def test_ring_buffer_size(self) -> None:
        wm = WorldModel(history_size=5)
        wm.register_source("sys", {"cpu": {"expected": 0.0, "tolerance": 50.0}})
        for i in range(10):
            wm.update("sys", {"cpu": float(i)})
        e = wm.get_entry("sys", "cpu")
        assert e is not None
        assert len(e._history) == 5


class TestWorldModelResetErrors:
    def test_reset(self) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        wm.update("sys", {"cpu": 80.0})
        wm.reset_errors()
        e = wm.get_entry("sys", "cpu")
        assert e is not None
        assert e.prediction_error == 0.0


class TestWorldModelPersistence:
    def test_persist_and_load(self, tmp_path) -> None:
        wm = WorldModel()
        wm.register_source("sys", {"cpu": {"expected": 30.0, "tolerance": 20.0}})
        wm.update("sys", {"cpu": 45.0})

        path = tmp_path / "world.json"
        wm.persist(path)

        wm2 = WorldModel()
        wm2.load(path)
        e = wm2.get_entry("sys", "cpu")
        assert e is not None
        assert e.expected_value == pytest.approx(31.5, abs=0.1)

    def test_load_nonexistent(self, tmp_path) -> None:
        wm = WorldModel()
        wm.load(tmp_path / "missing.json")
        assert wm.get_predictions("any") == []
