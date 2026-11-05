//! The launch half of the pipeline executor.
//!
//! A ticket becomes an assignment here: one worktree, one branch, one session
//! name, and a bounded prepare command. The turn that follows is a separate
//! slice, so a ticket whose prepare succeeded stops at a pending assignment —
//! the state `recovery_for` already knows how to relaunch — rather than being
//! left `running` with nothing running.
//!
//! A launch that fails ends the ticket with the output in its report. Nothing is
//! silently retried: the issue says a retry or a person's message tries again,
//! and a pipeline that retried by itself would hide a broken prepare behind an
//! infinite loop.

use std::collections::HashMap;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use openbad_core::event::NewEvent;
use openbad_core::{SessionId, WorkspaceId};
use openbad_llm::chat::ChatMessage;
use openbad_pipeline::config::{ExecutionConfig, load_pipeline_config};
use openbad_pipeline::execution::{
    Budget, LaunchPlan, PREPARE_TIMEOUT, SlotPriority, Slots, Spent, effects_of, launch_plan,
    ticket_prompt,
};
use openbad_pipeline::ticket::{AssignmentState, ReportOutcome, TicketState};
use openbad_store::Store;
use openbad_store::pipeline::{NewAssignment, TicketRecord};
use serde::Serialize;
use tauri::State;

use crate::AppState;

/// The prepare's output lives with its worktree, so a person can read the whole
/// log rather than the part a report kept.
const PREPARE_LOG: &str = "prepare.log";

/// How much of that output is copied into the report. Enough for a compiler's
/// first error and a test summary; not enough to fill a database row with noise.
const PREPARE_LOG_LIMIT: usize = 20_000;

/// What a launch did, and what a person would need to act on it.
#[derive(Debug, Clone, Serialize)]
pub struct LaunchReport {
    pub ticket_id: String,
    pub assignment_id: String,
    /// `worker-<ticket>`: the session this ticket is known by from here on.
    pub session: String,
    pub branch: String,
    pub directory: String,
    /// Present when the ticket could not be prepared. The ticket is ended in that
    /// case, with the same words in its report.
    pub failure: Option<String>,
    pub prepare_output: String,
}

/// Prepares one ticket to run: its worktree, its branch, and its prepare command.
#[tauri::command]
pub async fn pipeline_launch(
    state: State<'_, AppState>,
    project_path: String,
    ticket_id: String,
) -> Result<LaunchReport, String> {
    let store = Arc::clone(&state.store);
    tauri::async_runtime::spawn_blocking(move || launch(&store, &project_path, &ticket_id))
        .await
        .map_err(|error| error.to_string())?
}

fn launch(
    store: &Arc<Mutex<Store>>,
    project_path: &str,
    ticket_id: &str,
) -> Result<LaunchReport, String> {
    let project_root = PathBuf::from(project_path);
    let config = load_pipeline_config(&project_root).map_err(|error| error.to_string())?;

    let (ticket, workspace) = {
        let store = lock(store)?;
        let workspace = crate::project_workspace(&store, project_path)?;
        let ticket = store
            .pipeline_ticket(ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("no ticket {ticket_id}"))?;
        if ticket.workspace_id != workspace {
            return Err(format!("ticket {ticket_id} belongs to another project"));
        }
        if ticket.state != TicketState::Open {
            return Err(format!(
                "ticket {ticket_id} is {}, not open",
                ticket.state.as_str()
            ));
        }
        if let Some(active) = store
            .active_pipeline_assignment(ticket_id)
            .map_err(|error| error.to_string())?
        {
            return Err(format!(
                "ticket {ticket_id} already has a {} assignment {}",
                active.state.as_str(),
                active.id
            ));
        }
        (ticket, workspace)
    };

    let repository = config
        .sources
        .iter()
        .find(|source| source.id == ticket.source_id)
        .map(|source| source.repository.clone())
        .ok_or_else(|| {
            format!(
                "ticket {ticket_id} names source {}, which is no longer configured",
                ticket.source_id
            )
        })?;

    let root = execution_root(&config.execution, &project_root);
    let plan = launch_plan(
        &root,
        &project_root,
        &ticket.id,
        &ticket.source_id,
        &ticket.thread.0,
        &repository,
        &ticket.mandate_id,
    );

    let assignment = {
        let store = lock(store)?;
        store
            .create_pipeline_assignment(NewAssignment {
                workspace_id: workspace,
                ticket_id: ticket.id.clone(),
                executor: plan.session.clone(),
            })
            .map_err(|error| error.to_string())?
    };

    // A worktree the ticket has already worked in was prepared for it before the
    // flag existed; treating that as unprepared re-runs a setup command that is
    // not idempotent and fails the ticket on work it had already done.
    let worked_before = plan.directory.join(".git").exists()
        && store
            .lock()
            .ok()
            .and_then(|store| store.pipeline_report(&ticket.id).ok().flatten())
            .is_some();
    if worked_before {
        remember_prepared(store, &plan.branch);
    }
    let prepared = ensure_worktree(&project_root, &plan).and_then(|()| {
        if already_prepared(store, &plan.branch) {
            // A worktree keeps what its first prepare made. Re-running a setup
            // command that is not idempotent — a virtualenv, an install — fails
            // on work already done, which would turn a delivered ticket into a
            // failed one and hide its report behind an infrastructure error.
            return Ok(String::new());
        }
        let output = run_prepare(config.execution.prepare.as_deref(), &plan)?;
        remember_prepared(store, &plan.branch);
        Ok(output)
    });

    match prepared {
        Ok(output) => Ok(report(&ticket, &plan, &assignment.id, None, output)),
        Err(failure) => {
            let store = lock(store)?;
            let record = store
                .write_pipeline_report(&assignment.id, ReportOutcome::Failed, &failure, None)
                .map_err(|error| error.to_string())?;
            // The rules, not this module, decide what a final report does.
            let effects = effects_of(record.outcome);
            if effects.end_assignment {
                store
                    .move_pipeline_assignment(&assignment.id, AssignmentState::Ended)
                    .map_err(|error| error.to_string())?;
            }
            if let Some(next) = effects.ticket {
                store
                    .move_pipeline_ticket(&ticket.id, next)
                    .map_err(|error| error.to_string())?;
            }
            Ok(report(
                &ticket,
                &plan,
                &assignment.id,
                Some(failure.clone()),
                failure,
            ))
        }
    }
}

fn report(
    ticket: &TicketRecord,
    plan: &LaunchPlan,
    assignment_id: &str,
    failure: Option<String>,
    prepare_output: String,
) -> LaunchReport {
    LaunchReport {
        ticket_id: ticket.id.clone(),
        assignment_id: assignment_id.to_owned(),
        session: plan.session.clone(),
        branch: plan.branch.clone(),
        directory: plan.directory.display().to_string(),
        failure,
        prepare_output,
    }
}

/// Where this project's worktrees live.
///
/// A configured root wins; a relative one is resolved against the project. The
/// default sits beside the project rather than inside it, because a worktree
/// inside the checked-out repository would show up as untracked noise in every
/// status the project's own agent runs.
fn execution_root(execution: &ExecutionConfig, project_root: &Path) -> PathBuf {
    match execution.execution_root.as_deref() {
        Some(configured) => {
            let configured = PathBuf::from(configured);
            if configured.is_absolute() {
                configured
            } else {
                project_root.join(configured)
            }
        }
        None => {
            let name = project_root
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_else(|| "project".to_owned());
            project_root
                .parent()
                .unwrap_or_else(|| Path::new("/"))
                .join(".openbad-executors")
                .join(name)
        }
    }
}

/// Whether this worktree has already been prepared.
///
/// Keyed by the ticket's branch, which is what the worktree is: a ticket keeps
/// one worktree for its whole life, so a second round of work reuses the
/// environment the first one built instead of rebuilding it.
fn prepare_key(branch: &str) -> String {
    format!("pipeline.prepared.{branch}")
}

fn already_prepared(store: &Arc<Mutex<Store>>, branch: &str) -> bool {
    store
        .lock()
        .ok()
        .and_then(|store| store.get_setting(&prepare_key(branch)).ok().flatten())
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
}

fn remember_prepared(store: &Arc<Mutex<Store>>, branch: &str) {
    if let Ok(store) = store.lock() {
        let _ = store.set_setting(prepare_key(branch), serde_json::json!(true));
    }
}

/// Creates the ticket's worktree on its own branch, or reuses the one a previous
/// attempt left behind so its history and its branch survive a retry.
fn ensure_worktree(project_root: &Path, plan: &LaunchPlan) -> Result<(), String> {
    if plan.directory.join(".git").exists() {
        return Ok(());
    }
    if plan.directory.exists() {
        return Err(format!(
            "{} exists and is not a worktree; remove it and retry",
            plan.directory.display()
        ));
    }
    if let Some(parent) = plan.directory.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create {}: {error}", parent.display()))?;
    }

    let directory = plan.directory.to_string_lossy().into_owned();
    let branch = plan.branch.clone();
    let branch_exists = git(
        project_root,
        &["rev-parse", "--verify", &format!("refs/heads/{branch}")],
    )
    .is_ok();

    let mut args = vec!["worktree".to_owned(), "add".to_owned()];
    if branch_exists {
        args.push(directory);
        args.push(branch);
    } else {
        args.push("-b".to_owned());
        args.push(branch);
        args.push(directory);
        args.push("HEAD".to_owned());
    }
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    git(project_root, &borrowed).map(|_| ())
}

/// Runs the project's prepare command in the new worktree, bounded.
///
/// The variables are the launch's own closed set, so a credential cannot arrive
/// here even by accident; the environment is inherited as it is for any other
/// command the app runs.
fn run_prepare(command: Option<&str>, plan: &LaunchPlan) -> Result<String, String> {
    let Some(command) = command.map(str::trim).filter(|command| !command.is_empty()) else {
        return Ok(String::new());
    };

    let log = plan.directory.join(PREPARE_LOG);
    let file =
        File::create(&log).map_err(|error| format!("cannot create {}: {error}", log.display()))?;
    let captured = file
        .try_clone()
        .map_err(|error| format!("cannot open {}: {error}", log.display()))?;
    let mut child = Command::new("sh")
        .arg("-c")
        .arg(command)
        .current_dir(&plan.directory)
        .envs(
            plan.variables
                .iter()
                .map(|(variable, value)| (variable.name(), value)),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::from(captured))
        .stderr(Stdio::from(file))
        .spawn()
        .map_err(|error| format!("cannot run prepare: {error}"))?;

    let deadline = Instant::now() + PREPARE_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let output = read_log(&log);
                return if status.success() {
                    Ok(output)
                } else {
                    Err(format!("prepare exited with {status}\n{output}"))
                };
            }
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "prepare did not finish within {}s\n{}",
                    PREPARE_TIMEOUT.as_secs(),
                    read_log(&log)
                ));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(error) => return Err(format!("cannot wait for prepare: {error}")),
        }
    }
}

