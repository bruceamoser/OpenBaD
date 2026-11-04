//! A GitHub source: turns repository activity into triggers.
//!
//! The label filter is pushed into the query rather than applied afterwards. That
//! is not an optimisation — it is what makes "a label added to an existing issue"
//! admit work without the source having to remember every issue's labels: adding
//! a label changes the issue's `updated_at`, so the next poll sees it inside the
//! `since` window and emits the same admission trigger a fresh issue would.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use async_trait::async_trait;
use serde::Deserialize;

use crate::config::SourceConfig;
use crate::cursor::SourceCursor;
use crate::error::SourceError;
use crate::trigger::{ThreadId, Trigger, TriggerKind};

/// The read side of GitHub, as the source needs it.
///
/// A trait rather than a client so the poller's logic — the cursor, the dedup,
/// the filtering, the thread identity — is testable without a network or a
/// credential. Everything below the trait is provider plumbing.
#[async_trait]
pub trait GitHubApi: Send + Sync {
    /// `GET /repos/…`, returning the decoded JSON body.
    async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError>;
}

/// Lets a source borrow a reader instead of taking it.
///
/// A source only reads, so requiring ownership would force every caller to hand
/// over something it may still need — the supervisor polling several sources, for
/// one.
#[async_trait]
impl<T: GitHubApi + Sync> GitHubApi for &T {
    async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError> {
        (**self).get(path).await
    }
}

/// Runs `gh api`, which is how this project already reaches GitHub.
pub struct GhCli;

/// Where `gh` is, remembering the answer for the life of the process.
///
/// A desktop app inherits the session's PATH, not a login shell's, so a per-user
/// install under `~/.local/bin` is invisible to it — every poll then fails to
/// launch and the pipeline admits nothing while looking idle. `OPENBAD_GH` names
/// the binary outright; otherwise PATH, then the directories installers use.
fn gh_binary() -> Option<&'static Path> {
    static BINARY: OnceLock<Option<PathBuf>> = OnceLock::new();
    BINARY
        .get_or_init(|| {
            if let Some(explicit) = std::env::var_os("OPENBAD_GH") {
                return Some(PathBuf::from(explicit));
            }
            if let Some(found) = find_gh(
                std::env::var_os("PATH").as_deref(),
                std::env::var_os("HOME").map(PathBuf::from).as_deref(),
            ) {
                return Some(found);
            }
            None
        })
        .as_deref()
}

