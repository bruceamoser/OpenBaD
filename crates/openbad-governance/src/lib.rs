//! Capability policy, approvals, audit primitives, and secret storage.

pub mod secrets;

pub use secrets::{MemorySecretStore, Secret, SecretError, SecretStore, provider_key_reference};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// How risky an action is. Approvals are required from `Execute` upward.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RiskLevel {
    Read,
    Write,
    Execute,
    Network,
    Destructive,
}

impl RiskLevel {
    #[must_use]
    pub fn requires_approval(self) -> bool {
        self >= Self::Execute
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalDecision {
    AllowOnce,
    AllowSession,
    AllowWorkspace,
    Deny,
    Cancelled,
    Expired,
}

impl ApprovalDecision {
    #[must_use]
    pub fn is_allowed(self) -> bool {
        matches!(
            self,
            Self::AllowOnce | Self::AllowSession | Self::AllowWorkspace
        )
    }
}

/// How a tool risk is handled without asking.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    Allow,
    Ask,
    Deny,
}

/// The workspace tool policy. Presets are stored per session so a restart does
/// not silently widen access.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ToolPolicy {
    /// Only reads. Writes and commands are denied.
    ReadOnly,
    /// Writes are allowed; commands are denied.
    WritesOnly,
    /// Writes are allowed without asking; writes ask only if the policy says so.
    AskWrites,
    /// Writes are allowed; commands and network access ask first.
    #[default]
    AskCommands,
    /// Everything is allowed without asking.
    FullAccess,
}

impl ToolPolicy {
    #[must_use]
    pub fn from_flags(allow_writes: bool, allow_commands: bool) -> Self {
        if allow_commands {
            Self::FullAccess
        } else if allow_writes {
            Self::WritesOnly
        } else {
            Self::ReadOnly
        }
    }

    #[must_use]
    pub fn access(self, risk: RiskLevel) -> Access {
        match (self, risk) {
            (_, RiskLevel::Read) => Access::Allow,
            (Self::ReadOnly, _) => Access::Deny,
            (Self::WritesOnly, RiskLevel::Write) => Access::Allow,
            (Self::WritesOnly, _) => Access::Deny,
            (Self::AskWrites, RiskLevel::Write) => Access::Ask,
            (Self::AskWrites, _) => Access::Deny,
            (Self::AskCommands, RiskLevel::Write) => Access::Allow,
            (Self::AskCommands, _) => Access::Ask,
            (Self::FullAccess, _) => Access::Allow,
        }
    }

    #[must_use]
    pub fn access_mode(self) -> &'static str {
        match self {
            Self::ReadOnly => "read",
            Self::WritesOnly => "edit",
            Self::AskWrites => "ask-writes",
            Self::AskCommands => "ask-commands",
            Self::FullAccess => "full",
        }
    }

    #[must_use]
    pub fn from_access_mode(mode: &str) -> Option<Self> {
        match mode {
            "read" => Some(Self::ReadOnly),
            "edit" => Some(Self::WritesOnly),
            "ask-writes" => Some(Self::AskWrites),
            "ask-commands" => Some(Self::AskCommands),
            "full" => Some(Self::FullAccess),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub id: Uuid,
    pub risk: RiskLevel,
    pub title: String,
    pub detail: String,
}

impl ApprovalRequest {
    #[must_use]
    pub fn new(risk: RiskLevel, title: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            id: Uuid::now_v7(),
            risk,
            title: title.into(),
            detail: detail.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_do_not_need_approval() {
        assert!(!RiskLevel::Read.requires_approval());
        assert!(!RiskLevel::Write.requires_approval());
        assert!(RiskLevel::Execute.requires_approval());
        assert!(RiskLevel::Destructive.requires_approval());
    }

    #[test]
    fn denial_is_not_allowed() {
        assert!(!ApprovalDecision::Deny.is_allowed());
        assert!(ApprovalDecision::AllowOnce.is_allowed());
    }

    #[test]
    fn tool_policy_matrix_is_monotonic() {
        use Access::{Allow, Ask, Deny};

        assert_eq!(ToolPolicy::ReadOnly.access(RiskLevel::Read), Allow);
        assert_eq!(ToolPolicy::ReadOnly.access(RiskLevel::Write), Deny);

        assert_eq!(ToolPolicy::WritesOnly.access(RiskLevel::Write), Allow);
        assert_eq!(ToolPolicy::WritesOnly.access(RiskLevel::Execute), Deny);

        assert_eq!(ToolPolicy::AskWrites.access(RiskLevel::Write), Ask);
        assert_eq!(ToolPolicy::AskWrites.access(RiskLevel::Execute), Deny);

        assert_eq!(ToolPolicy::AskCommands.access(RiskLevel::Write), Allow);
        assert_eq!(ToolPolicy::AskCommands.access(RiskLevel::Execute), Ask);
        assert_eq!(ToolPolicy::AskCommands.access(RiskLevel::Destructive), Ask);

        assert_eq!(ToolPolicy::FullAccess.access(RiskLevel::Destructive), Allow);
    }

    #[test]
    fn access_modes_round_trip() {
        for policy in [
            ToolPolicy::ReadOnly,
            ToolPolicy::WritesOnly,
            ToolPolicy::AskWrites,
            ToolPolicy::AskCommands,
            ToolPolicy::FullAccess,
        ] {
            assert_eq!(
                ToolPolicy::from_access_mode(policy.access_mode()),
                Some(policy)
            );
        }
        assert_eq!(ToolPolicy::from_access_mode("bogus"), None);
    }
}
