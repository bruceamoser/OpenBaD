"""World model — prediction store with self-calibrating EMA."""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PredictionEntry:
    """A single metric's prediction state."""

    source_id: str
    metric_name: str
    expected_value: float
    tolerance: float
    prediction_error: float = 0.0
    last_updated: float = field(default_factory=time.monotonic)
    _history: deque[float] = field(
        default_factory=lambda: deque(maxlen=20),
    )

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "metric_name": self.metric_name,
            "expected_value": self.expected_value,
            "tolerance": self.tolerance,
            "prediction_error": self.prediction_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PredictionEntry:
        return cls(
            source_id=d["source_id"],
            metric_name=d["metric_name"],
            expected_value=d["expected_value"],
            tolerance=d["tolerance"],
            prediction_error=d.get("prediction_error", 0.0),
        )


class WorldModel:
    """Tracks expected values per source/metric with EMA self-calibration."""

    def __init__(
        self,
        history_size: int = 20,
        ema_alpha: float = 0.1,
    ) -> None:
        self._predictions: dict[str, PredictionEntry] = {}
        self._history_size = history_size
        self._alpha = ema_alpha

    # -- Key helpers ------------------------------------------------------- #

    @staticmethod
    def _key(source_id: str, metric_name: str) -> str:
        return f"{source_id}:{metric_name}"

    # -- Registration ------------------------------------------------------ #

    def register_source(
        self,
        source_id: str,
        defaults: dict[str, dict[str, float]],
    ) -> None:
        """Seed predictions from a plugin's ``default_predictions()``."""
        for metric_name, vals in defaults.items():
            key = self._key(source_id, metric_name)
            if key not in self._predictions:
                self._predictions[key] = PredictionEntry(
                    source_id=source_id,
                    metric_name=metric_name,
                    expected_value=vals["expected"],
                    tolerance=vals["tolerance"],
                    _history=deque(maxlen=self._history_size),
                )

    # -- Update ------------------------------------------------------------ #

    def update(
        self,
        source_id: str,
        metrics: dict[str, float | int | str],
    ) -> dict[str, float]:
        """Incorporate new observations and return per-metric prediction errors."""
        errors: dict[str, float] = {}
        for metric_name, observed in metrics.items():
            if not isinstance(observed, (int, float)):
                continue
            key = self._key(source_id, metric_name)
            entry = self._predictions.get(key)
            if entry is None:
                # Auto-register with loose tolerance.
                entry = PredictionEntry(
                    source_id=source_id,
                    metric_name=metric_name,
                    expected_value=float(observed),
                    tolerance=abs(float(observed)) * 0.5 + 1.0,
                    _history=deque(maxlen=self._history_size),
                )
                self._predictions[key] = entry

            val = float(observed)
            error = abs(val - entry.expected_value) / max(entry.tolerance, 1e-6)
            entry.prediction_error = min(error, 1.0)
            errors[metric_name] = entry.prediction_error

            # EMA update for expected value.
            entry.expected_value += self._alpha * (val - entry.expected_value)

            # Adjust tolerance from observed variance.
            entry._history.append(val)
            if len(entry._history) >= 2:
                mean = sum(entry._history) / len(entry._history)
                var = sum((x - mean) ** 2 for x in entry._history) / len(
                    entry._history,
                )
                std = math.sqrt(var)
                # Tolerance = 2 × std, with a floor of 1.0.
                entry.tolerance += self._alpha * (max(2.0 * std, 1.0) - entry.tolerance)

            entry.last_updated = time.monotonic()

        return errors

    # -- Queries ----------------------------------------------------------- #

    def get_predictions(self, source_id: str) -> list[PredictionEntry]:
        """Return all predictions for a source."""
        return [e for e in self._predictions.values() if e.source_id == source_id]

    def get_entry(self, source_id: str, metric_name: str) -> PredictionEntry | None:
        return self._predictions.get(self._key(source_id, metric_name))

    def reset_errors(self) -> None:
        """Reset all prediction errors to zero (post-consolidation)."""
        for entry in self._predictions.values():
            entry.prediction_error = 0.0

    # -- Persistence ------------------------------------------------------- #

    def persist(self, path: Path) -> None:
        """Save world model state to a JSON file."""
        data = [e.to_dict() for e in self._predictions.values()]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: Path) -> None:
        """Load world model state from a JSON file."""
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        for d in raw:
            key = self._key(d["source_id"], d["metric_name"])
            self._predictions[key] = PredictionEntry.from_dict(d)
