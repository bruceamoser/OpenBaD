//! Per-project permission policy (`.openbad/permissions.yaml`).

use std::path::{Path, PathBuf};

use globset::Glob;
use serde::{Deserialize, Serialize};

const COMMAND_CLASSES: [&str; 5] = [
    "read-only",
    "write-workspace",
    "network",
    "destructive",
    "privileged",
];

#[derive(Debug, thiserror::Error)]
pub enum PolicyError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid project policy: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectPolicy {
    #[serde(default)]
    pub allow_writes: Option<bool>,
    #[serde(default)]
    pub allow_paths: Vec<String>,
    #[serde(default)]
    pub deny_paths: Vec<String>,
    #[serde(default)]
    pub command_classes: Vec<String>,
    #[serde(default)]
    pub deny_command_patterns: Vec<String>,
    #[serde(default)]
    pub allow_network: Option<bool>,
    #[serde(default)]
    pub network_hosts: Vec<String>,
}

impl ProjectPolicy {
    #[must_use]
    pub fn write_denied(&self, path: &str) -> Option<String> {
        let path = path.trim_start_matches("./").replace('\\', "/");
        if self.allow_writes == Some(false) {
            return Some(format!(
                "policy .openbad/permissions.yaml sets allow_writes: false (write denied for {path})"
            ));
        }
        if self
            .deny_paths
            .iter()
            .any(|pattern| glob_matches(pattern, &path))
        {
            return Some(format!(
                "policy .openbad/permissions.yaml deny_paths blocks {path}"
            ));
        }
        if !self.allow_paths.is_empty()
            && !self
                .allow_paths
                .iter()
                .any(|pattern| glob_matches(pattern, &path))
        {
            return Some(format!(
                "policy .openbad/permissions.yaml allow_paths does not include {path}"
            ));
        }
        None
    }

    #[must_use]
    pub fn network_denied(&self, host: &str) -> Option<String> {
        if self.allow_network == Some(false) {
            return Some("policy .openbad/permissions.yaml sets allow_network: false".to_owned());
        }
        if !self.network_hosts.is_empty()
            && !self.network_hosts.iter().any(|allowed| {
                host.eq_ignore_ascii_case(allowed)
                    || host
                        .to_ascii_lowercase()
                        .ends_with(&format!(".{}", allowed.to_ascii_lowercase()))
            })
        {
            return Some(format!(
                "policy .openbad/permissions.yaml network_hosts does not allow {host}; allowed: {}",
                self.network_hosts.join(", ")
            ));
        }
        None
    }

    #[must_use]
    pub fn command_denied(&self, class: &str, command: &str) -> Option<String> {
        if !self.command_classes.is_empty()
            && !self.command_classes.iter().any(|allowed| allowed == class)
        {
            return Some(format!(
                "policy .openbad/permissions.yaml allows command classes [{}]; {class} is not permitted",
                self.command_classes.join(", ")
            ));
        }
        let lower = command.to_ascii_lowercase();
        if let Some(pattern) = self
            .deny_command_patterns
            .iter()
            .find(|pattern| lower.contains(&pattern.to_ascii_lowercase()))
        {
            return Some(format!(
                "policy .openbad/permissions.yaml deny_command_patterns blocks `{pattern}`"
            ));
        }
        None
    }
}

#[must_use]
pub fn policy_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("permissions.yaml")
}

pub fn load_project_policy(root: &Path) -> Result<Option<ProjectPolicy>, PolicyError> {
    let path = policy_path(root);
    if !path.is_file() {
        return Ok(None);
    }
    let content = std::fs::read_to_string(&path)?;
    let policy: ProjectPolicy =
        serde_yaml::from_str(&content).map_err(|error| PolicyError::Invalid(error.to_string()))?;
    for class in &policy.command_classes {
        if !COMMAND_CLASSES.contains(&class.as_str()) {
            return Err(PolicyError::Invalid(format!(
                "unknown command class `{class}`; expected one of {}",
                COMMAND_CLASSES.join(", ")
            )));
        }
    }
    for pattern in policy
        .allow_paths
        .iter()
        .chain(policy.deny_paths.iter())
        .chain(policy.deny_command_patterns.iter())
    {
        if pattern.trim().is_empty() {
            return Err(PolicyError::Invalid(
                "policy patterns must not be empty".to_owned(),
            ));
        }
    }
    Ok(Some(policy))
}

fn glob_matches(pattern: &str, path: &str) -> bool {
    Glob::new(pattern)
        .ok()
        .map(|glob| glob.compile_matcher().is_match(path))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn parses_and_enforces_write_and_command_rules() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(
            directory.path().join(".openbad/permissions.yaml"),
            r#"
allow_writes: true
deny_paths: ["archive/**", ".env*"]
allow_paths: []
command_classes: ["read-only", "write-workspace"]
deny_command_patterns: ["--force"]
"#,
        )
        .expect("policy");
        let policy = load_project_policy(directory.path())
            .expect("load")
            .expect("present");
        assert!(policy.write_denied("src/main.rs").is_none());
        assert!(policy.write_denied("archive/old.md").is_some());
        assert!(policy.write_denied(".env.local").is_some());
        assert!(policy.command_denied("read-only", "ls").is_none());
        assert!(
            policy
                .command_denied("network", "curl example.com")
                .is_some()
        );
        assert!(
            policy
                .command_denied("write-workspace", "git push --force")
                .is_some()
        );
        assert!(policy.network_denied("docs.rs").is_none());
    }

    #[test]
    fn network_policy_enforces_allowlist_and_kill_switch() {
        let mut policy = ProjectPolicy {
            network_hosts: vec!["docs.rs".to_owned(), "example.com".to_owned()],
            ..ProjectPolicy::default()
        };
        assert!(policy.network_denied("docs.rs").is_none());
        assert!(policy.network_denied("api.docs.rs").is_none());
        assert!(policy.network_denied("evil.test").is_some());
        policy.allow_network = Some(false);
        assert!(policy.network_denied("docs.rs").is_some());
    }

    #[test]
    fn rejects_unknown_command_classes() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(
            directory.path().join(".openbad/permissions.yaml"),
            "command_classes: [\"everything\"]\n",
        )
        .expect("policy");
        assert!(load_project_policy(directory.path()).is_err());
    }

    #[test]
    fn allow_paths_acts_as_an_allowlist() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(
            directory.path().join(".openbad/permissions.yaml"),
            "allow_paths: [\"src/**\"]\n",
        )
        .expect("policy");
        let policy = load_project_policy(directory.path())
            .expect("load")
            .expect("present");
        assert!(policy.write_denied("src/main.rs").is_none());
        assert!(policy.write_denied("docs/readme.md").is_some());
    }
}
