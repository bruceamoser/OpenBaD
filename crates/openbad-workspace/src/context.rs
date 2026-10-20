//! Context residency rules and negative knowledge.

use std::path::{Path, PathBuf};

use globset::Glob;
use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum ContextError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid context data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextRule {
    pub path: String,
    #[serde(default)]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextPolicy {
    #[serde(default)]
    pub pinned: Vec<ContextRule>,
    #[serde(default)]
    pub excluded: Vec<ContextRule>,
}

impl ContextPolicy {
    #[must_use]
    pub fn is_excluded(&self, path: &str) -> bool {
        let path = path.trim_start_matches("./").replace('\\', "/");
        self.excluded
            .iter()
            .any(|rule| glob_matches(&rule.path, &path))
    }

    #[must_use]
    pub fn is_pinned(&self, path: &str) -> bool {
        let path = path.trim_start_matches("./").replace('\\', "/");
        self.pinned
            .iter()
            .any(|rule| glob_matches(&rule.path, &path))
    }

    pub fn add_excluded(&mut self, path: &str, note: Option<&str>) {
        if !self.excluded.iter().any(|rule| rule.path == path) {
            self.excluded.push(ContextRule {
                path: path.to_owned(),
                note: note.map(str::to_owned),
            });
        }
    }

    pub fn add_pinned(&mut self, path: &str, note: Option<&str>) {
        if !self.pinned.iter().any(|rule| rule.path == path) {
            self.pinned.push(ContextRule {
                path: path.to_owned(),
                note: note.map(str::to_owned),
            });
        }
    }

    pub fn remove(&mut self, path: &str) -> bool {
        let before = self.pinned.len() + self.excluded.len();
        self.pinned.retain(|rule| rule.path != path);
        self.excluded.retain(|rule| rule.path != path);
        before != self.pinned.len() + self.excluded.len()
    }

    #[must_use]
    pub fn summary(&self) -> String {
        let mut summary = String::new();
        if !self.pinned.is_empty() {
            summary.push_str("Pinned context (keep resident):\n");
            for rule in &self.pinned {
                summary.push_str(&format!(
                    "- {}{}\n",
                    rule.path,
                    rule.note
                        .as_deref()
                        .map(|note| format!(" — {note}"))
                        .unwrap_or_default()
                ));
            }
        }
        if !self.excluded.is_empty() {
            summary.push_str("Excluded context (do not read):\n");
            for rule in &self.excluded {
                summary.push_str(&format!(
                    "- {}{}\n",
                    rule.path,
                    rule.note
                        .as_deref()
                        .map(|note| format!(" — {note}"))
                        .unwrap_or_default()
                ));
            }
        }
        summary
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeadEnd {
    pub pattern: String,
    pub reason: String,
    #[serde(default)]
    pub hypothesis: Option<String>,
    #[serde(default)]
    pub created_at: i64,
}

#[must_use]
pub fn context_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("context.yaml")
}

#[must_use]
pub fn dead_ends_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("dead-ends.json")
}

pub fn load_context_policy(root: &Path) -> Result<ContextPolicy, ContextError> {
    let path = context_path(root);
    if !path.is_file() {
        return Ok(ContextPolicy::default());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_yaml::from_str(&content).map_err(|error| ContextError::Invalid(error.to_string()))
}

pub fn save_context_policy(root: &Path, policy: &ContextPolicy) -> Result<(), ContextError> {
    let path = context_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content =
        serde_yaml::to_string(policy).map_err(|error| ContextError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

pub fn load_dead_ends(root: &Path) -> Result<Vec<DeadEnd>, ContextError> {
    let path = dead_ends_path(root);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_json::from_str(&content).map_err(|error| ContextError::Invalid(error.to_string()))
}

pub fn save_dead_ends(root: &Path, dead_ends: &[DeadEnd]) -> Result<(), ContextError> {
    let path = dead_ends_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_string_pretty(dead_ends)
        .map_err(|error| ContextError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

#[must_use]
pub fn dead_end_summary(dead_ends: &[DeadEnd], limit: usize) -> String {
    if dead_ends.is_empty() {
        return String::new();
    }
    let mut summary = String::from("Ruled out (do not re-explore without new evidence):\n");
    for dead_end in dead_ends.iter().take(limit) {
        summary.push_str(&format!(
            "- {}{}: {}\n",
            dead_end.pattern,
            dead_end
                .hypothesis
                .as_deref()
                .map(|hypothesis| format!(" ({hypothesis})"))
                .unwrap_or_default(),
            dead_end.reason
        ));
    }
    summary
}

fn glob_matches(pattern: &str, path: &str) -> bool {
    Glob::new(pattern)
        .ok()
        .map(|glob| glob.compile_matcher().is_match(path))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn context_rules_match_and_round_trip() {
        let directory = tempdir().expect("tempdir");
        let mut policy = ContextPolicy::default();
        policy.add_excluded("assets/**", Some("binary blobs"));
        policy.add_pinned("docs/architecture.md", None);
        assert!(policy.is_excluded("assets/logo.png"));
        assert!(!policy.is_excluded("src/main.rs"));
        assert!(policy.is_pinned("docs/architecture.md"));
        save_context_policy(directory.path(), &policy).expect("save");
        let mut loaded = load_context_policy(directory.path()).expect("load");
        assert_eq!(loaded, policy);
        assert!(loaded.summary().contains("Excluded context"));
        assert!(loaded.remove("assets/**"));
        assert!(!loaded.summary().contains("assets/**"));
    }

    #[test]
    fn dead_ends_round_trip_and_summarize() {
        let directory = tempdir().expect("tempdir");
        let dead_ends = vec![DeadEnd {
            pattern: "src/legacy/**".to_owned(),
            reason: "No parser entry point exists".to_owned(),
            hypothesis: Some("Legacy parser".to_owned()),
            created_at: 1,
        }];
        save_dead_ends(directory.path(), &dead_ends).expect("save");
        let loaded = load_dead_ends(directory.path()).expect("load");
        assert_eq!(loaded, dead_ends);
        let summary = dead_end_summary(&loaded, 5);
        assert!(summary.contains("src/legacy/**"));
        assert!(summary.contains("Legacy parser"));
    }
}
