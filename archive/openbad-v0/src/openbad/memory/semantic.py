"""Semantic Long-Term Memory — vector embeddings with similarity search.

Provides a lightweight in-process cosine-similarity store backed by JSON.
Embedding vectors are produced by a pluggable callback; a simple hash-based
fallback is included for testing without external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier

# Type for an embedding function: text → vector
EmbeddingFn = Callable[[str], list[float]]

_DEFAULT_DIM = 64


def hash_embedding(text: str, dim: int = _DEFAULT_DIM) -> list[float]:
    """Deterministic hash-based embedding for testing (not for production)."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    raw = [int(digest[i : i + 2], 16) / 255.0 for i in range(0, min(dim * 2, len(digest)), 2)]
    # Pad or truncate to dim
    while len(raw) < dim:
        raw.append(0.0)
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw[:dim]]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


class SemanticMemory(MemoryStore):
    """Vector-based semantic memory with cosine similarity search.

    Each entry's value is embedded via `embed_fn`. Similarity search returns
    entries ranked by cosine distance to the query embedding.
    """

    def __init__(
        self,
        embed_fn: EmbeddingFn | None = None,
        storage_path: Path | None = None,
        auto_persist: bool = True,
        similarity_threshold: float = 0.0,
    ) -> None:
        self._embed_fn: EmbeddingFn = embed_fn or hash_embedding
        self._storage_path = storage_path
        self._auto_persist = auto_persist
        self._similarity_threshold = similarity_threshold
        self._entries: dict[str, MemoryEntry] = {}
        self._vectors: dict[str, list[float]] = {}

        if storage_path and storage_path.exists():
            self._load()

    # ------------------------------------------------------------------ #
    # MemoryStore interface
    # ------------------------------------------------------------------ #

    def write(self, entry: MemoryEntry) -> str:
        """Write entry and compute its embedding vector."""
        now = time.time()
        if entry.created_at == 0.0:
            entry.created_at = now
        entry.tier = MemoryTier.SEMANTIC

        self._entries[entry.key] = entry
        self._vectors[entry.key] = self._embed_fn(str(entry.value))

        if self._auto_persist:
            self._save()

        return entry.entry_id

    def read(self, key: str) -> MemoryEntry | None:
        """Read an entry by key."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.touch(time.time())
        return entry

    def delete(self, key: str) -> bool:
        """Delete entry and its vector."""
        if key not in self._entries:
            return False
        del self._entries[key]
        self._vectors.pop(key, None)
        if self._auto_persist:
            self._save()
        return True

    def query(self, prefix: str) -> list[MemoryEntry]:
        """Query entries by key prefix."""
        return [e for k, e in self._entries.items() if k.startswith(prefix)]

    def list_keys(self) -> list[str]:
        """Return all keys."""
        return list(self._entries.keys())

    def size(self) -> int:
        """Return number of entries."""
        return len(self._entries)

    # ------------------------------------------------------------------ #
    # Semantic-specific methods
    # ------------------------------------------------------------------ #

    def search(
        self,
        query_text: str,
        top_k: int = 5,
        tags: list[str] | None = None,
        tag_boost: float = 0.1,
    ) -> list[tuple[MemoryEntry, float]]:
        """Find entries most similar to query_text.

        When *tags* are provided, entries whose ``metadata["tags"]`` overlap
        with the query tags receive a bonus of *tag_boost* per matching tag
        (capped so the final score never exceeds 1.0).

        Returns list of (entry, similarity_score) sorted by descending score.
        """
        query_vec = self._embed_fn(query_text)
        tag_set = set(t.lower() for t in tags) if tags else set()
        scored: list[tuple[str, float]] = []
        for key, vec in self._vectors.items():
            sim = cosine_similarity(query_vec, vec)
            if tag_set:
                entry_tags = {
                    t.lower()
                    for t in self._entries[key].metadata.get("tags", [])
                }
                overlap = len(tag_set & entry_tags)
                sim = min(1.0, sim + overlap * tag_boost)
            if sim >= self._similarity_threshold:
                scored.append((key, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            (self._entries[k], s)
            for k, s in scored[:top_k]
            if k in self._entries
        ]

    def get_vector(self, key: str) -> list[float] | None:
        """Return the embedding vector for a key."""
        return self._vectors.get(key)

    def save(self) -> None:
        """Explicitly persist to disk."""
        self._save()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        """Write entries and vectors to JSON file."""
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": {k: e.to_dict() for k, e in self._entries.items()},
            "vectors": self._vectors,
        }
        self._storage_path.write_text(
            json.dumps(data, indent=2, default=_json_default),
            encoding="utf-8",
        )

    def _load(self) -> None:
        """Load entries and vectors from JSON file."""
        if self._storage_path is None or not self._storage_path.exists():
            return
        raw = self._storage_path.read_text(encoding="utf-8")
        if not raw.strip():
            return
        data = json.loads(raw)
        entries_data: dict[str, Any] = data.get("entries", {})
        self._entries = {
            k: MemoryEntry.from_dict(v) for k, v in entries_data.items()
        }
        self._vectors = data.get("vectors", {})


def _json_default(obj: Any) -> Any:
    """JSON serializer fallback."""
    return str(obj)
