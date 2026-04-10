"""Tests for CLI entrypoint and subcommand dispatch."""

from __future__ import annotations

from click.testing import CliRunner

from openbad.cli import main


class TestMainGroup:
    """Top-level CLI group behaviour."""

    def test_no_subcommand_shows_help(self):
        result = CliRunner().invoke(main)
        assert result.exit_code == 0
        assert "OpenBaD" in result.output

    def test_help_flag(self):
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "status" in result.output
        assert "version" in result.output
        assert "setup" in result.output
        assert "tui" in result.output
        assert "wui" in result.output


class TestVersionCommand:
    """``openbad version``."""

    def test_prints_version(self):
        result = CliRunner().invoke(main, ["version"])
        assert result.exit_code == 0
        assert "openbad" in result.output
        assert "0.1.0" in result.output


class TestSetupCommand:
    """``openbad setup`` command surface."""

    def test_setup_help(self):
        result = CliRunner().invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "--config-dir" in result.output
        assert "--check" in result.output
        assert "--non-interactive" in result.output


class TestTuiCommand:
    """``openbad tui`` command surface."""

    def test_tui_help(self):
        result = CliRunner().invoke(main, ["tui", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output


class TestWuiCommand:
    """``openbad wui`` command surface."""

    def test_wui_help(self):
        result = CliRunner().invoke(main, ["wui", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--mqtt-host" in result.output
        assert "--mqtt-port" in result.output


class TestStatusCommand:
    """``openbad status`` — MQTT reachability check."""

    def test_status_no_broker(self):
        """Without a running broker, exit code should be 1."""
        result = CliRunner().invoke(main, ["status", "--port", "19999"])
        assert result.exit_code == 1
        assert '"mqtt_reachable": false' in result.output


class TestRunCommand:
    """``openbad run`` — daemon lifecycle."""

    def test_run_help(self):
        result = CliRunner().invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--verbose" in result.output
