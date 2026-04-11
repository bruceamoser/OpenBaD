from __future__ import annotations

import sqlite3
from pathlib import Path

from openbad.state.db import DEFAULT_STATE_DB_PATH, StateDatabase, initialize_state_db

REQUIRED_TABLES = {
    "schema_migrations",
    "tasks",
    "task_nodes",
    "task_edges",
    "task_runs",
    "task_events",
    "task_notes",
    "task_leases",
    "heartbeat_state",
    "research_nodes",
    "research_findings",
    "reward_programs",
    "mcp_audit",
    "scheduler_windows",
}

REQUIRED_INDEXES = {
    "idx_tasks_status_due_at",
    "idx_task_nodes_task_status",
    "idx_task_edges_task_from",
    "idx_task_edges_task_to",
    "idx_task_runs_task_started_at",
    "idx_task_events_task_created_at",
    "idx_task_events_node_created_at",
    "idx_task_leases_owner_expires_at",
    "idx_task_leases_resource",
    "idx_research_nodes_status_priority",
    "idx_mcp_audit_task_started_at",
    "idx_scheduler_windows_type_start_at",
}


def _sqlite_objects(conn: sqlite3.Connection, object_type: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ?",
        (object_type,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_initialize_state_db_creates_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "state.db"

    conn = initialize_state_db(db_path)
    conn.close()

    assert db_path.exists()

    verify = sqlite3.connect(str(db_path))
    tables = _sqlite_objects(verify, "table")
    indexes = _sqlite_objects(verify, "index")
    verify.close()

    assert REQUIRED_TABLES.issubset(tables)
    assert REQUIRED_INDEXES.issubset(indexes)


def test_initialize_state_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"

    first = initialize_state_db(db_path)
    second = initialize_state_db(db_path)

    migration_count = second.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0]

    first.close()
    second.close()

    assert migration_count == 1


def test_state_database_wrapper_initializes_db(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "state.db"

    db = StateDatabase(db_path)
    tables = _sqlite_objects(db.connection, "table")
    db.close()

    assert "tasks" in tables
    assert db_path.exists()


def test_default_db_path_is_configurable() -> None:
    assert Path("data/state.db") == DEFAULT_STATE_DB_PATH


def test_custom_db_path_resolution(tmp_path: Path) -> None:
    custom_path = tmp_path / "custom" / "nested" / "my.db"
    conn = initialize_state_db(custom_path)
    conn.close()

    assert custom_path.exists()
    assert custom_path.parent.is_dir()


def test_connection_pragmas_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "pragma_test.db"
    conn = initialize_state_db(db_path)

    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()

    assert journal_mode == "wal"
    assert fk_enabled == 1
