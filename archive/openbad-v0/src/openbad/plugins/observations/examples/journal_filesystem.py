"""File system journal observation plugin template.

Setup Instructions
==================
1. Set ``JOURNAL_WATCH_DIR`` to the directory to monitor.
2. The plugin counts new/modified files and total directory size.

Required Permissions
====================
- Read access to the watched directory.

Expected Metrics
================
- ``files_modified_last_hour``: Files created or modified in the last hour.
- ``total_files``: Total number of files in the directory.
- ``total_size_mb``: Total directory size in megabytes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult


class JournalFilesystemPlugin(ObservationPlugin):
    """Observe file system changes in a watched directory.

    This is a **template** — real filesystem scanning is stubbed.
    Replace the body of :pymethod:`observe` with actual ``os.scandir``
    or ``pathlib`` directory walks.
    """

    @property
    def source_id(self) -> str:
        return "journal_filesystem"

    @property
    def poll_interval_seconds(self) -> int:
        return 120  # 2 minutes

    async def observe(self) -> ObservationResult:
        # TODO: Replace with real directory scanning.
        # Example:
        #   from pathlib import Path
        #   watch_dir = Path(os.environ["JOURNAL_WATCH_DIR"])
        #   files = list(watch_dir.rglob("*"))
        #   total_size = sum(f.stat().st_size for f in files if f.is_file())
        return ObservationResult(
            metrics={
                "files_modified_last_hour": 0,
                "total_files": 0,
                "total_size_mb": 0.0,
            },
            timestamp=datetime.now(UTC),
        )

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "files_modified_last_hour": {"expected": 2.0, "tolerance": 5.0},
            "total_files": {"expected": 50.0, "tolerance": 50.0},
            "total_size_mb": {"expected": 100.0, "tolerance": 100.0},
        }
