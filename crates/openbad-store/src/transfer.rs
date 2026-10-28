//! Project-scoped state that travels in a transfer bundle.
//!
//! These rows mirror the SQLite schema so a bundle can be restored exactly:
//! chat sessions keep their events, projections, turns, and file checkpoints;
//! knowledge keeps document metadata and chunks; memories keep provenance.
//! Reference folders are deliberately absent because their paths are
//! machine-specific.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};

use crate::error::StoreError;
use openbad_core::WorkspaceId;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImportCounts {
    pub sessions: u64,
    pub events: u64,
    pub messages: u64,
    pub memories: u64,
    pub knowledge_documents: u64,
    pub checkpoints: u64,
}

/// How one bundled session relates to the target workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionImport {
    /// The row is new; copy it.
    Insert,
    /// The id already belongs to the target workspace; merge its rows in place.
    Update,
    /// An earlier merge of this bundle already produced a copy.
    AlreadyImported,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceState {
    pub memories: Vec<MemoryRow>,
    pub knowledge: Vec<KnowledgeRow>,
    pub sessions: Vec<SessionBundle>,
    pub checkpoints: Vec<CheckpointRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryRow {
    pub id: String,
    pub content: String,
    pub tags_json: String,
    pub pinned: bool,
    pub created_at: i64,
    pub updated_at: i64,
    pub last_accessed_at: Option<i64>,
    pub access_count: i64,
    pub forgotten_at: Option<i64>,
    pub origin_turn: Option<i64>,
    pub origin_task: Option<String>,
    pub author: String,
    pub scope: String,
    pub supersedes: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeRow {
    pub id: String,
    pub title: String,
    pub kind: String,
    pub source: Option<String>,
    pub tags_json: String,
    pub content: String,
    pub content_hash: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub confidence: String,
    pub source_hash: Option<String>,
    pub supersedes: Option<String>,
    pub review_status: String,
    pub chunks: Vec<KnowledgeChunkRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeChunkRow {
    pub seq: i64,
    pub content: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionBundle {
    pub session: SessionRow,
    pub events: Vec<EventRow>,
    pub messages: Vec<MessageRow>,
    pub turns: Vec<TurnRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionRow {
    pub id: String,
    pub parent_session_id: Option<String>,
    pub title: Option<String>,
    pub status: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub forked_from_seq: Option<i64>,
    pub plan_mode: bool,
    pub goal_json: Option<String>,
    pub metadata_json: String,
    pub last_turn: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EventRow {
    pub id: String,
    pub seq: i64,
    pub turn: Option<i64>,
    pub step: Option<i64>,
    pub kind: String,
    pub surface: i64,
    pub payload_json: String,
    pub created_at: i64,
    pub search_text: Option<String>,
    /// Base64-encoded BLOB.
    pub content_hash: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MessageRow {
    pub seq: i64,
    pub turn: Option<i64>,
    pub step: Option<i64>,
    pub role: String,
    pub content_json: String,
    pub source: Option<String>,
    pub created_at: i64,
    /// `0` for messages shadowed by a compaction checkpoint. Bundles written
    /// before compacted surfaces existed default to visible.
    #[serde(default = "visible_surface")]
    pub surface: i64,
}

fn visible_surface() -> i64 {
    1
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TurnRow {
    pub turn: i64,
    pub started_at: i64,
    pub ended_at: Option<i64>,
    pub reason: Option<String>,
    pub steps: i64,
    pub input_tokens: i64,
    pub cache_read_tokens: i64,
    pub output_tokens: i64,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckpointRow {
    pub id: String,
    pub session_id: String,
    pub turn: Option<i64>,
    pub path: String,
    pub existed: bool,
    pub content: Option<String>,
    pub content_hash: Option<String>,
    pub created_at: i64,
}

fn encode_blob(value: Option<Vec<u8>>) -> Option<String> {
    value.map(|bytes| BASE64.encode(bytes))
}

fn decode_blob(value: &Option<String>) -> Result<Option<Vec<u8>>, StoreError> {
    value
        .as_ref()
        .map(|encoded| {
            BASE64
                .decode(encoded)
                .map_err(|error| StoreError::InvalidData(error.to_string()))
        })
        .transpose()
}

pub(crate) fn export_workspace_state(
    connection: &Connection,
    workspace_id: WorkspaceId,
) -> Result<WorkspaceState, StoreError> {
    let workspace = workspace_id.to_string();
    let mut state = WorkspaceState {
        memories: export_memories(connection, &workspace)?,
        knowledge: export_knowledge(connection, &workspace)?,
        sessions: Vec::new(),
        checkpoints: Vec::new(),
    };

    let mut statement = connection.prepare(
        "SELECT id, parent_session_id, title, status, created_at, updated_at,
                forked_from_seq, plan_mode, goal_json, metadata_json, last_turn
         FROM sessions
         WHERE workspace_id = ?1 AND deleted_at IS NULL
         ORDER BY created_at ASC",
    )?;
    let sessions = statement
        .query_map([&workspace], |row| {
            Ok(SessionRow {
                id: row.get(0)?,
                parent_session_id: row.get(1)?,
                title: row.get(2)?,
                status: row.get(3)?,
                created_at: row.get(4)?,
                updated_at: row.get(5)?,
                forked_from_seq: row.get(6)?,
                plan_mode: row.get(7)?,
                goal_json: row.get(8)?,
                metadata_json: row.get(9)?,
                last_turn: row.get(10)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;

    for session in sessions {
        state.sessions.push(SessionBundle {
            events: export_events(connection, &session.id)?,
            messages: export_messages(connection, &session.id)?,
            turns: export_turns(connection, &session.id)?,
            session,
        });
    }
    state.checkpoints = export_checkpoints(connection, &workspace)?;
    Ok(state)
}

fn export_memories(connection: &Connection, workspace: &str) -> Result<Vec<MemoryRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT id, content, tags_json, pinned, created_at, updated_at,
                last_accessed_at, access_count, forgotten_at, origin_turn,
                origin_task, author, scope, supersedes, expires_at
         FROM memories
         WHERE workspace_id = ?1
         ORDER BY created_at ASC",
    )?;
    let rows = statement.query_map([workspace], |row| {
        Ok(MemoryRow {
            id: row.get(0)?,
            content: row.get(1)?,
            tags_json: row.get(2)?,
            pinned: row.get(3)?,
            created_at: row.get(4)?,
            updated_at: row.get(5)?,
            last_accessed_at: row.get(6)?,
            access_count: row.get(7)?,
            forgotten_at: row.get(8)?,
            origin_turn: row.get(9)?,
            origin_task: row.get(10)?,
            author: row.get(11)?,
            scope: row.get(12)?,
            supersedes: row.get(13)?,
            expires_at: row.get(14)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn export_knowledge(
    connection: &Connection,
    workspace: &str,
) -> Result<Vec<KnowledgeRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT id, title, kind, source, tags_json, content, content_hash,
                created_at, updated_at, confidence, source_hash, supersedes,
                review_status
         FROM knowledge_documents
         WHERE workspace_id = ?1
         ORDER BY created_at ASC",
    )?;
    let documents = statement
        .query_map([workspace], |row| {
            Ok(KnowledgeRow {
                id: row.get(0)?,
                title: row.get(1)?,
                kind: row.get(2)?,
                source: row.get(3)?,
                tags_json: row.get(4)?,
                content: row.get(5)?,
                content_hash: row.get(6)?,
                created_at: row.get(7)?,
                updated_at: row.get(8)?,
                confidence: row.get(9)?,
                source_hash: row.get(10)?,
                supersedes: row.get(11)?,
                review_status: row.get(12)?,
                chunks: Vec::new(),
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;

    let mut chunk_statement = connection.prepare(
        "SELECT seq, content FROM knowledge_chunks WHERE document_id = ?1 ORDER BY seq ASC",
    )?;
    let mut documents = documents;
    for document in &mut documents {
        document.chunks = chunk_statement
            .query_map([&document.id], |row| {
                Ok(KnowledgeChunkRow {
                    seq: row.get(0)?,
                    content: row.get(1)?,
                })
            })?
            .collect::<Result<Vec<_>, _>>()?;
    }
    Ok(documents)
}

fn export_events(connection: &Connection, session: &str) -> Result<Vec<EventRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT id, seq, turn, step, kind, surface, payload_json, created_at,
                search_text, content_hash
         FROM session_events WHERE session_id = ?1 ORDER BY seq ASC",
    )?;
    let rows = statement.query_map([session], |row| {
        let content_hash: Option<Vec<u8>> = row.get(9)?;
        Ok(EventRow {
            id: row.get(0)?,
            seq: row.get(1)?,
            turn: row.get(2)?,
            step: row.get(3)?,
            kind: row.get(4)?,
            surface: row.get(5)?,
            payload_json: row.get(6)?,
            created_at: row.get(7)?,
            search_text: row.get(8)?,
            content_hash: encode_blob(content_hash),
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn export_messages(connection: &Connection, session: &str) -> Result<Vec<MessageRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT seq, turn, step, role, content_json, source, created_at, surface
         FROM session_messages WHERE session_id = ?1 ORDER BY seq ASC",
    )?;
    let rows = statement.query_map([session], |row| {
        Ok(MessageRow {
            seq: row.get(0)?,
            turn: row.get(1)?,
            step: row.get(2)?,
            role: row.get(3)?,
            content_json: row.get(4)?,
            source: row.get(5)?,
            created_at: row.get(6)?,
            surface: row.get(7)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn export_turns(connection: &Connection, session: &str) -> Result<Vec<TurnRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT turn, started_at, ended_at, reason, steps, input_tokens,
                cache_read_tokens, output_tokens, cost_usd
         FROM session_turns WHERE session_id = ?1 ORDER BY turn ASC",
    )?;
    let rows = statement.query_map([session], |row| {
        Ok(TurnRow {
            turn: row.get(0)?,
            started_at: row.get(1)?,
            ended_at: row.get(2)?,
            reason: row.get(3)?,
            steps: row.get(4)?,
            input_tokens: row.get(5)?,
            cache_read_tokens: row.get(6)?,
            output_tokens: row.get(7)?,
            cost_usd: row.get(8)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn export_checkpoints(
    connection: &Connection,
    workspace: &str,
) -> Result<Vec<CheckpointRow>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT c.id, c.session_id, c.turn, c.path, c.existed, c.content,
                c.content_hash, c.created_at
         FROM file_checkpoints c
         JOIN sessions s ON s.id = c.session_id
         WHERE s.workspace_id = ?1 AND s.deleted_at IS NULL
         ORDER BY c.created_at ASC",
    )?;
    let rows = statement.query_map([workspace], |row| {
        let content: Option<Vec<u8>> = row.get(5)?;
        let content_hash: Option<Vec<u8>> = row.get(6)?;
        Ok(CheckpointRow {
            id: row.get(0)?,
            session_id: row.get(1)?,
            turn: row.get(2)?,
            path: row.get(3)?,
            existed: row.get(4)?,
            content: encode_blob(content),
            content_hash: encode_blob(content_hash),
            created_at: row.get(7)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn copied_session_metadata(metadata_json: &str, source_id: &str) -> String {
    let mut value = serde_json::from_str::<serde_json::Value>(metadata_json)
        .ok()
        .filter(serde_json::Value::is_object)
        .unwrap_or_else(|| serde_json::json!({}));
    if let Some(object) = value.as_object_mut() {
        object.insert(
            "imported_session_id".to_owned(),
            serde_json::Value::String(source_id.to_owned()),
        );
    }
    value.to_string()
}

fn imported_session_origins(
    connection: &Connection,
    workspace: &str,
) -> Result<std::collections::HashMap<String, String>, StoreError> {
    let mut statement =
        connection.prepare("SELECT id, metadata_json FROM sessions WHERE workspace_id = ?1")?;
    let rows = statement.query_map([workspace], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut origins = std::collections::HashMap::new();
    for row in rows {
        let (id, metadata_json) = row?;
        let origin = serde_json::from_str::<serde_json::Value>(&metadata_json)
            .ok()
            .and_then(|value| {
                value
                    .get("imported_session_id")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_owned)
            });
        if let Some(origin) = origin {
            origins.entry(origin).or_insert(id);
        }
    }
    Ok(origins)
}

pub(crate) fn import_workspace_state(
    connection: &mut Connection,
    workspace_id: WorkspaceId,
    state: &WorkspaceState,
) -> Result<ImportCounts, StoreError> {
    let transaction = connection.transaction()?;
    let workspace = workspace_id.to_string();
    let mut counts = ImportCounts::default();

    for memory in &state.memories {
        transaction.execute(
            "INSERT INTO memories
                (id, workspace_id, content, tags_json, pinned, created_at, updated_at,
                 last_accessed_at, access_count, forgotten_at, origin_turn, origin_task,
                 author, scope, supersedes, expires_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)
             ON CONFLICT(id) DO UPDATE SET
                 workspace_id = excluded.workspace_id,
                 content = excluded.content,
                 tags_json = excluded.tags_json,
                 pinned = excluded.pinned,
                 updated_at = excluded.updated_at,
                 last_accessed_at = excluded.last_accessed_at,
                 access_count = excluded.access_count,
                 forgotten_at = excluded.forgotten_at,
                 origin_turn = excluded.origin_turn,
                 origin_task = excluded.origin_task,
                 author = excluded.author,
                 scope = excluded.scope,
                 supersedes = excluded.supersedes,
                 expires_at = excluded.expires_at",
            params![
                memory.id,
                workspace,
                memory.content,
                memory.tags_json,
                memory.pinned,
                memory.created_at,
                memory.updated_at,
                memory.last_accessed_at,
                memory.access_count,
                memory.forgotten_at,
                memory.origin_turn,
                memory.origin_task,
                memory.author,
                memory.scope,
                memory.supersedes,
                memory.expires_at,
            ],
        )?;
        counts.memories += 1;
    }

    for document in &state.knowledge {
        transaction.execute(
            "INSERT INTO knowledge_documents
                (id, workspace_id, title, kind, source, tags_json, content, content_hash,
                 created_at, updated_at, confidence, source_hash, supersedes, review_status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)
             ON CONFLICT(id) DO UPDATE SET
                 workspace_id = excluded.workspace_id,
                 title = excluded.title,
                 kind = excluded.kind,
                 source = excluded.source,
                 tags_json = excluded.tags_json,
                 content = excluded.content,
                 content_hash = excluded.content_hash,
                 updated_at = excluded.updated_at,
                 confidence = excluded.confidence,
                 source_hash = excluded.source_hash,
                 supersedes = excluded.supersedes,
                 review_status = excluded.review_status",
            params![
                document.id,
                workspace,
                document.title,
                document.kind,
                document.source,
                document.tags_json,
                document.content,
                document.content_hash,
                document.created_at,
                document.updated_at,
                document.confidence,
                document.source_hash,
                document.supersedes,
                document.review_status,
            ],
        )?;
        transaction.execute(
            "DELETE FROM knowledge_chunks WHERE document_id = ?1",
            [&document.id],
        )?;
        transaction.execute(
            "DELETE FROM knowledge_fts WHERE document_id = ?1",
            [&document.id],
        )?;
        let chunks: Vec<(i64, &str)> = if document.chunks.is_empty() {
            vec![(0, document.content.as_str())]
        } else {
            document
                .chunks
                .iter()
                .map(|chunk| (chunk.seq, chunk.content.as_str()))
                .collect()
        };
        for (seq, content) in chunks {
            let chunk_id = uuid::Uuid::now_v7().to_string();
            transaction.execute(
                "INSERT INTO knowledge_chunks (id, document_id, workspace_id, seq, content)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![chunk_id, document.id, workspace, seq, content],
            )?;
            transaction.execute(
                "INSERT INTO knowledge_fts (workspace_id, document_id, title, seq, content)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![workspace, document.id, document.title, seq, content],
            )?;
        }
        counts.knowledge_documents += 1;
    }

    let bundle_ids: std::collections::HashSet<&str> = state
        .sessions
        .iter()
        .map(|bundle| bundle.session.id.as_str())
        .collect();
    // Sessions keep their ids when those ids are free or already imported into
    // this workspace. A session id owned by another workspace is copied under a
    // fresh id so merging appends chats instead of hijacking them. A copy records
    // the original id in its metadata so a repeated merge does not duplicate it.
    let mut imported_origins = imported_session_origins(&transaction, &workspace)?;
    let mut session_map: std::collections::HashMap<&str, (String, SessionImport)> =
        std::collections::HashMap::new();
    for bundle in &state.sessions {
        let existing: Option<Option<String>> = transaction
            .query_row(
                "SELECT workspace_id FROM sessions WHERE id = ?1",
                [&bundle.session.id],
                |row| row.get(0),
            )
            .optional()?;
        let (final_id, mode) = match existing {
            None => (bundle.session.id.clone(), SessionImport::Insert),
            Some(Some(owner)) if owner == workspace => {
                (bundle.session.id.clone(), SessionImport::Update)
            }
            Some(_) => match imported_origins.remove(bundle.session.id.as_str()) {
                Some(copied_id) => (copied_id, SessionImport::AlreadyImported),
                None => (uuid::Uuid::now_v7().to_string(), SessionImport::Insert),
            },
        };
        session_map.insert(bundle.session.id.as_str(), (final_id, mode));
    }

    for bundle in &state.sessions {
        let session = &bundle.session;
        let Some((final_id, mode)) = session_map.get(session.id.as_str()) else {
            continue;
        };
        if *mode == SessionImport::AlreadyImported {
            continue;
        }
        let copied = *mode == SessionImport::Insert;
        let inserted = if copied {
            transaction.execute(
                "INSERT INTO sessions
                    (id, workspace_id, parent_session_id, title, status, created_at, updated_at,
                     forked_from_seq, model_route_id, plan_mode, goal_json, metadata_json,
                     last_turn, deleted_at)
                 VALUES (?1, ?2, NULL, ?3, ?4, ?5, ?6, ?7, NULL, ?8, ?9, ?10, ?11, NULL)",
                params![
                    final_id,
                    workspace,
                    session.title,
                    session.status,
                    session.created_at,
                    session.updated_at,
                    session.forked_from_seq,
                    session.plan_mode,
                    session.goal_json,
                    copied_session_metadata(&session.metadata_json, &session.id),
                    session.last_turn,
                ],
            )? as u64
        } else {
            0
        };
        if copied {
            counts.sessions += inserted;
        }
        for event in &bundle.events {
            let content_hash = decode_blob(&event.content_hash)?;
            let event_id = if copied {
                uuid::Uuid::now_v7().to_string()
            } else {
                event.id.clone()
            };
            counts.events += transaction.execute(
                "INSERT OR IGNORE INTO session_events
                    (id, session_id, seq, turn, step, kind, surface, payload_json,
                     created_at, search_text, content_hash)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    event_id,
                    final_id,
                    event.seq,
                    event.turn,
                    event.step,
                    event.kind,
                    event.surface,
                    event.payload_json,
                    event.created_at,
                    event.search_text,
                    content_hash,
                ],
            )? as u64;
        }
        for message in &bundle.messages {
            counts.messages += transaction.execute(
                "INSERT OR IGNORE INTO session_messages
                    (session_id, seq, turn, step, role, content_json, source, created_at, surface)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    final_id,
                    message.seq,
                    message.turn,
                    message.step,
                    message.role,
                    message.content_json,
                    message.source,
                    message.created_at,
                    message.surface,
                ],
            )? as u64;
        }
        for turn in &bundle.turns {
            transaction.execute(
                "INSERT OR IGNORE INTO session_turns
                    (session_id, turn, started_at, ended_at, reason, steps, input_tokens,
                     cache_read_tokens, output_tokens, cost_usd)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    final_id,
                    turn.turn,
                    turn.started_at,
                    turn.ended_at,
                    turn.reason,
                    turn.steps,
                    turn.input_tokens,
                    turn.cache_read_tokens,
                    turn.output_tokens,
                    turn.cost_usd,
                ],
            )?;
        }
    }

    for bundle in &state.sessions {
        let Some(parent) = &bundle.session.parent_session_id else {
            continue;
        };
        if !bundle_ids.contains(parent.as_str()) {
            continue;
        }
        let Some((final_id, mode)) = session_map.get(bundle.session.id.as_str()) else {
            continue;
        };
        if *mode == SessionImport::AlreadyImported {
            continue;
        }
        let Some((parent_id, _)) = session_map.get(parent.as_str()) else {
            continue;
        };
        transaction.execute(
            "UPDATE sessions SET parent_session_id = ?1 WHERE id = ?2",
            params![parent_id, final_id],
        )?;
    }

    for checkpoint in &state.checkpoints {
        let Some((session_id, mode)) = session_map.get(checkpoint.session_id.as_str()) else {
            continue;
        };
        if *mode == SessionImport::AlreadyImported {
            continue;
        }
        let checkpoint_id = if *mode == SessionImport::Insert {
            uuid::Uuid::now_v7().to_string()
        } else {
            checkpoint.id.clone()
        };
        let content = decode_blob(&checkpoint.content)?;
        let content_hash = decode_blob(&checkpoint.content_hash)?;
        counts.checkpoints += transaction.execute(
            "INSERT OR IGNORE INTO file_checkpoints
                (id, session_id, turn, path, existed, content, content_hash, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                checkpoint_id,
                session_id,
                checkpoint.turn,
                checkpoint.path,
                checkpoint.existed,
                content,
                content_hash,
                checkpoint.created_at,
            ],
        )? as u64;
    }

    transaction.commit()?;
    Ok(counts)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::Store;
    use openbad_core::event::{NewEvent, SessionEventKind};
    use serde_json::json;
    use tempfile::tempdir;

    fn seeded_store() -> (Store, WorkspaceId) {
        let store = Store::open_in_memory().expect("store");
        let workspace = tempdir().expect("workspace");
        let workspace_id = store
            .create_workspace("demo", &workspace.path().to_string_lossy())
            .expect("workspace");
        (store, workspace_id)
    }

    #[test]
    fn round_trips_project_state() {
        let (store, workspace) = seeded_store();
        let session = store
            .create_session(Some(workspace), Some("Round trip"))
            .expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            )
            .expect("turn start");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({"content": "hello", "display": "hello"})),
            )
            .expect("event");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_turn(1)
                    .with_step(2)
                    .with_payload(json!({"content": "hi there"})),
            )
            .expect("event");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnEnd)
                    .with_turn(1)
                    .with_payload(json!({"reason": "completed"})),
            )
            .expect("turn end");
        store
            .remember_memory(
                workspace,
                "The API lives in crates/openbad-core",
                vec!["arch".to_owned()],
                true,
            )
            .expect("memory");
        let document = store
            .add_knowledge_document(
                workspace,
                "Design notes",
                "note",
                None,
                vec!["design".to_owned()],
                "The session event log is the source of truth.",
            )
            .expect("knowledge");
        store
            .save_file_checkpoint(
                session,
                Some(1),
                "src/main.rs",
                true,
                Some(b"fn main() {}".to_vec()),
            )
            .expect("checkpoint");

        let state = store
            .export_workspace_state(workspace)
            .expect("export state");
        assert_eq!(state.sessions.len(), 1);
        assert_eq!(state.sessions[0].events.len(), 4);
        assert!(!state.sessions[0].messages.is_empty());
        assert_eq!(state.sessions[0].turns.len(), 1);
        assert_eq!(state.memories.len(), 1);
        assert_eq!(state.knowledge.len(), 1);
        assert!(!state.knowledge[0].chunks.is_empty());
        assert_eq!(state.checkpoints.len(), 1);
        assert_eq!(state.knowledge[0].id, document.id);

        let (target, target_workspace) = seeded_store();
        let counts = target
            .import_workspace_state(target_workspace, state.clone())
            .expect("import");
        assert_eq!(counts.sessions, 1);
        assert_eq!(counts.memories, 1);
        assert_eq!(counts.knowledge_documents, 1);
        assert_eq!(counts.checkpoints, 1);
        assert_eq!(counts.events, 4);

        let reexport = target
            .export_workspace_state(target_workspace)
            .expect("reexport");
        assert_eq!(reexport.sessions.len(), 1);
        assert_eq!(reexport.sessions[0].events.len(), 4);
        assert_eq!(reexport.memories.len(), 1);
        assert_eq!(reexport.knowledge.len(), 1);
        assert_eq!(reexport.checkpoints.len(), 1);
        assert_eq!(
            target
                .load_messages(openbad_core::SessionId::from(
                    uuid::Uuid::parse_str(&reexport.sessions[0].session.id).expect("uuid")
                ))
                .expect("messages")
                .len(),
            reexport.sessions[0].messages.len()
        );

        // Importing the same bundle twice is idempotent.
        let counts = target
            .import_workspace_state(target_workspace, state)
            .expect("reimport");
        assert_eq!(counts.sessions, 0);
        assert_eq!(counts.events, 0);
        let again = target
            .export_workspace_state(target_workspace)
            .expect("reexport");
        assert_eq!(again.sessions.len(), 1);
        assert_eq!(again.sessions[0].events.len(), 4);
        assert_eq!(again.memories.len(), 1);
        assert_eq!(again.knowledge.len(), 1);
    }

    #[test]
    fn import_upserts_knowledge_and_memories_without_duplicating() {
        let (store, workspace) = seeded_store();
        store
            .remember_memory(workspace, "first", Vec::new(), false)
            .expect("memory");
        let document = store
            .add_knowledge_document(workspace, "Notes", "note", None, Vec::new(), "v1")
            .expect("doc");
        let state = store.export_workspace_state(workspace).expect("state");

        let (target, target_workspace) = seeded_store();
        target
            .import_workspace_state(target_workspace, state.clone())
            .expect("import");
        target
            .import_workspace_state(target_workspace, state)
            .expect("reimport");

        let reexport = target
            .export_workspace_state(target_workspace)
            .expect("reexport");
        assert_eq!(reexport.memories.len(), 1);
        assert_eq!(reexport.knowledge.len(), 1);
        assert_eq!(reexport.knowledge[0].id, document.id);
        let mut statement = target
            .export_workspace_state(target_workspace)
            .expect("state")
            .knowledge;
        let chunks = statement.remove(0).chunks.len();
        assert!(chunks >= 1);
    }
}
