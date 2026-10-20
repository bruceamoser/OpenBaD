//! Canon ledger, glossary, and ADR storage.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum CanonError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid canon data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonEntry {
    pub id: String,
    pub statement: String,
    pub status: String,
    #[serde(default)]
    pub superseded_by: Option<String>,
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default)]
    pub updated_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BannedPhrase {
    pub phrase: String,
    pub replacement: String,
    #[serde(default)]
    pub citation: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonLedger {
    #[serde(default)]
    pub entries: Vec<CanonEntry>,
    #[serde(default)]
    pub banned_phrases: Vec<BannedPhrase>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GlossaryEntry {
    pub term: String,
    pub definition: String,
    #[serde(default)]
    pub expansion: Option<String>,
    #[serde(default)]
    pub aliases: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Glossary {
    #[serde(default)]
    pub entries: Vec<GlossaryEntry>,
}

#[must_use]
pub fn canon_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("canon.yaml")
}

#[must_use]
pub fn glossary_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("glossary.yaml")
}

#[must_use]
pub fn adr_directory(root: &Path) -> PathBuf {
    root.join(".openbad").join("adr")
}

pub fn load_canon(root: &Path) -> Result<CanonLedger, CanonError> {
    let path = canon_path(root);
    if !path.is_file() {
        return Ok(CanonLedger::default());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_yaml::from_str(&content).map_err(|error| CanonError::Invalid(error.to_string()))
}

pub fn save_canon(root: &Path, canon: &CanonLedger) -> Result<(), CanonError> {
    let path = canon_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content =
        serde_yaml::to_string(canon).map_err(|error| CanonError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

pub fn load_glossary(root: &Path) -> Result<Glossary, CanonError> {
    let path = glossary_path(root);
    if !path.is_file() {
        return Ok(Glossary::default());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_yaml::from_str(&content).map_err(|error| CanonError::Invalid(error.to_string()))
}

pub fn save_glossary(root: &Path, glossary: &Glossary) -> Result<(), CanonError> {
    let path = glossary_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content =
        serde_yaml::to_string(glossary).map_err(|error| CanonError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

#[must_use]
pub fn validate_canon_status(status: &str) -> bool {
    matches!(status, "canonical" | "superseded" | "disputed" | "unknown")
}

#[must_use]
pub fn lint_text(ledger: &CanonLedger, text: &str) -> Vec<(String, String, Option<String>)> {
    let lower = text.to_ascii_lowercase();
    ledger
        .banned_phrases
        .iter()
        .filter(|phrase| lower.contains(&phrase.phrase.to_ascii_lowercase()))
        .map(|phrase| {
            (
                phrase.phrase.clone(),
                phrase.replacement.clone(),
                phrase.citation.clone(),
            )
        })
        .collect()
}

#[must_use]
pub fn current_truth<'a>(ledger: &'a CanonLedger, topic: &str) -> Vec<&'a CanonEntry> {
    let topic = topic.to_ascii_lowercase();
    ledger
        .entries
        .iter()
        .filter(|entry| {
            entry.status == "canonical"
                && (entry.id.to_ascii_lowercase().contains(&topic)
                    || entry.statement.to_ascii_lowercase().contains(&topic))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn canon_round_trip_and_lint() {
        let directory = tempdir().expect("tempdir");
        let mut ledger = CanonLedger::default();
        ledger.entries.push(CanonEntry {
            id: "CANON-1".to_owned(),
            statement: "The case contains one phial.".to_owned(),
            status: "canonical".to_owned(),
            superseded_by: None,
            evidence: vec!["rewrite#ch3".to_owned()],
            updated_at: 1,
        });
        ledger.banned_phrases.push(BannedPhrase {
            phrase: "two phials".to_owned(),
            replacement: "one phial".to_owned(),
            citation: Some("rewrite#ch3".to_owned()),
        });
        save_canon(directory.path(), &ledger).expect("save");
        let loaded = load_canon(directory.path()).expect("load");
        assert_eq!(loaded, ledger);
        let hits = lint_text(&loaded, "The case had TWO PHIALS.");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].1, "one phial");
        assert_eq!(current_truth(&loaded, "phial").len(), 1);
    }

    #[test]
    fn glossary_round_trip() {
        let directory = tempdir().expect("tempdir");
        let glossary = Glossary {
            entries: vec![GlossaryEntry {
                term: "CT".to_owned(),
                definition: "Containment Truth".to_owned(),
                expansion: Some("Containment Truth".to_owned()),
                aliases: vec!["containment truth".to_owned()],
            }],
        };
        save_glossary(directory.path(), &glossary).expect("save");
        assert_eq!(load_glossary(directory.path()).expect("load"), glossary);
    }
}
