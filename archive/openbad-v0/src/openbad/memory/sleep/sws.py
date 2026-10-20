"""Slow Wave Sleep (SWS) phase — negative constraint extraction.

Replays failed execution traces from STM, extracts negative constraints
(what went wrong), and writes restrictive policy entries to episodic LTM.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbad.memory.base import MemoryEntry, MemoryTier

if TYPE_CHECKING:
    from openbad.memory.controller import MemoryController

logger = logging.getLogger(__name__)

# Metadata keys that indicate a failure entry
_FAILURE_TAGS = frozenset({"error", "failure", "exception", "timeout", "rejected"})


@dataclass
class NegativeConstraint:
    """A structured constraint extracted from a failure trace."""

    constraint_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source_entry_id: str = ""
    action: str = ""
    error_type: str = ""
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "source_entry_id": self.source_entry_id,
            "action": self.action,
            "error_type": self.error_type,
            "description": self.description,
            "created_at": self.created_at,
        }


class SlowWaveSleep:
    """SWS phase: scan STM for failures, extract constraints, consolidate."""

    def __init__(
        self,
        memory_controller: MemoryController,
        classify_fn: Callable[[MemoryEntry], list[NegativeConstraint]] | None = None,
    ) -> None:
        self._mc = memory_controller
        self._classify_fn = classify_fn

    # ------------------------------------------------------------------ #
    # Extract failures from STM
    # ------------------------------------------------------------------ #

    def extract_failures(self, context: str | None = None) -> list[MemoryEntry]:
        """Scan STM for entries tagged with error/failure metadata."""
        all_entries = self._mc.stm.query("")
        failures: list[MemoryEntry] = []

        for entry in all_entries:
            if self._is_failure(entry, context):
                failures.append(entry)

        logger.debug("SWS extracted %d failures from STM", len(failures))
        return failures

    # ------------------------------------------------------------------ #
    # Analyze failures → constraints
    # ------------------------------------------------------------------ #

    def analyze(self, failures: list[MemoryEntry]) -> list[NegativeConstraint]:
        """Extract structured constraints from failure entries."""
        if not failures:
            return []

        if self._classify_fn is not None:
            constraints: list[NegativeConstraint] = []
            for entry in failures:
                constraints.extend(self._classify_fn(entry))
            return constraints

        return self._heuristic_analyze(failures)

    # ------------------------------------------------------------------ #
    # Consolidate — write to episodic LTM
    # ------------------------------------------------------------------ #

    def consolidate(self, constraints: list[NegativeConstraint]) -> list[str]:
        """Write constraints to episodic LTM with context='sws_constraint'.

        Returns list of entry IDs written.
        """
        ids: list[str] = []
        for c in constraints:
            entry = MemoryEntry(
                key=f"sws/{c.constraint_id}",
                value=c.description,
                tier=MemoryTier.EPISODIC,
                metadata={
                    "context": "sws_constraint",
                    "action": c.action,
                    "error_type": c.error_type,
                    "source_entry_id": c.source_entry_id,
                    **c.to_dict(),
                },
            )
            eid = self._mc.episodic.write(entry)
            ids.append(eid)
            logger.debug("SWS consolidated constraint %s", c.constraint_id)

        if ids:
            logger.info("SWS wrote %d constraints to episodic LTM", len(ids))
        return ids

    # ------------------------------------------------------------------ #
    # Full pipeline
    # ------------------------------------------------------------------ #

    def run(self, context: str | None = None) -> int:
        """Execute full SWS pipeline: extract → analyze → consolidate.

        Returns count of constraints written.
        """
        failures = self.extract_failures(context)
        constraints = self.analyze(failures)
        ids = self.consolidate(constraints)
        return len(ids)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_failure(entry: MemoryEntry, context: str | None) -> bool:
        """Check if an entry represents a failure."""
        meta = entry.metadata or {}

        # Filter by context if specified
        if context and meta.get("context") != context:
            return False

        # Check metadata keys for failure indicators
        status = str(meta.get("status", "")).lower()
        if status in _FAILURE_TAGS:
            return True

        # Check for 'error' or 'failure' keys in metadata
        for tag in _FAILURE_TAGS:
            if tag in meta:
                return True

        # Check value for failure patterns
        val = str(entry.value).lower()
        return any(tag in val for tag in ("error:", "failed:", "exception:"))

    @staticmethod
    def _heuristic_analyze(
        failures: list[MemoryEntry],
    ) -> list[NegativeConstraint]:
        """Extract constraints using heuristic pattern matching."""
        constraints: list[NegativeConstraint] = []

        for entry in failures:
            meta = entry.metadata or {}
            action = str(meta.get("action", entry.key))
            error_type = str(meta.get("error_type", meta.get("status", "unknown")))
            description = str(entry.value)

            constraints.append(
                NegativeConstraint(
                    source_entry_id=entry.entry_id,
                    action=action,
                    error_type=error_type,
                    description=f"Avoid: {description}",
                )
            )

        return constraints