/// The tail of the prepare's log: a compiler's error and a test summary are at
/// the end, and a long install would otherwise push them out of the report.
fn read_log(path: &Path) -> String {
    let text = fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default();
    let text = text.trim_end();
    if text.len() <= PREPARE_LOG_LIMIT {
        return text.to_owned();
    }
    text[text.len() - PREPARE_LOG_LIMIT..].to_owned()
}

fn git(directory: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .current_dir(directory)
        .args(args)
        .output()
        .map_err(|error| format!("cannot run git: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn lock(store: &Arc<Mutex<Store>>) -> Result<MutexGuard<'_, Store>, String> {
    store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))
}

/// What one turn did, in the terms a report is written in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TurnResult {
    pub text: String,
    pub steps: u32,
    pub tokens: u64,
}

/// Runs a ticket's turn.
///
/// A seam, so the protocol around the turn — the session it resumes, the states it
/// moves, the report it writes, the budget it enforces, the stop it honours — can
/// be tested without a model or a network.
#[async_trait::async_trait]
pub trait TurnRunner: Send + Sync {
    async fn run(
        &self,
        worktree: &Path,
        history: &[ChatMessage],
        cancel: &Arc<AtomicBool>,
    ) -> Result<TurnResult, String>;

    /// Reads the branch as a reviewer and answers, without changing it.
    ///
    /// Separate from [`TurnRunner::run`] because it is a different job with a
    /// different prompt and no history: the reviewer has to arrive at a verdict,
    /// not continue the work. A runner that cannot review says so, and the ticket
    /// stays waiting for a person rather than merging unreviewed.
    async fn review(
        &self,
        _worktree: &Path,
        _prompt: &str,
        _cancel: &Arc<AtomicBool>,
    ) -> Result<String, String> {
        Err("this runner cannot review".to_owned())
    }
}

/// What a running turn shares with the rest of the app.
#[derive(Clone, Default)]
pub struct ExecutionOptions {
    /// Set by `pipeline_stop`; the observer the agent polls between steps reads it,
    /// so a stop lands at a step boundary instead of killing a process mid-write.
    pub cancel: Arc<AtomicBool>,
    /// Shared per project, so two tickets cannot run when the project allows one.
    pub slots: Arc<Mutex<HashMap<WorkspaceId, Slots>>>,
    /// The GitHub account this project's pipeline acts as, if it names one.
    pub github: Option<openbad_pipeline::GitHubIdentity>,
}

/// What an execution did with its ticket.
#[derive(Debug, Clone, Serialize)]
pub struct ExecutionReport {
    pub ticket_id: String,
    pub assignment_id: String,
    /// The ticket's session, which its next assignment resumes.
    pub session: String,
    pub outcome: ReportOutcome,
    /// The executor's own words, verbatim.
    pub last_words: String,
    pub steps: u32,
    pub tokens: u64,
    /// Present when the turn did not finish cleanly.
    pub failure: Option<String>,
}

/// Runs one ticket's executor turn and records what it said.
#[tauri::command]
pub async fn pipeline_execute(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    project_path: String,
    ticket_id: String,
) -> Result<ExecutionReport, String> {
    let turn = AgentTurn::resolve(&app, &state, &ticket_id, &project_path)?;
    let options = {
        let mut runs = state
            .pipeline_runs
            .lock()
            .map_err(|error| format!("pipeline runs lock poisoned: {error}"))?;
        let cancel = Arc::new(AtomicBool::new(false));
        runs.insert(ticket_id.clone(), Arc::clone(&cancel));
        let github = {
            let store = lock(&state.store)?;
            crate::pipeline::github_identity(&store, &project_path)?
        };
        ExecutionOptions {
            cancel,
            slots: Arc::clone(&state.pipeline_slots),
            github,
        }
    };

    let result = execute(&state.store, &project_path, &ticket_id, &turn, &options).await;

    if let Ok(mut runs) = state.pipeline_runs.lock() {
        runs.remove(&ticket_id);
    }
    result
}

/// Stops a running ticket, if one is running. Answers whether anything was.
#[tauri::command]
pub fn pipeline_stop(state: State<'_, AppState>, ticket_id: String) -> Result<bool, String> {
    let runs = state
        .pipeline_runs
        .lock()
        .map_err(|error| format!("pipeline runs lock poisoned: {error}"))?;
    Ok(match runs.get(&ticket_id) {
        Some(cancel) => {
            cancel.store(true, Ordering::SeqCst);
            true
        }
        None => false,
    })
}

/// Runs the turn a launch prepared, then lets the rules decide what its report
/// does to the assignment and the ticket.
pub async fn execute<R: TurnRunner>(
    store: &Arc<Mutex<Store>>,
    project_path: &str,
    ticket_id: &str,
    runner: &R,
    options: &ExecutionOptions,
) -> Result<ExecutionReport, String> {
    let project_root = PathBuf::from(project_path);
    let config = load_pipeline_config(&project_root).map_err(|error| error.to_string())?;

    let (ticket, assignment) = {
        let store = lock(store)?;
        let ticket = store
            .pipeline_ticket(ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("no ticket {ticket_id}"))?;
        if ticket.state != TicketState::Open {
            return Err(format!(
                "ticket {ticket_id} is {}, not open",
                ticket.state.as_str()
            ));
        }
        let assignment = store
            .active_pipeline_assignment(ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| {
                format!("ticket {ticket_id} has no assignment; launch it before running it")
            })?;
        if assignment.state != AssignmentState::Pending {
            return Err(format!(
                "ticket {ticket_id} has a {} assignment {}; only a pending one can start",
                assignment.state.as_str(),
                assignment.id
            ));
        }
        (ticket, assignment)
    };

    let repository = config
        .sources
        .iter()
        .find(|source| source.id == ticket.source_id)
        .map(|source| source.repository.clone())
        .ok_or_else(|| {
            format!(
                "ticket {ticket_id} names source {}, which is no longer configured",
                ticket.source_id
            )
        })?;

    let root = execution_root(&config.execution, &project_root);
    let plan = launch_plan(
        &root,
        &project_root,
        &ticket.id,
        &ticket.source_id,
        &ticket.thread.0,
        &repository,
        &ticket.mandate_id,
    );
    if !plan.directory.join(".git").exists() {
        return Err(format!(
            "ticket {ticket_id} has no worktree at {}; launch it first",
            plan.directory.display()
        ));
    }

    {
        let mut all = options
            .slots
            .lock()
            .map_err(|error| format!("pipeline slots lock poisoned: {error}"))?;
        let slots = all
            .entry(ticket.workspace_id)
            .or_insert_with(|| Slots::from(config.execution.clone()));
        if !slots.try_acquire(&ticket.id, SlotPriority::Background) {
            return Err(format!(
                "all {} slots are busy; this ticket waits for one",
                slots.capacity()
            ));
        }
    }

    let session = {
        let store = lock(store)?;
        start_session(
            &store,
            ticket.workspace_id,
            &plan.session,
            &ticket.id,
            &assignment.id,
        )?
    };
    let turn = turn_number_store(store, session)?;
    if let Ok(store) = store.lock() {
        let _ = store.append_event(
            session,
            NewEvent::new(openbad_core::event::SessionEventKind::TurnStart).with_turn(turn),
        );
    }

    let prompt = ticket_prompt(&plan, &ticket.mandate);
    let history = {
        let store = lock(store)?;
        let (mut history, _) = crate::load_history(&store, session)?;
        history.push(ChatMessage::user(prompt));
        history
    };

    {
        let store = lock(store)?;
        store
            .move_pipeline_assignment(&assignment.id, AssignmentState::Running)
            .map_err(|error| error.to_string())?;
    }

    let outcome_of_turn = runner.run(&plan.directory, &history, &options.cancel).await;
    let stopped = options.cancel.load(Ordering::SeqCst);

    let (outcome, last_words, steps, tokens, failure) = match outcome_of_turn {
        _ if stopped => (
            ReportOutcome::Waiting,
            "stopped by a person".to_owned(),
            0,
            0,
            None,
        ),
        Ok(run) => {
            let budget = Budget::from(config.execution.budget);
            let spent = Spent {
                steps: u64::from(run.steps),
                tokens: run.tokens,
            };
            match budget.reached(spent) {
                // A limit exists so an unattended loop cannot run forever, not to
                // keep work small. The numbers travel with the reason, because
                // "budget exceeded" is not actionable.
                Some(limit) => {
                    let reason = limit.reason();
                    (
                        ReportOutcome::Failed,
                        format!("{reason}\n{}", run.text),
                        run.steps,
                        run.tokens,
                        Some(reason),
                    )
                }
                None => (ReportOutcome::Ready, run.text, run.steps, run.tokens, None),
            }
        }
        Err(error) => (ReportOutcome::Failed, error.clone(), 0, 0, Some(error)),
    };

    // A ticket that opened a pull request subscribes to its thread, so review
    // activity wakes this executor instead of needing a new admission.
    let pull_request = if outcome == ReportOutcome::Ready {
        detect_pull_request(&plan.directory, &plan.branch, options.github.as_ref())
    } else {
        None
    };

    // The half of the loop that used to leave a person holding it: a fresh
    // context reads the branch, and — when the project asked for it — an approved
    // pull request is merged. Both are opt-in, so a project that wants a person
    // to review is unaffected.
    let mut outcome = outcome;
    let mut last_words = last_words;
    if let Some(pull_request) = &pull_request {
        let reviewed = review_then_merge(
            &plan.directory,
            &plan.branch,
            &base_branch(&plan.directory),
            &ticket.thread.0,
            pull_request,
            &config.execution,
            options.github.as_ref(),
            runner,
            &options.cancel,
        )
        .await;
        match reviewed {
            ReviewOutcome::Merged { words } => {
                outcome = ReportOutcome::Merged;
                last_words = words;
            }
            ReviewOutcome::Kept { words } => last_words = words,
            ReviewOutcome::Skipped => {}
            // A conflict is work, not a verdict. The executor rebases its own
            // branch, and the loop then reviews and merges the result — once, so
            // a branch that cannot be reconciled cannot spin.
            ReviewOutcome::Rebase { words } => {
                let mut retry = history.clone();
                retry.push(ChatMessage::user(format!(
                    "The pull request for this ticket conflicts with its base, so it cannot merge. \
                     Rebase {branch} onto the current base, resolve every conflict yourself, re-run \
                     the tests, and push. Do not ask a person.\n\n{words}",
                    branch = plan.branch
                )));
                last_words = match runner.run(&plan.directory, &retry, &options.cancel).await {
                    Ok(run) => run.text,
                    Err(error) => format!("{words}\nThe rebase turn failed: {error}"),
                };
                let again = review_then_merge(
                    &plan.directory,
                    &plan.branch,
                    &base_branch(&plan.directory),
                    &ticket.thread.0,
                    pull_request,
                    &config.execution,
                    options.github.as_ref(),
                    runner,
                    &options.cancel,
                )
                .await;
                match again {
                    ReviewOutcome::Merged { words } => {
                        outcome = ReportOutcome::Merged;
                        last_words = words;
                    }
                    ReviewOutcome::Kept { words } | ReviewOutcome::Rebase { words } => {
                        last_words = words
                    }
                    ReviewOutcome::Skipped => {}
                }
            }
        }
    }

    {
        let store = lock(store)?;
        let record = store
            .write_pipeline_report(
                &assignment.id,
                outcome,
                &last_words,
                pull_request.as_deref(),
            )
            .map_err(|error| error.to_string())?;
        if pull_request.is_some() {
            store
                .subscribe_pipeline_thread(
                    ticket.workspace_id,
                    &ticket.id,
                    &ticket.source_id,
                    &ticket.thread.0,
                )
                .map_err(|error| error.to_string())?;
        }
        let effects = effects_of(record.outcome);
        if effects.end_assignment {
            store
                .move_pipeline_assignment(&assignment.id, AssignmentState::Ended)
                .map_err(|error| error.to_string())?;
        }
        if let Some(next) = effects.ticket {
            store
                .move_pipeline_ticket(&ticket.id, next)
                .map_err(|error| error.to_string())?;
        }
        if effects.release_slot {
            if let Ok(mut all) = options.slots.lock() {
                if let Some(slots) = all.get_mut(&ticket.workspace_id) {
                    slots.release(&ticket.id);
                }
            }
        }
        finish_session(&store, session, turn, &last_words);
    }

    let store = lock(store)?;
    let record = store
        .pipeline_report(&ticket.id)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| format!("ticket {ticket_id} has no report after its turn"))?;

    Ok(ExecutionReport {
        ticket_id: ticket.id,
        assignment_id: assignment.id,
        session: session.to_string(),
        outcome: record.outcome,
        last_words: record.last_words,
        steps,
        tokens,
        failure,
    })
}

