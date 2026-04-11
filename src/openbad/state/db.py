"""SQLite state database initialization and migration runner."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_DB_PATH = Path("data/state.db")
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class Migration:
    name: str
    path: Path


def _discover_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        migrations.append(Migration(name=path.stem, path=path))
    return migrations


def _create_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at REAL NOT NULL DEFAULT (unixepoch('now'))
        )
        """
    )


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return {str(row[0]) for row in rows}


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    sql = migration.path.read_text()
    try:
        conn.executescript(sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (migration.name,),
        )
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(
            f"Failed applying migration {migration.name} from {migration.path}: {exc}"
        ) from exc


def initialize_state_db(db_path: str | Path = DEFAULT_STATE_DB_PATH) -> sqlite3.Connection:
    """Create or open the state DB and apply pending migrations."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        _create_migration_table(conn)
        applied = _applied_migrations(conn)
        for migration in _discover_migrations():
            if migration.name in applied:
                continue
            _apply_migration(conn, migration)
            applied.add(migration.name)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    return conn


class StateDatabase:
    """Thin lifecycle wrapper around the SQLite state database connection."""

    def __init__(self, db_path: str | Path = DEFAULT_STATE_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._conn = initialize_state_db(self._db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()
