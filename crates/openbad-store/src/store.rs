use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::sync::{Mutex, MutexGuard};
use std::thread::{self, JoinHandle};

use openbad_core::event::{NewEvent, SessionEvent};
use openbad_core::{ModelRouteId, ProviderId, SessionId, WorkspaceId};
use rusqlite::{Connection, OpenFlags, OptionalExtension, params};
use serde_json::Value;
use uuid::Uuid;

use crate::audit;
use crate::error::StoreError;
use crate::migrations;
use crate::now_millis;
use crate::projections;
use crate::types::{
    AuditRecord, AuditReport, DoctorReport, FileCheckpoint, FileCheckpointSummary, IndexHit,
    IndexSummary, IndexedFile, ModelRouteRecord, NewModelRoute, NewProvider, NewSession,
    NewWorkspace, ProviderRecord, SessionMessage, SessionSummary, SessionTurn, SymbolHit,
};

enum WriteCommand {
    AppendEvent {
        session_id: SessionId,
        draft: NewEvent,
        reply: SyncSender<Result<SessionEvent, StoreError>>,
    },
    CreateWorkspace {
        workspace: NewWorkspace,
        reply: SyncSender<Result<WorkspaceId, StoreError>>,
    },
    EnsureWorkspace {
        workspace: NewWorkspace,
        reply: SyncSender<Result<WorkspaceId, StoreError>>,
    },
    CreateSession {
        session: NewSession,
        reply: SyncSender<Result<SessionId, StoreError>>,
    },
    SetSetting {
        key: String,
        value: Value,
        reply: SyncSender<Result<(), StoreError>>,
    },
    CreateProvider {
        provider: NewProvider,
        reply: SyncSender<Result<ProviderRecord, StoreError>>,
    },
    CreateModelRoute {
        route: NewModelRoute,
        reply: SyncSender<Result<ModelRouteRecord, StoreError>>,
    },
    AuditAppend {
        actor: String,
        action: String,
        target: Option<String>,
        decision: Option<String>,
        policy: Option<String>,
        metadata: Value,
        reply: SyncSender<Result<AuditRecord, StoreError>>,
    },
    Backup {
        path: PathBuf,
        reply: SyncSender<Result<(), StoreError>>,
    },
    RebuildProjections {
        reply: SyncSender<Result<(), StoreError>>,
    },
    UpdateWorkspaceIndex {
        workspace_id: WorkspaceId,
        files: Vec<IndexedFile>,
        removed: Vec<String>,
        reply: SyncSender<Result<(), StoreError>>,
    },
    ClearWorkspaceIndex {
        workspace_id: WorkspaceId,
        reply: SyncSender<Result<(), StoreError>>,
    },
    SaveFileCheckpoint {
        session_id: SessionId,
        turn: Option<u32>,
        path: String,
        existed: bool,
        content: Option<Vec<u8>>,
        reply: SyncSender<Result<FileCheckpointSummary, StoreError>>,
    },
    DeleteFileCheckpoint {
        checkpoint_id: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    Shutdown,
}

/// A single-writer SQLite store with a dedicated writer thread and a
/// read-only connection for queries.
pub struct Store {
    writer: Sender<WriteCommand>,
    reader: Mutex<Connection>,
    join: Mutex<Option<JoinHandle<()>>>,
    path: Option<PathBuf>,
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, StoreError> {
        let path = path.as_ref().to_path_buf();
        let mut writer = Connection::open(&path)?;
        Self::configure(&mut writer)?;
        migrations::apply(&mut writer)?;

        let mut reader = Connection::open(&path)?;
        Self::configure(&mut reader)?;

        Ok(Self::start(writer, reader, Some(path)))
    }

    pub fn open_in_memory() -> Result<Self, StoreError> {
        let uri = format!("file:openbad-{}?mode=memory&cache=shared", Uuid::now_v7());
        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_URI;

        let mut writer = Connection::open_with_flags(&uri, flags)?;
        Self::configure(&mut writer)?;
        migrations::apply(&mut writer)?;

        let mut reader = Connection::open_with_flags(&uri, flags)?;
        Self::configure(&mut reader)?;

        Ok(Self::start(writer, reader, None))
    }

    /// Open the application database under the platform data directory.
    pub fn open_default() -> Result<Self, StoreError> {
        let directories = directories::ProjectDirs::from("com", "OpenBaD", "OpenBaD Desktop")
            .ok_or(StoreError::NoDataDirectory)?;
        std::fs::create_dir_all(directories.data_dir())?;
        Self::open(directories.data_dir().join("openbad.db"))
    }

    fn start(writer: Connection, reader: Connection, path: Option<PathBuf>) -> Self {
        let (sender, receiver) = mpsc::channel();
        let join = thread::Builder::new()
            .name("openbad-store-writer".to_owned())
            .spawn(move || writer_loop(writer, receiver))
            .expect("spawn store writer");

        Self {
            writer: sender,
            reader: Mutex::new(reader),
            join: Mutex::new(Some(join)),
            path,
        }
    }

    /// Validate a backup and copy it over an offline target database.
    /// The target must not be open by another process.
    pub fn restore_from(
        backup: impl AsRef<Path>,
        target: impl AsRef<Path>,
    ) -> Result<(), StoreError> {
        let backup = backup.as_ref();
        let target = target.as_ref();

        let connection = Connection::open_with_flags(
            backup,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
        )?;
        let integrity: String =
            connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
        if integrity != "ok" {
            return Err(StoreError::Integrity(integrity));
        }
        drop(connection);

        std::fs::copy(backup, target)?;
        for suffix in ["-wal", "-shm"] {
            let sidecar = PathBuf::from(format!("{}{suffix}", target.display()));
            match std::fs::remove_file(sidecar) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(StoreError::Io(error)),
            }
        }
        Ok(())
    }

