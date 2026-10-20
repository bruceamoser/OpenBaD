from __future__ import annotations

import os
import pty
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
from openbad.skills.access_control import effective_allowed_roots


@dataclass
class _Session:
    session_id: str
    process: subprocess.Popen[bytes]
    master_fd: int
    cwd: str
    shell: str
    requester: str
    created_at: float
    last_activity: float


class TerminalSessionManager:
    def __init__(self, *, idle_timeout_s: float = 900.0, db_path: str | Path | None = None) -> None:
        self._idle_timeout_s = idle_timeout_s
        self._db_path = db_path or DEFAULT_STATE_DB_PATH
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    def _audit(self, session_id: str, action: str, payload: dict[str, Any]) -> None:
        conn = initialize_state_db(self._db_path)
        conn.execute(
            """
            INSERT INTO terminal_session_audit (audit_id, session_id, action, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid4()), session_id, action, __import__("json").dumps(payload, sort_keys=True), time.time()),
        )
        conn.commit()

    def _reap_idle(self) -> None:
        now_ts = time.time()
        expired = [sid for sid, session in self._sessions.items() if now_ts - session.last_activity > self._idle_timeout_s]
        for session_id in expired:
            self.close_session(session_id, reason="idle-timeout")

    def _validate_cwd(self, cwd: str) -> str:
        resolved = str(Path(cwd).expanduser().resolve(strict=False))
        for root in effective_allowed_roots(db_path=self._db_path):
            root_path = str(Path(root).expanduser().resolve(strict=False))
            if resolved == root_path or resolved.startswith(root_path + os.sep):
                return resolved
        raise PermissionError(f"Working directory escapes allowed roots for terminal session: {resolved}")

    def create_session(self, *, cwd: str, requester: str = "session", shell: str = "/bin/bash") -> dict[str, Any]:
        with self._lock:
            self._reap_idle()
            resolved_cwd = self._validate_cwd(cwd)
            master_fd, slave_fd = pty.openpty()
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": resolved_cwd,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "TERM": "xterm-256color",
            }
            process = subprocess.Popen(
                [shell, "--noprofile", "--norc", "-i"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=resolved_cwd,
                env=env,
                start_new_session=True,
            )
            os.close(slave_fd)
            os.set_blocking(master_fd, False)
            now_ts = time.time()
            session = _Session(
                session_id=str(uuid4()),
                process=process,
                master_fd=master_fd,
                cwd=resolved_cwd,
                shell=shell,
                requester=requester.strip() or "session",
                created_at=now_ts,
                last_activity=now_ts,
            )
            self._sessions[session.session_id] = session
            self._audit(session.session_id, "create", {"cwd": resolved_cwd, "shell": shell, "requester": session.requester})
            return self._serialize(session)

    def _serialize(self, session: _Session) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "cwd": session.cwd,
            "shell": session.shell,
            "requester": session.requester,
            "pid": session.process.pid,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "alive": session.process.poll() is None,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            self._reap_idle()
            return [self._serialize(session) for session in self._sessions.values()]

    def send_input(self, session_id: str, text: str, *, append_newline: bool = True) -> dict[str, Any]:
        with self._lock:
            self._reap_idle()
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            payload = text + ("\n" if append_newline else "")
            os.write(session.master_fd, payload.encode())
            session.last_activity = time.time()
            self._audit(session_id, "input", {"length": len(payload), "append_newline": append_newline})
            return self._serialize(session)

    def read_output(self, session_id: str, *, max_bytes: int = 8192) -> dict[str, Any]:
        with self._lock:
            self._reap_idle()
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            chunks: list[bytes] = []
            remaining = max(1, max_bytes)
            while remaining > 0:
                try:
                    chunk = os.read(session.master_fd, min(4096, remaining))
                except BlockingIOError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            session.last_activity = time.time()
            output = b"".join(chunks).decode(errors="replace")
            self._audit(session_id, "read", {"bytes": len(output.encode())})
            return {**self._serialize(session), "output": output}

    def close_session(self, session_id: str, *, reason: str = "user") -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                raise KeyError(session_id)
            if session.process.poll() is None:
                with __import__("contextlib").suppress(ProcessLookupError):
                    os.killpg(session.process.pid, signal.SIGTERM)
                try:
                    session.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with __import__("contextlib").suppress(ProcessLookupError):
                        os.killpg(session.process.pid, signal.SIGKILL)
                    session.process.wait(timeout=2)
            with __import__("contextlib").suppress(OSError):
                os.close(session.master_fd)
            self._audit(session_id, "close", {"reason": reason})
            return self._serialize(session)


_DEFAULT_MANAGER = TerminalSessionManager()


def get_terminal_session_manager() -> TerminalSessionManager:
    return _DEFAULT_MANAGER