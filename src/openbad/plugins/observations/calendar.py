"""Calendar observation plugin for Active Inference engine."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult

logger = logging.getLogger(__name__)


class CalendarObservationPlugin(ObservationPlugin):
    """Observes upcoming calendar events.

    Supports ICS files (local or remote via HTTP).
    Detects conflicts, upcoming deadlines, and schedule gaps.
    """

    def __init__(
        self,
        calendar_source: str,
        lookahead_hours: int = 48,
    ):
        """Initialize calendar observer.

        Args:
            calendar_source: Path to .ics file or HTTP URL
            lookahead_hours: Hours ahead to scan for events
        """
        self._source = calendar_source
        self._lookahead_hours = lookahead_hours

    @property
    def source_id(self) -> str:
        """Unique identifier for calendar observations."""
        return "calendar"

    async def observe(self) -> ObservationResult:
        """Fetch upcoming calendar events."""
        try:
            events = await asyncio.to_thread(self._parse_ics_events)
            now = datetime.now(UTC)
            cutoff = now + timedelta(hours=self._lookahead_hours)

            upcoming = [e for e in events if now <= e["start"] <= cutoff]
            conflicts = self._detect_conflicts(upcoming)

            return ObservationResult(
                metrics={
                    "upcoming_count": len(upcoming),
                    "conflicts_count": len(conflicts),
                    "hours_until_next": self._hours_until_next_event(upcoming),
                },
                raw_data={
                    "events": [
                        {"summary": e["summary"], "start": e["start"].isoformat()}
                        for e in upcoming[:10]
                    ],
                    "conflicts": conflicts,
                },
            )
        except Exception as e:
            logger.warning(f"Calendar observation failed: {e}")
            return ObservationResult(
                metrics={
                    "upcoming_count": 0,
                    "conflicts_count": 0,
                    "hours_until_next": 999.0,
                },
                raw_data={"error": str(e)},
            )

    def _parse_ics_events(self) -> list[dict[str, Any]]:
        """Parse ICS file and extract events."""
        try:
            # Try to import icalendar (optional dependency)
            import icalendar  # type: ignore

            if self._source.startswith("http"):
                import urllib.request

                with urllib.request.urlopen(self._source) as response:
                    ics_data = response.read()
            else:
                ics_data = Path(self._source).read_bytes()

            cal = icalendar.Calendar.from_ical(ics_data)
            events = []

            for component in cal.walk():
                if component.name == "VEVENT":
                    start = component.get("DTSTART").dt
                    if isinstance(start, datetime):
                        if start.tzinfo is None:
                            start = start.replace(tzinfo=UTC)
                    else:
                        # Date only, assume midnight UTC
                        start = datetime.combine(start, datetime.min.time()).replace(
                            tzinfo=UTC
                        )

                    events.append(
                        {
                            "summary": str(component.get("SUMMARY", "(no title)")),
                            "start": start,
                            "end": component.get("DTEND", start).dt,
                        }
                    )

            return events

        except ImportError:
            logger.warning("icalendar library not installed, calendar plugin disabled")
            return []
        except Exception as e:
            logger.error(f"ICS parsing failed: {e}")
            return []

    @staticmethod
    def _detect_conflicts(events: list[dict[str, Any]]) -> list[str]:
        """Detect overlapping events."""
        conflicts = []
        for i, event1 in enumerate(events):
            for event2 in events[i + 1 :]:
                if event1["start"] < event2["end"] and event2["start"] < event1["end"]:
                    conflicts.append(
                        f"{event1['summary']} overlaps {event2['summary']}"
                    )
        return conflicts

    @staticmethod
    def _hours_until_next_event(events: list[dict[str, Any]]) -> float:
        """Calculate hours until the next event."""
        if not events:
            return 999.0
        now = datetime.now(UTC)
        next_event = min(events, key=lambda e: e["start"])
        delta = (next_event["start"] - now).total_seconds() / 3600
        return max(0.0, delta)

    def default_predictions(self) -> dict[str, dict[str, float]]:
        """Initial predictions for calendar metrics."""
        return {
            "upcoming_count": {"expected": 3.0, "tolerance": 5.0},
            "conflicts_count": {"expected": 0.0, "tolerance": 1.0},
            "hours_until_next": {"expected": 4.0, "tolerance": 24.0},
        }

    @property
    def poll_interval_seconds(self) -> int:
        """Poll calendar every 15 minutes."""
        return 900
