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
    /// How much work the project lets run.
    #[serde(default)]
    pub execution: ExecutionConfig,
    /// The GitHub account every pipeline action acts as.
    ///
    /// Absent means "whatever `gh` is authenticated as", which is the previous
    /// behaviour and is fine for a machine with one account. A named account is
    /// pinned per process instead of being made active, so a person working under
    /// another account at the same time cannot be flipped by either side.
    #[serde(default)]
    pub github: GitHubConfig,
}

/// Which GitHub account the pipeline acts as.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GitHubConfig {
    /// A login that already has a credential in `gh`'s account store. The
    /// pipeline reads that credential; it never runs `gh auth login` or `switch`,
    /// so this can name an account that is not the active one.
    #[serde(default)]
    pub user: Option<String>,
}

/// Concurrency and cost limits for one project's executors.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionConfig {
    /// How many executors may run at once.
    ///
    /// One by default: the pipeline runs unattended, and serialized work is
    /// easier to follow than four tickets moving at once. Raise it to run tickets
    /// concurrently. Zero is refused rather than clamped, because a pipeline that
    /// silently never starts is harder to diagnose than a configuration error.
    #[serde(default = "default_slots")]
    pub slots: u32,
    /// What one ticket may spend.
    #[serde(default)]
    pub budget: BudgetConfig,
    /// Where ticket worktrees are created. A relative path is resolved against the
    /// project; the default keeps them beside the project rather than inside it, so
    /// a worktree never becomes untracked noise in the repository it checks out.
    #[serde(default)]
    pub execution_root: Option<String>,
    /// Run in the new worktree before the executor's turn. Its failure fails the
    /// ticket with the output recorded; without it the worker starts in a checkout
    /// nothing has been done to.
    #[serde(default)]
    pub prepare: Option<String>,
}

/// A ticket's budget, in the project's own terms.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BudgetConfig {
    /// Steps allowed per ticket. `0` means no limit.
    #[serde(default)]
    pub steps: u64,
    /// Tokens allowed per ticket. `0` means no limit.
    #[serde(default)]
    pub tokens: u64,
}

impl Default for ExecutionConfig {
    fn default() -> Self {
        Self {
            slots: default_slots(),
            budget: BudgetConfig::default(),
            execution_root: None,
            prepare: None,
        }
    }
}

impl From<BudgetConfig> for crate::execution::Budget {
    fn from(config: BudgetConfig) -> Self {
        Self {
            steps: config.steps,
            tokens: config.tokens,
        }
    }
}

impl From<ExecutionConfig> for crate::execution::Slots {
    fn from(config: ExecutionConfig) -> Self {
        Self::new(config.slots)
    }
}

const fn default_slots() -> u32 {
    1
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

/// Parses and validates a pipeline configuration without touching the file
/// system, so a surface can refuse to save what the loader would refuse to run.
pub fn parse_pipeline_config(text: &str) -> Result<PipelineConfig, String> {
    let config: PipelineConfig = serde_yaml::from_str(text).map_err(|error| error.to_string())?;
    if config.execution.slots == 0 {
        return Err(
            "execution.slots must be at least 1; disable a source to stop its work".to_owned(),
        );
    }
    for source in &config.sources {
        if source.id.trim().is_empty() {
            return Err("a source needs an id".to_owned());
        }
        if !source.repository.contains('/') {
            return Err(format!(
                "source {} needs a repository as owner/repo, found {:?}",
                source.id, source.repository
            ));
        }
    }
    Ok(config)
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
    parse_pipeline_config(&text)
        .map_err(|error| SourceError::Config(format!("{}: {error}", path.display())))
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

#[cfg(test)]
mod execution_tests {
    use super::*;

    #[test]
    fn a_project_that_says_nothing_runs_one_executor_with_no_budget() {
        let config = PipelineConfig::default();
        assert_eq!(config.execution.slots, 1);
        assert!(crate::execution::Budget::from(config.execution.budget).is_unlimited());
    }

    #[test]
    fn slots_and_budget_are_read_from_the_file() {
        let project = tempfile::tempdir().expect("project");
        let path = pipeline_config_path(project.path());
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "execution:\n  slots: 3\n  budget:\n    steps: 40\n    tokens: 500000\n",
        )
        .expect("write");

        let config = load_pipeline_config(project.path()).expect("load");

        assert_eq!(config.execution.slots, 3);
        assert_eq!(config.execution.budget.steps, 40);
        assert_eq!(config.execution.budget.tokens, 500_000);
    }

    #[test]
    fn zero_slots_is_a_configuration_error_not_a_silent_clamp() {
        let project = tempfile::tempdir().expect("project");
        let path = pipeline_config_path(project.path());
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(&path, "execution:\n  slots: 0\n").expect("write");

        let error = load_pipeline_config(project.path()).expect_err("no slots");

        assert!(error.to_string().contains("at least 1"), "{error}");
    }

    #[test]
    fn an_unknown_execution_key_is_refused_rather_than_ignored() {
        let project = tempfile::tempdir().expect("project");
        let path = pipeline_config_path(project.path());
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(&path, "execution:\n  slot: 3\n").expect("write");

        let error = load_pipeline_config(project.path()).expect_err("typo");

        assert!(error.to_string().contains("pipeline.yaml"), "{error}");
    }
}
