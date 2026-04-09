"""Short-Term Memory — in-memory, token-bounded rolling buffer with TTL."""

from __future__ import annotations

import time
from typing import Any, Callable

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier


def _estimate_tokens(value: Any) -> int:
    """Estimate token count from a value (1 token ≈ 0.75 words)."""
    text = str(value)
    words = len(text.split())
    return max(1, int(words / 0.75))


class ShortTermMemory(MemoryStore):
    """Token-bounded, volatile in-memory buffer with TTL expiry."""

    def __init__(
        self,
        max_tokens: int = 32768,
        default_ttl: float = 3600.0,
        publish_fn: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._default_ttl = default_ttl
        self._publish_fn = publish_fn
        self._entries: dict[str, MemoryEntry] = {}
        self._token_counts: dict[str, int] = {}
        self._tokens_used: int = 0

    # ------------------------------------------------------------------ #
    # MemoryStore interface
    # ------------------------------------------------------------------ #

    def write(self, entry: MemoryEntry) -> str:
        """Write entry, evicting oldest if over budget."""
        now = time.time()
        if entry.created_at == 0.0:
            entry.created_at = now
        if entry.ttl_seconds is None:
            entry.ttl_seconds = self._default_ttl
        entry.tier = MemoryTier.STM

        tokens = _estimate_tokens(entry.value)

        # If key already exists, remove old tokens first
        if entry.key in self._entries:
            self._tokens_used -= self._token_counts[entry.key]

        # Evict expired first
        self.evict_expired()

        # Evict oldest entries until we have room
        while (
            self._tokens_used + tokens > self._max_tokens
            and self._entries
        ):
            self._evict_oldest()

        self._entries[entry.key] = entry
        self._token_counts[entry.key] = tokens
        self._tokens_used += tokens

        if self._publish_fn:
            self._publish_fn(
                "agent/memory/stm/write",
                entry.key.encode(),
            )

        return entry.entry_id

    def read(self, key: str) -> MemoryEntry | None:
        """Read entry by key, updating access stats."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        now = time.time()
        if entry.is_expired(now):
            self._remove(key)
            return None
        entry.touch(now)
        return entry

    def delete(self, key: str) -> bool:
        """Delete entry by key."""
        if key in self._entries:
            self._remove(key)
            return True
        return False

    def query(self, prefix: str) -> list[MemoryEntry]:
        """Query entries by key prefix."""
        now = time.time()
        return [
            e for k, e in self._entries.items()
            if k.startswith(prefix) and not e.is_expired(now)
        ]

    def list_keys(self) -> list[str]:
        """Return all non-expired keys."""
        now = time.time()
        return [k for k, e in self._entries.items() if not e.is_expired(now)]

    def size(self) -> int:
        """Return number of entries."""
        return len(self._entries)

    # ------------------------------------------------------------------ #
    # STM-specific methods
    # ------------------------------------------------------------------ #

    def flush(self) -> list[str]:
        """Clear all entries. Returns list of flushed keys."""
        keys = list(self._entries.keys())
        self._entries.clear()
        self._token_counts.clear()
        self._tokens_used = 0
        return keys

    def usage(self) -> dict[str, int | float]:
        """Return current memory usage stats."""
        now = time.time()
        ages = [now - e.created_at for e in self._entries.values()]
        return {
            "tokens_used": self._tokens_used,
            "tokens_max": self._max_tokens,
            "entry_count": len(self._entries),
            "oldest_entry_age": max(ages) if ages else 0.0,
        }

    def expired_entries(self) -> list[MemoryEntry]:
        """Return entries that have exceeded their TTL."""
        now = time.time()
        return [e for e in self._entries.values() if e.is_expired(now)]

    def evict_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.time()
        expired_keys = [
            k for k, e in self._entries.items() if e.is_expired(now)
        ]
        for k in expired_keys:
            self._remove(k)
        return len(expired_keys)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _remove(self, key: str) -> None:
        """Remove an entry and update token count."""
        if key in self._entries:
            del self._entries[key]
            self._tokens_used -= self._token_counts.pop(key, 0)

    def _evict_oldest(self) -> None:
        """Evict the oldest entry by created_at."""
        if not self._entries:
            return
        oldest_key = min(self._entries, key=lambda k: self._entries[k].created_at)
        self._remove(oldest_key)
