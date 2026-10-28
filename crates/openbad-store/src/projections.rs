use std::str::FromStr;

use openbad_core::SessionId;
use openbad_core::event::{SessionEvent, SessionEventKind};
use rusqlite::{Connection, Row, Transaction, params};
use uuid::Uuid;

use crate::error::StoreError;
use crate::types::{SessionMessage, SessionTurn};

/// Update derived tables for one appended event.
pub fn apply(transaction: &Transaction<'_>, event: &SessionEvent) -> Result<(), StoreError> {
    match event.kind {
        SessionEventKind::TurnStart => {
            if let Some(turn) = event.turn {
                transaction.execute(
                    "INSERT INTO session_turns (session_id, turn, started_at)
                     VALUES (?1, ?2, ?3)
                     ON CONFLICT(session_id, turn)
                     DO UPDATE SET started_at = excluded.started_at",
                    params![event.session_id.to_string(), turn, event.created_at],
                )?;
            }
        }
        SessionEventKind::TurnEnd => {
            if let Some(turn) = event.turn {
                let reason = event
                    .payload
                    .get("reason")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("unknown");
                transaction.execute(
                    "INSERT INTO session_turns (session_id, turn, started_at, ended_at, reason)
                     VALUES (?1, ?2, ?3, ?3, ?4)
                     ON CONFLICT(session_id, turn)
                     DO UPDATE SET ended_at = excluded.ended_at, reason = excluded.reason",
                    params![event.session_id.to_string(), turn, event.created_at, reason],
                )?;
            }
        }
        SessionEventKind::StepEnd => {
            if let Some(turn) = event.turn {
                transaction.execute(
                    "UPDATE session_turns
                     SET steps = steps + 1
                     WHERE session_id = ?1 AND turn = ?2",
                    params![event.session_id.to_string(), turn],
                )?;
            }
        }
        SessionEventKind::UsageRecorded => {
            if let Some(turn) = event.turn {
                let input = event
                    .payload
                    .get("input_tokens")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0);
                let cache_read = event
                    .payload
                    .get("cache_read_tokens")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0);
                let output = event
                    .payload
                    .get("output_tokens")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0);
                let cost = event
                    .payload
                    .get("cost_usd")
                    .and_then(serde_json::Value::as_f64)
                    .unwrap_or(0.0);
                transaction.execute(
                    "UPDATE session_turns
                     SET input_tokens = input_tokens + ?3,
                         cache_read_tokens = cache_read_tokens + ?4,
                         output_tokens = output_tokens + ?5,
                         cost_usd = cost_usd + ?6
                     WHERE session_id = ?1 AND turn = ?2",
                    params![
                        event.session_id.to_string(),
                        turn,
                        input as i64,
                        cache_read as i64,
                        output as i64,
                        cost
                    ],
                )?;
            }
        }
        SessionEventKind::UserMessage
        | SessionEventKind::SystemMessage
        | SessionEventKind::DeveloperMessage
        | SessionEventKind::AssistantMessage
        | SessionEventKind::ToolResult
        | SessionEventKind::CompactionSummary => {
            if event.kind == SessionEventKind::CompactionSummary {
                // The checkpoint replaces a prefix of the surface. Keep the
                // original rows for the transcript and audit, but stop
                // replaying them to the model. Every older checkpoint is also
                // absorbed by the new one.
                match event
                    .payload
                    .get("shadowed_through_seq")
                    .and_then(serde_json::Value::as_i64)
                {
                    Some(cutoff) => {
                        transaction.execute(
                            "UPDATE session_messages SET surface = 0
                             WHERE session_id = ?1 AND surface = 1
                               AND (seq <= ?2 OR source = 'compaction')",
                            params![event.session_id.to_string(), cutoff],
                        )?;
                    }
                    None => {
                        transaction.execute(
                            "UPDATE session_messages SET surface = 0
                             WHERE session_id = ?1 AND surface = 1 AND seq < ?2",
                            params![event.session_id.to_string(), event.seq as i64],
                        )?;
                    }
                }
            }
            let role = match event.kind {
                SessionEventKind::UserMessage => "user",
                SessionEventKind::SystemMessage => "system",
                SessionEventKind::DeveloperMessage => "developer",
                SessionEventKind::AssistantMessage => "assistant",
                SessionEventKind::ToolResult => "tool",
                SessionEventKind::CompactionSummary => "user",
                _ => unreachable!("message match arm"),
            };
            let content_json = serde_json::to_string(&event.payload)?;
            let source = event
                .payload
                .get("source")
                .and_then(serde_json::Value::as_str);
            transaction.execute(
                "INSERT OR REPLACE INTO session_messages
                 (session_id, seq, turn, step, role, content_json, source, surface, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 1, ?8)",
                params![
                    event.session_id.to_string(),
                    event.seq as i64,
                    event.turn.map(i64::from),
                    event.step.map(i64::from),
                    role,
                    content_json,
                    source,
                    event.created_at,
                ],
            )?;
        }
        _ => {}
    }

    Ok(())
}

