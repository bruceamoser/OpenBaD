use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::sync::{Mutex, MutexGuard};
use std::thread::{self, JoinHandle};

use openbad_core::event::{NewEvent, SessionEvent, SessionEventKind};
use openbad_core::{ModelRouteId, ProviderId, SessionId, WorkspaceId};
use rusqlite::{Connection, OpenFlags, OptionalExtension, Transaction, params};
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
    ModelRouteRecord, NewModelRoute, NewProvider, NewSession, NewWorkspace, OutsideAccessGrant,
    ProjectHit, ProjectOverview, ProjectRepo, ProviderRecord, SessionEventRecord, SessionMessage,
    SessionSearchHit, SessionSummary, SessionTurn, SubagentRecord, SubagentUpdate, SymbolHit,
    WorkspaceSummary,
};

const REFERENCE_GRANT_REASON: &str = "reference folder (automatic)";

enum WriteCommand {
    AppendEvent {
        session_id: SessionId,
        draft: NewEvent,
        reply: SyncSender<Result<SessionEvent, StoreError>>,
    },
    AppendEvents {
        session_id: SessionId,
        drafts: Vec<NewEvent>,
        reply: SyncSender<Result<Vec<SessionEvent>, StoreError>>,
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
    ForkSession {
        source: SessionId,
        at_user_seq: u64,
        title: Option<String>,
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
    SetSessionAccessMode {
        session_id: SessionId,
        access_mode: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    SetSessionArchived {
        session_id: SessionId,
        archived: bool,
        reply: SyncSender<Result<(), StoreError>>,
    },
    DeleteSession {
        session_id: SessionId,
        reply: SyncSender<Result<(), StoreError>>,
    },
    AddOutsideAccessGrant {
        workspace_id: WorkspaceId,
        path: String,
        write_access: bool,
        reason: String,
        reply: SyncSender<Result<OutsideAccessGrant, StoreError>>,
    },
    RemoveOutsideAccessGrant {
        workspace_id: WorkspaceId,
        grant_id: String,
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
        scope: String,
        author: String,
        origin_turn: Option<u32>,
        origin_task: Option<String>,
        supersedes: Option<String>,
        expires_at: Option<i64>,
        reply: SyncSender<Result<MemoryRecord, StoreError>>,
    },
    UpdateMemory {
        memory_id: String,
        content: Option<String>,
        tags: Option<Vec<String>>,
        pinned: Option<bool>,
        scope: Option<String>,
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
    SetProjectRepoReadOnly {
        repo_id: String,
        read_only: bool,
        reply: SyncSender<Result<(), StoreError>>,
    },
    AddKnowledgeDocument {
        workspace_id: WorkspaceId,
        title: String,
        kind: String,
        source: Option<String>,
        tags: Vec<String>,
        content: String,
        confidence: String,
        source_hash: Option<String>,
        supersedes: Option<String>,
        review_status: String,
        reply: SyncSender<Result<KnowledgeDocumentSummary, StoreError>>,
    },
    UpdateKnowledgeDocument {
        document_id: String,
        title: Option<String>,
        content: Option<String>,
        source: Option<String>,
        tags: Option<Vec<String>>,
        confidence: Option<String>,
        source_hash: Option<String>,
        supersedes: Option<String>,
        review_status: Option<String>,
        reply: SyncSender<Result<KnowledgeDocumentSummary, StoreError>>,
    },
    SetKnowledgeKind {
        document_id: String,
        kind: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    DeleteKnowledgeDocument {
        document_id: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    ImportWorkspaceState {
        workspace_id: WorkspaceId,
        state: Box<crate::transfer::WorkspaceState>,
        reply: SyncSender<Result<crate::transfer::ImportCounts, StoreError>>,
    },
    CreateSubagent {
        record: SubagentRecord,
        reply: SyncSender<Result<SubagentRecord, StoreError>>,
    },
    UpdateSubagent {
        id: String,
        update: SubagentUpdate,
        reply: SyncSender<Result<SubagentRecord, StoreError>>,
    },
    DeleteFinishedSubagent {
        id: String,
        reply: SyncSender<Result<(), StoreError>>,
    },
    PruneFinishedSubagents {
        workspace_id: WorkspaceId,
        older_than: i64,
        reply: SyncSender<Result<u64, StoreError>>,
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

    /// Open the application database, honoring `OPENBAD_DB_PATH` for isolated
    /// automation runs.
    pub fn open_default() -> Result<Self, StoreError> {
        if let Some(path) = std::env::var_os("OPENBAD_DB_PATH") {
            let path = PathBuf::from(path);
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            return Self::open(path);
        }
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

    pub fn session_access_mode(&self, session_id: SessionId) -> Result<String, StoreError> {
        let connection = self.reader()?;
        let metadata: Option<String> = connection
            .query_row(
                "SELECT metadata_json FROM sessions WHERE id = ?1",
                [session_id.to_string()],
                |row| row.get(0),
            )
            .optional()?;
        Ok(metadata
            .as_deref()
            .map(access_mode_from_metadata)
            .unwrap_or_else(|| "read".to_owned()))
    }

    pub fn set_session_access_mode(
        &self,
        session_id: SessionId,
        access_mode: &str,
    ) -> Result<(), StoreError> {
        if !matches!(
            access_mode,
            "read" | "edit" | "ask-writes" | "ask-commands" | "full"
        ) {
            return Err(StoreError::InvalidData(format!(
                "unknown access mode: {access_mode}"
            )));
        }
        self.write(|reply| WriteCommand::SetSessionAccessMode {
            session_id,
            access_mode: access_mode.to_owned(),
            reply,
        })
    }

    pub fn delete_workspace(&self, workspace_id: WorkspaceId) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteWorkspace {
            workspace_id,
            reply,
        })
    }

    pub fn list_outside_access_grants(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<Vec<OutsideAccessGrant>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, workspace_id, path, write_access, reason, created_at
             FROM outside_access_grants
             WHERE workspace_id = ?1
             ORDER BY created_at ASC",
        )?;
        let rows = statement.query_map([workspace_id.to_string()], |row| {
            Ok(OutsideAccessGrant {
                id: row.get(0)?,
                workspace_id,
                path: row.get(2)?,
                write_access: row.get::<_, i64>(3)? != 0,
                reason: row.get(4)?,
                created_at: row.get(5)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn add_outside_access_grant(
        &self,
        workspace_id: WorkspaceId,
        path: &str,
        write_access: bool,
        reason: &str,
    ) -> Result<OutsideAccessGrant, StoreError> {
        self.write(|reply| WriteCommand::AddOutsideAccessGrant {
            workspace_id,
            path: path.to_owned(),
            write_access,
            reason: reason.to_owned(),
            reply,
        })
    }

    pub fn remove_outside_access_grant(
        &self,
        workspace_id: WorkspaceId,
        grant_id: &str,
    ) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::RemoveOutsideAccessGrant {
            workspace_id,
            grant_id: grant_id.to_owned(),
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

    pub fn set_project_repo_read_only(
        &self,
        repo_id: &str,
        read_only: bool,
    ) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::SetProjectRepoReadOnly {
            repo_id: repo_id.to_owned(),
            read_only,
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
                    d.created_at, d.updated_at, d.confidence, d.source_hash, d.supersedes, d.review_status
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
                        d.created_at, d.updated_at, d.confidence, d.source_hash, d.supersedes, d.review_status, d.content
                 FROM knowledge_documents d WHERE d.id = ?1",
                [document_id],
                |row| {
                    let summary = knowledge_summary_from_row(row)?;
                    Ok(KnowledgeDocument {
                        summary,
                        content: row.get(14)?,
                    })
                },
            )
            .optional()?;
        Ok(document)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn add_knowledge_document(
        &self,
        workspace_id: WorkspaceId,
        title: &str,
        kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        content: &str,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.add_knowledge_document_with_confidence(
            workspace_id,
            title,
            kind,
            source,
            tags,
            content,
            "inferred",
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn add_knowledge_document_with_confidence(
        &self,
        workspace_id: WorkspaceId,
        title: &str,
        kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        content: &str,
        confidence: &str,
        source_hash: Option<&str>,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.add_knowledge_document_with_metadata(
            workspace_id,
            title,
            kind,
            source,
            tags,
            content,
            confidence,
            source_hash,
            None,
            "accepted",
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn add_knowledge_document_with_metadata(
        &self,
        workspace_id: WorkspaceId,
        title: &str,
        kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        content: &str,
        confidence: &str,
        source_hash: Option<&str>,
        supersedes: Option<&str>,
        review_status: &str,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.write(|reply| WriteCommand::AddKnowledgeDocument {
            workspace_id,
            title: title.to_owned(),
            kind: kind.to_owned(),
            source: source.map(str::to_owned),
            tags,
            content: content.to_owned(),
            confidence: confidence.to_owned(),
            source_hash: source_hash.map(str::to_owned),
            supersedes: supersedes.map(str::to_owned),
            review_status: review_status.to_owned(),
            reply,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update_knowledge_document(
        &self,
        document_id: &str,
        title: Option<&str>,
        content: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.update_knowledge_document_with_confidence(
            document_id,
            title,
            content,
            source,
            tags,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update_knowledge_document_with_confidence(
        &self,
        document_id: &str,
        title: Option<&str>,
        content: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
        confidence: Option<&str>,
        source_hash: Option<&str>,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.update_knowledge_document_with_metadata(
            document_id,
            title,
            content,
            source,
            tags,
            confidence,
            source_hash,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update_knowledge_document_with_metadata(
        &self,
        document_id: &str,
        title: Option<&str>,
        content: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
        confidence: Option<&str>,
        source_hash: Option<&str>,
        supersedes: Option<&str>,
        review_status: Option<&str>,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.write(|reply| WriteCommand::UpdateKnowledgeDocument {
            document_id: document_id.to_owned(),
            title: title.map(str::to_owned),
            content: content.map(str::to_owned),
            source: source.map(str::to_owned),
            tags,
            confidence: confidence.map(str::to_owned),
            source_hash: source_hash.map(str::to_owned),
            supersedes: supersedes.map(str::to_owned),
            review_status: review_status.map(str::to_owned),
            reply,
        })
    }

    pub fn set_knowledge_review_status(
        &self,
        document_id: &str,
        review_status: &str,
    ) -> Result<KnowledgeDocumentSummary, StoreError> {
        self.update_knowledge_document_with_metadata(
            document_id,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Some(review_status),
        )
    }

    pub fn set_knowledge_kind(&self, document_id: &str, kind: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::SetKnowledgeKind {
            document_id: document_id.to_owned(),
            kind: kind.to_owned(),
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
             JOIN knowledge_documents d ON d.id = f.document_id
             WHERE f.workspace_id = ?1 AND knowledge_fts MATCH ?2
               AND d.review_status NOT IN ('rejected', 'superseded')
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

        let mut memories = connection.prepare(
            "SELECT m.id, snippet(memory_fts, 2, '<<', '>>', '…', 24)
             FROM memory_fts
             JOIN memories m ON m.id = memory_fts.id
             WHERE memory_fts.workspace_id = ?1
               AND memory_fts MATCH ?2
               AND m.forgotten_at IS NULL
             ORDER BY m.pinned DESC, memory_fts.rank LIMIT 4",
        )?;
        let rows = memories.query_map(params![workspace_id.to_string(), terms], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        for (rank, row) in rows.enumerate() {
            let (id, snippet) = row?;
            hits.push(ProjectHit {
                source: "memory".to_owned(),
                title: "Durable memory".to_owned(),
                snippet,
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

    /// Appends several events in one transaction. Either every event lands or
    /// none does, which is what step settlement requires.
    pub fn append_events(
        &self,
        session_id: SessionId,
        drafts: Vec<NewEvent>,
    ) -> Result<Vec<SessionEvent>, StoreError> {
        if drafts.is_empty() {
            return Ok(Vec::new());
        }
        self.write(|reply| WriteCommand::AppendEvents {
            session_id,
            drafts,
            reply,
        })
    }

    /// Copy the session's history before `at_user_seq` into a new session.
    ///
    /// `at_user_seq` must be the sequence of a `user/message` event: forking
    /// there can never split a tool round trip, because every tool result
    /// follows its assistant message inside the same turn.
    pub fn fork_session(
        &self,
        source: SessionId,
        at_user_seq: u64,
        title: Option<String>,
    ) -> Result<SessionId, StoreError> {
        self.write(|reply| WriteCommand::ForkSession {
            source,
            at_user_seq,
            title,
            reply,
        })
    }

    /// The session this one was forked from and the sequence it branched at.
    pub fn session_fork_origin(
        &self,
        session_id: SessionId,
    ) -> Result<Option<(SessionId, u64)>, StoreError> {
        let connection = self.reader()?;
        let row = connection
            .query_row(
                "SELECT forked_from_session, forked_from_seq FROM sessions WHERE id = ?1",
                [session_id.to_string()],
                |row| {
                    Ok((
                        row.get::<_, Option<String>>(0)?,
                        row.get::<_, Option<i64>>(1)?,
                    ))
                },
            )
            .ok();
        let Some((Some(source), Some(seq))) = row else {
            return Ok(None);
        };
        let source = Uuid::parse_str(&source)
            .map(SessionId::from)
            .map_err(|error| StoreError::InvalidData(error.to_string()))?;
        Ok(u64::try_from(seq).ok().map(|seq| (source, seq)))
    }

    /// Reads all project-scoped state for a transfer bundle.
    pub fn export_workspace_state(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<crate::transfer::WorkspaceState, StoreError> {
        let connection = self.reader()?;
        crate::transfer::export_workspace_state(&connection, workspace_id)
    }

    /// Restores a transfer bundle's state into a workspace. Rows are upserted
    /// by id, so re-importing the same bundle is idempotent.
    pub fn import_workspace_state(
        &self,
        workspace_id: WorkspaceId,
        state: crate::transfer::WorkspaceState,
    ) -> Result<crate::transfer::ImportCounts, StoreError> {
        self.write(|reply| WriteCommand::ImportWorkspaceState {
            workspace_id,
            state: Box::new(state),
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
                              WHERE t.session_id = s.id), s.last_turn, 0),
                    s.metadata_json, s.forked_from_seq, s.forked_from_session
             FROM sessions s
             LEFT JOIN workspaces w ON w.id = s.workspace_id
             WHERE s.deleted_at IS NULL AND s.status != 'archived'
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
                row.get::<_, String>(8)?,
                row.get::<_, Option<i64>>(9)?,
                row.get::<_, Option<String>>(10)?,
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
                metadata_json,
                forked_from_seq,
                forked_from_session,
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
                access_mode: access_mode_from_metadata(&metadata_json),
                forked_from_seq: forked_from_seq.and_then(|seq| u64::try_from(seq).ok()),
                forked_from_session: forked_from_session
                    .map(|value| {
                        Uuid::parse_str(&value)
                            .map(SessionId::from)
                            .map_err(|error| StoreError::InvalidData(error.to_string()))
                    })
                    .transpose()?,
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
        self.remember_memory_scoped(
            workspace_id,
            content,
            tags,
            pinned,
            "project",
            "agent",
            None,
            None,
            None,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn remember_memory_scoped(
        &self,
        workspace_id: WorkspaceId,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
        scope: &str,
        author: &str,
        origin_turn: Option<u32>,
        origin_task: Option<&str>,
        supersedes: Option<&str>,
        expires_at: Option<i64>,
    ) -> Result<MemoryRecord, StoreError> {
        self.write(|reply| WriteCommand::RememberMemory {
            workspace_id,
            content: content.to_owned(),
            tags,
            pinned,
            scope: scope.to_owned(),
            author: author.to_owned(),
            origin_turn,
            origin_task: origin_task.map(str::to_owned),
            supersedes: supersedes.map(str::to_owned),
            expires_at,
            reply,
        })
    }

    pub fn update_memory(
        &self,
        memory_id: &str,
        content: Option<&str>,
        tags: Option<Vec<String>>,
        pinned: Option<bool>,
        scope: Option<&str>,
    ) -> Result<MemoryRecord, StoreError> {
        self.write(|reply| WriteCommand::UpdateMemory {
            memory_id: memory_id.to_owned(),
            content: content.map(str::to_owned),
            tags,
            pinned,
            scope: scope.map(str::to_owned),
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
        let query = query.map(str::trim).filter(|query| !query.is_empty());
        let terms = query.map(fts_terms).filter(|terms| !terms.is_empty());
        if let Some(terms) = terms {
            let mut statement = connection.prepare(
                "SELECT m.id, m.content, m.tags_json, m.pinned, m.created_at, m.updated_at,
                        m.access_count, m.origin_turn, m.origin_task, m.author, m.scope,
                        m.supersedes, m.expires_at
                 FROM memory_fts
                 JOIN memories m ON m.id = memory_fts.id
                 WHERE memory_fts.workspace_id = ?1
                   AND memory_fts MATCH ?2
                   AND m.forgotten_at IS NULL
                   AND (m.expires_at IS NULL OR m.expires_at > ?3)
                 ORDER BY m.pinned DESC, memory_fts.rank
                 LIMIT ?4",
            )?;
            let rows = statement.query_map(
                params![
                    workspace_id.to_string(),
                    terms,
                    now_millis(),
                    i64::from(limit)
                ],
                memory_record_from_row,
            )?;
            return rows
                .collect::<Result<Vec<_>, _>>()
                .map_err(StoreError::from);
        }

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
            "SELECT id, content, tags_json, pinned, created_at, updated_at, access_count,
                    origin_turn, origin_task, author, scope, supersedes, expires_at
             FROM memories
             WHERE workspace_id = ?1
               AND forgotten_at IS NULL
               AND (expires_at IS NULL OR expires_at > ?4)
               AND (?2 = 0
                    OR lower(content) LIKE lower(?3) ESCAPE '\\'
                    OR lower(tags_json) LIKE lower(?3) ESCAPE '\\')
             ORDER BY pinned DESC, updated_at DESC
             LIMIT ?5",
        )?;
        let rows = statement.query_map(
            params![
                workspace_id.to_string(),
                i64::from(query.is_some()),
                pattern,
                now_millis(),
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

    pub fn list_archived_sessions(&self, limit: u32) -> Result<Vec<SessionSummary>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT s.id, s.workspace_id, w.path, s.title, s.status, s.created_at,
                    s.updated_at,
                    COALESCE((SELECT MAX(t.turn) FROM session_turns t
                              WHERE t.session_id = s.id), s.last_turn, 0),
                    s.metadata_json, s.forked_from_seq, s.forked_from_session
             FROM sessions s
             LEFT JOIN workspaces w ON w.id = s.workspace_id
             WHERE s.deleted_at IS NULL AND s.status = 'archived'
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
                row.get::<_, String>(8)?,
                row.get::<_, Option<i64>>(9)?,
                row.get::<_, Option<String>>(10)?,
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
                metadata_json,
                forked_from_seq,
                forked_from_session,
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
                access_mode: access_mode_from_metadata(&metadata_json),
                forked_from_seq: forked_from_seq.and_then(|seq| u64::try_from(seq).ok()),
                forked_from_session: forked_from_session
                    .map(|value| {
                        Uuid::parse_str(&value)
                            .map(SessionId::from)
                            .map_err(|error| StoreError::InvalidData(error.to_string()))
                    })
                    .transpose()?,
            });
        }
        Ok(sessions)
    }

    pub fn set_session_archived(
        &self,
        session_id: SessionId,
        archived: bool,
    ) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::SetSessionArchived {
            session_id,
            archived,
            reply,
        })
    }

    pub fn delete_session(&self, session_id: SessionId) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteSession { session_id, reply })
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
                              WHERE t.session_id = s.id), s.last_turn, 0),
                    s.metadata_json, s.forked_from_seq, s.forked_from_session
             FROM sessions s
             LEFT JOIN workspaces w ON w.id = s.workspace_id
             WHERE s.deleted_at IS NULL
               AND s.status != 'archived'
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
                row.get::<_, String>(8)?,
                row.get::<_, Option<i64>>(9)?,
                row.get::<_, Option<String>>(10)?,
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
                metadata_json,
                forked_from_seq,
                forked_from_session,
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
                access_mode: access_mode_from_metadata(&metadata_json),
                forked_from_seq: forked_from_seq.and_then(|seq| u64::try_from(seq).ok()),
                forked_from_session: forked_from_session
                    .map(|value| {
                        Uuid::parse_str(&value)
                            .map(SessionId::from)
                            .map_err(|error| StoreError::InvalidData(error.to_string()))
                    })
                    .transpose()?,
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

    /// Load the messages a model request should replay.
    ///
    /// The newest checkpoint always replaces a prefix of the surface, so it is
    /// moved ahead of the retained tail. Everything else stays in log order.
    pub fn load_surface_messages(
        &self,
        session_id: SessionId,
    ) -> Result<Vec<SessionMessage>, StoreError> {
        let connection = self.reader()?;
        let mut messages = projections::load_surface_messages(&connection, session_id)?;
        if let Some(index) = messages
            .iter()
            .rposition(|message| message.source.as_deref() == Some("compaction"))
        {
            let checkpoint = messages.remove(index);
            let insert_at = usize::from(messages.first().is_some_and(|m| m.role == "system"));
            messages.insert(insert_at, checkpoint);
        }
        Ok(messages)
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

    pub fn search_session_events(
        &self,
        workspace_id: WorkspaceId,
        query: &str,
        limit: u32,
    ) -> Result<Vec<SessionSearchHit>, StoreError> {
        let terms = fts_terms(query);
        if terms.is_empty() {
            return Ok(Vec::new());
        }
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT e.session_id, s.title, e.seq, e.kind,
                    snippet(session_events_fts, 0, '[', ']', '…', 12), e.created_at
             FROM session_events_fts
             JOIN session_events e ON e.rowid = session_events_fts.rowid
             JOIN sessions s ON s.id = e.session_id
             WHERE session_events_fts MATCH ?1
               AND s.workspace_id = ?2
               AND s.deleted_at IS NULL
             ORDER BY session_events_fts.rank
             LIMIT ?3",
        )?;
        let rows = statement.query_map(
            params![
                terms,
                workspace_id.to_string(),
                i64::from(limit.clamp(1, 100))
            ],
            |row| {
                Ok(SessionSearchHit {
                    session_id: row.get(0)?,
                    session_title: row.get(1)?,
                    seq: row.get::<_, i64>(2)?.max(0) as u64,
                    kind: row.get(3)?,
                    snippet: row.get(4)?,
                    created_at: row.get(5)?,
                })
            },
        )?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
    }

    pub fn read_session_events(
        &self,
        workspace_id: WorkspaceId,
        session_id: &str,
        from_seq: Option<u64>,
        to_seq: Option<u64>,
    ) -> Result<Vec<SessionEventRecord>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT e.seq, e.turn, e.step, e.kind, e.payload_json, e.created_at
             FROM session_events e
             JOIN sessions s ON s.id = e.session_id
             WHERE s.id = ?1
               AND s.workspace_id = ?2
               AND s.deleted_at IS NULL
               AND e.seq >= ?3
               AND e.seq <= ?4
             ORDER BY e.seq
             LIMIT 500",
        )?;
        let rows = statement.query_map(
            params![
                session_id,
                workspace_id.to_string(),
                from_seq.unwrap_or(0) as i64,
                to_seq.unwrap_or(i64::MAX as u64) as i64
            ],
            |row| {
                let payload: String = row.get(4)?;
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, Option<i64>>(1)?,
                    row.get::<_, Option<i64>>(2)?,
                    row.get::<_, String>(3)?,
                    payload,
                    row.get::<_, i64>(5)?,
                ))
            },
        )?;
        let mut events = Vec::new();
        for row in rows {
            let (seq, turn, step, kind, payload, created_at) = row?;
            events.push(SessionEventRecord {
                seq: seq.max(0) as u64,
                turn: turn.map(|value| value as u32),
                step: step.map(|value| value as u32),
                kind,
                payload: serde_json::from_str(&payload)?,
                created_at,
            });
        }
        Ok(events)
    }

    pub fn create_subagent(&self, record: SubagentRecord) -> Result<SubagentRecord, StoreError> {
        self.write(|reply| WriteCommand::CreateSubagent { record, reply })
    }

    pub fn update_subagent(
        &self,
        id: &str,
        update: SubagentUpdate,
    ) -> Result<SubagentRecord, StoreError> {
        self.write(|reply| WriteCommand::UpdateSubagent {
            id: id.to_owned(),
            update,
            reply,
        })
    }

    pub fn delete_finished_subagent(&self, id: &str) -> Result<(), StoreError> {
        self.write(|reply| WriteCommand::DeleteFinishedSubagent {
            id: id.to_owned(),
            reply,
        })
    }

    pub fn prune_finished_subagents(
        &self,
        workspace_id: WorkspaceId,
        older_than: i64,
    ) -> Result<u64, StoreError> {
        self.write(|reply| WriteCommand::PruneFinishedSubagents {
            workspace_id,
            older_than,
            reply,
        })
    }

    pub fn get_subagent(&self, id: &str) -> Result<Option<SubagentRecord>, StoreError> {
        let connection = self.reader()?;
        connection
            .query_row(
                "SELECT id, workspace_id, parent_session_id, title, task, model, status, log,
                        result, error, steps, created_at, updated_at, finished_at
                 FROM subagents WHERE id = ?1",
                [id],
                subagent_from_row,
            )
            .optional()
            .map_err(StoreError::from)
    }

    pub fn list_subagents(
        &self,
        workspace_id: WorkspaceId,
        limit: u32,
    ) -> Result<Vec<SubagentRecord>, StoreError> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT id, workspace_id, parent_session_id, title, task, model, status, log,
                    result, error, steps, created_at, updated_at, finished_at
             FROM subagents
             WHERE workspace_id = ?1
             ORDER BY created_at DESC
             LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![workspace_id.to_string(), i64::from(limit.clamp(1, 200))],
            subagent_from_row,
        )?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StoreError::from)
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

fn subagent_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SubagentRecord> {
    let workspace: String = row.get(1)?;
    Ok(SubagentRecord {
        id: row.get(0)?,
        workspace_id: WorkspaceId::from(
            Uuid::parse_str(&workspace).unwrap_or_else(|_| Uuid::nil()),
        ),
        parent_session_id: row.get(2)?,
        title: row.get(3)?,
        task: row.get(4)?,
        model: row.get(5)?,
        status: row.get(6)?,
        log: row.get(7)?,
        result: row.get(8)?,
        error: row.get(9)?,
        steps: row.get::<_, i64>(10)?.max(0) as u32,
        created_at: row.get(11)?,
        updated_at: row.get(12)?,
        finished_at: row.get(13)?,
    })
}

fn create_subagent(
    connection: &Connection,
    record: SubagentRecord,
) -> Result<SubagentRecord, StoreError> {
    connection.execute(
        "INSERT INTO subagents
            (id, workspace_id, parent_session_id, title, task, model, status, log, result,
             error, steps, created_at, updated_at, finished_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            record.id,
            record.workspace_id.to_string(),
            record.parent_session_id,
            record.title,
            record.task,
            record.model,
            record.status,
            record.log,
            record.result,
            record.error,
            i64::from(record.steps),
            record.created_at,
            record.updated_at,
            record.finished_at,
        ],
    )?;
    connection
        .query_row(
            "SELECT id, workspace_id, parent_session_id, title, task, model, status, log,
                    result, error, steps, created_at, updated_at, finished_at
             FROM subagents WHERE id = ?1",
            [&record.id],
            subagent_from_row,
        )
        .map_err(StoreError::from)
}

fn update_subagent(
    connection: &Connection,
    id: &str,
    update: SubagentUpdate,
) -> Result<SubagentRecord, StoreError> {
    let now = now_millis();
    let status = update.status;
    let terminal = status
        .as_deref()
        .is_some_and(|status| matches!(status, "completed" | "failed" | "cancelled"));
    connection.execute(
        "UPDATE subagents SET
            status = COALESCE(?2, status),
            log = CASE WHEN ?3 IS NULL THEN log
                       ELSE substr(log || ?3, -32000) END,
            result = COALESCE(?4, result),
            error = COALESCE(?5, error),
            steps = COALESCE(?6, steps),
            updated_at = ?7,
            finished_at = CASE WHEN ?8 = 1 THEN ?7 ELSE finished_at END
         WHERE id = ?1",
        params![
            id,
            status,
            update.append_log,
            update.result,
            update.error,
            update.steps.map(i64::from),
            now,
            i64::from(terminal),
        ],
    )?;
    connection
        .query_row(
            "SELECT id, workspace_id, parent_session_id, title, task, model, status, log,
                    result, error, steps, created_at, updated_at, finished_at
             FROM subagents WHERE id = ?1",
            [id],
            subagent_from_row,
        )
        .map_err(StoreError::from)
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
        confidence: row.get(10)?,
        source_hash: row.get(11)?,
        supersedes: row.get(12)?,
        review_status: row.get(13)?,
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
    // Reference folders are part of the project by definition. Grant read
    // access to the assistant in the same transaction so it can inspect them
    // without a second manual approval step.
    connection.execute(
        "INSERT OR IGNORE INTO outside_access_grants
             (id, workspace_id, path, write_access, reason, created_at)
         VALUES (?1, ?2, ?3, 0, ?4, ?5)",
        params![
            Uuid::now_v7().to_string(),
            workspace_id.to_string(),
            path,
            REFERENCE_GRANT_REASON,
            now_millis()
        ],
    )?;
    Ok(repo)
}

#[allow(clippy::too_many_arguments)]
fn add_knowledge_document(
    connection: &Connection,
    workspace_id: WorkspaceId,
    title: &str,
    kind: &str,
    source: Option<&str>,
    tags: Vec<String>,
    content: &str,
    confidence: &str,
    source_hash: Option<&str>,
    supersedes: Option<&str>,
    review_status: &str,
) -> Result<KnowledgeDocumentSummary, StoreError> {
    if !matches!(
        review_status,
        "proposed" | "accepted" | "rejected" | "superseded"
    ) {
        return Err(StoreError::InvalidData(format!(
            "review_status must be proposed, accepted, rejected, or superseded: {review_status}"
        )));
    }
    if content.trim().is_empty() {
        return Err(StoreError::InvalidData(
            "knowledge content cannot be empty".to_owned(),
        ));
    }
    let hash = blake3::hash(content.as_bytes()).to_hex().to_string();
    if let Some(previous) = supersedes {
        let valid: Option<String> = connection
            .query_row(
                "SELECT id FROM knowledge_documents WHERE id = ?1 AND workspace_id = ?2",
                params![previous, workspace_id.to_string()],
                |row| row.get(0),
            )
            .optional()?;
        if valid.is_none() {
            return Err(StoreError::InvalidData(format!(
                "superseded knowledge document not found: {previous}"
            )));
        }
    }
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
                        d.created_at, d.updated_at, d.confidence, d.source_hash, d.supersedes, d.review_status
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
            (id, workspace_id, title, kind, source, tags_json, content, content_hash,
             confidence, source_hash, supersedes, review_status, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?13)",
        params![
            document_id,
            workspace_id.to_string(),
            title,
            kind,
            source,
            tags_json,
            content,
            hash,
            confidence,
            source_hash,
            supersedes,
            review_status,
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
    if let Some(previous) = supersedes {
        transaction.execute(
            "UPDATE knowledge_documents
             SET review_status = 'superseded', updated_at = ?1
             WHERE id = ?2",
            params![now, previous],
        )?;
    }
    transaction.commit()?;

    connection
        .query_row(
            "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                length(d.content),
                (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                d.created_at, d.updated_at, d.confidence, d.source_hash, d.supersedes, d.review_status
         FROM knowledge_documents d WHERE d.id = ?1",
            [&document_id],
            knowledge_summary_from_row,
        )
        .map_err(StoreError::from)
}

#[allow(clippy::too_many_arguments)]
fn update_knowledge_document(
    connection: &Connection,
    document_id: &str,
    title: Option<&str>,
    content: Option<&str>,
    source: Option<&str>,
    tags: Option<Vec<String>>,
    confidence: Option<&str>,
    source_hash: Option<&str>,
    supersedes: Option<&str>,
    review_status: Option<&str>,
) -> Result<KnowledgeDocumentSummary, StoreError> {
    if let Some(review_status) = review_status {
        if !matches!(
            review_status,
            "proposed" | "accepted" | "rejected" | "superseded"
        ) {
            return Err(StoreError::InvalidData(format!(
                "review_status must be proposed, accepted, rejected, or superseded: {review_status}"
            )));
        }
    }
    let existing = connection
        .query_row(
            "SELECT workspace_id, title, kind, source, content, tags_json, confidence, source_hash
             FROM knowledge_documents WHERE id = ?1",
            [document_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, Option<String>>(7)?,
                ))
            },
        )
        .optional()?
        .ok_or_else(|| StoreError::NotFound(document_id.to_owned()))?;
    let (
        workspace,
        old_title,
        kind,
        old_source,
        old_content,
        old_tags,
        old_confidence,
        old_source_hash,
    ) = existing;
    let title = title.unwrap_or(&old_title).trim();
    if title.is_empty() {
        return Err(StoreError::InvalidData(
            "knowledge title cannot be empty".to_owned(),
        ));
    }
    let content = content.unwrap_or(&old_content);
    if content.trim().is_empty() {
        return Err(StoreError::InvalidData(
            "knowledge content cannot be empty".to_owned(),
        ));
    }
    let source = source.or(old_source.as_deref());
    let tags = tags.unwrap_or_else(|| serde_json::from_str(&old_tags).unwrap_or_default());
    let tags_json = serde_json::to_string(&tags)?;
    let confidence = confidence.unwrap_or(&old_confidence);
    let source_hash = source_hash.or(old_source_hash.as_deref());
    let hash = blake3::hash(content.as_bytes()).to_hex().to_string();
    let chunks = split_knowledge_chunks(content);
    let now = now_millis();

    let transaction = connection.unchecked_transaction()?;
    transaction.execute(
        "DELETE FROM knowledge_fts WHERE document_id = ?1",
        [document_id],
    )?;
    transaction.execute(
        "DELETE FROM knowledge_chunks WHERE document_id = ?1",
        [document_id],
    )?;
    transaction.execute(
        "UPDATE knowledge_documents
         SET title = ?1, kind = ?2, source = ?3, tags_json = ?4, content = ?5,
             content_hash = ?6, confidence = ?7, source_hash = ?8,
             supersedes = COALESCE(?9, supersedes),
             review_status = COALESCE(?10, review_status),
             updated_at = ?11
         WHERE id = ?12",
        params![
            title,
            kind,
            source,
            tags_json,
            content,
            hash,
            confidence,
            source_hash,
            supersedes,
            review_status,
            now,
            document_id
        ],
    )?;
    for (seq, chunk) in chunks.iter().enumerate() {
        let chunk_id = Uuid::now_v7().to_string();
        transaction.execute(
            "INSERT INTO knowledge_chunks (id, document_id, workspace_id, seq, content)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![chunk_id, document_id, workspace, seq as i64, chunk],
        )?;
        transaction.execute(
            "INSERT INTO knowledge_fts (workspace_id, document_id, title, seq, content)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![workspace, document_id, title, seq as i64, chunk],
        )?;
    }
    transaction.commit()?;

    connection
        .query_row(
            "SELECT d.id, d.workspace_id, d.title, d.kind, d.source, d.tags_json,
                    length(d.content),
                    (SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id),
                    d.created_at, d.updated_at, d.confidence, d.source_hash, d.supersedes, d.review_status
             FROM knowledge_documents d WHERE d.id = ?1",
            [document_id],
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

#[allow(clippy::too_many_arguments)]
pub fn remember_memory(
    connection: &Connection,
    workspace_id: WorkspaceId,
    content: &str,
    tags: Vec<String>,
    pinned: bool,
    scope: &str,
    author: &str,
    origin_turn: Option<u32>,
    origin_task: Option<&str>,
    supersedes: Option<&str>,
    expires_at: Option<i64>,
) -> Result<MemoryRecord, StoreError> {
    if !matches!(scope, "session" | "project" | "global") {
        return Err(StoreError::InvalidData(format!(
            "memory scope must be session, project, or global: {scope}"
        )));
    }
    let workspace = workspace_id.to_string();
    let tags_json = serde_json::to_string(&tags)?;
    if let Some(supersedes) = supersedes {
        let valid: Option<String> = connection
            .query_row(
                "SELECT id FROM memories
                 WHERE id = ?1 AND workspace_id = ?2 AND forgotten_at IS NULL",
                params![supersedes, workspace],
                |row| row.get(0),
            )
            .optional()?;
        if valid.is_none() {
            return Err(StoreError::InvalidData(format!(
                "superseded memory not found in this project: {supersedes}"
            )));
        }
    }
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
         SET tags_json = ?1, pinned = MAX(pinned, ?2), updated_at = ?3,
             scope = ?4, author = ?5, origin_turn = COALESCE(?6, origin_turn),
             origin_task = COALESCE(?7, origin_task), expires_at = ?8
         WHERE id = ?9",
            params![
                tags_json,
                pinned,
                now,
                scope,
                author,
                origin_turn.map(i64::from),
                origin_task,
                expires_at,
                id
            ],
        )?;
        id
    } else {
        let id = Uuid::now_v7().to_string();
        connection.execute(
            "INSERT INTO memories
            (id, workspace_id, content, tags_json, pinned, created_at, updated_at,
             scope, author, origin_turn, origin_task, supersedes, expires_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                id,
                workspace,
                content,
                tags_json,
                pinned,
                now,
                scope,
                author,
                origin_turn.map(i64::from),
                origin_task,
                supersedes,
                expires_at
            ],
        )?;
        id
    };
    if let Some(supersedes) = supersedes {
        connection.execute(
            "UPDATE memories SET forgotten_at = ?1, updated_at = ?1 WHERE id = ?2",
            params![now, supersedes],
        )?;
    }
    connection
        .query_row(
            "SELECT id, content, tags_json, pinned, created_at, updated_at, access_count,
                    origin_turn, origin_task, author, scope, supersedes, expires_at
         FROM memories WHERE id = ?1",
            [id],
            memory_record_from_row,
        )
        .map_err(StoreError::from)
}

fn update_memory(
    connection: &Connection,
    memory_id: &str,
    content: Option<&str>,
    tags: Option<Vec<String>>,
    pinned: Option<bool>,
    scope: Option<&str>,
) -> Result<MemoryRecord, StoreError> {
    if let Some(scope) = scope {
        if !matches!(scope, "session" | "project" | "global") {
            return Err(StoreError::InvalidData(format!(
                "memory scope must be session, project, or global: {scope}"
            )));
        }
    }
    let tags_json = tags.map(|tags| serde_json::to_string(&tags)).transpose()?;
    let changed = connection.execute(
        "UPDATE memories
         SET content = COALESCE(?1, content),
             tags_json = COALESCE(?2, tags_json),
             pinned = COALESCE(?3, pinned),
             scope = COALESCE(?4, scope),
             updated_at = ?5
         WHERE id = ?6 AND forgotten_at IS NULL",
        params![content, tags_json, pinned, scope, now_millis(), memory_id],
    )?;
    if changed == 0 {
        return Err(StoreError::InvalidData(format!(
            "memory not found: {memory_id}"
        )));
    }
    connection
        .query_row(
            "SELECT id, content, tags_json, pinned, created_at, updated_at, access_count,
                    origin_turn, origin_task, author, scope, supersedes, expires_at
             FROM memories WHERE id = ?1",
            [memory_id],
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
        origin_turn: row
            .get::<_, Option<i64>>(7)?
            .and_then(|turn| u32::try_from(turn).ok()),
        origin_task: row.get(8)?,
        author: row.get(9)?,
        scope: row.get(10)?,
        supersedes: row.get(11)?,
        expires_at: row.get(12)?,
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
            WriteCommand::AppendEvents {
                session_id,
                drafts,
                reply,
            } => {
                let _ = reply.send(append_events(&mut connection, session_id, drafts));
            }
            WriteCommand::ImportWorkspaceState {
                workspace_id,
                state,
                reply,
            } => {
                let _ = reply.send(crate::transfer::import_workspace_state(
                    &mut connection,
                    workspace_id,
                    &state,
                ));
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
            WriteCommand::ForkSession {
                source,
                at_user_seq,
                title,
                reply,
            } => {
                let _ = reply.send(fork_session(&mut connection, source, at_user_seq, title));
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
            WriteCommand::SetSessionAccessMode {
                session_id,
                access_mode,
                reply,
            } => {
                let result = (|| {
                    let metadata_json: Option<String> = connection
                        .query_row(
                            "SELECT metadata_json FROM sessions WHERE id = ?1",
                            [session_id.to_string()],
                            |row| row.get(0),
                        )
                        .optional()?;
                    let mut metadata = metadata_json
                        .as_deref()
                        .and_then(|value| serde_json::from_str::<Value>(value).ok())
                        .filter(Value::is_object)
                        .unwrap_or_else(|| serde_json::json!({}));
                    if let Some(object) = metadata.as_object_mut() {
                        object.insert("access_mode".to_owned(), serde_json::json!(access_mode));
                    }
                    connection.execute(
                        "UPDATE sessions SET metadata_json = ?1 WHERE id = ?2",
                        params![serde_json::to_string(&metadata)?, session_id.to_string()],
                    )?;
                    Ok(())
                })();
                let _ = reply.send(result);
            }
            WriteCommand::SetSessionArchived {
                session_id,
                archived,
                reply,
            } => {
                let result = connection
                    .execute(
                        "UPDATE sessions SET status = ?1, updated_at = ?2
                         WHERE id = ?3 AND deleted_at IS NULL",
                        params![
                            if archived { "archived" } else { "active" },
                            now_millis(),
                            session_id.to_string()
                        ],
                    )
                    .map_err(StoreError::from)
                    .and_then(|changed| {
                        if changed == 0 {
                            Err(StoreError::NotFound(session_id.to_string()))
                        } else {
                            Ok(())
                        }
                    });
                let _ = reply.send(result);
            }
            WriteCommand::DeleteSession { session_id, reply } => {
                let result = connection
                    .execute(
                        "UPDATE sessions
                         SET deleted_at = ?1, status = 'deleted', updated_at = ?1
                         WHERE id = ?2 AND deleted_at IS NULL",
                        params![now_millis(), session_id.to_string()],
                    )
                    .map_err(StoreError::from)
                    .and_then(|changed| {
                        if changed == 0 {
                            Err(StoreError::NotFound(session_id.to_string()))
                        } else {
                            Ok(())
                        }
                    });
                let _ = reply.send(result);
            }
            WriteCommand::AddOutsideAccessGrant {
                workspace_id,
                path,
                write_access,
                reason,
                reply,
            } => {
                let result = (|| {
                    let now = now_millis();
                    connection.execute(
                        "INSERT INTO outside_access_grants
                             (id, workspace_id, path, write_access, reason, created_at)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                         ON CONFLICT(workspace_id, path) DO UPDATE SET
                             write_access = excluded.write_access,
                             reason = excluded.reason,
                             created_at = excluded.created_at",
                        params![
                            Uuid::now_v7().to_string(),
                            workspace_id.to_string(),
                            path,
                            i64::from(write_access),
                            reason,
                            now
                        ],
                    )?;
                    connection
                        .query_row(
                            "SELECT id, path, write_access, reason, created_at
                             FROM outside_access_grants
                             WHERE workspace_id = ?1 AND path = ?2",
                            params![workspace_id.to_string(), path],
                            |row| {
                                Ok(OutsideAccessGrant {
                                    id: row.get(0)?,
                                    workspace_id,
                                    path: row.get(1)?,
                                    write_access: row.get::<_, i64>(2)? != 0,
                                    reason: row.get(3)?,
                                    created_at: row.get(4)?,
                                })
                            },
                        )
                        .map_err(StoreError::from)
                })();
                let _ = reply.send(result);
            }
            WriteCommand::RemoveOutsideAccessGrant {
                workspace_id,
                grant_id,
                reply,
            } => {
                let result = connection
                    .execute(
                        "DELETE FROM outside_access_grants
                         WHERE workspace_id = ?1 AND id = ?2",
                        params![workspace_id.to_string(), grant_id],
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
                scope,
                author,
                origin_turn,
                origin_task,
                supersedes,
                expires_at,
                reply,
            } => {
                let _ = reply.send(remember_memory(
                    &connection,
                    workspace_id,
                    &content,
                    tags,
                    pinned,
                    &scope,
                    &author,
                    origin_turn,
                    origin_task.as_deref(),
                    supersedes.as_deref(),
                    expires_at,
                ));
            }
            WriteCommand::UpdateMemory {
                memory_id,
                content,
                tags,
                pinned,
                scope,
                reply,
            } => {
                let result = update_memory(
                    &connection,
                    &memory_id,
                    content.as_deref(),
                    tags,
                    pinned,
                    scope.as_deref(),
                );
                let _ = reply.send(result);
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
            WriteCommand::SetProjectRepoReadOnly {
                repo_id,
                read_only,
                reply,
            } => {
                let result = (|| -> Result<(), StoreError> {
                    let repository: Option<(String, String)> = connection
                        .query_row(
                            "SELECT workspace_id, path FROM project_repos WHERE id = ?1",
                            [&repo_id],
                            |row| Ok((row.get(0)?, row.get(1)?)),
                        )
                        .optional()?;
                    let Some((workspace_id, path)) = repository else {
                        return Ok(());
                    };
                    connection.execute(
                        "UPDATE project_repos SET read_only = ?1 WHERE id = ?2",
                        rusqlite::params![read_only, repo_id],
                    )?;
                    connection.execute(
                        "INSERT INTO outside_access_grants
                             (id, workspace_id, path, write_access, reason, created_at)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                         ON CONFLICT(workspace_id, path) DO UPDATE SET
                             write_access = excluded.write_access,
                             reason = excluded.reason",
                        rusqlite::params![
                            Uuid::now_v7().to_string(),
                            workspace_id,
                            path,
                            if read_only { 0 } else { 1 },
                            if read_only {
                                REFERENCE_GRANT_REASON
                            } else {
                                "reference folder (editable)"
                            },
                            now_millis()
                        ],
                    )?;
                    Ok(())
                })();
                let _ = reply.send(result);
            }
            WriteCommand::AddKnowledgeDocument {
                workspace_id,
                title,
                kind,
                source,
                tags,
                content,
                confidence,
                source_hash,
                supersedes,
                review_status,
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
                    &confidence,
                    source_hash.as_deref(),
                    supersedes.as_deref(),
                    &review_status,
                ));
            }
            WriteCommand::UpdateKnowledgeDocument {
                document_id,
                title,
                content,
                source,
                tags,
                confidence,
                source_hash,
                supersedes,
                review_status,
                reply,
            } => {
                let _ = reply.send(update_knowledge_document(
                    &connection,
                    &document_id,
                    title.as_deref(),
                    content.as_deref(),
                    source.as_deref(),
                    tags,
                    confidence.as_deref(),
                    source_hash.as_deref(),
                    supersedes.as_deref(),
                    review_status.as_deref(),
                ));
            }
            WriteCommand::SetKnowledgeKind {
                document_id,
                kind,
                reply,
            } => {
                let result = connection
                    .execute(
                        "UPDATE knowledge_documents SET kind = ?1, updated_at = ?2 WHERE id = ?3",
                        params![kind, now_millis(), document_id],
                    )
                    .map(|_| ())
                    .map_err(StoreError::from);
                let _ = reply.send(result);
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
            WriteCommand::CreateSubagent { record, reply } => {
                let result = create_subagent(&connection, record);
                let _ = reply.send(result);
            }
            WriteCommand::UpdateSubagent { id, update, reply } => {
                let result = update_subagent(&connection, &id, update);
                let _ = reply.send(result);
            }
            WriteCommand::DeleteFinishedSubagent { id, reply } => {
                let result = (|| -> Result<(), StoreError> {
                    connection.execute(
                        "DELETE FROM subagents
                         WHERE id = ?1
                           AND status IN ('completed', 'failed', 'cancelled')",
                        [&id],
                    )?;
                    Ok(())
                })();
                let _ = reply.send(result);
            }
            WriteCommand::PruneFinishedSubagents {
                workspace_id,
                older_than,
                reply,
            } => {
                let result = connection
                    .execute(
                        "DELETE FROM subagents
                         WHERE workspace_id = ?1
                           AND status IN ('completed', 'failed', 'cancelled')
                           AND updated_at < ?2",
                        params![workspace_id.to_string(), older_than],
                    )
                    .map(|count| count as u64)
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

fn access_mode_from_metadata(metadata_json: &str) -> String {
    serde_json::from_str::<Value>(metadata_json)
        .ok()
        .and_then(|value| {
            value
                .get("access_mode")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .filter(|mode| {
            matches!(
                mode.as_str(),
                "read" | "edit" | "ask-writes" | "ask-commands" | "full"
            )
        })
        .unwrap_or_else(|| "read".to_owned())
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

fn fork_session(
    connection: &mut Connection,
    source: SessionId,
    at_user_seq: u64,
    title: Option<String>,
) -> Result<SessionId, StoreError> {
    let transaction = connection.transaction()?;
    let forkable: Option<String> = transaction
        .query_row(
            "SELECT kind FROM session_events WHERE session_id = ?1 AND seq = ?2",
            params![
                source.to_string(),
                i64::try_from(at_user_seq).unwrap_or(i64::MAX)
            ],
            |row| row.get(0),
        )
        .ok();
    if forkable.as_deref() != Some(SessionEventKind::UserMessage.as_str()) {
        return Err(StoreError::Conflict(
            "a fork must start at a user message".to_owned(),
        ));
    }

    let (workspace_id, source_title): (Option<String>, Option<String>) = transaction.query_row(
        "SELECT workspace_id, title FROM sessions WHERE id = ?1",
        [source.to_string()],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let target = SessionId::new();
    let now = now_millis();
    let title = title.or_else(|| source_title.map(|title| format!("{title} (edited)")));
    transaction.execute(
        "INSERT INTO sessions
             (id, workspace_id, title, created_at, updated_at, forked_from_seq, forked_from_session)
         VALUES (?1, ?2, ?3, ?4, ?4, ?5, ?6)",
        params![
            target.to_string(),
            workspace_id,
            title,
            now,
            i64::try_from(at_user_seq).unwrap_or(i64::MAX),
            source.to_string()
        ],
    )?;

    // Copy the history in order so projections see the same stream the source
    // built, then point the copy at its origin.
    let events = projections::load_events(&transaction, Some(source))?;
    let mut last_turn = 0_u32;
    for event in events.into_iter().filter(|event| event.seq < at_user_seq) {
        last_turn = last_turn.max(event.turn.unwrap_or(0));
        let search_text = event_search_text(&event.kind, &event.payload);
        let payload_json = serde_json::to_string(&event.payload)?;
        transaction.execute(
            "INSERT INTO session_events
             (id, session_id, seq, turn, step, kind, surface, payload_json, search_text, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                Uuid::now_v7().to_string(),
                target.to_string(),
                i64::try_from(event.seq).unwrap_or(i64::MAX),
                event.turn.map(i64::from),
                event.step.map(i64::from),
                event.kind.as_str(),
                i64::from(event.surface),
                payload_json,
                search_text,
                event.created_at,
            ],
        )?;
        projections::apply(
            &transaction,
            &SessionEvent {
                id: Uuid::now_v7(),
                session_id: target,
                seq: event.seq,
                turn: event.turn,
                step: event.step,
                kind: event.kind,
                surface: event.surface,
                payload: event.payload,
                created_at: event.created_at,
            },
        )?;
    }
    transaction.execute(
        "UPDATE sessions SET last_turn = ?2 WHERE id = ?1",
        params![target.to_string(), i64::from(last_turn)],
    )?;
    transaction.commit()?;
    Ok(target)
}

fn event_search_text(kind: &SessionEventKind, payload: &Value) -> Option<String> {
    if !kind.is_surface() {
        return None;
    }
    payload
        .get("content")
        .and_then(Value::as_str)
        .or_else(|| payload.get("message").and_then(Value::as_str))
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(str::to_owned)
}

fn append_event(
    connection: &mut Connection,
    session_id: SessionId,
    draft: NewEvent,
) -> Result<SessionEvent, StoreError> {
    let transaction = connection.transaction()?;
    let event = append_event_in_transaction(&transaction, session_id, draft)?;
    transaction.commit()?;
    Ok(event)
}

fn append_events(
    connection: &mut Connection,
    session_id: SessionId,
    drafts: Vec<NewEvent>,
) -> Result<Vec<SessionEvent>, StoreError> {
    let transaction = connection.transaction()?;
    let mut events = Vec::with_capacity(drafts.len());
    for draft in drafts {
        events.push(append_event_in_transaction(
            &transaction,
            session_id,
            draft,
        )?);
    }
    transaction.commit()?;
    Ok(events)
}

fn append_event_in_transaction(
    transaction: &Transaction<'_>,
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

    let search_text = event_search_text(&event.kind, &event.payload);
    transaction.execute(
        "INSERT INTO session_events
         (id, session_id, seq, turn, step, kind, surface, payload_json, search_text, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        params![
            event.id.to_string(),
            session_id.to_string(),
            next_seq,
            turn.map(i64::from),
            step.map(i64::from),
            kind.as_str(),
            if surface { 1_i64 } else { 0_i64 },
            payload_json,
            search_text,
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

    projections::apply(transaction, &event)?;

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
            "0016_session_fork"
        );
    }

    #[test]
    fn forking_at_a_user_message_copies_only_the_earlier_history() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("fork", "/tmp/openbad-fork")
            .expect("workspace");
        let source = store
            .create_session(Some(workspace), Some("fork source"))
            .expect("session");
        let events = vec![
            NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
            NewEvent::new(SessionEventKind::UserMessage)
                .with_turn(1)
                .with_step(1)
                .with_payload(json!({"content": "first"})),
            NewEvent::new(SessionEventKind::AssistantMessage)
                .with_turn(1)
                .with_step(1)
                .with_payload(json!({"content": "first answer"})),
            NewEvent::new(SessionEventKind::TurnEnd)
                .with_turn(1)
                .with_payload(json!({"reason": "stop"})),
            NewEvent::new(SessionEventKind::TurnStart).with_turn(2),
            NewEvent::new(SessionEventKind::UserMessage)
                .with_turn(2)
                .with_step(1)
                .with_payload(json!({"content": "second"})),
        ];
        store.append_events(source, events).expect("source events");
        let second_user_seq = store
            .load_events(source)
            .expect("events")
            .into_iter()
            .find(|event| {
                event.kind == SessionEventKind::UserMessage
                    && event.payload.get("content").and_then(Value::as_str) == Some("second")
            })
            .expect("second user message")
            .seq;

        let fork = store
            .fork_session(source, second_user_seq, Some("edited".to_owned()))
            .expect("fork");

        let surface = |session| {
            store
                .load_surface_messages(session)
                .expect("surface")
                .into_iter()
                .map(|message| message.content.get("content").cloned().unwrap_or_default())
                .collect::<Vec<_>>()
        };
        assert_eq!(
            surface(fork),
            vec![json!("first"), json!("first answer")],
            "the fork keeps the history before the edited message"
        );
        assert_eq!(
            surface(source),
            vec![json!("first"), json!("first answer"), json!("second")],
            "the source is untouched"
        );
        let summary = store
            .list_sessions(10)
            .expect("sessions")
            .into_iter()
            .find(|session| session.id == fork)
            .expect("fork summary");
        assert_eq!(summary.title.as_deref(), Some("edited"));
        assert_eq!(summary.forked_from_seq, Some(second_user_seq));
        assert_eq!(summary.forked_from_session, Some(source));
    }

    #[test]
    fn forking_outside_a_user_message_is_rejected() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("fork", "/tmp/openbad-fork-invalid")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("fork source"))
            .expect("session");
        let assistant = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({"content": "answer"})),
            )
            .expect("assistant message");

        let error = store
            .fork_session(session, assistant.seq, None)
            .expect_err("assistant messages are not fork points");
        assert!(matches!(error, StoreError::Conflict(_)));
    }

    #[test]
    fn step_end_counts_toward_the_turn() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("steps", "/tmp/openbad-step-count")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("steps"))
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
                NewEvent::new(SessionEventKind::StepStart)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({"step": 1})),
            )
            .expect("step start");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::StepEnd)
                    .with_turn(1)
                    .with_step(1)
                    .with_payload(json!({"step": 1, "cancelled": false})),
            )
            .expect("step end");

        let turns = store.load_turns(session).expect("turns");
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].steps, 1);
    }

    #[test]
    fn batch_append_is_atomic_and_ordered() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("batch", "/tmp/openbad-batch")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("batch"))
            .expect("session");

        let events = store
            .append_events(
                session,
                vec![
                    NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
                    NewEvent::new(SessionEventKind::StepStart)
                        .with_turn(1)
                        .with_step(1),
                    NewEvent::new(SessionEventKind::StepEnd)
                        .with_turn(1)
                        .with_step(1),
                ],
            )
            .expect("batch append");

        assert_eq!(events.len(), 3);
        assert_eq!(events[1].seq, events[0].seq + 1);
        assert_eq!(events[2].seq, events[1].seq + 1);
        assert_eq!(store.load_turns(session).expect("turns")[0].steps, 1);
    }

    #[test]
    fn subagent_lifecycle_is_recorded() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-subagents")
            .expect("workspace");
        let now = now_millis();
        let record = SubagentRecord {
            id: "agent-1".to_owned(),
            workspace_id: workspace,
            parent_session_id: None,
            title: "Inspect parser".to_owned(),
            task: "Find the parser entry point".to_owned(),
            model: "deepseek-flash".to_owned(),
            status: "running".to_owned(),
            log: String::new(),
            result: String::new(),
            error: None,
            steps: 0,
            created_at: now,
            updated_at: now,
            finished_at: None,
        };
        store.create_subagent(record).expect("create subagent");

        let running = store
            .update_subagent(
                "agent-1",
                SubagentUpdate {
                    append_log: Some("[step 1] -> grep\n".to_owned()),
                    steps: Some(1),
                    ..SubagentUpdate::default()
                },
            )
            .expect("progress");
        assert_eq!(running.status, "running");
        assert_eq!(running.steps, 1);
        assert!(running.log.contains("grep"));
        assert!(running.finished_at.is_none());

        let finished = store
            .update_subagent(
                "agent-1",
                SubagentUpdate {
                    status: Some("completed".to_owned()),
                    result: Some("parser lives in src/parse.rs".to_owned()),
                    steps: Some(3),
                    ..SubagentUpdate::default()
                },
            )
            .expect("finish");
        assert_eq!(finished.status, "completed");
        assert_eq!(finished.result, "parser lives in src/parse.rs");
        assert!(finished.finished_at.is_some());

        let listed = store.list_subagents(workspace, 10).expect("list subagents");
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id, "agent-1");
        assert_eq!(
            store
                .get_subagent("agent-1")
                .expect("get")
                .expect("present")
                .status,
            "completed"
        );

        let pruned = store
            .prune_finished_subagents(workspace, now_millis() + 1_000)
            .expect("prune");
        assert_eq!(pruned, 1);
        assert!(store.get_subagent("agent-1").expect("get").is_none());
    }

    #[test]
    fn session_search_and_read_are_workspace_scoped() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-session-search")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("Cache design"))
            .expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_payload(json!({"content": "How should the cache prefix stay stable?"})),
            )
            .expect("user event");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_payload(json!({"content": "Keep the tool schema sorted and immutable."})),
            )
            .expect("assistant event");

        let hits = store
            .search_session_events(workspace, "cache", 10)
            .expect("search");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].session_id, session.to_string());
        assert!(hits[0].snippet.contains("cache"));

        let events = store
            .read_session_events(workspace, &session.to_string(), Some(1), Some(2))
            .expect("read");
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, "user/message");
        assert!(
            events[1].payload["content"]
                .as_str()
                .unwrap_or_default()
                .contains("tool schema")
        );

        let other = store
            .create_workspace("other", "/tmp/openbad-other")
            .expect("other workspace");
        assert!(
            store
                .read_session_events(other, &session.to_string(), None, None)
                .expect("scoped read")
                .is_empty()
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
    fn compaction_shadows_old_messages_but_keeps_the_transcript() {
        let store = Store::open_in_memory().expect("store");
        let session = store.create_session(None, None).expect("session");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(1)
                    .with_payload(json!({"content": "old request"})),
            )
            .expect("first");
        let old_answer = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_turn(1)
                    .with_payload(json!({"content": "old answer"})),
            )
            .expect("old answer");
        let retained = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(2)
                    .with_payload(json!({"content": "current request"})),
            )
            .expect("retained");
        let checkpoint = store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::CompactionSummary)
                    .with_turn(2)
                    .with_payload(json!({
                        "content": "<compacted-summary>old context</compacted-summary>",
                        "source": "compaction",
                        "shadowed_through_seq": old_answer.seq as i64,
                    })),
            )
            .expect("checkpoint");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_turn(2)
                    .with_payload(json!({"content": "current answer"})),
            )
            .expect("current answer");

        let all = store.load_messages(session).expect("transcript");
        assert_eq!(all.len(), 5, "the transcript keeps shadowed messages");
        assert_eq!(all[0].content["content"], "old request");
        assert_eq!(
            all.iter()
                .filter(|message| message.content["content"] == "old answer")
                .count(),
            1
        );

        let surface = store.load_surface_messages(session).expect("surface");
        let contents = surface
            .iter()
            .map(|message| message.content["content"].as_str().unwrap_or_default())
            .collect::<Vec<_>>();
        assert_eq!(
            contents,
            vec![
                "<compacted-summary>old context</compacted-summary>",
                "current request",
                "current answer"
            ],
            "the checkpoint leads the retained tail"
        );
        assert_eq!(surface[0].source.as_deref(), Some("compaction"));
        assert_eq!(surface[1].seq, retained.seq);
        assert!(checkpoint.seq > retained.seq);

        // A later checkpoint absorbs the previous one without disturbing the
        // retained tail it did not summarize.
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(3)
                    .with_payload(json!({"content": "next request"})),
            )
            .expect("next request");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_turn(3)
                    .with_payload(json!({"content": "next answer"})),
            )
            .expect("next answer");
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::CompactionSummary)
                    .with_turn(3)
                    .with_payload(json!({
                        "content": "<compacted-summary>newer context</compacted-summary>",
                        "source": "compaction",
                        "shadowed_through_seq": 0,
                    })),
            )
            .expect("second checkpoint");

        let surface = store.load_surface_messages(session).expect("surface");
        let contents = surface
            .iter()
            .map(|message| message.content["content"].as_str().unwrap_or_default())
            .collect::<Vec<_>>();
        assert_eq!(
            contents,
            vec![
                "<compacted-summary>newer context</compacted-summary>",
                "current request",
                "current answer",
                "next request",
                "next answer"
            ]
        );
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
    fn session_access_mode_is_sticky_and_validated() {
        let store = Store::open_in_memory().expect("store");
        let session = store.create_session(None, None).expect("session");
        assert_eq!(store.session_access_mode(session).expect("mode"), "read");

        store
            .set_session_access_mode(session, "full")
            .expect("set mode");
        assert_eq!(store.session_access_mode(session).expect("mode"), "full");
        let sessions = store.list_sessions(10).expect("sessions");
        assert_eq!(sessions[0].access_mode, "full");

        store
            .set_session_access_mode(session, "edit")
            .expect("set mode");
        assert_eq!(
            store.list_sessions(10).expect("sessions")[0].access_mode,
            "edit"
        );
        store
            .set_session_access_mode(session, "ask-writes")
            .expect("set mode");
        assert_eq!(
            store.list_sessions(10).expect("sessions")[0].access_mode,
            "ask-writes"
        );
        store
            .set_session_access_mode(session, "ask-commands")
            .expect("set mode");
        assert_eq!(
            store.list_sessions(10).expect("sessions")[0].access_mode,
            "ask-commands"
        );
        assert!(matches!(
            store.set_session_access_mode(session, "admin"),
            Err(StoreError::InvalidData(_))
        ));
    }

    #[test]
    fn sessions_archive_restore_and_delete() {
        let store = Store::open_in_memory().expect("store");
        let session = store.create_session(None, None).expect("session");
        assert_eq!(store.list_sessions(10).expect("active").len(), 1);

        store.set_session_archived(session, true).expect("archive");
        assert!(store.list_sessions(10).expect("active").is_empty());
        let archived = store.list_archived_sessions(10).expect("archived");
        assert_eq!(archived.len(), 1);
        assert_eq!(archived[0].status, "archived");

        store.set_session_archived(session, false).expect("restore");
        assert!(
            store
                .list_archived_sessions(10)
                .expect("archived")
                .is_empty()
        );
        assert_eq!(store.list_sessions(10).expect("active").len(), 1);

        store.delete_session(session).expect("delete");
        assert!(store.list_sessions(10).expect("active").is_empty());
        assert!(
            store
                .list_archived_sessions(10)
                .expect("archived")
                .is_empty()
        );
        assert!(matches!(
            store.delete_session(session),
            Err(StoreError::NotFound(_))
        ));
    }

    #[test]
    fn outside_access_grants_round_trip_and_upgrade() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", "/tmp/openbad-access")
            .expect("workspace");
        let grant = store
            .add_outside_access_grant(workspace, "/tmp/outside", false, "read docs")
            .expect("grant");
        assert!(!grant.write_access);
        assert_eq!(grant.reason, "read docs");
        assert_eq!(
            store
                .list_outside_access_grants(workspace)
                .expect("list")
                .len(),
            1
        );

        let upgraded = store
            .add_outside_access_grant(workspace, "/tmp/outside", true, "write docs")
            .expect("upgrade");
        assert_eq!(upgraded.id, grant.id);
        assert!(upgraded.write_access);
        let grants = store.list_outside_access_grants(workspace).expect("list");
        assert_eq!(grants.len(), 1);
        assert_eq!(grants[0].reason, "write docs");

        store
            .remove_outside_access_grant(workspace, &grant.id)
            .expect("remove");
        assert!(
            store
                .list_outside_access_grants(workspace)
                .expect("list")
                .is_empty()
        );
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
        assert_eq!(sessions[0].access_mode, "read");

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
    fn memory_fts_tracks_content_tags_updates_and_forgetting() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("memory", "/tmp/openbad-memory-fts")
            .expect("workspace");
        let memory = store
            .remember_memory(
                workspace,
                "Prefer conventional commits for git history.",
                vec!["style".to_owned()],
                false,
            )
            .expect("memory");
        assert_eq!(
            store
                .recall_memories(workspace, Some("commit"), 10)
                .expect("stemmed recall")
                .len(),
            1
        );
        assert_eq!(
            store
                .recall_memories(workspace, Some("style"), 10)
                .expect("tag recall")
                .len(),
            1
        );
        assert!(
            store
                .retrieve_project(workspace, "conventional", 10)
                .expect("retrieval")
                .iter()
                .any(|hit| hit.source == "memory")
        );

        store
            .update_memory(
                &memory.id,
                Some("Use squash merges for feature branches."),
                Some(vec!["git".to_owned()]),
                None,
                None,
            )
            .expect("update");
        assert!(
            store
                .recall_memories(workspace, Some("conventional"), 10)
                .expect("stale recall")
                .is_empty()
        );
        assert_eq!(
            store
                .recall_memories(workspace, Some("squash"), 10)
                .expect("updated recall")
                .len(),
            1
        );

        store.forget_memory(&memory.id).expect("forget");
        assert!(
            store
                .retrieve_project(workspace, "squash", 10)
                .expect("retrieval after forget")
                .iter()
                .all(|hit| hit.source != "memory")
        );
    }

    #[test]
    fn memory_provenance_supersedes_updates_and_expiry() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("memory", "/tmp/openbad-memory-provenance")
            .expect("workspace");
        let first = store
            .remember_memory_scoped(
                workspace,
                "Use tabs for indentation.",
                vec!["style".to_owned()],
                false,
                "session",
                "human",
                Some(3),
                Some("issue-1"),
                None,
                None,
            )
            .expect("first memory");
        assert_eq!(first.scope, "session");
        assert_eq!(first.author, "human");
        assert_eq!(first.origin_turn, Some(3));
        assert_eq!(first.origin_task.as_deref(), Some("issue-1"));

        let second = store
            .remember_memory_scoped(
                workspace,
                "Use spaces for indentation.",
                vec!["style".to_owned()],
                true,
                "project",
                "agent",
                Some(4),
                None,
                Some(&first.id),
                None,
            )
            .expect("superseding memory");
        assert_eq!(second.supersedes.as_deref(), Some(first.id.as_str()));
        let active = store.recall_memories(workspace, None, 10).expect("recall");
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].id, second.id);

        let updated = store
            .update_memory(
                &second.id,
                Some("Use spaces for indentation, never tabs."),
                Some(vec!["style".to_owned(), "formatting".to_owned()]),
                Some(false),
                Some("global"),
            )
            .expect("update memory");
        assert_eq!(updated.scope, "global");
        assert!(!updated.pinned);
        assert_eq!(updated.tags.len(), 2);

        let expired = store
            .remember_memory_scoped(
                workspace,
                "This temporary note has expired.",
                vec![],
                false,
                "session",
                "agent",
                None,
                None,
                None,
                Some(now_millis() - 1),
            )
            .expect("expired memory");
        assert!(expired.expires_at.is_some());
        let active = store
            .recall_memories(workspace, None, 10)
            .expect("recall without expired");
        assert!(active.iter().all(|memory| memory.id != expired.id));

        let missing = store.remember_memory_scoped(
            workspace,
            "Use two-space indentation.",
            vec![],
            false,
            "project",
            "agent",
            None,
            None,
            Some("missing-id"),
            None,
        );
        assert!(missing.is_err());
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
            .remember_memory(
                first,
                "Keep the parser incremental cache and persistent cache.",
                vec![],
                false,
            )
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
        assert_eq!(document.confidence, "inferred");
        assert!(document.source_hash.is_none());

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

        let updated = store
            .update_knowledge_document(
                &document.id,
                Some("Parser architecture v2"),
                Some("The parser now uses a persistent cache and snapshots."),
                None,
                Some(vec!["parser".to_owned(), "cache".to_owned()]),
            )
            .expect("update knowledge");
        assert_eq!(updated.title, "Parser architecture v2");
        assert_eq!(updated.tags.len(), 2);
        let verified = store
            .update_knowledge_document_with_confidence(
                &document.id,
                None,
                None,
                None,
                None,
                Some("verified"),
                Some("source-hash"),
            )
            .expect("confidence update");
        assert_eq!(verified.confidence, "verified");
        assert_eq!(verified.source_hash.as_deref(), Some("source-hash"));
        let old_hits = store
            .retrieve_project(first, "content hashes", 10)
            .expect("old retrieval");
        assert!(old_hits.iter().all(|hit| hit.source != "knowledge"));
        let new_hits = store
            .retrieve_project(first, "persistent cache snapshots", 10)
            .expect("new retrieval");
        assert!(new_hits.iter().any(|hit| hit.source == "knowledge"));

        let hits = store
            .retrieve_project(first, "persistent cache", 10)
            .expect("retrieval");
        assert!(
            hits.iter()
                .any(|hit| hit.source == "knowledge" && hit.title == "Parser architecture v2")
        );
        assert!(hits.iter().any(|hit| hit.source == "memory"));
        assert!(
            store
                .retrieve_project(second, "persistent cache", 10)
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
        assert!(full.content.contains("persistent cache"));

        store
            .delete_knowledge_document(&document.id)
            .expect("delete knowledge");
        assert!(
            store
                .retrieve_project(first, "persistent cache", 10)
                .expect("retrieval after delete")
                .iter()
                .all(|hit| hit.source != "knowledge")
        );
    }

    #[test]
    fn knowledge_supersession_and_review_status_control_retrieval() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("knowledge-review", "/tmp/openbad-knowledge-review")
            .expect("workspace");
        let first = store
            .add_knowledge_document(
                workspace,
                "Old decision",
                "decision",
                None,
                vec!["storage".to_owned()],
                "The parser uses only an in-memory cache.",
            )
            .expect("first");
        let second = store
            .add_knowledge_document_with_metadata(
                workspace,
                "New decision",
                "decision",
                None,
                vec!["storage".to_owned()],
                "The parser uses a persistent cache and snapshots.",
                "verified",
                None,
                Some(&first.id),
                "accepted",
            )
            .expect("second");
        assert_eq!(second.supersedes.as_deref(), Some(first.id.as_str()));
        assert_eq!(second.review_status, "accepted");
        let documents = store.list_knowledge_documents(workspace).expect("list");
        assert_eq!(documents.len(), 2);
        assert_eq!(
            documents
                .iter()
                .find(|document| document.id == first.id)
                .expect("first present")
                .review_status,
            "superseded"
        );
        let hits = store
            .retrieve_project(workspace, "cache", 10)
            .expect("retrieve");
        assert!(hits.iter().all(|hit| !hit.locator.starts_with(&first.id)));
        assert!(hits.iter().any(|hit| hit.locator.starts_with(&second.id)));

        let rejected = store
            .set_knowledge_review_status(&second.id, "rejected")
            .expect("reject");
        assert_eq!(rejected.review_status, "rejected");
        let hits = store
            .retrieve_project(workspace, "persistent cache", 10)
            .expect("retrieve rejected");
        assert!(hits.iter().all(|hit| !hit.locator.starts_with(&second.id)));
        assert!(
            store
                .set_knowledge_review_status(&second.id, "invalid")
                .is_err()
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
