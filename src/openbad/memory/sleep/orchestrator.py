"""Sleep cycle orchestrator — idle detection and phase sequencing.

Detects agent idle state, triggers SWS → REM → pruning in sequence,
and publishes consolidation events to the MQTT nervous system.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.event_loop import CognitiveEventLoop, CognitiveRequest
from openbad.cognitive.model_router import Priority
from openbad.memory.forgetting import prune_store
from openbad.memory.sleep.prompts import (
    EXTRACT_TAGS,
    SCORE_IMPORTANCE,
    SUMMARIZE_BATCH,
    SUMMARIZE_SINGLE,
)

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

        stm_entries = self._memory_controller.stm.query("")
        episodic_entries = self._memory_controller.episodic.query("")

        try:
            # SWS phase: summarize pending entries.
            self._set_phase(SleepPhase.SWS)
            t0 = time.time()
            stm_summaries = await self._summarize_entries(stm_entries)
            episodic_summaries = await self._summarize_episodic_batches(
                episodic_entries,
            )
            report.phase_durations[SleepPhase.SWS.value] = time.time() - t0

            # REM phase: extract tags, score importance, and write LTM.
            self._set_phase(SleepPhase.REM)
            t0 = time.time()
            stm_count = await self._write_ltm(stm_entries, stm_summaries)
            episodic_count = await self._write_episodic_ltm(
                episodic_entries, episodic_summaries,
            )
            report.entries_consolidated = stm_count + episodic_count
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
        """Summarize individual STM entries using the single-entry template."""
        summaries: dict[str, str] = {}
        for entry in entries:
            prompt = SUMMARIZE_SINGLE.format(entry=str(entry.value))
            response = await self._request_sleep_pass(
                entry=entry,
                stage="summarize",
                prompt=prompt,
                context=str(entry.value),
            )
            summaries[entry.key] = response.answer.strip()
        return summaries

    async def _summarize_episodic_batches(
        self,
        entries: list[MemoryEntry],
    ) -> dict[str, str]:
        """Batch episodic entries by context/topic and summarize each batch."""
        batches = _batch_by_context(entries)
        summaries: dict[str, str] = {}
        for _topic, batch in batches.items():
            if len(batch) == 1:
                prompt = SUMMARIZE_SINGLE.format(entry=str(batch[0].value))
            else:
                entries_text = "\n---\n".join(
                    f"[{e.key}] {e.value}" for e in batch
                )
                prompt = SUMMARIZE_BATCH.format(entries=entries_text)
            # Use the first entry as the representative for the request.
            representative = batch[0]
            context = "\n".join(str(e.value) for e in batch)
            response = await self._request_sleep_pass(
                entry=representative,
                stage="summarize",
                prompt=prompt,
                context=context,
            )
            summary = response.answer.strip()
            for entry in batch:
                summaries[entry.key] = summary
        return summaries

    async def _write_ltm(
        self,
        entries: list[MemoryEntry],
        summaries: dict[str, str],
    ) -> int:
        """Write STM entries to semantic LTM and remove from STM."""
        consolidated = 0
        for entry in entries:
            summary = summaries.get(entry.key, "").strip()
            if not summary:
                continue
            tags, importance = await self._extract_and_score(entry, summary)
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

    async def _write_episodic_ltm(
        self,
        entries: list[MemoryEntry],
        summaries: dict[str, str],
    ) -> int:
        """Compress episodic entries into semantic LTM.

        Originals are kept (not deleted) so they remain recoverable within
        the configurable retention window.
        """
        batches = _batch_by_context(entries)
        consolidated = 0
        written_summaries: set[str] = set()
        for _topic, batch in batches.items():
            representative = batch[0]
            summary = summaries.get(representative.key, "").strip()
            if not summary or summary in written_summaries:
                continue
            tags, importance = await self._extract_and_score(
                representative, summary,
            )
            source_keys = [e.key for e in batch]
            self._memory_controller.write_semantic(
                f"sleep/episodic/{representative.key}",
                summary,
                context="sleep_consolidation",
                metadata={
                    "source_episodic_keys": source_keys,
                    "importance": importance,
                    "tags": tags,
                    "consolidated": True,
                },
            )
            # Mark originals as consolidated but do NOT delete.
            for entry in batch:
                entry.metadata["consolidated"] = True
            written_summaries.add(summary)
            consolidated += len(batch)
        return consolidated

    async def _extract_and_score(
        self,
        entry: MemoryEntry,
        summary: str,
    ) -> tuple[list[str], float]:
        """Run tag extraction and importance scoring for a summary."""
        tags_response = await self._request_sleep_pass(
            entry=entry,
            stage="extract",
            prompt=EXTRACT_TAGS.format(summary=summary),
            context=summary,
        )
        score_response = await self._request_sleep_pass(
            entry=entry,
            stage="score",
            prompt=SCORE_IMPORTANCE.format(summary=summary),
            context=summary,
        )
        return _parse_tags(tags_response.answer), _parse_importance(
            score_response.answer,
        )

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


def _batch_by_context(
    entries: list[MemoryEntry],
) -> dict[str, list[MemoryEntry]]:
    """Group entries by their ``context`` field (or 'general' if blank)."""
    batches: dict[str, list[MemoryEntry]] = defaultdict(list)
    for entry in entries:
        topic = (entry.context or entry.metadata.get("topic", "")).strip()
        if not topic:
            topic = "general"
        batches[topic].append(entry)
    return dict(batches)


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
