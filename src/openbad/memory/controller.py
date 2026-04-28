"""Memory Controller — unified orchestrator for STM and LTM tiers.

Routes writes to the appropriate store, handles tier promotion from
STM to LTM, and exposes a unified query API across all memory tiers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier
from openbad.memory.cognitive_store import CognitiveMemoryStore
from openbad.memory.config import MemoryConfig
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.procedural import ProceduralMemory, Skill
from openbad.memory.semantic import SemanticMemory, hash_embedding
from openbad.memory.stm import ShortTermMemory
from openbad.state.db import initialize_state_db

logger = logging.getLogger(__name__)

# Type for an optional MQTT publish callback
PublishFn = Callable[[str, bytes], None]


def make_ollama_embed_fn(
    base_url: str = "http://localhost:11434",
    model: str = "nomic-embed-text",
) -> Callable[[str], list[float]]:
    """Create a sync embedding function backed by OllamaProvider.

    Falls back to ``hash_embedding`` when Ollama is unreachable.
    """
    from openbad.cognitive.providers.ollama import OllamaProvider

    provider = OllamaProvider(base_url=base_url)

    def _embed(text: str) -> list[float]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(
                        asyncio.run, provider.embed([text], model_id=model)
                    ).result(timeout=30)
            else:
                result = asyncio.run(provider.embed([text], model_id=model))
            return result[0]
        except Exception:
            logger.debug("Ollama embed unavailable, falling back to hash_embedding")
            return hash_embedding(text, dim=768)

    return _embed


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

        if cfg.ltm_backend == "sqlite":
            db_path = Path(storage_dir) / ".." / "state.db"
            conn = initialize_state_db(db_path)
            self.episodic: MemoryStore = CognitiveMemoryStore(
                conn, MemoryTier.EPISODIC,
            )
            self.semantic: MemoryStore = CognitiveMemoryStore(
                conn, MemoryTier.SEMANTIC,
            )
            self.procedural: MemoryStore = CognitiveMemoryStore(
                conn, MemoryTier.PROCEDURAL,
            )
            self._cognitive = True
            self._db_conn: sqlite3.Connection | None = conn
            _migrate_json_to_sqlite(storage_dir, conn)
        else:
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
            self._cognitive = False
            self._db_conn = None

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

    def recall(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Recall memories ranked by relevance, with library ref annotations.

        When the cognitive backend is active, uses the ``activate()``
        pipeline (BM25 + ACT-R + Hebbian).  Otherwise falls back to the
        legacy cosine-similarity search.
        """
        if self._cognitive:
            return self._recall_cognitive(query, top_k)
        return self._recall_legacy(query, top_k)

    def _recall_cognitive(
        self, query: str, top_k: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for store_name in ("semantic", "episodic"):
            store = getattr(self, store_name)
            if not isinstance(store, CognitiveMemoryStore):
                continue
            try:
                for ar in store.activate(query, limit=top_k):
                    item: dict[str, Any] = {
                        "key": ar.entry.key,
                        "value": str(ar.entry.value),
                        "tier": ar.entry.tier.value,
                        "score": ar.score,
                        "why": ar.why,
                        "metadata": ar.entry.metadata,
                    }
                    _annotate_library_refs(item, ar.entry)
                    results.append(item)
            except Exception:
                logger.exception("Cognitive recall failed for %s", store_name)

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def _recall_legacy(
        self, query: str, top_k: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        # Semantic search — scored
        try:
            for entry, score in self.semantic.search(query, top_k=top_k):
                item: dict[str, Any] = {
                    "key": entry.key,
                    "value": str(entry.value),
                    "tier": MemoryTier.SEMANTIC.value,
                    "score": score,
                    "metadata": entry.metadata,
                }
                _annotate_library_refs(item, entry)
                results.append(item)
        except Exception:
            logger.exception("Semantic recall failed")

        # Episodic prefix search — unscored, ordered by recency
        try:
            for entry in self.episodic.query(query)[:top_k]:
                item = {
                    "key": entry.key,
                    "value": str(entry.value),
                    "tier": MemoryTier.EPISODIC.value,
                    "score": 0.0,
                    "metadata": entry.metadata,
                }
                _annotate_library_refs(item, entry)
                results.append(item)
        except Exception:
            logger.exception("Episodic recall failed")

        # Semantic scored results first, then episodic
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _annotate_library_refs(item: dict[str, Any], entry: MemoryEntry) -> None:
    """Append library pointer annotations when ``library_refs`` exist."""
    refs = entry.metadata.get("library_refs")
    if not refs:
        return
    annotations = []
    for book_id in refs:
        annotations.append(
            f"[Knowledge Node: {entry.key}. Detail: {entry.value}.\n"
            f" Exhaustive documentation available in Library Book ID: {book_id}]"
        )
    item["library_annotations"] = annotations


# ---------------------------------------------------------------------------
# JSON → SQLite migration
# ---------------------------------------------------------------------------

_TIER_MAP = {
    "episodic": MemoryTier.EPISODIC,
    "semantic": MemoryTier.SEMANTIC,
    "procedural": MemoryTier.PROCEDURAL,
}


def _migrate_json_to_sqlite(
    storage_dir: Path,
    conn: sqlite3.Connection,
) -> None:
    """One-time migration of JSON memory files into the engrams table.

    Runs automatically on first startup when JSON files exist and the
    engrams table is empty for that tier.  After successful migration
    the JSON files are renamed to ``*.json.migrated``.
    """
    for name, tier in _TIER_MAP.items():
        json_path = Path(storage_dir) / f"{name}.json"
        if not json_path.exists():
            continue

        # Only migrate if the tier has no existing data
        count = conn.execute(
            "SELECT COUNT(*) FROM engrams WHERE tier = ?", (tier.value,),
        ).fetchone()[0]
        if count > 0:
            continue

        try:
            raw = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read %s for migration", json_path)
            continue

        entries: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            # Typical format: { "entries": [...] } or bare { key: entry }
            if "entries" in raw and isinstance(raw["entries"], list):
                entries = raw["entries"]
            else:
                entries = list(raw.values())
        elif isinstance(raw, list):
            entries = raw

        migrated = 0
        store = CognitiveMemoryStore(conn, tier)
        for data in entries:
            if not isinstance(data, dict):
                continue
            try:
                entry = MemoryEntry.from_dict(data)
            except (KeyError, TypeError):
                logger.debug("Skipping malformed entry during migration")
                continue
            store.write(entry)
            migrated += 1

        if migrated:
            migrated_path = json_path.with_suffix(".json.migrated")
            json_path.rename(migrated_path)
            logger.info(
                "Migrated %d %s entries from JSON to SQLite", migrated, name,
            )
