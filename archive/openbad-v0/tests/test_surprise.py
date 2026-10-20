"""Tests for the surprise calculator."""

from __future__ import annotations

import pytest

from openbad.active_inference.surprise import aggregate_surprise, compute_surprise


class TestComputeSurprise:
    def test_zero_surprise(self) -> None:
        assert compute_surprise(50.0, 50.0, 10.0) == 0.0

    def test_half_surprise(self) -> None:
        assert compute_surprise(55.0, 50.0, 10.0) == pytest.approx(0.5)

    def test_full_surprise(self) -> None:
        assert compute_surprise(60.0, 50.0, 10.0) == pytest.approx(1.0)

    def test_capped_at_one(self) -> None:
        assert compute_surprise(100.0, 0.0, 10.0) == 1.0

    def test_zero_tolerance_safe(self) -> None:
        # Should not divide by zero; uses 1e-6 floor.
        result = compute_surprise(1.0, 0.0, 0.0)
        assert result == 1.0

    def test_negative_direction(self) -> None:
        assert compute_surprise(40.0, 50.0, 10.0) == pytest.approx(1.0)


class TestAggregateSurprise:
    def test_empty(self) -> None:
        assert aggregate_surprise({}) == 0.0

    def test_max_value(self) -> None:
        errors = {"a": 0.2, "b": 0.8, "c": 0.5}
        assert aggregate_surprise(errors) == pytest.approx(0.8)

    def test_single(self) -> None:
        assert aggregate_surprise({"x": 0.3}) == pytest.approx(0.3)
