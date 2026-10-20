"""Configuration loader for the Corsair peripheral transducer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default config search paths (production → repo fallback).
_DEFAULT_SEARCH_PATHS: list[Path] = [
    Path("/etc/openbad/peripherals.yaml"),
    Path("config/peripherals.yaml"),
]

# Credentials dir search order (production → repo fallback).
_CREDENTIALS_DIR_CANDIDATES: list[Path] = [
    Path("/var/lib/openbad/data/config/peripherals"),
    Path("data/config/peripherals"),
]


@dataclass(frozen=True)
class PluginConfig:
    """A single Corsair plugin entry."""

    name: str
    enabled: bool = False
    credentials_file: str = ""


@dataclass(frozen=True)
class CorsairConfig:
    """Top-level Corsair sidecar configuration."""

    entry_point: str = ""
    webhook_secret: str = ""
    plugins: list[PluginConfig] = field(default_factory=list)


def resolve_credentials_dir() -> Path:
    """Return the credentials directory, preferring the production path.

    Creates the directory if it doesn't exist at the selected location.
    Falls back to ``data/config/peripherals`` (relative to CWD) for dev.
    """
    for candidate in _CREDENTIALS_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
        # If the parent exists and is writable, use this candidate
        if candidate.parent.exists():
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                continue
    # Final fallback: relative path
    fallback = _CREDENTIALS_DIR_CANDIDATES[-1]
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def resolve_config_write_path() -> Path:
    """Return the path to write ``peripherals.yaml``.

    Prefers ``/etc/openbad/peripherals.yaml`` when writable, otherwise
    falls back to the repo-local ``config/peripherals.yaml``.
    """
    for candidate in _DEFAULT_SEARCH_PATHS:
        if candidate.exists():
            try:
                # Check if writable
                candidate.open("a").close()
                return candidate
            except OSError:
                continue
        elif candidate.parent.is_dir():
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                continue
    # Fallback
    fallback = _DEFAULT_SEARCH_PATHS[-1]
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def _resolve_config_path(
    explicit: Path | None = None,
    search_paths: list[Path] | None = None,
) -> Path | None:
    """Return the first existing config file from *search_paths*."""
    if explicit is not None:
        return explicit if explicit.is_file() else None
    for candidate in search_paths or _DEFAULT_SEARCH_PATHS:
        if candidate.is_file():
            return candidate
    return None


def load_peripherals_config(
    path: Path | None = None,
) -> CorsairConfig:
    """Load and validate the peripherals YAML config.

    Parameters
    ----------
    path:
        Explicit path to the YAML file.  When *None* the standard
        search order is used (``/etc/openbad`` → ``config/``).

    Returns
    -------
    CorsairConfig
        Parsed, validated configuration.  Returns a default (empty) config
        when no file is found.
    """
    resolved = _resolve_config_path(path)
    if resolved is None:
        return CorsairConfig()

    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    corsair_raw = raw.get("corsair", {})
    if not isinstance(corsair_raw, dict):
        return CorsairConfig()

    plugins: list[PluginConfig] = []
    for entry in corsair_raw.get("plugins", []) or []:
        if isinstance(entry, dict) and "name" in entry:
            plugins.append(
                PluginConfig(
                    name=entry["name"],
                    enabled=bool(entry.get("enabled", False)),
                    credentials_file=str(entry.get("credentials_file", "")),
                )
            )

    return CorsairConfig(
        entry_point=str(corsair_raw.get("entry_point", "")),
        webhook_secret=str(corsair_raw.get("webhook_secret", "")),
        plugins=plugins,
    )


def resolve_credentials_path(
    plugin: PluginConfig,
    credentials_dir: Path | None = None,
) -> Path | None:
    """Return the absolute path to a plugin's credentials file.

    Returns *None* when:
    - ``plugin.credentials_file`` is empty, or
    - the resolved file does not exist.
    """
    if not plugin.credentials_file:
        return None

    base = credentials_dir or resolve_credentials_dir()
    candidate = base / plugin.credentials_file

    if not candidate.is_file():
        return None

    # Warn (but don't block) if permissions are too open.
    try:
        mode = candidate.stat().st_mode & 0o777
        if mode & 0o077:
            import logging

            logging.getLogger(__name__).warning(
                "Credentials file %s has mode %04o — expected 0600.",
                candidate,
                mode,
            )
    except OSError:
        pass

    return candidate


def enabled_plugin_names(cfg: CorsairConfig) -> list[str]:
    """Return the names of all enabled plugins."""
    return [p.name for p in cfg.plugins if p.enabled]
