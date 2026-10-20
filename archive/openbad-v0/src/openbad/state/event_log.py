"""Centralized persistent logging for OpenBaD — powered by loguru.

Call ``setup_logging()`` once at each entry point (heartbeat CLI, WUI server).
After that, every ``logging.getLogger().error/warning/info()`` call anywhere
in the codebase is automatically intercepted and routed through loguru into:

  1. stderr (for journalctl)
  2. SQLite ``system_events`` table for fast indexed queries
  3. A JSON-lines file with automatic rotation + retention (backup/archive)

The SQLite table is the source of truth for the WUI "System Event Log".

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
import sqlite3
import threading
import time as _time
from pathlib import Path
from typing import Any

from loguru import logger

# ── Defaults ──────────────────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = Path("/var/log/openbad")
_DEFAULT_LOG_FILE = "events.jsonl"
_ROTATION = "5 MB"
_RETENTION = "7 days"
_COMPRESSION = "gz"

_MAX_EVENT_ROWS = 50_000  # prune beyond this many rows
_PRUNE_BATCH = 5_000       # delete this many oldest rows per prune cycle
_PRUNE_INTERVAL = 300.0    # seconds between prune checks

_setup_done = False

# Module-level DB connection for the SQLite event sink.
_db_conn: sqlite3.Connection | None = None
_db_lock = threading.Lock()
_last_prune: float = 0.0


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


# ── SQLite event sink ─────────────────────────────────────────────────────────

def _get_event_db() -> sqlite3.Connection | None:
    """Return the module-level event DB connection, or None if not set up."""
    return _db_conn


def _init_event_db() -> sqlite3.Connection | None:
    """Open (or reuse) a SQLite connection for the event log.

    Uses the state DB path conventions — preferred: /var/lib/openbad/data/state.db,
    fallback: local data/state.db.
    """
    global _db_conn  # noqa: PLW0603

    if _db_conn is not None:
        return _db_conn

    # Try to import and reuse the state DB initializer (brings migrations).
    try:
        from openbad.state.db import initialize_state_db  # noqa: PLC0415

        _db_conn = initialize_state_db()
        return _db_conn
    except Exception:  # noqa: BLE001
        pass

    return None


def _sqlite_sink(message: Any) -> None:
    """Loguru sink that writes to the system_events SQLite table."""
    conn = _db_conn
    if conn is None:
        return

    record = message.record
    level_name = record["level"].name
    source = record["name"] or "system"
    msg = record["message"] or ""
    func = record["function"] or ""
    line_no = record["line"] or 0
    ts = record["time"].timestamp()

    exc_text = ""
    if record["exception"] is not None:
        exc_type = record["exception"].type
        exc_value = record["exception"].value
        if exc_type:
            exc_text = exc_type.__name__
            if exc_value:
                exc_text += f": {exc_value}"

    try:
        with _db_lock:
            conn.execute(
                """
                INSERT INTO system_events (ts, level, source, category, summary, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, level_name, source, func, msg[:2000], exc_text[:5000]),
            )
            conn.commit()
            _maybe_prune(conn)
    except Exception:  # noqa: BLE001
        pass  # Never let logging crash the app.