/// What happened when the pipeline tried to close its own loop.
enum ReviewOutcome {
    /// A reviewer approved and the pull request is merged.
    Merged { words: String },
    /// The ticket keeps its report, with the reviewer's words attached.
    Kept { words: String },
    /// The branch cannot merge until it is rebased, which is the executor's job.
    Rebase { words: String },
    /// The project asked for neither a review nor a merge.
    Skipped,
}

/// The branch the work is judged against.
///
/// The ticket's branch was cut from the project's checkout, so its upstream's
/// default is the honest base; `main` is the fallback for a checkout with no
/// remote, which is also what `git worktree add` starts from.
fn base_branch(worktree: &Path) -> String {
    git(worktree, &["rev-parse", "--abbrev-ref", "origin/HEAD"])
        .ok()
        .map(|name| name.trim().trim_start_matches("origin/").to_owned())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "main".to_owned())
}

/// Reviews the branch, then merges it when the project asked for both.
///
/// The order matters and so does what each step refuses: a review that cannot run
/// leaves the ticket waiting for a person, never merged, and a merge is attempted
/// only after a reviewer approved and GitHub reports the pull request clean. An
/// error in either is written into the report rather than swallowed, because a
/// pipeline whose loops fail quietly is worse than one that stops.
#[allow(clippy::too_many_arguments)]
async fn review_then_merge<R: TurnRunner>(
    worktree: &Path,
    branch: &str,
    base: &str,
    thread: &str,
    pull_request: &str,
    execution: &openbad_pipeline::config::ExecutionConfig,
    identity: Option<&openbad_pipeline::GitHubIdentity>,
    runner: &R,
    cancel: &Arc<AtomicBool>,
) -> ReviewOutcome {
    use openbad_pipeline::config::{MergePolicy, ReviewPolicy};

    if matches!(execution.review, ReviewPolicy::None) {
        return ReviewOutcome::Skipped;
    }

    let prompt = openbad_pipeline::review_prompt(thread, branch, base, None);
    let head_before = git(worktree, &["rev-parse", "HEAD"]).ok();

    let answer = match runner.review(worktree, &prompt, cancel).await {
        Ok(answer) => answer,
        Err(error) => {
            return ReviewOutcome::Kept {
                words: format!(
                    "Review did not run: {error}. The pull request is waiting for a person."
                ),
            };
        }
    };

    // A reviewer that moved the branch has changed what it was asked to judge.
    let head_after = git(worktree, &["rev-parse", "HEAD"]).ok();
    let verdict = if head_before.is_some() && head_before != head_after {
        openbad_pipeline::Verdict {
            approved: false,
            reason: "the reviewer changed the branch it was reviewing".to_owned(),
        }
    } else {
        openbad_pipeline::parse_verdict(&answer)
    };

    if !verdict.approved {
        return ReviewOutcome::Kept {
            words: format!("Review rejected: {}\n\n{answer}", verdict.reason),
        };
    }
    if !matches!(execution.merge, MergePolicy::Auto) {
        return ReviewOutcome::Kept {
            words: format!("Review approved: {}\n\n{answer}", verdict.reason),
        };
    }

    match merge_pull_request(worktree, pull_request, identity) {
        Err(error) if error.contains("conflicts with the base") => ReviewOutcome::Rebase {
            words: format!(
                "Review approved: {}\nThe branch conflicts with its base: {error}",
                verdict.reason
            ),
        },
        Ok(words) => ReviewOutcome::Merged {
            words: format!(
                "Review approved: {}\nMerged {pull_request}. {words}",
                verdict.reason
            ),
        },
        Err(error) => ReviewOutcome::Kept {
            words: format!("Review approved: {}\nMerge failed: {error}", verdict.reason),
        },
    }
}

/// Merges a ticket's pull request because a person asked for it.
///
/// The same gates as the automatic path: GitHub decides whether the pull request
/// may merge at all, and its refusal is returned rather than forced with
/// administrator rights. What differs is only who decided.
#[tauri::command]
pub async fn pipeline_merge_ticket(
    state: State<'_, AppState>,
    project_path: String,
    ticket_id: String,
) -> Result<String, String> {
    let (pull_request, assignment_id) = {
        let store = lock(&state.store)?;
        let report = store
            .pipeline_report(&ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("ticket {ticket_id} has no report"))?;
        let pull_request = store
            .pipeline_last_pull_request(&ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| {
                format!(
                    "ticket {ticket_id} never opened a pull request, so there is nothing to merge"
                )
            })?;
        (pull_request, report.assignment_id)
    };

    let config =
        load_pipeline_config(Path::new(&project_path)).map_err(|error| error.to_string())?;
    let root = execution_root(&config.execution, Path::new(&project_path));
    let (ticket, workspace) = {
        let store = lock(&state.store)?;
        let workspace = crate::project_workspace(&store, &project_path)?;
        let ticket = store
            .pipeline_ticket(&ticket_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("no ticket {ticket_id}"))?;
        (ticket, workspace)
    };
    let _ = workspace;
    let plan = launch_plan(
        &root,
        Path::new(&project_path),
        &ticket.id,
        &ticket.source_id,
        &ticket.thread.0,
        &config
            .sources
            .iter()
            .find(|source| source.id == ticket.source_id)
            .map(|source| source.repository.clone())
            .unwrap_or_default(),
        &ticket.mandate_id,
    );
    let identity = {
        let store = lock(&state.store)?;
        crate::pipeline::github_identity(&store, &project_path)?
    };
    let merged = merge_pull_request(&plan.directory, &pull_request, identity.as_ref())?;

    let store = lock(&state.store)?;
    store
        .write_pipeline_report(
            &assignment_id,
            ReportOutcome::Merged,
            &format!("Merged {pull_request} because a person asked. {merged}"),
            Some(&pull_request),
        )
        .map_err(|error| error.to_string())?;
    store
        .move_pipeline_ticket(&ticket_id, TicketState::Ended)
        .map_err(|error| error.to_string())?;
    Ok(merged)
}

/// Opens a link in the person's own browser.
///
/// A webview cannot navigate to the web platform's pages, so a bare `target=_blank`
/// anchor silently does nothing — which reads as a dead control. Only http(s) is
/// accepted: a link from a report is still a link from a model's text.
#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    let trimmed = url.trim();
    if !(trimmed.starts_with("https://") || trimmed.starts_with("http://")) {
        return Err(format!(
            "refusing to open {trimmed}: only http(s) links open"
        ));
    }
    let mut command = if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(trimmed);
        command
    } else if cfg!(windows) {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", trimmed]);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(trimmed);
        command
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("cannot open {trimmed}: {error}"))
}

/// Brings a pull request's branch up to date with its base, and waits for it.
///
/// Bounded on purpose: the point is to remove a mechanical chore, not to spin. If
/// it is still not ready after a few minutes the merge reports why, and a person
/// or the executor decides.
fn update_branch(
    binary: &Path,
    worktree: &Path,
    pull_request: &str,
    identity: Option<&openbad_pipeline::GitHubIdentity>,
) -> Result<(), String> {
    let mut command = Command::new(binary);
    command
        .current_dir(worktree)
        .args(["pr", "update-branch", pull_request]);
    openbad_pipeline::github::apply(identity, &mut command);
    let output = command
        .output()
        .map_err(|error| format!("cannot run gh pr update-branch: {error}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_owned());
    }

    let deadline = Instant::now() + Duration::from_secs(240);
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_secs(10));
        let Ok((_, state, _)) = pull_request_state(binary, worktree, pull_request, identity) else {
            continue;
        };
        if state == "CLEAN" || state == "UNSTABLE" {
            return Ok(());
        }
        if state == "DIRTY" {
            return Err("the branch still conflicts after being updated".to_owned());
        }
    }
    Err("the branch did not come up to date in time".to_owned())
}

