"""Tests for syslog observation plugin."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openbad.plugins.observations.syslog import SyslogObservationPlugin


@pytest.fixture
def syslog_plugin():
    """Syslog plugin with test configuration."""
    return SyslogObservationPlugin(lookback_minutes=15, severity_threshold="warning")


def test_source_id(syslog_plugin):
    """Test syslog plugin source ID."""
    assert syslog_plugin.source_id == "syslog"


def test_default_predictions(syslog_plugin):
    """Test syslog plugin default predictions."""
    preds = syslog_plugin.default_predictions()
    assert "total_entries" in preds
    assert "error_count" in preds
    assert "critical_count" in preds
    assert "affected_services" in preds


def test_poll_interval(syslog_plugin):
    """Test syslog poll interval."""
    assert syslog_plugin.poll_interval_seconds == 300


def test_get_priority():
    """Test priority extraction from journal entry."""
    entry_info = {"PRIORITY": "6"}
    assert SyslogObservationPlugin._get_priority(entry_info) == 6

    entry_err = {"PRIORITY": "3"}
    assert SyslogObservationPlugin._get_priority(entry_err) == 3

    entry_invalid = {"PRIORITY": "invalid"}
    assert SyslogObservationPlugin._get_priority(entry_invalid) == 6


@pytest.mark.asyncio
async def test_observe_journalctl_not_found(syslog_plugin):
    """Test syslog observation when journalctl is unavailable."""
    with patch(
        "subprocess.run", side_effect=FileNotFoundError("journalctl not found")
    ):
        result = await syslog_plugin.observe()

    assert result.metrics["total_entries"] == 0
    assert result.metrics["error_count"] == 0


@pytest.mark.asyncio
async def test_observe_success():
    """Test successful syslog observation."""
    plugin = SyslogObservationPlugin()

    # Mock journalctl output
    mock_output = """{"PRIORITY":"4","MESSAGE":"Warning message","_SYSTEMD_UNIT":"test.service"}
{"PRIORITY":"3","MESSAGE":"Error message","_SYSTEMD_UNIT":"fail.service"}
{"PRIORITY":"2","MESSAGE":"Critical error","_SYSTEMD_UNIT":"fail.service"}"""

    from subprocess import CompletedProcess

    mock_result = CompletedProcess(
        args=[], returncode=0, stdout=mock_output, stderr=""
    )

    with patch("subprocess.run", return_value=mock_result):
        result = await plugin.observe()

    assert result.metrics["total_entries"] == 3
    assert result.metrics["error_count"] == 2
    assert result.metrics["critical_count"] == 1
    assert result.metrics["affected_services"] == 1
    assert "fail.service" in result.raw_data["services_with_errors"]
