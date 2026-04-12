"""Session-scoped autonomy and immune policy helpers.

This module centralizes persisted session policy so WUI, heartbeat, and other
subsystems can share the same defaults and semantics.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SESSION_POLICY_PATH = Path("/var/lib/openbad/session_policy.yaml")

DEFAULT_SESSION_POLICY: dict[str, object] = {
    "sessions": {
        "chat": {
            "session_id": "chat-main",
            "label": "Chat",
            "allow_task_autonomy": False,
            "allow_research_autonomy": False,
            "allow_destructive": False,
        },
        "tasks": {
            "session_id": "tasks-autonomy",
            "label": "Tasks",
            "allow_task_autonomy": True,
            "allow_research_autonomy": False,
            "allow_destructive": False,
        },
        "research": {
            "session_id": "research-autonomy",
            "label": "Research",
            "allow_task_autonomy": False,
            "allow_research_autonomy": True,
            "allow_destructive": False,
        },
        "immune": {
            "session_id": "immune-monitor",
            "label": "Immune",
            "allow_task_autonomy": False,
            "allow_research_autonomy": False,
            "allow_destructive": False,
        },
    }
}

_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(rm\s+-rf|delete\s+all|drop\s+table|truncate\s+table|"
    r"format\s+disk|shutdown|poweroff|reboot|disable\s+service|"
    r"kill\s+-9|wipe|destroy|purge)\b",
    re.IGNORECASE,
)


def load_session_policy(path: Path = SESSION_POLICY_PATH) -> dict[str, object]:
    """Load policy from disk, merging with defaults."""
    data: dict[str, object] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text()) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001
            data = {}

    result = {
        "sessions": {
            **(DEFAULT_SESSION_POLICY.get("sessions", {})),
            **(data.get("sessions", {}) if isinstance(data.get("sessions"), dict) else {}),
        }
    }
    return result


def save_session_policy(policy: dict[str, object], path: Path = SESSION_POLICY_PATH) -> None:
    """Persist policy to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")


def list_sessions(policy: dict[str, object]) -> list[dict[str, str]]:
    """Return session metadata suitable for the WUI dropdown."""
    sessions = policy.get("sessions", {})
    if not isinstance(sessions, dict):
        return []
    result: list[dict[str, str]] = []
    for key, raw in sessions.items():
        if not isinstance(raw, dict):
            continue
        result.append(
            {
                "key": str(key),
                "session_id": str(raw.get("session_id", key)),
                "label": str(raw.get("label", key)).strip() or str(key),
            }
        )
    return result


def session_id_for(policy: dict[str, object], key: str) -> str:
    sessions = policy.get("sessions", {})
    if isinstance(sessions, dict):
        raw = sessions.get(key)
        if isinstance(raw, dict):
            return str(raw.get("session_id", key))
    return key


def session_allows(policy: dict[str, object], key: str, flag: str, default: bool = False) -> bool:
    sessions = policy.get("sessions", {})
    if not isinstance(sessions, dict):
        return default
    raw = sessions.get(key)
    if not isinstance(raw, dict):
        return default
    return bool(raw.get(flag, default))


def is_destructive_request(text: str) -> bool:
    """Best-effort destructive-intent matcher used by heartbeat autonomy."""
    return bool(_DESTRUCTIVE_PATTERN.search(text or ""))
