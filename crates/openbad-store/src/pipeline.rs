//! Persistence for the supervised pipeline: tickets, mandates, assignments,
//! reports, questions, and subscriptions.
//!
//! The state machine lives in [`openbad_pipeline::ticket`]; this module stores its
//! states and enforces the invariants that have to survive a crash. Where a rule
//! can be expressed as a constraint it is — a unique index cannot be forgotten by
//! a caller, and it holds even if the process dies between two writes.
//!
//! A mandate is written with its ticket and never updated: the authority a result
//! was produced under has to stay readable afterwards, which is impossible if the
//! record can be amended.

use openbad_core::WorkspaceId;
use openbad_pipeline::cursor::SourceCursor;
use openbad_pipeline::mandate::{Mandate, MandateStrength};
use openbad_pipeline::ticket::{AssignmentState, ReportOutcome, TicketState};
use openbad_pipeline::trigger::ThreadId;
use rusqlite::{Connection, OptionalExtension, Row, params};
use uuid::Uuid;

use crate::error::StoreError;
use crate::now_millis;

/// A ticket and the mandate it was created under.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TicketRecord {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub source_id: String,
    pub thread: ThreadId,
    /// The trigger whose acceptance created this ticket, so a redelivery of the
    /// same event cannot create a second one.
    pub trigger_id: String,
    pub mandate_id: String,
    pub mandate: Mandate,
    pub state: TicketState,
    pub created_at: i64,
    pub updated_at: i64,
    pub ended_at: Option<i64>,
}

/// What a caller supplies to start a ticket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewTicket {
    pub workspace_id: WorkspaceId,
    pub source_id: String,
    pub thread: ThreadId,
    pub trigger_id: String,
    pub mandate: Mandate,
}

/// One executor's turn at a ticket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssignmentRecord {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub ticket_id: String,
    /// The session running the work, once one is bound.
    pub session_id: Option<String>,
    /// Which executor produced this, in the executor's own terms.
    pub executor: String,
    pub state: AssignmentState,
    pub created_at: i64,
    pub updated_at: i64,
    pub ended_at: Option<i64>,
}

/// The session the ticket's most recent assignment ran in.
///
/// A ticket keeps one executor session across its assignments, so the work has a
/// history rather than starting cold every time it is woken.
pub fn last_session_for_ticket(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<String>, StoreError> {
    Ok(connection
        .query_row(
            "SELECT session_id FROM pipeline_assignments
             WHERE ticket_id = ?1 AND session_id IS NOT NULL
             ORDER BY created_at DESC, id DESC LIMIT 1",
            [ticket_id],
            |row| row.get(0),
        )
        .optional()?)
}

/// What a caller supplies to start an assignment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewAssignment {
    pub workspace_id: WorkspaceId,
    pub ticket_id: String,
    pub executor: String,
}

/// The ticket's current state as its executor last left it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReportRecord {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub ticket_id: String,
    pub assignment_id: String,
    pub outcome: ReportOutcome,
    /// The executor's own words. Kept verbatim: it is what a person reads when the
    /// report is all they have.
    pub last_words: String,
    /// A pull request this report binds to the ticket.
    pub pull_request: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// A question waiting for a person.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttentionRecord {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub ticket_id: String,
    pub question: String,
    pub answer: Option<String>,
    pub asked_at: i64,
    pub answered_at: Option<i64>,
    pub answered_by: Option<String>,
}

impl AttentionRecord {
    /// Whether this question is still waiting for an answer.
    #[must_use]
    pub const fn is_open(&self) -> bool {
        self.answered_at.is_none()
    }
}

const TICKET_COLUMNS: &str = "t.id, t.workspace_id, t.source_id, t.thread_key, t.trigger_id,
     t.mandate_id, t.state, t.created_at, t.updated_at, t.ended_at,
     m.policy_version, m.scope, m.strength, m.matched_rules_json, m.requester,
     m.source_id AS mandate_source_id, m.received_via";

fn ticket_from_row(row: &Row<'_>) -> rusqlite::Result<TicketRecord> {
    let state: String = row.get(6)?;
    let strength: String = row.get(12)?;
    let matched_rules: String = row.get(13)?;
    Ok(TicketRecord {
        id: row.get(0)?,
        workspace_id: parse_workspace(row, 1)?,
        source_id: row.get(2)?,
        thread: ThreadId(row.get(3)?),
        trigger_id: row.get(4)?,
        mandate_id: row.get(5)?,
        state: TicketState::parse(&state)
            .ok_or_else(|| invalid("pipeline_tickets.state", &state))?,
        created_at: row.get(7)?,
        updated_at: row.get(8)?,
        ended_at: row.get(9)?,
        mandate: Mandate {
            policy_version: row.get(10)?,
            scope: row.get(11)?,
            strength: parse_strength(&strength)?,
            matched_rules: serde_json::from_str(&matched_rules)
                .map_err(|_| invalid("pipeline_mandates.matched_rules_json", &matched_rules))?,
            requester: row.get(14)?,
            source_id: row.get(15)?,
            received_via: row.get(16)?,
        },
    })
}

