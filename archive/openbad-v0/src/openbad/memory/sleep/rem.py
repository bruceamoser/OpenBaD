"""REM sleep phase — skill abstraction from successful traces.

Analyzes successful task sequences from STM, abstracts them into
reusable procedural skills with Bayesian confidence scoring, and
stores them in the skill library.  Deduplicates against existing
skills by name similarity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from openbad.memory.base import MemoryEntry
from openbad.memory.procedural import Skill

if TYPE_CHECKING:
    from openbad.memory.controller import MemoryController

logger = logging.getLogger(__name__)

_SUCCESS_TAGS = frozenset({"success", "completed", "ok", "done"})


class RapidEyeMovement:
    """REM phase: abstract successful STM traces into procedural skills."""

    def __init__(
        self,
        memory_controller: MemoryController,
        abstract_fn: Callable[[list[MemoryEntry]], Skill | None] | None = None,
    ) -> None:
        self._mc = memory_controller
        self._abstract_fn = abstract_fn

    # ------------------------------------------------------------------ #
    # Extract successes from STM
    # ------------------------------------------------------------------ #

    def extract_successes(
        self, context: str | None = None,
    ) -> list[MemoryEntry]:
        """Scan STM for entries tagged with success metadata."""
        all_entries = self._mc.stm.query("")
        successes: list[MemoryEntry] = []

        for entry in all_entries:
            if self._is_success(entry, context):
                successes.append(entry)

        logger.debug("REM extracted %d successes from STM", len(successes))
        return successes

    # ------------------------------------------------------------------ #
    # Abstract to skill
    # ------------------------------------------------------------------ #

    def abstract_to_skill(
        self, entries: list[MemoryEntry],
    ) -> Skill | None:
        """Abstract related success entries into a reusable Skill.

        Uses optional LLM abstract_fn if provided, otherwise heuristic.
        """
        if not entries:
            return None

        if self._abstract_fn is not None:
            return self._abstract_fn(entries)

        return self._heuristic_abstract(entries)

    # ------------------------------------------------------------------ #
    # Consolidate — write/update procedural LTM
    # ------------------------------------------------------------------ #

    def consolidate(self, skills: list[Skill]) -> list[str]:
        """Write new skills or update existing ones in procedural LTM.

        If a skill with a matching name already exists, update its
        confidence rather than creating a duplicate.  Returns list of
        keys written/updated.
        """
        keys: list[str] = []
        for skill in skills:
            existing_key = self._find_existing(skill.name)
            if existing_key is not None:
                # Update confidence on existing skill
                self._mc.procedural.record_outcome(existing_key, success=True)
                keys.append(existing_key)
                logger.debug("REM updated existing skill %s", existing_key)
            else:
                key = f"rem/{skill.name}"
                self._mc.write_procedural(key, skill)
                keys.append(key)
                logger.debug("REM created new skill %s", key)

        if keys:
            logger.info("REM consolidated %d skills", len(keys))
        return keys

    # ------------------------------------------------------------------ #
    # Full pipeline
    # ------------------------------------------------------------------ #

    def run(self, context: str | None = None) -> int:
        """Execute full REM pipeline: extract → abstract → consolidate.

        Groups successes by action/task context and abstracts each group
        into a skill.  Returns count of skills created/updated.
        """
        successes = self.extract_successes(context)
        if not successes:
            return 0

        groups = self._group_by_action(successes)
        skills: list[Skill] = []
        for _action, entries in groups.items():
            skill = self.abstract_to_skill(entries)
            if skill is not None:
                skills.append(skill)

        keys = self.consolidate(skills)
        return len(keys)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_success(entry: MemoryEntry, context: str | None) -> bool:
        """Check if an entry represents a successful trace."""
        meta = entry.metadata or {}

        # Context filter
        if context and meta.get("context") != context:
            return False

        status = str(meta.get("status", "")).lower()
        if status in _SUCCESS_TAGS:
            return True

        return any(tag in meta for tag in _SUCCESS_TAGS)

    def _find_existing(self, name: str) -> str | None:
        """Find an existing skill key that matches the given name."""
        name_lower = name.lower()
        for key in self._mc.procedural.list_keys():
            skill = self._mc.procedural.get_skill(key)
            if skill is not None and skill.name.lower() == name_lower:
                return key
        return None

    @staticmethod
    def _group_by_action(
        entries: list[MemoryEntry],
    ) -> dict[str, list[MemoryEntry]]:
        """Group success entries by their action metadata."""
        groups: dict[str, list[MemoryEntry]] = {}
        for entry in entries:
            meta = entry.metadata or {}
            action = str(meta.get("action", entry.key))
            groups.setdefault(action, []).append(entry)
        return groups

    @staticmethod
    def _heuristic_abstract(entries: list[MemoryEntry]) -> Skill:
        """Create a Skill from success entries using heuristic extraction."""
        # Use the first entry's action as the skill name
        meta = entries[0].metadata or {}
        action = str(meta.get("action", entries[0].key))
        descriptions = [str(e.value) for e in entries]
        summary = "; ".join(descriptions[:5])

        # Collect capabilities from entry metadata
        capabilities: list[str] = []
        for entry in entries:
            m = entry.metadata or {}
            if "capability" in m:
                cap = m["capability"]
                if cap not in capabilities:
                    capabilities.append(cap)

        return Skill(
            name=action,
            description=f"Abstracted from {len(entries)} successes: {summary}",
            capabilities=capabilities if capabilities else [action],
            confidence=0.5 + min(len(entries) * 0.05, 0.4),
            success_count=len(entries),
        )
