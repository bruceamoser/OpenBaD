"""Procedural Long-Term Memory — skill library for learned strategies.

Stores reusable workflows, executable strategies, and learned patterns.
Each skill has a capability set for search-by-capability, a confidence
score (Bayesian), and optional executable code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier


@dataclass
class Skill:
    """A reusable procedural skill."""

    name: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    code: str = ""
    confidence: float = 0.5
    success_count: int = 0
    failure_count: int = 0

    def update_confidence(self, success: bool) -> None:
        """Bayesian confidence update based on outcome."""
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        total = self.success_count + self.failure_count
        # Bayesian posterior mean with uniform prior (alpha=1, beta=1)
        self.confidence = (self.success_count + 1) / (total + 2)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "code": self.code,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Skill:
        """Deserialize from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            code=data.get("code", ""),
            confidence=data.get("confidence", 0.5),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
        )


class ProceduralMemory(MemoryStore):
    """Skill library backed by JSON persistence.

    Each entry's value is expected to be a Skill (or a dict that describes
    one). Supports search by capability and confidence-based ranking.
    """

    def __init__(
        self,
        storage_path: Path | None = None,
        auto_persist: bool = True,
    ) -> None:
        self._storage_path = storage_path
        self._auto_persist = auto_persist
        self._entries: dict[str, MemoryEntry] = {}
        self._skills: dict[str, Skill] = {}

        if storage_path and storage_path.exists():
            self._load()

    # ------------------------------------------------------------------ #
    # MemoryStore interface
    # ------------------------------------------------------------------ #

    def write(self, entry: MemoryEntry) -> str:
        """Write a skill entry to the store."""
        now = time.time()
        if entry.created_at == 0.0:
            entry.created_at = now
        entry.tier = MemoryTier.PROCEDURAL

        self._entries[entry.key] = entry

        # Accept Skill objects or dicts
        if isinstance(entry.value, Skill):
            self._skills[entry.key] = entry.value
        elif isinstance(entry.value, dict):
            self._skills[entry.key] = Skill.from_dict(entry.value)
        else:
            # Wrap bare values as a skill with the value as description
            self._skills[entry.key] = Skill(
                name=entry.key, description=str(entry.value),
            )

        if self._auto_persist:
            self._save()

        return entry.entry_id

    def read(self, key: str) -> MemoryEntry | None:
        """Read a skill entry by key."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.touch(time.time())
        return entry

    def delete(self, key: str) -> bool:
        """Delete a skill entry."""
        if key not in self._entries:
            return False
        del self._entries[key]
        self._skills.pop(key, None)
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
        """Return number of skills."""
        return len(self._entries)

    # ------------------------------------------------------------------ #
    # Procedural-specific methods
    # ------------------------------------------------------------------ #

    def get_skill(self, key: str) -> Skill | None:
        """Return the Skill object for a key."""
        return self._skills.get(key)

    def search_by_capability(self, capability: str) -> list[tuple[str, Skill]]:
        """Find skills that have the given capability, ranked by confidence."""
        matches = [
            (k, s) for k, s in self._skills.items()
            if capability in s.capabilities
        ]
        matches.sort(key=lambda x: x[1].confidence, reverse=True)
        return matches

    def record_outcome(self, key: str, success: bool) -> None:
        """Record a success/failure for a skill, updating confidence."""
        skill = self._skills.get(key)
        if skill is None:
            return
        skill.update_confidence(success)
        if self._auto_persist:
            self._save()

    def top_skills(self, n: int = 5) -> list[tuple[str, Skill]]:
        """Return top N skills ranked by confidence."""
        ranked = sorted(
            self._skills.items(),
            key=lambda x: x[1].confidence,
            reverse=True,
        )
        return ranked[:n]

    def save(self) -> None:
        """Explicitly persist to disk."""
        self._save()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        """Write entries and skills to JSON file."""
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": {k: e.to_dict() for k, e in self._entries.items()},
            "skills": {k: s.to_dict() for k, s in self._skills.items()},
        }
        self._storage_path.write_text(
            json.dumps(data, indent=2, default=_json_default),
            encoding="utf-8",
        )

    def _load(self) -> None:
        """Load entries and skills from JSON file."""
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
        skills_data: dict[str, Any] = data.get("skills", {})
        self._skills = {
            k: Skill.from_dict(v) for k, v in skills_data.items()
        }


def _json_default(obj: Any) -> Any:
    """JSON serializer fallback."""
    if isinstance(obj, Skill):
        return obj.to_dict()
    return str(obj)
