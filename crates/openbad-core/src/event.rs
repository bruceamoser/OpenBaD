use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::ids::SessionId;

/// The durable vocabulary of a session.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionEventKind {
    TurnStart,
    TurnEnd,
    StepStart,
    StepEnd,
    UserMessage,
    SystemMessage,
    DeveloperMessage,
    AssistantMessage,
    AssistantAttempt,
    ToolCall,
    ToolResult,
    ApprovalRequest,
    ApprovalDecision,
    RequestHeader,
    RequestContext,
    UsageRecorded,
    CompactionStart,
    CompactionSummary,
    CompactionEnd,
    MemoryRecalled,
    MemoryWritten,
    EvidenceAccepted,
    EvidenceRejected,
    JobStarted,
    JobFinished,
    ScheduleFired,
    Error,
}

impl SessionEventKind {
    /// The stable wire/storage name for this event kind.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::TurnStart => "turn/start",
            Self::TurnEnd => "turn/end",
            Self::StepStart => "step/start",
            Self::StepEnd => "step/end",
            Self::UserMessage => "user/message",
            Self::SystemMessage => "system/message",
            Self::DeveloperMessage => "developer/message",
            Self::AssistantMessage => "assistant/message",
            Self::AssistantAttempt => "assistant/attempt",
            Self::ToolCall => "tool/call",
            Self::ToolResult => "tool/result",
            Self::ApprovalRequest => "approval/request",
            Self::ApprovalDecision => "approval/decision",
            Self::RequestHeader => "request/header",
            Self::RequestContext => "request/context",
            Self::UsageRecorded => "usage/recorded",
            Self::CompactionStart => "compaction/start",
            Self::CompactionSummary => "compaction/summary",
            Self::CompactionEnd => "compaction/end",
            Self::MemoryRecalled => "memory/recalled",
            Self::MemoryWritten => "memory/written",
            Self::EvidenceAccepted => "evidence/accepted",
            Self::EvidenceRejected => "evidence/rejected",
            Self::JobStarted => "job/started",
            Self::JobFinished => "job/finished",
            Self::ScheduleFired => "schedule/fired",
            Self::Error => "error",
        }
    }

    /// Whether this event contributes to the model-visible message history.
    #[must_use]
    pub fn is_surface(self) -> bool {
        matches!(
            self,
            Self::UserMessage
                | Self::SystemMessage
                | Self::DeveloperMessage
                | Self::AssistantMessage
                | Self::ToolCall
                | Self::ToolResult
                | Self::CompactionSummary
        )
    }
}

impl std::str::FromStr for SessionEventKind {
    type Err = crate::error::CoreError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let kind = match value {
            "turn/start" => Self::TurnStart,
            "turn/end" => Self::TurnEnd,
            "step/start" => Self::StepStart,
            "step/end" => Self::StepEnd,
            "user/message" => Self::UserMessage,
            "system/message" => Self::SystemMessage,
            "developer/message" => Self::DeveloperMessage,
            "assistant/message" => Self::AssistantMessage,
            "assistant/attempt" => Self::AssistantAttempt,
            "tool/call" => Self::ToolCall,
            "tool/result" => Self::ToolResult,
            "approval/request" => Self::ApprovalRequest,
            "approval/decision" => Self::ApprovalDecision,
            "request/header" => Self::RequestHeader,
            "request/context" => Self::RequestContext,
            "usage/recorded" => Self::UsageRecorded,
            "compaction/start" => Self::CompactionStart,
            "compaction/summary" => Self::CompactionSummary,
            "compaction/end" => Self::CompactionEnd,
            "memory/recalled" => Self::MemoryRecalled,
            "memory/written" => Self::MemoryWritten,
            "evidence/accepted" => Self::EvidenceAccepted,
            "evidence/rejected" => Self::EvidenceRejected,
            "job/started" => Self::JobStarted,
            "job/finished" => Self::JobFinished,
            "schedule/fired" => Self::ScheduleFired,
            "error" => Self::Error,
            other => {
                return Err(crate::error::CoreError::InvalidEvent(format!(
                    "unknown event kind: {other}"
                )));
            }
        };
        Ok(kind)
    }
}

/// An event proposed for append to a session.
#[derive(Debug, Clone)]
pub struct NewEvent {
    pub kind: SessionEventKind,
    pub turn: Option<u32>,
    pub step: Option<u32>,
    pub payload: Value,
}

impl NewEvent {
    #[must_use]
    pub fn new(kind: SessionEventKind) -> Self {
        Self {
            kind,
            turn: None,
            step: None,
            payload: Value::Object(serde_json::Map::new()),
        }
    }

    #[must_use]
    pub fn with_turn(mut self, turn: u32) -> Self {
        self.turn = Some(turn);
        self
    }

    #[must_use]
    pub fn with_step(mut self, step: u32) -> Self {
        self.step = Some(step);
        self
    }

    #[must_use]
    pub fn with_payload(mut self, payload: Value) -> Self {
        self.payload = payload;
        self
    }
}

/// A durable, appended event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionEvent {
    pub id: Uuid,
    pub session_id: SessionId,
    pub seq: u64,
    pub turn: Option<u32>,
    pub step: Option<u32>,
    pub kind: SessionEventKind,
    pub surface: bool,
    pub payload: Value,
    pub created_at: i64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn surface_events_are_model_visible() {
        assert!(SessionEventKind::UserMessage.is_surface());
        assert!(SessionEventKind::AssistantMessage.is_surface());
        assert!(SessionEventKind::ToolResult.is_surface());
        assert!(!SessionEventKind::TurnStart.is_surface());
        assert!(!SessionEventKind::AssistantAttempt.is_surface());
        assert!(!SessionEventKind::RequestContext.is_surface());
    }

    #[test]
    fn event_names_are_stable() {
        assert_eq!(SessionEventKind::TurnStart.as_str(), "turn/start");
        assert_eq!(
            SessionEventKind::CompactionSummary.as_str(),
            "compaction/summary"
        );
    }
}
