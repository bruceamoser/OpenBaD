//! The pipeline supervisor: one tick of poll → evaluate → admit.
//!
//! The decisions themselves live in `openbad-pipeline`; this is the plumbing that
//! feeds them and records what they said. It deliberately stops short of running
//! an executor: a tick can create a ticket and say why, and the launch is a
//! separate step with its own review.
//!
//! One rule shapes everything here: **the read position advances only past
//! triggers that were fully handled.** A trigger that would need an executor to
//! deliver it is reported as deferred and holds the position, because advancing
//! first would drop a message that nobody would ever learn about.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use openbad_core::WorkspaceId;
use openbad_pipeline::config::{
    PipelineConfig, SourceConfig, load_pipeline_config, pipeline_config_path,
};
use openbad_pipeline::eval::{Decision, DecisionKind, EvalInput, evaluate};
use openbad_pipeline::source::{GhCli, GitHubApi, GitHubSource};
use openbad_store::Store;
use openbad_store::pipeline::{NewAssignment, NewTicket, TicketRecord};
use serde::Serialize;
use tauri::State;

use crate::AppState;

/// What one source did in a tick, and what it chose not to do.
///
/// The counts exist so silence is explainable: "the agent ignored my issue" and
/// "the label filter never fetched it" look identical from the outside.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct SourceTick {
    pub source_id: String,
    pub triggers: usize,
    pub dropped_own_bot: usize,
    pub dropped_duplicate: usize,
    /// The provider or the configuration failed. One source failing does not stop
    /// the others, so this is reported rather than returned.
    pub error: Option<String>,
    /// Whether this source's read position moved. False means a trigger is
    /// waiting for something that does not exist yet.
    pub advanced: bool,
}

/// A decision and what it produced.
#[derive(Debug, Clone, Serialize)]
pub struct Admitted {
    pub source_id: String,
    pub thread: String,
    pub actor: String,
    pub decision: Decision,
    /// The ticket the decision created or reached, when it reached one.
    pub ticket_id: Option<String>,
}

/// Everything one tick did.
#[derive(Debug, Clone, Default, Serialize)]
pub struct TickReport {
    pub sources: Vec<SourceTick>,
    pub admitted: Vec<Admitted>,
    /// Triggers that were judged but not yet deliverable, so the read position
    /// stopped in front of them.
    pub deferred: usize,
    /// Set when no tick ran at all, with the reason.
    pub skipped: Option<String>,
}

impl TickReport {
    /// Whether anything in this tick is worth telling a person about.
    #[must_use]
    pub fn is_quiet(&self) -> bool {
        self.admitted
            .iter()
            .all(|admitted| admitted.decision.kind == DecisionKind::Ignore)
            && self.deferred == 0
            && self.sources.iter().all(|source| source.error.is_none())
    }

    #[must_use]
    pub fn tickets_created(&self) -> usize {
        self.admitted
            .iter()
            .filter(|admitted| admitted.ticket_id.is_some())
            .count()
    }
}

/// Runs the pipeline for one project.
pub struct Supervisor {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
    project_root: std::path::PathBuf,
    policy_version: String,
}

impl Supervisor {
    #[must_use]
    pub fn new(
        store: Arc<Mutex<Store>>,
        workspace_id: WorkspaceId,
        project_root: impl Into<std::path::PathBuf>,
    ) -> Self {
        Self {
            store,
            workspace_id,
            project_root: project_root.into(),
            policy_version: "1".to_owned(),
        }
    }

    /// Reads the project's configuration, or reports why it cannot run.
    pub fn config(&self) -> Result<PipelineConfig, String> {
        load_pipeline_config(&self.project_root).map_err(|error| error.to_string())
    }

    /// Polls every enabled source once and records what the triggers were allowed
    /// to do.
    ///
    /// Generic over the GitHub reader so the whole loop can be tested without a
    /// network or a credential.
    pub async fn tick<A: GitHubApi>(&self, api: &A) -> Result<TickReport, String> {
        let config = self.config()?;
        let mut report = TickReport::default();

        if config.sources.iter().all(|source| !source.enabled) {
            report.skipped = Some("no enabled source".to_owned());
            return Ok(report);
        }

        for source in config.sources.iter().filter(|source| source.enabled) {
            self.tick_source(source, api, &mut report).await;
        }
        Ok(report)
    }

