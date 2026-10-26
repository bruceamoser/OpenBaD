use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::Duration;

use serde::Serialize;
use tauri::State;

use crate::{off_thread, AppState};

/// Network git operations (`fetch`, `pull`, `push`) may legitimately take a while.
const GIT_NETWORK_TIMEOUT: Duration = Duration::from_secs(120);
/// Local reads answer in milliseconds; a slow answer means a slow or remote
/// filesystem, so fail fast instead of pinning a worker thread.
const GIT_PROBE_TIMEOUT: Duration = Duration::from_secs(30);

/// How many repositories to probe at once when summarizing a project.
const SUMMARY_CONCURRENCY: usize = 4;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitFileChange {
    pub path: String,
    pub old_path: Option<String>,
    pub index_status: String,
    pub worktree_status: String,
    pub staged: bool,
    pub unstaged: bool,
    pub untracked: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitBranch {
    pub name: String,
    pub current: bool,
    pub remote: bool,
    pub upstream: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitRemote {
    pub name: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitCommit {
    pub hash: String,
    pub short_hash: String,
    pub author: String,
    pub email: String,
    pub date: String,
    pub subject: String,
    pub refs: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitRepoStatus {
    pub path: String,
    pub name: String,
    pub is_repo: bool,
    pub branch: Option<String>,
    pub detached: bool,
    pub upstream: Option<String>,
    pub ahead: u32,
    pub behind: u32,
    pub has_commits: bool,
    pub files: Vec<GitFileChange>,
    pub branches: Vec<GitBranch>,
    pub remotes: Vec<GitRemote>,
    pub last_commit: Option<GitCommit>,
    pub operation: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitRepoSummary {
    pub path: String,
    pub name: String,
    pub is_repo: bool,
    pub branch: Option<String>,
    pub detached: bool,
    pub upstream: Option<String>,
    pub ahead: u32,
    pub behind: u32,
    pub dirty: u32,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitCommandResult {
    pub ok: bool,
    pub message: String,
    pub status: Option<GitRepoStatus>,
}

fn git(repo: &Path, args: &[&str]) -> Result<Output, String> {
    git_with_timeout(repo, args, GIT_PROBE_TIMEOUT)
}

fn git_with_timeout(repo: &Path, args: &[&str], timeout: Duration) -> Result<Output, String> {
    let mut command = Command::new("git");
    command
        .current_dir(repo)
        .args(args)
        .env("GIT_TERMINAL_PROMPT", "0");
    crate::process::run_bounded(&mut command, timeout, &format!("git {}", args.join(" ")))
}

/// Describes a non-zero git exit, preferring stderr because stdout is usually empty.
fn failure(args: &[&str], output: &Output) -> String {
    let detail = stderr(output);
    if detail.is_empty() {
        format!("git {} exited with {}", args.join(" "), output.status)
    } else {
        format!("git {} failed: {detail}", args.join(" "))
    }
}

fn text(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).trim().to_owned()
}

fn is_repo(repo: &Path) -> Result<bool, String> {
    let output = git(repo, &["rev-parse", "--is-inside-work-tree"])?;
    if !output.status.success() {
        // `rev-parse` exits non-zero for a plain directory; that is a real
        // answer, not a failure to report.
        return Ok(false);
    }
    Ok(text(&output) == "true")
}

fn branch_name(repo: &Path) -> (Option<String>, bool) {
    if let Ok(output) = git(repo, &["symbolic-ref", "--short", "-q", "HEAD"]) {
        if output.status.success() {
            let name = text(&output);
            if !name.is_empty() {
                return (Some(name), false);
            }
        }
    }
    if let Ok(output) = git(repo, &["rev-parse", "--short", "HEAD"]) {
        if output.status.success() {
            let name = text(&output);
            if !name.is_empty() {
                return (Some(name), true);
            }
        }
    }
    (None, false)
}

fn upstream(repo: &Path) -> Option<String> {
    git(
        repo,
        &["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
    )
    .ok()
    .filter(|output| output.status.success())
    .map(|output| text(&output))
    .filter(|value| !value.is_empty())
}

fn ahead_behind(repo: &Path, upstream: Option<&str>) -> (u32, u32) {
    let Some(upstream) = upstream else {
        return (0, 0);
    };
    let range = format!("{upstream}...HEAD");
    let Ok(output) = git(repo, &["rev-list", "--left-right", "--count", &range]) else {
        return (0, 0);
    };
    if !output.status.success() {
        return (0, 0);
    }
    let value = text(&output);
    let mut parts = value.split_whitespace();
    let behind = parts.next().and_then(|v| v.parse().ok()).unwrap_or(0);
    let ahead = parts.next().and_then(|v| v.parse().ok()).unwrap_or(0);
    (ahead, behind)
}

fn parse_files(repo: &Path) -> Result<Vec<GitFileChange>, String> {
    let args = ["status", "--porcelain=v1", "-z"];
    let output = git(repo, &args)?;
    if !output.status.success() {
        return Err(failure(&args, &output));
    }
    let raw = String::from_utf8_lossy(&output.stdout);
    let mut parts = raw.split('\0');
    let mut files = Vec::new();
    while let Some(entry) = parts.next() {
        if entry.len() < 4 {
            continue;
        }
        let index_status = entry[0..1].to_owned();
        let worktree_status = entry[1..2].to_owned();
        let path = entry[3..].to_owned();
        let old_path = if index_status == "R" || index_status == "C" {
            parts.next().map(str::to_owned)
        } else {
            None
        };
        let untracked = index_status == "?" && worktree_status == "?";
        files.push(GitFileChange {
            path,
            old_path,
            staged: !untracked && index_status != " ",
            unstaged: !untracked && worktree_status != " ",
            untracked,
            index_status,
            worktree_status,
        });
    }
    Ok(files)
}

fn parse_branches(repo: &Path) -> Vec<GitBranch> {
    let mut branches = Vec::new();
    for (kind, current_marker) in [("refs/heads", true), ("refs/remotes", false)] {
        let Ok(output) = git(
            repo,
            &[
                "for-each-ref",
                "--format=%(refname:short)%00%(HEAD)%00%(upstream:short)",
                kind,
            ],
        ) else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        for line in text(&output).lines() {
            let mut fields = line.split('\0');
            let Some(name) = fields.next().filter(|value| !value.is_empty()) else {
                continue;
            };
            if name.ends_with("/HEAD") {
                continue;
            }
            let marker = fields.next().unwrap_or_default();
            let upstream = fields.next().unwrap_or_default();
            let remote = !current_marker;
            branches.push(GitBranch {
                name: name.to_owned(),
                current: marker == "*",
                remote,
                upstream: if upstream.is_empty() {
                    None
                } else {
                    Some(upstream.to_owned())
                },
            });
        }
    }
    branches.sort_by(|left, right| {
        right
            .current
            .cmp(&left.current)
            .then_with(|| left.remote.cmp(&right.remote))
            .then_with(|| left.name.cmp(&right.name))
    });
    branches
}

fn parse_remotes(repo: &Path) -> Vec<GitRemote> {
    let Ok(output) = git(repo, &["remote", "-v"]) else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    let mut remotes: Vec<GitRemote> = Vec::new();
    for line in text(&output).lines() {
        let mut fields = line.split_whitespace();
        let Some(name) = fields.next() else { continue };
        let Some(url) = fields.next() else { continue };
        if fields.next() != Some("(fetch)") {
            continue;
        }
        if let Some(existing) = remotes.iter_mut().find(|remote| remote.name == name) {
            existing.url = url.to_owned();
        } else {
            remotes.push(GitRemote {
                name: name.to_owned(),
                url: url.to_owned(),
            });
        }
    }
    remotes
}

fn last_commit(repo: &Path) -> Option<GitCommit> {
    let output = git(
        repo,
        &[
            "log",
            "-1",
            "--format=%H%x00%h%x00%an%x00%ae%x00%aI%x00%s%x00%D",
        ],
    )
    .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = text(&output);
    let mut fields = value.split('\0');
    Some(GitCommit {
        hash: fields.next()?.to_owned(),
        short_hash: fields.next()?.to_owned(),
        author: fields.next()?.to_owned(),
        email: fields.next()?.to_owned(),
        date: fields.next()?.to_owned(),
        subject: fields.next()?.to_owned(),
        refs: fields.next().unwrap_or_default().to_owned(),
    })
}

fn operation(repo: &Path) -> Option<String> {
    let git_dir = git(repo, &["rev-parse", "--git-dir"]).ok()?;
    if !git_dir.status.success() {
        return None;
    }
    let git_dir_text = text(&git_dir);
    let path = Path::new(&git_dir_text);
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        repo.join(path)
    };
    for (name, label) in [
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    ] {
        if path.join(name).exists() {
            return Some(label.to_owned());
        }
    }
    None
}

pub fn status_for(repo: &Path) -> GitRepoStatus {
    let mut status = GitRepoStatus {
        path: repo.to_string_lossy().into_owned(),
        name: repo
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("repository")
            .to_owned(),
        is_repo: false,
        branch: None,
        detached: false,
        upstream: None,
        ahead: 0,
        behind: 0,
        has_commits: false,
        files: Vec::new(),
        branches: Vec::new(),
        remotes: Vec::new(),
        last_commit: None,
        operation: None,
        error: None,
    };
    match is_repo(repo) {
        Ok(true) => status.is_repo = true,
        Ok(false) => return status,
        Err(error) => {
            status.error = Some(error);
            return status;
        }
    }
    let (branch, detached) = branch_name(repo);
    status.branch = branch;
    status.detached = detached;
    status.upstream = upstream(repo);
    let (ahead, behind) = ahead_behind(repo, status.upstream.as_deref());
    status.ahead = ahead;
    status.behind = behind;
    status.has_commits = git(repo, &["rev-parse", "--verify", "HEAD"])
        .map(|output| output.status.success())
        .unwrap_or(false);
    match parse_files(repo) {
        Ok(files) => status.files = files,
        Err(error) => status.error = Some(error),
    }
    status.branches = parse_branches(repo);
    status.remotes = parse_remotes(repo);
    status.last_commit = last_commit(repo);
    status.operation = operation(repo);
    status
}

fn repo_name(path: &Path) -> String {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("repository")
        .to_owned()
}

/// A cheap status summary for the Source Control repo list. Unlike
/// [`status_for`], it does not collect branches, remotes, or the last commit.
pub fn summary_for(repo: &Path) -> GitRepoSummary {
    let mut summary = GitRepoSummary {
        path: repo.to_string_lossy().into_owned(),
        name: repo_name(repo),
        is_repo: false,
        branch: None,
        detached: false,
        upstream: None,
        ahead: 0,
        behind: 0,
        dirty: 0,
        error: None,
    };
    match is_repo(repo) {
        Ok(true) => summary.is_repo = true,
        Ok(false) => return summary,
        Err(error) => {
            summary.error = Some(error);
            return summary;
        }
    }
    let (branch, detached) = branch_name(repo);
    summary.branch = branch;
    summary.detached = detached;
    summary.upstream = upstream(repo);
    let (ahead, behind) = ahead_behind(repo, summary.upstream.as_deref());
    summary.ahead = ahead;
    summary.behind = behind;
    match parse_files(repo) {
        Ok(files) => summary.dirty = files.len() as u32,
        Err(error) => summary.error = Some(error),
    }
    summary
}

fn unavailable_summary(path: &str, error: &str) -> GitRepoSummary {
    GitRepoSummary {
        path: path.to_owned(),
        name: repo_name(Path::new(path)),
        is_repo: false,
        branch: None,
        detached: false,
        upstream: None,
        ahead: 0,
        behind: 0,
        dirty: 0,
        error: Some(error.to_owned()),
    }
}

fn canonical(path: &str) -> Result<PathBuf, String> {
    std::fs::canonicalize(path).map_err(|error| format!("{path}: {error}"))
}

/// Only allow operations on the project workspace or a registered reference.
fn checked_repo(
    state: &AppState,
    workspace_path: &str,
    repo_path: &str,
) -> Result<PathBuf, String> {
    let workspace = canonical(workspace_path)?;
    let repo = canonical(repo_path)?;
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let Some(workspace) = store
        .list_workspaces()
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|candidate| canonical(&candidate.path).ok().as_deref() == Some(workspace.as_path()))
    else {
        return Err(format!(
            "{} is not a registered project",
            workspace_path
        ));
    };
    if repo == Path::new(&workspace.path) {
        return Ok(repo);
    }
    let references = store
        .list_project_repos(workspace.id)
        .map_err(|error| error.to_string())?;
    if references.iter().any(|reference| {
        canonical(&reference.path).ok().as_deref() == Some(repo.as_path())
    }) {
        Ok(repo)
    } else {
        Err(format!(
            "{} is not the workspace or a registered reference",
            repo_path
        ))
    }
}

fn validate_branch(branch: &str) -> Result<(), String> {
    if branch.is_empty() || branch.len() > 240 || branch.starts_with('-') {
        return Err("invalid branch name".to_owned());
    }
    if branch.chars().any(|character| {
        character.is_control() || matches!(character, ' ' | '~' | '^' | ':' | '?' | '*' | '[' | '\\')
    }) {
        return Err("invalid branch name".to_owned());
    }
    Ok(())
}

fn validate_paths(paths: &[String]) -> Result<(), String> {
    if paths.is_empty() {
        return Err("select at least one file".to_owned());
    }
    for path in paths {
        let candidate = Path::new(path);
        if path.is_empty()
            || candidate.is_absolute()
            || candidate
                .components()
                .any(|component| matches!(component, std::path::Component::ParentDir))
        {
            return Err(format!("invalid path: {path}"));
        }
    }
    Ok(())
}

fn build_result(repo: &Path, output: &Output) -> GitCommandResult {
    let mut message = text(output);
    let error = stderr(output);
    if !message.is_empty() && !error.is_empty() {
        message.push('\n');
    }
    message.push_str(&error);
    GitCommandResult {
        ok: output.status.success(),
        message,
        status: Some(status_for(repo)),
    }
}

fn run_action(repo: &Path, args: &[String], timeout: Duration) -> Result<GitCommandResult, String> {
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    let output = git_with_timeout(repo, &refs, timeout)?;
    Ok(build_result(repo, &output))
}

enum SummaryTarget {
    Repo(PathBuf),
    Unavailable { path: String, error: String },
}

/// Probes every repository, at most [`SUMMARY_CONCURRENCY`] at a time, and
/// returns the summaries in the order they were requested.
fn summarize_all(targets: Vec<SummaryTarget>) -> Vec<GitRepoSummary> {
    let collected = std::sync::Mutex::new(Vec::with_capacity(targets.len()));
    let next = std::sync::atomic::AtomicUsize::new(0);
    let workers = SUMMARY_CONCURRENCY.min(targets.len()).max(1);
    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| {
                loop {
                    let index = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    let Some(target) = targets.get(index) else {
                        break;
                    };
                    let summary = match target {
                        SummaryTarget::Repo(path) => summary_for(path),
                        SummaryTarget::Unavailable { path, error } => {
                            unavailable_summary(path, error)
                        }
                    };
                    if let Ok(mut collected) = collected.lock() {
                        collected.push((index, summary));
                    }
                }
            });
        }
    });
    let Ok(mut collected) = collected.into_inner() else {
        return Vec::new();
    };
    collected.sort_by_key(|(index, _)| *index);
    collected.into_iter().map(|(_, summary)| summary).collect()
}

