//! Tickets and the states they move through.
//!
//! A ticket is the durable unit of work an accept creates. Its persistence lives
//! in `openbad-store`; the rules about which states may follow which are here, so
//! they are testable without a database and cannot drift between callers.
//!
//! Two axes are deliberately independent:
//!
//! * **Ticket state** — whether work may still reach this ticket.
//! * **Assignment state** — whether an executor is currently running.
//!
//! A ticket can be open with no assignment (queued), and an ended assignment does
//! not end its ticket. Collapsing the two would lose the difference between "the
//! executor finished its turn" and "this work is over", which is exactly the case
//! a person's message needs to distinguish.

use serde::{Deserialize, Serialize};

/// Where a ticket is in its life.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TicketState {
    /// Work may reach it.
    Open,
    /// The executor's side finished without a stop. A person's message resumes it
    /// under the mandate it started with.
    Ended,
    /// A person stopped it. Nothing reaches it, and it never moves again — restarting
    /// stopped work is a new admission with a new mandate, not a revival.
    Stopped,
}

impl TicketState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::Ended => "ended",
            Self::Stopped => "stopped",
        }
    }

    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "open" => Some(Self::Open),
            "ended" => Some(Self::Ended),
            "stopped" => Some(Self::Stopped),
            _ => None,
        }
    }

    /// Whether input addressed to this ticket reaches it.
    ///
    /// Only an open ticket. An ended ticket is resumed by a person's message
    /// through [`Self::can_move_to`], not by delivering into it.
    #[must_use]
    pub const fn accepts_input(self) -> bool {
        matches!(self, Self::Open)
    }

    /// Whether this transition is allowed.
    #[must_use]
    pub const fn can_move_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::Open, Self::Ended | Self::Stopped) | (Self::Ended, Self::Open | Self::Stopped)
        )
    }
}

/// Where an assignment is in its life.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AssignmentState {
    /// Created, not yet running.
    Pending,
    /// An executor is working.
    Running,
    /// This assignment is over. The ticket may get another.
    Ended,
}

impl AssignmentState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Running => "running",
            Self::Ended => "ended",
        }
    }

    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "pending" => Some(Self::Pending),
            "running" => Some(Self::Running),
            "ended" => Some(Self::Ended),
            _ => None,
        }
    }

    /// Whether this assignment still holds the ticket's executor slot.
    #[must_use]
    pub const fn is_active(self) -> bool {
        !matches!(self, Self::Ended)
    }
}

/// What an assignment reports about its ticket.
///
/// A report is the ticket's current state as the executor last left it, not a
/// stream of events: an assignment updates its own report as it works, and the
/// latest one is what the ticket shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReportOutcome {
    /// Still working. The only interim value.
    Progress,
    /// This turn is finished and the ticket is idle until something wakes it.
    Waiting,
    /// A reviewable result exists.
    Ready,
    /// Something outside the executor's control has to change.
    Blocked,
    /// The work failed.
    Failed,
    /// There was nothing to do, or the work is no longer wanted.
    Dropped,
}