/// Squash-merges a pull request the pipeline opened, as the project's account.
///
/// GitHub still decides: a protected branch with required reviews refuses a
/// self-review, and that refusal is reported rather than worked around. A branch
/// that is only behind is brought up to date first — that is a mechanical chore,
/// not a decision — while a conflicting branch is handed back to the executor,
/// whose job it is to rebase its own work.
fn merge_pull_request(
    worktree: &Path,
    pull_request: &str,
    identity: Option<&openbad_pipeline::GitHubIdentity>,
) -> Result<String, String> {
    let binary = openbad_pipeline::gh_binary()
        .ok_or_else(|| "cannot find gh, so the pull request cannot be merged".to_owned())?;

    for attempt in 0..2 {
        let (mergeable, state_text, failing) =
            pull_request_state(binary, worktree, pull_request, identity)?;
        if mergeable == "CONFLICTING" {
            return Err(format!(
                "the branch conflicts with the base ({state_text}); the executor has to rebase it \
                 before this can merge"
            ));
        }
        if mergeable == "MERGEABLE" && failing == 0 && state_text == "BEHIND" && attempt == 0 {
            update_branch(binary, worktree, pull_request, identity)?;
            continue;
        }
        if mergeable != "MERGEABLE" || failing > 0 {
            return Err(format!(
                "GitHub reports {mergeable}/{state_text} with {failing} check(s) not passing"
            ));
        }

        let mut merge = Command::new(binary);
        merge.current_dir(worktree).args([
            "pr",
            "merge",
            pull_request,
            "--squash",
            "--delete-branch",
        ]);
        openbad_pipeline::github::apply(identity, &mut merge);
        let output = merge
            .output()
            .map_err(|error| format!("cannot run gh pr merge: {error}"))?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).trim().to_owned());
        }
        let said = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        return Ok(if said.is_empty() {
            format!("{state_text} before merging")
        } else {
            said
        });
    }
    Err("the pull request did not become mergeable".to_owned())
}

/// What GitHub says about the pull request right now.
fn pull_request_state(
    binary: &Path,
    worktree: &Path,
    pull_request: &str,
    identity: Option<&openbad_pipeline::GitHubIdentity>,
) -> Result<(String, String, usize), String> {
    let mut view = Command::new(binary);
    view.current_dir(worktree).args([
        "pr",
        "view",
        pull_request,
        "--json",
        "mergeable,mergeStateStatus,statusCheckRollup",
    ]);
    openbad_pipeline::github::apply(identity, &mut view);
    let output = view
        .output()
        .map_err(|error| format!("cannot run gh pr view: {error}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_owned());
    }
    let view: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("gh pr view returned an unreadable answer: {error}"))?;
    let mergeable = view
        .get("mergeable")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_owned();
    let state = view
        .get("mergeStateStatus")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_owned();
    let failing = view
        .get("statusCheckRollup")
        .and_then(|value| value.as_array())
        .map(|checks| {
            checks
                .iter()
                .filter(|check| {
                    let conclusion = check
                        .get("conclusion")
                        .or_else(|| check.get("state"))
                        .and_then(|value| value.as_str())
                        .unwrap_or_default()
                        .to_ascii_uppercase();
                    !matches!(conclusion.as_str(), "SUCCESS" | "NEUTRAL" | "SKIPPED" | "")
                })
                .count()
        })
        .unwrap_or(0);
    Ok((mergeable, state, failing))
}

/// The pull request this ticket's branch has open, if it has one.
///
/// Asked of `gh` rather than of the model: the executor may describe its work
/// however it likes, but the branch is the fact that decides whether review can
/// reach it. `gh` is resolved rather than assumed — a desktop app's PATH does not
/// have the per-user install the person's shell does, and a spawn that fails here
/// is silent: the work is pushed, the pull request is open, and the panel says
/// there is none.
fn detect_pull_request(
    worktree: &Path,
    branch: &str,
    identity: Option<&openbad_pipeline::GitHubIdentity>,
) -> Option<String> {
    let binary = match openbad_pipeline::gh_binary() {
        Some(binary) => binary,
        None => {
            crate::diagnostics::record(
                "WARN",
                "cannot find gh: no pull request can be detected for this ticket",
            );
            return None;
        }
    };
    let mut command = Command::new(binary);
    command.current_dir(worktree).args([
        "pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url",
    ]);
    openbad_pipeline::github::apply(identity, &mut command);
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let url = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    (!url.is_empty()).then_some(url)
}

/// Resumes the ticket's session when an earlier assignment ran one — the executor
/// keeps its history — and binds it to this assignment.
fn start_session(
    store: &Store,
    workspace: WorkspaceId,
    name: &str,
    ticket_id: &str,
    assignment_id: &str,
) -> Result<SessionId, String> {
    let session = match store
        .pipeline_ticket_session(ticket_id)
        .map_err(|error| error.to_string())?
        .and_then(|id| uuid::Uuid::parse_str(&id).ok())
        .map(SessionId::from)
    {
        Some(session) => session,
        None => store
            .create_session(Some(workspace), Some(name))
            .map_err(|error| error.to_string())?,
    };
    store
        .bind_pipeline_assignment_session(assignment_id, &session.to_string())
        .map_err(|error| error.to_string())?;
    Ok(session)
}

fn turn_number_store(store: &Arc<Mutex<Store>>, session: SessionId) -> Result<u32, String> {
    let store = lock(store)?;
    Ok(store
        .load_turns(session)
        .map(|turns| turns.len() as u32 + 1)
        .unwrap_or(1))
}

fn finish_session(store: &Store, session: SessionId, turn: u32, text: &str) {
    let _ = store.append_events(
        session,
        vec![
            NewEvent::new(openbad_core::event::SessionEventKind::AssistantMessage)
                .with_turn(turn)
                .with_step(1)
                .with_payload(serde_json::json!({ "content": text })),
            NewEvent::new(openbad_core::event::SessionEventKind::TurnEnd)
                .with_turn(turn)
                .with_payload(serde_json::json!({ "reason": "complete" })),
        ],
    );
}

/// Whether this project asked to be watched.
///
/// Persisted rather than remembered in the process: an unattended pipeline that
/// forgets it was running as soon as the window closes is not unattended. The
/// setting is the human's decision; the watcher is what carries it out.
/// How many times the pipeline has tried to finish this ticket on its own.
fn retry_key(ticket_id: &str) -> String {
    format!("pipeline.retry.{ticket_id}")
}

/// When it last tried.
fn retry_at_key(ticket_id: &str) -> String {
    format!("pipeline.retry_at.{ticket_id}")
}

/// Wall-clock milliseconds, which is what the store's own timestamps use.
fn now_millis() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or_default()
}

fn watch_key(workspace: WorkspaceId) -> String {
    format!("pipeline.watch.{workspace}")
}

/// Whether this project should be watched.
///
/// A recorded decision always wins. With no decision yet, the answer comes from
/// the configuration: a project that names an enabled source has already opted
/// in, and requiring a second click to make that mean anything is how a
/// correctly configured pipeline sits empty while labelled work waits in it.
pub(crate) fn wants_watching(store: &Store, workspace: WorkspaceId, root: &Path) -> bool {
    if let Some(chosen) = store
        .get_setting(&watch_key(workspace))
        .ok()
        .flatten()
        .and_then(|value| value.as_bool())
    {
        return chosen;
    }
    enabled_source_cadence(root).is_some()
}

fn remember_watch(store: &Store, workspace: WorkspaceId, watched: bool) {
    let _ = store.set_setting(watch_key(workspace), serde_json::json!(watched));
}

/// The fastest cadence any enabled source asks for, if the project has one.
fn enabled_source_cadence(root: &Path) -> Option<u64> {
    load_pipeline_config(root).ok().and_then(|config| {
        config
            .sources
            .iter()
            .filter(|source| source.enabled)
            .map(|source| source.poll_interval_secs)
            .min()
    })
}

/// How often to tick a project: the fastest enabled source's own cadence.
fn watcher_interval(root: &Path) -> Duration {
    Duration::from_secs(enabled_source_cadence(root).unwrap_or(60).clamp(5, 3_600))
}

/// What a watcher needs to run turns, resolved once per start.
#[derive(Clone)]
pub struct WatcherCredentials {
    api_key: String,
    base_url: String,
    user_id: String,
}

impl WatcherCredentials {
    fn from_state(state: &AppState) -> Result<Self, String> {
        let api_key = crate::resolve_api_key(state)?;
        let user_id = {
            let store = lock(&state.store)?;
            crate::ensure_user_id(&store)?
        };
        let base_url = std::env::var("OPENBAD_DEEPSEEK_BASE_URL")
            .unwrap_or_else(|_| "https://api.deepseek.com".to_owned());
        Ok(Self {
            api_key,
            base_url,
            user_id,
        })
    }
}

/// The app-wide handles a watcher needs, gathered so starting one takes a single
/// argument rather than five that always travel together.
#[derive(Clone)]
pub struct WatcherHandles {
    pub app: tauri::AppHandle,
    pub store: Arc<Mutex<Store>>,
    pub runs: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    pub slots: Arc<Mutex<HashMap<WorkspaceId, Slots>>>,
    pub watchers: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
}

