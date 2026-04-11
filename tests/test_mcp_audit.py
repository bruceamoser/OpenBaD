from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openbad.plugins.mcp_audit import AuditRecord, MCPAuditStore, initialize_audit_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "audit.db")
    initialize_audit_db(conn)
    return conn


@pytest.fixture()
def store(db: sqlite3.Connection) -> MCPAuditStore:
    return MCPAuditStore(db)


# ---------------------------------------------------------------------------
# Success audit write
# ---------------------------------------------------------------------------


def test_record_success_returns_audit_record(store: MCPAuditStore) -> None:
    rec = store.record(session_id="s1", tool_name="file.read")

    assert isinstance(rec, AuditRecord)
    assert rec.success is True
    assert rec.tool_name == "file.read"
    assert rec.audit_id


def test_record_success_no_error_summary(store: MCPAuditStore) -> None:
    rec = store.record(session_id="s1", tool_name="file.read")
    assert rec.error_summary is None


def test_record_success_with_task_and_run(store: MCPAuditStore) -> None:
    rec = store.record(
        session_id="s1",
        tool_name="db.insert",
        task_id="task-123",
        run_id="run-456",
    )
    assert rec.task_id == "task-123"
    assert rec.run_id == "run-456"


# ---------------------------------------------------------------------------
# Failure audit write
# ---------------------------------------------------------------------------


def test_record_failure_sets_success_false(store: MCPAuditStore) -> None:
    rec = store.record(
        session_id="s1",
        tool_name="bad.tool",
        success=False,
        error_summary="permission denied",
    )
    assert rec.success is False
    assert rec.error_summary == "permission denied"


def test_record_failure_has_audit_id(store: MCPAuditStore) -> None:
    rec = store.record(
        session_id="s1", tool_name="bad.tool", success=False, error_summary="err"
    )
    assert rec.audit_id


# ---------------------------------------------------------------------------
# Audit query by task
# ---------------------------------------------------------------------------


def test_query_by_task_returns_records(store: MCPAuditStore) -> None:
    store.record(session_id="s1", tool_name="file.read", task_id="t1")
    store.record(session_id="s1", tool_name="db.insert", task_id="t1")

    records = store.query_by_task("t1")
    assert len(records) == 2


def test_query_by_task_excludes_other_tasks(store: MCPAuditStore) -> None:
    store.record(session_id="s1", tool_name="file.read", task_id="t1")
    store.record(session_id="s2", tool_name="file.read", task_id="t2")

    records = store.query_by_task("t1")
    assert all(r.task_id == "t1" for r in records)


def test_query_by_task_empty_result(store: MCPAuditStore) -> None:
    assert store.query_by_task("no-such-task") == []


# ---------------------------------------------------------------------------
# Audit query by run
# ---------------------------------------------------------------------------


def test_query_by_run_returns_records(store: MCPAuditStore) -> None:
    store.record(session_id="s1", tool_name="tool_a", run_id="r1")
    store.record(session_id="s1", tool_name="tool_b", run_id="r1")
    store.record(session_id="s1", tool_name="tool_c", run_id="r2")

    records = store.query_by_run("r1")
    assert len(records) == 2
    assert all(r.run_id == "r1" for r in records)


def test_query_by_run_empty_result(store: MCPAuditStore) -> None:
    assert store.query_by_run("nonexistent-run") == []


def test_audit_record_to_dict(store: MCPAuditStore) -> None:
    rec = store.record(
        session_id="s1",
        tool_name="file.read",
        task_id="t1",
        success=True,
    )
    d = rec.to_dict()
    assert d["tool_name"] == "file.read"
    assert d["success"] is True
    assert "recorded_at" in d
