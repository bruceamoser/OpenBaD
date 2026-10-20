"""Tests for calendar observation plugin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from openbad.plugins.observations.calendar import CalendarObservationPlugin


@pytest.fixture
def calendar_plugin(tmp_path):
    """Calendar plugin with test ICS file."""
    ics_file = tmp_path / "test.ics"
    ics_file.write_text(
        """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Test Event
DTSTART:20260420T100000Z
DTEND:20260420T110000Z
END:VEVENT
END:VCALENDAR"""
    )
    return CalendarObservationPlugin(calendar_source=str(ics_file))


def test_source_id(calendar_plugin):
    """Test calendar plugin source ID."""
    assert calendar_plugin.source_id == "calendar"


def test_default_predictions(calendar_plugin):
    """Test calendar plugin default predictions."""
    preds = calendar_plugin.default_predictions()
    assert "upcoming_count" in preds
    assert "conflicts_count" in preds
    assert "hours_until_next" in preds


def test_poll_interval(calendar_plugin):
    """Test calendar poll interval."""
    assert calendar_plugin.poll_interval_seconds == 900


def test_detect_conflicts():
    """Test conflict detection."""
    now = datetime.now(UTC)
    event1 = {"summary": "Event A", "start": now, "end": now + timedelta(hours=1)}
    event2 = {
        "summary": "Event B",
        "start": now + timedelta(minutes=30),
        "end": now + timedelta(hours=1, minutes=30),
    }
    event3 = {
        "summary": "Event C",
        "start": now + timedelta(hours=2),
        "end": now + timedelta(hours=3),
    }

    conflicts = CalendarObservationPlugin._detect_conflicts([event1, event2, event3])
    assert len(conflicts) == 1
    assert "Event A" in conflicts[0] and "Event B" in conflicts[0]


def test_hours_until_next_event():
    """Test hours until next event calculation."""
    now = datetime.now(UTC)
    event1 = {"summary": "Soon", "start": now + timedelta(hours=2), "end": now}
    event2 = {"summary": "Later", "start": now + timedelta(hours=5), "end": now}

    hours = CalendarObservationPlugin._hours_until_next_event([event1, event2])
    assert 1.5 < hours < 2.5


@pytest.mark.asyncio
async def test_observe_no_icalendar():
    """Test calendar observation without icalendar library."""
    plugin = CalendarObservationPlugin(calendar_source="/nonexistent/path")

    result = await plugin.observe()

    assert result.metrics["upcoming_count"] == 0
    assert result.metrics["conflicts_count"] == 0