/// Starts one watcher, unless this project already has one.
pub fn start_watcher(
    handles: WatcherHandles,
    project_path: String,
    interval: Duration,
    credentials: WatcherCredentials,
    github: Option<openbad_pipeline::GitHubIdentity>,
) -> bool {
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut watchers = match handles.watchers.lock() {
            Ok(watchers) => watchers,
            Err(_) => return false,
        };
        if watchers.contains_key(&project_path) {
            return false;
        }
        watchers.insert(project_path.clone(), Arc::clone(&cancel));
    }

    let watcher_path = Path::new(&project_path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| project_path.clone());
    let watcher = Arc::new(Watcher {
        app: handles.app,
        store: handles.store,
        runs: handles.runs,
        slots: handles.slots,
        api_key: credentials.api_key,
        base_url: credentials.base_url,
        user_id: credentials.user_id,
        github,
    });
    // Said out loud once, at the moment it starts: which account the pipeline
    // acts as, and how often it polls. "Is it running, and as whom?" is the first
    // question a quiet pipeline raises, and a log that only records failures
    // cannot answer it.
    let acting_as = match &watcher.github {
        Some(identity) => format!("as {}", identity.user()),
        None => "as whichever account gh has active".to_owned(),
    };
    crate::diagnostics::record(
        "INFO",
        &format!(
            "pipeline watching {} {acting_as}, polling every {}s",
            watcher_path,
            interval.as_secs()
        ),
    );
    tauri::async_runtime::spawn(async move {
        watcher.watch(project_path, cancel, interval).await;
    });
    true
}

/// Resumes the watchers the last session left on, so a restart does not silently
/// stop a pipeline the human already asked to run, and starts the ones a
/// project's own configuration asks for.
pub fn resume_watchers(app: &tauri::AppHandle, state: &AppState) {
    let credentials = match WatcherCredentials::from_state(state) {
        Ok(credentials) => credentials,
        Err(error) => {
            // No model credential is not a reason to skip polling: ticking and
            // admitting still work, and the panel can start it by hand once a key
            // exists. Say so rather than fail quietly.
            crate::diagnostics::record("INFO", &format!("pipeline watchers not resumed: {error}"));
            return;
        }
    };

    let projects: Vec<(WorkspaceId, String)> = match state.store.lock() {
        Ok(store) => match store.list_workspaces() {
            Ok(workspaces) => workspaces
                .into_iter()
                .map(|workspace| (workspace.id, workspace.path))
                .collect(),
            Err(_) => return,
        },
        Err(_) => return,
    };

    for (workspace, path) in projects {
        let root = PathBuf::from(&path);
        if !openbad_pipeline::config::pipeline_config_path(&root).is_file() {
            continue;
        }
        let wanted = match state.store.lock() {
            Ok(store) => wants_watching(&store, workspace, &root),
            Err(_) => false,
        };
        if !wanted {
            continue;
        }
        let interval = watcher_interval(&root);
        let github = match state.store.lock() {
            Ok(store) => crate::pipeline::github_identity(&store, &path).unwrap_or_else(|error| {
                // Polling without a named account still works; polling as the
                // wrong one would push and comment as the wrong person.
                crate::diagnostics::record("WARN", &format!("pipeline github identity: {error}"));
                None
            }),
            Err(_) => None,
        };
        start_watcher(
            WatcherHandles {
                app: app.clone(),
                store: Arc::clone(&state.store),
                runs: Arc::clone(&state.pipeline_runs),
                slots: Arc::clone(&state.pipeline_slots),
                watchers: Arc::clone(&state.pipeline_watchers),
            },
            path,
            interval,
            credentials.clone(),
            github,
        );
    }
}

/// Watches one project: polls its sources, launches what was admitted, and runs
/// what was prepared, until a person stops it.
pub struct Watcher {
    app: tauri::AppHandle,
    store: Arc<Mutex<Store>>,
    runs: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    slots: Arc<Mutex<HashMap<WorkspaceId, Slots>>>,
    api_key: String,
    base_url: String,
    user_id: String,
    /// Resolved when the watcher starts: polling, pull-request detection, and the
    /// executor's own `gh` and `git` all act as this account.
    github: Option<openbad_pipeline::GitHubIdentity>,
}

/// Starts watching a project. Idempotent: watching twice is still one watcher.
#[tauri::command]
pub fn pipeline_watch(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    project_path: String,
    interval_seconds: Option<u64>,
) -> Result<bool, String> {
    if state
        .pipeline_watchers
        .lock()
        .map_err(|error| format!("pipeline watchers lock poisoned: {error}"))?
        .contains_key(&project_path)
    {
        return Ok(false);
    }

    let credentials = WatcherCredentials::from_state(&state)?;
    let root = PathBuf::from(&project_path);
    let interval = interval_seconds
        .map(|seconds| Duration::from_secs(seconds.clamp(5, 3_600)))
        .unwrap_or_else(|| watcher_interval(&root));

    let github = {
        let store = lock(&state.store)?;
        crate::pipeline::github_identity(&store, &project_path)?
    };
    let started = start_watcher(
        WatcherHandles {
            app,
            store: Arc::clone(&state.store),
            runs: Arc::clone(&state.pipeline_runs),
            slots: Arc::clone(&state.pipeline_slots),
            watchers: Arc::clone(&state.pipeline_watchers),
        },
        project_path.clone(),
        interval,
        credentials,
        github,
    );
    if started {
        let store = lock(&state.store)?;
        let workspace = crate::project_workspace(&store, &project_path)?;
        remember_watch(&store, workspace, true);
    }
    Ok(started)
}

/// Stops watching a project. Answers whether a watcher was running.
#[tauri::command]
pub fn pipeline_unwatch(state: State<'_, AppState>, project_path: String) -> Result<bool, String> {
    let mut watchers = state
        .pipeline_watchers
        .lock()
        .map_err(|error| format!("pipeline watchers lock poisoned: {error}"))?;
    let stopped = match watchers.remove(&project_path) {
        Some(cancel) => {
            cancel.store(true, Ordering::SeqCst);
            true
        }
        None => false,
    };
    drop(watchers);

    if let Ok(store) = state.store.lock() {
        if let Ok(workspace) = crate::project_workspace(&store, &project_path) {
            remember_watch(&store, workspace, false);
        }
    }
    Ok(stopped)
}