/// Creates the mandate and the ticket in one transaction.
///
/// Idempotent on the trigger: accepting the same trigger twice returns the ticket
/// the first accept created instead of a second one. A thread with a live ticket
/// is a conflict rather than a second ticket, because two open tickets on one
/// thread would make "who owns this message?" ambiguous.
pub fn create_ticket(connection: &Connection, new: NewTicket) -> Result<TicketRecord, StoreError> {
    let transaction = connection.unchecked_transaction()?;

    if let Some(existing) = ticket_by_trigger(&transaction, new.workspace_id, &new.trigger_id)? {
        return Ok(existing);
    }

    if let Some(live) = live_ticket(
        &transaction,
        new.workspace_id,
        &new.source_id,
        new.thread.as_str(),
    )? {
        return Err(StoreError::Conflict(format!(
            "{} already has open ticket {} in this workspace",
            new.thread.as_str(),
            live.id
        )));
    }

    let mandate_id = Uuid::now_v7().to_string();
    let now = now_millis();
    let matched_rules = serde_json::to_string(&new.mandate.matched_rules)?;
    transaction.execute(
        "INSERT INTO pipeline_mandates
            (id, workspace_id, policy_version, scope, strength, matched_rules_json,
             requester, source_id, received_via, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        params![
            mandate_id,
            new.workspace_id.to_string(),
            new.mandate.policy_version,
            new.mandate.scope,
            new.mandate.strength.as_str(),
            matched_rules,
            new.mandate.requester,
            new.mandate.source_id,
            new.mandate.received_via,
            now,
        ],
    )?;

    let id = Uuid::now_v7().to_string();
    transaction.execute(
        "INSERT INTO pipeline_tickets
            (id, workspace_id, source_id, thread_key, trigger_id, mandate_id, state,
             created_at, updated_at, ended_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?8, NULL)",
        params![
            id,
            new.workspace_id.to_string(),
            new.source_id,
            new.thread.as_str(),
            new.trigger_id,
            mandate_id,
            TicketState::Open.as_str(),
            now,
        ],
    )?;

    let created = ticket(&transaction, &id)?
        .ok_or_else(|| StoreError::NotFound(format!("ticket {id} after insert")))?;
    transaction.commit()?;
    Ok(created)
}

/// Looks a ticket up by id.
pub fn ticket(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<TicketRecord>, StoreError> {
    let sql = format!(
        "SELECT {TICKET_COLUMNS}
         FROM pipeline_tickets t
         JOIN pipeline_mandates m ON m.id = t.mandate_id
         WHERE t.id = ?1"
    );
    query_ticket(connection, &sql, params![ticket_id])
}

/// Looks a ticket up by the trigger that created it.
pub fn ticket_by_trigger(
    connection: &Connection,
    workspace_id: WorkspaceId,
    trigger_id: &str,
) -> Result<Option<TicketRecord>, StoreError> {
    let sql = format!(
        "SELECT {TICKET_COLUMNS}
         FROM pipeline_tickets t
         JOIN pipeline_mandates m ON m.id = t.mandate_id
         WHERE t.workspace_id = ?1 AND t.trigger_id = ?2"
    );
    query_ticket(
        connection,
        &sql,
        params![workspace_id.to_string(), trigger_id],
    )
}

/// The open ticket on a thread, if any.
pub fn live_ticket(
    connection: &Connection,
    workspace_id: WorkspaceId,
    source_id: &str,
    thread_key: &str,
) -> Result<Option<TicketRecord>, StoreError> {
    let sql = format!(
        "SELECT {TICKET_COLUMNS}
         FROM pipeline_tickets t
         JOIN pipeline_mandates m ON m.id = t.mandate_id
         WHERE t.workspace_id = ?1 AND t.source_id = ?2 AND t.thread_key = ?3
           AND t.state = 'open'"
    );
    query_ticket(
        connection,
        &sql,
        params![workspace_id.to_string(), source_id, thread_key],
    )
}

/// Every ticket on a thread, newest first — including ended ones, because a person
/// looking at a thread wants its history rather than only what is live.
pub fn tickets_on_thread(
    connection: &Connection,
    workspace_id: WorkspaceId,
    source_id: &str,
    thread_key: &str,
) -> Result<Vec<TicketRecord>, StoreError> {
    let sql = format!(
        "SELECT {TICKET_COLUMNS}
         FROM pipeline_tickets t
         JOIN pipeline_mandates m ON m.id = t.mandate_id
         WHERE t.workspace_id = ?1 AND t.source_id = ?2 AND t.thread_key = ?3
         ORDER BY t.created_at DESC, t.id DESC"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(
        params![workspace_id.to_string(), source_id, thread_key],
        ticket_from_row,
    )?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// The workspace's tickets, most recently touched first.
pub fn list_tickets(
    connection: &Connection,
    workspace_id: WorkspaceId,
    limit: u32,
) -> Result<Vec<TicketRecord>, StoreError> {
    let sql = format!(
        "SELECT {TICKET_COLUMNS}
         FROM pipeline_tickets t
         JOIN pipeline_mandates m ON m.id = t.mandate_id
         WHERE t.workspace_id = ?1
         ORDER BY t.updated_at DESC, t.id DESC
         LIMIT ?2"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(params![workspace_id.to_string(), limit], ticket_from_row)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// Moves a ticket to `next`, refusing a transition the state machine disallows.
///
/// Returns `false` when the ticket was already in `next`, so a caller retrying an
/// idempotent operation does not have to distinguish "done" from "was done".
pub fn move_ticket(
    connection: &Connection,
    ticket_id: &str,
    next: TicketState,
) -> Result<bool, StoreError> {
    let current = ticket(connection, ticket_id)?
        .ok_or_else(|| StoreError::NotFound(format!("ticket {ticket_id}")))?;

    if current.state == next {
        return Ok(false);
    }
    if !current.state.can_move_to(next) {
        return Err(StoreError::Conflict(format!(
            "a {} ticket cannot become {}",
            current.state.as_str(),
            next.as_str()
        )));
    }

    let now = now_millis();
    let ended_at = if next == TicketState::Open {
        None
    } else {
        Some(now)
    };
    connection.execute(
        "UPDATE pipeline_tickets
         SET state = ?1, updated_at = ?2, ended_at = ?3
         WHERE id = ?4",
        params![next.as_str(), now, ended_at, ticket_id],
    )?;
    Ok(true)
}

fn query_ticket<P: rusqlite::Params>(
    connection: &Connection,
    sql: &str,
    parameters: P,
) -> Result<Option<TicketRecord>, StoreError> {
    let mut statement = connection.prepare(sql)?;
    Ok(statement
        .query_row(parameters, ticket_from_row)
        .optional()?)
}

/// Creates an assignment for a ticket.
///
/// Refuses to create a second active assignment: one executor owns a ticket at a
/// time, and that has to hold even if a caller forgets to end the previous one.
pub fn create_assignment(
    connection: &Connection,
    new: NewAssignment,
) -> Result<AssignmentRecord, StoreError> {
    if let Some(active) = active_assignment(connection, &new.ticket_id)? {
        return Err(StoreError::Conflict(format!(
            "ticket {} already has {} assignment {}",
            new.ticket_id,
            active.state.as_str(),
            active.id
        )));
    }

    let id = Uuid::now_v7().to_string();
    let now = now_millis();
    connection.execute(
        "INSERT INTO pipeline_assignments
            (id, workspace_id, ticket_id, session_id, executor, state, created_at,
             updated_at, ended_at)
         VALUES (?1, ?2, ?3, NULL, ?4, ?5, ?6, ?6, NULL)",
        params![
            id,
            new.workspace_id.to_string(),
            new.ticket_id,
            new.executor,
            AssignmentState::Pending.as_str(),
            now,
        ],
    )?;
    assignment(connection, &id)?
        .ok_or_else(|| StoreError::NotFound(format!("assignment {id} after insert")))
}

/// Moves an assignment to `next`.
pub fn move_assignment(
    connection: &Connection,
    assignment_id: &str,
    next: AssignmentState,
) -> Result<AssignmentRecord, StoreError> {
    let now = now_millis();
    let ended_at = if next == AssignmentState::Ended {
        Some(now)
    } else {
        None
    };
    connection.execute(
        "UPDATE pipeline_assignments
         SET state = ?1, updated_at = ?2, ended_at = ?3
         WHERE id = ?4",
        params![next.as_str(), now, ended_at, assignment_id],
    )?;
    assignment(connection, assignment_id)?
        .ok_or_else(|| StoreError::NotFound(format!("assignment {assignment_id}")))
}

/// Binds the session that runs an assignment.
pub fn bind_assignment_session(
    connection: &Connection,
    assignment_id: &str,
    session_id: &str,
) -> Result<(), StoreError> {
    connection.execute(
        "UPDATE pipeline_assignments
         SET session_id = ?1, updated_at = ?2
         WHERE id = ?3",
        params![session_id, now_millis(), assignment_id],
    )?;
    Ok(())
}

/// Looks an assignment up by id.
pub fn assignment(
    connection: &Connection,
    assignment_id: &str,
) -> Result<Option<AssignmentRecord>, StoreError> {
    let sql = format!("{ASSIGNMENT_SELECT} AND id = ?1");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![assignment_id], assignment_from_row)
        .optional()?)
}

/// The assignment currently holding a ticket's executor slot.
pub fn active_assignment(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<AssignmentRecord>, StoreError> {
    let sql = format!("{ASSIGNMENT_SELECT} AND ticket_id = ?1 AND state IN ('pending', 'running')");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![ticket_id], assignment_from_row)
        .optional()?)
}

/// The pull request this ticket last recorded, if it ever opened one.
///
/// Asked separately from the newest report because a later attempt can fail
/// without one: the pull request is a fact about the ticket's branch, not about
/// whichever attempt happened to be last, and losing it hides reviewable work
/// behind an infrastructure error.
pub fn last_pull_request(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<String>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT pull_request FROM pipeline_reports
         WHERE ticket_id = ?1 AND pull_request IS NOT NULL
         ORDER BY created_at DESC, id DESC LIMIT 1",
    )?;
    Ok(statement
        .query_row(params![ticket_id], |row| row.get(0))
        .optional()?)
}

/// When this ticket's own work last ended, or `None` if it never ran.
///
/// The evidence that separates the pipeline's own output from new input: an
/// executor that signs in as the person it works for cannot be filtered by name,
/// so anything written before its last turn ended was already part of that turn.
/// Only `ended_at` counts — a pending or running assignment has not spoken yet,
/// and a message arriving mid-turn is the live ticket's to deliver.
pub fn spoke_at(connection: &Connection, ticket_id: &str) -> Result<Option<i64>, StoreError> {
    let mut statement = connection
        .prepare("SELECT MAX(ended_at) FROM pipeline_assignments WHERE ticket_id = ?1")?;
    Ok(statement.query_row(params![ticket_id], |row| row.get(0))?)
}

/// A ticket's assignments, newest first.
pub fn list_assignments(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Vec<AssignmentRecord>, StoreError> {
    let sql = format!("{ASSIGNMENT_SELECT} AND ticket_id = ?1 ORDER BY created_at DESC, id DESC");
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(params![ticket_id], assignment_from_row)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

const ASSIGNMENT_SELECT: &str = "SELECT id, workspace_id, ticket_id, session_id, executor,
     state, created_at, updated_at, ended_at
     FROM pipeline_assignments WHERE 1 = 1";

fn assignment_from_row(row: &Row<'_>) -> rusqlite::Result<AssignmentRecord> {
    let state: String = row.get(5)?;
    Ok(AssignmentRecord {
        id: row.get(0)?,
        workspace_id: parse_workspace(row, 1)?,
        ticket_id: row.get(2)?,
        session_id: row.get(3)?,
        executor: row.get(4)?,
        state: AssignmentState::parse(&state)
            .ok_or_else(|| invalid("pipeline_assignments.state", &state))?,
        created_at: row.get(6)?,
        updated_at: row.get(7)?,
        ended_at: row.get(8)?,
    })
}

/// Records an assignment's report, replacing its previous one.
///
/// One report per assignment: an executor that reports `progress` twice, then
/// `ready`, has one report whose outcome moved forward, not four reports for a
/// reader to reconcile.
pub fn write_report(
    connection: &Connection,
    assignment_id: &str,
    outcome: ReportOutcome,
    last_words: &str,
    pull_request: Option<&str>,
) -> Result<ReportRecord, StoreError> {
    let assignment = assignment(connection, assignment_id)?
        .ok_or_else(|| StoreError::NotFound(format!("assignment {assignment_id}")))?;
    let now = now_millis();

    let existing: Option<String> = connection
        .query_row(
            "SELECT id FROM pipeline_reports WHERE assignment_id = ?1",
            [assignment_id],
            |row| row.get(0),
        )
        .optional()?;

    match existing {
        Some(id) => {
            connection.execute(
                "UPDATE pipeline_reports
                 SET outcome = ?1, last_words = ?2, pull_request = ?3, updated_at = ?4
                 WHERE id = ?5",
                params![outcome.as_str(), last_words, pull_request, now, id],
            )?;
            report(connection, &id)?.ok_or_else(|| StoreError::NotFound(format!("report {id}")))
        }
        None => {
            let id = Uuid::now_v7().to_string();
            connection.execute(
                "INSERT INTO pipeline_reports
                    (id, workspace_id, ticket_id, assignment_id, outcome, last_words,
                     pull_request, created_at, updated_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?8)",
                params![
                    id,
                    assignment.workspace_id.to_string(),
                    assignment.ticket_id,
                    assignment_id,
                    outcome.as_str(),
                    last_words,
                    pull_request,
                    now,
                ],
            )?;
            report(connection, &id)?
                .ok_or_else(|| StoreError::NotFound(format!("report {id} after insert")))
        }
    }
}

/// Looks a report up by id.
pub fn report(
    connection: &Connection,
    report_id: &str,
) -> Result<Option<ReportRecord>, StoreError> {
    let sql = format!("{REPORT_SELECT} AND id = ?1");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![report_id], report_from_row)
        .optional()?)
}

/// The report a ticket shows: the one written most recently by any of its
/// assignments.
pub fn latest_report(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<ReportRecord>, StoreError> {
    let sql =
        format!("{REPORT_SELECT} AND ticket_id = ?1 ORDER BY updated_at DESC, id DESC LIMIT 1");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![ticket_id], report_from_row)
        .optional()?)
}