#[tauri::command]
pub async fn git_repo_status(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
) -> Result<GitRepoStatus, String> {
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || Ok(status_for(&repo))).await
}

#[tauri::command]
pub async fn git_project_repos(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_paths: Vec<String>,
) -> Result<Vec<GitRepoSummary>, String> {
    const MAX_REPOS: usize = 256;
    if repo_paths.len() > MAX_REPOS {
        return Err(format!(
            "at most {MAX_REPOS} repositories can be summarized"
        ));
    }
    let mut ordered = Vec::with_capacity(repo_paths.len() + 1);
    ordered.push(workspace_path.clone());
    ordered.extend(
        repo_paths
            .into_iter()
            .filter(|path| path != &workspace_path),
    );
    let mut seen = std::collections::HashSet::new();
    let mut targets = Vec::with_capacity(ordered.len());
    for path in ordered {
        if !seen.insert(path.clone()) {
            continue;
        }
        targets.push(match checked_repo(&state, &workspace_path, &path) {
            Ok(repo) => SummaryTarget::Repo(repo),
            Err(error) => SummaryTarget::Unavailable { path, error },
        });
    }
    off_thread(move || Ok(summarize_all(targets))).await
}

#[tauri::command]
pub async fn git_init(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
) -> Result<GitCommandResult, String> {
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || {
        if is_repo(&repo).unwrap_or(false) {
            return Ok(GitCommandResult {
                ok: true,
                message: "already a git repository".to_owned(),
                status: Some(status_for(&repo)),
            });
        }
        let output = git(&repo, &["init", "-b", "main"])?;
        Ok(build_result(&repo, &output))
    })
    .await
}

