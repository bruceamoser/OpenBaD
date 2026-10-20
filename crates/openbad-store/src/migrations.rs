use rusqlite::{Connection, params};

use crate::error::StoreError;
use crate::now_millis;

const MIGRATIONS: &[(&str, &str)] = &[
    (
        "0001_initial",
        include_str!("../migrations/0001_initial.sql"),
    ),
    (
        "0002_phase1_schema",
        include_str!("../migrations/0002_phase1_schema.sql"),
    ),
    (
        "0003_workspace_index",
        include_str!("../migrations/0003_workspace_index.sql"),
    ),
];

/// Apply every pending embedded migration.
pub fn apply(connection: &mut Connection) -> Result<(), StoreError> {
    connection.execute_batch(
        "CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY NOT NULL,
            applied_at INTEGER NOT NULL
        );",
    )?;

    for (version, sql) in MIGRATIONS {
        let already_applied = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = ?1)",
            [version],
            |row| row.get::<_, bool>(0),
        )?;

        if already_applied {
            continue;
        }

        let transaction = connection.transaction()?;
        transaction.execute_batch(sql)?;
        transaction.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?1, ?2)",
            params![version, now_millis()],
        )?;
        transaction.commit()?;
    }

    Ok(())
}

/// The newest applied migration version.
pub fn current_version(connection: &Connection) -> Result<String, StoreError> {
    let version = connection
        .query_row(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .ok();

    Ok(version.unwrap_or_else(|| "none".to_owned()))
}
