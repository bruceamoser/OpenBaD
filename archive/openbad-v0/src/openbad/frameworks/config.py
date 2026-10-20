"""Configuration loader and validator for framework integration.

Loads the ``frameworks:`` section from ``config/cognitive.yaml`` and the
``agent_priorities:`` section from ``config/model_routing.yaml``, validates
their structure, and exposes typed dataclasses for runtime use.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

_VALID_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
_VALID_CHECKPOINT_FORMATS = frozenset({"json", "pickle"})


# ── Dataclasses ─────────────────────────────────────────────────── #


@dataclasses.dataclass(frozen=True)
class CrewConfig:
    """Configuration for a single CrewAI crew."""

    verbose: bool = False
    max_iterations: int = 10
    allow_delegation: bool = True


@dataclasses.dataclass(frozen=True)
class LangGraphConfig:
    """LangGraph checkpoint and retention settings."""

    checkpoint_format: str = "json"
    checkpoint_retention_hours: int = 168


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    """Per-agent routing priority."""

    priority: str = "medium"


@dataclasses.dataclass(frozen=True)
class FrameworksConfig:
    """Top-level frameworks configuration."""

    crews: dict[str, CrewConfig] = dataclasses.field(default_factory=dict)
    langgraph: LangGraphConfig = dataclasses.field(
        default_factory=LangGraphConfig,
    )
    agents: dict[str, AgentConfig] = dataclasses.field(default_factory=dict)


# ── Validation ──────────────────────────────────────────────────── #


class ConfigValidationError(ValueError):
    """Raised when framework configuration is invalid."""


def _validate_priority(name: str, value: str) -> None:
    if value not in _VALID_PRIORITIES:
        raise ConfigValidationError(
            f"Invalid priority {value!r} for agent {name!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
        )


def _validate_checkpoint_format(fmt: str) -> None:
    if fmt not in _VALID_CHECKPOINT_FORMATS:
        raise ConfigValidationError(
            f"Invalid checkpoint format {fmt!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_CHECKPOINT_FORMATS))}"
        )


# ── Loading ─────────────────────────────────────────────────────── #


def _parse_crew(name: str, raw: dict[str, Any]) -> CrewConfig:
    return CrewConfig(
        verbose=bool(raw.get("verbose", False)),
        max_iterations=int(raw.get("max_iterations", 10)),
        allow_delegation=bool(raw.get("allow_delegation", True)),
    )


def _parse_langgraph(raw: dict[str, Any]) -> LangGraphConfig:
    fmt = str(raw.get("checkpoint_format", "json"))
    _validate_checkpoint_format(fmt)
    return LangGraphConfig(
        checkpoint_format=fmt,
        checkpoint_retention_hours=int(
            raw.get("checkpoint_retention_hours", 168),
        ),
    )


def _parse_agent(name: str, raw: dict[str, Any]) -> AgentConfig:
    priority = str(raw.get("priority", "medium"))
    _validate_priority(name, priority)
    return AgentConfig(priority=priority)


def load_frameworks_config(
    config_dir: Path | None = None,
) -> FrameworksConfig:
    """Load and validate the ``frameworks:`` section from cognitive.yaml.

    Parameters
    ----------
    config_dir:
        Override the default config directory (useful for testing).

    Returns
    -------
    FrameworksConfig
        Validated configuration, or defaults if the section is absent.
    """
    cfg_dir = config_dir or _CONFIG_DIR
    cognitive_path = cfg_dir / "cognitive.yaml"

    if not cognitive_path.exists():
        log.warning("cognitive.yaml not found at %s; using defaults", cfg_dir)
        return FrameworksConfig()

    with cognitive_path.open() as f:
        data = yaml.safe_load(f) or {}

    frameworks_raw = data.get("cognitive", {}).get("frameworks", {})
    if not frameworks_raw:
        log.info("No frameworks section in cognitive.yaml; using defaults")
        return FrameworksConfig()

    crews = {
        name: _parse_crew(name, crew_raw)
        for name, crew_raw in frameworks_raw.get("crews", {}).items()
    }

    langgraph_raw = frameworks_raw.get("langgraph", {})
    langgraph = _parse_langgraph(langgraph_raw)

    agents = {
        name: _parse_agent(name, agent_raw)
        for name, agent_raw in frameworks_raw.get("agents", {}).items()
    }

    return FrameworksConfig(crews=crews, langgraph=langgraph, agents=agents)


def load_agent_priorities(
    config_dir: Path | None = None,
) -> dict[str, str]:
    """Load ``agent_priorities:`` from model_routing.yaml.

    Returns
    -------
    dict[str, str]
        Mapping of agent name → priority string.
    """
    cfg_dir = config_dir or _CONFIG_DIR
    routing_path = cfg_dir / "model_routing.yaml"

    if not routing_path.exists():
        log.warning(
            "model_routing.yaml not found at %s; using defaults", cfg_dir,
        )
        return {}

    with routing_path.open() as f:
        data = yaml.safe_load(f) or {}

    priorities = data.get("agent_priorities", {})
    for name, prio in priorities.items():
        _validate_priority(name, str(prio))

    return {name: str(prio) for name, prio in priorities.items()}
