//! What an accept grants.
//!
//! A Mandate is the artifact that separates "the agent may do this" from "the
//! agent was mentioned once". It records *why* work is allowed, so an executor
//! can act inside that scope without asking per action, and so a later reader can
//! see the authority a result was produced under.
//!
//! It is immutable. A wider scope is a new ticket with a new mandate, never an
//! amended one — otherwise the record of what was authorized stops matching what
//! actually happened.

use serde::{Deserialize, Serialize};

/// How much the requester asked for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MandateStrength {
    /// A person on the allowed list asked for this work.
    Explicit,
    /// A bot on the source's bot list proposed it. A proposal is never an
    /// explicit request: it may start work, but it cannot claim a person asked.
    Inferred,
}

impl MandateStrength {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Explicit => "explicit",
            Self::Inferred => "inferred",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Mandate {
    /// The policy version the decision was made under. A later policy change is
    /// delivered to a live ticket rather than retroactively revoking this.
    pub policy_version: String,
    /// What the work may touch. For a ticket started from a thread, the thread.
    pub scope: String,
    pub strength: MandateStrength,
    /// The rules that admitted it, in the order they matched. Kept because
    /// "why did the agent do this?" is the first question anyone asks.
    pub matched_rules: Vec<String>,
    /// The GitHub login that caused the admission.
    pub requester: String,
    pub source_id: String,
    /// How the source received it, in the source's own terms — for GitHub, the
    /// label that admitted the issue.
    pub received_via: String,
}

impl Mandate {
    #[must_use]
    pub fn new(
        policy_version: impl Into<String>,
        scope: impl Into<String>,
        strength: MandateStrength,
        matched_rules: Vec<String>,
        requester: impl Into<String>,
        source_id: impl Into<String>,
        received_via: impl Into<String>,
    ) -> Self {
        Self {
            policy_version: policy_version.into(),
            scope: scope.into(),
            strength,
            matched_rules,
            requester: requester.into(),
            source_id: source_id.into(),
            received_via: received_via.into(),
        }
    }

    /// Whether this mandate came from a person asking.
    #[must_use]
    pub fn is_explicit(&self) -> bool {
        self.strength == MandateStrength::Explicit
    }
}
