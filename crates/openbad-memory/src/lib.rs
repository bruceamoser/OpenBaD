//! Cognitive memory model: engrams, scopes, and categories.

use openbad_core::{SessionId, WorkspaceId};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryScope {
    Global,
    Workspace(WorkspaceId),
    Session(SessionId),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EngramCategory {
    Fact,
    Preference,
    Decision,
    Experience,
    Procedure,
    Reference,
    Relationship,
    Convention,
    CodePattern,
    BugLesson,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Engram {
    pub id: Uuid,
    pub scope: MemoryScope,
    pub category: EngramCategory,
    pub content: String,
    pub confidence: f32,
    pub base_activation: f32,
    pub pinned: bool,
}

impl Engram {
    #[must_use]
    pub fn new(scope: MemoryScope, category: EngramCategory, content: impl Into<String>) -> Self {
        Self {
            id: Uuid::now_v7(),
            scope,
            category,
            content: content.into(),
            confidence: 1.0,
            base_activation: 0.0,
            pinned: false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_engrams_start_at_full_confidence() {
        let engram = Engram::new(
            MemoryScope::Global,
            EngramCategory::Preference,
            "Prefer small diffs",
        );
        assert_eq!(engram.confidence, 1.0);
        assert!(!engram.pinned);
    }
}
