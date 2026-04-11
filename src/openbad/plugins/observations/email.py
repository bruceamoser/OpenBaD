"""Email observation plugin for Active Inference engine."""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import logging

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult

logger = logging.getLogger(__name__)


class EmailObservationPlugin(ObservationPlugin):
    """Observes unread email via IMAP.

    Publishes email summaries (count, senders, subjects) as observations.
    Uses lightweight provider routing for summarization (not heavy reasoning).
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        folder: str = "INBOX",
        max_recent: int = 10,
    ):
        """Initialize email observer.

        Args:
            server: IMAP server hostname (e.g., 'imap.gmail.com')
            username: Email account username
            password: Email account password (use secure storage in production)
            folder: IMAP folder to monitor (default: INBOX)
            max_recent: Maximum recent emails to analyze
        """
        self._server = server
        self._username = username
        self._password = password
        self._folder = folder
        self._max_recent = max_recent

    @property
    def source_id(self) -> str:
        """Unique identifier for email observations."""
        return "email"

    async def observe(self) -> ObservationResult:
        """Fetch unread email summaries."""
        try:
            unread_count, recent_subjects, recent_senders = await asyncio.to_thread(
                self._fetch_email_data
            )

            return ObservationResult(
                metrics={
                    "unread_count": unread_count,
                    "recent_count": len(recent_subjects),
                    "urgency_count": sum(
                        1 for s in recent_subjects if self._is_urgent(s)
                    ),
                },
                raw_data={
                    "subjects": recent_subjects,
                    "senders": recent_senders,
                },
            )
        except Exception as e:
            logger.warning(f"Email observation failed: {e}")
            return ObservationResult(
                metrics={
                    "unread_count": 0,
                    "recent_count": 0,
                    "urgency_count": 0,
                },
                raw_data={"error": str(e)},
            )

    def _fetch_email_data(self) -> tuple[int, list[str], list[str]]:
        """Fetch email data via IMAP (blocking call)."""
        mail = imaplib.IMAP4_SSL(self._server)
        mail.login(self._username, self._password)
        mail.select(self._folder, readonly=True)

        # Search for unread emails
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            return 0, [], []

        email_ids = messages[0].split()
        unread_count = len(email_ids)

        # Fetch recent email summaries
        recent_subjects = []
        recent_senders = []
        for email_id in email_ids[-self._max_recent :]:
            status, msg_data = mail.fetch(email_id, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(
                msg_data[0][1], policy=email.policy.default
            )
            recent_subjects.append(msg.get("Subject", "(no subject)"))
            recent_senders.append(msg.get("From", "(unknown)"))

        mail.logout()
        return unread_count, recent_subjects, recent_senders

    @staticmethod
    def _is_urgent(subject: str) -> bool:
        """Detect urgency markers in email subjects."""
        urgent_keywords = ["urgent", "asap", "deadline", "critical", "important"]
        return any(keyword in subject.lower() for keyword in urgent_keywords)

    def default_predictions(self) -> dict[str, dict[str, float]]:
        """Initial predictions for email metrics."""
        return {
            "unread_count": {"expected": 5.0, "tolerance": 10.0},
            "recent_count": {"expected": 3.0, "tolerance": 5.0},
            "urgency_count": {"expected": 0.0, "tolerance": 2.0},
        }

    @property
    def poll_interval_seconds(self) -> int:
        """Poll email every 5 minutes."""
        return 300
