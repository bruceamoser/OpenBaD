#!/usr/bin/env python3
"""Read the desktop session log without disturbing the running app.

The app records every turn in `openbad.db`: user messages, assistant text,
tool calls and their results, usage, and errors verbatim. That makes it the
best record of what actually happened; it is just not readable by eye.

This prints a summary by default — counts, tool names, errors in full — and
`--full` prints the exchanges themselves. Secrets are redacted either way.

Usage:
  scripts/diagnostics.py                      # last 24h, summary
  scripts/diagnostics.py --since 2h --errors  # just the failures
  scripts/diagnostics.py --session <id> --full
  scripts/diagnostics.py --list               # session ids and titles
  scripts/diagnostics.py --log 50              # tail the app log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(
    os.environ.get("OPENBAD_DB", Path.home() / ".local/share/openbaddesktop/openbad.db")
)
APP_LOG = DB.parent / "openbad.log"

# Anything that looks like a credential is replaced before printing. The log is
# a debugging aid, not a place to leak a key into a transcript.
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|(?i:api[_-]?key[\"'\s:=]+)[A-Za-z0-9._\-]{8,})"
)


def redact(text: str) -> str:
    return SECRET.sub("[redacted]", text)


def parse_since(value: str) -> int:
    """`30m`, `2h`, `7d`, or an ISO date, as epoch milliseconds."""
    match = re.fullmatch(r"(\d+)([smhd])", value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
        return int((time.time() - seconds) * 1000)
    if value.isdigit():
        return int(value)
    raise SystemExit(f"cannot read --since {value!r}; use 30m, 2h, 7d, or epoch millis")


def connect() -> sqlite3.Connection:
    if not DB.is_file():
        raise SystemExit(f"no session log at {DB}")
    # Read-only, so a running app is never disturbed and the WAL is ignored.
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)


def sessions(conn: sqlite3.Connection, since_ms: int) -> list[tuple[str, str, int, str]]:
    # The workspace root matters: without it a path refusal cannot be judged.
    rows = conn.execute(
        """select s.id, coalesce(s.title, '(untitled)'), s.updated_at,
                  coalesce(w.name || '  ' || w.path, '(no workspace)')
             from sessions s
             left join workspaces w on w.id = s.workspace_id
            where s.updated_at >= ?
            order by s.updated_at desc""",
        (since_ms,),
    ).fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def events(conn: sqlite3.Connection, session: str, limit: int) -> list[tuple]:
    return conn.execute(
        """select kind, seq, payload_json, created_at
             from session_events
            where session_id = ?
            order by seq
            limit ?""",
        (session, limit),
    ).fetchall()


def short(value: str, width: int) -> str:
    value = value.replace("\n", " ⏎ ")
    return value if len(value) <= width else value[: width - 1] + "…"


def payload(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def summarise(conn: sqlite3.Connection, session: str, title: str, workspace: str) -> None:
    rows = events(conn, session, 100_000)
    tools: dict[str, int] = {}
    errors: list[str] = []
    turns = 0
    usage: dict[str, int] = {}

    for kind, _seq, raw, _at in rows:
        body = payload(raw)
        if kind == "turn/start":
            turns += 1
        elif kind == "tool/result":
            name = body.get("call_id", "?")
            tools[name] = tools.get(name, 0) + 1
        elif kind == "error":
            errors.append(redact(str(body.get("message", ""))))
        elif kind == "usage/recorded":
            for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
                usage[key] = usage.get(key, 0) + int(body.get(key) or 0)

    print(f"\n{session}  {redact(title)}")
    print(f"  workspace {redact(workspace)}")
    print(f"  events {len(rows)} · turns {turns} · tool results {sum(tools.values())}")
    if usage:
        print(
            "  tokens in {} · out {} · cache-read {}".format(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0),
            )
        )
    for index, message in enumerate(errors[-10:], start=1):
        print(f"  error {index}: {message}")


def detail(
    conn: sqlite3.Connection, session: str, width: int, limit: int, reasoning: bool
) -> None:
    # Results carry a call_id but not the tool name, so the requests are indexed first.
    names: dict[str, str] = {}
    for kind, _seq, raw, _at in events(conn, session, limit):
        if kind != "assistant/message":
            continue
        for call in payload(raw).get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            if call_id:
                names[call_id] = str(call.get("function", {}).get("name", "?"))

    for kind, seq, raw, _at in events(conn, session, limit):
        body = payload(raw)
        if kind == "user/message":
            print(f"\n[{seq}] USER: {redact(str(body.get('content', '')))}")
        elif kind == "assistant/message":
            text = str(body.get("content") or "")
            thought = str(body.get("reasoning_content") or "")
            if reasoning and thought.strip():
                print(redact(f"[{seq}] THINKING: {short(thought, width)}"))
            if text.strip():
                print(redact(f"[{seq}] ASSISTANT: {short(text, width)}"))
            for call in body.get("tool_calls") or []:
                name = call.get("function", {}).get("name", "?")
                arguments = call.get("function", {}).get("arguments", "")
                print(redact(f"[{seq}]   → {name} {short(str(arguments), width)}"))
        elif kind == "tool/result":
            call_id = str(body.get("call_id") or "")
            name = names.get(call_id, call_id or "?")
            state = "ERROR" if body.get("is_error") else "ok"
            took = body.get("duration_ms")
            took = f" {took}ms" if isinstance(took, int) else ""
            print(redact(f"[{seq}]   ← {name} ({state}{took}) {short(str(body.get('content', '')), width)}"))
        elif kind == "error":
            print(f"[{seq}] ERROR: {redact(str(body.get('message', '')))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read the OpenBaD session log.")
    parser.add_argument("--since", default="24h", help="window: 30m, 2h, 7d, or epoch millis")
    parser.add_argument("--session", help="only this session id")
    parser.add_argument("--list", action="store_true", help="list sessions and stop")
    parser.add_argument("--errors", action="store_true", help="only sessions with an error")
    parser.add_argument("--full", action="store_true", help="print the exchanges")
    parser.add_argument("--reasoning", action="store_true", help="include model reasoning")
    parser.add_argument("--log", type=int, metavar="N", help="tail the last N app log lines and stop")
    parser.add_argument("--width", type=int, default=160, help="characters per line")
    parser.add_argument("--limit", type=int, default=400, help="events printed with --full")
    args = parser.parse_args()

    if args.log:
        if not APP_LOG.is_file():
            print(f"no app log at {APP_LOG}")
            return 0
        lines = APP_LOG.read_text(errors="replace").splitlines()[-args.log :]
        for line in lines:
            print(redact(line))
        return 0

    conn = connect()
    since = parse_since(args.since)
    found = sessions(conn, since)
    if args.session:
        found = [row for row in found if row[0] == args.session] or [
            (args.session, "", 0, "(not in the window; widen --since)")
        ]

    if args.list:
        for session, title, updated, _workspace in found:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated / 1000))
            print(f"{stamp}  {session}  {redact(title)}")
        return 0

    if not found:
        print(f"no sessions since {args.since} in {DB}")
        return 0

    for session, title, _updated, workspace in found:
        if args.errors:
            rows = events(conn, session, 100_000)
            errors = [
                redact(str(payload(raw).get("message", "")))
                for kind, _seq, raw, _at in rows
                if kind == "error"
            ]
            if not errors:
                continue
            print(f"\n{session}  {redact(title)}")
            print(f"  workspace {redact(workspace)}")
            for message in errors[-20:]:
                print(f"  {message}")
            continue
        summarise(conn, session, title, workspace)
        if args.full:
            detail(conn, session, args.width, args.limit, args.reasoning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
