//! Durable plan mode.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum PlanError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid plan: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlanStep {
    pub id: String,
    pub text: String,
    #[serde(default = "step_state")]
    pub status: String,
    #[serde(default)]
    pub verify_with: Option<String>,
    #[serde(default)]
    pub evidence: Option<String>,
}

fn step_state() -> String {
    "todo".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Plan {
    pub goal: String,
    #[serde(default = "plan_status")]
    pub status: String,
    #[serde(default)]
    pub steps: Vec<PlanStep>,
    pub created_at: i64,
    pub updated_at: i64,
    #[serde(default)]
    pub approved_at: Option<i64>,
}

fn plan_status() -> String {
    "draft".to_owned()
}

#[must_use]
pub fn plan_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("plan.json")
}

pub fn load_plan(root: &Path) -> Result<Option<Plan>, PlanError> {
    let path = plan_path(root);
    if !path.is_file() {
        return Ok(None);
    }
    let content = std::fs::read_to_string(path)?;
    serde_json::from_str(&content)
        .map(Some)
        .map_err(|error| PlanError::Invalid(error.to_string()))
}

pub fn save_plan(root: &Path, plan: &Plan) -> Result<(), PlanError> {
    let path = plan_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(
        path,
        serde_json::to_string_pretty(plan)
            .map_err(|error| PlanError::Invalid(error.to_string()))?,
    )?;
    Ok(())
}

pub fn approve_plan(root: &Path) -> Result<Plan, PlanError> {
    let mut plan = load_plan(root)?.ok_or_else(|| PlanError::Invalid("no plan".to_owned()))?;
    plan.status = "approved".to_owned();
    plan.approved_at = Some(now_millis());
    plan.updated_at = now_millis();
    save_plan(root, &plan)?;
    Ok(plan)
}

#[must_use]
pub fn plan_summary(plan: &Plan) -> String {
    let completed = plan
        .steps
        .iter()
        .filter(|step| step.status == "done")
        .count();
    let mut summary = format!(
        "Plan ({}, {}/{} steps): {}\n",
        plan.status,
        completed,
        plan.steps.len(),
        plan.goal
    );
    for step in &plan.steps {
        summary.push_str(&format!(
            "- [{}] {} {}{}\n",
            step.status,
            step.id,
            step.text,
            step.verify_with
                .as_deref()
                .map(|recipe| format!(" (verify: {recipe})"))
                .unwrap_or_default()
        ));
    }
    if plan.status == "draft" {
        summary.push_str(
            "The plan is draft: do not execute it until a human approves it in Project → Plan.\n",
        );
    }
    summary
}

pub fn update_step(
    root: &Path,
    id: &str,
    status: &str,
    evidence: Option<&str>,
) -> Result<Plan, PlanError> {
    if !matches!(status, "todo" | "doing" | "done" | "blocked" | "dropped") {
        return Err(PlanError::Invalid(format!("unknown step status: {status}")));
    }
    let mut plan = load_plan(root)?.ok_or_else(|| PlanError::Invalid("no plan".to_owned()))?;
    let step = plan
        .steps
        .iter_mut()
        .find(|step| step.id == id)
        .ok_or_else(|| PlanError::Invalid(format!("unknown plan step: {id}")))?;
    step.status = status.to_owned();
    if let Some(evidence) = evidence {
        step.evidence = Some(evidence.to_owned());
    }
    plan.updated_at = now_millis();
    save_plan(root, &plan)?;
    Ok(plan)
}

fn now_millis() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as i64)
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn plan_round_trip_approval_and_step_updates() {
        let directory = tempdir().expect("tempdir");
        let plan = Plan {
            goal: "Ship a release".to_owned(),
            status: "draft".to_owned(),
            steps: vec![PlanStep {
                id: "1".to_owned(),
                text: "Run tests".to_owned(),
                status: "todo".to_owned(),
                verify_with: Some("verify".to_owned()),
                evidence: None,
            }],
            created_at: 1,
            updated_at: 1,
            approved_at: None,
        };
        save_plan(directory.path(), &plan).expect("save");
        assert_eq!(load_plan(directory.path()).expect("load"), Some(plan));
        let approved = approve_plan(directory.path()).expect("approve");
        assert_eq!(approved.status, "approved");
        assert!(approved.approved_at.is_some());
        let updated =
            update_step(directory.path(), "1", "done", Some("tests passed")).expect("step");
        assert_eq!(updated.steps[0].status, "done");
        assert!(updated.steps[0].evidence.as_deref() == Some("tests passed"));
        assert!(update_step(directory.path(), "1", "invalid", None).is_err());
    }
}
