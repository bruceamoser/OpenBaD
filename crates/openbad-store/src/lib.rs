//! SQLite persistence for OpenBaD Desktop.

pub mod audit;
pub mod error;
pub mod migrations;
pub mod projections;
pub mod store;
pub mod types;

pub use error::StoreError;
pub use store::Store;

pub(crate) fn now_millis() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};

    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as i64)
}
