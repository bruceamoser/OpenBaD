"""Active-inference configuration loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ActiveInferenceConfig:
    """Configuration for the prediction-driven exploration system."""

    plugin_dir: Path = Path("src/openbad/plugins/observations")
    surprise_threshold: float = 0.6
    daily_token_budget: int = 5000
    cooldown_seconds: int = 300
    max_concurrent: int = 1
    suppressed_in_states: list[str] = field(
        default_factory=lambda: ["THROTTLED", "EMERGENCY"],
    )
    world_model_history_size: int = 20
    ema_alpha: float = 0.1

    @classmethod
    def from_yaml(cls, path: Path) -> ActiveInferenceConfig:
        """Load configuration from a YAML file."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = raw.get("active_inference", raw)
        if "plugin_dir" in data:
            data["plugin_dir"] = Path(data["plugin_dir"])
        if "suppressed_in_states" in data:
            data["suppressed_in_states"] = list(data["suppressed_in_states"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["plugin_dir"] = str(d["plugin_dir"])
        return d
