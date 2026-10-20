"""Immune system configuration — detection thresholds, model paths, quarantine settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RulesConfig:
    """Configuration for the regex rules engine."""

    rules_path: str = "config/immune_rules.yaml"
    max_scan_ms: int = 50


@dataclass(frozen=True)
class ClassifierConfig:
    """Configuration for the Ollama-based prompt injection classifier."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    timeout_ms: int = 500
    confidence_threshold: float = 0.7


@dataclass(frozen=True)
class QuarantineConfig:
    """Configuration for the quarantine subsystem."""

    quarantine_dir: str = "quarantine"
    encryption_key_env: str = "OPENBAD_QUARANTINE_KEY"
    max_payload_bytes: int = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True)
class ImmuneConfig:
    """Top-level immune system configuration."""

    rules: RulesConfig = field(default_factory=RulesConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    quarantine: QuarantineConfig = field(default_factory=QuarantineConfig)
    scan_timeout_ms: int = 100
    enabled: bool = True


def load_immune_config(yaml_path: str | Path = "config/immune.yaml") -> ImmuneConfig:
    """Load immune system config from a YAML file, falling back to defaults."""
    path = Path(yaml_path)
    if not path.exists():
        return ImmuneConfig()

    with open(path) as f:  # noqa: S108
        data = yaml.safe_load(f) or {}

    immune = data.get("immune", {})
    rules_data = immune.get("rules", {})
    classifier_data = immune.get("classifier", {})
    quarantine_data = immune.get("quarantine", {})

    return ImmuneConfig(
        rules=RulesConfig(**rules_data),
        classifier=ClassifierConfig(**classifier_data),
        quarantine=QuarantineConfig(**quarantine_data),
        scan_timeout_ms=immune.get("scan_timeout_ms", 100),
        enabled=immune.get("enabled", True),
    )
