"""Recurrence rule parsing and next-due computation.

Supported rule formats:
    daily|HH:MM|TZ          — every day at HH:MM in timezone TZ
    weekly|DOW|HH:MM|TZ     — every week on DOW at HH:MM (mon-sun)
    interval|Ns              — every N seconds (no timezone needed)

Timezone values use IANA names (e.g. ``America/New_York``).
Common aliases like ``US/Eastern`` are mapped to IANA equivalents.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Common timezone aliases → IANA
_TZ_ALIASES: dict[str, str] = {
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "US/Hawaii": "Pacific/Honolulu",
    "US/Alaska": "America/Anchorage",
    "EST": "America/New_York",
    "CST": "America/Chicago",
    "MST": "America/Denver",
    "PST": "America/Los_Angeles",
    "GMT": "Etc/GMT",
    "UTC": "UTC",
}

_DAYS_OF_WEEK = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_INTERVAL_RE = re.compile(r"^(\d+)s$")


class RecurrenceError(ValueError):
    """Raised when a recurrence rule cannot be parsed."""


def _resolve_tz(tz_str: str) -> ZoneInfo:
    """Resolve a timezone string to a ZoneInfo, supporting aliases."""
    canonical = _TZ_ALIASES.get(tz_str, tz_str)
    try:
        return ZoneInfo(canonical)
    except (ZoneInfoNotFoundError, KeyError) as exc:
        raise RecurrenceError(f"Unknown timezone: {tz_str!r}") from exc


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse HH:MM and return (hour, minute)."""
    m = _TIME_RE.match(time_str)
    if m is None:
        raise RecurrenceError(f"Invalid time format: {time_str!r} (expected HH:MM)")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise RecurrenceError(f"Time out of range: {time_str!r}")
    return hour, minute


def validate_recurrence_rule(rule: str) -> None:
    """Validate a recurrence rule string. Raises RecurrenceError if invalid."""
    parts = rule.split("|")
    if not parts:
        raise RecurrenceError("Empty recurrence rule")

    kind = parts[0].lower()

    if kind == "daily":
        if len(parts) != 3:
            raise RecurrenceError(
                f"daily rule requires 3 parts: daily|HH:MM|TZ (got {len(parts)})"
            )
        _parse_time(parts[1])
        _resolve_tz(parts[2])

    elif kind == "weekly":
        if len(parts) != 4:
            raise RecurrenceError(
                f"weekly rule requires 4 parts: weekly|DOW|HH:MM|TZ (got {len(parts)})"
            )
        dow = parts[1].lower()
        if dow not in _DAYS_OF_WEEK:
            raise RecurrenceError(
                f"Invalid day of week: {parts[1]!r} "
                f"(expected one of {', '.join(_DAYS_OF_WEEK)})"
            )
        _parse_time(parts[2])
        _resolve_tz(parts[3])

    elif kind == "interval":
        if len(parts) != 2:
            raise RecurrenceError(
                f"interval rule requires 2 parts: interval|Ns (got {len(parts)})"
            )
        m = _INTERVAL_RE.match(parts[1])
        if m is None:
            raise RecurrenceError(
                f"Invalid interval format: {parts[1]!r} (expected Ns, e.g. 3600s)"
            )
        seconds = int(m.group(1))
        if seconds < 1:
            raise RecurrenceError("Interval must be at least 1 second")

    else:
        raise RecurrenceError(
            f"Unknown recurrence type: {kind!r} (expected daily, weekly, or interval)"
        )


def compute_next_due(rule: str, after: datetime | None = None) -> float:
    """Compute the next due timestamp (Unix) for a recurrence rule.

    Parameters
    ----------
    rule:
        A validated recurrence rule string.
    after:
        The reference time. Defaults to now (UTC).

    Returns
    -------
    float
        Unix timestamp of the next occurrence.
    """
    validate_recurrence_rule(rule)
    parts = rule.split("|")
    kind = parts[0].lower()

    if after is None:
        after = datetime.now(UTC)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=UTC)

    if kind == "daily":
        hour, minute = _parse_time(parts[1])
        tz = _resolve_tz(parts[2])
        return _next_daily(after, hour, minute, tz)

    if kind == "weekly":
        dow_str = parts[1].lower()
        target_dow = _DAYS_OF_WEEK[dow_str]
        hour, minute = _parse_time(parts[2])
        tz = _resolve_tz(parts[3])
        return _next_weekly(after, target_dow, hour, minute, tz)

    # interval
    m = _INTERVAL_RE.match(parts[1])
    assert m is not None
    seconds = int(m.group(1))
    return (after + timedelta(seconds=seconds)).timestamp()


def _next_daily(
    after: datetime, hour: int, minute: int, tz: ZoneInfo
) -> float:
    """Find the next daily occurrence at hour:minute in tz after 'after'."""
    local = after.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _next_weekly(
    after: datetime,
    target_dow: int,
    hour: int,
    minute: int,
    tz: ZoneInfo,
) -> float:
    """Find the next weekly occurrence on target_dow at hour:minute in tz."""
    local = after.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    current_dow = local.weekday()
    days_ahead = target_dow - current_dow
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0 and candidate <= local:
        days_ahead = 7
    candidate += timedelta(days=days_ahead)
    return candidate.timestamp()
