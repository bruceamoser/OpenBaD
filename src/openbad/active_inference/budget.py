"""Exploration budget — daily token bucket with cooldown."""

from __future__ import annotations

import time


class ExplorationBudget:
    """Daily token budget that gates exploration actions.

    Tracks remaining tokens and enforces a cooldown between actions.
    """

    def __init__(
        self,
        daily_limit: int = 5000,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._daily_limit = daily_limit
        self._remaining = daily_limit
        self._cooldown = cooldown_seconds
        self._last_action: float = -cooldown_seconds

    # -- Properties -------------------------------------------------------- #

    @property
    def remaining(self) -> int:
        return self._remaining

    @property
    def daily_limit(self) -> int:
        return self._daily_limit

    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown

    # -- Actions ----------------------------------------------------------- #

    def can_spend(self, cost: int = 1, *, now: float | None = None) -> bool:
        """Check whether *cost* tokens can be spent right now."""
        now = now if now is not None else time.monotonic()
        if self._remaining < cost:
            return False
        return not now - self._last_action < self._cooldown

    def spend(self, cost: int = 1, *, now: float | None = None) -> bool:
        """Deduct *cost* tokens if allowed. Returns ``True`` on success."""
        now = now if now is not None else time.monotonic()
        if not self.can_spend(cost, now=now):
            return False
        self._remaining -= cost
        self._last_action = now
        return True

    def reset(self, daily_limit: int | None = None) -> None:
        """Reset the budget (e.g. at midnight)."""
        if daily_limit is not None:
            self._daily_limit = daily_limit
        self._remaining = self._daily_limit
        self._last_action = -self._cooldown