fn row_event(row: &Row<'_>) -> Result<SessionEvent, rusqlite::Error> {
    let id: String = row.get(0)?;
    let session: String = row.get(1)?;
    let seq: i64 = row.get(2)?;
    let turn: Option<i64> = row.get(3)?;
    let step: Option<i64> = row.get(4)?;
    let kind: String = row.get(5)?;
    let surface: i64 = row.get(6)?;
    let payload: String = row.get(7)?;
    let created_at: i64 = row.get(8)?;

    let kind = SessionEventKind::from_str(&kind).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(5, rusqlite::types::Type::Text, Box::new(error))
    })?;
    let session_id = SessionId::from_str(&session).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(1, rusqlite::types::Type::Text, Box::new(error))
    })?;
    let id = Uuid::parse_str(&id).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(error))
    })?;
    let payload = serde_json::from_str(&payload).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(7, rusqlite::types::Type::Text, Box::new(error))
    })?;

    Ok(SessionEvent {
        id,
        session_id,
        seq: seq as u64,
        turn: turn.map(|value| value as u32),
        step: step.map(|value| value as u32),
        kind,
        surface: surface != 0,
        payload,
        created_at,
    })
}

pub fn load_events(
    connection: &Connection,
    session_id: Option<SessionId>,
) -> Result<Vec<SessionEvent>, StoreError> {
    let sql = match session_id {
        Some(_) => {
            "SELECT id, session_id, seq, turn, step, kind, surface, payload_json, created_at
             FROM session_events WHERE session_id = ?1 ORDER BY seq ASC"
        }
        None => {
            "SELECT id, session_id, seq, turn, step, kind, surface, payload_json, created_at
             FROM session_events ORDER BY session_id, seq ASC"
        }
    };

    let mut statement = connection.prepare(sql)?;
    let mut events = Vec::new();

    if let Some(session_id) = session_id {
        let rows = statement.query_map([session_id.to_string()], row_event)?;
        for row in rows {
            events.push(row?);
        }
    } else {
        let rows = statement.query_map([], row_event)?;
        for row in rows {
            events.push(row?);
        }
    }

    Ok(events)
}

pub fn load_messages(
    connection: &Connection,
    session_id: SessionId,
) -> Result<Vec<SessionMessage>, StoreError> {
    query_messages(connection, session_id, false)
}

/// Load only the messages currently on the model-visible surface.
pub fn load_surface_messages(
    connection: &Connection,
    session_id: SessionId,
) -> Result<Vec<SessionMessage>, StoreError> {
    query_messages(connection, session_id, true)
}

