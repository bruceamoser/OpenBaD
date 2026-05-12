"""SQLite state database initialization and migration runner."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec


def _resolve_default_state_db() -> Path:
    """Resolve the canonical state DB path.

    Priority:
      1. OPENBAD_STATE_DB env var (explicit override)
      2. /var/lib/openbad/data/state.db (production install)
      3. data/state.db (relative fallback for dev without production dir)
    """
    configured = os.environ.get("OPENBAD_STATE_DB", "").strip()
    if configured:
        return Path(configured)
    preferred = Path("/var/lib/openbad/data/state.db")
    if preferred.exists() or preferred.parent.exists():
        return preferred
    return Path("data/state.db")


DEFAULT_STATE_DB_PATH = _resolve_default_state_db()
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Connection cache: reuse a single connection per resolved db path.
_conn_cache: dict[str, sqlite3.Connection] = {}
_conn_lock = threading.Lock()


@dataclass(frozen=True)
class Migration:
    name: str
    path: Path


def _discover_migrations(migrations_dir: Path = _MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
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


def initialize_state_db(
    db_path: str | Path = DEFAULT_STATE_DB_PATH,
    *,
    migrations_dir: Path | None = None,
) -> sqlite3.Connection:
    """Create or open the state DB and apply pending migrations.

    Connections are cached per resolved path so that repeated calls
    (e.g. from high-frequency API polling) reuse the same connection
    instead of leaking one per call.
    """
    path = Path(db_path)
    cache_key = str(path.resolve())

    with _conn_lock:
        cached = _conn_cache.get(cache_key)
        if cached is not None:
            try:
                cached.execute("SELECT 1")
                return cached
            except sqlite3.ProgrammingError:
                # Connection was closed externally; fall through to recreate.
                _conn_cache.pop(cache_key, None)

    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    effective_dir = migrations_dir if migrations_dir is not None else _MIGRATIONS_DIR

    try:
        _create_migration_table(conn)
        applied = _applied_migrations(conn)
        for migration in _discover_migrations(effective_dir):
            if migration.name in applied:
                continue
            _apply_migration(conn, migration)
            applied.add(migration.name)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    with _conn_lock:
        _conn_cache[cache_key] = conn

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
        cache_key = str(self._db_path.resolve())
        with _conn_lock:
            _conn_cache.pop(cache_key, None)
        self._conn.close()


def close_cached_connection(db_path: str | Path = DEFAULT_STATE_DB_PATH) -> None:
    """Close and evict a cached connection for *db_path*."""
    cache_key = str(Path(db_path).resolve())
    with _conn_lock:
        conn = _conn_cache.pop(cache_key, None)
    if conn is not None:
        conn.close()
