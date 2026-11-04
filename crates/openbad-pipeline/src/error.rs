//! Failures a source can report.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum SourceError {
    /// The project's pipeline configuration cannot be read or is malformed.
    #[error("pipeline configuration: {0}")]
    Config(String),
    /// The provider refused or failed the request.
    #[error("github api: {0}")]
    Api(String),
    /// A response did not have the shape we expect.
    #[error("github response: {0}")]
    Shape(String),
}

impl SourceError {
    /// Whether polling again might succeed without anyone changing anything.
    ///
    /// Rate limits and transport failures are worth retrying; a malformed
    /// configuration is not, and retrying it only hides the problem.
    #[must_use]
    pub fn is_transient(&self) -> bool {
        matches!(self, Self::Api(_))
    }
}
