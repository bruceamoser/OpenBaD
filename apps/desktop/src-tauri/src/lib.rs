use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use openbad_agent::{AgentConfig, AgentObserver, default_agent};
use openbad_core::event::{NewEvent, SessionEventKind};
use openbad_core::{SessionId, WorkspaceId};
#[cfg(feature = "keyring")]
use openbad_governance::secrets::KeychainSecretStore;
#[cfg(not(feature = "keyring"))]
use openbad_governance::secrets::MemorySecretStore;
use openbad_governance::secrets::{Secret, SecretStore};
use openbad_harness::ReasoningEffort;
use openbad_index::{IndexReport, WorkspaceIndexer};
use openbad_llm::chat::{ChatMessage, ChatRole, ToolCall};
use openbad_llm::{DeepSeekClient, FLASH_MODEL, ReqwestTransport, UsageTotals};
use openbad_store::Store;
use openbad_store::types::{
    AuditReport, DoctorReport, KnowledgeDocument, KnowledgeDocumentSummary, ModelRouteRecord,
    NewModelRoute, NewProvider, ProjectHit, ProjectOverview, ProjectRepo, ProviderRecord,
    SessionMessage, SessionSummary,
};
use openbad_tools::{
    CheckpointSink, KnowledgeDocumentRef, KnowledgeEntryRef, MemoryStore, ProjectAreas,
    ProjectRepoRef, WorkspaceSearch,
};
use openbad_workspace::WorkspaceService;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::State;
use tauri::ipc::Channel;
use uuid::Uuid;

const API_KEY_REFERENCE: &str = "openbad/deepseek/api_key";
const USER_ID_SETTING: &str = "install_user_id";

pub struct AppState {
    store: Arc<Mutex<Store>>,
    secrets: Box<dyn SecretStore>,
    cancel: Arc<AtomicBool>,
}

struct StoreSearch {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

struct WorkspaceMemory {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

impl MemoryStore for WorkspaceMemory {
    fn remember(
        &self,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
    ) -> Result<openbad_tools::MemoryEntry, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let memory = store
            .remember_memory(self.workspace_id, content, tags, pinned)
            .map_err(|error| error.to_string())?;
        Ok(memory_entry(memory))
    }

    fn remember_scoped(
        &self,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
        options: openbad_tools::MemoryWriteOptions,
    ) -> Result<openbad_tools::MemoryEntry, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let memory = store
            .remember_memory_scoped(
                self.workspace_id,
                content,
                tags,
                pinned,
                &options.scope,
                &options.author,
                options.origin_turn,
                options.origin_task.as_deref(),
                options.supersedes.as_deref(),
                options.expires_at,
            )
            .map_err(|error| error.to_string())?;
        Ok(memory_entry(memory))
    }

    fn update(
        &self,
        id: &str,
        content: Option<&str>,
        tags: Option<Vec<String>>,
        pinned: Option<bool>,
        scope: Option<&str>,
    ) -> Result<openbad_tools::MemoryEntry, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .update_memory(id, content, tags, pinned, scope)
            .map_err(|error| error.to_string())
            .map(memory_entry)
    }

    fn forget(&self, id: &str) -> Result<(), String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store.forget_memory(id).map_err(|error| error.to_string())
    }

    fn recall(
        &self,
        query: Option<&str>,
        limit: usize,
    ) -> Result<Vec<openbad_tools::MemoryEntry>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let memories = store
            .recall_memories(self.workspace_id, query, limit as u32)
            .map_err(|error| error.to_string())?;
        Ok(memories.into_iter().map(memory_entry).collect())
    }
}

struct StoreProjectAreas {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

impl ProjectAreas for StoreProjectAreas {
    fn list_repos(&self) -> Result<Vec<ProjectRepoRef>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .list_project_repos(self.workspace_id)
            .map_err(|error| error.to_string())
            .map(|repos| {
                repos
                    .into_iter()
                    .map(|repo| ProjectRepoRef {
                        id: repo.id,
                        name: repo.name,
                        path: repo.path,
                        read_only: repo.read_only,
                    })
                    .collect()
            })
    }

    fn list_knowledge(&self) -> Result<Vec<KnowledgeEntryRef>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .list_knowledge_documents(self.workspace_id)
            .map_err(|error| error.to_string())
            .map(|documents| documents.into_iter().map(knowledge_ref).collect())
    }

    fn add_knowledge(
        &self,
        title: &str,
        content: &str,
        source: Option<&str>,
        tags: Vec<String>,
        confidence: &str,
    ) -> Result<KnowledgeEntryRef, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .add_knowledge_document_with_confidence(
                self.workspace_id,
                title,
                "note",
                source,
                tags,
                content,
                confidence,
                None,
            )
            .map_err(|error| error.to_string())
            .map(knowledge_ref)
    }

    fn get_knowledge(&self, id: &str) -> Result<Option<KnowledgeDocumentRef>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .knowledge_document(id)
            .map_err(|error| error.to_string())
            .map(|document| document.map(knowledge_document_ref))
    }

    fn update_knowledge(
        &self,
        id: &str,
        title: Option<&str>,
        content: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
        confidence: Option<&str>,
    ) -> Result<KnowledgeDocumentRef, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .update_knowledge_document_with_confidence(
                id, title, content, source, tags, confidence, None,
            )
            .map_err(|error| error.to_string())?;
        store
            .knowledge_document(id)
            .map_err(|error| error.to_string())?
            .map(knowledge_document_ref)
            .ok_or_else(|| format!("knowledge document not found: {id}"))
    }

    fn delete_knowledge(&self, id: &str) -> Result<(), String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .knowledge_document(id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("knowledge document not found: {id}"))?;
        store
            .delete_knowledge_document(id)
            .map_err(|error| error.to_string())
    }

    fn retrieve(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<openbad_tools::ProjectHit>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .retrieve_project(self.workspace_id, query, limit as u32)
            .map_err(|error| error.to_string())
            .map(|hits| {
                hits.into_iter()
                    .map(|hit| openbad_tools::ProjectHit {
                        source: hit.source,
                        title: hit.title,
                        snippet: hit.snippet,
                        locator: hit.locator,
                        score: hit.score,
                    })
                    .collect()
            })
    }
}

