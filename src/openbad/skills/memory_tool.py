"""Memory tool adapter — query and store to LTM tiers.

Registers under ``ToolRole.MEMORY`` and wraps the existing memory
subsystem to provide cognitive-callable actions: recall, store, forget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openbad.memory.base import MemoryTier
from openbad.memory.controller import MemoryController

logger = logging.getLogger(__name__)


@dataclass
class RecallResult:
    """A single recall result with relevance score."""

    key: str
    value: str
    tier: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


class MemoryToolAdapter:
    """Cognitive-callable memory tool wrapping :class:`MemoryController`.

    Parameters
    ----------
    controller:
        The memory controller managing all tiers.
    """

    def __init__(self, controller: MemoryController) -> None:
        self._ctrl = controller

    def recall(self, query: str, top_k: int = 5) -> list[RecallResult]:
        """Search across episodic + semantic stores.

        Delegates to ``MemoryController.recall()`` which provides library
        ref annotations.  Returns ranked results by relevance.
        """
        raw = self._ctrl.recall(query, top_k=top_k)
        results: list[RecallResult] = []
        for item in raw:
            value = item["value"]
            annotations = item.get("library_annotations")
            if annotations:
                value = value + "\n" + "\n".join(annotations)
            results.append(
                RecallResult(
                    key=item["key"],
                    value=value,
                    tier=item["tier"],
                    score=item["score"],
                    metadata=item["metadata"],
                )
            )
        return results

    def store(
        self,
        content: str,
        tier: str = "episodic",
        key: str | None = None,
        metadata: dict | None = None,
    ) -> str | None:
        """Write content to the specified memory tier.

        Parameters
        ----------
        content:
            The content to store.
        tier:
            One of ``"episodic"``, ``"semantic"``, ``"stm"``.
        key:
            Optional key. Auto-generated if not provided.
        metadata:
            Optional metadata dict.

        Returns the entry ID, or ``None`` on failure.
        """
        import uuid

        resolved_key = key or f"tool-{uuid.uuid4().hex[:8]}"
        try:
            if tier == "episodic":
                return self._ctrl.write_episodic(
                    resolved_key, content, metadata=metadata or {},
                )
            if tier == "semantic":
                return self._ctrl.write_semantic(
                    resolved_key, content, metadata=metadata or {},
                )
            if tier == "stm":
                return self._ctrl.write_stm(
                    resolved_key, content, metadata=metadata or {},
                )
            logger.warning("Unknown tier %r, defaulting to episodic", tier)
            return self._ctrl.write_episodic(
                resolved_key, content, metadata=metadata or {},
            )
        except Exception:
            logger.exception("Memory store failed for tier %s", tier)
            return None

    def forget(self, key: str) -> bool:
        """Mark an entry for forgetting during next sleep consolidation.

        Sets ``metadata["forget_requested"] = True`` on the entry if found.
        Returns ``True`` if the entry was found and marked.
        """
        entry = self._ctrl.read(key)
        if entry is None:
            return False

        entry.metadata["forget_requested"] = True

        # Re-write with updated metadata to persist the flag
        try:
            if entry.tier is MemoryTier.EPISODIC and self._ctrl.episodic:
                self._ctrl.episodic.write(entry)
            elif entry.tier is MemoryTier.SEMANTIC and self._ctrl.semantic:
                self._ctrl.semantic.write(entry)
            elif entry.tier is MemoryTier.STM and self._ctrl.stm:
                self._ctrl.stm.write(entry)
            return True
        except Exception:
            logger.exception("Failed to mark %s for forgetting", key)
            return False

    def health_check(self) -> bool:
        """Verify memory backend availability."""
        try:
            stats = self._ctrl.stats()
            return stats is not None
        except Exception:
            return False
