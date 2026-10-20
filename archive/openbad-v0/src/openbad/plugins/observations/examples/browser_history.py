"""Browser history observation plugin template.

Setup Instructions
==================
1. Close the browser (or copy the history database) to avoid SQLite locks.
2. Set ``BROWSER_HISTORY_PATH`` to the history database location:
   - **Chrome** (Linux): ``~/.config/google-chrome/Default/History``
   - **Chrome** (macOS): ``~/Library/Application Support/Google/Chrome/Default/History``
   - **Firefox**: ``~/.mozilla/firefox/<profile>/places.sqlite``

Required Permissions
====================
- Read access to the browser history SQLite database.

Expected Metrics
================
- ``visits_last_hour``: Number of page visits in the last hour.
- ``unique_domains_last_hour``: Distinct domains visited in the last hour.
- ``total_visits_today``: Total visits since midnight.
"""

from __future__ import annotations

from datetime import UTC, datetime

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class BrowserHistoryPlugin(ObservationPlugin):
    """Observe browsing activity via SQLite history database.

    This is a **template** — real SQLite queries are stubbed.  Replace the
    body of :pymethod:`observe` with actual ``sqlite3`` queries against the
    browser's history database.
    """

    @property
    def source_id(self) -> str:
        return "browser_history"

    @property
    def poll_interval_seconds(self) -> int:
        return 300  # 5 minutes

    async def observe(self) -> ObservationResult:
        # TODO: Replace with real SQLite query.
        # Example (Chrome):
        #   conn = sqlite3.connect(history_path)
        #   cursor = conn.execute(
        #       "SELECT COUNT(*) FROM urls WHERE last_visit_time > ?", (cutoff,)
        #   )
        return ObservationResult(
            metrics={
                "visits_last_hour": 0,
                "unique_domains_last_hour": 0,
                "total_visits_today": 0,
            },
            timestamp=datetime.now(UTC),
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "visits_last_hour": {"expected": 20.0, "tolerance": 30.0},
            "unique_domains_last_hour": {"expected": 5.0, "tolerance": 10.0},
            "total_visits_today": {"expected": 100.0, "tolerance": 100.0},
        }
