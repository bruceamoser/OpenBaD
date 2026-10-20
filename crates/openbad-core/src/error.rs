/// Errors raised by the core domain.
#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    #[error("invalid identifier: {0}")]
    InvalidId(String),
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("invalid event: {0}")]
    InvalidEvent(String),
}