const REPORT_SELECT: &str = "SELECT id, workspace_id, ticket_id, assignment_id, outcome,
     last_words, pull_request, created_at, updated_at
     FROM pipeline_reports WHERE 1 = 1";

fn report_from_row(row: &Row<'_>) -> rusqlite::Result<ReportRecord> {
    let outcome: String = row.get(4)?;
    Ok(ReportRecord {
        id: row.get(0)?,
        workspace_id: parse_workspace(row, 1)?,
        ticket_id: row.get(2)?,
        assignment_id: row.get(3)?,
        outcome: ReportOutcome::parse(&outcome)
            .ok_or_else(|| invalid("pipeline_reports.outcome", &outcome))?,
        last_words: row.get(5)?,
        pull_request: row.get(6)?,
        created_at: row.get(7)?,
        updated_at: row.get(8)?,
    })
}

/// Records a question for a person on a ticket.
///
/// Refuses a second open question: one question at a time is what makes it
/// answerable, and the executor is waiting on the first.
pub fn ask(
    connection: &Connection,
    workspace_id: WorkspaceId,
    ticket_id: &str,
    question: &str,
) -> Result<AttentionRecord, StoreError> {
    if let Some(open) = open_attention(connection, ticket_id)? {
        return Err(StoreError::Conflict(format!(
            "ticket {ticket_id} already asks {:?}",
            open.question
        )));
    }

    let id = Uuid::now_v7().to_string();
    connection.execute(
        "INSERT INTO pipeline_attentions
            (id, workspace_id, ticket_id, question, answer, asked_at, answered_at, answered_by)
         VALUES (?1, ?2, ?3, ?4, NULL, ?5, NULL, NULL)",
        params![
            id,
            workspace_id.to_string(),
            ticket_id,
            question,
            now_millis()
        ],
    )?;
    attention(connection, &id)?
        .ok_or_else(|| StoreError::NotFound(format!("attention {id} after insert")))
}

