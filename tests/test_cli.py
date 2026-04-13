"""Tests for CLI entrypoint and subcommand dispatch."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from openbad.cli import (
    _normalized_endocrine_deltas,
    _should_publish_endocrine_event,
    main,
)


class TestMainGroup:
    """Top-level CLI group behaviour."""

    def test_no_subcommand_shows_help(self):
        result = CliRunner().invoke(main)
        assert result.exit_code == 0
        assert "OpenBaD" in result.output

    def test_help_flag(self):
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "start" in result.output
        assert "stop" in result.output
        assert "restart" in result.output
        assert "health" in result.output
        assert "version" in result.output
        assert "setup" in result.output
        assert "tui" in result.output
        assert "  run " not in result.output
        assert "  status " not in result.output
        assert "  wui " not in result.output


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


class TestHealthCommand:
    """``openbad health`` operator surface."""

    def test_health_help(self):
        result = CliRunner().invoke(main, ["health", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--wui-url" in result.output


class TestServiceControlCommands:
    def test_start_invokes_systemctl_start(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["start"])

        assert result.exit_code == 0
        assert run.call_args.args[0][0].endswith("systemctl")
        assert run.call_args.args[0][1] == "start"

    def test_stop_invokes_systemctl_stop(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["stop"])

        assert result.exit_code == 0
        assert run.call_args.args[0][0].endswith("systemctl")
        assert run.call_args.args[0][1] == "stop"

    def test_restart_invokes_systemctl_restart(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["restart"])

        assert result.exit_code == 0
        assert run.call_args.args[0][0].endswith("systemctl")
        assert run.call_args.args[0][1] == "restart"

    def test_restart_skips_disabled_broker_unit(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="disabled\n", stderr="", returncode=1),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["restart"])

        assert result.exit_code == 0
        assert run.call_args.args[0][-2:] == ["openbad.service", "openbad-wui.service"]

    def test_start_skips_missing_broker_unit(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="not-found\n", stderr="", returncode=1),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["start"])

        assert result.exit_code == 0
        assert run.call_args.args[0][-2:] == ["openbad.service", "openbad-wui.service"]

    def test_service_command_reports_systemctl_failure(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = FileNotFoundError()
            result = CliRunner().invoke(main, ["start"])

        assert result.exit_code == 1
        assert "systemctl not found" in result.output

    def test_service_command_reports_sudo_requirement(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["/bin/systemctl", "start", "openbad.service"],
                    stderr="Interactive authentication required.",
                ),
            ]
            result = CliRunner().invoke(main, ["start"])

        assert result.exit_code == 1
        assert "sudo openbad start" in result.output


class TestHealthStatusCommand:
    def test_health_without_services_or_broker(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.side_effect = [
                MagicMock(stdout="inactive\n", stderr="", returncode=3),
                MagicMock(stdout="inactive\n", stderr="", returncode=3),
                MagicMock(stdout="inactive\n", stderr="", returncode=3),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
            ]
            result = CliRunner().invoke(main, ["health", "--port", "19999"])

        assert result.exit_code == 1
        assert '"mqtt_reachable": false' in result.output
        assert '"openbad.service": "inactive"' in result.output

    def test_health_succeeds_with_disabled_broker_unit_and_external_broker(self):
        with patch("openbad.cli.subprocess.run") as run, patch(
            "openbad.cli.urllib_request.urlopen"
        ) as urlopen, patch("openbad.nervous_system.client.NervousSystemClient") as client_cls:
            run.side_effect = [
                MagicMock(stdout="inactive\n", stderr="", returncode=3),
                MagicMock(stdout="active\n", stderr="", returncode=0),
                MagicMock(stdout="active\n", stderr="", returncode=0),
                MagicMock(stdout="disabled\n", stderr="", returncode=1),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
                MagicMock(stdout="enabled\n", stderr="", returncode=0),
            ]
            client = MagicMock()
            client_cls.return_value = client
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = b'{"ok": true}'
            response.status = 200
            urlopen.return_value = response

            result = CliRunner().invoke(main, ["health"])

        assert result.exit_code == 0
        assert '"openbad-broker.service": "inactive"' in result.output
        assert '"openbad-broker.service": "disabled"' in result.output

    def test_status_alias_invokes_health(self):
        with patch("openbad.cli.subprocess.run") as run:
            run.return_value = MagicMock(stdout="inactive\n", stderr="", returncode=3)
            result = CliRunner().invoke(main, ["status", "--port", "19999"])

        assert result.exit_code == 1
        assert '"services"' in result.output


class TestInternalCommands:
    """Internal foreground commands remain invokable for systemd/debugging."""

    def test_run_help(self):
        result = CliRunner().invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--verbose" in result.output

    def test_wui_help(self):
        result = CliRunner().invoke(main, ["wui", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--mqtt-host" in result.output
        assert "--mqtt-port" in result.output


class TestEndocrineHelpers:
    def test_normalized_endocrine_deltas_filters_unknown_and_zero(self):
        normalized = _normalized_endocrine_deltas(
            {
                "cortisol": 0.12,
                "adrenaline": 0.0,
                "unknown": 1.0,
                "dopamine": -0.03,
            }
        )

        assert normalized == {"cortisol": 0.12, "dopamine": -0.03}

    def test_should_publish_endocrine_event_false_for_no_change(self):
        assert not _should_publish_endocrine_event(
            {"cortisol": 0.6},
            {"cortisol": 0.6},
            "cortisol",
        )

    def test_should_publish_endocrine_event_true_for_change(self):
        assert _should_publish_endocrine_event(
            {"cortisol": 0.6},
            {"cortisol": 0.62},
            "cortisol",
        )