#[tauri::command]
pub async fn git_checkout(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    branch: String,
    create: bool,
) -> Result<GitCommandResult, String> {
    validate_branch(&branch)?;
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || {
        let args = if create {
            vec!["switch".to_owned(), "-c".to_owned(), branch]
        } else {
            vec!["switch".to_owned(), branch]
        };
        run_action(&repo, &args, GIT_PROBE_TIMEOUT)
    })
    .await
}

#[tauri::command]
pub async fn git_fetch(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    remote: Option<String>,
) -> Result<GitCommandResult, String> {
    let mut args = vec!["fetch".to_owned(), "--prune".to_owned()];
    if let Some(remote) = remote.as_deref() {
        validate_branch(remote)?;
        args.push(remote.to_owned());
    }
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || run_action(&repo, &args, GIT_NETWORK_TIMEOUT)).await
}

#[tauri::command]
pub async fn git_pull(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    mode: Option<String>,
) -> Result<GitCommandResult, String> {
    let mut args = vec!["pull".to_owned()];
    args.push(
        match mode.as_deref().unwrap_or("ff-only") {
            "merge" => "--no-rebase",
            "rebase" => "--rebase",
            _ => "--ff-only",
        }
        .to_owned(),
    );
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || run_action(&repo, &args, GIT_NETWORK_TIMEOUT)).await
}

