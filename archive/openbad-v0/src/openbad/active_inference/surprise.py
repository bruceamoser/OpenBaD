"""Surprise calculator — normalized prediction error."""

from __future__ import annotations


def compute_surprise(
    observed: float,
    expected: float,
    tolerance: float,
) -> float:
    """Return normalised surprise in ``[0.0, 1.0]``.

    ``surprise = min(|observed - expected| / max(tolerance, 1e-6), 1.0)``
    """
    return min(abs(observed - expected) / max(tolerance, 1e-6), 1.0)


def aggregate_surprise(errors: dict[str, float]) -> float:
    """Return the maximum surprise across all metrics."""
    if not errors:
        return 0.0
    return max(errors.values())