    async fn tick_source<A: GitHubApi>(
        &self,
        source: &SourceConfig,
        api: &A,
        report: &mut TickReport,
    ) {
        let cursor = match self.store.lock() {
            Ok(store) => match store.pipeline_cursor(self.workspace_id, &source.id) {
                Ok(cursor) => cursor,
                Err(error) => {
                    report.sources.push(SourceTick {
                        source_id: source.id.clone(),
                        error: Some(error.to_string()),
                        ..SourceTick::default()
                    });
                    return;
                }
            },
            Err(error) => {
                report.sources.push(SourceTick {
                    source_id: source.id.clone(),
                    error: Some(format!("store lock poisoned: {error}")),
                    ..SourceTick::default()
                });
                return;
            }
        };

        let outcome = GitHubSource::new(source.clone(), api).poll(&cursor).await;
        let outcome = match outcome {
            Ok(outcome) => outcome,
            Err(error) => {
                report.sources.push(SourceTick {
                    source_id: source.id.clone(),
                    error: Some(error.to_string()),
                    ..SourceTick::default()
                });
                return;
            }
        };

        let mut tick = SourceTick {
            source_id: source.id.clone(),
            triggers: outcome.triggers.len(),
            dropped_own_bot: outcome.dropped_own_bot,
            dropped_duplicate: outcome.dropped_duplicate,
            error: None,
            advanced: false,
        };

        // Judged one at a time so a trigger that cannot be handled stops the
        // position at itself rather than at the end of the batch.
        let mut position = cursor.clone();
        let mut handled = 0usize;
        for trigger in &outcome.triggers {
            let thread = match self.thread_context(source, trigger.thread.as_str()) {
                Ok(thread) => thread,
                Err(error) => {
                    tick.error = Some(error);
                    break;
                }
            };
            let decision = evaluate(
                &EvalInput {
                    source,
                    policy_version: &self.policy_version,
                    thread,
                },
                std::slice::from_ref(trigger),
            )
            .into_iter()
            .next()
            .expect("one trigger produces one decision");

            match decision.kind {
                DecisionKind::Accept => match self.admit(source, trigger, &decision) {
                    Ok(ticket) => {
                        report.admitted.push(Admitted {
                            source_id: source.id.clone(),
                            thread: trigger.thread.0.clone(),
                            actor: trigger.actor.clone(),
                            decision,
                            ticket_id: Some(ticket.id),
                        });
                    }
                    Err(error) => {
                        tick.error = Some(error);
                        break;
                    }
                },
                DecisionKind::Ignore => {
                    report.admitted.push(Admitted {
                        source_id: source.id.clone(),
                        thread: trigger.thread.0.clone(),
                        actor: trigger.actor.clone(),
                        decision,
                        ticket_id: None,
                    });
                }
                // A message on a live ticket is delivered by waking its executor:
                // a pending assignment the scheduler will run. Only a stop waits
                // for a person, and it holds the position rather than losing the
                // event nobody acted on.
                DecisionKind::Attach => match self.wake(source, trigger) {
                    Ok(Some(ticket)) => {
                        report.admitted.push(Admitted {
                            source_id: source.id.clone(),
                            thread: trigger.thread.0.clone(),
                            actor: trigger.actor.clone(),
                            decision,
                            ticket_id: Some(ticket.id),
                        });
                    }
                    Ok(None) => {
                        report.admitted.push(Admitted {
                            source_id: source.id.clone(),
                            thread: trigger.thread.0.clone(),
                            actor: trigger.actor.clone(),
                            decision,
                            ticket_id: None,
                        });
                        report.deferred += 1;
                        break;
                    }
                    Err(error) => {
                        tick.error = Some(error);
                        break;
                    }
                },
                DecisionKind::Stop => {
                    report.admitted.push(Admitted {
                        source_id: source.id.clone(),
                        thread: trigger.thread.0.clone(),
                        actor: trigger.actor.clone(),
                        decision,
                        ticket_id: None,
                    });
                    report.deferred += 1;
                    break;
                }
            }
            position.accept(&trigger.id, trigger.at_ms);
            handled += 1;
        }

        // The position moves only when every trigger in the batch was handled.
        // Anything short of that would read past an event nobody delivered.
        let complete = handled == outcome.triggers.len() && tick.error.is_none();
        if complete {
            tick.advanced = true;
            if let Ok(store) = self.store.lock() {
                if let Err(error) =
                    store.save_pipeline_cursor(self.workspace_id, &source.id, &position)
                {
                    tick.error = Some(error.to_string());
                    tick.advanced = false;
                }
            }
        }
        report.sources.push(tick);
    }

