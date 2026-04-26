"""Tests for framework configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openbad.frameworks.config import (
    ConfigValidationError,
    CrewConfig,
    FrameworksConfig,
    LangGraphConfig,
    load_agent_priorities,
    load_frameworks_config,
)


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write_cognitive(cfg_dir: Path, data: dict) -> None:
    (cfg_dir / "cognitive.yaml").write_text(yaml.dump(data))


def _write_routing(cfg_dir: Path, data: dict) -> None:
    (cfg_dir / "model_routing.yaml").write_text(yaml.dump(data))


# ── Defaults ──────────────────────────────────────────────────────── #


class TestDefaults:
    def test_missing_file_returns_defaults(self, config_dir: Path) -> None:
        cfg = load_frameworks_config(config_dir)
        assert cfg == FrameworksConfig()

    def test_missing_section_returns_defaults(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {"cognitive": {"enabled": True}})
        cfg = load_frameworks_config(config_dir)
        assert cfg == FrameworksConfig()

    def test_default_crew_values(self) -> None:
        crew = CrewConfig()
        assert crew.verbose is False
        assert crew.max_iterations == 10
        assert crew.allow_delegation is True

    def test_default_langgraph_values(self) -> None:
        lg = LangGraphConfig()
        assert lg.checkpoint_format == "json"
        assert lg.checkpoint_retention_hours == 168


# ── Loading ───────────────────────────────────────────────────────── #


class TestLoading:
    def test_loads_crew_config(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {
            "cognitive": {
                "frameworks": {
                    "crews": {
                        "user_facing": {
                            "verbose": True,
                            "max_iterations": 5,
                            "allow_delegation": False,
                        },
                    },
                },
            },
        })
        cfg = load_frameworks_config(config_dir)
        assert "user_facing" in cfg.crews
        assert cfg.crews["user_facing"].verbose is True
        assert cfg.crews["user_facing"].max_iterations == 5
        assert cfg.crews["user_facing"].allow_delegation is False

    def test_loads_langgraph_config(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {
            "cognitive": {
                "frameworks": {
                    "langgraph": {
                        "checkpoint_format": "pickle",
                        "checkpoint_retention_hours": 48,
                    },
                },
            },
        })
        cfg = load_frameworks_config(config_dir)
        assert cfg.langgraph.checkpoint_format == "pickle"
        assert cfg.langgraph.checkpoint_retention_hours == 48

    def test_loads_agent_config(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {
            "cognitive": {
                "frameworks": {
                    "agents": {
                        "doctor": {"priority": "critical"},
                        "explorer": {"priority": "low"},
                    },
                },
            },
        })
        cfg = load_frameworks_config(config_dir)
        assert cfg.agents["doctor"].priority == "critical"
        assert cfg.agents["explorer"].priority == "low"

    def test_loads_agent_priorities_from_routing(
        self, config_dir: Path,
    ) -> None:
        _write_routing(config_dir, {
            "agent_priorities": {
                "chat": "medium",
                "doctor": "critical",
            },
        })
        priorities = load_agent_priorities(config_dir)
        assert priorities == {"chat": "medium", "doctor": "critical"}

    def test_missing_routing_file(self, config_dir: Path) -> None:
        priorities = load_agent_priorities(config_dir)
        assert priorities == {}


# ── Validation ────────────────────────────────────────────────────── #


class TestValidation:
    def test_invalid_agent_priority(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {
            "cognitive": {
                "frameworks": {
                    "agents": {
                        "chat": {"priority": "super_high"},
                    },
                },
            },
        })
        with pytest.raises(ConfigValidationError, match="super_high"):
            load_frameworks_config(config_dir)

    def test_invalid_checkpoint_format(self, config_dir: Path) -> None:
        _write_cognitive(config_dir, {
            "cognitive": {
                "frameworks": {
                    "langgraph": {
                        "checkpoint_format": "msgpack",
                    },
                },
            },
        })
        with pytest.raises(ConfigValidationError, match="msgpack"):
            load_frameworks_config(config_dir)

    def test_invalid_routing_priority(self, config_dir: Path) -> None:
        _write_routing(config_dir, {
            "agent_priorities": {
                "doctor": "ultra",
            },
        })
        with pytest.raises(ConfigValidationError, match="ultra"):
            load_agent_priorities(config_dir)


# ── Full config file ──────────────────────────────────────────────── #


class TestFullConfig:
    def test_loads_real_config_dir(self) -> None:
        """Load from the actual project config/ directory."""
        cfg = load_frameworks_config()
        assert isinstance(cfg, FrameworksConfig)
        assert len(cfg.crews) > 0
        assert len(cfg.agents) > 0

    def test_loads_real_agent_priorities(self) -> None:
        """Load agent priorities from the actual config/ directory."""
        priorities = load_agent_priorities()
        assert isinstance(priorities, dict)
        assert len(priorities) > 0
