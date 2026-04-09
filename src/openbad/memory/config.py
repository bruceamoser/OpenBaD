"""Memory system configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MemoryConfig:
    """Configuration for the hierarchical memory system."""

    stm_max_tokens: int = 32768
    stm_ttl_seconds: float = 3600.0
    ltm_backend: str = "json"
    ltm_storage_dir: Path = field(default_factory=lambda: Path("data/memory"))
    pruning_interval_seconds: float = 3600.0
    forgetting_half_life_hours: float = 168.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> MemoryConfig:
        """Load configuration from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        mem = data.get("memory", data)
        return cls(
            stm_max_tokens=mem.get("stm_max_tokens", 32768),
            stm_ttl_seconds=mem.get("stm_ttl_seconds", 3600.0),
            ltm_backend=mem.get("ltm_backend", "json"),
            ltm_storage_dir=Path(mem.get("ltm_storage_dir", "data/memory")),
            pruning_interval_seconds=mem.get("pruning_interval_seconds", 3600.0),
            forgetting_half_life_hours=mem.get("forgetting_half_life_hours", 168.0),
        )

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "stm_max_tokens": self.stm_max_tokens,
            "stm_ttl_seconds": self.stm_ttl_seconds,
            "ltm_backend": self.ltm_backend,
            "ltm_storage_dir": str(self.ltm_storage_dir),
            "pruning_interval_seconds": self.pruning_interval_seconds,
            "forgetting_half_life_hours": self.forgetting_half_life_hours,
        }
