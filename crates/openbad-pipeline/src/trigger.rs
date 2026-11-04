//! Triggers: immutable facts from a source, addressed to a thread.

use serde::{Deserialize, Serialize};

/// A stable identity for one trigger.
///
/// The id is derived from the source, the kind, the thread and the provider's
/// own id for the event, so polling the same window twice yields the same ids and
/// accepting a trigger twice cannot create two work items. It is also readable:
/// when a duplicate is suppressed, the log says which event it was.
pub type TriggerId = String;

/// What a source observed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TriggerKind {
    /// An issue carries an admitting label. Not necessarily *opened* — a label
    /// added to an existing issue admits it just as well — which is why the name
    /// says what was admitted rather than what happened.
    IssueAdmitted,
    IssueComment,
    PullRequestReview,
    PullRequestReviewComment,
}

impl TriggerKind {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::IssueAdmitted => "issue_admitted",
            Self::IssueComment => "issue_comment",
            Self::PullRequestReview => "pull_request_review",
            Self::PullRequestReviewComment => "pull_request_review_comment",
        }
    }

    /// Whether this trigger needs a label set to be admitted.
    ///
    /// A labelled issue is the human's authorizing artifact. A comment on an
    /// existing thread is admitted by the thread's ticket subscription instead,
    /// so it carries no labels of its own.
    #[must_use]
    pub const fn needs_labels(self) -> bool {
        matches!(self, Self::IssueAdmitted)
    }
}

/// Which conversation a trigger belongs to.
///
/// GitHub numbers issues and pull requests in one sequence per repository, so
/// `owner/repo#123` identifies one conversation whether it is an issue or a pull
/// request. That is what lets a review comment land on the same thread as the
/// issue that started the work.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ThreadId(pub String);

impl ThreadId {
    #[must_use]
    pub fn issue(repository: &str, number: u64) -> Self {
        Self(format!("{repository}#{number}"))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Where a result may be written back.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Channel {
    /// Repository and number, for the provider to address.
    pub thread: ThreadId,
    /// What kind of write this channel accepts, in the provider's terms.
    pub kind: ChannelKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChannelKind {
    /// Comment on the issue or pull request conversation.
    Conversation,
    /// Reply within a review thread.
    ReviewThread,
}

/// One immutable event from a source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Trigger {
    pub id: TriggerId,
    pub source_id: String,
    pub kind: TriggerKind,
    pub thread: ThreadId,
    /// GitHub login of whoever caused the event.
    pub actor: String,
    /// The labels on the issue at the time of the event, lowercased. Empty for
    /// kinds that carry no labels.
    pub labels: Vec<String>,
    /// The text a human wrote, when there is any.
    pub text: Option<String>,
    /// Provider timestamp in milliseconds since the epoch, used for the read
    /// position.
    pub at_ms: i64,
    /// Provider-native identifier, kept for tracing back to the event.
    pub native_id: String,
    /// The provider's own title for the thread, when it has one.
    pub title: Option<String>,
}

impl Trigger {
    /// Builds a trigger, deriving its stable id.
    #[must_use]
    pub fn new(
        source_id: &str,
        kind: TriggerKind,
        thread: ThreadId,
        native_id: impl Into<String>,
    ) -> Self {
        let native_id = native_id.into();
        Self {
            id: format!(
                "{source_id}:{}:{}:{native_id}",
                kind.as_str(),
                thread.as_str()
            ),
            source_id: source_id.to_owned(),
            kind,
            thread,
            actor: String::new(),
            labels: Vec::new(),
            text: None,
            at_ms: 0,
            native_id,
            title: None,
        }
    }

    /// The channel a result for this trigger belongs on.
    #[must_use]
    pub fn channel(&self) -> Channel {
        let kind = match self.kind {
            TriggerKind::PullRequestReviewComment => ChannelKind::ReviewThread,
            _ => ChannelKind::Conversation,
        };
        Channel {
            thread: self.thread.clone(),
            kind,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_same_event_always_produces_the_same_id() {
        let build = || {
            Trigger::new(
                "main",
                TriggerKind::IssueComment,
                ThreadId::issue("o/r", 7),
                "1234",
            )
        };
        assert_eq!(build().id, build().id);
        assert_eq!(build().id, "main:issue_comment:o/r#7:1234");
    }

    #[test]
    fn different_events_produce_different_ids() {
        let a = Trigger::new(
            "main",
            TriggerKind::IssueAdmitted,
            ThreadId::issue("o/r", 7),
            "7",
        );
        let b = Trigger::new(
            "main",
            TriggerKind::IssueComment,
            ThreadId::issue("o/r", 7),
            "7",
        );
        assert_ne!(a.id, b.id, "the kind is part of the identity");
    }

    #[test]
    fn an_issue_and_its_pull_request_share_one_thread() {
        // GitHub numbers them in one sequence; a review comment must land on the
        // thread the issue opened.
        assert_eq!(ThreadId::issue("o/r", 42).as_str(), "o/r#42");
    }

    #[test]
    fn review_comments_reply_in_their_thread_and_others_in_the_conversation() {
        let review = Trigger::new(
            "main",
            TriggerKind::PullRequestReviewComment,
            ThreadId::issue("o/r", 1),
            "9",
        );
        assert_eq!(review.channel().kind, ChannelKind::ReviewThread);
        let comment = Trigger::new(
            "main",
            TriggerKind::IssueComment,
            ThreadId::issue("o/r", 1),
            "9",
        );
        assert_eq!(comment.channel().kind, ChannelKind::Conversation);
    }
}
