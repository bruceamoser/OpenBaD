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
    IndexSummary, IndexedFile, KnowledgeDocument, KnowledgeDocumentSummary, MemoryRecord,
    ModelRouteRecord, NewModelRoute, NewProvider, NewSession, NewWorkspace, ProjectHit,
    ProjectOverview, ProjectRepo, ProviderRecord, SessionMessage, SessionSummary, SessionTurn,
    SymbolHit, WorkspaceSummary,
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
    RenameWorkspace {
        workspace_id: WorkspaceId,
        name: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    RenameSession {
        session_id: SessionId,
        title: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    DeleteWorkspace {
        workspace_id: WorkspaceId,
        reply: SyncSender<Result<(), StoreError>>,
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
    RememberMemory {
        workspace_id: WorkspaceId,
        content: String,
        tags: Vec<String>,
        pinned: bool,
        reply: SyncSender<Result<MemoryRecord, StoreError>>,
    },
    ForgetMemory {
        memory_id: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    AddProjectRepo {
        workspace_id: WorkspaceId,
        name: String,
        path: String,
        reply: SyncSender<Result<ProjectRepo, StoreError>>,
    },
    RemoveProjectRepo {
        repo_id: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    AddKnowledgeDocument {
        workspace_id: WorkspaceId,
        title: String,
        kind: String,
        source: Option<String>,
        tags: Vec<String>,
        content: String,
        reply: SyncSender<Result<KnowledgeDocumentSummary, StoreError>>,
    },
    DeleteKnowledgeDocument {
        document_id: String,
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

    pub fn list_workspaces(&self) -> Result<Vec<WorkspaceSummary>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT w.id, w.name, w.path, w.created_at, w.updated_at,
                    (SELECT COUNT(*) FROM sessions s
                     WHERE s.workspace_id = w.id AND s.deleted_at IS NULL)
             FROM workspaces w
             ORDER BY w.updated_at DESC",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(4)?,
                row.get::<_, i64>(5)?,
            ))
        })?;
        let mut projects = Vec::new();
        for row in rows {
            let (id, name, path, created_at, updated_at, session_count) = row?;
            projects.push(WorkspaceSummary {
                id: WorkspaceId::from(
                    Uuid::parse_str(&id)
                        .map_err(|error| StoreError::InvalidData(error.to_string()))?,
                ),
                name,
                path,
                created_at,
                updated_at,
                session_count: session_count as u64,
            });
        }
        Ok(projects)
    }

    pub fn workspace_summary(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<Option<WorkspaceSummary>, StoreError> {
        Ok(self
            .list_workspaces()?
            .into_iter()
            .find(|workspace| workspace.id == workspace_id))
    }

    pub fn rename_workspace(
        &self,
        workspace_id: WorkspaceId,
        name: &str,
    ) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::RenameWorkspace {
            workspace_id,
            name: name.to_owned(),
            reply,
        })
    }

    pub fn rename_session(&self, session_id: SessionId, title: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::RenameSession {
            session_id,
            title: title.to_owned(),
            reply,
        })
    }

    pub fn delete_workspace(&self, workspace_id: WorkspaceId) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteWorkspace {
            workspace_id,
            reply,
        })
    }

    pub fn list_project_repos(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<Vec<ProjectRepo>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, workspace_id, name, path, read_only, created_at
             FROM project_repos WHERE workspace_id = ?1 ORDER BY created_at",
        )?;
        let rows = statement.query_map([workspace_id.to_string()], |row| {
            let workspace: String = row.get(1)?;
            Ok(ProjectRepo {
                id: row.get(0)?,
                workspace_id: WorkspaceId::from(
                    Uuid::parse_str(&workspace).map_err(|_| rusqlite::Error::InvalidQuery)?,
                ),
                name: row.get(2)?,
                path: row.get(3)?,
                read_only: row.get(4)?,
                created_at: row.get(5)?,
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn add_project_repo(
        &self,
        workspace_id: WorkspaceId,
        name: &str,
        path: &str,
    ) -> Result<ProjectRepo, StoreError> {
        self.write(|reply| WriteCommand::AddProjectRepo {
            workspace_id,
            name: name.to_owned(),
            path: path.to_owned(),
            reply,
        })
    }

    pub fn remove_project_repo(&self, repo_id: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::RemoveProjectRepo {
            repo_id: repo_id.to_owned(),
            reply,
        })
    }

    pub fn list_knowledge_documents(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<Vec<KnowledgeDocumentSummary>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                    length(d.content),
                    (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                    d.created_at, d.updated_at
             FROM knowledge_documents d
             WHERE d.workspace_id = ?1
             ORDER BY d.updated_at DESC",
        )?;
        let rows = statement.query_map([workspace_id.to_string()], knowledge_summary_from_row)?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn knowledge_document(
        &self,
        document_id: &str,
    ) -> Result<Option<KnowledgeDocument>, StoreError> {
        let connection = self.reader()?;
        let document = connection
            .query_row(
                "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                        length(d.content),
                        (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                        d.created_at, d.updated_at, d.content
                 FROM knowledge_documents d WHERE d.id = ?1",
                [document_id],
                |row| {
                    let summary = knowledge_summary_from_row(row)?;
                    Ok(KnowledgeDocument {
                        summary,
                        content: row.get(10)?,
                    })
                },
            )
            .optional()?;
        Ok(document)
    }

    pub fn add_knowledge_document(
        &self,
        workspace_id: WorkspaceId,
        title: &str,
        kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        content: &str,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.write(|reply| WriteCommand::AddKnowledgeDocument {
            workspace_id,
            title: title.to_owned(),
            kind: kind.to_owned(),
            source: source.map(str::to_owned),
            tags,
            content: content.to_owned(),
            reply,
        })
    }

    pub fn delete_knowledge_document(&self, document_id: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteKnowledgeDocument {
            document_id: document_id.to_owned(),
            reply,
        })
    }

    pub fn project_overview(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<ProjectOverview, StoreError> {
        let index = self.workspace_index_summary(workspace_id)?;
        let connection = self.reader()?;
        let (repos, documents, characters, memories) = connection.query_row(
            "SELECT
                (SELECT COUNT(*) FROM project_repos WHERE workspace_id = ?1),
                (SELECT COUNT(*) FROM knowledge_documents WHERE workspace_id = ?1),
                (SELECT COALESCE(SUM(length(content)), 0) FROM knowledge_documents WHERE workspace_id = ?1),
                (SELECT COUNT(*) FROM memories WHERE workspace_id = ?1 AND forgotten_at IS NULL)",
            [workspace_id.to_string()],
            |row| {
                Ok((
                    row.get::<_, i64>(0)? as u64,
                    row.get::<_, i64>(1)? as u64,
                    row.get::<_, i64>(2)? as u64,
                    row.get::<_, i64>(3)? as u64,
                ))
            },
        )?;
        Ok(ProjectOverview {
            repos,
            knowledge_documents: documents,
            knowledge_characters: characters,
            memories,
            index,
        })
    }

    /// Search the project's knowledge, indexed code, and durable memories.
    /// Scores use reciprocal rank fusion so sources stay comparable.
    pub fn retrieve_project(
        &self,
        workspace_id: WorkspaceId,
        query: &str,
        limit: u32,
    ) -> Result<Vec<ProjectHit>, StoreError> {
        let terms = fts_terms(query);
        if terms.is_empty() {
            return Ok(Vec::new());
        }
        let connection = self.reader()?;
        let per_source = i64::from(limit.max(1));
        let mut hits = Vec::new();

        let mut knowledge = connection.prepare(
            "SELECT f.title, f.document_id, f.seq,
                    snippet(knowledge_fts, 4, '<<', '>>', '…', 24)
             FROM knowledge_fts f
             WHERE f.workspace_id = ?1 AND knowledge_fts MATCH ?2
             ORDER BY rank LIMIT ?3",
        )?;
        let rows = knowledge.query_map(
            params![workspace_id.to_string(), terms, per_source],
            |row| {
                let title: String = row.get(0)?;
                let document_id: String = row.get(1)?;
                let seq: i64 = row.get(2)?;
                Ok((title, document_id, seq, row.get::<_, String>(3)?))
            },
        )?;
        for (rank, row) in rows.enumerate() {
            let (title, document_id, seq, snippet) = row?;
            hits.push(ProjectHit {
                source: "knowledge".to_owned(),
                title,
                snippet,
                locator: format!("{document_id}#{seq}"),
                score: reciprocal_rank(rank),
            });
        }

        let mut code = connection.prepare(
            "SELECT path, snippet(workspace_fts, 2, '<<', '>>', '…', 18)
             FROM workspace_fts
             WHERE workspace_id = ?1 AND workspace_fts MATCH ?2
             ORDER BY rank LIMIT ?3",
        )?;
        let rows = code.query_map(
            params![workspace_id.to_string(), terms, per_source],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )?;
        for (rank, row) in rows.enumerate() {
            let (path, snippet) = row?;
            hits.push(ProjectHit {
                source: "code".to_owned(),
                title: path.clone(),
                snippet,
                locator: path,
                score: reciprocal_rank(rank),
            });
        }

        let pattern = format!(
            "%{}%",
            query
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        );
        let mut memories = connection.prepare(
            "SELECT id, content FROM memories
             WHERE workspace_id = ?1 AND forgotten_at IS NULL
               AND content LIKE ?2 ESCAPE '\\'
             ORDER BY pinned DESC, updated_at DESC LIMIT 4",
        )?;
        let rows = memories.query_map(params![workspace_id.to_string(), pattern], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        for (rank, row) in rows.enumerate() {
            let (id, content) = row?;
            hits.push(ProjectHit {
                source: "memory".to_owned(),
                title: "Durable memory".to_owned(),
                snippet: content,
                locator: id,
                score: reciprocal_rank(rank) + 0.01,
            });
        }

        hits.sort_by(|left, right| {
            right
                .score
                .partial_cmp(&left.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        hits.truncate(limit as usize);
        Ok(hits)
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

    pub fn remember_memory(
        &self,
        workspace_id: WorkspaceId,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
    ) -> Result<MemoryRecord, StoreError> {
        self.write(|reply| WriteCommand::RememberMemory {
            workspace_id,
            content: content.to_owned(),
            tags,
            pinned,
            reply,
        })
    }

    pub fn recall_memories(
        &self,
        workspace_id: WorkspaceId,
        query: Option<&str>,
        limit: u32,
    ) -> Result<Vec<MemoryRecord>, StoreError> {
        let connection = self.reader()?;
        let has_query = query.is_some_and(|query| !query.is_empty());
        let pattern = query.map_or_else(String::new, |query| {
            format!(
                "%{}%",
                query
                    .replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_")
            )
        });
        let mut statement = connection.prepare(
            "SELECT id, content, tags_json, pinned, created_at, updated_at, access_count
             FROM memories
             WHERE workspace_id = ?1
               AND forgotten_at IS NULL
               AND (?2 = 0
                    OR lower(content) LIKE lower(?3) ESCAPE '\\'
                    OR lower(tags_json) LIKE lower(?3) ESCAPE '\\')
             ORDER BY pinned DESC, updated_at DESC
             LIMIT ?4",
        )?;
        let rows = statement.query_map(
            params![
                workspace_id.to_string(),
                i64::from(has_query),
                pattern,
                i64::from(limit)
            ],
            memory_record_from_row,
        )?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn forget_memory(&self, memory_id: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::ForgetMemory {
            memory_id: memory_id.to_owned(),
            reply,
        })
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

    pub fn list_workspace_files(
        &self,
        workspace_id: WorkspaceId,
        limit: u32,
    ) -> Result<Vec<String>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT path FROM workspace_files
             WHERE workspace_id = ?1
             ORDER BY path
             LIMIT ?2",
        )?;
        let rows = statement
            .query_map(params![workspace_id.to_string(), i64::from(limit)], |row| {
                row.get::<_, String>(0)
            })?;
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

fn reciprocal_rank(rank: usize) -> f64 {
    1.0 / (60.0 + rank as f64)
}

fn knowledge_summary_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<KnowledgeDocumentSummary> {
    let workspace: String = row.get(1)?;
    let tags: String = row.get(5)?;
    Ok(KnowledgeDocumentSummary {
        id: row.get(0)?,
        workspace_id: WorkspaceId::from(
            Uuid::parse_str(&workspace).map_err(|_| rusqlite::Error::InvalidQuery)?,
        ),
        title: row.get(2)?,
        kind: row.get(3)?,
        source: row.get(4)?,
        tags: serde_json::from_str(&tags).unwrap_or_default(),
        characters: row.get::<_, i64>(6)? as u64,
        chunks: row.get::<_, i64>(7)? as u64,
        created_at: row.get(8)?,
        updated_at: row.get(9)?,
    })
}

fn add_project_repo(
    connection: &Connection,
    workspace_id: WorkspaceId,
    name: &str,
    path: &str,
) -> Result<ProjectRepo, StoreError> {
    let canonical = std::fs::canonicalize(path)
        .map_err(|error| StoreError::InvalidData(format!("repository path: {error}")))?;
    if !canonical.is_dir() {
        return Err(StoreError::InvalidData(
            "repository path is not a directory".to_owned(),
        ));
    }
    let path = canonical.to_string_lossy().into_owned();
    let exists: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM project_repos WHERE workspace_id = ?1 AND path = ?2)",
        params![workspace_id.to_string(), path],
        |row| row.get(0),
    )?;
    if exists {
        return Err(StoreError::Conflict(
            "repository is already linked to this project".to_owned(),
        ));
    }
    let name = if name.trim().is_empty() {
        canonical
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("repository")
            .to_owned()
    } else {
        name.trim().to_owned()
    };
    let repo = ProjectRepo {
        id: Uuid::now_v7().to_string(),
        workspace_id,
        name,
        path: path.clone(),
        read_only: true,
        created_at: now_millis(),
    };
    connection.execute(
        "INSERT INTO project_repos (id, workspace_id, name, path, read_only, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            repo.id,
            workspace_id.to_string(),
            repo.name,
            repo.path,
            repo.read_only,
            repo.created_at
        ],
    )?;
    Ok(repo)
}

fn add_knowledge_document(
    connection: &Connection,
    workspace_id: WorkspaceId,
    title: &str,
    kind: &str,
    source: Option<&str>,
    tags: Vec<String>,
    content: &str,
) -> Result<KnowledgeDocumentSummary, StoreError> {
    if content.trim().is_empty() {
        return Err(StoreError::InvalidData(
            "knowledge content cannot be empty".to_owned(),
        ));
    }
    let hash = blake3::hash(content.as_bytes()).to_hex().to_string();
    let existing: Option<String> = connection
        .query_row(
            "SELECT id FROM knowledge_documents WHERE workspace_id = ?1 AND content_hash = ?2",
            params![workspace_id.to_string(), hash],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(id) = existing {
        return connection
            .query_row(
                "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                        length(d.content),
                        (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                        d.created_at, d.updated_at
                 FROM knowledge_documents d WHERE d.id = ?1",
                [id],
                knowledge_summary_from_row,
            )
            .map_err(StoreError::from);
    }

    let document_id = Uuid::now_v7().to_string();
    let now = now_millis();
    let title = if title.trim().is_empty() {
        source
            .and_then(|source| source.rsplit('/').next())
            .filter(|name| !name.is_empty())
            .unwrap_or("Project note")
            .to_owned()
    } else {
        title.trim().to_owned()
    };
    let tags_json = serde_json::to_string(&tags)?;
    let chunks = split_knowledge_chunks(content);
    let transaction = connection.unchecked_transaction()?;
    transaction.execute(
        "INSERT INTO knowledge_documents
            (id, workspace_id, title, kind, source, tags_json, content, content_hash, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)",
        params![
            document_id,
            workspace_id.to_string(),
            title,
            kind,
            source,
            tags_json,
            content,
            hash,
            now
        ],
    )?;
    for (seq, chunk) in chunks.iter().enumerate() {
        let chunk_id = Uuid::now_v7().to_string();
        transaction.execute(
            "INSERT INTO knowledge_chunks (id, document_id, workspace_id, seq, content)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                chunk_id,
                document_id,
                workspace_id.to_string(),
                seq as i64,
                chunk
            ],
        )?;
        transaction.execute(
            "INSERT INTO knowledge_fts (workspace_id, document_id, title, seq, content)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                workspace_id.to_string(),
                document_id,
                title,
                seq as i64,
                chunk
            ],
        )?;
    }
    transaction.commit()?;

    connection
        .query_row(
            "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                length(d.content),
                (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                d.created_at, d.updated_at
         FROM knowledge_documents d WHERE d.id = ?1",
            [&document_id],
            knowledge_summary_from_row,
        )
        .map_err(StoreError::from)
}

fn split_knowledge_chunks(content: &str) -> Vec<String> {
    const MAX: usize = 1_400;
    const OVERLAP: usize = 160;
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < content.len() {
        while start > 0 && !content.is_char_boundary(start) {
            start -= 1;
        }
        let mut end = (start + MAX).min(content.len());
        while end < content.len() && !content.is_char_boundary(end) {
            end -= 1;
        }
        if end < content.len() {
            let window = &content[start..end];
            if let Some(boundary) = window
                .char_indices()
                .rev()
                .find(|(_, character)| *character == '\n')
                .map(|(index, _)| start + index)
                .filter(|boundary| *boundary > start + MAX / 3)
            {
                end = boundary;
            }
        }
        let chunk = content[start..end].trim();
        if !chunk.is_empty() {
            chunks.push(chunk.to_owned());
        }
        if end >= content.len() {
            break;
        }
        start = end.saturating_sub(OVERLAP);
        if start == 0 {
            start = end;
        }
    }
    chunks
}

fn fts_terms(query: &str) -> String {
    let terms: Vec<String> = query
        .split(|character: char| !character.is_alphanumeric())
        .filter(|term| !term.is_empty())
        .map(|term| format!("\"{}\"*", term.to_lowercase()))
        .collect();
    terms.join(" AND ")
}

pub fn remember_memory(
    connection: &Connection,
    workspace_id: WorkspaceId,
    content: &str,
    tags: Vec<String>,
    pinned: bool,
) -> Result<MemoryRecord, StoreError> {
    let workspace = workspace_id.to_string();
    let tags_json = serde_json::to_string(&tags)?;
    let existing: Option<String> = connection
        .query_row(
            "SELECT id FROM memories
         WHERE workspace_id = ?1 AND content = ?2 AND forgotten_at IS NULL",
            params![workspace, content],
            |row| row.get(0),
        )
        .optional()?;
    let now = now_millis();
    let id = if let Some(id) = existing {
        connection.execute(
            "UPDATE memories
         SET tags_json = ?1, pinned = MAX(pinned, ?2), updated_at = ?3
         WHERE id = ?4",
            params![tags_json, pinned, now, id],
        )?;
        id
    } else {
        let id = Uuid::now_v7().to_string();
        connection.execute(
            "INSERT INTO memories
            (id, workspace_id, content, tags_json, pinned, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
            params![id, workspace, content, tags_json, pinned, now],
        )?;
        id
    };
    connection
        .query_row(
            "SELECT id, content, tags_json, pinned, created_at, updated_at, access_count
         FROM memories WHERE id = ?1",
            [id],
            memory_record_from_row,
        )
        .map_err(StoreError::from)
}

fn memory_record_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<MemoryRecord> {
    let tags_json: String = row.get(2)?;
    Ok(MemoryRecord {
        id: row.get(0)?,
        content: row.get(1)?,
        tags: serde_json::from_str(&tags_json).unwrap_or_default(),
        pinned: row.get(3)?,
        created_at: row.get(4)?,
        updated_at: row.get(5)?,
        access_count: row.get::<_, i64>(6)? as u32,
    })
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
            WriteCommand::RenameWorkspace {
                workspace_id,
                name,
                reply,
            } => {
                let result = connection
                    .execute(
                        "UPDATE workspaces SET name = ?1, updated_at = ?2 WHERE id = ?3",
                        params![name, now_millis(), workspace_id.to_string()],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::RenameSession {
                session_id,
                title,
                reply,
            } => {
                let result = connection
                    .execute(
                        "UPDATE sessions SET title = ?1, updated_at = ?2 WHERE id = ?3",
                        params![title, now_millis(), session_id.to_string()],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::DeleteWorkspace {
                workspace_id,
                reply,
            } => {
                let result = connection
                    .execute(
                        "DELETE FROM workspaces WHERE id = ?1",
                        [workspace_id.to_string()],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
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
            WriteCommand::RememberMemory {
                workspace_id,
                content,
                tags,
                pinned,
                reply,
            } => {
                let _ = reply.send(remember_memory(
                    &connection,
                    workspace_id,
                    &content,
                    tags,
                    pinned,
                ));
            }
            WriteCommand::ForgetMemory { memory_id, reply } => {
                let result = connection
                    .execute(
                        "UPDATE memories SET forgotten_at = ?1, updated_at = ?1 WHERE id = ?2",
                        params![now_millis(), memory_id],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::AddProjectRepo {
                workspace_id,
                name,
                path,
                reply,
            } => {
                let _ = reply.send(add_project_repo(&connection, workspace_id, &name, &path));
            }
            WriteCommand::RemoveProjectRepo { repo_id, reply } => {
                let result = connection
                    .execute("DELETE FROM project_repos WHERE id = ?1", [&repo_id])
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
            }
            WriteCommand::AddKnowledgeDocument {
                workspace_id,
                title,
                kind,
                source,
                tags,
                content,
                reply,
            } => {
                let _ = reply.send(add_knowledge_document(
                    &connection,
                    workspace_id,
                    &title,
                    &kind,
                    source.as_deref(),
                    tags,
                    &content,
                ));
            }
            WriteCommand::DeleteKnowledgeDocument { document_id, reply } => {
                let result = (|| -> Result<(), StoreError> {
                    connection.execute(
                        "DELETE FROM knowledge_fts WHERE document_id = ?1",
                        [&document_id],
                    )?;
                    connection.execute(
                        "DELETE FROM knowledge_documents WHERE id = ?1",
                        [&document_id],
                    )?;
                    Ok(())
                })();
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
            "0006_project_areas"
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
    fn projects_list_and_rename_with_sessions() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .ensure_workspace("demo", "/tmp/openbad-projects")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("original"))
            .expect("session");

        let projects = store.list_workspaces().expect("projects");
        assert_eq!(projects.len(), 1);
        assert_eq!(projects[0].name, "demo");
        assert_eq!(projects[0].session_count, 1);

        store
            .rename_workspace(workspace, "Renamed")
            .expect("rename");
        store
            .rename_session(session, "Better title")
            .expect("rename session");
        let projects = store.list_workspaces().expect("projects");
        assert_eq!(projects[0].name, "Renamed");
        let sessions = store.list_sessions(10).expect("sessions");
        assert_eq!(sessions[0].title.as_deref(), Some("Better title"));

        store
            .ensure_workspace("folder-name", "/tmp/openbad-projects")
            .expect("re-ensure");
        let projects = store.list_workspaces().expect("projects");
        assert_eq!(projects[0].name, "Renamed");
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
    fn memories_round_trip_dedupe_and_forget() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("memory", "/tmp/openbad-memory")
            .expect("workspace");
        let memory = store
            .remember_memory(
                workspace,
                "Use the cache-first prompt order.",
                vec!["decision".to_owned()],
                true,
            )
            .expect("remember");
        assert!(memory.pinned);

        let duplicate = store
            .remember_memory(
                workspace,
                "Use the cache-first prompt order.",
                vec!["prompt".to_owned()],
                false,
            )
            .expect("dedupe");
        assert_eq!(duplicate.id, memory.id);
        assert_eq!(duplicate.tags, vec!["prompt".to_owned()]);
        assert!(duplicate.pinned);

        let recalled = store
            .recall_memories(workspace, Some("cache"), 10)
            .expect("recall");
        assert_eq!(recalled.len(), 1);
        assert_eq!(recalled[0].id, memory.id);

        store.forget_memory(&memory.id).expect("forget");
        assert!(
            store
                .recall_memories(workspace, None, 10)
                .expect("recall after forget")
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
    fn project_areas_are_scoped_and_searchable() {
        let store = Store::open_in_memory().expect("store");
        let first = store
            .ensure_workspace("First", "/tmp/first")
            .expect("first workspace");
        let second = store
            .ensure_workspace("Second", "/tmp/second")
            .expect("second workspace");
        store
            .remember_memory(first, "Keep the parser incremental cache.", vec![], false)
            .expect("memory");
        let document = store
            .add_knowledge_document(
                first,
                "Parser architecture",
                "note",
                Some("design.md"),
                vec!["parser".to_owned()],
                "The parser keeps an incremental cache so edits stay cheap.\n\n\
                 Cache invalidation uses content hashes. The assistant should read\
                 the design note before changing syntax nodes.",
            )
            .expect("knowledge");
        assert_eq!(document.workspace_id, first);
        assert_eq!(document.chunks, 1);
        assert_eq!(document.tags, vec!["parser".to_owned()]);

        let duplicate = store
            .add_knowledge_document(
                first,
                "Duplicate",
                "note",
                None,
                vec![],
                "The parser keeps an incremental cache so edits stay cheap.\n\n\
                 Cache invalidation uses content hashes. The assistant should read\
                 the design note before changing syntax nodes.",
            )
            .expect("idempotent knowledge");
        assert_eq!(duplicate.id, document.id);

        let hits = store
            .retrieve_project(first, "incremental cache", 10)
            .expect("retrieval");
        assert!(
            hits.iter()
                .any(|hit| hit.source == "knowledge" && hit.title == "Parser architecture")
        );
        assert!(hits.iter().any(|hit| hit.source == "memory"));
        assert!(
            store
                .retrieve_project(second, "incremental cache", 10)
                .expect("scoped retrieval")
                .is_empty()
        );

        let overview = store.project_overview(first).expect("overview");
        assert_eq!(overview.knowledge_documents, 1);
        assert_eq!(overview.memories, 1);

        let full = store
            .knowledge_document(&document.id)
            .expect("document")
            .expect("present");
        assert!(full.content.contains("content hashes"));

        store
            .delete_knowledge_document(&document.id)
            .expect("delete knowledge");
        assert!(
            store
                .retrieve_project(first, "incremental cache", 10)
                .expect("retrieval after delete")
                .iter()
                .all(|hit| hit.source != "knowledge")
        );
    }

    #[test]
    fn splits_long_knowledge_into_overlapping_chunks() {
        let content = "paragraph one ".repeat(200);
        let chunks = split_knowledge_chunks(&content);
        assert!(chunks.len() > 1);
        assert!(chunks.iter().all(|chunk| chunk.chars().count() <= 1_400));
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
