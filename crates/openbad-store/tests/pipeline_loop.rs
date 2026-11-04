//! The assignment loop, end to end, against a fake executor.
//!
//! Every rule here is a decision the supervisor applies around a turn, so the
//! test plays the supervisor's part explicitly: admit, take a slot, launch, take
//! reports, apply their effects. Exercising them together is what shows the
//! decisions compose — a unit test per rule cannot catch a slot released for a
//! report that never ended its assignment.

use openbad_core::WorkspaceId;
use openbad_pipeline::execution::{
    Budget, Candidate, Recovery, SlotPriority, Slots, Spent, effects_of, launch_order, recovery_for,
};
use openbad_pipeline::mandate::{Mandate, MandateStrength};
use openbad_pipeline::ticket::{AssignmentState, ReportOutcome, TicketState};
use openbad_pipeline::trigger::ThreadId;
use openbad_store::pipeline::{
    NewAssignment, NewTicket, active_assignment, create_assignment, create_ticket, latest_report,
    live_ticket, move_assignment, move_ticket, ticket, write_report,
};
use rusqlite::{Connection, params};

/// An executor whose reports are scripted, so each run is deterministic.
struct FakeExecutor {
    reports: Vec<ReportOutcome>,
    issued: usize,
}

impl FakeExecutor {
    fn new(reports: &[ReportOutcome]) -> Self {
        Self {
            reports: reports.to_vec(),
            issued: 0,
        }
    }

    fn next_report(&mut self) -> ReportOutcome {
        let report = self
            .reports
            .get(self.issued)
            .copied()
            .unwrap_or(ReportOutcome::Waiting);
        self.issued += 1;
        report
    }
}

struct Harness {
    connection: Connection,
    workspace: WorkspaceId,
    slots: Slots,
}

impl Harness {
    fn new(slots: u32) -> Self {
        let mut connection = Connection::open_in_memory().expect("connection");
        openbad_store::migrations::apply(&mut connection).expect("migrations");
        let workspace = WorkspaceId::new();
        connection
            .execute(
                "INSERT INTO workspaces (id, name, path, created_at, updated_at)
                 VALUES (?1, 'demo', ?2, 1, 1)",
                params![workspace.to_string(), format!("/tmp/{workspace}")],
            )
            .expect("workspace");
        Self {
            connection,
            workspace,
            slots: Slots::new(slots),
        }
    }

    fn admit(&self, trigger: &str, thread: &str) -> String {
        create_ticket(
            &self.connection,
            NewTicket {
                workspace_id: self.workspace,
                source_id: "main".to_owned(),
                thread: ThreadId(thread.to_owned()),
                trigger_id: trigger.to_owned(),
                mandate: Mandate::new(
                    "policy-1",
                    thread,
                    MandateStrength::Explicit,
                    vec!["allowed-person".to_owned()],
                    "alice",
                    "main",
                    "label agent:run",
                ),
            },
        )
        .expect("admit")
        .id
    }

    /// Launches a ticket if the slot accounting allows it, and returns the
    /// assignment it created.
    fn launch(&mut self, ticket_id: &str, executor: &str) -> Option<String> {
        if !self.slots.try_acquire(ticket_id, SlotPriority::Background) {
            return None;
        }
        let assignment = create_assignment(
            &self.connection,
            NewAssignment {
                workspace_id: self.workspace,
                ticket_id: ticket_id.to_owned(),
                executor: executor.to_owned(),
            },
        )
        .expect("assignment");
        move_assignment(&self.connection, &assignment.id, AssignmentState::Running)
            .expect("running");
        Some(assignment.id)
    }

    /// Applies one report's effects, exactly as the supervisor would.
    fn take_report(&mut self, ticket_id: &str, assignment_id: &str, outcome: ReportOutcome) {
        self.take_report_saying(ticket_id, assignment_id, outcome, "from the fake executor");
    }

    fn take_report_saying(
        &mut self,
        ticket_id: &str,
        assignment_id: &str,
        outcome: ReportOutcome,
        last_words: &str,
    ) {
        write_report(&self.connection, assignment_id, outcome, last_words, None).expect("report");
        let effects = effects_of(outcome);
        if effects.end_assignment {
            move_assignment(&self.connection, assignment_id, AssignmentState::Ended).expect("end");
        }
        if effects.release_slot {
            self.slots.release(ticket_id);
        }
        if let Some(next) = effects.ticket {
            move_ticket(&self.connection, ticket_id, next).expect("move ticket");
        }
    }

    fn ticket_state(&self, ticket_id: &str) -> TicketState {
        ticket(&self.connection, ticket_id)
            .expect("read")
            .expect("ticket")
            .state
    }
}

#[test]
fn a_ticket_runs_turns_until_a_final_report() {
    let mut harness = Harness::new(1);
    let ticket = harness.admit("t-1", "o/r#1");
    let assignment = harness.launch(&ticket, "worker-1").expect("launch");
    let mut executor = FakeExecutor::new(&[
        ReportOutcome::Progress,
        ReportOutcome::Progress,
        ReportOutcome::Waiting,
    ]);

    for _ in 0..3 {
        let outcome = executor.next_report();
        harness.take_report(&ticket, &assignment, outcome);
    }

    assert_eq!(
        latest_report(&harness.connection, &ticket)
            .expect("report")
            .map(|report| report.outcome),
        Some(ReportOutcome::Waiting)
    );
    assert_eq!(
        harness.ticket_state(&ticket),
        TicketState::Open,
        "waiting is idle, not over"
    );
    assert_eq!(harness.slots.in_use(), 0, "a final report frees the slot");
}

