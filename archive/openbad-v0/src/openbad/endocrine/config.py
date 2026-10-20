"""Endocrine system configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HormoneConfig:
    """Per-hormone tunables."""

    increment: float = 0.15
    activation_threshold: float = 0.50
    escalation_threshold: float | None = None
    half_life_seconds: float = 300.0


@dataclass
class EndocrineConfig:
    """Top-level endocrine configuration."""

    dopamine: HormoneConfig = field(
        default_factory=lambda: HormoneConfig(
            increment=0.15,
            activation_threshold=0.50,
            half_life_seconds=300.0,
        ),
    )
    adrenaline: HormoneConfig = field(
        default_factory=lambda: HormoneConfig(
            increment=0.25,
            activation_threshold=0.60,
            escalation_threshold=0.85,
            half_life_seconds=60.0,
        ),
    )
    cortisol: HormoneConfig = field(
        default_factory=lambda: HormoneConfig(
            increment=0.15,
            activation_threshold=0.50,
            escalation_threshold=0.80,
            half_life_seconds=900.0,
        ),
    )
    endorphin: HormoneConfig = field(
        default_factory=lambda: HormoneConfig(
            increment=0.15,
            activation_threshold=0.40,
            half_life_seconds=600.0,
        ),
    )
    publish_interval_seconds: float = 10.0
    significant_change_delta: float = 0.1

    @classmethod
    def from_yaml(cls, path: Path) -> EndocrineConfig:
        """Load configuration from a YAML file."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = raw.get("endocrine", raw)

        hormones: dict[str, HormoneConfig] = {}
        for name in ("dopamine", "adrenaline", "cortisol", "endorphin"):
            if name in data and isinstance(data[name], dict):
                fields = HormoneConfig.__dataclass_fields__
                hormones[name] = HormoneConfig(
                    **{k: v for k, v in data[name].items() if k in fields},
                )

        kwargs: dict = {}
        for scalar in ("publish_interval_seconds", "significant_change_delta"):
            if scalar in data:
                kwargs[scalar] = float(data[scalar])

        return cls(**hormones, **kwargs)
