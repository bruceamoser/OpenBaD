use serde::{Deserialize, Serialize};

/// Trust state of a workspace.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkspaceTrust {
    #[default]
    Untrusted,
    Trusted,
    Restricted,
}

impl WorkspaceTrust {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Untrusted => "untrusted",
            Self::Trusted => "trusted",
            Self::Restricted => "restricted",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionStatus {
    #[default]
    Active,
    Idle,
    Archived,
    Error,
}

impl SessionStatus {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Idle => "idle",
            Self::Archived => "archived",
            Self::Error => "error",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workspace_trust_is_untrusted_by_default() {
        assert_eq!(WorkspaceTrust::default(), WorkspaceTrust::Untrusted);
    }

    #[test]
    fn session_status_strings_are_stable() {
        assert_eq!(SessionStatus::Active.as_str(), "active");
        assert_eq!(SessionStatus::Error.as_str(), "error");
    }
}