#[tauri::command]
pub async fn git_push(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    remote: Option<String>,
    branch: Option<String>,
    set_upstream: bool,
) -> Result<GitCommandResult, String> {
    let mut args = vec!["push".to_owned()];
    if set_upstream {
        args.push("--set-upstream".to_owned());
    }
    if let Some(remote) = remote.as_deref() {
        validate_branch(remote)?;
        args.push(remote.to_owned());
    }
    if let Some(branch) = branch.as_deref() {
        validate_branch(branch)?;
        args.push(branch.to_owned());
    }
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || run_action(&repo, &args, GIT_NETWORK_TIMEOUT)).await
}

#[tauri::command]
pub async fn git_stage(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    paths: Vec<String>,
) -> Result<GitCommandResult, String> {
    validate_paths(&paths)?;
    let mut args = vec!["add".to_owned(), "--".to_owned()];
    args.extend(paths);
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || run_action(&repo, &args, GIT_PROBE_TIMEOUT)).await
}

#[tauri::command]
pub async fn git_unstage(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    paths: Vec<String>,
) -> Result<GitCommandResult, String> {
    validate_paths(&paths)?;
    let mut args = vec!["restore".to_owned(), "--staged".to_owned(), "--".to_owned()];
    args.extend(paths);
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || run_action(&repo, &args, GIT_PROBE_TIMEOUT)).await
}

