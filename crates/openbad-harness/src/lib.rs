//! Prompt assembly and cache-first request planning.

pub mod envelope;
pub mod prompt;

pub use envelope::{
    ApiFormat, ReasoningEffort, RequestEnvelope, SeriesChange, SeriesChangeReason, SeriesGuard,
    hash_bytes,
};
pub use prompt::{
    CacheClass, PromptError, PromptSection, SectionRegistry, canonical_json, looks_volatile,
};