    fn configure(connection: &mut Connection) -> Result<(), StoreError> {
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;
             PRAGMA synchronous = NORMAL;",
        )?;
        let _ = connection.query_row("PRAGMA journal_mode = WAL", [], |_| Ok(()));
        Ok(())
    }

    #[must_use]
    pub fn database_path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    pub fn schema_version(&self) -> Result<String, StoreError> {
        let connection = self.reader()?;
        migrations::current_version(&connection)
    }

    pub fn create_workspace(&self, name: &str, path: &str) -> Result<WorkspaceId, StoreError> {
        self.create_workspace_record(NewWorkspace::new(name, path))
    }

    pub fn create_workspace_record(
        &self,
        workspace: NewWorkspace,
    ) -> Result<WorkspaceId, StoreError> {
        self.write(|reply| WriteCommand::CreateWorkspace { workspace, reply })
    }

    /// Return the existing workspace for a path, or create it.
    pub fn ensure_workspace(&self, name: &str, path: &str) -> Result<WorkspaceId, StoreError> {
        self.write(|reply| WriteCommand::EnsureWorkspace {
            workspace: NewWorkspace::new(name, path),
            reply,
        })
    }

    pub fn create_session(
        &self,
        workspace_id: Option<WorkspaceId>,
        title: Option<&str>,
    ) -> Result<SessionId, StoreError> {
        self.create_session_record(NewSession::new(workspace_id, title.map(str::to_owned)))
    }

    pub fn create_session_record(&self, session: NewSession) -> Result<SessionId, StoreError> {
        self.write(|reply| WriteCommand::CreateSession { session, reply })
    }

    pub fn append_event(
        &self,
        session_id: SessionId,
        draft: NewEvent,
    ) -> Result<SessionEvent, StoreError> {
        self.write(|reply| WriteCommand::AppendEvent {
            session_id,
            draft,
            reply,
        })
    }

    pub fn session_workspace_id(
        &self,
        session_id: SessionId,
    ) -> Result<Option<WorkspaceId>, StoreError> {
        let connection = self.reader()?;
        let workspace: Option<String> = connection
            .query_row(
                "SELECT workspace_id FROM sessions WHERE id = ?1",
                [session_id.to_string()],
                |row| row.get(0),
            )
            .optional()?;
        workspace
            .map(|value| {
                Uuid::parse_str(&value)
                    .map(WorkspaceId::from)
                    .map_err(|error| StoreError::InvalidData(error.to_string()))
            })
            .transpose()
    }

    pub fn list_sessions(&self, limit: u32) -> Result<Vec<SessionSummary>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT s.id, s.workspace_id, w.path, s.title, s.status, s.created_at,
                    s.updated_at,
                    COALESCE((SELECT MAX(t.turn) FROM session_turns t
                              WHERE t.session_id = s.id), s.last_turn, 0)
             FROM sessions s
             LEFT JOIN workspaces w ON w.id = s.workspace_id
             WHERE s.deleted_at IS NULL
             ORDER BY s.updated_at DESC
             LIMIT ?1",
        )?;
        let rows = statement.query_map([i64::from(limit)], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, i64>(6)?,
                row.get::<_, i64>(7)?,
            ))
        })?;

        let mut sessions = Vec::new();
        for row in rows {
            let (
                id,
                workspace_id,
                workspace_path,
                title,
                status,
                created_at,
                updated_at,
                last_turn,
            ) = row?;
            sessions.push(SessionSummary {
                id: SessionId::from(
                    Uuid::parse_str(&id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                workspace_id: workspace_id
                    .map(|value| {
                        Uuid::parse_str(&value)
                            .map(WorkspaceId::from)
                            .map_err(|error| StoreError::InvalidData(error.to_string()))
                    })
                    .transpose()?,
                workspace_path,
                title,
                status,
                created_at,
                updated_at,
                last_turn: last_turn as u32,
            });
        }
        Ok(sessions)
    }

    pub fn save_file_checkpoint(
        &self,
        session_id: SessionId,
        turn: Option<u32>,
        path: &str,
        existed: bool,
        content: Option<Vec<u8>>,
    ) -> Result<FileCheckpointSummary, StoreError> {
        self.write(|reply| WriteCommand::SaveFileCheckpoint {
            session_id,
            turn,
            path: path.to_owned(),
            existed,
            content,
            reply,
        })
    }

    pub fn list_file_checkpoints(
        &self,
        session_id: SessionId,
        limit: u32,
    ) -> Result<Vec<FileCheckpointSummary>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, path, existed, COALESCE(length(content), 0), created_at
             FROM file_checkpoints
             WHERE session_id = ?1
             ORDER BY created_at DESC
             LIMIT ?2",
        )?;
        let rows = statement.query_map(
            [session_id.to_string(), i64::from(limit).to_string()],
            |row| {
                Ok(FileCheckpointSummary {
                    id: row.get(0)?,
                    path: row.get(1)?,
                    existed: row.get::<_, bool>(2)?,
                    bytes: row.get::<_, i64>(3)? as u64,
                    created_at: row.get(4)?,
                })
            },
        )?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn load_file_checkpoint(
        &self,
        checkpoint_id: &str,
    ) -> Result<Option<FileCheckpoint>, StoreError> {
        let connection = self.reader()?;
        let checkpoint = connection
            .query_row(
                "SELECT id, session_id, path, existed, content, created_at
                 FROM file_checkpoints WHERE id = ?1",
                [checkpoint_id],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, bool>(3)?,
                        row.get::<_, Option<Vec<u8>>>(4)?,
                        row.get::<_, i64>(5)?,
                    ))
                },
            )
            .optional()?;
        let Some((id, session_id, path, existed, content, created_at)) = checkpoint else {
            return Ok(None);
        };
        Ok(Some(FileCheckpoint {
            id,
            session_id: SessionId::from(
                Uuid::parse_str(&session_id)
                    .map_err(|error| StoreError::InvalidData(error.to_string()))?,
            ),
            path,
            existed,
            content,
            created_at,
        }))
    }

    pub fn delete_file_checkpoint(&self, checkpoint_id: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteFileCheckpoint {
            checkpoint_id: checkpoint_id.to_owned(),
            reply,
        })
    }

    pub fn session_workspace_path(
        &self,
        session_id: SessionId,
    ) -> Result<Option<String>, StoreError> {
        let connection = self.reader()?;
        connection
            .query_row(
                "SELECT w.path FROM sessions s
                 JOIN workspaces w ON w.id = s.workspace_id
                 WHERE s.id = ?1",
                [session_id.to_string()],
                |row| row.get::<_, String>(0),
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn search_sessions(
        &self,
        query: &str,
        limit: u32,
    ) -> Result<Vec<SessionSummary>, StoreError> {
        let pattern = format!(
            "%{}%",
            query
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        );
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT s.id, s.workspace_id, w.path, s.title, s.status, s.created_at,
                    s.updated_at,
                    COALESCE((SELECT MAX(t.turn) FROM session_turns t
                              WHERE t.session_id = s.id), s.last_turn, 0)
             FROM sessions s
             LEFT JOIN workspaces w ON w.id = s.workspace_id
             WHERE s.deleted_at IS NULL
               AND (s.title LIKE ?1 ESCAPE '\\'
                    OR EXISTS (SELECT 1 FROM session_messages m
                               WHERE m.session_id = s.id
                                 AND m.content_json LIKE ?1 ESCAPE '\\'))
             ORDER BY s.updated_at DESC
             LIMIT ?2",
        )?;
        let rows = statement.query_map(params![pattern, i64::from(limit)], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, i64>(6)?,
                row.get::<_, i64>(7)?,
            ))
        })?;

        let mut sessions = Vec::new();
        for row in rows {
            let (
                id,
                workspace_id,
                workspace_path,
                title,
                status,
                created_at,
                updated_at,
                last_turn,
            ) = row?;
            sessions.push(SessionSummary {
                id: SessionId::from(
                    Uuid::parse_str(&id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                workspace_id: workspace_id
                    .map(|value| {
                        Uuid::parse_str(&value)
                            .map(WorkspaceId::from)
                            .map_err(|error| StoreError::InvalidData(error.to_string()))
                    })
                    .transpose()?,
                workspace_path,
                title,
                status,
                created_at,
                updated_at,
                last_turn: last_turn as u32,
            });
        }
        Ok(sessions)
    }

    pub fn load_events(&self, session_id: SessionId) -> Result<Vec<SessionEvent>, StoreError> {
        let connection = self.reader()?;
        projections::load_events(&connection, Some(session_id))
    }

    pub fn load_messages(&self, session_id: SessionId) -> Result<Vec<SessionMessage>, StoreError> {
        let connection = self.reader()?;
        projections::load_messages(&connection, session_id)
    }

    pub fn load_turns(&self, session_id: SessionId) -> Result<Vec<SessionTurn>, StoreError> {
        let connection = self.reader()?;
        projections::load_turns(&connection, session_id)
    }

    pub fn set_setting(&self, key: impl Into<String>, value: Value) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::SetSetting {
            key: key.into(),
            value,
            reply,
        })
    }

    pub fn get_setting(&self, key: &str) -> Result<Option<Value>, StoreError> {
        let connection = self.reader()?;
        let value = connection
            .query_row(
                "SELECT value_json FROM settings WHERE key = ?1",
                [key],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        value
            .map(|json| serde_json::from_str(&json).map_err(StoreError::from))
            .transpose()
    }

    pub fn create_provider(&self, provider: NewProvider) -> Result<ProviderRecord, StoreError> {
        self.write(|reply| WriteCommand::CreateProvider { provider, reply })
    }

    pub fn list_providers(&self) -> Result<Vec<ProviderRecord>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, kind, name, base_url, keychain_ref, enabled
             FROM providers ORDER BY name ASC",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, i64>(5)?,
            ))
        })?;

        let mut providers = Vec::new();
        for row in rows {
            let (id, kind, name, base_url, keychain_ref, enabled) = row?;
            providers.push(ProviderRecord {
                id: ProviderId::from(
                    Uuid::parse_str(&id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                kind,
                name,
                base_url,
                keychain_ref,
                enabled: enabled != 0,
            });
        }
        Ok(providers)
    }

    pub fn create_model_route(&self, route: NewModelRoute) -> Result<ModelRouteRecord, StoreError> {
        self.write(|reply| WriteCommand::CreateModelRoute { route, reply })
    }

    pub fn list_model_routes(&self) -> Result<Vec<ModelRouteRecord>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, name, task_class, provider_id, model, context_window,
                    max_output_tokens, pricing_json, peak_pricing_json, cache_mode,
                    api_format, thinking_default, capabilities_json, enabled
             FROM model_routes ORDER BY name ASC",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, i64>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, String>(9)?,
                row.get::<_, String>(10)?,
                row.get::<_, String>(11)?,
                row.get::<_, String>(12)?,
                row.get::<_, i64>(13)?,
            ))
        })?;

        let mut routes = Vec::new();
        for row in rows {
            let (
                id,
                name,
                task_class,
                provider_id,
                model,
                context_window,
                max_output_tokens,
                pricing,
                peak_pricing,
                cache_mode,
                api_format,
                thinking_default,
                capabilities,
                enabled,
            ) = row?;
            routes.push(ModelRouteRecord {
                id: ModelRouteId::from(
                    Uuid::parse_str(&id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                name,
                task_class,
                provider_id: ProviderId::from(
                    Uuid::parse_str(&provider_id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                model,
                context_window: context_window as u32,
                max_output_tokens: max_output_tokens as u32,
                pricing: serde_json::from_str(&pricing)?,
                peak_pricing: serde_json::from_str(&peak_pricing)?,
                cache_mode,
                api_format,
                thinking_default,
                capabilities: serde_json::from_str(&capabilities)?,
                enabled: enabled != 0,
            });
        }
        Ok(routes)
    }

    pub fn audit_append(
        &self,
        actor: impl Into<String>,
        action: impl Into<String>,
        target: Option<&str>,
        decision: Option<&str>,
        policy: Option<&str>,
        metadata: Value,
    ) -> Result<AuditRecord, StoreError> {
        self.write(|reply| WriteCommand::AuditAppend {
            actor: actor.into(),
            action: action.into(),
            target: target.map(str::to_owned),
            decision: decision.map(str::to_owned),
            policy: policy.map(str::to_owned),
            metadata,
            reply,
        })
    }

    pub fn audit_verify(&self) -> Result<AuditReport, StoreError> {
        let connection = self.reader()?;
        audit::verify(&connection)
    }

    pub fn backup_to(&self, path: impl Into<PathBuf>) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::Backup {
            path: path.into(),
            reply,
        })
    }

    pub fn integrity_check(&self) -> Result<String, StoreError> {
        let connection = self.reader()?;
        let result = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
        Ok(result)
    }

    pub fn rebuild_projections(&self) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::RebuildProjections { reply })
    }

    pub fn workspace_index_hashes(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<Vec<(String, String)>, StoreError> {
        let connection = self.reader()?;
        let mut statement =
            connection.prepare("SELECT path, hash FROM workspace_files WHERE workspace_id = ?1")?;
        let rows = statement.query_map([workspace_id.to_string()], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn update_workspace_index(
        &self,
        workspace_id: WorkspaceId,
        files: Vec<IndexedFile>,
        removed: Vec<String>,
    ) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::UpdateWorkspaceIndex {
            workspace_id,
            files,
            removed,
            reply,
        })
    }

    pub fn clear_workspace_index(&self, workspace_id: WorkspaceId) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::ClearWorkspaceIndex {
            workspace_id,
            reply,
        })
    }

    pub fn search_workspace(
        &self,
        workspace_id: WorkspaceId,
        query: &str,
        limit: u32,
    ) -> Result<Vec<IndexHit>, StoreError> {
        let terms = fts_terms(query);
        if terms.is_empty() {
            return Ok(Vec::new());
        }
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT path,
                    snippet(workspace_fts, 2, '<<', '>>', '…', 16) AS snippet,
                    rank
             FROM workspace_fts
             WHERE workspace_fts MATCH ?1 AND workspace_id = ?2
             ORDER BY rank
             LIMIT ?3",
        )?;
        let rows = statement.query_map(
            params![terms, workspace_id.to_string(), i64::from(limit)],
            |row| {
                Ok(IndexHit {
                    path: row.get(0)?,
                    snippet: row.get(1)?,
                    score: row.get::<_, f64>(2)?,
                })
            },
        )?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn find_indexed_symbols(
        &self,
        workspace_id: WorkspaceId,
        query: &str,
        kind: Option<&str>,
        limit: u32,
    ) -> Result<Vec<SymbolHit>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT path, name, kind, line, signature
             FROM workspace_symbols
             WHERE workspace_id = ?1
               AND lower(name) LIKE '%' || lower(?2) || '%'
               AND (?3 IS NULL OR kind = ?3)
             ORDER BY CASE WHEN lower(name) = lower(?2) THEN 0 ELSE 1 END,
                      length(name), name
             LIMIT ?4",
        )?;
        let rows = statement.query_map(
            params![workspace_id.to_string(), query, kind, i64::from(limit)],
            |row| {
                Ok(SymbolHit {
                    path: row.get(0)?,
                    name: row.get(1)?,
                    kind: row.get(2)?,
                    line: row.get::<_, i64>(3)? as u32,
                    signature: row.get(4)?,
                })
            },
        )?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn workspace_index_summary(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<IndexSummary, StoreError> {
        let connection = self.reader()?;
        let (files, bytes, indexed_at) = connection.query_row(
            "SELECT COUNT(*), COALESCE(SUM(size), 0), MAX(indexed_at)
             FROM workspace_files WHERE workspace_id = ?1",
            [workspace_id.to_string()],
            |row| {
                Ok((
                    row.get::<_, i64>(0)? as u64,
                    row.get::<_, i64>(1)? as u64,
                    row.get::<_, Option<i64>>(2)?,
                ))
            },
        )?;
        let symbols = connection.query_row(
            "SELECT COUNT(*) FROM workspace_symbols WHERE workspace_id = ?1",
            [workspace_id.to_string()],
            |row| row.get::<_, i64>(0),
        )? as u64;
        Ok(IndexSummary {
            files,
            symbols,
            bytes,
            indexed_at,
        })
    }

    pub fn doctor(&self) -> Result<DoctorReport, StoreError> {
        let connection = self.reader()?;
        let schema_version = migrations::current_version(&connection)?;
        let integrity = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
        let page_count: i64 = connection.query_row("PRAGMA page_count", [], |row| row.get(0))?;
        let page_size: i64 = connection.query_row("PRAGMA page_size", [], |row| row.get(0))?;
        let event_count: i64 =
            connection.query_row("SELECT COUNT(*) FROM session_events", [], |row| row.get(0))?;

        Ok(DoctorReport {
            schema_version,
            integrity,
            database_bytes: page_count * page_size,
            event_count,
        })
    }

    fn write<T>(
        &self,
        make_command: impl FnOnce(SyncSender<Result<T, StoreError>>) -> WriteCommand,
    ) -> Result<T, StoreError> {
        let (reply, response) = mpsc::sync_channel(1);
        self.writer
            .send(make_command(reply))
            .map_err(|_| StoreError::ActorUnavailable)?;
        response.recv().map_err(|_| StoreError::ActorUnavailable)?
    }

    fn reader(&self) -> Result<MutexGuard<'_, Connection>, StoreError> {
        self.reader.lock().map_err(|_| StoreError::LockPoisoned)
    }
}