fn memory_entry(memory: openbad_store::types::MemoryRecord) -> openbad_tools::MemoryEntry {
    openbad_tools::MemoryEntry {
        id: memory.id,
        content: memory.content,
        tags: memory.tags,
        pinned: memory.pinned,
        scope: memory.scope,
        author: memory.author,
        origin_turn: memory.origin_turn,
        origin_task: memory.origin_task,
        supersedes: memory.supersedes,
        expires_at: memory.expires_at,
    }
}

fn source_is_stale(document: &KnowledgeDocumentSummary) -> bool {
    let (Some(expected), Some(source)) =
        (document.source_hash.as_deref(), document.source.as_deref())
    else {
        return false;
    };
    match std::fs::read(source) {
        Ok(bytes) => blake3::hash(&bytes).to_hex().as_str() != expected,
        Err(_) => true,
    }
}

fn knowledge_ref(document: KnowledgeDocumentSummary) -> KnowledgeEntryRef {
    let stale = source_is_stale(&document);
    KnowledgeEntryRef {
        id: document.id,
        title: document.title,
        kind: document.kind,
        source: document.source,
        chunks: document.chunks,
        confidence: document.confidence,
        stale,
    }
}

fn knowledge_document_ref(document: KnowledgeDocument) -> KnowledgeDocumentRef {
    let stale = source_is_stale(&document.summary);
    KnowledgeDocumentRef {
        id: document.summary.id,
        title: document.summary.title,
        kind: document.summary.kind,
        source: document.summary.source,
        tags: document.summary.tags,
        content: document.content,
        chunks: document.summary.chunks,
        confidence: document.summary.confidence,
        stale,
    }
}

struct SessionCheckpoints {
    store: Arc<Mutex<Store>>,
    session_id: SessionId,
    turn: u32,
}

impl CheckpointSink for SessionCheckpoints {
    fn save(&self, path: &str, existed: bool, content: Option<&[u8]>) -> Result<String, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .save_file_checkpoint(
                self.session_id,
                Some(self.turn),
                path,
                existed,
                content.map(<[u8]>::to_vec),
            )
            .map(|checkpoint| checkpoint.id)
            .map_err(|error| error.to_string())
    }
}

impl WorkspaceSearch for StoreSearch {
    fn search(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<openbad_tools::WorkspaceSearchHit>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let hits = store
            .search_workspace(self.workspace_id, query, limit as u32)
            .map_err(|error| error.to_string())?;
        Ok(hits
            .into_iter()
            .map(|hit| openbad_tools::WorkspaceSearchHit {
                path: hit.path,
                snippet: hit.snippet,
            })
            .collect())
    }

    fn find_symbols(
        &self,
        query: &str,
        kind: Option<&str>,
        limit: usize,
    ) -> Result<Vec<openbad_tools::WorkspaceSymbolHit>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let hits = store
            .find_indexed_symbols(self.workspace_id, query, kind, limit as u32)
            .map_err(|error| error.to_string())?;
        Ok(hits
            .into_iter()
            .map(|hit| openbad_tools::WorkspaceSymbolHit {
                path: hit.path,
                name: hit.name,
                kind: hit.kind,
                line: hit.line,
                signature: hit.signature,
            })
            .collect())
    }
}

#[derive(Serialize)]
pub struct Health {
    pub status: &'static str,
    pub schema_version: String,
}

#[derive(Serialize)]
pub struct Foundation {
    pub app_version: &'static str,
    pub doctor: DoctorReport,
    pub audit: AuditReport,
    pub providers: Vec<ProviderRecord>,
    pub routes: Vec<ModelRouteRecord>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AgentEvent {
    Step {
        step: u32,
    },
    Reasoning {
        step: u32,
        text: String,
    },
    Content {
        step: u32,
        text: String,
    },
    ToolCall {
        step: u32,
        name: String,
        arguments: String,
    },
    ToolResult {
        step: u32,
        name: String,
        content: String,
        is_error: bool,
        metadata: Value,
    },
    Usage {
        usage: UsageTotals,
    },
}

struct ChannelObserver {
    channel: Channel<AgentEvent>,
    cancel: Arc<AtomicBool>,
}

impl AgentObserver for ChannelObserver {
    fn on_step(&self, step: u32) {
        let _ = self.channel.send(AgentEvent::Step { step });
    }

    fn on_reasoning(&self, step: u32, text: &str) {
        let _ = self.channel.send(AgentEvent::Reasoning {
            step,
            text: text.to_owned(),
        });
    }

    fn on_content(&self, step: u32, text: &str) {
        let _ = self.channel.send(AgentEvent::Content {
            step,
            text: text.to_owned(),
        });
    }

    fn on_tool_call(&self, step: u32, call: &openbad_llm::chat::ToolCall) {
        let _ = self.channel.send(AgentEvent::ToolCall {
            step,
            name: call.function.name.clone(),
            arguments: call.function.arguments.clone(),
        });
    }

    fn on_tool_result(
        &self,
        step: u32,
        name: &str,
        content: &str,
        is_error: bool,
        metadata: &Value,
    ) {
        let _ = self.channel.send(AgentEvent::ToolResult {
            step,
            name: name.to_owned(),
            content: content.to_owned(),
            is_error,
            metadata: metadata.clone(),
        });
    }

    fn on_usage(&self, usage: &UsageTotals) {
        let _ = self.channel.send(AgentEvent::Usage { usage: *usage });
    }

    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

#[derive(Serialize)]
pub struct ChatReply {
    pub session_id: String,
    pub workspace_id: String,
    pub text: String,
    pub steps: u32,
    pub usage: UsageTotals,
    pub tool_trace: Vec<String>,
}

#[derive(Serialize)]
pub struct UiMessage {
    pub role: String,
    pub text: String,
    pub reasoning: Option<String>,
}

#[derive(Serialize)]
pub struct SessionView {
    pub session_id: String,
    pub messages: Vec<UiMessage>,
}

#[tauri::command]
fn sessions(state: State<'_, AppState>) -> Result<Vec<SessionSummary>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store.list_sessions(50).map_err(|error| error.to_string())
}

#[tauri::command]
fn checkpoint_diff(state: State<'_, AppState>, checkpoint_id: String) -> Result<String, String> {
    let checkpoint = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .load_file_checkpoint(&checkpoint_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("checkpoint not found: {checkpoint_id}"))?
    };
    let workspace_path = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .session_workspace_path(checkpoint.session_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "session has no workspace".to_owned())?
    };
    let workspace = WorkspaceService::new(workspace_path);
    let path = workspace
        .resolve(Path::new(&checkpoint.path))
        .map_err(|error| error.to_string())?;
    let before = checkpoint
        .content
        .map(String::from_utf8)
        .transpose()
        .map_err(|error| format!("checkpoint is not UTF-8: {error}"))?
        .unwrap_or_default();
    let after = std::fs::read_to_string(&path).unwrap_or_default();
    let relative = checkpoint.path.replace('\\', "/");
    let diff = similar::TextDiff::from_lines(&before, &after);
    let rendered = diff
        .unified_diff()
        .context_radius(3)
        .header(&format!("a/{relative}"), &format!("b/{relative}"))
        .to_string();
    Ok(rendered.chars().take(200_000).collect())
}

