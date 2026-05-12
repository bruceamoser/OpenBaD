"""Immune interceptor for observation pipeline.

Scans observations for threats before cognitive processing,
maintains threat-source memory for heightened vigilance,
and triggers endocrine responses (adrenaline on threat, dopamine on novelty).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbad.endocrine.controller import EndocrineController

logger = logging.getLogger(__name__)

# Threat memory decay: after this many seconds without threats, reduce vigilance
_VIGILANCE_DECAY_SECONDS = 3600.0  # 1 hour
# Vigilance multiplier: how much faster to poll flagged sources
_VIGILANCE_POLL_FACTOR = 0.5
# Habituation: after this many boring observations, slow polling
_HABITUATION_THRESHOLD = 10
_HABITUATION_POLL_FACTOR = 2.0


@dataclass
class ThreatMemory:
    """Per-source threat tracking for immune memory."""

    source_id: str
    threat_count: int = 0
    last_threat_ts: float = 0.0
    boring_streak: int = 0

    @property
    def is_vigilant(self) -> bool:
        """Source still under heightened scrutiny."""
        if self.threat_count == 0:
            return False
        elapsed = time.monotonic() - self.last_threat_ts
        return elapsed < _VIGILANCE_DECAY_SECONDS

    @property
    def is_habituated(self) -> bool:
        """Source consistently boring — reduce attention."""
        return self.boring_streak >= _HABITUATION_THRESHOLD


@dataclass
class InterceptResult:
    """Outcome of immune interception."""

    quarantined: bool = False
    reason: str = ""
    source_id: str = ""
    threat_indicators: list[str] = field(default_factory=list)


class ImmuneInterceptor:
    """Intercepts observations between scanner and cognitive processing.

    Responsibilities:
    - Scan observations for threat indicators
    - Quarantine suspicious data (block cognitive processing)
    - Maintain per-source threat memory
    - Signal endocrine responses (adrenaline on threat, dopamine on novelty)
    - Adjust polling recommendations based on source history
    """

    def __init__(self, endocrine: EndocrineController | None = None) -> None:
        self._endocrine = endocrine
        self._threat_memory: dict[str, ThreatMemory] = {}
        # Patterns that indicate threats in observation data
        self._threat_patterns: list[str] = [
            "authentication failure",
            "unauthorized access",
            "permission denied",
            "segfault",
            "out of memory",
            "kernel panic",
            "disk full",
            "connection refused",
            "brute force",
            "injection attempt",
        ]

    def intercept(
        self,
        source_id: str,
        surprise: float,
        errors: dict[str, float],
        raw_data: object | None = None,
    ) -> InterceptResult:
        """Screen an observation for threats before cognitive processing.

        Returns InterceptResult indicating whether to quarantine.
        Also triggers appropriate endocrine signals.
        """
        memory = self._get_or_create_memory(source_id)
        threat_indicators = self._scan_for_threats(source_id, errors, raw_data)

        if threat_indicators:
            # Threat detected → quarantine + adrenaline
            memory.threat_count += 1
            memory.last_threat_ts = time.monotonic()
            memory.boring_streak = 0

            if self._endocrine is not None:
                self._endocrine.trigger("adrenaline")

            logger.warning(
                "Immune quarantine: source=%s threats=%s",
                source_id,
                threat_indicators,
            )
            return InterceptResult(
                quarantined=True,
                reason=f"Threat detected: {', '.join(threat_indicators)}",
                source_id=source_id,
                threat_indicators=threat_indicators,
            )

        # No threat — update habituation tracking
        if surprise > 0.0:
            # Novel observation → dopamine anticipation
            memory.boring_streak = 0
            if self._endocrine is not None and surprise >= 0.3:
                self._endocrine.trigger("dopamine", amount=surprise * 0.2)
        else:
            # Boring observation → habituation
            memory.boring_streak += 1

        return InterceptResult(quarantined=False, source_id=source_id)

    def get_poll_factor(self, source_id: str) -> float:
        """Get polling interval multiplier for a source.

        < 1.0 means poll faster (vigilant), > 1.0 means poll slower (habituated).
        """
        memory = self._threat_memory.get(source_id)
        if memory is None:
            return 1.0
        if memory.is_vigilant:
            return _VIGILANCE_POLL_FACTOR
        if memory.is_habituated:
            return _HABITUATION_POLL_FACTOR
        return 1.0

    def is_quarantined_source(self, source_id: str) -> bool:
        """Check if a source is currently under heightened vigilance."""
        memory = self._threat_memory.get(source_id)
        return memory is not None and memory.is_vigilant

    def _get_or_create_memory(self, source_id: str) -> ThreatMemory:
        if source_id not in self._threat_memory:
            self._threat_memory[source_id] = ThreatMemory(source_id=source_id)
        return self._threat_memory[source_id]

    def _scan_for_threats(
        self,
        source_id: str,
        errors: dict[str, float],
        raw_data: object | None,
    ) -> list[str]:
        """Scan observation data for threat indicators."""
        indicators: list[str] = []

        # Check raw_data for threat patterns
        if raw_data is not None:
            data_str = str(raw_data).lower()
            for pattern in self._threat_patterns:
                if pattern in data_str:
                    indicators.append(pattern)

        # Critical error spikes from a previously-flagged source
        memory = self._threat_memory.get(source_id)
        if memory is not None and memory.is_vigilant:
            # Heightened sensitivity: lower threshold for flagged sources
            for metric, error_val in errors.items():
                if error_val > 0.8:
                    indicators.append(f"high_error:{metric}={error_val:.2f}")

        return indicators
