//! A review pass over the branch an executor produced.
//!
//! The reviewing agent from ADR-009 lives outside the domain as a GitHub actor.
//! Without one installed, a project can ask the pipeline for a second pass
//! instead: a fresh agent, with none of the turn's history, that reads the diff,
//! runs the tests, and has to answer. It is deliberately not the executor asked
//! "are you sure?" — the value is a context that has not spent the last hour
//! convincing itself the work is done.
//!
//! Two things keep the pass honest:
//!
//! * It may not change the branch. A reviewer that edits what it reviews has not
//!   reviewed it, so the caller compares the commit before and after and treats a
//!   moved head as a rejection.
//! * The verdict is structured. [`parse_verdict`] reads the first line, so
//!   "approved" buried in prose cannot be mistaken for a decision.

use crate::config::{ExecutionConfig, ReviewPolicy};

/// What a review pass decided.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub approved: bool,
    /// Why, in the reviewer's own words. Always populated: an approval with no
    /// reason is as unreadable later as a rejection with none.
    pub reason: String,
}

impl Verdict {
    fn rejected(reason: impl Into<String>) -> Self {
        Self {
            approved: false,
            reason: reason.into(),
        }
    }
}

/// Reads a reviewer's answer.
///
/// The first non-empty line decides. Anything else is a rejection: a reviewer
/// that cannot say "approve" plainly has not reviewed, and silence must never
/// merge a branch.
#[must_use]
pub fn parse_verdict(text: &str) -> Verdict {
    let line = text
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or_default();
    if line.is_empty() {
        return Verdict::rejected("the reviewer said nothing");
    }
    // A reviewer may make the word bold or quote it; that is formatting, not a
    // different answer, and rejecting it would lose a real review over asterisks.
    let line = line.trim_start_matches(['#', '*', '-', '>', ' ', '"', '\'']);
    let upper = line.to_ascii_uppercase();
    if upper.starts_with("APPROVE") || upper.starts_with("APPROVED") {
        return Verdict {
            approved: true,
            reason: reason_after(line, "APPROVE"),
        };
    }
    if upper.starts_with("REJECT") || upper.starts_with("REJECTED") {
        return Verdict::rejected(reason_after(line, "REJECT"));
    }
    Verdict::rejected(format!(
        "the reviewer did not answer approve or reject (began: {})",
        line.chars().take(80).collect::<String>()
    ))
}

/// The text after the verdict word, on the same line.
fn reason_after(line: &str, word: &str) -> String {
    let upper = line.to_ascii_uppercase();
    let rest = match upper.find(word) {
        Some(at) => &line[at + word.len()..],
        None => line,
    };
    let rest = rest.trim_start_matches([':', '-', '—', ' ']).trim();
    if rest.is_empty() {
        word.to_ascii_lowercase()
    } else {
        rest.to_owned()
    }
}

/// What the reviewing agent is told to do.
///
/// It names the branch, forbids editing, and requires the first line to be the
/// verdict — the parser and the instruction have to agree, or every review looks
/// like a rejection.
#[must_use]
pub fn review_prompt(thread: &str, branch: &str, base: &str, tests: Option<&str>) -> String {
    let tests = match tests.map(str::trim).filter(|command| !command.is_empty()) {
        Some(command) => format!("Run `{command}` and include the result.\n"),
        None => String::new(),
    };
    format!(
        "You are reviewing work another agent just finished, not doing it.\n\n\
         Thread: {thread}\n\
         Branch: {branch} (the base is {base})\n\n\
         Read the change as a reviewer: `git diff {base}...HEAD`, the files it \
         touches, and enough surrounding code to judge it. Check that it does what \
         the thread asked, that it does not break what was working, and that \
         nothing was left half-done. {tests}\
         Do not edit, commit, push, or merge anything: a reviewer that changes the \
         branch has not reviewed it, and a moved head rejects the review.\n\n\
         Answer with your verdict as the very first line, exactly one of:\n\
         APPROVE: <one sentence>\n\
         REJECT: <the specific problem and what would fix it>\n\n\
         Then, below that line, the reasoning a person would need to disagree with \
         you."
    )
}

/// Whether the project asked for a review pass at all.
#[must_use]
pub fn reviews_before_merge(execution: &ExecutionConfig) -> bool {
    !matches!(execution.review, ReviewPolicy::None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_verdict_is_read_from_the_first_line() {
        assert_eq!(
            parse_verdict("APPROVE: the tests pass and the diff is tight\nthen prose"),
            Verdict {
                approved: true,
                reason: "the tests pass and the diff is tight".to_owned()
            }
        );
        assert_eq!(
            parse_verdict("REJECT: the index is rebuilt on every read"),
            Verdict {
                approved: false,
                reason: "the index is rebuilt on every read".to_owned()
            }
        );
    }

    #[test]
    fn anything_that_is_not_approval_is_a_rejection() {
        for answer in [
            "",
            "   \n\n",
            "Looks good to me!",
            "LGTM",
            "I could not run the tests.",
        ] {
            let verdict = parse_verdict(answer);
            assert!(
                !verdict.approved,
                "silence or prose must never merge a branch: {answer:?}"
            );
            assert!(!verdict.reason.is_empty());
        }
    }

    #[test]
    fn approval_survives_markdown_and_case() {
        assert!(
            parse_verdict("**APPROVED** — clean").approved,
            "formatting is not a different answer"
        );
        assert!(parse_verdict("> APPROVE: quoted").approved);
        assert!(parse_verdict("approve: fine").approved);
        assert!(!parse_verdict("REJECT: no").approved);
    }

    #[test]
    fn the_prompt_names_the_branch_forbids_edits_and_asks_for_the_tests() {
        let prompt = review_prompt(
            "owner/repo#4",
            "worker/abc",
            "main",
            Some("python -m unittest discover -s tests"),
        );
        assert!(prompt.contains("worker/abc"));
        assert!(prompt.contains("owner/repo#4"));
        assert!(prompt.contains("git diff main...HEAD"));
        assert!(prompt.contains("python -m unittest discover -s tests"));
        assert!(
            prompt.contains("Do not edit, commit, push, or merge"),
            "the reviewer must be told not to change what it reviews"
        );
        assert!(prompt.contains("APPROVE:"));
        assert!(prompt.contains("REJECT:"));
    }

    #[test]
    fn a_project_that_asked_for_no_review_gets_none() {
        let mut execution = ExecutionConfig::default();
        assert!(!reviews_before_merge(&execution));
        execution.review = ReviewPolicy::Agent;
        assert!(reviews_before_merge(&execution));
    }
}
