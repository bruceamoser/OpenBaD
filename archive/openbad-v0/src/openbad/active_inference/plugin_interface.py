"""Observation plugin interface and result dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ObservationResult:
    """Structured observation returned by an :class:`ObservationPlugin`."""

    metrics: dict[str, float | int | str]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_data: Any = None


class ObservationPlugin(ABC):
    """Base class for all data-source observation plugins.

    Subclasses must implement :pymethod:`source_id`, :pymethod:`observe`,
    and :pymethod:`default_predictions`.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this data source (e.g. ``'system_health'``)."""

    @abstractmethod
    async def observe(self) -> ObservationResult:
        """Fetch the current state of this data source."""

    @abstractmethod
    def default_predictions(self) -> dict[str, dict[str, float]]:
        """Return initial predictions for this source before any observations.

        Returns a mapping of metric name → ``{"expected": …, "tolerance": …}``.
        """

    @property
    def poll_interval_seconds(self) -> int:
        """How often to poll this source.  Default: 60 s."""
        return 60
