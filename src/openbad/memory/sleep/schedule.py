"""Sleep schedule configuration and scheduler.

Provides a ``SleepScheduleConfig`` dataclass and an async
``SleepScheduler`` that triggers FSM sleep/wake transitions
based on a daily time window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.types import CognitiveRequest, CognitiveResponse

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
    start_minute: int = 0
    duration_hours: float = 3.0
    idle_timeout_minutes: int = 15
    allow_daytime_naps: bool = True
    enabled: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.start_hour <= 23:
            raise ValueError(f"start_hour must be 0–23, got {self.start_hour}")
        if not 0 <= self.start_minute <= 59:
            raise ValueError(f"start_minute must be 0–59, got {self.start_minute}")
        if self.duration_hours <= 0 or self.duration_hours > 24:
            raise ValueError(
                f"duration_hours must be in (0, 24], got {self.duration_hours}",
            )
        if self.idle_timeout_minutes <= 0:
            raise ValueError(
                f"idle_timeout_minutes must be > 0, got {self.idle_timeout_minutes}",
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SleepScheduleConfig:
        start_text = str(data.get("sleep_window_start", "")).strip()
        if start_text:
            parts = start_text.split(":", 1)
            if len(parts) != 2:
                raise ValueError("sleep_window_start must be HH:MM")
            start_hour = int(parts[0])
            start_minute = int(parts[1])
        else:
            start_hour = int(data.get("start_hour", 2))
            start_minute = int(data.get("start_minute", 0))

        return cls(
            start_hour=start_hour,
            start_minute=start_minute,
            duration_hours=float(
                data.get("duration_hours", data.get("sleep_window_duration_hours", 3.0))
            ),
            idle_timeout_minutes=int(data.get("idle_timeout_minutes", 15)),
            allow_daytime_naps=bool(data.get("allow_daytime_naps", True)),
            enabled=bool(data.get("enabled", True)),
        )

    @property
    def sleep_window_start(self) -> str:
        """Return the configured window start in HH:MM form."""
        return f"{self.start_hour:02d}:{self.start_minute:02d}"

    @property
    def sleep_window_duration_hours(self) -> float:
        """Alias used by memory.yaml and WUI sleep settings."""
        return self.duration_hours

    @property
    def idle_timeout_seconds(self) -> float:
        """Idle timeout converted to seconds for runtime checks."""
        return float(self.idle_timeout_minutes * 60)

    def is_in_window(self, now: datetime | None = None) -> bool:
        """Check if the given time falls within the sleep window."""
        if not self.enabled:
            return False
        if now is None:
            now = datetime.now(tz=UTC)
        current_hour = now.hour + now.minute / 60.0
        start_hour = self.start_hour + self.start_minute / 60.0
        end_hour = start_hour + self.duration_hours
        if end_hour <= 24:
            return start_hour <= current_hour < end_hour
        # Wraps past midnight: e.g. 23:00 → 02:00
        return current_hour >= start_hour or current_hour < (end_hour - 24)

    def seconds_until_start(self, now: datetime | None = None) -> float:
        """Seconds until the next sleep window start."""
        if now is None:
            now = datetime.now(tz=UTC)
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        start_seconds = self.start_hour * 3600 + self.start_minute * 60
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
        start_seconds = self.start_hour * 3600 + self.start_minute * 60
        end_seconds = start_seconds + (self.duration_hours * 3600)
        if end_seconds > 86400:
            # Wraps past midnight
            if current_seconds >= start_seconds:
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
        get_last_activity: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._fsm = fsm
        self._orchestrator = orchestrator
        self._get_last_activity = get_last_activity
        self._sleeping = False
        self._task: asyncio.Task[None] | None = None
        self._manual_wake = False

    def _is_idle(self) -> bool:
        """Return whether activity has been idle long enough for sleep."""
        if self._get_last_activity is None:
            return True
        return (time.time() - self._get_last_activity()) >= self._config.idle_timeout_seconds

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
                    if not self._is_idle():
                        logger.debug("Sleep window active but user is not idle; deferring")
                        continue
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
