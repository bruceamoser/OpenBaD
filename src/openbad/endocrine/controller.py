"""Central endocrine controller — continuous hormone levels with decay."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from openbad.endocrine.config import EndocrineConfig, HormoneConfig

HORMONES = ("dopamine", "adrenaline", "cortisol", "endorphin")


@dataclass
class HormoneState:
    """Snapshot of all hormone levels."""

    dopamine: float = 0.0
    adrenaline: float = 0.0
    cortisol: float = 0.0
    endorphin: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, float]:
        return {h: getattr(self, h) for h in HORMONES}


class EndocrineController:
    """Manages continuous hormone levels with additive triggers and exponential decay."""

    def __init__(self, config: EndocrineConfig | None = None) -> None:
        self._config = config or EndocrineConfig()
        self._levels: dict[str, float] = {h: 0.0 for h in HORMONES}
        self._last_decay: float = time.monotonic()
        self._last_publish: float = time.monotonic()
        self._previous_state: dict[str, float] = {h: 0.0 for h in HORMONES}

    # -- Queries ----------------------------------------------------------- #

    def get_state(self) -> HormoneState:
        """Return the current hormone levels."""
        return HormoneState(**self._levels)

    def level(self, hormone: str) -> float:
        """Return current level for a single hormone."""
        return self._levels[hormone]

    def is_active(self, hormone: str) -> bool:
        """``True`` when *hormone* exceeds its activation threshold."""
        cfg = self._hormone_config(hormone)
        return self._levels[hormone] > cfg.activation_threshold

    def is_escalated(self, hormone: str) -> bool:
        """``True`` when *hormone* exceeds its escalation threshold (if any)."""
        cfg = self._hormone_config(hormone)
        if cfg.escalation_threshold is None:
            return False
        return self._levels[hormone] > cfg.escalation_threshold

    # -- Mutations --------------------------------------------------------- #

    def trigger(self, hormone: str, amount: float | None = None) -> float:
        """Add *amount* (default: configured increment) to *hormone*, clamped to [0, 1]."""
        cfg = self._hormone_config(hormone)
        delta = amount if amount is not None else cfg.increment
        self._levels[hormone] = max(0.0, min(1.0, self._levels[hormone] + delta))
        return self._levels[hormone]

    def decay(self, dt: float | None = None) -> None:
        """Apply exponential decay to all hormones.

        If *dt* is ``None``, use the wall-clock delta since the last decay.
        """
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_decay
        self._last_decay = now

        if dt <= 0:
            return

        for hormone in HORMONES:
            cfg = self._hormone_config(hormone)
            hl = cfg.half_life_seconds
            self._levels[hormone] *= math.pow(2, -dt / hl)
            # Snap to zero when negligible.
            if self._levels[hormone] < 1e-4:
                self._levels[hormone] = 0.0

    def reset(self) -> None:
        """Reset all hormone levels to zero."""
        for h in HORMONES:
            self._levels[h] = 0.0
        self._last_decay = time.monotonic()

    # -- Publishing helpers ------------------------------------------------ #

    def should_publish(self) -> bool:
        """``True`` when it's time for a periodic or significant-change publish."""
        now = time.monotonic()
        if now - self._last_publish >= self._config.publish_interval_seconds:
            return True
        delta = self._config.significant_change_delta
        return any(
            abs(self._levels[h] - self._previous_state.get(h, 0.0)) >= delta
            for h in HORMONES
        )

    def mark_published(self) -> None:
        """Record that a publish just happened."""
        self._last_publish = time.monotonic()
        self._previous_state = dict(self._levels)

    # -- Internal ---------------------------------------------------------- #

    def _hormone_config(self, hormone: str) -> HormoneConfig:
        cfg = getattr(self._config, hormone, None)
        if cfg is None:
            raise ValueError(f"Unknown hormone: {hormone!r}")
        return cfg
