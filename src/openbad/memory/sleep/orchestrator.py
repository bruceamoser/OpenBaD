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

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.event_loop import CognitiveEventLoop, CognitiveRequest
from openbad.cognitive.model_router import Priority
from openbad.memory.forgetting import prune_store

if TYPE_CHECKING:
    from openbad.memory.base import MemoryEntry
    from openbad.memory.controller import MemoryController

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
    entries_consolidated: int = 0
    entries_pruned: int = 0
    skipped: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.finished_at - self.started_at,
            "phase_durations": self.phase_durations,
            "entries_consolidated": self.entries_consolidated,
            "entries_pruned": self.entries_pruned,
            "skipped": self.skipped,
            "error": self.error,
        }


class SleepOrchestrator:
    """Orchestrates sleep consolidation through the cognitive event loop."""

    def __init__(
        self,
        memory_controller: MemoryController,
        cognitive_event_loop: CognitiveEventLoop,
        pruner: MemoryPruner | None = None,
        fsm: Any = None,
        idle_threshold_seconds: float = 300.0,
        publish_fn: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._memory_controller = memory_controller
        self._event_loop = cognitive_event_loop
        self._pruner = pruner
        self._fsm = fsm
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

    async def run_cycle(self) -> SleepReport:
        """Execute summarization → extraction/scoring → pruning in sequence."""
        report = SleepReport(started_at=time.time())
        self._enter_sleep_state()

        entries = self._memory_controller.stm.query("")

        try:
            # SWS phase: summarize pending STM entries.
            self._set_phase(SleepPhase.SWS)
            t0 = time.time()
            summaries = await self._summarize_entries(entries)
            report.phase_durations[SleepPhase.SWS.value] = time.time() - t0

            # REM phase: extract tags, score importance, and write LTM.
            self._set_phase(SleepPhase.REM)
            t0 = time.time()
            report.entries_consolidated = await self._write_ltm(entries, summaries)
            report.phase_durations[SleepPhase.REM.value] = time.time() - t0

            # Pruning phase.
            self._set_phase(SleepPhase.PRUNING)
            t0 = time.time()
            report.entries_pruned = self._pruner.run() if self._pruner else 0
            report.phase_durations[SleepPhase.PRUNING.value] = time.time() - t0

            report.finished_at = time.time()
            self._set_phase(SleepPhase.COMPLETE)

            logger.info(
                "Sleep cycle complete: %d consolidated, %d pruned",
                report.entries_consolidated,
                report.entries_pruned,
            )
        except Exception as exc:
            report.finished_at = time.time()
            report.skipped = True
            report.error = str(exc)
            logger.warning("Sleep cycle skipped: %s", exc)
        finally:
            self._set_phase(SleepPhase.AWAKE)
            self._wake_sleep_state()

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
                    await self.run_cycle()
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

    def _enter_sleep_state(self) -> None:
        if self._fsm is not None and getattr(self._fsm, "state", None) != "SLEEP":
            self._fsm.fire("sleep")

    def _wake_sleep_state(self) -> None:
        if self._fsm is not None and getattr(self._fsm, "state", None) == "SLEEP":
            self._fsm.fire("wake")

    async def _summarize_entries(
        self,
        entries: list[MemoryEntry],
    ) -> dict[str, str]:
        summaries: dict[str, str] = {}
        for entry in entries:
            response = await self._request_sleep_pass(
                entry=entry,
                stage="summarize",
                prompt=(
                    "Summarize this memory entry for long-term recall in 1-2 "
                    "sentences."
                ),
                context=str(entry.value),
            )
            summaries[entry.key] = response.answer.strip()
        return summaries

    async def _write_ltm(
        self,
        entries: list[MemoryEntry],
        summaries: dict[str, str],
    ) -> int:
        consolidated = 0
        for entry in entries:
            summary = summaries.get(entry.key, "").strip()
            if not summary:
                continue
            tags_response = await self._request_sleep_pass(
                entry=entry,
                stage="extract",
                prompt=(
                    "Extract up to 5 short retrieval tags for this summary. Return "
                    "a comma-separated list only."
                ),
                context=summary,
            )
            score_response = await self._request_sleep_pass(
                entry=entry,
                stage="score",
                prompt=(
                    "Score the long-term importance of this summary from 0.0 to 1.0. "
                    "Return only the numeric score."
                ),
                context=summary,
            )
            tags = _parse_tags(tags_response.answer)
            importance = _parse_importance(score_response.answer)
            self._memory_controller.write_semantic(
                f"sleep/{entry.key}",
                summary,
                context="sleep_consolidation",
                metadata={
                    "source_stm_key": entry.key,
                    "source_entry_id": entry.entry_id,
                    "importance": importance,
                    "tags": tags,
                },
            )
            self._memory_controller.stm.delete(entry.key)
            consolidated += 1
        return consolidated

    async def _request_sleep_pass(
        self,
        *,
        entry: MemoryEntry,
        stage: str,
        prompt: str,
        context: str,
    ) -> Any:
        response = await self._event_loop.handle_request(
            CognitiveRequest(
                request_id=f"sleep-{stage}-{entry.entry_id}",
                prompt=prompt,
                context=context,
                system=CognitiveSystem.SLEEP,
                priority=Priority.LOW,
            )
        )
        if response.error:
            raise RuntimeError(response.error)
        return response


def _parse_tags(raw: str) -> list[str]:
    tags: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        tag = chunk.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:5]


def _parse_importance(raw: str) -> float:
    try:
        score = float(raw.strip())
    except ValueError:
        return 0.5
    return max(0.0, min(1.0, score))
