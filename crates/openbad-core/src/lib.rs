//! Core domain types for OpenBaD Desktop.

pub mod clock;
pub mod config;
pub mod error;
pub mod event;
pub mod ids;
pub mod state;

pub use clock::{Clock, SystemClock};
pub use error::CoreError;
pub use event::{NewEvent, SessionEvent, SessionEventKind};
pub use ids::{ModelRouteId, ProviderId, SessionId, WorkspaceId};
pub use state::{SessionStatus, WorkspaceTrust};
