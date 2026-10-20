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
}
