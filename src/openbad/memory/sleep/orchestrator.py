"""Sleep cycle orchestrator — idle detection and phase sequencing.

Detects agent idle state, triggers SWS → REM → pruning in sequence,
and publishes consolidation events to the MQTT nervous system.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbad.memory.forgetting import prune_store

if TYPE_CHECKING:
    from openbad.memory.controller import MemoryController
    from openbad.memory.sleep.rem import RapidEyeMovement
    from openbad.memory.sleep.sws import SlowWaveSleep

logger = logging.getLogger(__name__)


class SleepPhase(enum.Enum):
    """Phases of the sleep consolidation cycle."""

    AWAKE = "awake"
    SWS = "sws"
    REM = "rem"
    PRUNING = "pruning"
    COMPLETE = "complete"


@dataclass
class MemoryPruner:
    """Wrapper around the forgetting module for use in the sleep cycle."""

    memory_controller: MemoryController
    threshold: float = 0.1
    half_life_hours: float = 168.0

    def run(self) -> int:
        """Prune decayed entries from episodic and semantic stores.

        Returns total count of entries pruned.
        """
        now = time.time()
        total = 0
        total += len(
            prune_store(
                self.memory_controller.episodic,
                threshold=self.threshold,
                half_life_hours=self.half_life_hours,
                now=now,
            )
        )
        total += len(
            prune_store(
                self.memory_controller.semantic,
                threshold=self.threshold,
                half_life_hours=self.half_life_hours,
                now=now,
            )
        )
        return total


@dataclass
class SleepReport:
    """Report from a completed sleep cycle."""

    started_at: float = 0.0
    finished_at: float = 0.0
    phase_durations: dict[str, float] = field(default_factory=dict)
    constraints_extracted: int = 0
    skills_created: int = 0
    entries_pruned: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.finished_at - self.started_at,
            "phase_durations": self.phase_durations,
            "constraints_extracted": self.constraints_extracted,
            "skills_created": self.skills_created,
            "entries_pruned": self.entries_pruned,
        }


class SleepOrchestrator:
    """Orchestrates SWS → REM → pruning sleep consolidation cycles."""

    def __init__(
        self,
        sws: SlowWaveSleep,
        rem: RapidEyeMovement,
        pruner: MemoryPruner,
        idle_threshold_seconds: float = 300.0,
        publish_fn: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._sws = sws
        self._rem = rem
        self._pruner = pruner
        self._idle_threshold = idle_threshold_seconds
        self._publish_fn = publish_fn
        self._phase = SleepPhase.AWAKE
        self._task: asyncio.Task[None] | None = None

    @property
    def phase(self) -> SleepPhase:
        return self._phase

    # ------------------------------------------------------------------ #
    # Idle detection
    # ------------------------------------------------------------------ #

    def is_idle(self, last_activity: float) -> bool:
        """Check if agent has been idle long enough to trigger sleep."""
        return (time.time() - last_activity) >= self._idle_threshold

    # ------------------------------------------------------------------ #
    # Run a single sleep cycle
    # ------------------------------------------------------------------ #

    def run_cycle(self) -> SleepReport:
        """Execute SWS → REM → pruning in sequence."""
        report = SleepReport(started_at=time.time())

        # SWS phase
        self._set_phase(SleepPhase.SWS)
        t0 = time.time()
        report.constraints_extracted = self._sws.run()
        report.phase_durations[SleepPhase.SWS.value] = time.time() - t0

        # REM phase
        self._set_phase(SleepPhase.REM)
        t0 = time.time()
        report.skills_created = self._rem.run()
        report.phase_durations[SleepPhase.REM.value] = time.time() - t0

        # Pruning phase
        self._set_phase(SleepPhase.PRUNING)
        t0 = time.time()
        report.entries_pruned = self._pruner.run()
        report.phase_durations[SleepPhase.PRUNING.value] = time.time() - t0

        # Complete
        report.finished_at = time.time()
        self._set_phase(SleepPhase.COMPLETE)

        logger.info(
            "Sleep cycle complete: %d constraints, %d skills, %d pruned",
            report.constraints_extracted,
            report.skills_created,
            report.entries_pruned,
        )

        # Return to awake
        self._set_phase(SleepPhase.AWAKE)
        return report

    # ------------------------------------------------------------------ #
    # Background monitoring
    # ------------------------------------------------------------------ #

    async def start_background(
        self,
        get_last_activity: Callable[[], float],
        check_interval: float = 60.0,
    ) -> None:
        """Start an asyncio background task that checks idle and triggers cycles."""
        if self._task is not None:
            return

        self._task = asyncio.current_task()

        try:
            while True:
                await asyncio.sleep(check_interval)
                last = get_last_activity()
                if self.is_idle(last):
                    logger.info("Agent idle — starting sleep cycle")
                    self.run_cycle()
        except asyncio.CancelledError:
            logger.info("Sleep orchestrator background task cancelled")

    def stop(self) -> None:
        """Cancel the background task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _set_phase(self, phase: SleepPhase) -> None:
        self._phase = phase
        if self._publish_fn is not None:
            self._publish_fn(
                "agent/memory/sleep/phase",
                phase.value.encode(),
            )
