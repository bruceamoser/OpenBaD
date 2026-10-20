use std::path::{Path, PathBuf};

/// Application paths and process-level configuration.
#[derive(Debug, Clone)]
pub struct AppConfig {
    pub data_dir: PathBuf,
    pub database_path: PathBuf,
}

impl AppConfig {
    #[must_use]
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        let data_dir = data_dir.as_ref().to_path_buf();
        let database_path = data_dir.join("openbad.db");
        Self {
            data_dir,
            database_path,
        }
    }
}
