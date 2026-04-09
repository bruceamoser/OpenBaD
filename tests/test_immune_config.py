"""Tests for openbad.immune_system.config — immune system configuration."""

from __future__ import annotations

import textwrap
from pathlib import Path

from openbad.immune_system.config import (
    ClassifierConfig,
    ImmuneConfig,
    QuarantineConfig,
    RulesConfig,
    load_immune_config,
)

# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    def test_immune_config_defaults(self) -> None:
        cfg = ImmuneConfig()
        assert cfg.enabled is True
        assert cfg.scan_timeout_ms == 100

    def test_rules_defaults(self) -> None:
        cfg = RulesConfig()
        assert cfg.rules_path == "config/immune_rules.yaml"
        assert cfg.max_scan_ms == 50

    def test_classifier_defaults(self) -> None:
        cfg = ClassifierConfig()
        assert cfg.base_url == "http://localhost:11434"
        assert cfg.model == "llama3.2"
        assert cfg.timeout_ms == 500
        assert cfg.confidence_threshold == 0.7

    def test_quarantine_defaults(self) -> None:
        cfg = QuarantineConfig()
        assert cfg.quarantine_dir == "quarantine"
        assert cfg.encryption_key_env == "OPENBAD_QUARANTINE_KEY"
        assert cfg.max_payload_bytes == 10 * 1024 * 1024

    def test_nested_defaults(self) -> None:
        cfg = ImmuneConfig()
        assert isinstance(cfg.rules, RulesConfig)
        assert isinstance(cfg.classifier, ClassifierConfig)
        assert isinstance(cfg.quarantine, QuarantineConfig)

    def test_frozen(self) -> None:
        cfg = ImmuneConfig()
        try:
            cfg.enabled = False  # type: ignore[misc]
            raised = False
        except AttributeError:
            raised = True
        assert raised


# ---------------------------------------------------------------------------
# Load from YAML
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_immune_config(tmp_path / "nonexistent.yaml")
        assert cfg == ImmuneConfig()

    def test_load_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("")
        cfg = load_immune_config(yaml_path)
        assert cfg == ImmuneConfig()

    def test_load_partial_config(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "partial.yaml"
        yaml_path.write_text(
            textwrap.dedent("""\
            immune:
              enabled: false
              scan_timeout_ms: 200
        """)
        )
        cfg = load_immune_config(yaml_path)
        assert cfg.enabled is False
        assert cfg.scan_timeout_ms == 200
        # Nested should still be defaults
        assert cfg.rules == RulesConfig()
        assert cfg.classifier == ClassifierConfig()

    def test_load_full_config(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "full.yaml"
        yaml_path.write_text(
            textwrap.dedent("""\
            immune:
              enabled: true
              scan_timeout_ms: 75
              rules:
                rules_path: "custom/rules.yaml"
                max_scan_ms: 25
              classifier:
                base_url: "http://remote:11434"
                model: "phi3"
                timeout_ms: 300
                confidence_threshold: 0.9
              quarantine:
                quarantine_dir: "/var/quarantine"
                encryption_key_env: "CUSTOM_KEY"
                max_payload_bytes: 5242880
        """)
        )
        cfg = load_immune_config(yaml_path)
        assert cfg.scan_timeout_ms == 75
        assert cfg.rules.rules_path == "custom/rules.yaml"
        assert cfg.rules.max_scan_ms == 25
        assert cfg.classifier.base_url == "http://remote:11434"
        assert cfg.classifier.model == "phi3"
        assert cfg.quarantine.quarantine_dir == "/var/quarantine"
        assert cfg.quarantine.max_payload_bytes == 5242880
