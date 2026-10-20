"""Deterministic STM → LTM consolidation (no LLM required).

Performs the mechanical data-movement phase of sleep:
1. Copy recent conversation turns from STM to episodic LTM.
2. Extract key facts (simple heuristics) to semantic memory.
3. Prune STM entries older than a configurable threshold.
4. Update memory indices.

This runs *before* the optional CrewAI Maintenance Crew so
the intelligent consolidation has a clean workspace.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbad.memory.base import MemoryEntry

if TYPE_CHECKING:
    from openbad.memory.controller import MemoryController

logger = logging.getLogger(__name__)

# Metadata keys that mark a conversation turn
_TURN_CONTEXTS = frozenset({
    "conversation", "chat", "user_turn", "assistant_turn",
})

# Keys used to detect extractable facts
_FACT_TAGS = frozenset({
    "definition", "fact", "preference", "rule", "instruction",
})


@dataclass
class ConsolidationReport:
    """Summary of what the deterministic consolidation did."""

    turns_promoted: int = 0
    facts_extracted: int = 0
    stm_entries_pruned: int = 0
    indices_updated: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns_promoted": self.turns_promoted,
            "facts_extracted": self.facts_extracted,
            "stm_entries_pruned": self.stm_entries_pruned,
            "indices_updated": self.indices_updated,
            "errors": self.errors,
        }


def consolidate_stm_to_ltm(
    memory_controller: MemoryController,
    *,
    stm_age_threshold: float = 1800.0,
    publish_fn: Any | None = None,
) -> ConsolidationReport:
    """Run deterministic STM → LTM consolidation.

    Parameters
    ----------
    memory_controller:
        The unified memory controller with stm, episodic, semantic stores.
    stm_age_threshold:
        Entries older than this (seconds) are candidates for promotion/pruning.
    publish_fn:
        Optional ``(topic, payload)`` callback for MQTT events.
    """
    report = ConsolidationReport()
    now = time.time()
    all_stm = memory_controller.stm.query("")

    if not all_stm:
        logger.debug("No STM entries to consolidate")
        return report

    for entry in all_stm:
        age = now - entry.created_at
        if age < stm_age_threshold:
            continue

        try:
            if _is_conversation_turn(entry):
                _promote_to_episodic(memory_controller, entry)
                report.turns_promoted += 1
            elif _has_extractable_facts(entry):
                _extract_to_semantic(memory_controller, entry)
                report.facts_extracted += 1
            else:
                # Old entry with no special classification — prune
                memory_controller.stm.delete(entry.key)
                report.stm_entries_pruned += 1
        except Exception as exc:
            report.errors.append(f"{entry.key}: {exc}")
            logger.warning("Consolidation error for %s: %s", entry.key, exc)

    # Update indices (touch semantic index file to trigger re-index)
    report.indices_updated = _update_indices(memory_controller)

    if publish_fn is not None:
        import json

        publish_fn(
            "agent/memory/sleep/consolidation",
            json.dumps(report.to_dict()).encode(),
        )

    logger.info(
        "Deterministic consolidation: %d turns promoted, "
        "%d facts extracted, %d pruned",
        report.turns_promoted,
        report.facts_extracted,
        report.stm_entries_pruned,
    )
    return report


def _is_conversation_turn(entry: MemoryEntry) -> bool:
    """Check if the entry represents a conversation turn."""
    ctx = (entry.context or "").lower()
    if ctx in _TURN_CONTEXTS:
        return True
    meta = entry.metadata or {}
    return meta.get("type") in ("user_turn", "assistant_turn")


def _has_extractable_facts(entry: MemoryEntry) -> bool:
    """Check if the entry contains tagged facts worth promoting."""
    meta = entry.metadata or {}
    tags = set(meta.get("tags", []))
    if tags & _FACT_TAGS:
        return True
    ctx = (entry.context or "").lower()
    return ctx in _FACT_TAGS


def _promote_to_episodic(
    mc: MemoryController, entry: MemoryEntry,
) -> None:
    """Copy a conversation turn from STM to episodic LTM, then delete."""
    mc.write_episodic(
        entry.key,
        entry.value,
        context=entry.context or "conversation",
        metadata={
            **(entry.metadata or {}),
            "promoted_from": "stm",
            "consolidation": "deterministic",
        },
    )
    mc.stm.delete(entry.key)


def _extract_to_semantic(
    mc: MemoryController, entry: MemoryEntry,
) -> None:
    """Extract a fact entry from STM into semantic LTM, then delete."""
    mc.write_semantic(
        f"fact/{entry.key}",
        entry.value,
        context="sleep_fact_extraction",
        metadata={
            **(entry.metadata or {}),
            "promoted_from": "stm",
            "consolidation": "deterministic",
        },
    )
    mc.stm.delete(entry.key)


def _update_indices(mc: MemoryController) -> int:
    """Touch memory indices to ensure they are current.

    Returns count of indices updated.
    """
    updated = 0

    # Semantic store has an internal index that rebuilds on query
    # Force a trivial query to refresh
    try:
        mc.semantic.query("")
        updated += 1
    except Exception:
        logger.debug("Semantic index refresh failed", exc_info=True)

    # Episodic store is always consistent (append-only)
    try:
        mc.episodic.query("")
        updated += 1
    except Exception:
        logger.debug("Episodic index refresh failed", exc_info=True)

    return updated
