from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessRequestRecord:
    request_id: str
    requested_path: str
    normalized_root: str
    requester: str
    reason: str
    status: str
    created_at: float
    decided_at: float | None = None
    decided_by: str = ""


@dataclass(frozen=True)
class AccessGrantRecord:
    grant_id: str
    requested_path: str
    normalized_root: str
    reason: str
    approved_by: str
    created_at: float
    source_request_id: str = ""
    revoked_at: float | None = None
    revoked_by: str = ""


def _normalize_root(path: str, *, prefer_parent: bool = False) -> str:
    raw = Path(path).expanduser()
    resolved = raw.resolve(strict=False)
    if prefer_parent:
        candidate = resolved.parent if raw.suffix or resolved.is_file() else resolved
    else:
        candidate = resolved
    return str(candidate)


def _base_roots() -> list[str]:
    roots: list[str] = []
    configured = os.environ.get("OPENBAD_TOOL_ALLOWED_ROOTS", "").strip()
    if configured:
        roots.extend(part.strip() for part in configured.split(os.pathsep) if part.strip())

    project_root = os.environ.get("OPENBAD_PROJECT_ROOT", "").strip()
    if project_root:
        roots.append(project_root)

    roots.extend([
        str(Path.cwd()),
        "/var/lib/openbad/sandbox",
    ])

    seen: set[str] = set()
    normalized: list[str] = []
    for root in roots:
        try:
            resolved = str(Path(root).expanduser().resolve(strict=False))
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return normalized


OPENBAD_USER = "openbad"


