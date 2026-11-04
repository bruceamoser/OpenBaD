//! What a repository's branch protection has to be before auto-merge may exist.
//!
//! ADR-009 makes branch protection the only thing between a large unreviewed
//! change and `main`, because auto-merge has no line cap. So the rule is a pure
//! function of the protection payload: the same answer for the panel that refuses
//! the switch and for an agent that is asked to configure the project.

use serde::Serialize;

/// Whether a branch is protected well enough, and what is missing if not.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Guards {
    pub branch: String,
    pub protected: bool,
    /// The specific settings that are absent, named so a person can fix them.
    pub missing: Vec<String>,
}

impl Guards {
    /// Whether auto-merge may be offered.
    #[must_use]
    pub fn satisfied(&self) -> bool {
        self.protected && self.missing.is_empty()
    }
}

/// The guard rules, from the payload of the branch-protection endpoint.
///
/// `None` means the endpoint answered nothing usable — not protected, or not
/// visible — which is treated as no protection at all rather than as consent.
#[must_use]
pub fn guards_from(branch: &str, protection: Option<&serde_json::Value>) -> Guards {
    let mut missing = Vec::new();
    let reviews = protection.and_then(|value| value.get("required_pull_request_reviews"));
    let approvals = reviews
        .and_then(|value| value.get("required_approving_review_count"))
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0);
    if approvals < 1 {
        missing.push("required approving reviews (at least 1)".to_owned());
    }
    let dismiss_stale = reviews
        .and_then(|value| value.get("dismiss_stale_reviews"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    if !dismiss_stale {
        missing.push("dismiss stale approvals when new commits arrive".to_owned());
    }
    let checks = protection
        .and_then(|value| value.get("required_status_checks"))
        .and_then(|value| {
            value
                .get("checks")
                .and_then(|checks| checks.as_array())
                .map(|checks| !checks.is_empty())
                .or_else(|| {
                    value
                        .get("contexts")
                        .and_then(|contexts| contexts.as_array())
                        .map(|contexts| !contexts.is_empty())
                })
        })
        .unwrap_or(false);
    if !checks {
        missing.push("required status checks".to_owned());
    }

    Guards {
        branch: branch.to_owned(),
        protected: protection.is_some(),
        missing,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_missing_setting_is_named_and_only_a_real_rule_passes() {
        let unprotected = guards_from("main", None);
        let partial = guards_from(
            "main",
            Some(&serde_json::json!({
                "required_pull_request_reviews": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews": false
                },
                "required_status_checks": { "contexts": ["ci"] }
            })),
        );
        let complete = guards_from(
            "main",
            Some(&serde_json::json!({
                "required_pull_request_reviews": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews": true
                },
                "required_status_checks": { "checks": [{ "context": "ci" }] }
            })),
        );

        assert_eq!(
            (
                unprotected.protected,
                unprotected.satisfied(),
                unprotected.missing.len(),
                partial.missing.clone(),
                complete.protected,
                complete.satisfied(),
                complete.missing.clone(),
            ),
            (
                false,
                false,
                3,
                vec!["dismiss stale approvals when new commits arrive".to_owned()],
                true,
                true,
                Vec::<String>::new(),
            )
        );
    }
}
