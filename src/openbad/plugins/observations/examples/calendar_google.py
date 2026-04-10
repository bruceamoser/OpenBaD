"""Google Calendar observation plugin template.

Setup Instructions
==================
1. Create a Google Cloud project and enable the Calendar API.
2. Create OAuth 2.0 credentials (Desktop app type).
3. Download ``credentials.json`` and place it in a secure location.
4. Set the ``GCAL_CREDENTIALS_PATH`` environment variable.

Required Permissions
====================
- ``https://www.googleapis.com/auth/calendar.readonly``

Expected Metrics
================
- ``upcoming_events_1h``: Number of events starting in the next hour.
- ``upcoming_events_24h``: Number of events starting in the next 24 hours.
- ``free_blocks_today``: Number of free 30-min blocks remaining today.
"""

from __future__ import annotations

from datetime import UTC, datetime

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class GoogleCalendarPlugin(ObservationPlugin):
    """Observe Google Calendar schedule density.

    This is a **template** — real API calls are stubbed.  Replace the body
    of :pymethod:`observe` with actual ``google-api-python-client`` calls.
    """

    @property
    def source_id(self) -> str:
        return "calendar_google"

    @property
    def poll_interval_seconds(self) -> int:
        return 600  # 10 minutes

    async def observe(self) -> ObservationResult:
        # TODO: Replace with real Calendar API call.
        # Example:
        #   service = build("calendar", "v3", credentials=creds)
        #   events = service.events().list(calendarId="primary", ...).execute()
        return ObservationResult(
            metrics={
                "upcoming_events_1h": 0,
                "upcoming_events_24h": 0,
                "free_blocks_today": 16,
            },
            timestamp=datetime.now(UTC),
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "upcoming_events_1h": {"expected": 0.5, "tolerance": 2.0},
            "upcoming_events_24h": {"expected": 4.0, "tolerance": 5.0},
            "free_blocks_today": {"expected": 10.0, "tolerance": 6.0},
        }
