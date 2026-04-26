"""Event log tool — read and write persistent system events.

Level 1 tool that gives every subsystem (heartbeat, cognitive engine,
chat pipeline, endocrine, immune, etc.) structured read/write access
to the loguru-backed persistent event log at /var/log/openbad/events.jsonl.

Write operations also fire through stdlib logging so they appear in
journalctl and obey all configured loguru sinks.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EventLogToolConfig:
    base_url: str = "http://127.0.0.1:9200"
    timeout: float = 5.0


class EventLogToolAdapter:
    """Read and write persistent system events via the WUI API."""

    def __init__(
        self,
        config: EventLogToolConfig | None = None,
        http_get: object | None = None,
    ) -> None:
        self._config = config or EventLogToolConfig()
        self._http_get = http_get or self._default_http_get

    # ── Read ──────────────────────────────────────────────────────────

    def read_events(
        self,
        limit: int = 100,
        level: str = "",
        source: str = "",
        search: str = "",
    ) -> list[dict[str, Any]]:
        """Return recent persistent log events, newest first.

        Parameters
        ----------
        limit : int
            Maximum entries to return (capped at 500).
        level : str
            Filter by level: ERROR, WARNING, or INFO.
        source : str
            Filter by logger/module name substring.
        search : str
            Free-text substring search on the log message.
        """
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        if level.strip():
            params["level"] = level.strip().upper()
        if source.strip():
            params["source"] = source.strip()
        if search.strip():
            params["search"] = search.strip()

        query = urllib.parse.urlencode(params)
        url = f"{self._config.base_url.rstrip('/')}/api/events"
        if query:
            url = f"{url}?{query}"
        try:
            data = json.loads(
                self._http_get(url, self._config.timeout).decode("utf-8"),
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch events: {exc}") from exc
        events = data.get("events", []) if isinstance(data, dict) else []
        return events if isinstance(events, list) else []

    # ── Write ─────────────────────────────────────────────────────────

    def write_event(
        self,
        message: str,
        level: str = "INFO",
        source: str = "system",
    ) -> bool:
        """Write a structured event to the persistent log.

        The event flows through stdlib logging → loguru InterceptHandler →
        JSON-lines file + stderr/journalctl.

        Parameters
        ----------
        message : str
            Human-readable description of the event.
        level : str
            Severity: ERROR, WARNING, or INFO.
        source : str
            Subsystem or module name (e.g. "heartbeat", "chat", "immune").
        """
        log_fn = {
            "ERROR": logging.getLogger(f"openbad.event.{source}").error,
            "WARNING": logging.getLogger(f"openbad.event.{source}").warning,
        }.get(level.upper(), logging.getLogger(f"openbad.event.{source}").info)
        try:
            log_fn("%s", message)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("event log write failed", exc_info=True)
            return False

    # ── HTTP helper ───────────────────────────────────────────────────

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