fn query_messages(
    connection: &Connection,
    session_id: SessionId,
    surface_only: bool,
) -> Result<Vec<SessionMessage>, StoreError> {
    let sql = if surface_only {
        "SELECT seq, turn, step, role, content_json, source, created_at
         FROM session_messages WHERE session_id = ?1 AND surface = 1 ORDER BY seq ASC"
    } else {
        "SELECT seq, turn, step, role, content_json, source, created_at
         FROM session_messages WHERE session_id = ?1 ORDER BY seq ASC"
    };
    let mut statement = connection.prepare(sql)?;
    let rows = statement.query_map([session_id.to_string()], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, Option<i64>>(1)?,
            row.get::<_, Option<i64>>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, String>(4)?,
            row.get::<_, Option<String>>(5)?,
            row.get::<_, i64>(6)?,
        ))
    })?;

    let mut messages = Vec::new();
    for row in rows {
        let (seq, turn, step, role, content_json, source, created_at) = row?;
        messages.push(SessionMessage {
            session_id,
            seq: seq as u64,
            turn: turn.map(|value| value as u32),
            step: step.map(|value| value as u32),
            role,
            content: serde_json::from_str(&content_json)?,
            source,
            created_at,
        });
    }
    Ok(messages)
}

pub fn load_turns(
    connection: &Connection,
    session_id: SessionId,
) -> Result<Vec<SessionTurn>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT turn, started_at, ended_at, reason, steps,
                input_tokens, cache_read_tokens, output_tokens, cost_usd
         FROM session_turns WHERE session_id = ?1 ORDER BY turn ASC",
    )?;
    let rows = statement.query_map([session_id.to_string()], |row| {
        Ok(SessionTurn {
            session_id,
            turn: row.get::<_, i64>(0)? as u32,
            started_at: row.get(1)?,
            ended_at: row.get(2)?,
            reason: row.get(3)?,
            steps: row.get::<_, i64>(4)? as u32,
            input_tokens: row.get::<_, i64>(5)? as u64,
            cache_read_tokens: row.get::<_, i64>(6)? as u64,
            output_tokens: row.get::<_, i64>(7)? as u64,
            cost_usd: row.get(8)?,
        })
    })?;

    let mut turns = Vec::new();
    for row in rows {
        turns.push(row?);
    }
    Ok(turns)
}

/// Rebuild every projection from the event log.
pub fn rebuild(connection: &mut Connection) -> Result<(), StoreError> {
    let events = load_events(connection, None)?;
    let transaction = connection.transaction()?;
    transaction.execute("DELETE FROM session_messages", [])?;
    transaction.execute("DELETE FROM session_turns", [])?;
    for event in &events {
        apply(&transaction, event)?;
    }
    transaction.commit()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use openbad_core::event::SessionEventKind;
    use serde_json::json;

    #[test]
    fn rebuild_is_idempotent() {
        let mut connection = Connection::open_in_memory().expect("connection");
        crate::migrations::apply(&mut connection).expect("migrations");

        let workspace = Uuid::now_v7().to_string();
        connection
            .execute(
                "INSERT INTO workspaces (id, name, path, created_at, updated_at)
                 VALUES (?1, 'demo', ?2, 1, 1)",
                params![workspace, format!("/tmp/{workspace}")],
            )
            .expect("workspace");
        let session = SessionId::new();
        connection
            .execute(
                "INSERT INTO sessions (id, workspace_id, created_at, updated_at)
                 VALUES (?1, ?2, 1, 1)",
                params![session.to_string(), workspace],
            )
            .expect("session");

        let event = SessionEvent {
            id: Uuid::now_v7(),
            session_id: session,
            seq: 1,
            turn: Some(1),
            step: Some(1),
            kind: SessionEventKind::TurnStart,
            surface: false,
            payload: json!({}),
            created_at: 1,
        };
        connection
            .execute(
                "INSERT INTO session_events
                 (id, session_id, seq, turn, step, kind, surface, payload_json, created_at)
                 VALUES (?1, ?2, 1, 1, 1, 'turn/start', 0, '{}', 1)",
                params![event.id.to_string(), session.to_string()],
            )
            .expect("event");

        let transaction = connection.transaction().expect("transaction");
        apply(&transaction, &event).expect("apply");
        transaction.commit().expect("commit");

        rebuild(&mut connection).expect("rebuild");
        let turns = load_turns(&connection, session).expect("turns");
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].turn, 1);
    }
}
