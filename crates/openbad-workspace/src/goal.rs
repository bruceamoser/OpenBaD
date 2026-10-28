//! The workspace's durable goal: one objective that outlives a turn.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum GoalError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid goal data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum GoalStatus {
    Active,
    Paused,
    Done,
    Abandoned,
}

impl GoalStatus {
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "active" => Some(Self::Active),
            "paused" => Some(Self::Paused),
            "done" => Some(Self::Done),
            "abandoned" => Some(Self::Abandoned),
            _ => None,
        }
    }

    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Paused => "paused",
            Self::Done => "done",
            Self::Abandoned => "abandoned",
        }
    }

    #[must_use]
    pub fn is_open(self) -> bool {
        matches!(self, Self::Active | Self::Paused)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GoalNote {
    pub at: i64,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Goal {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub detail: String,
    pub status: GoalStatus,
    #[serde(default)]
    pub progress: Vec<GoalNote>,
    pub created_at: i64,
    pub updated_at: i64,
}

#[must_use]
pub fn goal_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("goal.json")
}

pub fn load_goal(root: &Path) -> Result<Option<Goal>, GoalError> {
    let path = goal_path(root);
    if !path.is_file() {
        return Ok(None);
    }
    let content = std::fs::read_to_string(&path)?;
    serde_json::from_str(&content)
        .map(Some)
        .map_err(|error| GoalError::Invalid(error.to_string()))
}

pub fn save_goal(root: &Path, goal: &Goal) -> Result<(), GoalError> {
    let path = goal_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_string_pretty(goal)
        .map_err(|error| GoalError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

/// Record a new goal. An open goal must be closed first: one objective at a
/// time keeps the prompt and the plan from disagreeing about what matters.
pub fn create_goal(root: &Path, title: &str, detail: Option<&str>) -> Result<Goal, GoalError> {
    let title = title.trim();
    if title.is_empty() {
        return Err(GoalError::Invalid("title must not be empty".to_owned()));
    }
    if let Some(existing) = load_goal(root)? {
        if existing.status.is_open() {
            return Err(GoalError::Invalid(format!(
                "goal {} ({}) is still {}; update it to done or abandoned before creating another",
                existing.id,
                existing.title,
                existing.status.as_str()
            )));
        }
    }
    let now = now_millis();
    let goal = Goal {
        id: format!("goal-{now}"),
        title: title.to_owned(),
        detail: detail.unwrap_or_default().trim().to_owned(),
        status: GoalStatus::Active,
        progress: Vec::new(),
        created_at: now,
        updated_at: now,
    };
    save_goal(root, &goal)?;
    Ok(goal)
}

/// Change a goal's status or append a progress note.
pub fn update_goal(
    root: &Path,
    status: Option<&str>,
    note: Option<&str>,
    title: Option<&str>,
) -> Result<Goal, GoalError> {
    let mut goal = load_goal(root)?
        .ok_or_else(|| GoalError::Invalid("no goal exists in this workspace".to_owned()))?;
    if status.is_none() && note.is_none() && title.is_none() {
        return Err(GoalError::Invalid(
            "update needs at least one of status, note, or title".to_owned(),
        ));
    }
    if let Some(status) = status {
        goal.status = GoalStatus::parse(status).ok_or_else(|| {
            GoalError::Invalid(format!(
                "unknown status {status}; expected active, paused, done, or abandoned"
            ))
        })?;
    }
    if let Some(title) = title {
        let title = title.trim();
        if title.is_empty() {
            return Err(GoalError::Invalid("title must not be empty".to_owned()));
        }
        goal.title = title.to_owned();
    }
    if let Some(note) = note {
        let note = note.trim();
        if note.is_empty() {
            return Err(GoalError::Invalid("note must not be empty".to_owned()));
        }
        goal.progress.push(GoalNote {
            at: now_millis(),
            note: note.to_owned(),
        });
    }
    goal.updated_at = now_millis();
    save_goal(root, &goal)?;
    Ok(goal)
}

#[must_use]
pub fn goal_summary(goal: &Goal, notes: usize) -> String {
    let mut summary = format!("{} [{}] {}", goal.id, goal.status.as_str(), goal.title);
    if !goal.detail.is_empty() {
        summary.push_str(&format!("\n{}", goal.detail));
    }
    if notes > 0 && !goal.progress.is_empty() {
        summary.push_str("\nprogress:");
        for note in goal
            .progress
            .iter()
            .skip(goal.progress.len().saturating_sub(notes))
        {
            summary.push_str(&format!("\n- {}", note.note));
        }
    }
    summary
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
    fn one_open_goal_at_a_time_and_notes_accumulate() {
        let directory = tempdir().expect("tempdir");
        assert!(load_goal(directory.path()).expect("load").is_none());

        let goal = create_goal(
            directory.path(),
            "Ship the search rewrite",
            Some("Index first"),
        )
        .expect("create");
        assert_eq!(goal.status, GoalStatus::Active);
        assert!(create_goal(directory.path(), "Something else", None).is_err());

        let updated =
            update_goal(directory.path(), None, Some("Indexed 400 files"), None).expect("note");
        assert_eq!(updated.progress.len(), 1);
        assert_eq!(
            goal_summary(&updated, 5)
                .matches("Indexed 400 files")
                .count(),
            1
        );

        assert!(update_goal(directory.path(), Some("shipping"), None, None).is_err());
        let done = update_goal(directory.path(), Some("done"), Some("Merged"), None).expect("done");
        assert_eq!(done.status, GoalStatus::Done);

        // A closed goal no longer blocks a new objective.
        let next = create_goal(directory.path(), "Next thing", None).expect("next");
        assert_eq!(next.title, "Next thing");
        assert!(create_goal(directory.path(), " ", None).is_err());
        assert!(update_goal(directory.path(), None, None, None).is_err());
    }
}