    fn thread_context(
        &self,
        source: &SourceConfig,
        thread_key: &str,
    ) -> Result<openbad_pipeline::eval::ThreadContext, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let mut context = store
            .pipeline_thread_context(self.workspace_id, &source.id, thread_key)
            .map_err(|error| error.to_string())?;
        // Only a thread that has a ticket can have spoken: asking for the
        // newest ticket's last turn is what turns "a person replied" into a fact
        // rather than a guess about who the comment's author is.
        if context.has_live_ticket || context.has_ended_ticket || context.stopped_by_person {
            let ticket = store
                .pipeline_ticket_on_thread(self.workspace_id, &source.id, thread_key)
                .map_err(|error| error.to_string())?;
            if let Some(ticket) = ticket {
                context.own_spoke_at_ms = store
                    .pipeline_spoke_at(&ticket.id)
                    .map_err(|error| error.to_string())?;
            }
        }
        Ok(context)
    }

    /// Gives the live ticket on this thread a pending assignment, so the
    /// scheduler picks the message up. One already in flight is not duplicated.
    fn wake(
        &self,
        source: &SourceConfig,
        trigger: &openbad_pipeline::trigger::Trigger,
    ) -> Result<Option<TicketRecord>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let Some(ticket) = store
            .live_pipeline_ticket(self.workspace_id, &source.id, &trigger.thread.0)
            .map_err(|error| error.to_string())?
        else {
            return Ok(None);
        };
        if store
            .active_pipeline_assignment(&ticket.id)
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Ok(Some(ticket));
        }
        store
            .create_pipeline_assignment(NewAssignment {
                workspace_id: self.workspace_id,
                ticket_id: ticket.id.clone(),
                executor: format!("worker-{}", ticket.id),
            })
            .map_err(|error| error.to_string())?;
        Ok(Some(ticket))
    }

    fn admit(
        &self,
        source: &SourceConfig,
        trigger: &openbad_pipeline::trigger::Trigger,
        decision: &Decision,
    ) -> Result<TicketRecord, String> {
        let mandate = decision
            .mandate
            .clone()
            .ok_or_else(|| "an accept without a mandate".to_owned())?;
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .create_pipeline_ticket(NewTicket {
                workspace_id: self.workspace_id,
                source_id: source.id.clone(),
                thread: trigger.thread.clone(),
                trigger_id: trigger.id.clone(),
                mandate,
            })
            .map_err(|error| error.to_string())
    }
}

/// The number of tickets a project has, grouped by state.
///
/// Small enough to live here rather than in the panel: the panel asks, the host
/// answers from rows.
pub fn ticket_counts(
    store: &Store,
    workspace_id: WorkspaceId,
) -> Result<HashMap<String, usize>, String> {
    let tickets = store
        .list_pipeline_tickets(workspace_id, 500)
        .map_err(|error| error.to_string())?;
    let mut counts: HashMap<String, usize> = HashMap::new();
    for ticket in tickets {
        *counts.entry(ticket.state.as_str().to_owned()).or_insert(0) += 1;
    }
    Ok(counts)
}

/// What the panel needs to answer "is this project set up, and what does it see".
///
/// Read-only: it names the sources and counts the tickets, so the setup surface
/// (#958) and the stage list (#959) can render before anything runs.
#[derive(Debug, Clone, Serialize)]
pub struct PipelineStatus {
    /// Whether the project has a `.openbad/pipeline.yaml` at all. A project
    /// without one behaves exactly as it did before ADR-009.
    pub configured: bool,
    pub sources: Vec<SourceStatus>,
    /// Tickets by state, so a stage can be shown without loading the tickets.
    pub tickets: HashMap<String, usize>,
    /// Whether this project should be watched: a person's recorded choice, or —
    /// with none recorded — the configuration's own enabled sources.
    pub watching: bool,
    /// Whether a watcher is actually running right now. `watching` is the intent;
    /// this is the fact, and they differ when a watcher could not be started.
    pub running: bool,
}

/// One configured source, as the panel shows it.
#[derive(Debug, Clone, Serialize)]
pub struct SourceStatus {
    pub id: String,
    pub repository: String,
    pub enabled: bool,
    pub labels: Vec<String>,
    pub poll_interval_secs: u64,
}

