"""Sleep schedule configuration and scheduler.

Provides a ``SleepScheduleConfig`` dataclass and an async
``SleepScheduler`` that triggers FSM sleep/wake transitions
based on a daily time window.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.event_loop import CognitiveRequest, CognitiveResponse

logger = logging.getLogger(__name__)

# Systems that are blocked during sleep (only SLEEP is allowed).
_BLOCKED_SYSTEMS = frozenset({
    CognitiveSystem.CHAT,
    CognitiveSystem.REASONING,
    CognitiveSystem.REACTIONS,
})


@dataclass
class SleepScheduleConfig:
    """User-configurable sleep schedule."""

    start_hour: int = 2
    duration_hours: float = 3.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.start_hour <= 23:
            raise ValueError(f"start_hour must be 0–23, got {self.start_hour}")
        if self.duration_hours <= 0 or self.duration_hours > 24:
            raise ValueError(
                f"duration_hours must be in (0, 24], got {self.duration_hours}",
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SleepScheduleConfig:
        return cls(
            start_hour=int(data.get("start_hour", 2)),
            duration_hours=float(data.get("duration_hours", 3.0)),
            enabled=bool(data.get("enabled", True)),
        )

    def is_in_window(self, now: datetime | None = None) -> bool:
        """Check if the given time falls within the sleep window."""
        if not self.enabled:
            return False
        if now is None:
            now = datetime.now(tz=UTC)
        current_hour = now.hour + now.minute / 60.0
        end_hour = self.start_hour + self.duration_hours
        if end_hour <= 24:
            return self.start_hour <= current_hour < end_hour
        # Wraps past midnight: e.g. 23:00 → 02:00
        return current_hour >= self.start_hour or current_hour < (end_hour - 24)

    def seconds_until_start(self, now: datetime | None = None) -> float:
        """Seconds until the next sleep window start."""
        if now is None:
            now = datetime.now(tz=UTC)
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        start_seconds = self.start_hour * 3600
        diff = start_seconds - current_seconds
        if diff <= 0:
            diff += 86400
        return float(diff)

    def window_remaining(self, now: datetime | None = None) -> float:
        """Seconds remaining in the current sleep window, or 0 if not in window."""
        if not self.is_in_window(now):
            return 0.0
        if now is None:
            now = datetime.now(tz=UTC)
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        end_seconds = (self.start_hour * 3600) + (self.duration_hours * 3600)
        if end_seconds > 86400:
            # Wraps past midnight
            if current_seconds >= self.start_hour * 3600:
                remaining = end_seconds - current_seconds
            else:
                remaining = (end_seconds - 86400) - current_seconds
        else:
            remaining = end_seconds - current_seconds
        return max(0.0, remaining)


class SleepScheduler:
    """Async scheduler that triggers FSM sleep/wake transitions on schedule.

    Parameters
    ----------
    config:
        The sleep schedule configuration.
    fsm:
        The agent FSM with ``fire("sleep")`` and ``fire("wake")`` methods.
    orchestrator:
        Optional SleepOrchestrator to run a consolidation cycle during sleep.
    """

    def __init__(
        self,
        config: SleepScheduleConfig,
        fsm: Any,
        orchestrator: Any = None,
    ) -> None:
        self._config = config
        self._fsm = fsm
        self._orchestrator = orchestrator
        self._sleeping = False
        self._task: asyncio.Task[None] | None = None
        self._manual_wake = False

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    def manual_wake(self) -> bool:
        """Emergency wake override — breaks sleep immediately.

        Returns True if the agent was sleeping and is now waking.
        """
        if not self._sleeping:
            return False
        self._manual_wake = True
        self._sleeping = False
        self._fsm.fire("wake")
        logger.info("Manual wake override triggered")
        return True

    async def start(self, check_interval: float = 60.0) -> None:
        """Run the scheduler loop."""
        if not self._config.enabled:
            logger.info("Sleep schedule disabled")
            return

        try:
            while True:
                await asyncio.sleep(check_interval)
                now = datetime.now(tz=UTC)

                if self._manual_wake:
                    self._manual_wake = False
                    continue

                if self._config.is_in_window(now) and not self._sleeping:
                    self._enter_sleep()
                elif not self._config.is_in_window(now) and self._sleeping:
                    self._exit_sleep()
        except asyncio.CancelledError:
            if self._sleeping:
                self._exit_sleep()
            logger.info("Sleep scheduler cancelled")

    def _enter_sleep(self) -> None:
        self._sleeping = True
        self._fsm.fire("sleep")
        logger.info("Scheduled sleep started (hour=%d)", self._config.start_hour)
        if self._orchestrator is not None:
            asyncio.ensure_future(self._orchestrator.run_cycle())

    def _exit_sleep(self) -> None:
        self._sleeping = False
        self._fsm.fire("wake")
        logger.info("Scheduled sleep ended")


def sleep_gate(
    request: CognitiveRequest,
    scheduler: SleepScheduler,
) -> CognitiveResponse | None:
    """Reject non-SLEEP cognitive requests while the agent is sleeping.

    Returns a ``CognitiveResponse`` with an error if the request should be
    blocked, or ``None`` to allow it through.
    """
    if not scheduler.sleeping:
        return None
    if request.system not in _BLOCKED_SYSTEMS:
        return None
    return CognitiveResponse(
        request_id=request.request_id,
        answer="",
        error="Agent is in scheduled sleep — request rejected",
    )
