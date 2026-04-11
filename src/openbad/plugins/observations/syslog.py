"""System log observation plugin for Active Inference engine."""

from __future__ import annotations

import asyncio
import logging
import subprocess

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult

logger = logging.getLogger(__name__)


class SyslogObservationPlugin(ObservationPlugin):
    """Observes system logs via journalctl for anomalies and events.

    Detects service failures, security events, and system errors.
    Can trigger immune system or adrenaline responses for critical events.
    """

    def __init__(
        self,
        lookback_minutes: int = 15,
        severity_threshold: str = "warning",
        service_filters: list[str] | None = None,
    ):
        """Initialize syslog observer.

        Args:
            lookback_minutes: Minutes of logs to analyze
            severity_threshold: Minimum severity ('debug', 'info', 'warning', 'err', 'crit')
            service_filters: Optional list of systemd units to monitor
        """
        self._lookback_minutes = lookback_minutes
        self._severity = severity_threshold
        self._services = service_filters or []

        # Severity to priority mapping
        self._priority_levels = {
            "debug": 7,
            "info": 6,
            "notice": 5,
            "warning": 4,
            "err": 3,
            "crit": 2,
            "alert": 1,
            "emerg": 0,
        }

    @property
    def source_id(self) -> str:
        """Unique identifier for syslog observations."""
        return "syslog"

    async def observe(self) -> ObservationResult:
        """Fetch recent system log entries."""
        try:
            total_entries, error_count, critical_count, services_with_errors = (
                await asyncio.to_thread(self._query_journalctl)
            )

            return ObservationResult(
                metrics={
                    "total_entries": total_entries,
                    "error_count": error_count,
                    "critical_count": critical_count,
                    "affected_services": len(services_with_errors),
                },
                raw_data={
                    "services_with_errors": services_with_errors,
                },
            )
        except Exception as e:
            logger.warning(f"Syslog observation failed: {e}")
            return ObservationResult(
                metrics={
                    "total_entries": 0,
                    "error_count": 0,
                    "critical_count": 0,
                    "affected_services": 0,
                },
                raw_data={"error": str(e)},
            )

    def _query_journalctl(
        self,
    ) -> tuple[int, int, int, list[str]]:
        """Query system journal via journalctl (blocking)."""
        try:
            # Build journalctl command
            cmd = [
                "journalctl",
                "--no-pager",
                "-o",
                "json",
                f"--since={self._lookback_minutes} minutes ago",
                f"--priority={self._severity}",
            ]

            # Add service filters if specified
            if self._services:
                for service in self._services:
                    cmd.extend(["-u", service])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.error(f"journalctl failed: {result.stderr}")
                return 0, 0, 0, []

            import json

            entries = [json.loads(line) for line in result.stdout.strip().split("\n") if line]
            total_entries = len(entries)

            # Count errors and criticals
            error_count = sum(
                1 for e in entries if self._get_priority(e) <= self._priority_levels["err"]
            )
            critical_count = sum(
                1 for e in entries if self._get_priority(e) <= self._priority_levels["crit"]
            )

            # Identify affected services
            services_with_errors = set()
            for entry in entries:
                if self._get_priority(entry) <= self._priority_levels["err"]:
                    unit = entry.get("_SYSTEMD_UNIT", "unknown")
                    if unit and unit != "unknown":
                        services_with_errors.add(unit)

            return total_entries, error_count, critical_count, list(services_with_errors)

        except FileNotFoundError:
            logger.warning("journalctl not found, syslog plugin disabled")
            return 0, 0, 0, []
        except Exception as e:
            logger.error(f"journalctl query failed: {e}")
            return 0, 0, 0, []

    @staticmethod
    def _get_priority(entry: dict) -> int:
        """Extract priority from journal entry."""
        priority_str = entry.get("PRIORITY", "6")
        try:
            return int(priority_str)
        except ValueError:
            return 6  # Default to INFO if parsing fails

    def default_predictions(self) -> dict[str, dict[str, float]]:
        """Initial predictions for syslog metrics."""
        return {
            "total_entries": {"expected": 20.0, "tolerance": 50.0},
            "error_count": {"expected": 0.0, "tolerance": 5.0},
            "critical_count": {"expected": 0.0, "tolerance": 1.0},
            "affected_services": {"expected": 0.0, "tolerance": 3.0},
        }

    @property
    def poll_interval_seconds(self) -> int:
        """Poll syslog every 5 minutes."""
        return 300
