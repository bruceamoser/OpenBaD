//! Claims and known unknowns.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum LearningError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid learning data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    pub id: String,
    pub claim: String,
    pub confidence: String,
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default)]
    pub how_to_check: Option<String>,
    #[serde(default)]
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unknown {
    pub id: String,
    pub question: String,
    #[serde(default)]
    pub context: Option<String>,
    #[serde(default)]
    pub created_at: i64,
}

#[must_use]
pub fn claims_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("claims.json")
}

#[must_use]
pub fn unknowns_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("unknowns.json")
}

pub fn load_claims(root: &Path) -> Result<Vec<Claim>, LearningError> {
    let path = claims_path(root);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(path)?;
    serde_json::from_str(&content).map_err(|error| LearningError::Invalid(error.to_string()))
}

pub fn save_claims(root: &Path, claims: &[Claim]) -> Result<(), LearningError> {
    let path = claims_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(
        path,
        serde_json::to_string_pretty(claims)
            .map_err(|error| LearningError::Invalid(error.to_string()))?,
    )?;
    Ok(())
}

pub fn load_unknowns(root: &Path) -> Result<Vec<Unknown>, LearningError> {
    let path = unknowns_path(root);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(path)?;
    serde_json::from_str(&content).map_err(|error| LearningError::Invalid(error.to_string()))
}

pub fn save_unknowns(root: &Path, unknowns: &[Unknown]) -> Result<(), LearningError> {
    let path = unknowns_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(
        path,
        serde_json::to_string_pretty(unknowns)
            .map_err(|error| LearningError::Invalid(error.to_string()))?,
    )?;
    Ok(())
}

#[must_use]
pub fn validate_confidence(confidence: &str) -> bool {
    matches!(confidence, "executed" | "read" | "inferred" | "assumed")
}

#[must_use]
pub fn learning_summary(claims: &[Claim], unknowns: &[Unknown], limit: usize) -> String {
    let mut summary = String::new();
    if !unknowns.is_empty() {
        summary.push_str("Known unknowns:\n");
        for unknown in unknowns.iter().take(limit) {
            summary.push_str(&format!("- [{}] {}\n", unknown.id, unknown.question));
        }
    }
    if !claims.is_empty() {
        summary.push_str("Recent claims:\n");
        for claim in claims.iter().rev().take(limit) {
            summary.push_str(&format!(
                "- [{}] {} {}\n",
                claim.id, claim.confidence, claim.claim
            ));
        }
    }
    summary
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn claims_and_unknowns_round_trip() {
        let directory = tempdir().expect("tempdir");
        let claims = vec![Claim {
            id: "claim-1".to_owned(),
            claim: "The build passes".to_owned(),
            confidence: "executed".to_owned(),
            evidence: vec!["cargo test".to_owned()],
            how_to_check: Some("cargo test --workspace".to_owned()),
            created_at: 1,
        }];
        save_claims(directory.path(), &claims).expect("claims");
        assert_eq!(load_claims(directory.path()).expect("load"), claims);
        let unknowns = vec![Unknown {
            id: "q-1".to_owned(),
            question: "Which database?".to_owned(),
            context: None,
            created_at: 1,
        }];
        save_unknowns(directory.path(), &unknowns).expect("unknowns");
        assert_eq!(load_unknowns(directory.path()).expect("load"), unknowns);
        let summary = learning_summary(&claims, &unknowns, 5);
        assert!(summary.contains("Which database?"));
        assert!(summary.contains("executed"));
    }
}
