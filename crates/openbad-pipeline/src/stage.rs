//! The stage a ticket is in, derived from structured facts.
//!
//! The prototype rejects inferring "waiting on a person" from a report's free
//! text, and this project has already paid for that lesson once: tool errors
//! carried no way to tell a recoverable refusal from a harness fault until a
//! structured `recoverable` flag existed. So a stage is computed from rows, never
//! from prose, and it is derived on read rather than stored.

use serde::Serialize;

use crate::ticket::{AssignmentState, ReportOutcome, TicketState};

/// Where a ticket is, in the terms a person asks about it.
///
/// `Person*` stages are the answer to "is this waiting on me?"; everything else
/// is the pipeline's own business.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Stage {
    /// Delivering a person's message to an executor failed and is retrying.
    #[serde(rename = "person/delivery")]
    PersonDelivery,
    /// The executor asked a question and is waiting for the answer.
    #[serde(rename = "person/answer")]
    PersonAnswer,
    /// There is a reviewable pull request.
    #[serde(rename = "person/review")]
    PersonReview,
    /// The executor is stuck on something outside its control.
    #[serde(rename = "person/blocked")]
    PersonBlocked,
    /// The work failed and a person has to decide what happens next.
    #[serde(rename = "person/failed")]
    PersonFailed,
    /// No executor is on it, or one is about to start.
    #[serde(rename = "working/queued")]
    WorkingQueued,
    /// The executor finished its turn and the ticket is idle until something
    /// wakes it.
    #[serde(rename = "idle")]
    Idle,
    /// An executor is working right now.
    #[serde(rename = "working")]
    Working,
    /// The ticket is over; the report says how.
    #[serde(rename = "ended")]
    Ended,
}

impl Stage {
    /// Whether this stage is the pipeline asking a person for something.
    #[must_use]
    pub const fn needs_a_person(self) -> bool {
        matches!(
            self,
            Self::PersonDelivery
                | Self::PersonAnswer
                | Self::PersonReview
                | Self::PersonBlocked
                | Self::PersonFailed
        )
    }

    /// The stable name a UI or a log can carry.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PersonDelivery => "person/delivery",
            Self::PersonAnswer => "person/answer",
            Self::PersonReview => "person/review",
            Self::PersonBlocked => "person/blocked",
            Self::PersonFailed => "person/failed",
            Self::WorkingQueued => "working/queued",
            Self::Idle => "idle",
            Self::Working => "working",
            Self::Ended => "ended",
        }
    }
}

/// The structured facts a stage is decided from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StageInput {
    pub ticket: TicketState,
    pub assignment: Option<AssignmentState>,
    pub report: Option<ReportOutcome>,
    /// Whether the ticket has an open attention (a question waiting for a person).
    pub attention: bool,
    /// Whether a person's message failed to reach an executor and is retrying.
    pub delivery_failed: bool,
}

impl Default for StageInput {
    fn default() -> Self {
        Self {
            ticket: TicketState::Open,
            assignment: None,
            report: None,
            attention: false,
            delivery_failed: false,
        }
    }
}

/// Which stage a ticket is in. First match wins.
#[must_use]
pub fn stage_of(input: StageInput) -> Stage {
    if input.ticket != TicketState::Open {
        return Stage::Ended;
    }
    if input.delivery_failed {
        return Stage::PersonDelivery;
    }
    if input.attention {
        return Stage::PersonAnswer;
    }
    match input.report {
        Some(ReportOutcome::Ready) => Stage::PersonReview,
        Some(ReportOutcome::Blocked) => Stage::PersonBlocked,
        Some(ReportOutcome::Failed) => Stage::PersonFailed,
        // `progress` is the only interim value: an executor with a live report is
        // working, whatever else the rows say.
        Some(ReportOutcome::Progress) => Stage::Working,
        Some(ReportOutcome::Waiting | ReportOutcome::Dropped) => Stage::Idle,
        None => match input.assignment {
            None | Some(AssignmentState::Pending) => Stage::WorkingQueued,
            Some(AssignmentState::Running) => Stage::Working,
            Some(AssignmentState::Ended) => Stage::Idle,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input() -> StageInput {
        StageInput {
            ticket: TicketState::Open,
            assignment: None,
            report: None,
            attention: false,
            delivery_failed: false,
        }
    }

    #[test]
    fn an_ended_ticket_is_ended_whatever_it_last_said() {
        assert_eq!(
            stage_of(StageInput {
                ticket: TicketState::Ended,
                report: Some(ReportOutcome::Failed),
                ..input()
            }),
            Stage::Ended
        );
        assert_eq!(
            stage_of(StageInput {
                ticket: TicketState::Stopped,
                ..input()
            }),
            Stage::Ended
        );
    }

    #[test]
    fn a_person_is_asked_first_for_the_most_actionable_thing() {
        assert_eq!(
            stage_of(StageInput {
                attention: true,
                report: Some(ReportOutcome::Ready),
                assignment: Some(AssignmentState::Running),
                ..input()
            }),
            Stage::PersonAnswer,
            "a question outranks a review: the executor cannot continue without it"
        );
        assert_eq!(
            stage_of(StageInput {
                report: Some(ReportOutcome::Ready),
                ..input()
            }),
            Stage::PersonReview
        );
        assert_eq!(
            stage_of(StageInput {
                report: Some(ReportOutcome::Blocked),
                ..input()
            }),
            Stage::PersonBlocked
        );
        assert_eq!(
            stage_of(StageInput {
                report: Some(ReportOutcome::Failed),
                ..input()
            }),
            Stage::PersonFailed
        );
    }

    #[test]
    fn an_open_ticket_with_nothing_on_it_is_queued_not_idle() {
        assert_eq!(stage_of(input()), Stage::WorkingQueued);
        assert_eq!(
            stage_of(StageInput {
                assignment: Some(AssignmentState::Pending),
                ..input()
            }),
            Stage::WorkingQueued,
            "prepared but not started is still queued"
        );
        assert_eq!(
            stage_of(StageInput {
                assignment: Some(AssignmentState::Running),
                ..input()
            }),
            Stage::Working
        );
        assert_eq!(
            stage_of(StageInput {
                report: Some(ReportOutcome::Waiting),
                ..input()
            }),
            Stage::Idle,
            "a finished turn that said nothing is idle, not failed"
        );
    }

    #[test]
    fn only_the_person_stages_need_a_person() {
        assert!(Stage::PersonReview.needs_a_person());
        assert!(!Stage::Working.needs_a_person());
        assert!(!Stage::Ended.needs_a_person());
        assert_eq!(Stage::PersonAnswer.as_str(), "person/answer");
    }
}
