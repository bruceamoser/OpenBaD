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


class TestVersionCommand:
    """``openbad version``."""

    def test_prints_version(self):
        result = CliRunner().invoke(main, ["version"])
        assert result.exit_code == 0
        assert "openbad" in result.output
        assert "0.1.0" in result.output


class TestSetupPlaceholder:
    """``openbad setup`` placeholder."""

    def test_setup_placeholder(self):
        result = CliRunner().invoke(main, ["setup"])
        assert result.exit_code == 0
        assert "Setup wizard" in result.output


class TestTuiPlaceholder:
    """``openbad tui`` placeholder."""

    def test_tui_placeholder(self):
        result = CliRunner().invoke(main, ["tui"])
        assert result.exit_code == 0
        assert "TUI" in result.output


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