impl Watcher {
    async fn watch(&self, project_path: String, cancel: Arc<AtomicBool>, interval: Duration) {
        while !cancel.load(Ordering::SeqCst) {
            self.poll_once(&project_path).await;

            // Slept in one-second steps so a stop does not wait out the interval.
            let deadline = Instant::now() + interval;
            while Instant::now() < deadline {
                if cancel.load(Ordering::SeqCst) {
                    return;
                }
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }

    /// One pass: poll the sources, then move every ready ticket one step forward.
    async fn poll_once(&self, project_path: &str) {
        use tauri::Emitter as _;

        let workspace = match self.workspace(project_path) {
            Ok(workspace) => workspace,
            Err(error) => {
                crate::diagnostics::record("WARN", &format!("pipeline watch: {error}"));
                return;
            }
        };

        let supervisor = crate::pipeline::Supervisor::new(
            Arc::clone(&self.store),
            workspace,
            PathBuf::from(project_path),
        );
        match supervisor
            .tick(&openbad_pipeline::source::GhCli::new(self.github.clone()))
            .await
        {
            Ok(report) => {
                // A quiet tick is the normal case and stays out of the log. A
                // tick that admitted, deferred, or failed is written down, so
                // "has anything happened?" has an answer older than the panel.
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
                let _ = self.app.emit(
                    "pipeline:tick",
                    serde_json::json!({ "project": project_path, "report": report }),
                );
            }
            Err(error) => {
                crate::diagnostics::record("WARN", &format!("pipeline tick: {error}"));
                return;
            }
        }

        // A ticket that reported ready but whose pull request has not merged is
        // unfinished work the pipeline owns: it is picked up again here, so a
        // conflict, a stale branch, or a red check does not need a person to
        // notice it before anything happens.
        self.resume_unfinished(workspace).await;

        let ready = match self.store.lock() {
            Ok(store) => match store.list_pipeline_tickets(workspace, 500) {
                Ok(tickets) => tickets,
                Err(error) => {
                    crate::diagnostics::record("WARN", &format!("pipeline tickets: {error}"));
                    return;
                }
            },
            Err(_) => return,
        };

        for ticket in ready {
            if ticket.state != TicketState::Open {
                continue;
            }
            if self
                .runs
                .lock()
                .map(|runs| runs.contains_key(&ticket.id))
                .unwrap_or(true)
            {
                continue;
            }
            self.advance(project_path, &ticket).await;
        }
    }

    /// Reopens ended tickets whose work is not finished.
    ///
    /// "Ready" means a reviewable result exists, not that it landed. If the pull
    /// request behind it is still open — conflicting, behind, or failing a check —
    /// the ticket has work left, so it gets a fresh assignment the scheduler runs.
    ///
    /// Two bounds keep that from becoming a spin: at most one attempt per ticket
    /// every ten minutes, and at most five attempts in all. A branch nobody can
    /// reconcile then stops asking and waits for a person, which is the honest
    /// end of an unattended loop.
    async fn resume_unfinished(&self, workspace: WorkspaceId) {
        const COOLDOWN: Duration = Duration::from_secs(600);
        const ATTEMPTS: u32 = 5;

        let candidates = {
            let Ok(store) = self.store.lock() else { return };
            let Ok(tickets) = store.list_pipeline_tickets(workspace, 500) else {
                return;
            };
            let mut due = Vec::new();
            for ticket in tickets {
                if ticket.state != TicketState::Ended {
                    continue;
                }
                let Ok(Some(report)) = store.pipeline_report(&ticket.id) else {
                    continue;
                };
                if report.outcome != ReportOutcome::Ready {
                    continue;
                }
                let Ok(Some(pull_request)) = store.pipeline_last_pull_request(&ticket.id) else {
                    continue;
                };
                let attempts = store
                    .get_setting(&retry_key(&ticket.id))
                    .ok()
                    .flatten()
                    .and_then(|value| value.as_u64())
                    .unwrap_or(0);
                if attempts >= u64::from(ATTEMPTS) {
                    continue;
                }
                let last = store
                    .get_setting(&retry_at_key(&ticket.id))
                    .ok()
                    .flatten()
                    .and_then(|value| value.as_i64());
                if let Some(last) = last {
                    let waited = now_millis().saturating_sub(last);
                    if waited >= 0 && waited < COOLDOWN.as_millis() as i64 {
                        continue;
                    }
                }
                due.push((ticket.id, pull_request, attempts));
            }
            due
        };

        for (ticket_id, pull_request, attempts) in candidates {
            if !self.pull_request_open(&pull_request) {
                continue;
            }
            if let Ok(store) = self.store.lock() {
                crate::diagnostics::record(
                    "INFO",
                    &format!(
                        "pipeline resuming {ticket_id}: {pull_request} has not merged (attempt {})",
                        attempts + 1
                    ),
                );
                let _ = store.set_setting(retry_key(&ticket_id), serde_json::json!(attempts + 1));
                let _ =
                    store.set_setting(retry_at_key(&ticket_id), serde_json::json!(now_millis()));
                if store
                    .move_pipeline_ticket(&ticket_id, TicketState::Open)
                    .is_err()
                {
                    continue;
                }
                let _ = store.create_pipeline_assignment(NewAssignment {
                    workspace_id: workspace,
                    ticket_id: ticket_id.clone(),
                    executor: format!("worker-{ticket_id}"),
                });
            }
        }
    }

    /// Whether GitHub still has this pull request open.
    fn pull_request_open(&self, pull_request: &str) -> bool {
        let Some(binary) = openbad_pipeline::gh_binary() else {
            return false;
        };
        let mut command = Command::new(binary);
        command.args(["pr", "view", pull_request, "--json", "state"]);
        openbad_pipeline::github::apply(self.github.as_ref(), &mut command);
        match command.output() {
            Ok(output) if output.status.success() => {
                let value: serde_json::Value =
                    serde_json::from_slice(&output.stdout).unwrap_or_default();
                value.get("state").and_then(|state| state.as_str()) == Some("OPEN")
            }
            _ => false,
        }
    }

    /// Launches a ticket that has no assignment, and runs one that is prepared.
    async fn advance(&self, project_path: &str, ticket: &TicketRecord) {
        let assignment = match self.store.lock() {
            Ok(store) => store.active_pipeline_assignment(&ticket.id).ok().flatten(),
            Err(_) => return,
        };

        match assignment {
            None => {
                if let Err(error) = launch(&self.store, project_path, &ticket.id) {
                    crate::diagnostics::record(
                        "WARN",
                        &format!("pipeline launch {}: {error}", ticket.id),
                    );
                }
            }
            Some(assignment) if assignment.state == AssignmentState::Pending => {
                let cancel = Arc::new(AtomicBool::new(false));
                if let Ok(mut runs) = self.runs.lock() {
                    if runs.contains_key(&ticket.id) {
                        return;
                    }
                    runs.insert(ticket.id.clone(), Arc::clone(&cancel));
                }
                let turn = AgentTurn {
                    app: self.app.clone(),
                    ticket: ticket.id.clone(),
                    api_key: self.api_key.clone(),
                    model: openbad_llm::FLASH_MODEL.to_owned(),
                    base_url: self.base_url.clone(),
                    user_id: self.user_id.clone(),
                    max_steps: 0,
                    github: self.github.clone(),
                };
                let options = ExecutionOptions {
                    cancel,
                    slots: Arc::clone(&self.slots),
                    github: self.github.clone(),
                };
                let project = project_path.to_owned();
                let id = ticket.id.clone();
                let store = Arc::clone(&self.store);
                let runs = Arc::clone(&self.runs);
                tauri::async_runtime::spawn(async move {
                    if let Err(error) = execute(&store, &project, &id, &turn, &options).await {
                        crate::diagnostics::record("WARN", &format!("pipeline turn {id}: {error}"));
                    }
                    if let Ok(mut runs) = runs.lock() {
                        runs.remove(&id);
                    }
                });
            }
            Some(_) => {}
        }
    }

    fn workspace(&self, project_path: &str) -> Result<WorkspaceId, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        crate::project_workspace(&store, project_path)
    }
}

/// The real runner: the app's own agent, rooted at the ticket's worktree.
///
/// `FullAccess` is the mandate's grant, and it is scoped by construction: the
/// workspace the tools can see is the worktree, so the two things a mandate does
/// not buy stay refused by the tool layer rather than by an approval prompt
/// nobody is there to answer.
pub struct AgentTurn {
    app: tauri::AppHandle,
    ticket: String,
    api_key: String,
    model: String,
    base_url: String,
    user_id: String,
    max_steps: u32,
    /// The account the turn's `gh` and `git` act as, handed to every command it
    /// runs. Absent means the machine's own `gh` configuration.
    github: Option<openbad_pipeline::GitHubIdentity>,
}

impl AgentTurn {
    fn resolve(
        app: &tauri::AppHandle,
        state: &AppState,
        ticket: &str,
        project_path: &str,
    ) -> Result<Self, String> {
        let api_key = crate::resolve_api_key(state)?;
        let user_id = {
            let store = lock(&state.store)?;
            crate::ensure_user_id(&store)?
        };
        let base_url = std::env::var("OPENBAD_DEEPSEEK_BASE_URL")
            .unwrap_or_else(|_| "https://api.deepseek.com".to_owned());
        let github = {
            let store = lock(&state.store)?;
            crate::pipeline::github_identity(&store, project_path)?
        };
        Ok(Self {
            app: app.clone(),
            ticket: ticket.to_owned(),
            github,
            api_key,
            model: openbad_llm::FLASH_MODEL.to_owned(),
            base_url,
            user_id,
            max_steps: 0,
        })
    }
}

/// Forwards the turn's steps to the panel and stops when a person says so.
struct PipelineObserver {
    app: tauri::AppHandle,
    ticket: String,
    cancel: Arc<AtomicBool>,
}

impl PipelineObserver {
    fn emit(&self, payload: serde_json::Value) {
        use tauri::Emitter as _;
        let _ = self.app.emit("pipeline:progress", payload);
    }
}

impl openbad_agent::AgentObserver for PipelineObserver {
    fn on_step(&self, step: u32) {
        self.emit(serde_json::json!({ "ticket": self.ticket, "kind": "step", "step": step }));
    }

    fn on_tool_call(&self, step: u32, call: &openbad_llm::chat::ToolCall) {
        self.emit(serde_json::json!({
            "ticket": self.ticket,
            "kind": "tool_call",
            "step": step,
            "tool": call.function.name,
            "arguments": call.function.arguments,
        }));
    }

    fn on_tool_result(
        &self,
        step: u32,
        call: &openbad_llm::chat::ToolCall,
        content: &str,
        is_error: bool,
        _metadata: &serde_json::Value,
    ) {
        self.emit(serde_json::json!({
            "ticket": self.ticket,
            "kind": "tool_result",
            "step": step,
            "tool": call.function.name,
            "is_error": is_error,
            "content": content.chars().take(2_000).collect::<String>(),
        }));
    }

    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

#[async_trait::async_trait]
impl TurnRunner for AgentTurn {
    async fn run(
        &self,
        worktree: &Path,
        history: &[ChatMessage],
        cancel: &Arc<AtomicBool>,
    ) -> Result<TurnResult, String> {
        let client = openbad_llm::DeepSeekClient::new(
            openbad_llm::ReqwestTransport::new(),
            self.api_key.clone(),
        )
        .with_model(self.model.clone())
        .with_base_url(self.base_url.clone())
        .with_user_id(self.user_id.clone());
        let agent = openbad_agent::default_agent(
            client,
            Arc::new(openbad_workspace::WorkspaceService::new(
                worktree.to_path_buf(),
            )),
            openbad_agent::AgentConfig {
                policy: openbad_governance::ToolPolicy::FullAccess,
                max_steps: self.max_steps,
                stream: false,
                command_env: self
                    .github
                    .as_ref()
                    .map(openbad_pipeline::GitHubIdentity::variables)
                    .unwrap_or_default(),
                ..openbad_agent::AgentConfig::default()
            },
        )
        .with_redactions({
            let mut redactions = vec![self.api_key.clone()];
            if let Some(github) = &self.github {
                redactions.push(github.token().to_owned());
            }
            redactions
        });
        let observer = PipelineObserver {
            app: self.app.clone(),
            ticket: self.ticket.clone(),
            cancel: Arc::clone(cancel),
        };
        let run = agent
            .run_with_observer(history.to_vec(), &observer)
            .await
            .map_err(|error| {
                if cancel.load(Ordering::SeqCst) {
                    "stopped by a person".to_owned()
                } else {
                    error.to_string().replace(&self.api_key, "[redacted]")
                }
            })?;
        Ok(TurnResult {
            text: run.text,
            steps: run.steps,
            tokens: run.usage.input_tokens + run.usage.output_tokens,
        })
    }

