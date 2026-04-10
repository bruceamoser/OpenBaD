"""Gmail observation plugin template.

Setup Instructions
==================
1. Create a Google Cloud project and enable the Gmail API.
2. Create OAuth 2.0 credentials (Desktop app type).
3. Download ``credentials.json`` and place it in a secure location.
4. Set the ``GMAIL_CREDENTIALS_PATH`` environment variable.
5. On first run the plugin will open a browser for OAuth consent.

Required Permissions
====================
- ``https://www.googleapis.com/auth/gmail.readonly``

Expected Metrics
================
- ``unread_count``: Number of unread messages in the inbox.
- ``inbox_total``: Total number of messages in the inbox.
- ``latest_timestamp_hours``: Hours since the most recent message.
"""

from __future__ import annotations

from datetime import UTC, datetime

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class GmailObservationPlugin(ObservationPlugin):
    """Observe Gmail inbox state via the Gmail API.

    This is a **template** — real API calls are stubbed.  Replace the body
    of :pymethod:`observe` with actual ``google-api-python-client`` calls.
    """

    @property
    def source_id(self) -> str:
        return "email_gmail"

    @property
    def poll_interval_seconds(self) -> int:
        return 300  # 5 minutes

    async def observe(self) -> ObservationResult:
        # TODO: Replace with real Gmail API call.
        # Example:
        #   service = build("gmail", "v1", credentials=creds)
        #   results = service.users().messages().list(userId="me", q="is:unread").execute()
        return ObservationResult(
            metrics={
                "unread_count": 0,
                "inbox_total": 0,
                "latest_timestamp_hours": 0.0,
            },
            timestamp=datetime.now(UTC),
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "unread_count": {"expected": 5.0, "tolerance": 10.0},
            "inbox_total": {"expected": 100.0, "tolerance": 50.0},
            "latest_timestamp_hours": {"expected": 1.0, "tolerance": 4.0},
        }