#[tauri::command]
pub async fn git_commit(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    message: String,
    stage_all: bool,
) -> Result<GitCommandResult, String> {
    let message = message.trim().to_owned();
    if message.is_empty() {
        return Err("commit message is required".to_owned());
    }
    if message.len() > 20_000 {
        return Err("commit message is too long".to_owned());
    }
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || {
        if stage_all {
            let output = git(&repo, &["add", "-A"])?;
            if !output.status.success() {
                return Ok(GitCommandResult {
                    ok: false,
                    message: stderr(&output),
                    status: Some(status_for(&repo)),
                });
            }
        }
        let output = git(&repo, &["commit", "-m", &message])?;
        Ok(build_result(&repo, &output))
    })
    .await
}

#[tauri::command]
pub async fn git_diff(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    path: Option<String>,
    staged: bool,
    context_lines: Option<u32>,
) -> Result<String, String> {
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    if let Some(path) = path.as_deref() {
        validate_paths(&[path.to_owned()])?;
    }
    off_thread(move || {
        let context = format!("-U{}", context_lines.unwrap_or(3).min(20));
        let mut args = vec!["diff".to_owned(), "--no-color".to_owned(), context];
        if staged {
            args.push("--staged".to_owned());
        }
        if let Some(path) = path {
            args.push("--".to_owned());
            args.push(path);
        }
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        let output = git_with_timeout(&repo, &refs, GIT_PROBE_TIMEOUT)?;
        if !output.status.success() {
            return Err(failure(&refs, &output));
        }
        let mut diff = String::from_utf8_lossy(&output.stdout).into_owned();
        const MAX_DIFF_BYTES: usize = 256 * 1024;
        if diff.len() > MAX_DIFF_BYTES {
            diff.truncate(MAX_DIFF_BYTES);
            diff.push_str("\n… diff truncated");
        }
        Ok(diff)
    })
    .await
}