    /// One pass, no history, verdict first line.
    ///
    /// The empty history is the point: the executor's context is full of the
    /// reasons its own work is correct, and a reviewer that inherits it agrees.
    async fn review(
        &self,
        worktree: &Path,
        prompt: &str,
        cancel: &Arc<AtomicBool>,
    ) -> Result<String, String> {
        let client = openbad_llm::DeepSeekClient::new(
            openbad_llm::ReqwestTransport::new(),
            self.api_key.clone(),
        )
        .with_model(self.model.clone())
        .with_base_url(self.base_url.clone())
        .with_user_id(self.user_id.clone());
        let agent = openbad_agent::default_agent(
            client,
            Arc::new(openbad_workspace::WorkspaceService::new(
                worktree.to_path_buf(),
            )),
            openbad_agent::AgentConfig {
                // Commands are needed to run the tests the review depends on.
                policy: openbad_governance::ToolPolicy::FullAccess,
                stream: false,
                command_env: self
                    .github
                    .as_ref()
                    .map(openbad_pipeline::GitHubIdentity::variables)
                    .unwrap_or_default(),
                ..openbad_agent::AgentConfig::default()
            },
        )
        .with_redactions({
            let mut redactions = vec![self.api_key.clone()];
            if let Some(github) = &self.github {
                redactions.push(github.token().to_owned());
            }
            redactions
        });
        let observer = PipelineObserver {
            app: self.app.clone(),
            ticket: self.ticket.clone(),
            cancel: Arc::clone(cancel),
        };
        let run = agent
            .run_with_observer(vec![ChatMessage::user(prompt)], &observer)
            .await
            .map_err(|error| {
                if cancel.load(Ordering::SeqCst) {
                    "stopped by a person".to_owned()
                } else {
                    error.to_string().replace(&self.api_key, "[redacted]")
                }
            })?;
        Ok(run.text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use openbad_pipeline::mandate::{Mandate, MandateStrength};
    use openbad_pipeline::trigger::ThreadId;
    use openbad_store::pipeline::NewTicket;
    use openbad_store::types::NewWorkspace;

    struct Project {
        root: tempfile::TempDir,
        /// Held for its lifetime: the worktrees inside it are removed with it.
        _executors: tempfile::TempDir,
        store: Arc<Mutex<Store>>,
        ticket: TicketRecord,
    }

    /// A project that is a real repository, because a launch creates a worktree
    /// of it. The execution root is its own temporary directory, so a test never
    /// writes beside the checkout it made.
    fn project(prepare: &str) -> Project {
        let root = tempfile::tempdir().expect("project");
        let path = root.path();
        std::fs::write(path.join("README.md"), "demo").expect("readme");
        git(path, &["init", "-q"]).expect("init");
        git(path, &["add", "."]).expect("add");
        git(
            path,
            &[
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=test",
                "commit",
                "-qm",
                "init",
            ],
        )
        .expect("commit");

        let executors = tempfile::tempdir().expect("executors");
        std::fs::create_dir_all(path.join(".openbad")).expect("dir");
        std::fs::write(
            path.join(".openbad/pipeline.yaml"),
            format!(
                "sources:\n  - id: main\n    repository: o/r\n    labels: [\"agent:run\"]\nexecution:\n  execution_root: {:?}\n  prepare: {prepare:?}\n",
                executors.path().display().to_string()
            ),
        )
        .expect("config");

        let store = Arc::new(Mutex::new(Store::open_in_memory().expect("store")));
        let workspace = {
            let store = store.lock().expect("lock");
            store
                .create_workspace_record(NewWorkspace::new("demo", path.display().to_string()))
                .expect("workspace")
        };
        let ticket = store
            .lock()
            .expect("lock")
            .create_pipeline_ticket(NewTicket {
                workspace_id: workspace,
                source_id: "main".to_owned(),
                thread: ThreadId("issue:1".to_owned()),
                trigger_id: "trigger-1".to_owned(),
                mandate: Mandate::new(
                    "policy-1",
                    "issue:1",
                    MandateStrength::Explicit,
                    vec!["alice".to_owned()],
                    "alice",
                    "main",
                    "label agent:run",
                ),
            })
            .expect("ticket");

        Project {
            root,
            _executors: executors,
            store,
            ticket,
        }
    }

    impl Project {
        fn path(&self) -> String {
            self.root.path().display().to_string()
        }

        fn launch(&self) -> Result<LaunchReport, String> {
            launch(&self.store, &self.path(), &self.ticket.id)
        }
    }

    #[test]
    fn a_launch_checks_the_ticket_out_into_its_own_worktree() {
        let project = project("printf 'ready\\n'");
        let report = project.launch().expect("launch");

        let worktree = PathBuf::from(&report.directory);
        let branch = git(
            project.root.path(),
            &[
                "rev-parse",
                "--verify",
                &format!("refs/heads/{}", report.branch),
            ],
        )
        .expect("branch");

        assert_eq!(
            (
                report.failure,
                report.session.as_str(),
                worktree.join("README.md").is_file(),
                branch.trim().len(),
                report.prepare_output.as_str(),
            ),
            (
                None,
                format!("worker-{}", project.ticket.id).as_str(),
                true,
                40,
                "ready",
            ),
            "the ticket gets a worktree of its own on a branch of its own"
        );
        assert_eq!(
            project
                .store
                .lock()
                .expect("lock")
                .pipeline_ticket(&project.ticket.id)
                .expect("ticket")
                .expect("ticket")
                .state,
            TicketState::Open,
            "a prepared ticket waits for its turn, still open"
        );
    }

    #[test]
    fn a_failed_prepare_ends_the_ticket_and_keeps_what_the_command_said() {
        let project = project("echo 'cannot install' >&2; exit 3");
        let report = project.launch().expect("launch");

        let store = project.store.lock().expect("lock");
        let outcome = store.pipeline_report(&project.ticket.id).expect("report");
        let failure = report.failure.clone().expect("a failure");

        assert_eq!(
            (
                failure.contains("cannot install"),
                report.prepare_output.contains("cannot install"),
                outcome.as_ref().map(|record| record.outcome),
                store
                    .pipeline_ticket(&project.ticket.id)
                    .expect("ticket")
                    .expect("ticket")
                    .state,
            ),
            (true, true, Some(ReportOutcome::Failed), TicketState::Ended),
            "a broken prepare fails the ticket with the output on the record"
        );
    }

    #[test]
    fn a_launch_refuses_a_ticket_that_already_has_an_assignment() {
        let project = project("true");
        let first = project.launch().expect("first launch");

        assert_eq!(
            project.launch().map(|_| ()),
            Err(format!(
                "ticket {} already has a pending assignment {}",
                project.ticket.id, first.assignment_id
            )),
            "one executor owns a ticket at a time"
        );
    }

    struct FakeTurn {
        result: Result<TurnResult, String>,
        seen: Mutex<Vec<String>>,
        review: Option<String>,
    }

    impl FakeTurn {
        fn new(result: Result<TurnResult, String>) -> Self {
            Self {
                result,
                seen: Mutex::new(Vec::new()),
                review: None,
            }
        }

        fn reviewing(mut self, answer: &str) -> Self {
            self.review = Some(answer.to_owned());
            self
        }

        fn prompts(&self) -> Vec<String> {
            self.seen.lock().expect("prompts").clone()
        }
    }

    #[async_trait::async_trait]
    impl TurnRunner for FakeTurn {
        async fn run(
            &self,
            _worktree: &Path,
            history: &[ChatMessage],
            _cancel: &Arc<AtomicBool>,
        ) -> Result<TurnResult, String> {
            if let Some(prompt) = history.last() {
                self.seen
                    .lock()
                    .expect("prompts")
                    .push(prompt.content.clone().unwrap_or_default());
            }
            self.result.clone()
        }

        async fn review(
            &self,
            _worktree: &Path,
            prompt: &str,
            _cancel: &Arc<AtomicBool>,
        ) -> Result<String, String> {
            self.seen.lock().expect("prompts").push(prompt.to_owned());
            self.review
                .clone()
                .ok_or_else(|| "this runner cannot review".to_owned())
        }
    }

    fn turn(text: &str, steps: u32, tokens: u64) -> Result<TurnResult, String> {
        Ok(TurnResult {
            text: text.to_owned(),
            steps,
            tokens,
        })
    }

    #[tokio::test]
    async fn a_finished_turn_leaves_a_ready_report_and_ends_its_assignment() {
        let project = project("true");
        let launch = project.launch().expect("launch");
        let runner = FakeTurn::new(turn("opened #12", 7, 1_200));

        let report = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &runner,
            &ExecutionOptions::default(),
        )
        .await
        .expect("execute");

        let store = project.store.lock().expect("lock");
        let record = store
            .pipeline_report(&project.ticket.id)
            .expect("report")
            .expect("report");
        assert_eq!(
            (
                report.outcome,
                report.failure,
                record.outcome,
                record.last_words.as_str(),
                store
                    .active_pipeline_assignment(&project.ticket.id)
                    .expect("assignment")
                    .is_none(),
                store
                    .pipeline_ticket(&project.ticket.id)
                    .expect("ticket")
                    .expect("ticket")
                    .state,
                runner.prompts().len(),
                runner.prompts()[0].contains(&launch.directory),
            ),
            (
                ReportOutcome::Ready,
                None,
                ReportOutcome::Ready,
                "opened #12",
                true,
                TicketState::Open,
                1,
                true,
            ),
            "a finished turn asks for review and leaves the ticket open for its next one"
        );
    }

    #[tokio::test]
    async fn a_turn_that_runs_out_of_steps_fails_with_the_numbers() {
        let project = project("true");
        project.launch().expect("launch");
        let mut config =
            std::fs::read_to_string(project.root.path().join(".openbad/pipeline.yaml"))
                .expect("config");
        config.push_str("  budget:\n    steps: 5\n");
        std::fs::write(project.root.path().join(".openbad/pipeline.yaml"), config).expect("config");
        let runner = FakeTurn::new(turn("still going", 5, 0));

        let report = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &runner,
            &ExecutionOptions::default(),
        )
        .await
        .expect("execute");

        assert_eq!(
            (
                report.outcome,
                report
                    .failure
                    .as_deref()
                    .map(str::to_owned)
                    .unwrap_or_default(),
                project
                    .store
                    .lock()
                    .expect("lock")
                    .pipeline_ticket(&project.ticket.id)
                    .expect("ticket")
                    .expect("ticket")
                    .state,
            ),
            (
                ReportOutcome::Failed,
                "stopped after 5 steps; the ticket allows 5".to_owned(),
                TicketState::Ended,
            ),
            "the limit is reported with what it allowed and what was spent"
        );
    }

    #[tokio::test]
    async fn a_turn_that_fails_ends_the_ticket_with_its_words() {
        let project = project("true");
        project.launch().expect("launch");
        let runner = FakeTurn::new(Err("the provider refused the request".to_owned()));

        let report = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &runner,
            &ExecutionOptions::default(),
        )
        .await
        .expect("execute");

        assert_eq!(
            (
                report.outcome,
                report.failure.as_deref(),
                project
                    .store
                    .lock()
                    .expect("lock")
                    .pipeline_ticket(&project.ticket.id)
                    .expect("ticket")
                    .expect("ticket")
                    .state,
            ),
            (
                ReportOutcome::Failed,
                Some("the provider refused the request"),
                TicketState::Ended
            ),
            "a failed turn is a failed ticket, with the reason on it"
        );
    }

    #[tokio::test]
    async fn a_turn_cannot_start_before_the_ticket_has_a_worktree() {
        let project = project("true");
        let runner = FakeTurn::new(turn("unused", 1, 0));

        assert_eq!(
            execute(
                &project.store,
                &project.path(),
                &project.ticket.id,
                &runner,
                &ExecutionOptions::default()
            )
            .await
            .map(|_| ()),
            Err(format!(
                "ticket {} has no assignment; launch it before running it",
                project.ticket.id
            )),
            "the turn waits for the launch that prepared it"
        );
        assert!(runner.prompts().is_empty(), "nothing ran");
    }

    #[tokio::test]
    async fn a_ticket_keeps_one_session_across_its_assignments() {
        let project = project("true");
        let options = ExecutionOptions::default();

        project.launch().expect("launch");
        let first = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &FakeTurn::new(turn("first pass", 1, 0)),
            &options,
        )
        .await
        .expect("first turn");

        project.launch().expect("relaunch");
        let second = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &FakeTurn::new(turn("second pass", 1, 0)),
            &options,
        )
        .await
        .expect("second turn");

