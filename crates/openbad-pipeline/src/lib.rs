//! The supervised work pipeline: sources, triggers, and read positions.
//!
//! ADR-009 splits the pipeline into six primitives. This crate starts with the
//! first: a source that turns repository activity into immutable triggers, with a
//! persisted read position so a restart neither replays nor drops events.
//!
//! Two boundaries are deliberate and easy to get wrong:
//!
//! * The source **drops** what it does not listen to — its own bot's messages and
//!   activity outside the label filter — before anything enters the domain.
//! * The source **does not authorize**. The allowed list travels with the source
//!   because it is that source's part of the policy, but judging who may
//!   originate work is Eval's job (see [`crate::config::SourceConfig::allowed`]).

pub mod config;
pub mod cursor;
pub mod error;
pub mod source;
pub mod trigger;

pub use config::{PipelineConfig, SourceConfig, load_pipeline_config};
pub use cursor::{MAX_REMEMBERED_TRIGGERS, SourceCursor};
pub use error::SourceError;
pub use source::{GhCli, GitHubApi, GitHubSource, PollOutcome};
pub use trigger::{Channel, ThreadId, Trigger, TriggerId, TriggerKind};
