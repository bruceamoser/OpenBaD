"""Centralized persistent logging for OpenBaD — powered by loguru.

Call ``setup_logging()`` once at each entry point (heartbeat CLI, WUI server).
After that, every ``logging.getLogger().error/warning/info()`` call anywhere
in the codebase is automatically intercepted and routed through loguru into:

  1. stderr (for journalctl)
  2. A JSON-lines file with automatic rotation + retention

The JSON file is the source of truth for the WUI "System Event Log" —
no custom DB table needed.

Usage::

    from openbad.state.event_log import setup_logging, recent_events

    setup_logging()  # call once at startup

    # Everywhere else, use stdlib logging as normal:
    import logging
    log = logging.getLogger(__name__)
    log.warning("Copilot token expired: %s", detail)

    # To read events from the WUI API:
    events = recent_events(limit=100, level="ERROR")
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

from loguru import logger

# ── Defaults ──────────────────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = Path("/var/log/openbad")
_DEFAULT_LOG_FILE = "events.jsonl"
_ROTATION = "5 MB"
_RETENTION = "7 days"
_COMPRESSION = "gz"

_setup_done = False


# ── InterceptHandler: stdlib logging → loguru ─────────────────────────────────

class _InterceptHandler(logging.Handler):
    """Route all stdlib logging records through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Find the loguru level that matches the stdlib level name.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the stack to find the real caller (skip logging internals).
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_logging(
    *,
    verbose: bool = False,
    log_dir: str | Path | None = None,
) -> None:
    """Configure loguru sinks and intercept stdlib logging.

    Safe to call multiple times — only the first call takes effect.
    """
    global _setup_done  # noqa: PLW0603
    if _setup_done:
        return
    _setup_done = True

    level = "DEBUG" if verbose else "INFO"
    resolved_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    resolved_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_dir / _DEFAULT_LOG_FILE

    # Remove loguru's default stderr sink — we'll add our own.
    logger.remove()

    # Sink 1: stderr (for journalctl) — human-readable
    logger.add(
        _safe_stderr_sink,
        level=level,
        format="{time:HH:mm:ss} [{level}] {name}: {message}",
        backtrace=False,
        diagnose=False,
    )

    # Sink 2: JSON-lines file with rotation + retention + compression
    logger.add(
        str(log_path),
        level=level,
        format="{message}",
        serialize=True,
        rotation=_ROTATION,
        retention=_RETENTION,
        compression=_COMPRESSION,
        enqueue=True,        # thread-safe, non-blocking
        backtrace=True,
        diagnose=False,      # don't leak variable values in prod
    )

    # Intercept all stdlib logging → loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)


def _safe_stderr_sink(message: str) -> None:
    """Write to stderr, never raise."""
    import sys  # noqa: PLC0415
    try:
        sys.stderr.write(message)
    except Exception:  # noqa: BLE001
        pass


# ── Query API (reads the JSON-lines log file) ────────────────────────────────

def _log_file_path(log_dir: str | Path | None = None) -> Path:
    resolved_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    return resolved_dir / _DEFAULT_LOG_FILE


def recent_events(
    *,
    limit: int = 100,
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
    log_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read recent log entries from the JSON-lines file.

    Returns newest-first, up to ``limit`` entries.
    Filters are applied in-memory after reading.
    """
    path = _log_file_path(log_dir)
    if not path.exists():
        return []

    try:
        # Read all lines, then return newest first.
        # For a 5 MB file this is ~10-20k lines — fast enough.
        raw_lines = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return []

    level_upper = level.upper() if level else None
    source_lower = source.lower() if source else None
    search_lower = search.lower() if search else None

    results: list[dict[str, Any]] = []
    # Iterate newest → oldest (file is append-only, newest at end).
    for raw in reversed(raw_lines):
        if len(results) >= limit:
            break
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        record = entry.get("record", {})
        entry_level = record.get("level", {}).get("name", "")
        entry_name = record.get("name", "")
        entry_message = record.get("message", "")
        entry_time = record.get("time", {}).get("repr", "")

        # Apply filters
        if level_upper and entry_level != level_upper:
            continue
        if source_lower and source_lower not in entry_name.lower():
            continue
        if search_lower and search_lower not in entry_message.lower():
            continue

        exc = record.get("exception")
        exc_text = ""
        if exc and isinstance(exc, dict):
            exc_text = exc.get("type", "")
            if exc.get("value"):
                exc_text += f": {exc['value']}"

        results.append({
            "ts": entry_time,
            "level": entry_level,
            "source": entry_name,
            "message": entry_message,
            "exception": exc_text,
            "function": record.get("function", ""),
            "line": record.get("line", 0),
        })

    return results