impl From<&SourceConfig> for SourceStatus {
    fn from(source: &SourceConfig) -> Self {
        Self {
            id: source.id.clone(),
            repository: source.repository.clone(),
            enabled: source.enabled,
            labels: source.labels.clone(),
            poll_interval_secs: source.poll_interval_secs,
        }
    }
}

/// Reports the project's pipeline configuration and ticket counts.
#[tauri::command]
pub fn pipeline_status(
    state: State<'_, AppState>,
    project_path: String,
) -> Result<PipelineStatus, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = crate::project_workspace(&store, &project_path)?;
    let root = PathBuf::from(&project_path);
    let config = load_pipeline_config(&root).map_err(|error| error.to_string())?;
    let running = state
        .pipeline_watchers
        .lock()
        .map(|watchers| watchers.contains_key(&project_path))
        .unwrap_or(false);
    Ok(describe(
        &config,
        pipeline_config_path(&root).is_file(),
        ticket_counts(&store, workspace)?,
        crate::executor::wants_watching(&store, workspace, &root),
        running,
    ))
}

/// Keeps the shape the panel renders free of the store and the file system, so it
/// can be asserted without either.
fn describe(
    config: &PipelineConfig,
    configured: bool,
    tickets: HashMap<String, usize>,
    watching: bool,
    running: bool,
) -> PipelineStatus {
    PipelineStatus {
        configured,
        sources: config.sources.iter().map(SourceStatus::from).collect(),
        tickets,
        watching,
        running,
    }
}

/// One ticket as the panel lists it.
#[derive(Debug, Clone, Serialize)]
pub struct PipelineTicketRow {
    pub id: String,
    pub source_id: String,
    pub thread: String,
    pub state: &'static str,
    pub stage: openbad_pipeline::stage::Stage,
    pub needs_a_person: bool,
    /// The last report's first line, which is what a row can show.
    pub summary: Option<String>,
    /// The executor's own words, which the row expands to show.
    pub last_words: Option<String>,
    pub outcome: Option<&'static str>,
    pub pull_request: Option<String>,
    pub updated_at: i64,
    /// Why this ticket exists: who asked, under which policy, and which rules
    /// matched.
    pub requester: String,
    pub policy_version: String,
    pub rules: Vec<String>,
}

/// A project's pipeline configuration as the setup surface edits it.
#[derive(Debug, Clone, Serialize)]
pub struct PipelineConfigFile {
    pub path: String,
    pub exists: bool,
    pub text: String,
}

/// Reads `.openbad/pipeline.yaml` verbatim, so the surface edits what is there.
#[tauri::command]
pub fn pipeline_config_file(project_path: String) -> Result<PipelineConfigFile, String> {
    let path = pipeline_config_path(&PathBuf::from(&project_path));
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    Ok(PipelineConfigFile {
        path: path.display().to_string(),
        exists: path.is_file(),
        text,
    })
}

/// Saves a configuration, refusing anything the loader would refuse to run.
///
/// Validating the text and writing it are one step on purpose: a file that saves
/// but does not load would leave a project that looks configured and silently
/// polls nothing.
#[tauri::command]
pub fn pipeline_save_config(project_path: String, text: String) -> Result<(), String> {
    use openbad_pipeline::config::parse_pipeline_config;

    parse_pipeline_config(&text)?;
    let path = pipeline_config_path(&PathBuf::from(&project_path));
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create {}: {error}", parent.display()))?;
    }
    std::fs::write(&path, text).map_err(|error| format!("cannot write {}: {error}", path.display()))
}

/// Whether a repository's branch protection is real enough to allow auto-merge.
///
/// The rules live in `openbad-pipeline` so the panel and an agent asked to
/// configure the project get the same answer.
#[tauri::command]
pub async fn pipeline_guards(
    repository: String,
) -> Result<openbad_pipeline::guards::Guards, String> {
    let api = GhCli;
    let repo: serde_json::Value = api
        .get(&format!("/repos/{repository}"))
        .await
        .map_err(|error| error.to_string())?;
    let branch = repo
        .get("default_branch")
        .and_then(|value| value.as_str())
        .unwrap_or("main")
        .to_owned();

    let protection = api
        .get(&format!("/repos/{repository}/branches/{branch}/protection"))
        .await
        .ok();
    Ok(openbad_pipeline::guards::guards_from(
        &branch,
        protection.as_ref(),
    ))
}

