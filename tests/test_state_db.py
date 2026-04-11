from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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


def test_migration_schema_version_is_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "version_test.db"
    conn = initialize_state_db(db_path)

    applied = {
        row[0]
        for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
    }
    conn.close()

    assert "0001_initial" in applied


# ---------------------------------------------------------------------------
# Issue #337: migration runner idempotency, failure handling, ordering
# ---------------------------------------------------------------------------


def test_migration_runner_ordered_application(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "0001_first.sql").write_text(
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);"
    )
    (mig_dir / "0002_second.sql").write_text(
        "CREATE TABLE second_table (id INTEGER PRIMARY KEY);"
    )

    db_path = tmp_path / "ordered.db"
    conn = initialize_state_db(db_path, migrations_dir=mig_dir)

    applied = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM schema_migrations ORDER BY applied_at ASC"
        ).fetchall()
    ]
    conn.close()

    assert applied == ["0001_first", "0002_second"]


def test_migration_runner_failure_includes_identifier(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "0001_bad.sql").write_text("NOT VALID SQL !!!")

    db_path = tmp_path / "fail.db"

    with pytest.raises(RuntimeError, match="0001_bad"):
        initialize_state_db(db_path, migrations_dir=mig_dir)


def test_migration_runner_failure_leaves_no_partial_state(tmp_path: Path) -> None:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "0001_good.sql").write_text(
        "CREATE TABLE good_table (id INTEGER PRIMARY KEY);"
    )
    (mig_dir / "0002_bad.sql").write_text("NOT VALID SQL !!!")

    db_path = tmp_path / "partial.db"

    with pytest.raises(RuntimeError):
        initialize_state_db(db_path, migrations_dir=mig_dir)

    # DB file may exist but schema_migrations should not record a partial run
    verify = sqlite3.connect(str(db_path))
    try:
        applied = [
            row[0]
            for row in verify.execute("SELECT name FROM schema_migrations").fetchall()
        ]
    except sqlite3.OperationalError:
        applied = []
    verify.close()

    assert "0002_bad" not in applied