        assert_eq!(
            (first.session.as_str(), second.session.as_str()),
            (first.session.as_str(), first.session.as_str()),
            "the executor resumes its session instead of starting cold"
        );
    }

    #[tokio::test]
    async fn a_stop_lands_as_waiting_rather_than_a_failure() {
        let project = project("true");
        project.launch().expect("launch");
        let options = ExecutionOptions::default();
        options.cancel.store(true, Ordering::SeqCst);

        let report = execute(
            &project.store,
            &project.path(),
            &project.ticket.id,
            &FakeTurn::new(turn("kept working", 3, 10)),
            &options,
        )
        .await
        .expect("execute");

        let store = project.store.lock().expect("lock");
        assert_eq!(
            (
                report.outcome,
                report.failure,
                store
                    .pipeline_ticket(&project.ticket.id)
                    .expect("ticket")
                    .expect("ticket")
                    .state,
                store
                    .active_pipeline_assignment(&project.ticket.id)
                    .expect("assignment")
                    .is_none(),
            ),
            (ReportOutcome::Waiting, None, TicketState::Open, true),
            "a person's stop is idle, not a failed ticket"
        );
    }

    #[tokio::test]
    async fn a_project_runs_no_more_tickets_than_its_slots() {
        let project = project("true");
        project.launch().expect("launch");
        let options = ExecutionOptions::default();
        {
            let mut all = options.slots.lock().expect("slots");
            let slots = all
                .entry(
                    project
                        .store
                        .lock()
                        .expect("lock")
                        .pipeline_ticket(&project.ticket.id)
                        .expect("ticket")
                        .expect("ticket")
                        .workspace_id,
                )
                .or_insert_with(|| Slots::new(1));
            assert!(slots.try_acquire("someone-else", SlotPriority::Background));
        }

        assert_eq!(
            execute(
                &project.store,
                &project.path(),
                &project.ticket.id,
                &FakeTurn::new(turn("unused", 1, 0)),
                &options,
            )
            .await
            .map(|_| ()),
            Err("all 1 slots are busy; this ticket waits for one".to_owned()),
            "the configured bound is a bound, even for a person's ticket"
        );
    }

    #[test]
    fn watching_is_remembered_on_the_project_and_ticked_at_its_fastest_cadence() {
        let stopped = project("true");
        let workspace = {
            let store = stopped.store.lock().expect("lock");
            store
                .pipeline_ticket(&stopped.ticket.id)
                .expect("ticket")
                .expect("ticket")
                .workspace_id
        };

        let bare = tempfile::tempdir().expect("bare");
        {
            let store = stopped.store.lock().expect("lock");
            assert!(
                !wants_watching(&store, workspace, bare.path()),
                "off until asked, because nothing is configured yet"
            );
            remember_watch(&store, workspace, true);
            assert!(
                wants_watching(&store, workspace, bare.path()),
                "the decision survives the call"
            );
            remember_watch(&store, workspace, false);
            assert!(!wants_watching(&store, workspace, bare.path()));
            assert!(
                !wants_watching(&store, workspace, stopped.root.path()),
                "a person's stop outranks a configured project too"
            );
        }

        std::fs::write(
            stopped.root.path().join(".openbad/pipeline.yaml"),
            "sources:\n  - id: slow\n    repository: o/r\n    poll_interval_secs: 300\n  - id: fast\n    repository: o/r2\n    poll_interval_secs: 30\n  - id: off\n    repository: o/r3\n    enabled: false\n    poll_interval_secs: 1\n",
        )
        .expect("config");

        assert_eq!(
            watcher_interval(stopped.root.path()),
            Duration::from_secs(30),
            "the fastest enabled source decides, and a disabled one is not polled"
        );

        let fresh = project("true");
        let fresh_workspace = {
            let store = fresh.store.lock().expect("lock");
            store
                .pipeline_ticket(&fresh.ticket.id)
                .expect("ticket")
                .expect("ticket")
                .workspace_id
        };
        {
            let store = fresh.store.lock().expect("lock");
            assert!(
                wants_watching(&store, fresh_workspace, fresh.root.path()),
                "a project that names an enabled source has opted in already"
            );
        }

        std::fs::write(
            fresh.root.path().join(".openbad/pipeline.yaml"),
            "sources:\n  - id: off\n    repository: o/r3\n    enabled: false\n",
        )
        .expect("config");
        {
            let store = fresh.store.lock().expect("lock");
            assert!(
                !wants_watching(&store, fresh_workspace, fresh.root.path()),
                "a project whose only source is off has nothing to poll"
            );
        }
    }

    fn review_and_merge_config(
        review: openbad_pipeline::config::ReviewPolicy,
        merge: openbad_pipeline::config::MergePolicy,
    ) -> openbad_pipeline::config::ExecutionConfig {
        openbad_pipeline::config::ExecutionConfig {
            review,
            merge,
            ..openbad_pipeline::config::ExecutionConfig::default()
        }
    }

    #[tokio::test]
    async fn a_rejected_review_leaves_the_pull_request_for_a_person() {
        let worktree = tempfile::tempdir().expect("worktree");
        let runner = FakeTurn::new(turn("done", 1, 1))
            .reviewing("REJECT: the index is rebuilt on every read");
        let outcome = review_then_merge(
            worktree.path(),
            "worker/t",
            "main",
            "owner/repo#4",
            "https://github.com/owner/repo/pull/4",
            &review_and_merge_config(
                openbad_pipeline::config::ReviewPolicy::Agent,
                openbad_pipeline::config::MergePolicy::Auto,
            ),
            None,
            &runner,
            &Arc::new(AtomicBool::new(false)),
        )
        .await;

        match outcome {
            ReviewOutcome::Kept { words } => {
                assert!(
                    words.contains("Review rejected"),
                    "the reviewer's reason belongs in the report: {words}"
                );
            }
            _ => panic!("a rejection must never merge"),
        }
        assert!(
            runner
                .prompts()
                .iter()
                .any(|prompt| prompt.contains("Do not edit, commit, push, or merge")),
            "the reviewer is asked to judge, not to change"
        );
    }

    #[tokio::test]
    async fn a_project_that_asked_for_no_review_keeps_its_old_loop() {
        let worktree = tempfile::tempdir().expect("worktree");
        let runner = FakeTurn::new(turn("done", 1, 1)).reviewing("APPROVE: fine");
        let outcome = review_then_merge(
            worktree.path(),
            "worker/t",
            "main",
            "owner/repo#4",
            "https://github.com/owner/repo/pull/4",
            &review_and_merge_config(
                openbad_pipeline::config::ReviewPolicy::None,
                openbad_pipeline::config::MergePolicy::Auto,
            ),
            None,
            &runner,
            &Arc::new(AtomicBool::new(false)),
        )
        .await;

        assert!(matches!(outcome, ReviewOutcome::Skipped));
        assert!(
            runner.prompts().is_empty(),
            "no review is run when the project did not ask for one"
        );
    }

    #[tokio::test]
    async fn a_reviewer_that_changes_the_branch_is_rejected() {
        let worktree = tempfile::tempdir().expect("worktree");
        git(worktree.path(), &["init", "-q"]).expect("init");
        std::fs::write(worktree.path().join("a.txt"), "one").expect("file");
        git(worktree.path(), &["add", "."]).expect("add");
        git(
            worktree.path(),
            &[
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "one",
            ],
        )
        .expect("commit");

        // The reviewer says approve, but leaves new work behind while saying it.
        struct EditingReviewer {
            worktree: std::path::PathBuf,
        }
        #[async_trait::async_trait]
        impl TurnRunner for EditingReviewer {
            async fn run(
                &self,
                _worktree: &Path,
                _history: &[ChatMessage],
                _cancel: &Arc<AtomicBool>,
            ) -> Result<TurnResult, String> {
                unreachable!("only the review runs here")
            }

            async fn review(
                &self,
                _worktree: &Path,
                _prompt: &str,
                _cancel: &Arc<AtomicBool>,
            ) -> Result<String, String> {
                std::fs::write(self.worktree.join("a.txt"), "two").expect("write");
                git(&self.worktree, &["commit", "-qam", "sneaky"]).expect("commit");
                Ok("APPROVE: looks fine".to_owned())
            }
        }

        let outcome = review_then_merge(
            worktree.path(),
            "worker/t",
            "main",
            "owner/repo#4",
            "https://github.com/owner/repo/pull/4",
            &review_and_merge_config(
                openbad_pipeline::config::ReviewPolicy::Agent,
                openbad_pipeline::config::MergePolicy::None,
            ),
            None,
            &EditingReviewer {
                worktree: worktree.path().to_path_buf(),
            },
            &Arc::new(AtomicBool::new(false)),
        )
        .await;

        match outcome {
            ReviewOutcome::Kept { words } => assert!(
                words.contains("changed the branch"),
                "a review that edits what it reviews is not a review: {words}"
            ),
            _ => panic!("a moved head must not be treated as approval"),
        }
    }

    #[test]
    fn prepare_runs_once_per_worktree_so_a_second_round_survives_its_own_output() {
        // Not idempotent on purpose: this is `uv venv`, which is what a second
        // round met when it rebuilt a virtualenv the first round had created.
        let project = project("mkdir .venv");
        let first = project.launch().expect("the first round prepares");
        assert_eq!(first.failure, None);

        project
            .store
            .lock()
            .expect("lock")
            .move_pipeline_assignment(&first.assignment_id, AssignmentState::Ended)
            .expect("end the first round");

        let second = project.launch().expect("the second round launches");
        assert_eq!(
            second.failure, None,
            "a worktree keeps what its first prepare made; repeating it fails on work already done"
        );
        assert_eq!(
            second.directory, first.directory,
            "and it is the same worktree, which is why re-preparing is not a fresh start"
        );
        assert_eq!(second.prepare_output, "", "nothing was prepared twice");
    }

    #[test]
    fn a_configured_execution_root_wins_and_a_relative_one_follows_the_project() {
        let execution = ExecutionConfig {
            execution_root: Some("work".to_owned()),
            ..ExecutionConfig::default()
        };
        assert_eq!(
            execution_root(&execution, Path::new("/repo")),
            PathBuf::from("/repo/work")
        );

        let execution = ExecutionConfig::default();
        assert_eq!(
            execution_root(&execution, Path::new("/repos/demo")),
            PathBuf::from("/repos/.openbad-executors/demo"),
            "the default keeps worktrees beside the repository, not inside it"
        );
    }
}