/// Lists the project's tickets with the stage derived from their rows.
#[tauri::command]
pub fn pipeline_tickets(
    state: State<'_, AppState>,
    project_path: String,
) -> Result<Vec<PipelineTicketRow>, String> {
    use openbad_pipeline::stage::{StageInput, stage_of};

    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = crate::project_workspace(&store, &project_path)?;
    let tickets = store
        .list_pipeline_tickets(workspace, 500)
        .map_err(|error| error.to_string())?;

    let mut rows = Vec::with_capacity(tickets.len());
    for ticket in tickets {
        let assignment = store
            .active_pipeline_assignment(&ticket.id)
            .map_err(|error| error.to_string())?;
        let report = store
            .pipeline_report(&ticket.id)
            .map_err(|error| error.to_string())?;
        let attention = store
            .pipeline_open_attention(&ticket.id)
            .map_err(|error| error.to_string())?;
        let stage = stage_of(StageInput {
            ticket: ticket.state,
            assignment: assignment.as_ref().map(|record| record.state),
            report: report.as_ref().map(|record| record.outcome),
            attention,
            // A delivery failure is not modelled yet; when it is, it arrives here.
            delivery_failed: false,
        });

        rows.push(PipelineTicketRow {
            id: ticket.id,
            source_id: ticket.source_id,
            thread: ticket.thread.0,
            state: ticket.state.as_str(),
            stage,
            needs_a_person: stage.needs_a_person(),
            summary: report.as_ref().map(|record| {
                record
                    .last_words
                    .lines()
                    .next()
                    .unwrap_or_default()
                    .chars()
                    .take(160)
                    .collect()
            }),
            last_words: report.as_ref().map(|record| record.last_words.clone()),
            outcome: report.as_ref().map(|record| record.outcome.as_str()),
            pull_request: report
                .as_ref()
                .and_then(|record| record.pull_request.clone()),
            updated_at: ticket.updated_at,
            requester: ticket.mandate.requester,
            policy_version: ticket.mandate.policy_version,
            rules: ticket.mandate.matched_rules,
        });
    }
    Ok(rows)
}

