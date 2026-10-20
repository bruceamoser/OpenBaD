/// Errors raised by the SQLite store.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("store lock poisoned")]
    LockPoisoned,
    #[error("store actor is unavailable")]
    ActorUnavailable,
    #[error("not found: {0}")]
    NotFound(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("integrity check failed: {0}")]
    Integrity(String),
    #[error("could not resolve the application data directory")]
    NoDataDirectory,
    #[error("invalid stored data: {0}")]
    InvalidData(String),
    #[error("migration error: {0}")]
    Migration(String),
}