impl ReportOutcome {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Progress => "progress",
            Self::Waiting => "waiting",
            Self::Ready => "ready",
            Self::Blocked => "blocked",
            Self::Failed => "failed",
            Self::Dropped => "dropped",
        }
    }

    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "progress" => Some(Self::Progress),
            "waiting" => Some(Self::Waiting),
            "ready" => Some(Self::Ready),
            "blocked" => Some(Self::Blocked),
            "failed" => Some(Self::Failed),
            "dropped" => Some(Self::Dropped),
            _ => None,
        }
    }

    /// Whether this report concludes the assignment's turn.
    #[must_use]
    pub const fn is_final(self) -> bool {
        !matches!(self, Self::Progress)
    }

    /// Whether a person has to act before the ticket can continue.
    ///
    /// `ready` asks for review and `blocked` asks for a decision. `failed` is not
    /// included: it is reported as a failure, and only a person's message starts it
    /// again — it does not carry a question of its own.
    #[must_use]
    pub const fn needs_person(self) -> bool {
        matches!(self, Self::Ready | Self::Blocked)
    }

    /// Whether this report ends the ticket from the executor's side.
    ///
    /// Only `dropped` and `failed`. `ready` waits for a person to merge, `blocked`
    /// waits for a person to unblock, and `waiting` is idle by definition.
    #[must_use]
    pub const fn ends_ticket(self) -> bool {
        matches!(self, Self::Dropped | Self::Failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_open_ticket_can_end_or_be_stopped() {
        assert!(TicketState::Open.can_move_to(TicketState::Ended));
        assert!(TicketState::Open.can_move_to(TicketState::Stopped));
    }

    #[test]
    fn an_ended_ticket_resumes_and_can_still_be_stopped() {
        assert!(TicketState::Ended.can_move_to(TicketState::Open));
        assert!(TicketState::Ended.can_move_to(TicketState::Stopped));
    }

    #[test]
    fn a_stopped_ticket_never_moves_again() {
        for next in [TicketState::Open, TicketState::Ended, TicketState::Stopped] {
            assert!(!TicketState::Stopped.can_move_to(next));
        }
    }

    #[test]
    fn no_state_transitions_to_itself() {
        for state in [TicketState::Open, TicketState::Ended, TicketState::Stopped] {
            assert!(!state.can_move_to(state));
        }
    }

    #[test]
    fn only_an_open_ticket_accepts_input() {
        assert!(TicketState::Open.accepts_input());
        assert!(!TicketState::Ended.accepts_input());
        assert!(!TicketState::Stopped.accepts_input());
    }

    #[test]
    fn a_pending_or_running_assignment_holds_the_slot() {
        assert!(AssignmentState::Pending.is_active());
        assert!(AssignmentState::Running.is_active());
        assert!(!AssignmentState::Ended.is_active());
    }

    #[test]
    fn only_progress_is_an_interim_report() {
        assert!(!ReportOutcome::Progress.is_final());
        for outcome in [
            ReportOutcome::Waiting,
            ReportOutcome::Ready,
            ReportOutcome::Blocked,
            ReportOutcome::Failed,
            ReportOutcome::Dropped,
        ] {
            assert!(outcome.is_final());
        }
    }

    #[test]
    fn ready_and_blocked_ask_for_a_person_and_the_others_do_not() {
        assert!(ReportOutcome::Ready.needs_person());
        assert!(ReportOutcome::Blocked.needs_person());
        for outcome in [
            ReportOutcome::Progress,
            ReportOutcome::Waiting,
            ReportOutcome::Failed,
            ReportOutcome::Dropped,
        ] {
            assert!(!outcome.needs_person());
        }
    }

    #[test]
    fn only_a_dropped_or_failed_report_ends_the_ticket() {
        assert!(ReportOutcome::Dropped.ends_ticket());
        assert!(ReportOutcome::Failed.ends_ticket());
        for outcome in [
            ReportOutcome::Progress,
            ReportOutcome::Waiting,
            ReportOutcome::Ready,
            ReportOutcome::Blocked,
        ] {
            assert!(!outcome.ends_ticket());
        }
    }

    #[test]
    fn every_state_round_trips_through_its_stored_form() {
        for state in [TicketState::Open, TicketState::Ended, TicketState::Stopped] {
            assert_eq!(TicketState::parse(state.as_str()), Some(state));
        }
        for state in [
            AssignmentState::Pending,
            AssignmentState::Running,
            AssignmentState::Ended,
        ] {
            assert_eq!(AssignmentState::parse(state.as_str()), Some(state));
        }
        for outcome in [
            ReportOutcome::Progress,
            ReportOutcome::Waiting,
            ReportOutcome::Ready,
            ReportOutcome::Blocked,
            ReportOutcome::Failed,
            ReportOutcome::Dropped,
        ] {
            assert_eq!(ReportOutcome::parse(outcome.as_str()), Some(outcome));
        }
    }

    #[test]
    fn an_unknown_stored_value_is_not_silently_a_default() {
        assert_eq!(TicketState::parse("paused"), None);
        assert_eq!(AssignmentState::parse("paused"), None);
        assert_eq!(ReportOutcome::parse("done"), None);
    }
}