fn project_workspace(store: &Store, workspace_path: &str) -> Result<WorkspaceId, String> {
    let path = PathBuf::from(workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    store
        .ensure_workspace(workspace_name(&path), workspace_path)
        .map_err(|error| error.to_string())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectTodo {
    pub id: String,
    pub text: String,
    pub state: String,
    #[serde(default)]
    pub blocked_by: Vec<String>,
    #[serde(default)]
    pub done_when: Option<String>,
    #[serde(default)]
    pub updated_at: i64,
}

#[tauri::command]
fn project_manifest(
    workspace_path: String,
) -> Result<Option<openbad_workspace::ProjectManifest>, String> {
    let root = PathBuf::from(&workspace_path);
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    openbad_workspace::load_project_manifest(&root).map_err(|error| error.to_string())
}

#[tauri::command]
fn project_todos(workspace_path: String) -> Result<Vec<ProjectTodo>, String> {
    let path = PathBuf::from(&workspace_path)
        .join(".openbad")
        .join("todos.json");
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path).map_err(|error| error.to_string())?;
    serde_json::from_str(&content).map_err(|error| format!("invalid .openbad/todos.json: {error}"))
}

#[tauri::command]
fn project_overview(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<ProjectOverview, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .project_overview(workspace)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn project_repos(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<ProjectRepo>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .list_project_repos(workspace)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn add_project_repo(
    state: State<'_, AppState>,
    workspace_path: String,
    path: String,
    name: Option<String>,
) -> Result<ProjectRepo, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .add_project_repo(workspace, name.as_deref().unwrap_or(""), &path)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn remove_project_repo(
    state: State<'_, AppState>,
    workspace_path: String,
    repo_id: String,
) -> Result<(), String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let belongs = store
        .list_project_repos(workspace)
        .map_err(|error| error.to_string())?
        .iter()
        .any(|repo| repo.id == repo_id);
    if !belongs {
        return Err(format!("repository not found in project: {repo_id}"));
    }
    store
        .remove_project_repo(&repo_id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn knowledge_documents(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<KnowledgeDocumentSummary>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .list_knowledge_documents(workspace)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn project_questions(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<openbad_workspace::Question>, String> {
    let path = PathBuf::from(&workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .ensure_workspace(workspace_name(&path), &workspace_path)
        .map_err(|error| error.to_string())?;
    openbad_workspace::load_questions(&path).map_err(|error| error.to_string())
}

#[tauri::command]
fn answer_project_question(
    state: State<'_, AppState>,
    workspace_path: String,
    question_id: String,
    answer: String,
) -> Result<openbad_workspace::Question, String> {
    let path = PathBuf::from(&workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .ensure_workspace(workspace_name(&path), &workspace_path)
        .map_err(|error| error.to_string())?;
    openbad_workspace::answer_question(&path, &question_id, &answer)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn knowledge_stale_documents(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<String>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let documents = store
        .list_knowledge_documents(workspace)
        .map_err(|error| error.to_string())?;
    Ok(documents
        .into_iter()
        .filter(source_is_stale)
        .map(|document| document.id)
        .collect())
}

#[tauri::command]
fn knowledge_document(
    state: State<'_, AppState>,
    workspace_path: String,
    document_id: String,
) -> Result<KnowledgeDocument, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let document = store
        .knowledge_document(&document_id)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| format!("knowledge document not found: {document_id}"))?;
    if document.summary.workspace_id != workspace {
        return Err(format!("knowledge document not found: {document_id}"));
    }
    Ok(document)
}

#[tauri::command]
fn add_knowledge_note(
    state: State<'_, AppState>,
    workspace_path: String,
    title: String,
    content: String,
    tags: Vec<String>,
    confidence: Option<String>,
) -> Result<KnowledgeDocumentSummary, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .add_knowledge_document_with_confidence(
            workspace,
            &title,
            "note",
            None,
            tags,
            &content,
            confidence.as_deref().unwrap_or("inferred"),
            None,
        )
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn import_knowledge_file(
    state: State<'_, AppState>,
    workspace_path: String,
    path: String,
    tags: Vec<String>,
) -> Result<KnowledgeDocumentSummary, String> {
    const MAX_IMPORT_BYTES: u64 = 4 * 1024 * 1024;
    let source =
        std::fs::canonicalize(&path).map_err(|error| format!("knowledge file: {error}"))?;
    let metadata = std::fs::metadata(&source).map_err(|error| error.to_string())?;
    if !metadata.is_file() {
        return Err("knowledge import needs a file".to_owned());
    }
    if metadata.len() > MAX_IMPORT_BYTES {
        return Err(format!(
            "knowledge file is too large ({} bytes, maximum {MAX_IMPORT_BYTES})",
            metadata.len()
        ));
    }
    let bytes = std::fs::read(&source).map_err(|error| error.to_string())?;
    let source_hash = blake3::hash(&bytes).to_hex().to_string();
    let content = String::from_utf8(bytes)
        .map_err(|error| format!("knowledge file must be UTF-8: {error}"))?;
    let title = source
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("Imported document")
        .to_owned();
    let kind = source
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or("file")
        .to_owned();
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .add_knowledge_document_with_confidence(
            workspace,
            &title,
            &kind,
            Some(source.to_string_lossy().as_ref()),
            tags,
            &content,
            "verified",
            Some(&source_hash),
        )
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn delete_knowledge_document(
    state: State<'_, AppState>,
    document_id: String,
) -> Result<(), String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .delete_knowledge_document(&document_id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn project_files(workspace_path: String) -> Result<Vec<String>, String> {
    let root =
        std::fs::canonicalize(&workspace_path).map_err(|error| format!("workspace: {error}"))?;
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let mut files = Vec::new();
    for entry in ignore::WalkBuilder::new(&root)
        .build()
        .filter_map(Result::ok)
    {
        if files.len() >= 400 {
            break;
        }
        if entry.file_type().is_some_and(|kind| kind.is_file()) {
            files.push(
                entry
                    .path()
                    .strip_prefix(&root)
                    .unwrap_or(entry.path())
                    .to_string_lossy()
                    .into_owned(),
            );
        }
    }
    files.sort();
    Ok(files)
}

#[tauri::command]
fn search_project(
    state: State<'_, AppState>,
    workspace_path: String,
    query: String,
    limit: Option<u32>,
) -> Result<Vec<ProjectHit>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .retrieve_project(workspace, &query, limit.unwrap_or(12).clamp(1, 50))
        .map_err(|error| error.to_string())
}

#[derive(Serialize)]
pub struct ProjectFileView {
    pub path: String,
    pub kind: String,
    pub content: Option<String>,
    pub data_url: Option<String>,
}

#[tauri::command]
fn project_file(workspace_path: String, path: String) -> Result<ProjectFileView, String> {
    const MAX_PREVIEW_BYTES: u64 = 8 * 1024 * 1024;
    let workspace = WorkspaceService::new(PathBuf::from(&workspace_path));
    let resolved = workspace
        .resolve(Path::new(&path))
        .map_err(|error| error.to_string())?;
    let metadata = std::fs::metadata(&resolved).map_err(|error| error.to_string())?;
    if !metadata.is_file() {
        return Err("preview path is not a file".to_owned());
    }
    if metadata.len() > MAX_PREVIEW_BYTES {
        return Err(format!(
            "file is too large to preview ({} bytes, maximum {MAX_PREVIEW_BYTES})",
            metadata.len()
        ));
    }
    let extension = resolved
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_lowercase();
    let relative = resolved
        .strip_prefix(workspace.root())
        .unwrap_or(&resolved)
        .to_string_lossy()
        .into_owned();
    if matches!(
        extension.as_str(),
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "svg" | "bmp" | "ico"
    ) {
        let bytes = std::fs::read(&resolved).map_err(|error| error.to_string())?;
        use base64::Engine as _;
        let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
        let mime = match extension.as_str() {
            "svg" => "image/svg+xml",
            "jpg" | "jpeg" => "image/jpeg",
            "gif" => "image/gif",
            "webp" => "image/webp",
            "bmp" => "image/bmp",
            "ico" => "image/x-icon",
            _ => "image/png",
        };
        return Ok(ProjectFileView {
            path: relative,
            kind: "image".to_owned(),
            content: None,
            data_url: Some(format!("data:{mime};base64,{encoded}")),
        });
    }
    let content = std::fs::read_to_string(&resolved)
        .map_err(|error| format!("file is not UTF-8 text: {error}"))?;
    Ok(ProjectFileView {
        path: relative,
        kind: "text".to_owned(),
        content: Some(content.chars().take(400_000).collect()),
        data_url: None,
    })
}

#[tauri::command]
fn memories(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<openbad_store::types::MemoryRecord>, String> {
    let path = PathBuf::from(&workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = store
        .ensure_workspace(workspace_name(&path), &workspace_path)
        .map_err(|error| error.to_string())?;
    store
        .recall_memories(workspace, None, 20)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn forget_memory(state: State<'_, AppState>, memory_id: String) -> Result<(), String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .forget_memory(&memory_id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn checkpoints(
    state: State<'_, AppState>,
    session_id: String,
) -> Result<Vec<openbad_store::types::FileCheckpointSummary>, String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .list_file_checkpoints(session, 50)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn restore_checkpoint(state: State<'_, AppState>, checkpoint_id: String) -> Result<String, String> {
    let checkpoint = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .load_file_checkpoint(&checkpoint_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("checkpoint not found: {checkpoint_id}"))?
    };
    let workspace_path = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .session_workspace_path(checkpoint.session_id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "session has no workspace".to_owned())?
    };
    let workspace = WorkspaceService::new(workspace_path);
    let path = workspace
        .resolve(Path::new(&checkpoint.path))
        .map_err(|error| error.to_string())?;
    if checkpoint.existed {
        let content = checkpoint
            .content
            .ok_or_else(|| "checkpoint has no original content".to_owned())?;
        let content = String::from_utf8(content)
            .map_err(|error| format!("checkpoint is not UTF-8: {error}"))?;
        openbad_tools::atomic_write(&path, &content).map_err(|error| error.to_string())?;
    } else if path.exists() {
        std::fs::remove_file(&path).map_err(|error| error.to_string())?;
    }
    {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .delete_file_checkpoint(&checkpoint_id)
            .map_err(|error| error.to_string())?;
    }
    Ok(path.display().to_string())
}

#[tauri::command]
fn search_sessions(
    state: State<'_, AppState>,
    query: String,
) -> Result<Vec<SessionSummary>, String> {
    let query = query.trim();
    if query.is_empty() {
        return sessions(state);
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .search_sessions(query, 50)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn session_view(state: State<'_, AppState>, session_id: String) -> Result<SessionView, String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let history = load_history(&store, session)?;
    Ok(SessionView {
        session_id,
        messages: history
            .iter()
            .filter_map(|message| match message.role {
                ChatRole::User => Some(UiMessage {
                    role: "user".to_owned(),
                    text: message.content.clone().unwrap_or_default(),
                    reasoning: None,
                }),
                ChatRole::Assistant => Some(UiMessage {
                    role: "assistant".to_owned(),
                    text: message
                        .content
                        .clone()
                        .filter(|text| !text.is_empty())
                        .unwrap_or_else(|| "(tool activity)".to_owned()),
                    reasoning: message.reasoning_content.clone(),
                }),
                _ => None,
            })
            .collect(),
    })
}

#[tauri::command]
fn list_projects(
    state: State<'_, AppState>,
) -> Result<Vec<openbad_store::types::WorkspaceSummary>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store.list_workspaces().map_err(|error| error.to_string())
}

#[tauri::command]
fn add_project(
    state: State<'_, AppState>,
    path: String,
    name: Option<String>,
) -> Result<openbad_store::types::WorkspaceSummary, String> {
    let path = PathBuf::from(&path);
    if !path.is_dir() {
        return Err(format!("project is not a directory: {}", path.display()));
    }
    let name = name
        .map(|name| name.trim().to_owned())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| workspace_name(&path).to_owned());
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = store
        .ensure_workspace(&name, &path.to_string_lossy())
        .map_err(|error| error.to_string())?;
    store
        .workspace_summary(workspace)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "project disappeared after creation".to_owned())
}

fn default_workspaces_directory() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("OPENBAD_WORKSPACES_DIR") {
        return Ok(PathBuf::from(path));
    }
    let base = directories::BaseDirs::new()
        .ok_or_else(|| "could not resolve the home directory".to_owned())?;
    Ok(base.home_dir().join("OpenBaD").join("workspaces"))
}

fn project_slug(name: &str) -> String {
    let mut slug = String::new();
    let mut separator = false;
    for character in name.chars().flat_map(char::to_lowercase) {
        if character.is_ascii_alphanumeric() {
            if separator && !slug.is_empty() {
                slug.push('-');
            }
            slug.push(character);
            separator = false;
        } else {
            separator = true;
        }
    }
    let slug: String = slug.trim_matches('-').chars().take(48).collect();
    if slug.is_empty() {
        "project".to_owned()
    } else {
        slug
    }
}

fn create_workspace_directory(root: &Path, name: &str) -> Result<PathBuf, String> {
    std::fs::create_dir_all(root).map_err(|error| error.to_string())?;
    let slug = project_slug(name);
    for attempt in 0..1_000 {
        let folder = if attempt == 0 {
            slug.clone()
        } else {
            format!("{slug}-{}", attempt + 1)
        };
        let candidate = root.join(folder);
        match std::fs::create_dir(&candidate) {
            Ok(()) => return candidate.canonicalize().map_err(|error| error.to_string()),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if candidate.is_dir()
                    && std::fs::read_dir(&candidate)
                        .map_err(|error| error.to_string())?
                        .next()
                        .is_none()
                {
                    return candidate.canonicalize().map_err(|error| error.to_string());
                }
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Err("could not find an available workspace directory".to_owned())
}

#[tauri::command]
fn workspaces_directory() -> Result<String, String> {
    let directory = default_workspaces_directory()?;
    std::fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    directory
        .canonicalize()
        .map(|path| path.to_string_lossy().into_owned())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn create_project(
    state: State<'_, AppState>,
    name: String,
) -> Result<openbad_store::types::WorkspaceSummary, String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("project name must not be empty".to_owned());
    }
    if name.chars().count() > 80 {
        return Err("project name must be 80 characters or fewer".to_owned());
    }
    let root = default_workspaces_directory()?;
    let path = create_workspace_directory(&root, name)?;
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = store
        .ensure_workspace(name, &path.to_string_lossy())
        .map_err(|error| error.to_string())?;
    store
        .workspace_summary(workspace)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "project disappeared after creation".to_owned())
}

#[tauri::command]
fn rename_project(
    state: State<'_, AppState>,
    project_id: String,
    name: String,
) -> Result<(), String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("project name must not be empty".to_owned());
    }
    let project = WorkspaceId::from(
        Uuid::parse_str(&project_id).map_err(|error| format!("invalid project id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .rename_workspace(project, name)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn remove_project(state: State<'_, AppState>, project_id: String) -> Result<(), String> {
    let project = WorkspaceId::from(
        Uuid::parse_str(&project_id).map_err(|error| format!("invalid project id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .delete_workspace(project)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn rename_session(
    state: State<'_, AppState>,
    session_id: String,
    title: String,
) -> Result<(), String> {
    let title = title.trim();
    if title.is_empty() {
        return Err("session title must not be empty".to_owned());
    }
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .rename_session(session, title)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn workspace_index_status(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<openbad_store::types::IndexSummary, String> {
    let path = PathBuf::from(&workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let name = workspace_name(&path);
    let workspace = store
        .ensure_workspace(name, &workspace_path)
        .map_err(|error| error.to_string())?;
    let summary = store
        .workspace_index_summary(workspace)
        .map_err(|error| error.to_string())?;
    if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
        eprintln!("[index] status {workspace_path}: {summary:?}");
    }
    Ok(summary)
}

#[tauri::command]
async fn refresh_workspace_index(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<IndexReport, String> {
    let path = PathBuf::from(&workspace_path);
    if !path.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = Arc::clone(&state.store);
    tauri::async_runtime::spawn_blocking(move || {
        let store = store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let workspace = store
            .ensure_workspace(workspace_name(&path), &path.to_string_lossy())
            .map_err(|error| error.to_string())?;
        let report = WorkspaceIndexer::new(&store, workspace, &path)
            .refresh()
            .map_err(|error| error.to_string())?;
        if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
            eprintln!(
                "[index] refresh {}: seen={} added={} updated={} removed={} elapsed={}ms",
                path.display(),
                report.seen,
                report.added,
                report.updated,
                report.removed,
                report.elapsed_ms
            );
        }
        Ok(report)
    })
    .await
    .map_err(|error| format!("index task failed: {error}"))?
}

fn workspace_name(path: &Path) -> &str {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("workspace")
}

#[tauri::command]
fn health(state: State<'_, AppState>) -> Result<Health, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let schema_version = store
        .schema_version()
        .map_err(|error| format!("could not read schema version: {error}"))?;
    Ok(Health {
        status: "ok",
        schema_version,
    })
}

#[tauri::command]
fn foundation(state: State<'_, AppState>) -> Result<Foundation, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    foundation_payload(&store)
}

#[tauri::command]
fn workspace_default() -> String {
    let Ok(path) = std::env::current_dir() else {
        return String::new();
    };
    let markers = [
        ".git",
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Makefile",
        "AGENTS.md",
        ".openbad",
    ];
    if markers.iter().any(|marker| path.join(marker).exists()) {
        path.display().to_string()
    } else {
        String::new()
    }
}

#[tauri::command]
fn save_api_key(state: State<'_, AppState>, key: String) -> Result<(), String> {
    if key.trim().is_empty() {
        return Err("API key must not be empty".to_owned());
    }
    state
        .secrets
        .set(API_KEY_REFERENCE, &Secret::new(key.trim()))
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn cancel_chat(state: State<'_, AppState>) -> Result<(), String> {
    state.cancel.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
fn clear_api_key(state: State<'_, AppState>) -> Result<(), String> {
    state
        .secrets
        .delete(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn api_key_status(state: State<'_, AppState>) -> Result<bool, String> {
    let stored = state
        .secrets
        .get(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())?
        .is_some();
    Ok(stored || std::env::var_os("DEEPSEEK_API_KEY").is_some())
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatInput {
    pub session_id: Option<String>,
    pub workspace_path: String,
    pub message: String,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub max_steps: Option<u32>,
    pub model: Option<String>,
    pub thinking: Option<bool>,
    pub reasoning_effort: Option<String>,
}

#[tauri::command]
async fn chat(
    state: State<'_, AppState>,
    request: ChatInput,
    on_event: Channel<AgentEvent>,
) -> Result<ChatReply, String> {
    let ChatInput {
        session_id,
        workspace_path,
        message,
        allow_writes,
        allow_commands,
        max_steps,
        model,
        thinking,
        reasoning_effort,
    } = request;
    let message = message.trim().to_owned();
    if message.is_empty() {
        return Err("message must not be empty".to_owned());
    }
    let workspace_path = PathBuf::from(workspace_path);
    if !workspace_path.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            workspace_path.display()
        ));
    }

    let api_key = resolve_api_key(&state)?;
    let redactions = vec![api_key.clone()];
    state.cancel.store(false, Ordering::SeqCst);

    let (session, workspace_id, history, turn, indexed) = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;

        let (session, workspace_id) = match session_id {
            Some(value) => {
                let session = SessionId::from(
                    Uuid::parse_str(&value)
                        .map_err(|error| format!("invalid session id: {error}"))?,
                );
                let workspace = store
                    .session_workspace_id(session)
                    .map_err(|error| error.to_string())?
                    .map(|workspace| workspace.to_string())
                    .unwrap_or_default();
                (session, workspace)
            }
            None => {
                let name = workspace_path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("workspace");
                let workspace = store
                    .ensure_workspace(name, &workspace_path.to_string_lossy())
                    .map_err(|error| error.to_string())?;
                let title = truncate_title(&message);
                let session = store
                    .create_session(Some(workspace), Some(&title))
                    .map_err(|error| error.to_string())?;
                (session, workspace.to_string())
            }
        };

        let turn = store
            .load_turns(session)
            .map_err(|error| error.to_string())?
            .len() as u32
            + 1;
        let mut history = load_history(&store, session)?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(turn),
            )
            .map_err(|error| error.to_string())?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(turn)
                    .with_step(1)
                    .with_payload(json!({"content": message})),
            )
            .map_err(|error| error.to_string())?;
        history.push(ChatMessage::user(message.clone()));

        let user_id = ensure_user_id(&store)?;
        let indexed = workspace_id
            .parse()
            .ok()
            .and_then(|workspace: WorkspaceId| store.workspace_index_summary(workspace).ok())
            .is_some_and(|summary| summary.files > 0);
        (session, workspace_id, (history, user_id), turn, indexed)
    };

    let (history, user_id) = history;
    let base_url = std::env::var("OPENBAD_DEEPSEEK_BASE_URL")
        .unwrap_or_else(|_| "https://api.deepseek.com".to_owned());
    let mut client = DeepSeekClient::new(ReqwestTransport::new(), api_key)
        .with_model(model.unwrap_or_else(|| FLASH_MODEL.to_owned()))
        .with_base_url(base_url)
        .with_user_id(user_id);
    if let Some(thinking) = thinking {
        client = client.with_thinking(thinking);
    }
    if let Some(effort) = reasoning_effort {
        let effort = match effort.as_str() {
            "low" => ReasoningEffort::Low,
            "max" => ReasoningEffort::Max,
            _ => ReasoningEffort::High,
        };
        client = client.with_reasoning_effort(effort);
    }
    let index_root = workspace_path.clone();
    let mut agent = default_agent(
        client,
        Arc::new(WorkspaceService::new(workspace_path)),
        AgentConfig {
            allow_writes,
            allow_commands,
            max_steps: max_steps.unwrap_or(0).min(100_000),
            ..AgentConfig::default()
        },
    );
    if let Ok(workspace_id) = workspace_id.parse::<WorkspaceId>() {
        agent = agent
            .with_memory(Arc::new(WorkspaceMemory {
                store: Arc::clone(&state.store),
                workspace_id,
            }))
            .with_project(Arc::new(StoreProjectAreas {
                store: Arc::clone(&state.store),
                workspace_id,
            }));
    }
    agent = agent
        .with_redactions(redactions)
        .with_checkpoints(Arc::new(SessionCheckpoints {
            store: Arc::clone(&state.store),
            session_id: session,
            turn,
        }));
    if indexed {
        if let Ok(workspace_id) = workspace_id.parse::<WorkspaceId>() {
            agent = agent.with_search(Arc::new(StoreSearch {
                store: Arc::clone(&state.store),
                workspace_id,
            }));
        }
    }

    let observer = ChannelObserver {
        channel: on_event,
        cancel: state.cancel.clone(),
    };
    let run = match agent.run_with_observer(history, &observer).await {
        Ok(run) => run,
        Err(error) => {
            if let Ok(store) = state.store.lock() {
                let _ = store.append_event(
                    session,
                    NewEvent::new(SessionEventKind::Error)
                        .with_turn(turn)
                        .with_payload(json!({"message": error.to_string()})),
                );
                let _ = store.append_event(
                    session,
                    NewEvent::new(SessionEventKind::TurnEnd)
                        .with_turn(turn)
                        .with_payload(json!({"reason": "error"})),
                );
            }
            return Err(error.to_string());
        }
    };

    let mut tool_trace = Vec::new();
    {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;

        for message in &run.new_messages {
            match message.role {
                ChatRole::Assistant => {
                    let payload = json!({
                        "content": message.content,
                        "reasoning_content": message.reasoning_content,
                        "tool_calls": message.tool_calls,
                    });
                    store
                        .append_event(
                            session,
                            NewEvent::new(SessionEventKind::AssistantMessage)
                                .with_turn(turn)
                                .with_step(run.steps)
                                .with_payload(payload),
                        )
                        .map_err(|error| error.to_string())?;
                }
                ChatRole::Tool => {
                    let content = message.content.clone().unwrap_or_default();
                    if !content.is_empty() {
                        tool_trace.push(truncate_for_ui(&content));
                    }
                    store
                        .append_event(
                            session,
                            NewEvent::new(SessionEventKind::ToolResult)
                                .with_turn(turn)
                                .with_step(run.steps)
                                .with_payload(json!({
                                    "content": content,
                                    "tool_call_id": message.tool_call_id,
                                })),
                        )
                        .map_err(|error| error.to_string())?;
                }
                _ => {}
            }
        }

        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UsageRecorded)
                    .with_turn(turn)
                    .with_payload(json!({
                        "input_tokens": run.usage.input_tokens,
                        "cache_read_tokens": run.usage.cache_read_tokens,
                        "output_tokens": run.usage.output_tokens,
                        "cost_usd": run.usage.cost_usd,
                    })),
            )
            .map_err(|error| error.to_string())?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnEnd)
                    .with_turn(turn)
                    .with_payload(json!({"reason": "complete"})),
            )
            .map_err(|error| error.to_string())?;
    }

    let index_store = Arc::clone(&state.store);
    tauri::async_runtime::spawn_blocking(move || {
        let Ok(store) = index_store.lock() else {
            return;
        };
        let Ok(workspace) =
            store.ensure_workspace(workspace_name(&index_root), &index_root.to_string_lossy())
        else {
            return;
        };
        match WorkspaceIndexer::new(&store, workspace, &index_root).refresh() {
            Ok(report) if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() => {
                eprintln!(
                    "[index] post-turn {}: added={} updated={} removed={} elapsed={}ms",
                    index_root.display(),
                    report.added,
                    report.updated,
                    report.removed,
                    report.elapsed_ms
                );
            }
            Ok(_) => {}
            Err(error) => {
                if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
                    eprintln!("[index] post-turn refresh failed: {error}");
                }
            }
        }
    });

    Ok(ChatReply {
        session_id: session.to_string(),
        workspace_id,
        text: run.text,
        steps: run.steps,
        usage: run.usage,
        tool_trace,
    })
}

fn resolve_api_key(state: &AppState) -> Result<String, String> {
    if let Some(secret) = state
        .secrets
        .get(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())?
    {
        return Ok(secret.expose().to_owned());
    }
    std::env::var("DEEPSEEK_API_KEY").map_err(|_| {
        "No DeepSeek API key configured. Save one in Settings or set DEEPSEEK_API_KEY.".to_owned()
    })
}

fn ensure_user_id(store: &Store) -> Result<String, String> {
    if let Some(value) = store
        .get_setting(USER_ID_SETTING)
        .map_err(|error| error.to_string())?
    {
        if let Some(value) = value.as_str() {
            return Ok(value.to_owned());
        }
    }
    let value = Uuid::now_v7().to_string();
    store
        .set_setting(USER_ID_SETTING, json!(value))
        .map_err(|error| error.to_string())?;
    Ok(value)
}

fn load_history(store: &Store, session: SessionId) -> Result<Vec<ChatMessage>, String> {
    let messages = store
        .load_messages(session)
        .map_err(|error| error.to_string())?;
    messages
        .iter()
        .map(chat_message_from_session)
        .collect::<Result<Vec<_>, _>>()
}

fn chat_message_from_session(message: &SessionMessage) -> Result<ChatMessage, String> {
    let payload = &message.content;
    match message.role.as_str() {
        "user" => Ok(ChatMessage::user(
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        "system" | "developer" => Ok(ChatMessage::system(
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        "assistant" => {
            let tool_calls = payload
                .get("tool_calls")
                .filter(|value| value.is_array())
                .cloned()
                .map(serde_json::from_value::<Vec<ToolCall>>)
                .transpose()
                .map_err(|error| format!("invalid tool calls in history: {error}"))?;
            Ok(ChatMessage {
                role: ChatRole::Assistant,
                content: payload
                    .get("content")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                reasoning_content: payload
                    .get("reasoning_content")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                tool_calls,
                tool_call_id: None,
                prefix: None,
            })
        }
        "tool" => Ok(ChatMessage::tool(
            payload
                .get("tool_call_id")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        other => Err(format!("unsupported stored message role: {other}")),
    }
}

fn truncate_title(message: &str) -> String {
    let mut title: String = message.chars().take(80).collect();
    if message.chars().count() > 80 {
        title.push('…');
    }
    title
}

fn truncate_for_ui(content: &str) -> String {
    let mut preview: String = content.chars().take(400).collect();
    if content.chars().count() > 400 {
        preview.push('…');
    }
    preview
}

fn foundation_payload(store: &Store) -> Result<Foundation, String> {
    Ok(Foundation {
        app_version: env!("CARGO_PKG_VERSION"),
        doctor: store
            .doctor()
            .map_err(|error| format!("doctor failed: {error}"))?,
        audit: store
            .audit_verify()
            .map_err(|error| format!("audit verification failed: {error}"))?,
        providers: store
            .list_providers()
            .map_err(|error| format!("could not list providers: {error}"))?,
        routes: store
            .list_model_routes()
            .map_err(|error| format!("could not list model routes: {error}"))?,
    })
}

fn secret_store() -> Box<dyn SecretStore> {
    #[cfg(feature = "keyring")]
    {
        Box::new(KeychainSecretStore::new("OpenBaD Desktop"))
    }
    #[cfg(not(feature = "keyring"))]
    {
        Box::new(MemorySecretStore::new())
    }
}

fn ensure_defaults(store: &Store) -> Result<(), String> {
    if !store
        .list_providers()
        .map_err(|error| format!("could not list providers: {error}"))?
        .is_empty()
    {
        return Ok(());
    }

    let provider = store
        .create_provider(
            NewProvider::new("deepseek", "DeepSeek")
                .with_base_url("https://api.deepseek.com")
                .with_keychain_ref(API_KEY_REFERENCE),
        )
        .map_err(|error| format!("could not create the DeepSeek provider: {error}"))?;

    store
        .create_model_route(NewModelRoute::new(
            "chat-default",
            "chat",
            provider.id,
            "deepseek-flash",
        ))
        .map_err(|error| format!("could not create the default chat route: {error}"))?;
    store
        .create_model_route(NewModelRoute::new(
            "reasoning-default",
            "reasoning",
            provider.id,
            "deepseek-v4-pro",
        ))
        .map_err(|error| format!("could not create the default reasoning route: {error}"))?;

    Ok(())
}

pub fn run() {
    let store = Store::open_default().expect("failed to open the OpenBaD store");
    ensure_defaults(&store).expect("failed to seed default providers");

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            store: Arc::new(Mutex::new(store)),
            secrets: secret_store(),
            cancel: Arc::new(AtomicBool::new(false)),
        })
        .invoke_handler(tauri::generate_handler![
            health,
            foundation,
            list_projects,
            add_project,
            create_project,
            workspaces_directory,
            rename_project,
            remove_project,
            rename_session,
            workspace_index_status,
            refresh_workspace_index,
            workspace_default,
            save_api_key,
            clear_api_key,
            cancel_chat,
            api_key_status,
            sessions,
            search_sessions,
            memories,
            project_overview,
            project_manifest,
            project_todos,
            project_questions,
            answer_project_question,
            project_repos,
            add_project_repo,
            remove_project_repo,
            knowledge_documents,
            knowledge_stale_documents,
            knowledge_document,
            add_knowledge_note,
            import_knowledge_file,
            delete_knowledge_document,
            search_project,
            project_files,
            project_file,
            forget_memory,
            checkpoints,
            checkpoint_diff,
            restore_checkpoint,
            session_view,
            chat
        ])
        .run(tauri::generate_context!())
        .expect("error while running OpenBaD Desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_files_lists_live_workspace_entries() {
        let root = tempfile::tempdir().expect("tempdir");
        std::fs::create_dir_all(root.path().join("src")).expect("src");
        std::fs::write(root.path().join("README.md"), "readme").expect("readme");
        std::fs::write(root.path().join("src/main.rs"), "fn main() {}").expect("main");
        let files = project_files(root.path().to_string_lossy().into_owned()).expect("files");
        assert!(files.contains(&"README.md".to_owned()));
        assert!(files.contains(&"src/main.rs".to_owned()));
    }

    #[test]
    fn project_slugs_are_safe_and_readable() {
        assert_eq!(project_slug("My Project"), "my-project");
        assert_eq!(project_slug("  API_v2 / Desktop  "), "api-v2-desktop");
        assert_eq!(project_slug("***"), "project");
        assert_eq!(project_slug("Café Notes"), "caf-notes");
    }

    #[test]
    fn workspace_directories_are_unique_and_not_overwritten() {
        let root = tempfile::tempdir().expect("tempdir");
        let first = create_workspace_directory(root.path(), "Demo").expect("first");
        assert_eq!(first, root.path().join("demo"));
        std::fs::write(first.join("keep.txt"), "keep").expect("file");
        let second = create_workspace_directory(root.path(), "Demo").expect("second");
        assert_eq!(second, root.path().join("demo-2"));
        assert!(first.join("keep.txt").is_file());
    }

    #[test]
    fn foundation_reports_seeded_defaults() {
        let store = Store::open_in_memory().expect("store");
        ensure_defaults(&store).expect("defaults");

        let payload = foundation_payload(&store).expect("foundation");
        assert_eq!(payload.doctor.schema_version, "0008_memory_provenance");
        assert_eq!(payload.doctor.integrity, "ok");
        assert!(payload.audit.valid);
        assert_eq!(payload.providers.len(), 1);
        assert_eq!(payload.providers[0].kind, "deepseek");
        assert_eq!(payload.routes.len(), 2);
        assert!(
            payload
                .routes
                .iter()
                .any(|route| route.model == "deepseek-flash")
        );
        assert!(
            payload
                .routes
                .iter()
                .any(|route| route.model == "deepseek-v4-pro")
        );
    }

    #[test]
    fn defaults_are_idempotent() {
        let store = Store::open_in_memory().expect("store");
        ensure_defaults(&store).expect("first");
        ensure_defaults(&store).expect("second");
        assert_eq!(store.list_providers().expect("providers").len(), 1);
        assert_eq!(store.list_model_routes().expect("routes").len(), 2);
    }

    #[test]
    fn chat_input_accepts_camel_case_fields() {
        let input: ChatInput = serde_json::from_value(json!({
            "sessionId": null,
            "workspacePath": "/tmp/work",
            "message": "hello",
            "allowWrites": true,
            "allowCommands": false,
            "maxSteps": 32,
            "model": "deepseek-v4-pro",
            "thinking": true,
            "reasoningEffort": "max"
        }))
        .expect("input");
        assert_eq!(input.workspace_path, "/tmp/work");
        assert!(input.allow_writes);
        assert_eq!(input.max_steps, Some(32));
        assert_eq!(input.model.as_deref(), Some("deepseek-v4-pro"));
        assert_eq!(input.reasoning_effort.as_deref(), Some("max"));
    }

    #[test]
    fn stored_history_round_trips_assistant_and_tool_messages() {
        let message = ChatMessage {
            role: ChatRole::Assistant,
            content: None,
            reasoning_content: Some("reasoning".to_owned()),
            tool_calls: Some(vec![ToolCall::function(
                "call-1",
                "read_file",
                json!({"path": "README.md"}),
            )]),
            tool_call_id: None,
            prefix: None,
        };
        let session_message = SessionMessage {
            session_id: SessionId::new(),
            seq: 1,
            turn: Some(1),
            step: Some(1),
            role: "assistant".to_owned(),
            content: json!({
                "content": message.content,
                "reasoning_content": message.reasoning_content,
                "tool_calls": message.tool_calls,
            }),
            source: None,
            created_at: 0,
        };

        let restored = chat_message_from_session(&session_message).expect("restore");
        assert_eq!(restored.role, ChatRole::Assistant);
        assert_eq!(restored.reasoning_content.as_deref(), Some("reasoning"));
        assert_eq!(
            restored.tool_calls.expect("tool calls")[0].function.name,
            "read_file"
        );
    }
}