#[tauri::command]
pub async fn git_log(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_path: String,
    limit: Option<u32>,
) -> Result<Vec<GitCommit>, String> {
    let repo = checked_repo(&state, &workspace_path, &repo_path)?;
    off_thread(move || {
        let limit = format!("-{}", limit.unwrap_or(20).clamp(1, 100));
        let output = git(
            &repo,
            &[
                "log",
                limit.as_str(),
                "--format=%H%x00%h%x00%an%x00%ae%x00%aI%x00%s%x00%D",
            ],
        )?;
        if !output.status.success() {
            return Ok(Vec::new());
        }
        let raw = String::from_utf8_lossy(&output.stdout);
        Ok(raw
            .lines()
            .filter_map(|line| {
                let mut fields = line.split('\0');
                Some(GitCommit {
                    hash: fields.next()?.to_owned(),
                    short_hash: fields.next()?.to_owned(),
                    author: fields.next()?.to_owned(),
                    email: fields.next()?.to_owned(),
                    date: fields.next()?.to_owned(),
                    subject: fields.next()?.to_owned(),
                    refs: fields.next().unwrap_or_default().to_owned(),
                })
            })
            .collect())
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn init_repo() -> tempfile::TempDir {
        let directory = tempfile::tempdir().expect("tempdir");
        let repo = directory.path();
        let output = git(repo, &["init", "-b", "main"]).expect("git init");
        assert!(output.status.success());
        for (key, value) in [
            ("user.name", "OpenBaD Test"),
            ("user.email", "test@openbad.local"),
        ] {
            let output = git(repo, &["config", key, value]).expect("git config");
            assert!(output.status.success());
        }
        directory
    }

    fn commit_file(repo: &Path, name: &str, content: &str) {
        std::fs::write(repo.join(name), content).expect("write file");
        let output = git(repo, &["add", name]).expect("git add");
        assert!(output.status.success());
        let output = git(repo, &["commit", "-m", "initial"]).expect("git commit");
        assert!(output.status.success(), "{}", stderr(&output));
    }

    #[test]
    fn status_reports_branch_commit_and_changes() {
        let directory = init_repo();
        let repo = directory.path();
        commit_file(repo, "README.md", "initial\n");
        std::fs::write(repo.join("README.md"), "changed\n").expect("update file");
        std::fs::write(repo.join("new.txt"), "new\n").expect("new file");

        let status = status_for(repo);
        assert!(status.is_repo);
        assert_eq!(status.branch.as_deref(), Some("main"));
        assert!(!status.detached);
        assert!(status.has_commits);
        assert!(status.last_commit.is_some());
        assert!(
            status
                .files
                .iter()
                .any(|file| file.path == "README.md" && file.unstaged)
        );
        assert!(
            status
                .files
                .iter()
                .any(|file| file.path == "new.txt" && file.untracked)
        );
    }

    #[test]
    fn summary_reports_branch_and_dirty_count() {
        let directory = init_repo();
        let repo = directory.path();
        commit_file(repo, "README.md", "initial\n");
        std::fs::write(repo.join("README.md"), "changed\n").expect("update file");
        std::fs::write(repo.join("new.txt"), "new\n").expect("new file");

        let summary = summary_for(repo);
        assert!(summary.is_repo);
        assert_eq!(summary.name, repo.file_name().unwrap().to_string_lossy());
        assert_eq!(summary.branch.as_deref(), Some("main"));
        assert!(!summary.detached);
        assert_eq!(summary.dirty, 2);
        assert!(summary.error.is_none());
    }

    #[test]
    fn summary_marks_non_repositories() {
        let directory = tempfile::tempdir().expect("tempdir");
        let summary = summary_for(directory.path());
        assert!(!summary.is_repo);
        assert_eq!(summary.dirty, 0);
        assert!(summary.branch.is_none());
    }

    #[test]
    fn branch_and_path_validation_rejects_injection() {
        assert!(validate_branch("feature/git-panel").is_ok());
        assert!(validate_branch("main").is_ok());
        assert!(validate_branch("-danger").is_err());
        assert!(validate_branch("bad branch").is_err());
        assert!(validate_paths(&["src/main.rs".to_owned()]).is_ok());
        assert!(validate_paths(&["/etc/passwd".to_owned()]).is_err());
        assert!(validate_paths(&["../outside".to_owned()]).is_err());
    }

    #[test]
    fn stage_commit_and_diff_round_trip() {
        let directory = init_repo();
        let repo = directory.path();
        commit_file(repo, "a.txt", "one\n");
        std::fs::write(repo.join("a.txt"), "two\n").expect("update");
        let output = git(repo, &["add", "a.txt"]).expect("add");
        assert!(output.status.success());
        let diff = git(repo, &["diff", "--staged", "--no-color"]).expect("diff");
        assert!(text(&diff).contains("-one"));
        assert!(text(&diff).contains("+two"));
        let output = git(repo, &["commit", "-m", "update"]).expect("commit");
        assert!(output.status.success());
        let status = status_for(repo);
        assert!(status.files.is_empty());
    }

    /// A corrupt index makes `git status` exit non-zero while the repository is
    /// still otherwise valid, which is exactly the state that used to be
    /// reported as "clean".
    fn corrupt_index(repo: &Path) {
        std::fs::write(repo.join(".git/index"), "garbage").expect("corrupt index");
    }

    #[test]
    fn summary_surfaces_a_failed_status_instead_of_clean() {
        let directory = init_repo();
        let repo = directory.path();
        commit_file(repo, "README.md", "initial\n");
        corrupt_index(repo);

        let summary = summary_for(repo);
        assert!(summary.is_repo, "the repository itself is still valid");
        assert_eq!(summary.dirty, 0);
        assert!(
            summary.error.is_some(),
            "a failed git status must not be reported as clean"
        );
    }

    #[test]
    fn status_surfaces_a_failed_status_instead_of_clean() {
        let directory = init_repo();
        let repo = directory.path();
        commit_file(repo, "README.md", "initial\n");
        corrupt_index(repo);

        let status = status_for(repo);
        assert!(status.is_repo);
        assert!(status.files.is_empty());
        assert!(
            status.error.is_some(),
            "a failed git status must surface an error"
        );
    }

    #[test]
    fn probe_timeout_is_shorter_than_network_timeout() {
        assert!(
            GIT_PROBE_TIMEOUT < GIT_NETWORK_TIMEOUT,
            "local probes must fail before network operations do"
        );
    }

    #[test]
    fn summarize_all_keeps_request_order_and_reports_unavailable() {
        let first = init_repo();
        let second = init_repo();
        commit_file(first.path(), "README.md", "one\n");
        let targets = vec![
            SummaryTarget::Repo(second.path().to_path_buf()),
            SummaryTarget::Unavailable {
                path: "/not-registered".to_owned(),
                error: "not registered".to_owned(),
            },
            SummaryTarget::Repo(first.path().to_path_buf()),
        ];

        let summaries = summarize_all(targets);

        assert_eq!(summaries.len(), 3);
        assert_eq!(summaries[0].path, second.path().to_string_lossy());
        assert_eq!(summaries[1].path, "/not-registered");
        assert_eq!(summaries[1].error.as_deref(), Some("not registered"));
        assert_eq!(summaries[2].path, first.path().to_string_lossy());
    }
}