/// Polls every enabled source once and records what its triggers were allowed to
/// do.
///
/// The read position advances only past triggers this tick fully handled, so a
/// trigger that needs an executor is reported as deferred and stops the source's
/// position in front of itself. That is the point: a later slice can deliver it,
/// and nothing was read past in the meantime.
#[tauri::command]
pub async fn pipeline_tick(
    state: State<'_, AppState>,
    project_path: String,
) -> Result<TickReport, String> {
    // The guard must not outlive this block: the tick takes the store as it
    // goes, and holding it across the await would deadlock the first read.
    let workspace = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        crate::project_workspace(&store, &project_path)?
    };

    let supervisor = Supervisor::new(Arc::clone(&state.store), workspace, project_path.as_str());
    let report = supervisor.tick(&GhCli).await?;
    // A quiet tick is the normal case and stays out of the log; anything a person
    // would want to explain later is written down.
    if !report.is_quiet() {
        crate::diagnostics::record(
            "INFO",
            &format!(
                "pipeline tick: {} tickets created, {} deferred, {} sources polled",
                report.tickets_created(),
                report.deferred,
                report.sources.len()
            ),
        );
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use openbad_pipeline::error::SourceError;
    use openbad_store::types::NewWorkspace;
    use uuid::Uuid;

    /// A GitHub that answers from a table of path prefixes, most specific first.
    ///
    /// Order matters: `/repos/o/r/issues/comments` also starts with
    /// `/repos/o/r/issues`, so a coarse table would hand an issue row to the
    /// comments endpoint and the failure would look like a missing field rather
    /// than a fixture mistake.
    struct FakeGitHub {
        bodies: Vec<(&'static str, serde_json::Value)>,
    }

    impl FakeGitHub {
        fn new(mut bodies: Vec<(&'static str, serde_json::Value)>) -> Self {
            bodies.sort_by_key(|(prefix, _)| std::cmp::Reverse(prefix.len()));
            Self { bodies }
        }
    }

    #[async_trait]
    impl GitHubApi for FakeGitHub {
        async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError> {
            for (prefix, body) in &self.bodies {
                if path.starts_with(prefix) {
                    return Ok(body.clone());
                }
            }
            Ok(serde_json::json!([]))
        }
    }

    fn project_with_source() -> tempfile::TempDir {
        let project = tempfile::tempdir().expect("project");
        let path = project.path().join(".openbad/pipeline.yaml");
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "sources:\n  - id: main\n    repository: o/r\n    labels: [\"agent:run\"]\n    allowed: [\"alice\"]\n",
        )
        .expect("config");
        project
    }

    fn issue(number: u64, labels: &[&str], user: &str, updated: &str) -> serde_json::Value {
        serde_json::json!({
            "number": number,
            "title": "do the thing",
            "body": "please",
            "html_url": format!("https://github.com/o/r/issues/{number}"),
            "updated_at": updated,
            "user": { "login": user },
            "labels": labels
                .iter()
                .map(|label| serde_json::json!({ "name": label }))
                .collect::<Vec<_>>(),
        })
    }

    fn supervisor(project: &tempfile::TempDir) -> (Supervisor, Arc<Mutex<Store>>, WorkspaceId) {
        let store = Arc::new(Mutex::new(Store::open_in_memory().expect("store")));
        let workspace = {
            let store = store.lock().expect("lock");
            store
                .create_workspace_record(NewWorkspace::new(
                    "demo",
                    format!("/tmp/{}", Uuid::now_v7()),
                ))
                .expect("workspace")
        };
        (
            Supervisor::new(Arc::clone(&store), workspace, project.path()),
            store,
            workspace,
        )
    }

    #[tokio::test]
    async fn a_labelled_issue_from_an_allowed_person_becomes_a_ticket() {
        let project = project_with_source();
        let (supervisor, store, workspace) = supervisor(&project);
        let api = FakeGitHub::new(vec![
            ("/repos/o/r/issues/comments", serde_json::json!([])),
            ("/repos/o/r/pulls", serde_json::json!([])),
            (
                "/repos/o/r/issues",
                serde_json::json!([issue(1, &["agent:run"], "alice", "2026-11-04T12:00:00Z")]),
            ),
        ]);

        let report = supervisor.tick(&api).await.expect("tick");

        assert_eq!(report.tickets_created(), 1, "{report:#?}");
        assert!(report.sources[0].advanced);
        let tickets = store
            .lock()
            .expect("lock")
            .list_pipeline_tickets(workspace, 10)
            .expect("tickets");
        assert_eq!(tickets.len(), 1);
        assert_eq!(tickets[0].thread.as_str(), "o/r#1");
        assert_eq!(tickets[0].mandate.requester, "alice");
    }

    #[tokio::test]
    async fn an_unlabelled_issue_is_judged_and_recorded_as_ignored() {
        let project = project_with_source();
        let (supervisor, store, workspace) = supervisor(&project);
        // The label filter travels in the query, so an unlabelled issue never
        // reaches the pipeline at all; this asserts the reason is visible when a
        // comment does.
        let api = FakeGitHub::new(vec![(
            "/repos/o/r/issues/comments",
            serde_json::json!([{
                "id": 7,
                "body": "hello",
                "created_at": "2026-11-04T12:00:00Z",
                "updated_at": "2026-11-04T12:00:00Z",
                "user": { "login": "mallory" },
                "issue_url": "https://api.github.com/repos/o/r/issues/9",
                "html_url": "https://github.com/o/r/issues/9#issuecomment-7"
            }]),
        )]);

        let report = supervisor.tick(&api).await.expect("tick");

        assert_eq!(report.tickets_created(), 0);
        assert_eq!(report.admitted.len(), 1);
        assert_eq!(report.admitted[0].decision.kind, DecisionKind::Ignore);
        assert!(
            report.admitted[0].decision.reason.contains("allowed list"),
            "{}",
            report.admitted[0].decision.reason
        );
        assert_eq!(
            store
                .lock()
                .expect("lock")
                .list_pipeline_tickets(workspace, 10)
                .expect("tickets")
                .len(),
            0
        );
    }

    #[tokio::test]
    async fn a_tick_with_nothing_new_advances_the_read_position() {
        let project = project_with_source();
        let (supervisor, store, workspace) = supervisor(&project);
        let api = FakeGitHub::new(Vec::new());

        let first = supervisor.tick(&api).await.expect("first");
        let second = supervisor.tick(&api).await.expect("second");

        assert!(first.sources[0].advanced);
        assert!(second.is_quiet());
        let cursor = store
            .lock()
            .expect("lock")
            .pipeline_cursor(workspace, "main")
            .expect("cursor");
        assert_eq!(cursor.emitted.len(), 0, "nothing was admitted");
    }

    #[tokio::test]
    async fn a_disabled_source_is_not_polled() {
        let project = tempfile::tempdir().expect("project");
        let path = project.path().join(".openbad/pipeline.yaml");
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "sources:\n  - id: main\n    repository: o/r\n    enabled: false\n    labels: [\"agent:run\"]\n",
        )
        .expect("config");
        let (supervisor, _, _) = supervisor(&project);

        let report = supervisor
            .tick(&FakeGitHub::new(Vec::new()))
            .await
            .expect("tick");

        assert_eq!(report.skipped.as_deref(), Some("no enabled source"));
    }

    #[tokio::test]
    async fn a_project_without_a_pipeline_file_does_nothing() {
        let project = tempfile::tempdir().expect("project");
        let (supervisor, _, _) = supervisor(&project);

        let report = supervisor
            .tick(&FakeGitHub::new(Vec::new()))
            .await
            .expect("tick");

        assert_eq!(report.skipped.as_deref(), Some("no enabled source"));
    }

    #[tokio::test]
    async fn a_message_on_a_live_ticket_wakes_its_executor() {
        let project = project_with_source();
        let (supervisor, store, workspace) = supervisor(&project);
        let first = FakeGitHub::new(vec![
            ("/repos/o/r/issues/comments", serde_json::json!([])),
            ("/repos/o/r/pulls", serde_json::json!([])),
            (
                "/repos/o/r/issues",
                serde_json::json!([issue(1, &["agent:run"], "alice", "2026-11-04T12:00:00Z")]),
            ),
        ]);
        supervisor.tick(&first).await.expect("admit");

        let comment = FakeGitHub::new(vec![(
            "/repos/o/r/issues/comments",
            serde_json::json!([{
                "id": 8,
                "body": "and also this",
                "created_at": "2026-11-04T13:00:00Z",
                "updated_at": "2026-11-04T13:00:00Z",
                "user": { "login": "alice" },
                "issue_url": "https://api.github.com/repos/o/r/issues/1",
                "html_url": "https://github.com/o/r/issues/1#issuecomment-8"
            }]),
        )]);
        let report = supervisor.tick(&comment).await.expect("attach");
        let after = supervisor.tick(&comment).await.expect("again");

        assert_eq!(report.deferred, 0);
        assert_eq!(report.admitted[0].decision.kind, DecisionKind::Attach);
        assert!(
            report.sources[0].advanced,
            "a message that reached its executor is delivered, not re-read"
        );
        assert_eq!(
            after.sources[0].triggers, 0,
            "the position moved past the message"
        );
        let tickets = store
            .lock()
            .expect("lock")
            .list_pipeline_tickets(workspace, 10)
            .expect("tickets");
        assert_eq!(tickets.len(), 1, "no second ticket on the same thread");
        let assignment = store
            .lock()
            .expect("lock")
            .active_pipeline_assignment(&tickets[0].id)
            .expect("assignment")
            .expect("assignment");
        assert_eq!(
            assignment.state,
            openbad_pipeline::ticket::AssignmentState::Pending,
            "the wake is a pending assignment the scheduler runs"
        );
    }

    #[tokio::test]
    async fn one_sources_message_does_not_stop_another_source_from_advancing() {
        let project = tempfile::tempdir().expect("project");
        let path = project.path().join(".openbad/pipeline.yaml");
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "sources:\n  - id: first\n    repository: o/first\n    labels: [\"agent:run\"]\n    allowed: [\"alice\"]\n  - id: second\n    repository: o/second\n    labels: [\"agent:run\"]\n    allowed: [\"alice\"]\n",
        )
        .expect("config");
        let (supervisor, store, workspace) = supervisor(&project);
        let admit = FakeGitHub::new(vec![
            ("/repos/o/first/issues/comments", serde_json::json!([])),
            ("/repos/o/first/pulls", serde_json::json!([])),
            (
                "/repos/o/first/issues",
                serde_json::json!([issue(1, &["agent:run"], "alice", "2026-11-04T12:00:00Z")]),
            ),
            ("/repos/o/second/issues/comments", serde_json::json!([])),
            ("/repos/o/second/pulls", serde_json::json!([])),
            (
                "/repos/o/second/issues",
                serde_json::json!([issue(2, &["agent:run"], "alice", "2026-11-04T12:00:00Z")]),
            ),
        ]);
        supervisor.tick(&admit).await.expect("admit both");

        // A message on the first source's live ticket wakes its executor. The
        // second source has nothing waiting and must still advance: one source
        // never holds another back.
        let mixed = FakeGitHub::new(vec![
            (
                "/repos/o/first/issues/comments",
                serde_json::json!([{
                    "id": 9,
                    "body": "more",
                    "updated_at": "2026-11-04T13:00:00Z",
                    "user": { "login": "alice" },
                    "issue_url": "https://api.github.com/repos/o/first/issues/1"
                }]),
            ),
            ("/repos/o/second/issues/comments", serde_json::json!([])),
            ("/repos/o/first/pulls", serde_json::json!([])),
            ("/repos/o/second/pulls", serde_json::json!([])),
        ]);
        let report = supervisor.tick(&mixed).await.expect("mixed");

        let first = report
            .sources
            .iter()
            .find(|source| source.source_id == "first")
            .expect("first");
        let second = report
            .sources
            .iter()
            .find(|source| source.source_id == "second")
            .expect("second");
        assert!(first.advanced, "the message was delivered to its executor");
        assert!(second.advanced, "the other source is unaffected");
        assert_eq!(
            store
                .lock()
                .expect("lock")
                .list_pipeline_tickets(workspace, 10)
                .expect("tickets")
                .len(),
            2
        );
    }

    #[tokio::test]
    async fn one_source_failing_does_not_stop_the_others() {
        let project = tempfile::tempdir().expect("project");
        let path = project.path().join(".openbad/pipeline.yaml");
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "sources:\n  - id: broken\n    repository: o/broken\n    labels: [\"agent:run\"]\n  - id: healthy\n    repository: o/healthy\n    labels: [\"agent:run\"]\n    allowed: [\"alice\"]\n",
        )
        .expect("config");
        let (supervisor, store, workspace) = supervisor(&project);

        struct HalfBroken;
        #[async_trait]
        impl GitHubApi for HalfBroken {
            async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError> {
                if path.starts_with("/repos/o/broken") {
                    return Err(SourceError::Api("rate limited".to_owned()));
                }
                if path.starts_with("/repos/o/healthy/issues?") {
                    return Ok(serde_json::json!([issue(
                        4,
                        &["agent:run"],
                        "alice",
                        "2026-11-04T12:00:00Z"
                    )]));
                }
                Ok(serde_json::json!([]))
            }
        }

        let report = supervisor.tick(&HalfBroken).await.expect("tick");

        assert_eq!(report.sources.len(), 2);
        assert!(report.sources[0].error.is_some());
        assert_eq!(report.sources[1].error, None);
        assert_eq!(
            store
                .lock()
                .expect("lock")
                .list_pipeline_tickets(workspace, 10)
                .expect("tickets")
                .len(),
            1,
            "the healthy source still admitted its issue"
        );
    }

    #[test]
    fn status_keeps_a_disabled_source_so_its_position_survives_a_toggle() {
        let project = tempfile::tempdir().expect("project");
        let path = project.path().join(".openbad/pipeline.yaml");
        std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
        std::fs::write(
            &path,
            "sources:\n  - id: on\n    repository: o/on\n    labels: [\"agent:run\"]\n  - id: off\n    repository: o/off\n    enabled: false\n    poll_interval_secs: 30\n",
        )
        .expect("config");

        let config = load_pipeline_config(project.path()).expect("config");
        let status = describe(
            &config,
            pipeline_config_path(project.path()).is_file(),
            HashMap::from([("open".to_owned(), 2usize)]),
            true,
            false,
        );

        assert_eq!(
            status
                .sources
                .iter()
                .map(|source| (
                    source.id.as_str(),
                    source.enabled,
                    source.poll_interval_secs
                ))
                .collect::<Vec<_>>(),
            vec![("on", true, 120), ("off", false, 30)],
            "a disabled source stays listed at its own cadence"
        );
        assert_eq!(status.tickets.get("open"), Some(&2));
        assert!(status.configured);
        assert!(
            !status.running,
            "intent and a live watcher are reported separately"
        );
    }
}
