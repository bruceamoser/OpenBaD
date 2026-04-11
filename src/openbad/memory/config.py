"""Memory system configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SleepConfig:
    """Configuration for scheduled and opportunistic sleep consolidation."""

    sleep_window_start: str = "02:00"
    sleep_window_duration_hours: float = 3.0
    idle_timeout_minutes: int = 15
    allow_daytime_naps: bool = True
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> SleepConfig:
        """Build sleep configuration from a mapping."""
        return cls(
            sleep_window_start=str(data.get("sleep_window_start", "02:00")),
            sleep_window_duration_hours=float(
                data.get("sleep_window_duration_hours", 3.0)
            ),
            idle_timeout_minutes=int(data.get("idle_timeout_minutes", 15)),
            allow_daytime_naps=bool(data.get("allow_daytime_naps", True)),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        """Serialize sleep config to a dictionary."""
        return {
            "sleep_window_start": self.sleep_window_start,
            "sleep_window_duration_hours": self.sleep_window_duration_hours,
            "idle_timeout_minutes": self.idle_timeout_minutes,
            "allow_daytime_naps": self.allow_daytime_naps,
            "enabled": self.enabled,
        }


@dataclass
class MemoryConfig:
    """Configuration for the hierarchical memory system."""

    stm_max_tokens: int = 32768
    stm_ttl_seconds: float = 3600.0
    ltm_backend: str = "json"
    ltm_storage_dir: Path = field(default_factory=lambda: Path("data/memory"))
    pruning_interval_seconds: float = 3600.0
    forgetting_half_life_hours: float = 168.0
    episodic_retention_days: float = 7.0
    sleep: SleepConfig = field(default_factory=SleepConfig)

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
            episodic_retention_days=mem.get("episodic_retention_days", 7.0),
            sleep=SleepConfig.from_dict(mem.get("sleep", {})),
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
            "episodic_retention_days": self.episodic_retention_days,
            "sleep": self.sleep.to_dict(),
        }
