"""Browser history observation plugin for Active Inference engine."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult

logger = logging.getLogger(__name__)


class BrowserHistoryObservationPlugin(ObservationPlugin):
    """Observes browser history for research patterns and topic trends.

    Supports Firefox and Chromium SQLite databases (read-only).
    No network requests, purely local analysis.
    """

    def __init__(
        self,
        browser_profile_path: str,
        browser_type: str = "firefox",
        lookback_hours: int = 24,
        top_n_domains: int = 10,
    ):
        """Initialize browser history observer.

        Args:
            browser_profile_path: Path to browser profile directory
            browser_type: 'firefox' or 'chromium'
            lookback_hours: Analysis window in hours
            top_n_domains: Number of top domains to report
        """
        self._profile_path = Path(browser_profile_path)
        self._browser_type = browser_type.lower()
        self._lookback_hours = lookback_hours
        self._top_n = top_n_domains

        if self._browser_type not in ("firefox", "chromium"):
            raise ValueError("browser_type must be 'firefox' or 'chromium'")

    @property
    def source_id(self) -> str:
        """Unique identifier for browser history observations."""
        return "browser_history"

    async def observe(self) -> ObservationResult:
        """Fetch recent browser history."""
        try:
            total_visits, top_domains, unique_domains = await asyncio.to_thread(
                self._query_browser_history
            )

            return ObservationResult(
                metrics={
                    "total_visits": total_visits,
                    "unique_domains": unique_domains,
                    "research_intensity": min(10.0, total_visits / 10.0),
                },
                raw_data={
                    "top_domains": top_domains,
                },
            )
        except Exception as e:
            logger.warning(f"Browser history observation failed: {e}")
            return ObservationResult(
                metrics={
                    "total_visits": 0,
                    "unique_domains": 0,
                    "research_intensity": 0.0,
                },
                raw_data={"error": str(e)},
            )

    def _query_browser_history(self) -> tuple[int, list[tuple[str, int]], int]:
        """Query browser history database (blocking)."""
        try:
            if self._browser_type == "firefox":
                db_path = self._profile_path / "places.sqlite"
                url_column = "url"
                timestamp_column = "visit_date"
                timestamp_divisor = 1_000_000  # Firefox uses microseconds
            else:  # chromium
                db_path = self._profile_path / "History"
                timestamp_column = "visit_time"
                timestamp_divisor = 1_000_000  # Chromium uses microseconds

            if not db_path.exists():
                logger.warning(f"Browser database not found: {db_path}")
                return 0, [], 0

            cutoff_time = datetime.now(UTC) - timedelta(hours=self._lookback_hours)
            cutoff_microseconds = int(
                cutoff_time.timestamp() * timestamp_divisor
            )

            # Open read-only to avoid locks
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Count total visits in window
            if self._browser_type == "firefox":
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM moz_historyvisits
                    WHERE {timestamp_column} >= ?
                    """,
                    (cutoff_microseconds,),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM visits
                    WHERE {timestamp_column} >= ?
                    """,
                    (cutoff_microseconds,),
                )

            total_visits = cursor.fetchone()[0]

            # Get top domains
            if self._browser_type == "firefox":
                cursor.execute(
                    f"""
                    SELECT moz_places.url, COUNT(*) as visit_count
                    FROM moz_historyvisits
                    JOIN moz_places ON moz_historyvisits.place_id = moz_places.id
                    WHERE moz_historyvisits.{timestamp_column} >= ?
                    GROUP BY moz_places.url
                    ORDER BY visit_count DESC
                    LIMIT ?
                    """,
                    (cutoff_microseconds, self._top_n),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT urls.url, COUNT(*) as visit_count
                    FROM visits
                    JOIN urls ON visits.url = urls.id
                    WHERE visits.{timestamp_column} >= ?
                    GROUP BY urls.url
                    ORDER BY visit_count DESC
                    LIMIT ?
                    """,
                    (cutoff_microseconds, self._top_n),
                )

            results = cursor.fetchall()
            top_domains = [
                (self._extract_domain(url), count) for url, count in results
            ]

            unique_domains = len(set(domain for domain, _ in top_domains))

            conn.close()
            return total_visits, top_domains, unique_domains

        except Exception as e:
            logger.error(f"Browser history query failed: {e}")
            return 0, [], 0

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.netloc or url

    def default_predictions(self) -> dict[str, dict[str, float]]:
        """Initial predictions for browser history metrics."""
        return {
            "total_visits": {"expected": 50.0, "tolerance": 100.0},
            "unique_domains": {"expected": 10.0, "tolerance": 20.0},
            "research_intensity": {"expected": 5.0, "tolerance": 5.0},
        }

    @property
    def poll_interval_seconds(self) -> int:
        """Poll browser history every hour."""
        return 3600
