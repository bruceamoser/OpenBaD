"""Tests for the Corsair peripherals configuration loader."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from openbad.peripherals.config import (
    CorsairConfig,
    PluginConfig,
    enabled_plugin_names,
    load_peripherals_config,
    resolve_config_write_path,
    resolve_credentials_dir,
    resolve_credentials_path,
)

# ── Fixtures ─────────────────────────────────────────────────────── #


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with a peripherals.yaml."""
    cfg = tmp_path / "peripherals.yaml"
    cfg.write_text(
        textwrap.dedent("""\
        corsair:
          entry_point: /opt/openbad/peripherals/corsair/dist/corsair.js
          webhook_secret: test-secret-123
          plugins:
            - name: discord
              enabled: true
              credentials_file: discord.json
            - name: slack
              enabled: false
              credentials_file: slack.json
            - name: gmail
              enabled: true
              credentials_file: gmail.json
        """),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def creds_dir(tmp_path: Path) -> Path:
    """Return a temp directory with sample credential files."""
    d = tmp_path / "creds"
    d.mkdir()
    discord_creds = d / "discord.json"
    discord_creds.write_text(
        json.dumps({"token": "fake-bot-token"}), encoding="utf-8",
    )
    discord_creds.chmod(0o600)

    slack_creds = d / "slack.json"
    slack_creds.write_text(
        json.dumps({"token": "fake-slack-token"}), encoding="utf-8",
    )
    slack_creds.chmod(0o644)  # intentionally too open
    return d


# ── load_peripherals_config ──────────────────────────────────────── #


class TestLoadConfig:
    """Tests for load_peripherals_config()."""

    def test_loads_from_explicit_path(self, config_dir: Path) -> None:
        cfg = load_peripherals_config(config_dir / "peripherals.yaml")
        assert cfg.entry_point == "/opt/openbad/peripherals/corsair/dist/corsair.js"
        assert cfg.webhook_secret == "test-secret-123"  # noqa: S105
        assert len(cfg.plugins) == 3

    def test_parses_plugin_entries(self, config_dir: Path) -> None:
        cfg = load_peripherals_config(config_dir / "peripherals.yaml")
        names = [p.name for p in cfg.plugins]
        assert names == ["discord", "slack", "gmail"]

    def test_plugin_enabled_flags(self, config_dir: Path) -> None:
        cfg = load_peripherals_config(config_dir / "peripherals.yaml")
        by_name = {p.name: p for p in cfg.plugins}
        assert by_name["discord"].enabled is True
        assert by_name["slack"].enabled is False
        assert by_name["gmail"].enabled is True

    def test_plugin_credentials_files(self, config_dir: Path) -> None:
        cfg = load_peripherals_config(config_dir / "peripherals.yaml")
        by_name = {p.name: p for p in cfg.plugins}
        assert by_name["discord"].credentials_file == "discord.json"

    def test_returns_default_when_no_file(self, tmp_path: Path) -> None:
        cfg = load_peripherals_config(tmp_path / "missing.yaml")
        assert cfg == CorsairConfig()
        assert cfg.plugins == []

    def test_returns_default_for_empty_yaml(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        cfg = load_peripherals_config(empty)
        assert cfg == CorsairConfig()

    def test_returns_default_for_no_corsair_key(self, tmp_path: Path) -> None:
        nope = tmp_path / "no_corsair.yaml"
        nope.write_text("other_stuff:\n  key: value\n", encoding="utf-8")
        cfg = load_peripherals_config(nope)
        assert cfg == CorsairConfig()

    def test_empty_plugins_list(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "peripherals.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
            corsair:
              entry_point: /opt/openbad/peripherals/corsair/dist/corsair.js
              plugins: []
            """),
            encoding="utf-8",
        )
        cfg = load_peripherals_config(cfg_file)
        assert cfg.plugins == []

    def test_skips_malformed_plugin_entries(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "peripherals.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
            corsair:
              plugins:
                - name: good
                  enabled: true
                - not_a_dict
                - enabled: true
            """),
            encoding="utf-8",
        )
        cfg = load_peripherals_config(cfg_file)
        # Only the entry with "name" is kept.
        assert len(cfg.plugins) == 1
        assert cfg.plugins[0].name == "good"


# ── resolve_credentials_path ─────────────────────────────────────── #


class TestResolveCredentials:
    """Tests for resolve_credentials_path()."""

    def test_returns_path_for_existing_file(self, creds_dir: Path) -> None:
        plugin = PluginConfig(name="discord", credentials_file="discord.json")
        result = resolve_credentials_path(plugin, creds_dir)
        assert result is not None
        assert result == creds_dir / "discord.json"

    def test_returns_none_for_missing_file(self, creds_dir: Path) -> None:
        plugin = PluginConfig(name="telegram", credentials_file="telegram.json")
        result = resolve_credentials_path(plugin, creds_dir)
        assert result is None

    def test_returns_none_for_empty_credentials_file(
        self, creds_dir: Path,
    ) -> None:
        plugin = PluginConfig(name="discord", credentials_file="")
        result = resolve_credentials_path(plugin, creds_dir)
        assert result is None

    def test_warns_on_open_permissions(
        self, creds_dir: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = PluginConfig(name="slack", credentials_file="slack.json")
        with caplog.at_level("WARNING"):
            result = resolve_credentials_path(plugin, creds_dir)
        assert result is not None
        assert "0644" in caplog.text or "mode" in caplog.text.lower()

    def test_no_warning_on_correct_permissions(
        self, creds_dir: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = PluginConfig(name="discord", credentials_file="discord.json")
        with caplog.at_level("WARNING"):
            resolve_credentials_path(plugin, creds_dir)
        assert "mode" not in caplog.text.lower()


# ── enabled_plugin_names ─────────────────────────────────────────── #


class TestEnabledPluginNames:
    """Tests for enabled_plugin_names()."""

    def test_returns_only_enabled(self, config_dir: Path) -> None:
        cfg = load_peripherals_config(config_dir / "peripherals.yaml")
        names = enabled_plugin_names(cfg)
        assert names == ["discord", "gmail"]

    def test_empty_when_none_enabled(self) -> None:
        cfg = CorsairConfig(
            plugins=[
                PluginConfig(name="a", enabled=False),
                PluginConfig(name="b", enabled=False),
            ],
        )
        assert enabled_plugin_names(cfg) == []

    def test_empty_for_default_config(self) -> None:
        assert enabled_plugin_names(CorsairConfig()) == []


# ── resolve_credentials_dir tests ─────────────────────────────────── #


class TestResolveCredentialsDir:

    def test_returns_existing_dir(self, tmp_path: Path) -> None:
        with patch(
            "openbad.peripherals.config._CREDENTIALS_DIR_CANDIDATES",
            [tmp_path / "prod", tmp_path / "dev"],
        ):
            prod = tmp_path / "prod"
            prod.mkdir()
            result = resolve_credentials_dir()
            assert result == prod

    def test_creates_dir_if_parent_exists(self, tmp_path: Path) -> None:
        with patch(
            "openbad.peripherals.config._CREDENTIALS_DIR_CANDIDATES",
            [tmp_path / "prod" / "creds", tmp_path / "dev"],
        ):
            (tmp_path / "prod").mkdir()
            result = resolve_credentials_dir()
            assert result == tmp_path / "prod" / "creds"
            assert result.is_dir()

    def test_falls_back_to_last_candidate(self, tmp_path: Path) -> None:
        with patch(
            "openbad.peripherals.config._CREDENTIALS_DIR_CANDIDATES",
            [tmp_path / "nonexistent" / "prod", tmp_path / "dev"],
        ):
            result = resolve_credentials_dir()
            assert result == tmp_path / "dev"
            assert result.is_dir()


# ── resolve_config_write_path tests ───────────────────────────────── #


class TestResolveConfigWritePath:

    def test_returns_existing_writable_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "peripherals.yaml"
        cfg.write_text("corsair: {}")
        with patch(
            "openbad.peripherals.config._DEFAULT_SEARCH_PATHS",
            [cfg, tmp_path / "fallback.yaml"],
        ):
            result = resolve_config_write_path()
            assert result == cfg

    def test_falls_back_when_not_writable(self, tmp_path: Path) -> None:
        read_only = tmp_path / "ro" / "peripherals.yaml"
        fallback = tmp_path / "fb" / "peripherals.yaml"
        (tmp_path / "fb").mkdir()
        with patch(
            "openbad.peripherals.config._DEFAULT_SEARCH_PATHS",
            [read_only, fallback],
        ):
            result = resolve_config_write_path()
            assert result == fallback