def _maybe_prune(conn: sqlite3.Connection) -> None:
    """Delete oldest rows if the table exceeds _MAX_EVENT_ROWS."""
    global _last_prune  # noqa: PLW0603

    now = _time.time()
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now

    try:
        row = conn.execute("SELECT COUNT(*) FROM system_events").fetchone()
        if row and row[0] > _MAX_EVENT_ROWS:
            conn.execute(
                """
                DELETE FROM system_events
                WHERE event_id IN (
                    SELECT event_id FROM system_events
                    ORDER BY ts ASC LIMIT ?
                )
                """,
                (_PRUNE_BATCH,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


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

    level = "DEBUG" if verbose else "INFO"
    resolved_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR

    def _configure(log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

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

        # Sink 2: JSON-lines file (backup/archive — rotation + retention)
        logger.add(
            str(log_path),
            level=level,
            format="{message}",
            serialize=True,
            rotation=_ROTATION,
            retention=_RETENTION,
            compression=_COMPRESSION,
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )

        # Sink 3: SQLite system_events table — primary source for WUI queries
        _init_event_db()
        if _db_conn is not None:
            logger.add(
                _sqlite_sink,
                level=level,
                backtrace=False,
                diagnose=False,
            )

        # Intercept all stdlib logging → loguru
        logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    log_path = resolved_dir / _DEFAULT_LOG_FILE
    try:
        _configure(log_path)
    except OSError:
        fallback_path = Path("/tmp/openbad") / _DEFAULT_LOG_FILE
        _configure(fallback_path)

    _setup_done = True


def _safe_stderr_sink(message: str) -> None:
    """Write to stderr, never raise."""
    import sys  # noqa: PLC0415
    try:
        sys.stderr.write(message)
    except Exception:  # noqa: BLE001
        pass


# ── Query API ─────────────────────────────────────────────────────────────────

def recent_events(
    *,
    limit: int = 100,
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
    log_dir: str | Path | None = None,
    page: int = 1,
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Return recent events, newest-first.

    Uses SQLite ``system_events`` table when available (fast indexed queries).
    Falls back to tail-reading the JSONL file if the DB is not initialised.

    Parameters
    ----------
    since:
        Unix epoch timestamp.  Only events at or after this time are returned.
    """
    conn = _get_event_db()
    if conn is not None:
        try:
            return _recent_events_db(conn, limit=limit, level=level,
                                     source=source, search=search, page=page,
                                     since=since)
        except Exception:  # noqa: BLE001
            pass  # fall through to file-based
    return _recent_events_file(limit=limit, level=level, source=source,
                               search=search, log_dir=log_dir)


def event_count(
    *,
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
) -> int:
    """Return the total count of matching events (for pagination)."""
    conn = _get_event_db()
    if conn is None:
        return 0
    clauses: list[str] = []
    params: list[object] = []
    if level:
        clauses.append("level = ?")
        params.append(level.upper())
    if source:
        clauses.append("source LIKE ?")
        params.append(f"%{source}%")
    if search:
        clauses.append("summary LIKE ?")
        params.append(f"%{search}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM system_events{where}", params).fetchone()  # noqa: S608
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _recent_events_db(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
    page: int = 1,
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Query system_events with indexed WHERE + LIMIT/OFFSET."""
    clauses: list[str] = []
    params: list[object] = []

    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    if level:
        clauses.append("level = ?")
        params.append(level.upper())
    if source:
        clauses.append("source LIKE ?")
        params.append(f"%{source}%")
    if search:
        clauses.append("summary LIKE ?")
        params.append(f"%{search}%")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    offset = (max(1, page) - 1) * limit
    params.extend([limit, offset])

    rows = conn.execute(
        f"SELECT ts, level, source, category, summary, detail"  # noqa: S608
        f" FROM system_events{where}"
        f" ORDER BY ts DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    from datetime import datetime, timezone  # noqa: PLC0415

    results: list[dict[str, Any]] = []
    for row in rows:
        ts_val = row[0] if isinstance(row, tuple) else row["ts"]
        ts_repr = datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
        results.append({
            "ts": ts_repr,
            "level": row[1] if isinstance(row, tuple) else row["level"],
            "source": row[2] if isinstance(row, tuple) else row["source"],
            "message": row[4] if isinstance(row, tuple) else row["summary"],
            "exception": row[5] if isinstance(row, tuple) else row["detail"],
            "function": row[3] if isinstance(row, tuple) else row["category"],
            "line": 0,
        })
    return results


# ── File-based fallback ──────────────────────────────────────────────────────

def _log_file_path(log_dir: str | Path | None = None) -> Path:
    resolved_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    return resolved_dir / _DEFAULT_LOG_FILE


def _read_tail_lines(path: Path, max_bytes: int = 256_000) -> list[str]:
    """Read lines from the end of a file without loading the whole thing."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []
    read_size = min(size, max_bytes)
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - read_size))
            chunk = fh.read(read_size)
    except OSError:
        return []
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > read_size:
        lines = lines[1:]
    return lines


def _recent_events_file(
    *,
    limit: int = 100,
    level: str | None = None,
    source: str | None = None,
    search: str | None = None,
    log_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fallback: tail-read the JSONL file."""
    path = _log_file_path(log_dir)
    if not path.exists():
        return []

    has_filter = bool(level or source or search)
    max_bytes = 512_000 if has_filter else 256_000
    raw_lines = _read_tail_lines(path, max_bytes=max_bytes)

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