/// The lookup itself, taking PATH and HOME as arguments so it can be asserted.
fn find_gh(path: Option<&OsStr>, home: Option<&Path>) -> Option<PathBuf> {
    let names: &[&str] = if cfg!(windows) {
        &["gh.exe", "gh.cmd", "gh"]
    } else {
        &["gh"]
    };
    if let Some(path) = path {
        for directory in std::env::split_paths(path) {
            for name in names {
                let candidate = directory.join(name);
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(home) = home {
        candidates.push(home.join(".local/bin/gh"));
    }
    candidates.extend([
        PathBuf::from("/usr/local/bin/gh"),
        PathBuf::from("/usr/bin/gh"),
        PathBuf::from("/opt/homebrew/bin/gh"),
        PathBuf::from("/home/linuxbrew/.linuxbrew/bin/gh"),
    ]);
    if let Some(home) = home {
        candidates.push(home.join(".nix-profile/bin/gh"));
        candidates.push(home.join(".asdf/shims/gh"));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

#[async_trait]
impl GitHubApi for GhCli {
    async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError> {
        let binary = gh_binary().ok_or_else(|| {
            SourceError::Api(
                "cannot find gh: install the GitHub CLI or set OPENBAD_GH to its path".to_owned(),
            )
        })?;
        let output = tokio::process::Command::new(binary)
            .args(["api", "--method", "GET", path])
            .output()
            .await
            .map_err(|error| SourceError::Api(format!("cannot run gh: {error}")))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(SourceError::Api(format!(
                "gh api {path} failed: {}",
                stderr.trim()
            )));
        }
        serde_json::from_slice(&output.stdout)
            .map_err(|error| SourceError::Shape(format!("{path}: {error}")))
    }
}

/// What one poll produced, and what it chose not to produce.
///
/// The counts exist so silence is explainable. "The agent ignored my issue" and
/// "the source dropped it because it had no admitting label" look identical in a
/// list of triggers; the difference is the whole conversation.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PollOutcome {
    pub triggers: Vec<Trigger>,
    pub cursor: SourceCursor,
    pub dropped_own_bot: usize,
    pub dropped_duplicate: usize,
}

/// One configured GitHub source.
pub struct GitHubSource<A: GitHubApi> {
    config: SourceConfig,
    api: A,
}

impl<A: GitHubApi> GitHubSource<A> {
    #[must_use]
    pub fn new(config: SourceConfig, api: A) -> Self {
        Self { config, api }
    }

    #[must_use]
    pub fn config(&self) -> &SourceConfig {
        &self.config
    }

    /// Polls once, returning new triggers and the advanced cursor.
    ///
    /// The caller persists the cursor only after the triggers are recorded; a
    /// crash between the two re-reads the window, and the id set suppresses the
    /// repeats. That ordering is what makes at-least-once delivery safe here.
    pub async fn poll(&self, cursor: &SourceCursor) -> Result<PollOutcome, SourceError> {
        let since = iso8601_from_ms(cursor.since_ms());
        let mut outcome = PollOutcome {
            cursor: cursor.clone(),
            ..PollOutcome::default()
        };

        for trigger in self.admitted_issues(&since).await? {
            self.accept(&mut outcome, trigger);
        }
        for trigger in self.issue_comments(&since).await? {
            self.accept(&mut outcome, trigger);
        }
        for trigger in self.pull_request_reviews(&since).await? {
            self.accept(&mut outcome, trigger);
        }
        Ok(outcome)
    }

    /// Applies the source's own filters: its bot's messages, and duplicates from
    /// the overlap.
    fn accept(&self, outcome: &mut PollOutcome, trigger: Trigger) {
        if self.config.is_own_bot(&trigger.actor) {
            outcome.dropped_own_bot += 1;
            return;
        }
        if outcome.cursor.accept(&trigger.id, trigger.at_ms) {
            outcome.triggers.push(trigger);
        } else {
            outcome.dropped_duplicate += 1;
        }
    }

    /// Issues carrying an admitting label. The label query is what admits work,
    /// so nothing is fetched that could not be admitted.
    async fn admitted_issues(&self, since: &str) -> Result<Vec<Trigger>, SourceError> {
        if self.config.labels.is_empty() {
            return Ok(Vec::new());
        }
        let labels = self
            .config
            .labels
            .iter()
            .map(|label| urlencode(label))
            .collect::<Vec<_>>()
            .join(",");
        let path = format!(
            "/repos/{}/issues?state=open&labels={labels}&since={since}&sort=updated&direction=asc&per_page=100",
            self.config.repository
        );
        let body = self.api.get(&path).await?;
        let issues: Vec<Issue> = serde_json::from_value(body)
            .map_err(|error| SourceError::Shape(format!("issues: {error}")))?;
        Ok(issues
            .into_iter()
            // A pull request is also an issue in this API; its activity arrives
            // as reviews, not as an admission.
            .filter(|issue| issue.pull_request.is_none())
            .map(|issue| {
                let at_ms = issue.updated_at_ms();
                let mut trigger = Trigger::new(
                    &self.config.id,
                    TriggerKind::IssueAdmitted,
                    ThreadId::issue(&self.config.repository, issue.number),
                    issue.number.to_string(),
                );
                trigger.actor = issue.user.login;
                trigger.labels = label_names(&issue.labels);
                trigger.at_ms = at_ms;
                trigger.text = issue.body;
                trigger.title = issue.title;
                trigger
            })
            .collect())
    }

    async fn issue_comments(&self, since: &str) -> Result<Vec<Trigger>, SourceError> {
        let path = format!(
            "/repos/{}/issues/comments?since={since}&sort=updated&direction=asc&per_page=100",
            self.config.repository
        );
        let body = self.api.get(&path).await?;
        let comments: Vec<Comment> = serde_json::from_value(body)
            .map_err(|error| SourceError::Shape(format!("issue comments: {error}")))?;
        Ok(comments
            .into_iter()
            .filter_map(|comment| {
                let number = comment.issue_number()?;
                let at_ms = comment.updated_at_ms();
                let mut trigger = Trigger::new(
                    &self.config.id,
                    TriggerKind::IssueComment,
                    ThreadId::issue(&self.config.repository, number),
                    comment.id.to_string(),
                );
                trigger.actor = comment.user.login;
                trigger.at_ms = at_ms;
                trigger.text = Some(comment.body);
                Some(trigger)
            })
            .collect())
    }

    /// Reviews and review comments on recently updated pull requests.
    ///
    /// GitHub has no repository-wide review feed, so this is one listing plus one
    /// request per pull request. The listing is bounded, and only pull requests
    /// touched inside the window are queried.
    async fn pull_request_reviews(&self, since: &str) -> Result<Vec<Trigger>, SourceError> {
        const MAX_PULL_REQUESTS: usize = 20;
        let path = format!(
            "/repos/{}/pulls?state=all&sort=updated&direction=desc&per_page={MAX_PULL_REQUESTS}",
            self.config.repository
        );
        let body = self.api.get(&path).await?;
        let pulls: Vec<PullRequest> = serde_json::from_value(body)
            .map_err(|error| SourceError::Shape(format!("pull requests: {error}")))?;
        let since_ms = parse_iso8601_ms(since).unwrap_or(0);

        let mut triggers = Vec::new();
        for pull in pulls
            .into_iter()
            .filter(|pull| pull.updated_at_ms() >= since_ms)
        {
            let thread = ThreadId::issue(&self.config.repository, pull.number);

            let reviews_path = format!(
                "/repos/{}/pulls/{}/reviews?per_page=100",
                self.config.repository, pull.number
            );
            for review in self
                .api
                .get(&reviews_path)
                .await?
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter_map(|value| serde_json::from_value::<Review>(value).ok())
            {
                // A review's body is often empty: an approval is a review with
                // no words, and it still has to reach the executor.
                let at_ms = review.submitted_at_ms();
                let mut trigger = Trigger::new(
                    &self.config.id,
                    TriggerKind::PullRequestReview,
                    thread.clone(),
                    review.id.to_string(),
                );
                trigger.actor = review.user.login;
                trigger.at_ms = at_ms;
                trigger.text = review.body.filter(|body| !body.trim().is_empty());
                trigger.title = Some(pull.title.clone());
                triggers.push(trigger);
            }

            let comments_path = format!(
                "/repos/{}/pulls/{}/comments?per_page=100",
                self.config.repository, pull.number
            );
            for comment in self
                .api
                .get(&comments_path)
                .await?
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter_map(|value| serde_json::from_value::<ReviewComment>(value).ok())
            {
                let at_ms = comment.created_at_ms();
                let mut trigger = Trigger::new(
                    &self.config.id,
                    TriggerKind::PullRequestReviewComment,
                    thread.clone(),
                    comment.id.to_string(),
                );
                trigger.actor = comment.user.login;
                trigger.at_ms = at_ms;
                trigger.text = Some(comment.body);
                trigger.title = Some(pull.title.clone());
                triggers.push(trigger);
            }
        }
        Ok(triggers)
    }
}

// --- provider shapes -------------------------------------------------------

#[derive(Debug, Deserialize)]
struct User {
    login: String,
}

#[derive(Debug, Deserialize)]
struct Label {
    name: String,
}

#[derive(Debug, Deserialize)]
struct Issue {
    number: u64,
    title: Option<String>,
    body: Option<String>,
    user: User,
    #[serde(default)]
    labels: Vec<Label>,
    #[serde(default)]
    pull_request: Option<serde_json::Value>,
    #[serde(default)]
    updated_at: Option<String>,
}

impl Issue {
    fn updated_at_ms(&self) -> i64 {
        self.updated_at
            .as_deref()
            .and_then(parse_iso8601_ms)
            .unwrap_or(0)
    }
}

#[derive(Debug, Deserialize)]
struct Comment {
    id: u64,
    body: String,
    user: User,
    issue_url: String,
    #[serde(default)]
    updated_at: Option<String>,
}

impl Comment {
    /// The issue number, taken from the API url that carries it.
    fn issue_number(&self) -> Option<u64> {
        self.issue_url.rsplit('/').next()?.parse().ok()
    }

    fn updated_at_ms(&self) -> i64 {
        self.updated_at
            .as_deref()
            .and_then(parse_iso8601_ms)
            .unwrap_or(0)
    }
}

#[derive(Debug, Deserialize)]
struct PullRequest {
    number: u64,
    title: String,
    #[serde(default)]
    updated_at: Option<String>,
}

impl PullRequest {
    fn updated_at_ms(&self) -> i64 {
        self.updated_at
            .as_deref()
            .and_then(parse_iso8601_ms)
            .unwrap_or(0)
    }
}

#[derive(Debug, Deserialize)]
struct Review {
    id: u64,
    user: User,
    #[serde(default)]
    body: Option<String>,
    #[serde(default)]
    submitted_at: Option<String>,
}

impl Review {
    fn submitted_at_ms(&self) -> i64 {
        self.submitted_at
            .as_deref()
            .and_then(parse_iso8601_ms)
            .unwrap_or(0)
    }
}

#[derive(Debug, Deserialize)]
struct ReviewComment {
    id: u64,
    body: String,
    user: User,
    #[serde(default)]
    created_at: Option<String>,
}

impl ReviewComment {
    fn created_at_ms(&self) -> i64 {
        self.created_at
            .as_deref()
            .and_then(parse_iso8601_ms)
            .unwrap_or(0)
    }
}

fn label_names(labels: &[Label]) -> Vec<String> {
    labels
        .iter()
        .map(|label| label.name.to_lowercase())
        .collect()
}

/// Percent-encodes the characters a label can contain that would break a query.
fn urlencode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

// --- time, without pulling in a date crate --------------------------------

const MS_PER_DAY: i64 = 86_400_000;

/// `2026-11-04T15:30:00Z` from milliseconds since the epoch.
#[must_use]
pub fn iso8601_from_ms(ms: i64) -> String {
    let ms = ms.max(0);
    let days = ms / MS_PER_DAY;
    let rest = ms % MS_PER_DAY;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3_600_000,
        (rest % 3_600_000) / 60_000,
        (rest % 60_000) / 1000
    )
}

/// Parses the subset of RFC 3339 that GitHub emits into milliseconds.
#[must_use]
pub fn parse_iso8601_ms(text: &str) -> Option<i64> {
    let bytes = text.as_bytes();
    if bytes.len() < 20 {
        return None;
    }
    let number =
        |range: std::ops::Range<usize>| -> Option<i64> { text.get(range)?.parse::<i64>().ok() };
    let year = number(0..4)?;
    let month = number(5..7)?;
    let day = number(8..10)?;
    let hour = number(11..13)?;
    let minute = number(14..16)?;
    let second = number(17..19)?;
    let days = days_from_civil(year, month, day)?;
    Some(days * MS_PER_DAY + hour * 3_600_000 + minute * 60_000 + second * 1000)
}

/// Howard Hinnant's days-from-civil, valid for the range we care about.
fn days_from_civil(year: i64, month: i64, day: i64) -> Option<i64> {
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month_shift = if month > 2 { month - 3 } else { month + 9 };
    let day_of_year = (153 * month_shift + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    Some(era * 146_097 + day_of_era - 719_468)
}

/// The inverse of [`days_from_civil`].
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_shift = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_shift + 2) / 5 + 1;
    let month = if month_shift < 10 {
        month_shift + 3
    } else {
        month_shift - 9
    };
    (year + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::sync::Mutex;

    /// Serves scripted bodies by path prefix and records what was asked for.
    struct FakeApi {
        routes: BTreeMap<String, serde_json::Value>,
        requested: Mutex<Vec<String>>,
    }

    impl FakeApi {
        fn new(routes: &[(&str, serde_json::Value)]) -> Self {
            Self {
                routes: routes
                    .iter()
                    .map(|(path, body)| ((*path).to_owned(), body.clone()))
                    .collect(),
                requested: Mutex::new(Vec::new()),
            }
        }

        fn requested(&self) -> Vec<String> {
            self.requested.lock().expect("requested").clone()
        }
    }

    #[async_trait]
    impl GitHubApi for FakeApi {
        async fn get(&self, path: &str) -> Result<serde_json::Value, SourceError> {
            self.requested
                .lock()
                .expect("requested")
                .push(path.to_owned());
            for (prefix, body) in &self.routes {
                if path.starts_with(prefix.as_str()) {
                    return Ok(body.clone());
                }
            }
            Ok(serde_json::json!([]))
        }
    }

    fn source(labels: &[&str], bots: &[&str]) -> SourceConfig {
        SourceConfig {
            id: "main".to_owned(),
            repository: "o/r".to_owned(),
            enabled: true,
            labels: labels.iter().map(|l| (*l).to_owned()).collect(),
            allowed: Vec::new(),
            bot_logins: bots.iter().map(|b| (*b).to_owned()).collect(),
            known_bots: Vec::new(),
            poll_interval_secs: 120,
        }
    }

    fn issue(number: u64, login: &str, labels: &[&str]) -> serde_json::Value {
        serde_json::json!({
            "number": number,
            "title": "Fix the thing",
            "body": "please",
            "user": {"login": login},
            "labels": labels.iter().map(|l| serde_json::json!({"name": l})).collect::<Vec<_>>(),
            "updated_at": "2026-11-04T10:00:00Z"
        })
    }

    #[tokio::test]
    async fn an_admitted_issue_becomes_a_trigger_on_its_own_thread() {
        let api = FakeApi::new(&[(
            "/repos/o/r/issues?",
            serde_json::json!([issue(7, "alice", &["agent:run"])]),
        )]);
        let source = GitHubSource::new(source(&["agent:run"], &["openbad-bot"]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");

        assert_eq!(outcome.triggers.len(), 1);
        let trigger = &outcome.triggers[0];
        assert_eq!(trigger.kind, TriggerKind::IssueAdmitted);
        assert_eq!(trigger.thread.as_str(), "o/r#7");
        assert_eq!(trigger.actor, "alice");
        assert_eq!(trigger.title.as_deref(), Some("Fix the thing"));
        assert!(trigger.labels.contains(&"agent:run".to_owned()));
        assert_eq!(outcome.cursor.watermark_ms, trigger.at_ms);
    }

    #[tokio::test]
    async fn the_label_filter_is_part_of_the_query() {
        // A label added later changes updated_at, so the same query finds it
        // without the source remembering every issue's labels.
        let api = FakeApi::new(&[]);
        let source = GitHubSource::new(source(&["Agent:Run"], &[]), api);
        let _ = source.poll(&SourceCursor::default()).await.expect("poll");
        let asked = source.api.requested();
        assert!(
            asked[0].contains("labels=Agent%3ARun"),
            "the filter travels in the request: {asked:?}"
        );
    }

    #[tokio::test]
    async fn a_source_with_no_labels_asks_github_for_nothing() {
        let api = FakeApi::new(&[(
            "/repos/o/r/issues?",
            serde_json::json!([issue(1, "a", &["x"])]),
        )]);
        let source = GitHubSource::new(source(&[], &[]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");
        assert!(
            outcome.triggers.is_empty(),
            "no labels means nothing is admitted"
        );
        // Admission is not the only fetch: comments are routing, and a ticket
        // already working on a thread still needs review feedback, so the
        // comment feed is polled even when nothing can be admitted.
        let asked = source.api.requested();
        assert!(
            !asked.iter().any(|path| path.contains("/issues?")),
            "no issue query without labels: {asked:?}"
        );
        assert!(
            asked.iter().any(|path| path.contains("/issues/comments")),
            "{asked:?}"
        );
    }

    #[tokio::test]
    async fn the_agents_own_activity_is_dropped_before_the_domain() {
        let api = FakeApi::new(&[(
            "/repos/o/r/issues?",
            serde_json::json!([
                issue(1, "openbad-bot", &["agent:run"]),
                issue(2, "alice", &["agent:run"])
            ]),
        )]);
        let source = GitHubSource::new(source(&["agent:run"], &["OpenBaD-Bot"]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");

        assert_eq!(outcome.dropped_own_bot, 1, "case-insensitive match");
        assert_eq!(outcome.triggers.len(), 1);
        assert_eq!(outcome.triggers[0].thread.as_str(), "o/r#2");
    }

    #[tokio::test]
    async fn a_pull_request_is_not_admitted_as_an_issue() {
        let mut pull_as_issue = issue(9, "alice", &["agent:run"]);
        pull_as_issue["pull_request"] = serde_json::json!({"url": "https://example/9"});
        let api = FakeApi::new(&[("/repos/o/r/issues?", serde_json::json!([pull_as_issue]))]);
        let source = GitHubSource::new(source(&["agent:run"], &[]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");
        assert!(
            outcome.triggers.is_empty(),
            "its activity arrives as reviews"
        );
    }

    #[tokio::test]
    async fn polling_twice_inside_the_overlap_emits_each_trigger_once() {
        let api = FakeApi::new(&[(
            "/repos/o/r/issues?",
            serde_json::json!([issue(3, "alice", &["agent:run"])]),
        )]);
        let source = GitHubSource::new(source(&["agent:run"], &[]), api);

        let first = source.poll(&SourceCursor::default()).await.expect("first");
        assert_eq!(first.triggers.len(), 1);

        let second = source.poll(&first.cursor).await.expect("second");
        assert!(
            second.triggers.is_empty(),
            "the overlap re-read is suppressed"
        );
        assert_eq!(second.dropped_duplicate, 1);
        assert_eq!(second.cursor, first.cursor, "the position did not move");
    }

    #[tokio::test]
    async fn a_comment_lands_on_the_thread_of_its_issue() {
        let api = FakeApi::new(&[(
            "/repos/o/r/issues/comments?",
            serde_json::json!([{
                "id": 555,
                "body": "any progress?",
                "user": {"login": "alice"},
                "issue_url": "https://api.github.com/repos/o/r/issues/7",
                "updated_at": "2026-11-04T11:00:00Z"
            }]),
        )]);
        let source = GitHubSource::new(source(&["agent:run"], &[]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");

        assert_eq!(outcome.triggers.len(), 1);
        let trigger = &outcome.triggers[0];
        assert_eq!(trigger.kind, TriggerKind::IssueComment);
        assert_eq!(trigger.thread.as_str(), "o/r#7", "same thread as the issue");
        assert_eq!(
            trigger.channel().kind,
            crate::trigger::ChannelKind::Conversation
        );
    }

    #[tokio::test]
    async fn an_approval_with_no_words_still_reaches_the_executor() {
        let api = FakeApi::new(&[
            (
                "/repos/o/r/pulls?",
                serde_json::json!([{
                    "number": 12,
                    "title": "Fix the thing",
                    "updated_at": "2026-11-04T12:00:00Z"
                }]),
            ),
            (
                "/repos/o/r/pulls/12/reviews",
                serde_json::json!([{
                    "id": 900,
                    "user": {"login": "reviewer-bot"},
                    "body": "",
                    "submitted_at": "2026-11-04T12:00:00Z"
                }]),
            ),
        ]);
        let source = GitHubSource::new(source(&["agent:run"], &[]), api);
        let outcome = source.poll(&SourceCursor::default()).await.expect("poll");

        assert_eq!(outcome.triggers.len(), 1);
        let trigger = &outcome.triggers[0];
        assert_eq!(trigger.kind, TriggerKind::PullRequestReview);
        assert_eq!(trigger.thread.as_str(), "o/r#12");
        assert!(trigger.text.is_none(), "an approval has no body");
    }

    #[test]
    fn timestamps_round_trip_through_the_provider_format() {
        for text in [
            "2026-11-04T10:00:00Z",
            "2024-02-29T23:59:59Z",
            "1970-01-01T00:00:00Z",
        ] {
            let ms = parse_iso8601_ms(text).expect("parse");
            assert_eq!(iso8601_from_ms(ms), text, "{text}");
        }
        assert!(parse_iso8601_ms("not a date").is_none());
        assert!(parse_iso8601_ms("2026-13-01T00:00:00Z").is_none());
    }

    #[test]
    fn gh_is_found_without_a_login_shells_path() {
        let empty = tempfile::tempdir().expect("empty");
        let home = tempfile::tempdir().expect("home");
        assert_eq!(
            find_gh(Some(empty.path().as_os_str()), Some(home.path())),
            None,
            "nothing installed, and PATH alone does not have it"
        );

        let bin = home.path().join(".local/bin");
        std::fs::create_dir_all(&bin).expect("dir");
        let binary = bin.join("gh");
        std::fs::write(&binary, "").expect("write");
        assert_eq!(
            find_gh(Some(empty.path().as_os_str()), Some(home.path())),
            Some(binary.clone()),
            "a per-user install is found even though the session PATH omits it"
        );

        assert_eq!(
            find_gh(Some(home.path().join(".local/bin").as_os_str()), None),
            Some(binary.clone()),
            "PATH is still consulted first when it has the binary"
        );
    }
}
