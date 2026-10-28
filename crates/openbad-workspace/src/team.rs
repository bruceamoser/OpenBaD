//! A shared work board so a parent and its subagents can divide and track work.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum BoardError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid board data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskState {
    Open,
    Claimed,
    Blocked,
    Done,
    Dropped,
}

impl TaskState {
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "open" => Some(Self::Open),
            "claimed" => Some(Self::Claimed),
            "blocked" => Some(Self::Blocked),
            "done" => Some(Self::Done),
            "dropped" => Some(Self::Dropped),
            _ => None,
        }
    }

    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::Claimed => "claimed",
            Self::Blocked => "blocked",
            Self::Done => "done",
            Self::Dropped => "dropped",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamTask {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub detail: String,
    pub state: TaskState,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub result: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

#[must_use]
pub fn board_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("team.json")
}

pub fn load_board(root: &Path) -> Result<Vec<TeamTask>, BoardError> {
    let path = board_path(root);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_json::from_str(&content).map_err(|error| BoardError::Invalid(error.to_string()))
}

pub fn save_board(root: &Path, tasks: &[TeamTask]) -> Result<(), BoardError> {
    let path = board_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_string_pretty(tasks)
        .map_err(|error| BoardError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

pub fn create_task(root: &Path, title: &str, detail: Option<&str>) -> Result<TeamTask, BoardError> {
    let title = title.trim();
    if title.is_empty() {
        return Err(BoardError::Invalid("title must not be empty".to_owned()));
    }
    let mut tasks = load_board(root)?;
    let now = now_millis();
    let task = TeamTask {
        id: next_id(&tasks),
        title: title.to_owned(),
        detail: detail.unwrap_or_default().trim().to_owned(),
        state: TaskState::Open,
        owner: None,
        result: None,
        created_at: now,
        updated_at: now,
    };
    tasks.push(task.clone());
    save_board(root, &tasks)?;
    Ok(task)
}

/// Change one task. Only the supplied fields move; a task that reaches a
/// terminal state keeps its result so the board stays a record.
pub fn update_task(
    root: &Path,
    id: &str,
    state: Option<&str>,
    owner: Option<&str>,
    result: Option<&str>,
) -> Result<TeamTask, BoardError> {
    if state.is_none() && owner.is_none() && result.is_none() {
        return Err(BoardError::Invalid(
            "update needs at least one of state, owner, or result".to_owned(),
        ));
    }
    let mut tasks = load_board(root)?;
    let task = tasks
        .iter_mut()
        .find(|task| task.id == id)
        .ok_or_else(|| BoardError::Invalid(format!("unknown team task: {id}")))?;
    if let Some(state) = state {
        task.state = TaskState::parse(state).ok_or_else(|| {
            BoardError::Invalid(format!(
                "unknown state {state}; expected open, claimed, blocked, done, or dropped"
            ))
        })?;
    }
    if let Some(owner) = owner {
        let owner = owner.trim();
        task.owner = if owner.is_empty() {
            None
        } else {
            Some(owner.to_owned())
        };
    }
    if let Some(result) = result {
        task.result = Some(result.trim().to_owned());
    }
    task.updated_at = now_millis();
    let updated = task.clone();
    save_board(root, &tasks)?;
    Ok(updated)
}

#[must_use]
pub fn task_report(task: &TeamTask) -> String {
    let mut report = format!("{} [{}] {}", task.id, task.state.as_str(), task.title);
    if let Some(owner) = &task.owner {
        report.push_str(&format!("\nowner: {owner}"));
    }
    if !task.detail.is_empty() {
        report.push_str(&format!("\n{}", task.detail));
    }
    if let Some(result) = &task.result {
        if !result.is_empty() {
            report.push_str(&format!("\nresult: {result}"));
        }
    }
    report
}

#[must_use]
pub fn board_summary(tasks: &[TeamTask]) -> String {
    if tasks.is_empty() {
        return "the team board is empty".to_owned();
    }
    let open = tasks
        .iter()
        .filter(|task| matches!(task.state, TaskState::Open | TaskState::Claimed))
        .count();
    let mut summary = format!("{} task(s), {open} still active", tasks.len());
    for task in tasks {
        summary.push('\n');
        summary.push_str(&format!("- {}", task_report(task).replace('\n', " — ")));
    }
    summary
}

fn next_id(tasks: &[TeamTask]) -> String {
    let highest = tasks
        .iter()
        .filter_map(|task| task.id.strip_prefix("task-"))
        .filter_map(|suffix| suffix.parse::<u64>().ok())
        .max()
        .unwrap_or(0);
    format!("task-{}", highest + 1)
}

fn now_millis() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| i64::try_from(elapsed.as_millis()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn tasks_get_stable_ids_and_move_through_states() {
        let directory = tempdir().expect("tempdir");
        assert_eq!(
            board_summary(&load_board(directory.path()).unwrap()),
            "the team board is empty"
        );

        let first = create_task(
            directory.path(),
            "Index the workspace",
            Some("Start with src"),
        )
        .unwrap();
        let second = create_task(directory.path(), "Rewrite the reader", None).unwrap();
        assert_eq!(first.id, "task-1");
        assert_eq!(second.id, "task-2");
        assert!(create_task(directory.path(), "   ", None).is_err());

        let claimed = update_task(
            directory.path(),
            &first.id,
            Some("claimed"),
            Some("child-a"),
            None,
        )
        .unwrap();
        assert_eq!(claimed.state, TaskState::Claimed);
        assert_eq!(claimed.owner.as_deref(), Some("child-a"));

        let done = update_task(
            directory.path(),
            &first.id,
            Some("done"),
            None,
            Some("412 files"),
        )
        .unwrap();
        assert_eq!(done.state, TaskState::Done);
        assert_eq!(done.result.as_deref(), Some("412 files"));

        assert!(update_task(directory.path(), "task-9", Some("done"), None, None).is_err());
        assert!(update_task(directory.path(), &first.id, Some("finished"), None, None).is_err());
        assert!(update_task(directory.path(), &first.id, None, None, None).is_err());

        let board = load_board(directory.path()).unwrap();
        let summary = board_summary(&board);
        assert!(summary.contains("2 task(s), 1 still active"), "{summary}");
        assert!(summary.contains("412 files"));
    }
}
