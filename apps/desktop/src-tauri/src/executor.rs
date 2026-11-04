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

use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use openbad_pipeline::config::{ExecutionConfig, load_pipeline_config};
use openbad_pipeline::execution::{LaunchPlan, PREPARE_TIMEOUT, effects_of, launch_plan};
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

    let prepared = ensure_worktree(&project_root, &plan)
        .and_then(|()| run_prepare(config.execution.prepare.as_deref(), &plan));

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
