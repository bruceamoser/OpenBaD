"""Memory Controller — unified orchestrator for STM and LTM tiers.

Routes writes to the appropriate store, handles tier promotion from
STM to LTM, and exposes a unified query API across all memory tiers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.procedural import ProceduralMemory, Skill
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory

logger = logging.getLogger(__name__)

# Type for an optional MQTT publish callback
PublishFn = Callable[[str, bytes], None]


class MemoryController:
    """Unified orchestrator for short-term and long-term memory tiers.

    Provides a single API surface for:
    - STM writes with automatic tier tagging
    - Promotion of STM entries to specific LTM stores
    - Unified search across all tiers
    - Snapshot and stats reporting
    """

    def __init__(
        self,
        config: MemoryConfig | None = None,
        publish_fn: PublishFn | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        cfg = config or MemoryConfig()
        storage_dir = cfg.ltm_storage_dir

        self.stm = ShortTermMemory(
            max_tokens=cfg.stm_max_tokens,
            default_ttl=cfg.stm_ttl_seconds,
            publish_fn=publish_fn,
        )
        self.episodic = EpisodicMemory(
            storage_path=Path(storage_dir) / "episodic.json",
        )
        self.semantic = SemanticMemory(
            embed_fn=embed_fn,
            storage_path=Path(storage_dir) / "semantic.json",
        )
        self.procedural = ProceduralMemory(
            storage_path=Path(storage_dir) / "procedural.json",
        )
        self._publish_fn = publish_fn

    # ------------------------------------------------------------------ #
    # Unified write
    # ------------------------------------------------------------------ #

    def write_stm(self, key: str, value: Any, **kwargs: Any) -> str:
        """Write an entry to short-term memory."""
        entry = MemoryEntry(key=key, value=value, tier=MemoryTier.STM, **kwargs)
        return self.stm.write(entry)

    def write_episodic(self, key: str, value: Any, **kwargs: Any) -> str:
        """Write an entry directly to episodic LTM."""
        entry = MemoryEntry(key=key, value=value, tier=MemoryTier.EPISODIC, **kwargs)
        return self.episodic.write(entry)

    def write_semantic(self, key: str, value: Any, **kwargs: Any) -> str:
        """Write an entry directly to semantic LTM."""
        entry = MemoryEntry(key=key, value=value, tier=MemoryTier.SEMANTIC, **kwargs)
        return self.semantic.write(entry)

    def write_procedural(
        self, key: str, value: Skill | dict[str, Any] | str, **kwargs: Any,
    ) -> str:
        """Write a skill to procedural LTM."""
        entry = MemoryEntry(key=key, value=value, tier=MemoryTier.PROCEDURAL, **kwargs)
        return self.procedural.write(entry)

    # ------------------------------------------------------------------ #
    # Tier promotion
    # ------------------------------------------------------------------ #

    def promote_to_episodic(self, stm_key: str) -> str | None:
        """Move an STM entry to episodic LTM. Returns new entry_id or None."""
        entry = self.stm.read(stm_key)
        if entry is None:
            logger.warning("Cannot promote — STM key not found: %s", stm_key)
            return None
        new_entry = MemoryEntry(
            key=entry.key,
            value=entry.value,
            tier=MemoryTier.EPISODIC,
            created_at=entry.created_at,
            context=entry.context,
            metadata={**entry.metadata, "promoted_from": "stm"},
        )
        eid = self.episodic.write(new_entry)
        self.stm.delete(stm_key)
        logger.info("Promoted %s from STM to episodic LTM", stm_key)
        return eid

    def promote_to_semantic(self, stm_key: str) -> str | None:
        """Move an STM entry to semantic LTM. Returns new entry_id or None."""
        entry = self.stm.read(stm_key)
        if entry is None:
            logger.warning("Cannot promote — STM key not found: %s", stm_key)
            return None
        new_entry = MemoryEntry(
            key=entry.key,
            value=entry.value,
            tier=MemoryTier.SEMANTIC,
            created_at=entry.created_at,
            context=entry.context,
            metadata={**entry.metadata, "promoted_from": "stm"},
        )
        eid = self.semantic.write(new_entry)
        self.stm.delete(stm_key)
        logger.info("Promoted %s from STM to semantic LTM", stm_key)
        return eid

    # ------------------------------------------------------------------ #
    # Unified read / search
    # ------------------------------------------------------------------ #

    def read(self, key: str) -> MemoryEntry | None:
        """Read from any tier, checking STM first then LTM stores."""
        for store in [self.stm, self.episodic, self.semantic, self.procedural]:
            result = store.read(key)
            if result is not None:
                return result
        return None

    def search_all(self, prefix: str) -> dict[str, list[MemoryEntry]]:
        """Query all tiers by key prefix. Returns {tier_name: entries}."""
        return {
            "stm": self.stm.query(prefix),
            "episodic": self.episodic.query(prefix),
            "semantic": self.semantic.query(prefix),
            "procedural": self.procedural.query(prefix),
        }

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        """Return memory usage statistics across all tiers."""
        return {
            "stm": self.stm.usage(),
            "episodic": {"entry_count": self.episodic.size()},
            "semantic": {"entry_count": self.semantic.size()},
            "procedural": {"entry_count": self.procedural.size()},
            "timestamp": time.time(),
        }

    def flush_stm(self) -> list[str]:
        """Flush all STM entries. Returns list of flushed keys."""
        return self.stm.flush()