impl Drop for Store {
    fn drop(&mut self) {
        let _ = self.writer.send(WriteCommand::Shutdown);
        if let Ok(mut guard) = self.join.lock() {
            if let Some(handle) = guard.take() {
                let _ = handle.join();
            }
        }
    }
}

fn update_workspace_index(
    connection: &mut Connection,
    workspace_id: WorkspaceId,
    files: &[IndexedFile],
    removed: &[String],
) -> Result<(), StoreError> {
    let workspace = workspace_id.to_string();
    let now = now_millis();
    let transaction = connection.transaction()?;

    {
        let mut delete_symbols = transaction.prepare_cached(
            "DELETE FROM workspace_symbols WHERE workspace_id = ?1 AND path = ?2",
        )?;
        let mut delete_fts = transaction
            .prepare_cached("DELETE FROM workspace_fts WHERE workspace_id = ?1 AND path = ?2")?;
        let mut delete_file = transaction
            .prepare_cached("DELETE FROM workspace_files WHERE workspace_id = ?1 AND path = ?2")?;
        for path in removed {
            delete_symbols.execute(params![workspace, path])?;
            delete_fts.execute(params![workspace, path])?;
            delete_file.execute(params![workspace, path])?;
        }
    }

    {
        let mut upsert_file = transaction.prepare_cached(
            "INSERT INTO workspace_files
                (workspace_id, path, hash, size, modified_at, language, indexed_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
             ON CONFLICT(workspace_id, path) DO UPDATE SET
                hash = excluded.hash,
                size = excluded.size,
                modified_at = excluded.modified_at,
                language = excluded.language,
                indexed_at = excluded.indexed_at",
        )?;
        let mut delete_symbols = transaction.prepare_cached(
            "DELETE FROM workspace_symbols WHERE workspace_id = ?1 AND path = ?2",
        )?;
        let mut delete_fts = transaction
            .prepare_cached("DELETE FROM workspace_fts WHERE workspace_id = ?1 AND path = ?2")?;
        let mut insert_symbol = transaction.prepare_cached(
            "INSERT OR REPLACE INTO workspace_symbols
                (workspace_id, path, name, kind, line, signature)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        )?;
        let mut insert_fts = transaction.prepare_cached(
            "INSERT INTO workspace_fts (workspace_id, path, content) VALUES (?1, ?2, ?3)",
        )?;

        for file in files {
            upsert_file.execute(params![
                workspace,
                file.path,
                file.hash,
                file.size as i64,
                file.modified_at,
                file.language,
                now,
            ])?;
            delete_symbols.execute(params![workspace, file.path])?;
            delete_fts.execute(params![workspace, file.path])?;
            for symbol in &file.symbols {
                insert_symbol.execute(params![
                    workspace,
                    file.path,
                    symbol.name,
                    symbol.kind,
                    i64::from(symbol.line),
                    symbol.signature,
                ])?;
            }
            insert_fts.execute(params![workspace, file.path, file.content])?;
        }
    }

    transaction.commit()?;
    Ok(())
}

fn clear_workspace_index(
    connection: &Connection,
    workspace_id: WorkspaceId,
) -> Result<(), StoreError> {
    let workspace = workspace_id.to_string();
    connection.execute(
        "DELETE FROM workspace_symbols WHERE workspace_id = ?1",
        [&workspace],
    )?;
    connection.execute(
        "DELETE FROM workspace_fts WHERE workspace_id = ?1",
        [&workspace],
    )?;
    connection.execute(
        "DELETE FROM workspace_files WHERE workspace_id = ?1",
        [&workspace],
    )?;
    Ok(())
}

fn fts_terms(query: &str) -> String {
    let terms: Vec<String> = query
        .split(|character: char| !character.is_alphanumeric())
        .filter(|term| !term.is_empty())
        .map(|term| format!("\"{}\"*", term.to_lowercase()))
        .collect();
    terms.join(" AND ")
}

fn save_file_checkpoint(
    connection: &Connection,
    session_id: SessionId,
    turn: Option<u32>,
    path: &str,
    existed: bool,
    content: Option<Vec<u8>>,
) -> Result<FileCheckpointSummary, StoreError> {
    let id = Uuid::now_v7().to_string();
    let created_at = now_millis();
    let bytes = content.as_ref().map_or(0, |content| content.len()) as u64;
    let hash = content
        .as_deref()
        .map(blake3::hash)
        .map(|hash| hash.as_bytes().to_vec());
    connection.execute(
        "INSERT INTO file_checkpoints
            (id, session_id, turn, path, existed, content, content_hash, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            id,
            session_id.to_string(),
            turn.map(i64::from),
            path,
            existed,
            content,
            hash,
            created_at
        ],
    )?;
    Ok(FileCheckpointSummary {
        id,
        path: path.to_owned(),
        existed,
        bytes,
        created_at,
    })
}

fn writer_loop(mut connection: Connection, receiver: Receiver<WriteCommand>) {
    while let Ok(command) = receiver.recv() {
        match command {
            WriteCommand::AppendEvent {
                session_id,
                draft,
                reply,
            } => {
                let _ = reply.send(append_event(&mut connection, session_id, draft));
            }
            WriteCommand::CreateWorkspace { workspace, reply } => {
                let _ = reply.send(create_workspace(&connection, workspace));
            }
            WriteCommand::EnsureWorkspace { workspace, reply } => {
                let _ = reply.send(ensure_workspace(&connection, workspace));
            }
            WriteCommand::CreateSession { session, reply } => {
                let _ = reply.send(create_session(&connection, session));
            }
            WriteCommand::SetSetting { key, value, reply } => {
                let _ = reply.send(set_setting(&connection, &key, &value));
            }
            WriteCommand::CreateProvider { provider, reply } => {
                let _ = reply.send(create_provider(&connection, provider));
            }
            WriteCommand::CreateModelRoute { route, reply } => {
                let _ = reply.send(create_model_route(&connection, route));
            }
            WriteCommand::AuditAppend {
                actor,
                action,
                target,
                decision,
                policy,
                metadata,
                reply,
            } => {
                let _ = reply.send(audit::append(
                    &mut connection,
                    &actor,
                    &action,
                    target.as_deref(),
                    decision.as_deref(),
                    policy.as_deref(),
                    metadata,
                ));
            }
            WriteCommand::Backup { path, reply } => {
                let result = connection
                    .execute("VACUUM INTO ?1", [path.to_string_lossy().as_ref()])
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::RebuildProjections { reply } => {
                let _ = reply.send(projections::rebuild(&mut connection));
            }
            WriteCommand::UpdateWorkspaceIndex {
                workspace_id,
                files,
                removed,
                reply,
            } => {
                let _ = reply.send(update_workspace_index(
                    &mut connection,
                    workspace_id,
                    &files,
                    &removed,
                ));
            }
            WriteCommand::ClearWorkspaceIndex {
                workspace_id,
                reply,
            } => {
                let _ = reply.send(clear_workspace_index(&connection, workspace_id));
            }
            WriteCommand::SaveFileCheckpoint {
                session_id,
                turn,
                path,
                existed,
                content,
                reply,
            } => {
                let _ = reply.send(save_file_checkpoint(
                    &connection,
                    session_id,
                    turn,
                    &path,
                    existed,
                    content,
                ));
            }
            WriteCommand::DeleteFileCheckpoint {
                checkpoint_id,
                reply,
            } => {
                let result = connection
                    .execute(
                        "DELETE FROM file_checkpoints WHERE id = ?1",
                        [&checkpoint_id],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::Shutdown => break,
        }
    }
}

fn create_workspace(
    connection: &Connection,
    workspace: NewWorkspace,
) -> Result<WorkspaceId, StoreError> {
    let id = WorkspaceId::new();
    let now = now_millis();
    connection.execute(
        "INSERT INTO workspaces (id, name, path, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?4)",
        params![id.to_string(), workspace.name, workspace.path, now],
    )?;
    Ok(id)
}

fn ensure_workspace(
    connection: &Connection,
    workspace: NewWorkspace,
) -> Result<WorkspaceId, StoreError> {
    let id = WorkspaceId::new();
    let now = now_millis();
    connection.execute(
        "INSERT INTO workspaces (id, name, path, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?4)
         ON CONFLICT(path) DO UPDATE SET
             name = excluded.name,
             updated_at = excluded.updated_at",
        params![id.to_string(), workspace.name, workspace.path, now],
    )?;
    let id: String = connection.query_row(
        "SELECT id FROM workspaces WHERE path = ?1",
        [workspace.path],
        |row| row.get(0),
    )?;
    Uuid::parse_str(&id)
        .map(WorkspaceId::from)
        .map_err(|error| StoreError::InvalidData(error.to_string()))
}

fn create_session(connection: &Connection, session: NewSession) -> Result<SessionId, StoreError> {
    let id = SessionId::new();
    let now = now_millis();
    let workspace_id = session.workspace_id.map(|value| value.to_string());
    connection.execute(
        "INSERT INTO sessions (id, workspace_id, title, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?4)",
        params![id.to_string(), workspace_id, session.title, now],
    )?;
    Ok(id)
}

fn append_event(
    connection: &mut Connection,
    session_id: SessionId,
    draft: NewEvent,
) -> Result<SessionEvent, StoreError> {
    let NewEvent {
        kind,
        turn,
        step,
        payload,
    } = draft;
    let payload_json = serde_json::to_string(&payload)?;
    let created_at = now_millis();
    let surface = kind.is_surface();

    let transaction = connection.transaction()?;
    let next_seq: i64 = transaction.query_row(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM session_events WHERE session_id = ?1",
        [session_id.to_string()],
        |row| row.get(0),
    )?;

    let event = SessionEvent {
        id: Uuid::now_v7(),
        session_id,
        seq: next_seq as u64,
        turn,
        step,
        kind,
        surface,
        payload,
        created_at,
    };

    transaction.execute(
        "INSERT INTO session_events
         (id, session_id, seq, turn, step, kind, surface, payload_json, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            event.id.to_string(),
            session_id.to_string(),
            next_seq,
            turn.map(i64::from),
            step.map(i64::from),
            kind.as_str(),
            if surface { 1_i64 } else { 0_i64 },
            payload_json,
            created_at,
        ],
    )?;
    transaction.execute(
        "UPDATE sessions
         SET updated_at = ?2,
             last_turn = CASE
                 WHEN ?3 IS NOT NULL AND ?3 > last_turn THEN ?3
                 ELSE last_turn
             END
         WHERE id = ?1",
        params![session_id.to_string(), created_at, turn.map(i64::from)],
    )?;

    projections::apply(&transaction, &event)?;
    transaction.commit()?;

    Ok(event)
}

fn set_setting(connection: &Connection, key: &str, value: &Value) -> Result<(), StoreError> {
    let now = now_millis();
    connection.execute(
        "INSERT INTO settings (key, value_json, updated_at)
         VALUES (?1, ?2, ?3)
         ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                       updated_at = excluded.updated_at",
        params![key, serde_json::to_string(value)?, now],
    )?;
    Ok(())
}

fn create_provider(
    connection: &Connection,
    provider: NewProvider,
) -> Result<ProviderRecord, StoreError> {
    let id = ProviderId::new();
    let now = now_millis();
    let NewProvider {
        kind,
        name,
        base_url,
        keychain_ref,
    } = provider;
    connection.execute(
        "INSERT INTO providers
         (id, kind, name, base_url, keychain_ref, enabled, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6, ?6)",
        params![id.to_string(), kind, name, base_url, keychain_ref, now],
    )?;
    Ok(ProviderRecord {
        id,
        kind,
        name,
        base_url,
        keychain_ref,
        enabled: true,
    })
}

fn create_model_route(
    connection: &Connection,
    route: NewModelRoute,
) -> Result<ModelRouteRecord, StoreError> {
    let id = ModelRouteId::new();
    connection.execute(
        "INSERT INTO model_routes
         (id, name, task_class, provider_id, model, context_window, max_output_tokens,
          pricing_json, peak_pricing_json, cache_mode, api_format, thinking_default,
          capabilities_json, enabled)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, 1)",
        params![
            id.to_string(),
            route.name,
            route.task_class,
            route.provider_id.to_string(),
            route.model,
            route.context_window,
            route.max_output_tokens,
            serde_json::to_string(&route.pricing)?,
            serde_json::to_string(&route.peak_pricing)?,
            route.cache_mode,
            route.api_format,
            route.thinking_default,
            serde_json::to_string(&route.capabilities)?,
        ],
    )?;
    Ok(ModelRouteRecord {
        id,
        name: route.name,
        task_class: route.task_class,
        provider_id: route.provider_id,
        model: route.model,
        context_window: route.context_window,
        max_output_tokens: route.max_output_tokens,
        pricing: route.pricing,
        peak_pricing: route.peak_pricing,
        cache_mode: route.cache_mode,
        api_format: route.api_format,
        thinking_default: route.thinking_default,
        capabilities: route.capabilities,
        enabled: true,
    })
}

#[cfg(test)]
mod tests {
    use openbad_core::event::SessionEventKind;
    use serde_json::json;

    use super::*;

    #[test]
    fn migrations_are_recorded() {
        let store = Store::open_in_memory().expect("store");
        assert_eq!(
            store.schema_version().expect("version"),
            "0004_file_checkpoints"
        );
    }

    #[test]
    fn append_and_load_events() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-demo")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("first session"))
            .expect("session");

        let start = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            )
            .expect("turn start");
        assert_eq!(start.seq, 1);
        assert!(!start.surface);

        let message = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({"content": "hello", "source": "user"})),
            )
            .expect("user message");
        assert_eq!(message.seq, 2);
        assert!(message.surface);

        let events = store.load_events(session).expect("events");
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, SessionEventKind::TurnStart);
        assert_eq!(events[1].kind, SessionEventKind::UserMessage);
        assert_eq!(events[1].payload["content"], "hello");

        let messages = store.load_messages(session).expect("messages");
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].role, "user");
        assert_eq!(messages[0].content["content"], "hello");

        let turns = store.load_turns(session).expect("turns");
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].turn, 1);
    }

    #[test]
    fn projections_rebuild_from_events() {
        let store = Store::open_in_memory().expect("store");
        let session = store.create_session(None, None).expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            )
            .expect("start");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(1)
                    .with_payload(json!({"content": "hello"})),
            )
            .expect("message");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnEnd)
                    .with_turn(1)
                    .with_payload(json!({"reason": "complete"})),
            )
            .expect("end");

        let before = store.load_messages(session).expect("messages");
        store.rebuild_projections().expect("rebuild");
        let after = store.load_messages(session).expect("messages");
        assert_eq!(before, after);

        let turns = store.load_turns(session).expect("turns");
        assert_eq!(turns[0].reason.as_deref(), Some("complete"));
    }

    #[test]
    fn sequence_is_monotonic_per_session() {
        let store = Store::open_in_memory().expect("store");
        let first = store.create_session(None, None).expect("first");
        let second = store.create_session(None, None).expect("second");

        for expected in 1..=4 {
            let event = store
                .append_event(
                    first,
                    NewEvent::new(SessionEventKind::TurnStart).with_turn(expected),
                )
                .expect("event");
            assert_eq!(event.seq, expected as u64);
        }
        let other = store
            .append_event(
                second,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            )
            .expect("other");
        assert_eq!(other.seq, 1);
    }

    #[test]
    fn session_requires_an_existing_workspace() {
        let store = Store::open_in_memory().expect("store");
        let missing = openbad_core::WorkspaceId::new();
        let error = store
            .create_session(Some(missing), None)
            .expect_err("foreign key");
        assert!(matches!(error, StoreError::Sqlite(_)));
    }

    #[test]
    fn tool_results_are_projected_as_messages() {
        let store = Store::open_in_memory().expect("store");
        let session = store.create_session(None, None).expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::ToolResult)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({
                        "content": "hello",
                        "tool_call_id": "call-1"
                    })),
            )
            .expect("tool result");

        let messages = store.load_messages(session).expect("messages");
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].role, "tool");
        assert_eq!(messages[0].content["tool_call_id"], "call-1");
    }

    #[test]
    fn settings_round_trip() {
        let store = Store::open_in_memory().expect("store");
        store
            .set_setting("theme", json!({"mode": "dark"}))
            .expect("set");
        assert_eq!(
            store.get_setting("theme").expect("get"),
            Some(json!({"mode": "dark"}))
        );
        assert_eq!(store.get_setting("missing").expect("get"), None);
    }

    #[test]
    fn ensure_workspace_reuses_the_same_path() {
        let store = Store::open_in_memory().expect("store");
        let first = store
            .ensure_workspace("demo", "/tmp/openbad-ensure")
            .expect("first");
        let second = store
            .ensure_workspace("renamed", "/tmp/openbad-ensure")
            .expect("second");
        assert_eq!(first, second);
    }

    #[test]
    fn sessions_list_reports_workspace_and_last_turn() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-sessions")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("listed"))
            .expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(3),
            )
            .expect("turn");

        let sessions = store.list_sessions(10).expect("sessions");
        assert_eq!(sessions.len(), 1);
        assert_eq!(
            sessions[0].workspace_path.as_deref(),
            Some("/tmp/openbad-sessions")
        );
        assert_eq!(sessions[0].title.as_deref(), Some("listed"));
        assert_eq!(sessions[0].last_turn, 3);

        assert_eq!(
            store
                .search_sessions("listed", 10)
                .expect("title search")
                .len(),
            1
        );
        assert!(
            store
                .search_sessions("missing", 10)
                .expect("empty search")
                .is_empty()
        );
    }

    #[test]
    fn file_checkpoints_round_trip_and_delete() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-checkpoints")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("checkpoint"))
            .expect("session");

        let checkpoint = store
            .save_file_checkpoint(
                session,
                Some(1),
                "src/main.rs",
                true,
                Some(b"original".to_vec()),
            )
            .expect("save");
        assert_eq!(checkpoint.path, "src/main.rs");
        assert_eq!(checkpoint.bytes, 8);

        let listed = store.list_file_checkpoints(session, 10).expect("list");
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id, checkpoint.id);

        let loaded = store
            .load_file_checkpoint(&checkpoint.id)
            .expect("load")
            .expect("checkpoint");
        assert_eq!(loaded.content.as_deref(), Some(b"original".as_slice()));
        assert!(loaded.existed);

        store
            .delete_file_checkpoint(&checkpoint.id)
            .expect("delete");
        assert!(
            store
                .list_file_checkpoints(session, 10)
                .expect("list after delete")
                .is_empty()
        );
    }

    #[test]
    fn session_search_matches_message_content() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-session-search")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("first"))
            .expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(1)
                    .with_payload(json!({"content": "explain the cache ledger"})),
            )
            .expect("message");

        let found = store.search_sessions("cache ledger", 10).expect("search");
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].id, session);
    }

    #[test]
    fn providers_and_routes_round_trip() {
        let store = Store::open_in_memory().expect("store");
        let provider = store
            .create_provider(
                NewProvider::new("deepseek", "DeepSeek")
                    .with_base_url("https://api.deepseek.com")
                    .with_keychain_ref("openbad/deepseek"),
            )
            .expect("provider");
        assert!(provider.enabled);

        let route = store
            .create_model_route(NewModelRoute::new(
                "chat-default",
                "chat",
                provider.id,
                "deepseek-flash",
            ))
            .expect("route");

        assert_eq!(route.name, "chat-default");
        assert_eq!(store.list_providers().expect("providers").len(), 1);
        assert_eq!(store.list_model_routes().expect("routes").len(), 1);
    }

    #[test]
    fn audit_chain_round_trip() {
        let store = Store::open_in_memory().expect("store");
        store
            .audit_append(
                "user",
                "workspace.trust",
                Some("workspace:demo"),
                Some("allow"),
                Some("trusted"),
                json!({"path": "/tmp/demo"}),
            )
            .expect("audit");
        let report = store.audit_verify().expect("verify");
        assert!(report.valid);
        assert_eq!(report.records, 1);
    }

    #[test]
    fn backup_and_integrity_check() {
        let store = Store::open_in_memory().expect("store");
        store
            .append_event(
                store.create_session(None, None).expect("session"),
                NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            )
            .expect("event");

        let directory = tempfile::tempdir().expect("tempdir");
        let backup_path = directory.path().join("backup.db");
        store.backup_to(&backup_path).expect("backup");
        assert!(backup_path.exists());
        assert_eq!(store.integrity_check().expect("integrity"), "ok");

        let backup = Store::open(&backup_path).expect("open backup");
        assert_eq!(backup.doctor().expect("doctor").event_count, 1);
        drop(backup);
        drop(store);

        let restored_path = directory.path().join("restored.db");
        Store::restore_from(&backup_path, &restored_path).expect("restore");
        let restored = Store::open(&restored_path).expect("open restored");
        assert_eq!(restored.doctor().expect("doctor").event_count, 1);
    }

    #[test]
    fn workspace_index_searches_text_and_symbols() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("index", "/tmp/openbad-index")
            .expect("workspace");
        store
            .update_workspace_index(
                workspace,
                vec![IndexedFile {
                    path: "src/agent.rs".to_owned(),
                    hash: "abc".to_owned(),
                    size: 42,
                    modified_at: 1,
                    language: Some("rust".to_owned()),
                    content: "pub struct AgentConfig { max_steps: u32 }\nfn run_agent() {}"
                        .to_owned(),
                    symbols: vec![
                        crate::types::IndexedSymbol {
                            name: "AgentConfig".to_owned(),
                            kind: "struct".to_owned(),
                            line: 1,
                            signature: "pub struct AgentConfig".to_owned(),
                        },
                        crate::types::IndexedSymbol {
                            name: "run_agent".to_owned(),
                            kind: "fn".to_owned(),
                            line: 2,
                            signature: "fn run_agent()".to_owned(),
                        },
                    ],
                }],
                Vec::new(),
            )
            .expect("index");

        let hits = store
            .search_workspace(workspace, "max steps", 10)
            .expect("search");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].path, "src/agent.rs");
        println!("SNIPPET={:?}", hits[0].snippet);
        assert!(hits[0].snippet.contains("max"));

        let symbols = store
            .find_indexed_symbols(workspace, "agent", Some("struct"), 10)
            .expect("symbols");
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0].name, "AgentConfig");
        assert_eq!(symbols[0].line, 1);

        let summary = store.workspace_index_summary(workspace).expect("summary");
        assert_eq!(summary.files, 1);
        assert_eq!(summary.symbols, 2);

        store
            .update_workspace_index(workspace, Vec::new(), vec!["src/agent.rs".to_owned()])
            .expect("remove");
        assert!(
            store
                .search_workspace(workspace, "agent", 10)
                .expect("search after removal")
                .is_empty()
        );
    }

    #[test]
    fn restore_rejects_a_corrupt_backup() {
        let directory = tempfile::tempdir().expect("tempdir");
        let corrupt = directory.path().join("corrupt.db");
        std::fs::write(&corrupt, b"not a sqlite database").expect("write");
        let target = directory.path().join("restored.db");

        let error = Store::restore_from(&corrupt, &target).expect_err("corrupt backup");
        assert!(matches!(error, StoreError::Sqlite(_)));
        assert!(!target.exists());
    }
}