#[test]
fn progress_alone_keeps_the_slot_and_the_assignment_running() {
    let mut harness = Harness::new(1);
    let ticket = harness.admit("t-1", "o/r#1");
    let assignment = harness.launch(&ticket, "worker-1").expect("launch");

    harness.take_report(&ticket, &assignment, ReportOutcome::Progress);

    assert_eq!(harness.slots.in_use(), 1, "the work is still running");
    assert_eq!(
        active_assignment(&harness.connection, &ticket)
            .expect("active")
            .map(|assignment| assignment.state),
        Some(AssignmentState::Running)
    );
}

#[test]
fn a_failed_ticket_ends_and_frees_its_slot_for_the_next_one() {
    let mut harness = Harness::new(1);
    let first = harness.admit("t-1", "o/r#1");
    let first_assignment = harness.launch(&first, "worker-1").expect("launch");
    let second = harness.admit("t-2", "o/r#2");

    assert!(
        harness.launch(&second, "worker-1").is_none(),
        "one slot is one executor"
    );

    harness.take_report(&first, &first_assignment, ReportOutcome::Failed);

    assert_eq!(harness.ticket_state(&first), TicketState::Ended);
    assert_eq!(harness.slots.in_use(), 0);
    assert!(
        harness.launch(&second, "worker-1").is_some(),
        "the freed slot is reusable"
    );
}

#[test]
fn ready_hands_the_ticket_to_a_person_without_ending_it() {
    let mut harness = Harness::new(1);
    let ticket = harness.admit("t-1", "o/r#1");
    let assignment = harness.launch(&ticket, "worker-1").expect("launch");

    harness.take_report(&ticket, &assignment, ReportOutcome::Ready);

    assert_eq!(harness.ticket_state(&ticket), TicketState::Open);
    assert_eq!(harness.slots.in_use(), 0, "a handoff frees the slot");
    assert_eq!(
        live_ticket(&harness.connection, harness.workspace, "main", "o/r#1")
            .expect("live")
            .map(|ticket| ticket.id),
        Some(ticket),
        "the ticket is still the one a review comment reaches"
    );
}

#[test]
fn a_budget_stop_ends_the_ticket_with_the_reason() {
    let mut harness = Harness::new(1);
    let ticket = harness.admit("t-1", "o/r#1");
    let assignment = harness.launch(&ticket, "worker-1").expect("launch");

    let budget = Budget {
        steps: 10,
        tokens: 0,
    };
    let limit = budget
        .reached(Spent {
            steps: 10,
            tokens: 0,
        })
        .expect("exhausted");

    harness.take_report_saying(&ticket, &assignment, ReportOutcome::Failed, &limit.reason());

    let report = latest_report(&harness.connection, &ticket)
        .expect("report")
        .expect("one");
    assert_eq!(report.outcome, ReportOutcome::Failed);
    assert!(
        report.last_words.contains("allows 10"),
        "{}",
        report.last_words
    );
    assert_eq!(harness.ticket_state(&ticket), TicketState::Ended);
}

#[test]
fn a_person_wakes_a_ticket_ahead_of_new_work() {
    let mut harness = Harness::new(1);
    let busy = harness.admit("t-1", "o/r#1");
    let busy_assignment = harness.launch(&busy, "worker-1").expect("launch");
    let background = Candidate {
        ticket_id: harness.admit("t-2", "o/r#2"),
        priority: SlotPriority::Background,
    };
    let person = Candidate {
        ticket_id: busy.clone(),
        priority: SlotPriority::Person,
    };

    harness.take_report(&busy, &busy_assignment, ReportOutcome::Waiting);

    let candidates = [person.clone(), background];
    let order: Vec<&str> = launch_order(&candidates)
        .iter()
        .map(|candidate| candidate.ticket_id.as_str())
        .collect();

    assert_eq!(order.first(), Some(&person.ticket_id.as_str()));
    assert!(
        harness.launch(&person.ticket_id, "worker-1").is_some(),
        "the slot the handoff freed is the one the person takes"
    );
}

#[test]
fn a_restart_relaunches_what_never_started_and_replaces_what_lost_its_session() {
    assert_eq!(
        recovery_for(AssignmentState::Pending, false),
        Recovery::Relaunch
    );
    assert_eq!(
        recovery_for(AssignmentState::Running, false),
        Recovery::EndAndRelaunch
    );
    assert_eq!(
        recovery_for(AssignmentState::Running, true),
        Recovery::Leave
    );

    let mut harness = Harness::new(1);
    let ticket = harness.admit("t-1", "o/r#1");
    let assignment = harness.launch(&ticket, "worker-1").expect("launch");

    // The host restarts and finds a running assignment whose session is gone.
    assert_eq!(
        recovery_for(AssignmentState::Running, false),
        Recovery::EndAndRelaunch
    );
    move_assignment(&harness.connection, &assignment, AssignmentState::Ended)
        .expect("end the dead one");
    harness.slots.release(&ticket);
    let replacement = harness.launch(&ticket, "worker-2").expect("replacement");

    assert_ne!(replacement, assignment);
    assert_eq!(
        active_assignment(&harness.connection, &ticket)
            .expect("active")
            .map(|assignment| assignment.executor),
        Some("worker-2".to_owned())
    );
}
