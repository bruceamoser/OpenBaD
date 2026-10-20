//! Structured questions queued for human answers.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum QuestionError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid question data: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Question {
    pub id: String,
    pub question: String,
    #[serde(default)]
    pub options: Vec<String>,
    #[serde(default)]
    pub multi: bool,
    #[serde(default)]
    pub context: Option<String>,
    pub created_at: i64,
    #[serde(default)]
    pub answer: Option<String>,
    #[serde(default)]
    pub answered_at: Option<i64>,
}

#[must_use]
pub fn questions_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("questions.json")
}

pub fn load_questions(root: &Path) -> Result<Vec<Question>, QuestionError> {
    let path = questions_path(root);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path)?;
    serde_json::from_str(&content).map_err(|error| QuestionError::Invalid(error.to_string()))
}

pub fn save_questions(root: &Path, questions: &[Question]) -> Result<(), QuestionError> {
    let path = questions_path(root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_string_pretty(questions)
        .map_err(|error| QuestionError::Invalid(error.to_string()))?;
    std::fs::write(path, content)?;
    Ok(())
}

pub fn answer_question(root: &Path, id: &str, answer: &str) -> Result<Question, QuestionError> {
    let answer = answer.trim();
    if answer.is_empty() {
        return Err(QuestionError::Invalid(
            "answer must not be empty".to_owned(),
        ));
    }
    let mut questions = load_questions(root)?;
    let question = questions
        .iter_mut()
        .find(|question| question.id == id)
        .ok_or_else(|| QuestionError::Invalid(format!("unknown question: {id}")))?;
    question.answer = Some(answer.to_owned());
    question.answered_at = Some(now_millis());
    let answered = question.clone();
    save_questions(root, &questions)?;
    Ok(answered)
}

#[must_use]
pub fn question_summary(questions: &[Question], answered_limit: usize) -> String {
    let pending = questions
        .iter()
        .filter(|question| question.answer.is_none())
        .collect::<Vec<_>>();
    let answered = questions
        .iter()
        .filter(|question| question.answer.is_some())
        .rev()
        .take(answered_limit)
        .collect::<Vec<_>>();
    if pending.is_empty() && answered.is_empty() {
        return String::new();
    }
    let mut summary = String::new();
    if !pending.is_empty() {
        summary.push_str("Pending human questions:\n");
        for question in pending {
            summary.push_str(&format!("- [{}] {}", question.id, question.question));
            if !question.options.is_empty() {
                summary.push_str(&format!(" (options: {})", question.options.join(", ")));
            }
            summary.push('\n');
        }
    }
    if !answered.is_empty() {
        summary.push_str("Answered human questions:\n");
        for question in answered {
            summary.push_str(&format!(
                "- {} → {}\n",
                question.question,
                question.answer.as_deref().unwrap_or_default()
            ));
        }
    }
    summary
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
    fn questions_round_trip_and_accept_one_answer() {
        let directory = tempdir().expect("tempdir");
        let questions = vec![Question {
            id: "q1".to_owned(),
            question: "Which database should we use?".to_owned(),
            options: vec!["SQLite".to_owned(), "Postgres".to_owned()],
            multi: false,
            context: Some("storage decision".to_owned()),
            created_at: 1,
            answer: None,
            answered_at: None,
        }];
        save_questions(directory.path(), &questions).expect("save");
        assert_eq!(load_questions(directory.path()).expect("load"), questions);
        let answered = answer_question(directory.path(), "q1", "SQLite").expect("answer");
        assert_eq!(answered.answer.as_deref(), Some("SQLite"));
        assert!(answered.answered_at.is_some());
        assert!(answer_question(directory.path(), "q1", " ").is_err());
        assert!(answer_question(directory.path(), "missing", "x").is_err());
        let summary = question_summary(&load_questions(directory.path()).expect("load"), 5);
        assert!(summary.contains("SQLite"));
        assert!(!summary.contains("Pending human questions"));
    }
}