def _grant_acl(root: str) -> None:
    """Grant the openbad service user read + traverse access via POSIX ACLs.

    Sets ``u:openbad:rX`` on the directory tree so the systemd service can
    read files inside it.  Also walks parent directories up to ``/`` and
    adds traverse-only (``u:openbad:x``) ACLs on any directory that lacks
    the execute bit for other users, since the service needs to traverse
    the full path chain.

    Requires ``setfacl`` (from the ``acl`` package).
    Falls back silently if setfacl is missing or the command fails.
    """
    setfacl = shutil.which("setfacl")
    if not setfacl:
        log.warning("setfacl not found; cannot grant OS-level ACL for %s", root)
        return

    resolved = str(Path(root).resolve(strict=False))

    # Grant read + traverse on the target tree
    try:
        subprocess.run(
            [setfacl, "-R", "-m", f"u:{OPENBAD_USER}:rX", resolved],
            capture_output=True, timeout=30, check=False,
        )
        # Set default ACL so new files inherit the permission
        subprocess.run(
            [setfacl, "-R", "-d", "-m", f"u:{OPENBAD_USER}:rX", resolved],
            capture_output=True, timeout=30, check=False,
        )
        log.info("Granted ACL u:%s:rX on %s", OPENBAD_USER, resolved)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Failed to set ACL on %s: %s", resolved, exc)
        return

    # Walk parent directories and ensure traverse permission
    parent = Path(resolved).parent
    while str(parent) != parent.root:
        try:
            subprocess.run(
                [setfacl, "-m", f"u:{OPENBAD_USER}:x", str(parent)],
                capture_output=True, timeout=5, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            break
        parent = parent.parent


def _revoke_acl(root: str) -> None:
    """Remove openbad ACL entries from a directory tree."""
    setfacl = shutil.which("setfacl")
    if not setfacl:
        return
    resolved = str(Path(root).resolve(strict=False))
    try:
        subprocess.run(
            [setfacl, "-R", "-x", f"u:{OPENBAD_USER}", resolved],
            capture_output=True, timeout=30, check=False,
        )
        log.info("Revoked ACL u:%s on %s", OPENBAD_USER, resolved)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Failed to revoke ACL on %s: %s", resolved, exc)


def effective_allowed_roots(base_roots: list[str] | None = None, *, db_path: str | Path | None = None) -> list[str]:
    roots = list(base_roots or _base_roots())
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    rows = conn.execute(
        """
        SELECT normalized_root
        FROM path_access_grants
        WHERE revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY created_at ASC
        """,
        (time.time(),),
    ).fetchall()
    seen = set(roots)
    for row in rows:
        root = str(row["normalized_root"] or "").strip()
        if root and root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def list_access_requests(*, db_path: str | Path | None = None, status: str | None = None) -> list[dict[str, Any]]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    if status:
        rows = conn.execute(
            "SELECT * FROM path_access_requests WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM path_access_requests ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def list_access_grants(*, db_path: str | Path | None = None, include_revoked: bool = False) -> list[dict[str, Any]]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    if include_revoked:
        rows = conn.execute("SELECT * FROM path_access_grants ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM path_access_grants WHERE revoked_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def create_access_request(
    path: str,
    *,
    requester: str = "session",
    reason: str = "",
    prefer_parent: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    normalized_root = _normalize_root(path, prefer_parent=prefer_parent)

    existing_grant = conn.execute(
        """
        SELECT * FROM path_access_grants
        WHERE normalized_root = ?
          AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > ?)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (normalized_root, time.time()),
    ).fetchone()
    if existing_grant is not None:
        return {"status": "granted", "grant": dict(existing_grant)}

    existing_request = conn.execute(
        """
        SELECT * FROM path_access_requests
        WHERE normalized_root = ?
          AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (normalized_root,),
    ).fetchone()
    if existing_request is not None:
        return {"status": "pending", "request": dict(existing_request)}

    request_id = str(uuid4())
    created_at = time.time()
    conn.execute(
        """
        INSERT INTO path_access_requests (
            request_id, requested_path, normalized_root, requester, reason, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (request_id, path, normalized_root, requester.strip() or "session", reason.strip(), created_at),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM path_access_requests WHERE request_id = ?", (request_id,)).fetchone()
    return {"status": "pending", "request": dict(row) if row is not None else {}}


def approve_access_request(
    request_id: str,
    *,
    approved_by: str,
    reason: str = "",
    expires_at: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    row = conn.execute(
        "SELECT * FROM path_access_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise KeyError(request_id)

    request_data = dict(row)
    normalized_root = str(request_data["normalized_root"])
    now_ts = time.time()
    conn.execute(
        """
        UPDATE path_access_requests
        SET status = 'approved', decided_at = ?, decided_by = ?
        WHERE request_id = ?
        """,
        (now_ts, approved_by.strip() or "user", request_id),
    )

    existing_grant = conn.execute(
        "SELECT * FROM path_access_grants WHERE normalized_root = ? AND revoked_at IS NULL LIMIT 1",
        (normalized_root,),
    ).fetchone()
    if existing_grant is None:
        grant_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO path_access_grants (
                grant_id, requested_path, normalized_root, reason, approved_by,
                created_at, expires_at, source_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grant_id,
                str(request_data["requested_path"]),
                normalized_root,
                reason.strip() or str(request_data.get("reason") or ""),
                approved_by.strip() or "user",
                now_ts,
                expires_at,
                request_id,
            ),
        )
    conn.commit()
    grant = conn.execute(
        "SELECT * FROM path_access_grants WHERE normalized_root = ? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (normalized_root,),
    ).fetchone()

    # Grant Linux ACL so the openbad service user can actually read the path
    _grant_acl(normalized_root)

    return {"request": dict(conn.execute("SELECT * FROM path_access_requests WHERE request_id = ?", (request_id,)).fetchone()), "grant": dict(grant) if grant is not None else {}}


def deny_access_request(
    request_id: str,
    *,
    denied_by: str,
    reason: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    row = conn.execute(
        "SELECT * FROM path_access_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise KeyError(request_id)

    request_data = dict(row)
    now_ts = time.time()
    decided_by = denied_by.strip() or "user"
    decision_reason = reason.strip() or str(request_data.get("reason") or "")
    conn.execute(
        """
        UPDATE path_access_requests
        SET status = 'denied', decided_at = ?, decided_by = ?, reason = ?
        WHERE request_id = ?
        """,
        (now_ts, decided_by, decision_reason, request_id),
    )
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM path_access_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return dict(updated) if updated is not None else {}


def revoke_access_grant(
    grant_id: str,
    *,
    revoked_by: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = initialize_state_db(db_path or DEFAULT_STATE_DB_PATH)
    row = conn.execute("SELECT * FROM path_access_grants WHERE grant_id = ?", (grant_id,)).fetchone()
    if row is None:
        raise KeyError(grant_id)
    now_ts = time.time()
    conn.execute(
        "UPDATE path_access_grants SET revoked_at = ?, revoked_by = ? WHERE grant_id = ?",
        (now_ts, revoked_by.strip() or "user", grant_id),
    )
    conn.commit()

    # Revoke Linux ACL
    grant_data = dict(row)
    normalized_root = str(grant_data.get("normalized_root", "")).strip()
    if normalized_root:
        _revoke_acl(normalized_root)

    updated = conn.execute("SELECT * FROM path_access_grants WHERE grant_id = ?", (grant_id,)).fetchone()
    return dict(updated) if updated is not None else {}