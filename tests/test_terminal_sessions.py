from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.toolbelt.access_control import approve_access_request, create_access_request
from openbad.toolbelt.terminal_sessions import TerminalSessionManager


@pytest.mark.parametrize("command", ["pwd", "echo terminal-test"])
def test_terminal_session_round_trip(tmp_path: Path, command: str) -> None:
    db_path = tmp_path / "state.db"
    initialize_state_db(db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    request = create_access_request(str(workspace), requester="test", db_path=db_path)["request"]
    approve_access_request(request["request_id"], approved_by="user", db_path=db_path)

    manager = TerminalSessionManager(db_path=db_path, idle_timeout_s=60)
    session = manager.create_session(cwd=str(workspace), requester="test")
    assert session["alive"] is True

    manager.send_input(session["session_id"], command)
    output = ""
    for _ in range(20):
        snapshot = manager.read_output(session["session_id"], max_bytes=8192)
        output += snapshot["output"]
        if "terminal-test" in output or str(workspace) in output:
            break

    assert "terminal-test" in output or str(workspace) in output
    closed = manager.close_session(session["session_id"], reason="test")
    assert closed["session_id"] == session["session_id"]


def test_terminal_session_denied_without_grant(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    initialize_state_db(db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = TerminalSessionManager(db_path=db_path, idle_timeout_s=60)

    with pytest.raises(PermissionError):
        manager.create_session(cwd=str(workspace), requester="test")