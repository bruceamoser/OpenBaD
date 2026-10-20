"""Tests for email observation plugin."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openbad.plugins.observations.email import EmailObservationPlugin


@pytest.fixture
def email_plugin():
    """Email plugin with test credentials."""
    return EmailObservationPlugin(
        server="imap.example.com",
        username="test@example.com",
        password="testpass",
    )


def test_source_id(email_plugin):
    """Test email plugin source ID."""
    assert email_plugin.source_id == "email"


def test_default_predictions(email_plugin):
    """Test email plugin default predictions."""
    preds = email_plugin.default_predictions()
    assert "unread_count" in preds
    assert "recent_count" in preds
    assert "urgency_count" in preds


def test_poll_interval(email_plugin):
    """Test email poll interval."""
    assert email_plugin.poll_interval_seconds == 300


def test_is_urgent():
    """Test urgency detection."""
    assert EmailObservationPlugin._is_urgent("URGENT: Action needed")
    assert EmailObservationPlugin._is_urgent("Deadline tomorrow")
    assert not EmailObservationPlugin._is_urgent("Regular email")


@pytest.mark.asyncio
async def test_observe_imap_error(email_plugin):
    """Test email observation handles IMAP errors gracefully."""
    with patch("imaplib.IMAP4_SSL", side_effect=OSError("Connection refused")):
        result = await email_plugin.observe()

    assert result.metrics["unread_count"] == 0
    assert result.metrics["recent_count"] == 0
    assert "error" in result.raw_data
