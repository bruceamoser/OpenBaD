"""Episodic Long-Term Memory — chronological interaction logs with JSON storage."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier


class EpisodicMemory(MemoryStore):
    """Chronological log of interactions, persisted to JSON.

    Supports time-range queries, task-ID filtering, and append-only logging
    with optional on-disk persistence.
    """

    def __init__(
        self,
        storage_path: Path | None = None,
        auto_persist: bool = True,
    ) -> None:
        self._storage_path = storage_path
        self._auto_persist = auto_persist
        self._entries: dict[str, MemoryEntry] = {}
        self._timeline: list[str] = []  # keys in chronological order

        if storage_path and storage_path.exists():
            self._load()

    # ------------------------------------------------------------------ #
    # MemoryStore interface
    # ------------------------------------------------------------------ #

    def write(self, entry: MemoryEntry) -> str:
        """Append an episodic entry to the log."""
        now = time.time()
        if entry.created_at == 0.0:
            entry.created_at = now
        entry.tier = MemoryTier.EPISODIC

        if entry.key in self._entries:
            # Replace existing — remove from timeline and re-append
            self._timeline = [k for k in self._timeline if k != entry.key]

        self._entries[entry.key] = entry
        self._timeline.append(entry.key)

        if self._auto_persist:
            self._save()

        return entry.entry_id

    def read(self, key: str) -> MemoryEntry | None:
        """Read an entry by key, updating access stats."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.touch(time.time())
        return entry

    def delete(self, key: str) -> bool:
        """Delete an entry from the log."""
        if key not in self._entries:
            return False
        del self._entries[key]
        self._timeline = [k for k in self._timeline if k != key]
        if self._auto_persist:
            self._save()
        return True

    def query(self, prefix: str) -> list[MemoryEntry]:
        """Query entries by key prefix, returned chronologically."""
        return [
            self._entries[k]
            for k in self._timeline
            if k.startswith(prefix)
        ]

    def list_keys(self) -> list[str]:
        """Return all keys in chronological order."""
        return list(self._timeline)

    def size(self) -> int:
        """Return the number of entries."""
        return len(self._entries)

    # ------------------------------------------------------------------ #
    # Episodic-specific methods
    # ------------------------------------------------------------------ #

    def query_time_range(
        self,
        start: float,
        end: float,
    ) -> list[MemoryEntry]:
        """Return entries within the given time range [start, end]."""
        return [
            self._entries[k]
            for k in self._timeline
            if start <= self._entries[k].created_at <= end
        ]

    def query_by_task(self, task_id: str) -> list[MemoryEntry]:
        """Return entries with a matching task_id in metadata."""
        return [
            self._entries[k]
            for k in self._timeline
            if self._entries[k].metadata.get("task_id") == task_id
        ]

    def recent(self, n: int = 10) -> list[MemoryEntry]:
        """Return the N most recent entries."""
        keys = self._timeline[-n:]
        return [self._entries[k] for k in keys]

    def save(self) -> None:
        """Explicitly persist to disk."""
        self._save()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        """Write entries to JSON file."""
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "timeline": self._timeline,
            "entries": {k: e.to_dict() for k, e in self._entries.items()},
        }
        self._storage_path.write_text(
            json.dumps(data, indent=2, default=_json_default),
            encoding="utf-8",
        )

    def _load(self) -> None:
        """Load entries from JSON file."""
        if self._storage_path is None or not self._storage_path.exists():
            return
        raw = self._storage_path.read_text(encoding="utf-8")
        if not raw.strip():
            return
        data = json.loads(raw)
        self._timeline = data.get("timeline", [])
        entries_data: dict[str, Any] = data.get("entries", {})
        self._entries = {
            k: MemoryEntry.from_dict(v) for k, v in entries_data.items()
        }


def _json_default(obj: Any) -> Any:
    """JSON serializer fallback for non-serializable types."""
    return str(obj)
