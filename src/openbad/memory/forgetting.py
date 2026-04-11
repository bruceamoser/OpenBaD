"""Ebbinghaus forgetting curve and memory pruning.

Implements exponential decay based on the Ebbinghaus forgetting curve:
    R(t) = e^(-t / S)

where R is retention, t is time since last access, and S is the memory
strength (based on access count and half-life). Memories with low
retention scores are candidates for pruning.
"""

from __future__ import annotations

import logging
import math
import time

from openbad.memory.base import MemoryEntry, MemoryStore

logger = logging.getLogger(__name__)


def retention_score(
    entry: MemoryEntry,
    now: float | None = None,
    half_life_hours: float = 168.0,
) -> float:
    """Calculate Ebbinghaus retention score for an entry.

    Uses exponential decay with access-count reinforcement and an
    optional ``metadata["importance"]`` multiplier:

        strength = half_life_hours * (1 + ln(1 + access_count)) * importance_factor
        R = e^(-elapsed_hours / strength)

    ``importance_factor`` ranges from 0.5 (importance=0) to 1.5
    (importance=1) with a neutral default of 1.0 when unset.

    Returns a value in [0.0, 1.0].
    """
    if now is None:
        now = time.time()

    last_access = entry.accessed_at if entry.accessed_at > 0 else entry.created_at
    elapsed_hours = max(0.0, (now - last_access) / 3600.0)

    # Strength increases with access count (logarithmic reinforcement)
    base_strength = half_life_hours * (1.0 + math.log(1.0 + entry.access_count))

    # Importance multiplier: maps [0.0, 1.0] → [0.5, 1.5], default 1.0
    importance = entry.metadata.get("importance")
    if importance is not None:
        try:
            importance = max(0.0, min(1.0, float(importance)))
        except (ValueError, TypeError):
            importance = 0.5  # neutral default on bad data
        importance_factor = 0.5 + importance
    else:
        importance_factor = 1.0

    strength = base_strength * importance_factor

    return math.exp(-elapsed_hours / strength) if strength > 0 else 0.0


def prune_store(
    store: MemoryStore,
    threshold: float = 0.1,
    half_life_hours: float = 168.0,
    now: float | None = None,
) -> list[str]:
    """Remove entries from a store whose retention is below threshold.

    Returns list of pruned keys.
    """
    if now is None:
        now = time.time()

    # Use query("") to get entries without touching them (read() updates access stats)
    entries = store.query("")
    pruned: list[str] = []

    for entry in entries:
        score = retention_score(entry, now=now, half_life_hours=half_life_hours)
        if score < threshold:
            store.delete(entry.key)
            pruned.append(entry.key)
            logger.debug("Pruned %s (retention=%.4f)", entry.key, score)

    if pruned:
        logger.info("Pruned %d entries below threshold %.2f", len(pruned), threshold)

    return pruned


def rank_by_retention(
    store: MemoryStore,
    half_life_hours: float = 168.0,
    now: float | None = None,
) -> list[tuple[str, float]]:
    """Rank all entries in a store by retention score (ascending).

    Returns list of (key, score) sorted weakest-first.
    """
    if now is None:
        now = time.time()

    scored: list[tuple[str, float]] = []
    # Use query("") to get entries without touching them
    for entry in store.query(""):
        score = retention_score(entry, now=now, half_life_hours=half_life_hours)
        scored.append((entry.key, score))

    scored.sort(key=lambda x: x[1])
    return scored
