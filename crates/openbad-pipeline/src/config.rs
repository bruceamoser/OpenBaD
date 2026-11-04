//! Pipeline configuration, read from a project's `.openbad/pipeline.yaml`.
//!
//! The pipeline is per project and opt-in: a project without this file behaves
//! exactly as it did before ADR-009.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::SourceError;

/// Where a project's pipeline configuration lives, relative to its root.
pub const PIPELINE_CONFIG_PATH: &str = ".openbad/pipeline.yaml";

/// The whole pipeline configuration for one project.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PipelineConfig {
    /// One entry per source instance. A disabled entry is kept, not removed, so
    /// turning a source back on does not lose its read position.
    #[serde(default)]
    pub sources: Vec<SourceConfig>,
}

/// One configured source: a repository plus what to listen to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceConfig {
    /// Stable identifier used in trigger ids and read positions.
    pub id: String,
    /// `owner/repo`.
    pub repository: String,
    /// Whether this source polls at all.
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    /// Triggers must carry at least one of these labels to enter the domain. An
    /// empty list means the filter admits nothing, which is the safe default: a
    /// project must name what it wants the agent to pick up.
    #[serde(default)]
    pub labels: Vec<String>,
    /// GitHub logins permitted to originate work. Carried here because the
    /// allowed list is a source's part of the policy, but **enforced by Eval**,
    /// not by this crate: a trigger from someone else is still recorded, so the
    /// decision to ignore it is visible rather than silent.
    #[serde(default)]
    pub allowed: Vec<String>,
    /// Accounts whose activity the source drops outright — the agent's own.
    #[serde(default)]
    pub bot_logins: Vec<String>,
    /// Bots whose proposals are **candidates**. A known bot can start work; it can
    /// never claim that a person asked, so its mandate is `inferred`. Any other
    /// bot is not admitted at all.
    #[serde(default)]
    pub known_bots: Vec<String>,
    /// How often to poll.
    #[serde(default = "default_poll_seconds")]
    pub poll_interval_secs: u64,
}

const fn default_enabled() -> bool {
    true
}

const fn default_poll_seconds() -> u64 {
    120
}

impl SourceConfig {
    /// Whether a label set admits this trigger.
    ///
    /// Matching is case-insensitive because GitHub labels are typed by hand.
    #[must_use]
    pub fn admits(&self, labels: &[String]) -> bool {
        if self.labels.is_empty() {
            return false;
        }
        labels.iter().any(|label| {
            self.labels
                .iter()
                .any(|wanted| wanted.eq_ignore_ascii_case(label))
        })
    }

    /// Whether this login is the agent's own.
    #[must_use]
    pub fn is_own_bot(&self, login: &str) -> bool {
        self.bot_logins
            .iter()
            .any(|bot| bot.eq_ignore_ascii_case(login))
    }

    /// Whether this login may propose work as a candidate.
    #[must_use]
    pub fn is_bot_known(&self, login: &str) -> bool {
        self.known_bots
            .iter()
            .any(|bot| bot.eq_ignore_ascii_case(login))
    }

    /// Whether this login may originate or continue work.
    ///
    /// A known bot is not "allowed" in this sense: allowed means a person whose
    /// request is explicit, and mixing the two would let a bot inherit a person's
    /// authority.
    #[must_use]
    pub fn is_allowed(&self, login: &str) -> bool {
        self.allowed
            .iter()
            .any(|person| person.eq_ignore_ascii_case(login))
    }
}

#[must_use]
pub fn pipeline_config_path(project_root: &Path) -> PathBuf {
    project_root.join(PIPELINE_CONFIG_PATH)
}

/// Reads a project's pipeline configuration.
///
/// A missing file is an empty configuration, not an error: the pipeline is
/// opt-in. A malformed file is an error, because silently running with no
/// sources would look like a working pipeline that never picks anything up.
pub fn load_pipeline_config(project_root: &Path) -> Result<PipelineConfig, SourceError> {
    let path = pipeline_config_path(project_root);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(PipelineConfig::default());
        }
        Err(error) => {
            return Err(SourceError::Config(format!("{}: {error}", path.display())));
        }
    };
    let config: PipelineConfig = serde_yaml::from_str(&text)
        .map_err(|error| SourceError::Config(format!("{}: {error}", path.display())))?;
    for source in &config.sources {
        if source.id.trim().is_empty() {
            return Err(SourceError::Config(format!(
                "{}: a source needs an id",
                path.display()
            )));
        }
        if !source.repository.contains('/') {
            return Err(SourceError::Config(format!(
                "{}: source {} needs a repository as owner/repo, found {:?}",
                path.display(),
                source.id,
                source.repository
            )));
        }
    }
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(project: &Path, body: &str) {
        let path = pipeline_config_path(project);
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(path, body).expect("write");
    }

    #[test]
    fn a_project_without_the_file_has_no_pipeline() {
        let project = tempfile::tempdir().expect("project");
        let config = load_pipeline_config(project.path()).expect("load");
        assert!(config.sources.is_empty());
    }

    #[test]
    fn a_malformed_file_is_an_error_rather_than_an_empty_pipeline() {
        let project = tempfile::tempdir().expect("project");
        write(project.path(), "sources: [ { id: x }");
        let error = load_pipeline_config(project.path()).expect_err("malformed");
        assert!(error.to_string().contains("pipeline.yaml"), "{error}");
    }

    #[test]
    fn a_source_needs_an_id_and_an_owner_repo() {
        let project = tempfile::tempdir().expect("project");
        write(
            project.path(),
            "sources:\n  - id: main\n    repository: not-a-repo\n",
        );
        let error = load_pipeline_config(project.path()).expect_err("bad repo");
        assert!(error.to_string().contains("owner/repo"), "{error}");
    }

    #[test]
    fn the_label_filter_admits_only_what_it_names() {
        let project = tempfile::tempdir().expect("project");
        write(
            project.path(),
            "sources:\n  - id: main\n    repository: o/r\n    labels: [\"agent:run\"]\n",
        );
        let config = load_pipeline_config(project.path()).expect("load");
        let source = &config.sources[0];
        assert!(source.enabled, "defaults to enabled");
        assert_eq!(source.poll_interval_secs, 120, "defaults to 120s");
        assert!(source.admits(&["AGENT:RUN".to_owned()]), "case-insensitive");
        assert!(!source.admits(&["bug".to_owned()]));
        assert!(!source.admits(&[]), "no labels admits nothing");
    }

    #[test]
    fn a_source_with_no_labels_admits_nothing() {
        let project = tempfile::tempdir().expect("project");
        write(
            project.path(),
            "sources:\n  - id: main\n    repository: o/r\n",
        );
        let config = load_pipeline_config(project.path()).expect("load");
        assert!(!config.sources[0].admits(&["bug".to_owned()]));
    }
}