/// Answers an open question. Answers once: a second answer is a conflict, because
/// the executor already received the first.
pub fn answer_attention(
    connection: &Connection,
    attention_id: &str,
    answer: &str,
    answered_by: &str,
) -> Result<AttentionRecord, StoreError> {
    let now = now_millis();
    let changed = connection.execute(
        "UPDATE pipeline_attentions
         SET answer = ?1, answered_at = ?2, answered_by = ?3
         WHERE id = ?4 AND answered_at IS NULL",
        params![answer, now, answered_by, attention_id],
    )?;
    if changed == 0 {
        return Err(StoreError::Conflict(format!(
            "attention {attention_id} is already answered or does not exist"
        )));
    }
    attention(connection, attention_id)?
        .ok_or_else(|| StoreError::NotFound(format!("attention {attention_id}")))
}

/// Looks an attention up by id.
pub fn attention(
    connection: &Connection,
    attention_id: &str,
) -> Result<Option<AttentionRecord>, StoreError> {
    let sql = format!("{ATTENTION_SELECT} AND id = ?1");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![attention_id], attention_from_row)
        .optional()?)
}

/// The question a ticket is waiting on, if any.
pub fn open_attention(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Option<AttentionRecord>, StoreError> {
    let sql = format!("{ATTENTION_SELECT} AND ticket_id = ?1 AND answered_at IS NULL");
    let mut statement = connection.prepare(&sql)?;
    Ok(statement
        .query_row(params![ticket_id], attention_from_row)
        .optional()?)
}

const ATTENTION_SELECT: &str = "SELECT id, workspace_id, ticket_id, question, answer,
     asked_at, answered_at, answered_by
     FROM pipeline_attentions WHERE 1 = 1";

fn attention_from_row(row: &Row<'_>) -> rusqlite::Result<AttentionRecord> {
    Ok(AttentionRecord {
        id: row.get(0)?,
        workspace_id: parse_workspace(row, 1)?,
        ticket_id: row.get(2)?,
        question: row.get(3)?,
        answer: row.get(4)?,
        asked_at: row.get(5)?,
        answered_at: row.get(6)?,
        answered_by: row.get(7)?,
    })
}

/// Subscribes a ticket to a thread. Repeating it is a no-op.
pub fn subscribe(
    connection: &Connection,
    workspace_id: WorkspaceId,
    ticket_id: &str,
    source_id: &str,
    thread_key: &str,
) -> Result<(), StoreError> {
    connection.execute(
        "INSERT INTO pipeline_subscriptions
            (workspace_id, ticket_id, source_id, thread_key, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(ticket_id, source_id, thread_key) DO NOTHING",
        params![
            workspace_id.to_string(),
            ticket_id,
            source_id,
            thread_key,
            now_millis()
        ],
    )?;
    Ok(())
}

/// The tickets listening on a thread — the routing question fan-out asks first.
pub fn subscribers(
    connection: &Connection,
    workspace_id: WorkspaceId,
    source_id: &str,
    thread_key: &str,
) -> Result<Vec<String>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT ticket_id FROM pipeline_subscriptions
         WHERE workspace_id = ?1 AND source_id = ?2 AND thread_key = ?3
         ORDER BY created_at",
    )?;
    let rows = statement.query_map(
        params![workspace_id.to_string(), source_id, thread_key],
        |row| row.get::<_, String>(0),
    )?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// The threads a ticket listens on.
pub fn subscriptions_of(
    connection: &Connection,
    ticket_id: &str,
) -> Result<Vec<(String, String)>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT source_id, thread_key FROM pipeline_subscriptions
         WHERE ticket_id = ?1 ORDER BY created_at",
    )?;
    let rows = statement.query_map([ticket_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// A source's read position, or an empty one when it has never polled.
///
/// Empty rather than absent, because "never polled" and "polled and found
/// nothing" both mean "start from the beginning of what the provider will give
/// us", and a caller should not have to spell that out at every call site.
pub fn cursor(
    connection: &Connection,
    workspace_id: WorkspaceId,
    source_id: &str,
) -> Result<SourceCursor, StoreError> {
    let stored: Option<String> = connection
        .query_row(
            "SELECT cursor_json FROM pipeline_cursors
             WHERE workspace_id = ?1 AND source_id = ?2",
            params![workspace_id.to_string(), source_id],
            |row| row.get(0),
        )
        .optional()?;
    match stored {
        Some(json) => Ok(serde_json::from_str(&json)?),
        None => Ok(SourceCursor::default()),
    }
}

/// Records a source's read position.
///
/// Written after the triggers it accounts for are admitted, never before: a crash
/// between the two then costs one re-read, which the cursor's id set absorbs.
/// Advancing first would lose the events the cursor claims to have seen.
pub fn save_cursor(
    connection: &Connection,
    workspace_id: WorkspaceId,
    source_id: &str,
    cursor: &SourceCursor,
) -> Result<(), StoreError> {
    connection.execute(
        "INSERT INTO pipeline_cursors (workspace_id, source_id, cursor_json, updated_at)
         VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(workspace_id, source_id) DO UPDATE
            SET cursor_json = excluded.cursor_json, updated_at = excluded.updated_at",
        params![
            workspace_id.to_string(),
            source_id,
            serde_json::to_string(cursor)?,
            now_millis()
        ],
    )?;
    Ok(())
}

/// Reads a workspace id from a text column.
fn parse_workspace(row: &Row<'_>, index: usize) -> rusqlite::Result<WorkspaceId> {
    let text: String = row.get(index)?;
    text.parse::<WorkspaceId>()
        .map_err(|_| invalid("workspace_id", &text))
}

fn parse_strength(text: &str) -> rusqlite::Result<MandateStrength> {
    match text {
        "explicit" => Ok(MandateStrength::Explicit),
        "inferred" => Ok(MandateStrength::Inferred),
        other => Err(invalid("pipeline_mandates.strength", other)),
    }
}

fn invalid(column: &str, value: &str) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        0,
        rusqlite::types::Type::Text,
        Box::new(StoreError::InvalidData(format!("{column} holds {value:?}"))),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    use super::cursor as read_cursor;
    use super::ticket as read_ticket;

    struct Fixture {
        connection: Connection,
        workspace: WorkspaceId,
    }

    impl Fixture {
        fn new() -> Self {
            let mut connection = Connection::open_in_memory().expect("connection");
            crate::migrations::apply(&mut connection).expect("migrations");
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
            }
        }

        fn new_ticket(&self, trigger: &str, thread: &str) -> NewTicket {
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
            }
        }
    }

    #[test]
    fn accepting_the_same_trigger_twice_creates_one_ticket() {
        let fixture = Fixture::new();
        let first = create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1"))
            .expect("first accept");
        let second = create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1"))
            .expect("second accept");

        assert_eq!(first.id, second.id);
        assert_eq!(
            list_tickets(&fixture.connection, fixture.workspace, 10)
                .expect("list")
                .len(),
            1
        );
        assert_eq!(first.state, TicketState::Open);
    }

    #[test]
    fn a_second_accept_on_a_live_thread_is_a_conflict() {
        let fixture = Fixture::new();
        let first = create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1"))
            .expect("first accept");

        let second = create_ticket(&fixture.connection, fixture.new_ticket("t-2", "o/r#1"));

        assert!(matches!(second, Err(StoreError::Conflict(_))), "{second:?}");
        assert_eq!(
            live_ticket(&fixture.connection, fixture.workspace, "main", "o/r#1")
                .expect("live")
                .map(|ticket| ticket.id),
            Some(first.id)
        );
    }

    #[test]
    fn a_re_request_after_the_ticket_ended_gets_a_fresh_mandate() {
        let fixture = Fixture::new();
        let first = create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1"))
            .expect("first accept");
        move_ticket(&fixture.connection, &first.id, TicketState::Ended).expect("end");

        let second = create_ticket(&fixture.connection, fixture.new_ticket("t-2", "o/r#1"))
            .expect("second accept");

        assert_ne!(first.id, second.id);
        assert_ne!(first.mandate_id, second.mandate_id);
        assert_eq!(
            tickets_on_thread(&fixture.connection, fixture.workspace, "main", "o/r#1")
                .expect("thread")
                .len(),
            2
        );
    }

    #[test]
    fn an_ended_ticket_resumes_but_a_stopped_one_does_not() {
        let fixture = Fixture::new();
        let ended =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        move_ticket(&fixture.connection, &ended.id, TicketState::Ended).expect("end");
        assert!(move_ticket(&fixture.connection, &ended.id, TicketState::Open).expect("resume"));
        assert_eq!(
            read_ticket(&fixture.connection, &ended.id)
                .expect("read")
                .map(|ticket| ticket.state),
            Some(TicketState::Open)
        );

        let stopped =
            create_ticket(&fixture.connection, fixture.new_ticket("t-2", "o/r#2")).expect("accept");
        move_ticket(&fixture.connection, &stopped.id, TicketState::Stopped).expect("stop");
        let revived = move_ticket(&fixture.connection, &stopped.id, TicketState::Open);

        assert!(
            matches!(revived, Err(StoreError::Conflict(_))),
            "{revived:?}"
        );
        assert_eq!(
            read_ticket(&fixture.connection, &stopped.id)
                .expect("read")
                .map(|ticket| ticket.state),
            Some(TicketState::Stopped)
        );
    }

    #[test]
    fn moving_a_ticket_to_where_it_already_is_reports_no_change() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");

        assert!(!move_ticket(&fixture.connection, &created.id, TicketState::Open).expect("noop"));
    }

    #[test]
    fn a_ticket_keeps_the_mandate_it_started_with() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        move_ticket(&fixture.connection, &created.id, TicketState::Ended).expect("end");
        move_ticket(&fixture.connection, &created.id, TicketState::Open).expect("resume");

        let resumed = read_ticket(&fixture.connection, &created.id)
            .expect("read")
            .expect("ticket");

        assert_eq!(resumed.mandate_id, created.mandate_id);
        assert_eq!(resumed.mandate, created.mandate);
        assert_eq!(resumed.mandate.strength, MandateStrength::Explicit);
        assert_eq!(resumed.mandate.matched_rules, ["allowed-person".to_owned()]);
    }

    #[test]
    fn a_ticket_runs_one_executor_at_a_time() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        let first = create_assignment(
            &fixture.connection,
            NewAssignment {
                workspace_id: fixture.workspace,
                ticket_id: created.id.clone(),
                executor: "worker-1".to_owned(),
            },
        )
        .expect("first assignment");

        let second = create_assignment(
            &fixture.connection,
            NewAssignment {
                workspace_id: fixture.workspace,
                ticket_id: created.id.clone(),
                executor: "worker-2".to_owned(),
            },
        );
        assert!(matches!(second, Err(StoreError::Conflict(_))), "{second:?}");

        move_assignment(&fixture.connection, &first.id, AssignmentState::Ended).expect("end");
        let third = create_assignment(
            &fixture.connection,
            NewAssignment {
                workspace_id: fixture.workspace,
                ticket_id: created.id.clone(),
                executor: "worker-2".to_owned(),
            },
        )
        .expect("replacement assignment");

        assert_eq!(
            list_assignments(&fixture.connection, &created.id)
                .expect("assignments")
                .len(),
            2
        );
        assert_eq!(
            active_assignment(&fixture.connection, &created.id)
                .expect("active")
                .map(|assignment| assignment.id),
            Some(third.id)
        );
    }

    #[test]
    fn an_assignment_ending_does_not_end_its_ticket() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        let assignment = create_assignment(
            &fixture.connection,
            NewAssignment {
                workspace_id: fixture.workspace,
                ticket_id: created.id.clone(),
                executor: "worker-1".to_owned(),
            },
        )
        .expect("assignment");
        move_assignment(&fixture.connection, &assignment.id, AssignmentState::Ended).expect("end");

        assert_eq!(
            read_ticket(&fixture.connection, &created.id)
                .expect("read")
                .map(|ticket| ticket.state),
            Some(TicketState::Open)
        );
    }

    #[test]
    fn reporting_twice_keeps_one_report_that_moved_forward() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        let assignment = create_assignment(
            &fixture.connection,
            NewAssignment {
                workspace_id: fixture.workspace,
                ticket_id: created.id.clone(),
                executor: "worker-1".to_owned(),
            },
        )
        .expect("assignment");

        write_report(
            &fixture.connection,
            &assignment.id,
            ReportOutcome::Progress,
            "half",
            None,
        )
        .expect("progress");
        let final_report = write_report(
            &fixture.connection,
            &assignment.id,
            ReportOutcome::Ready,
            "done",
            Some("https://github.com/o/r/pull/7"),
        )
        .expect("ready");

        let latest = latest_report(&fixture.connection, &created.id)
            .expect("latest")
            .expect("report");
        assert_eq!(latest.id, final_report.id);
        assert_eq!(latest.outcome, ReportOutcome::Ready);
        assert_eq!(latest.last_words, "done");
        assert_eq!(
            latest.pull_request,
            Some("https://github.com/o/r/pull/7".to_owned())
        );
        assert!(latest.outcome.is_final());
    }

    #[test]
    fn a_ticket_asks_one_question_at_a_time() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        let asked = ask(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "Ship behind a flag?",
        )
        .expect("ask");

        let second = ask(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "And the migration?",
        );
        assert!(matches!(second, Err(StoreError::Conflict(_))), "{second:?}");

        let answered =
            answer_attention(&fixture.connection, &asked.id, "Yes", "alice").expect("answer");
        assert!(!answered.is_open());
        assert_eq!(answered.answer.as_deref(), Some("Yes"));
        assert_eq!(answered.answered_by.as_deref(), Some("alice"));

        let answered_twice = answer_attention(&fixture.connection, &asked.id, "No", "bob");
        assert!(
            matches!(answered_twice, Err(StoreError::Conflict(_))),
            "{answered_twice:?}"
        );

        ask(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "And the migration?",
        )
        .expect("the next question after the first is answered");
    }

    #[test]
    fn a_subscription_is_held_by_the_thread_not_the_ticket_state() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        subscribe(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "main",
            "o/r#1",
        )
        .expect("subscribe");
        subscribe(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "main",
            "o/r#1",
        )
        .expect("repeat is a no-op");
        subscribe(
            &fixture.connection,
            fixture.workspace,
            &created.id,
            "main",
            "o/r#9",
        )
        .expect("second thread");

        move_ticket(&fixture.connection, &created.id, TicketState::Ended).expect("end");

        assert_eq!(
            subscribers(&fixture.connection, fixture.workspace, "main", "o/r#1").expect("routing"),
            std::slice::from_ref(&created.id)
        );
        assert_eq!(
            subscriptions_of(&fixture.connection, &created.id).expect("threads"),
            [
                ("main".to_owned(), "o/r#1".to_owned()),
                ("main".to_owned(), "o/r#9".to_owned())
            ]
        );
    }

    #[test]
    fn a_stored_state_the_code_cannot_read_is_an_error_not_a_default() {
        let fixture = Fixture::new();
        let created =
            create_ticket(&fixture.connection, fixture.new_ticket("t-1", "o/r#1")).expect("accept");
        fixture
            .connection
            .execute(
                "UPDATE pipeline_tickets SET state = 'paused' WHERE id = ?1",
                [&created.id],
            )
            .expect("corrupt");

        let read = read_ticket(&fixture.connection, &created.id);

        assert!(read.is_err(), "a corrupt state must not read as open");
    }

    #[test]
    fn a_source_that_never_polled_has_an_empty_read_position() {
        let fixture = Fixture::new();

        let loaded = read_cursor(&fixture.connection, fixture.workspace, "main").expect("cursor");

        assert_eq!(loaded, SourceCursor::default());
        assert_eq!(loaded.since_ms(), 0);
    }

    #[test]
    fn a_read_position_survives_a_round_trip() {
        let fixture = Fixture::new();
        let mut position = SourceCursor::default();
        assert!(position.accept("t-1", 1_000));
        assert!(position.accept("t-2", 2_000));

        save_cursor(&fixture.connection, fixture.workspace, "main", &position).expect("save");
        let loaded = read_cursor(&fixture.connection, fixture.workspace, "main").expect("load");

        assert_eq!(loaded, position);
        assert!(
            loaded.seen("t-1"),
            "the overlap set is what suppresses a re-read"
        );
    }

    #[test]
    fn a_read_position_is_per_source() {
        let fixture = Fixture::new();
        let mut main = SourceCursor::default();
        main.accept("t-1", 5_000);
        save_cursor(&fixture.connection, fixture.workspace, "main", &main).expect("save");

        let other = read_cursor(&fixture.connection, fixture.workspace, "second").expect("cursor");

        assert_eq!(other, SourceCursor::default(), "sources read independently");
    }

    #[test]
    fn a_cursor_the_code_cannot_read_is_an_error_not_an_empty_position() {
        let fixture = Fixture::new();
        fixture
            .connection
            .execute(
                "INSERT INTO pipeline_cursors (workspace_id, source_id, cursor_json, updated_at)
                 VALUES (?1, 'main', 'not json', 1)",
                [fixture.workspace.to_string()],
            )
            .expect("corrupt");

        let loaded = read_cursor(&fixture.connection, fixture.workspace, "main");

        assert!(
            loaded.is_err(),
            "an unreadable position must not silently replay everything"
        );
    }
}
