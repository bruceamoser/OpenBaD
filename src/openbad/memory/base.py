"""Base types and abstractions for the hierarchical memory system."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryTier(Enum):
    """Memory tier classification."""

    STM = "stm"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryEntry:
    """A single entry in the memory system.

    **Library refs convention** — Semantic entries that reference Library
    books should include ``metadata={"library_refs": ["book-uuid-1", ...]}``.
    When recalled, these pointers are expanded into annotations so the LLM
    knows exhaustive documentation is available in the Library.
    """

    key: str
    value: Any
    tier: MemoryTier
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: float = 0.0
    accessed_at: float = 0.0
    access_count: int = 0
    ttl_seconds: float | None = None
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float) -> bool:
        """Check if this entry has exceeded its TTL."""
        if self.ttl_seconds is None or self.ttl_seconds <= 0:
            return False
        return (now - self.created_at) > self.ttl_seconds

    def touch(self, now: float) -> None:
        """Update access timestamp and increment access count."""
        self.accessed_at = now
        self.access_count += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "entry_id": self.entry_id,
            "key": self.key,
            "value": self.value,
            "tier": self.tier.value,
            "created_at": self.created_at,
            "accessed_at": self.accessed_at,
            "access_count": self.access_count,
            "ttl_seconds": self.ttl_seconds,
            "context": self.context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        """Deserialize from dictionary."""
        return cls(
            entry_id=data["entry_id"],
            key=data["key"],
            value=data["value"],
            tier=MemoryTier(data["tier"]),
            created_at=data.get("created_at", 0.0),
            accessed_at=data.get("accessed_at", 0.0),
            access_count=data.get("access_count", 0),
            ttl_seconds=data.get("ttl_seconds"),
            context=data.get("context", ""),
            metadata=data.get("metadata", {}),
        )


class MemoryStore(ABC):
    """Abstract base class for memory tier stores."""

    @abstractmethod
    def write(self, entry: MemoryEntry) -> str:
        """Write an entry to the store. Returns the entry_id."""

    @abstractmethod
    def read(self, key: str) -> MemoryEntry | None:
        """Read an entry by key. Returns None if not found."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete an entry by key. Returns True if entry existed."""

    @abstractmethod
    def query(self, prefix: str) -> list[MemoryEntry]:
        """Query entries by key prefix or pattern."""

    @abstractmethod
    def list_keys(self) -> list[str]:
        """Return all keys in the store."""

    @abstractmethod
    def size(self) -> int:
        """Return the number of entries in the store."""
