//! The supervised work pipeline: sources, triggers, admission, and tickets.
//!
//! ADR-009 splits the pipeline into six primitives. This crate holds the parts
//! that are pure decisions — a source turns repository activity into immutable
//! triggers, Eval decides what each trigger is allowed to do, and a ticket is the
//! durable unit of work an accept creates. Persistence lives in `openbad-store`,
//! so every rule here is testable without a database.
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
pub mod eval;
pub mod mandate;
pub mod source;
pub mod ticket;
pub mod trigger;

pub use config::{PipelineConfig, SourceConfig, load_pipeline_config};
pub use cursor::{MAX_REMEMBERED_TRIGGERS, SourceCursor};
pub use error::SourceError;
pub use eval::{Decision, DecisionKind, EvalInput, ThreadContext, evaluate};
pub use mandate::{Mandate, MandateStrength};
pub use source::{GhCli, GitHubApi, GitHubSource, PollOutcome};
pub use ticket::{AssignmentState, ReportOutcome, TicketState};
pub use trigger::{Channel, ThreadId, Trigger, TriggerId, TriggerKind};
