mod clipboard;
mod git;
mod preview;
mod process;
mod services;

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use openbad_agent::{
    AgentConfig, AgentError, AgentObserver, ApprovalGate, AttemptFailure, CompactionRecord, Inbox,
    StepOutcome, StepSettlement, ToolStatus, default_agent,
};
use openbad_core::event::{NewEvent, SessionEventKind};
use openbad_core::{SessionId, WorkspaceId};
#[cfg(feature = "keyring")]
use openbad_governance::secrets::KeychainSecretStore;
#[cfg(not(feature = "keyring"))]
use openbad_governance::secrets::MemorySecretStore;
use openbad_governance::secrets::{Secret, SecretStore};
use openbad_governance::{ApprovalDecision, ApprovalRequest, ToolPolicy};
use openbad_harness::{ReasoningEffort, RequestEnvelope};
use openbad_index::{IndexReport, WorkspaceIndexer};
use openbad_llm::chat::{ChatMessage, ChatRole, ToolCall};
use openbad_llm::{DeepSeekClient, FLASH_MODEL, ReqwestTransport, UsageTotals};
use openbad_store::Store;
use openbad_store::types::{
    AuditReport, DoctorReport, KnowledgeDocument, KnowledgeDocumentSummary, ModelRouteRecord,
    NewModelRoute, NewProvider, ProjectHit, ProjectOverview, ProjectRepo, ProviderRecord,
    SessionMessage, SessionSummary, SubagentRecord, SubagentUpdate,
};
use openbad_tools::{
    CheckpointSink, KnowledgeDocumentRef, KnowledgeEntryRef, LspLocation as ToolLspLocation,
    LspService, McpServerChange, McpServerSpec, McpServerSummary, McpService, McpToolSummary,
    MemoryStore, OutsideGrant, ProjectAreas, ProjectRepoRef, SessionEventView, SessionHistory,
    SessionHistoryHit, SubagentInfo, SubagentRuntime, SubagentSpec, TerminalRead, TerminalService,
    WorkspaceSearch,
};
use openbad_workspace::WorkspaceService;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::State;
use tauri::ipc::Channel;
use uuid::Uuid;

const API_KEY_REFERENCE: &str = "openbad/deepseek/api_key";
const USER_ID_SETTING: &str = "install_user_id";
const DEFAULT_SUBAGENT_RETENTION_SECONDS: i64 = 60;

/// Runs a blocking body on the Tauri blocking pool. Commands that are not
/// `async` execute on the main thread, so anything that touches the
/// filesystem, a subprocess, or SQLite in bulk belongs here.
pub(crate) async fn off_thread<F, T>(work: F) -> Result<T, String>
where
    F: FnOnce() -> Result<T, String> + Send + 'static,
    T: Send + 'static,
{
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|error| error.to_string())?
}

pub fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or_default()
}

fn subagent_retention_ms() -> i64 {
    std::env::var("OPENBAD_SUBAGENT_RETENTION_SECONDS")
        .ok()
        .and_then(|value| value.parse::<i64>().ok())
        .filter(|seconds| *seconds >= 0)
        .unwrap_or(DEFAULT_SUBAGENT_RETENTION_SECONDS)
        * 1_000
}

pub struct AppState {
    store: Arc<Mutex<Store>>,
    secrets: Box<dyn SecretStore>,
    /// One cancellation token and inbox per UI-generated run id. The
    /// registry also enforces one running turn per session.
    runs: Arc<Mutex<RunRegistry>>,
    /// Standing approvals granted for a session or workspace.
    approvals: Arc<Mutex<ApprovalCache>>,
    /// In-flight approval prompts, keyed by request id.
    pending_approvals: Arc<Mutex<HashMap<Uuid, std::sync::mpsc::Sender<ApprovalDecision>>>>,
    subagent_cancels: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    terminal: Arc<openbad_terminal::TerminalManager>,
    mcp: McpManagers,
}

#[derive(Default)]
struct ApprovalCache {
    sessions: HashMap<(SessionId, String), ApprovalDecision>,
    workspaces: HashMap<(String, String), ApprovalDecision>,
}

#[derive(Default)]
struct RunRegistry {
    runs: HashMap<String, RunEntry>,
    /// Runs cancelled before their `chat` invocation registered the token. This
    /// closes the stop-immediately race without leaking a global cancel flag.
    cancelled: std::collections::HashSet<String>,
}

struct RunEntry {
    session_id: Option<String>,
    cancel: Arc<AtomicBool>,
    inbox: Arc<Inbox>,
}

struct RunGuard {
    registry: Arc<Mutex<RunRegistry>>,
    run_id: String,
    cancel: Arc<AtomicBool>,
    inbox: Arc<Inbox>,
}

impl RunGuard {
    fn cancel_token(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.cancel)
    }

    fn inbox(&self) -> Arc<Inbox> {
        Arc::clone(&self.inbox)
    }

    /// Close the inbox as soon as the agent loop ends, before persistence work
    /// gives the UI a false "still running" window to steer into.
    fn finish_inbox(&self) {
        self.inbox.end();
    }
}

impl Drop for RunGuard {
    fn drop(&mut self) {
        self.inbox.end();
        if let Ok(mut registry) = self.registry.lock() {
            let matches = registry
                .runs
                .get(&self.run_id)
                .is_some_and(|entry| Arc::ptr_eq(&entry.cancel, &self.cancel));
            if matches {
                registry.runs.remove(&self.run_id);
            }
        }
    }
}

fn begin_run(
    registry: &Arc<Mutex<RunRegistry>>,
    run_id: &str,
    session_id: Option<&str>,
) -> Result<RunGuard, String> {
    let mut runs = registry
        .lock()
        .map_err(|error| format!("run registry lock poisoned: {error}"))?;
    if runs.runs.contains_key(run_id) {
        return Err(format!("run id is already active: {run_id}"));
    }
    if let Some(session_id) = session_id {
        if runs
            .runs
            .values()
            .any(|entry| entry.session_id.as_deref() == Some(session_id))
        {
            return Err(
                "A turn is already running for this session. Wait for it to finish or stop it."
                    .to_owned(),
            );
        }
    }
    let cancelled = runs.cancelled.remove(run_id);
    let cancel = Arc::new(AtomicBool::new(cancelled));
    let inbox = Arc::new(Inbox::default());
    inbox.begin();
    runs.runs.insert(
        run_id.to_owned(),
        RunEntry {
            session_id: session_id.map(str::to_owned),
            cancel: Arc::clone(&cancel),
            inbox: Arc::clone(&inbox),
        },
    );
    Ok(RunGuard {
        registry: Arc::clone(registry),
        run_id: run_id.to_owned(),
        cancel,
        inbox,
    })
}

fn cancel_registered_run(registry: &Arc<Mutex<RunRegistry>>, run_id: &str) -> Result<bool, String> {
    let mut registry = registry
        .lock()
        .map_err(|error| format!("run registry lock poisoned: {error}"))?;
    if let Some(entry) = registry.runs.get(run_id) {
        entry.cancel.store(true, Ordering::SeqCst);
        return Ok(true);
    }
    registry.cancelled.insert(run_id.to_owned());
    Ok(false)
}

fn steer_registered_run(
    registry: &Arc<Mutex<RunRegistry>>,
    run_id: &str,
    message: &str,
) -> Result<bool, String> {
    let inbox = {
        let registry = registry
            .lock()
            .map_err(|error| format!("run registry lock poisoned: {error}"))?;
        registry
            .runs
            .get(run_id)
            .map(|entry| Arc::clone(&entry.inbox))
    };
    let Some(inbox) = inbox else {
        return Err("no active turn to steer".to_owned());
    };
    Ok(inbox.push_steering(message.to_owned()))
}

/// Keeps one MCP manager per workspace so the settings UI, the chat loop, and
/// subagents observe the same server processes and configuration.
#[derive(Default)]
struct McpManagers {
    managers: Mutex<HashMap<PathBuf, Arc<openbad_mcp::McpManager>>>,
}

impl McpManagers {
    fn get(&self, root: &Path) -> Result<Arc<openbad_mcp::McpManager>, String> {
        let mut managers = self
            .managers
            .lock()
            .map_err(|error| format!("MCP manager lock poisoned: {error}"))?;
        if let Some(manager) = managers.get(root) {
            return Ok(Arc::clone(manager));
        }
        let manager = Arc::new(
            openbad_mcp::McpManager::new(root)
                .map_err(|error| format!("could not load MCP configuration: {error}"))?,
        );
        managers.insert(root.to_path_buf(), Arc::clone(&manager));
        Ok(manager)
    }
}

struct StoreSearch {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

struct WorkspaceMemory {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

struct WorkspaceSessions {
    store: Arc<Mutex<Store>>,
    workspace_id: WorkspaceId,
}

impl SessionHistory for WorkspaceSessions {
    fn search(&self, query: &str, limit: usize) -> Result<Vec<SessionHistoryHit>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .search_session_events(self.workspace_id, query, limit as u32)
            .map(|hits| {
                hits.into_iter()
                    .map(|hit| SessionHistoryHit {
                        session_id: hit.session_id,
                        title: hit.session_title,
                        seq: hit.seq,
                        kind: hit.kind,
                        snippet: hit.snippet,
                        created_at: hit.created_at,
                    })
                    .collect()
            })
            .map_err(|error| error.to_string())
    }

    fn read(
        &self,
        session_id: &str,
        from_seq: Option<u64>,
        to_seq: Option<u64>,
    ) -> Result<Vec<SessionEventView>, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .read_session_events(self.workspace_id, session_id, from_seq, to_seq)
            .map(|events| {
                events
                    .into_iter()
                    .map(|event| SessionEventView {
                        seq: event.seq,
                        turn: event.turn,
                        kind: event.kind,
                        content: event
                            .payload
                            .get("content")
                            .and_then(Value::as_str)
                            .or_else(|| event.payload.get("message").and_then(Value::as_str))
                            .map(str::to_owned)
                            .unwrap_or_else(|| event.payload.to_string()),
                        created_at: event.created_at,
                    })
                    .collect()
            })
            .map_err(|error| error.to_string())
    }
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
        self.add_knowledge_detailed(
            title, content, "note", source, tags, confidence, None, "accepted",
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn add_knowledge_detailed(
        &self,
        title: &str,
        content: &str,
        kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        confidence: &str,
        supersedes: Option<&str>,
        review_status: &str,
    ) -> Result<KnowledgeEntryRef, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .add_knowledge_document_with_metadata(
                self.workspace_id,
                title,
                kind,
                source,
                tags,
                content,
                confidence,
                None,
                supersedes,
                review_status,
            )
            .map_err(|error| error.to_string())
            .map(knowledge_ref)
    }

    fn review_knowledge(&self, id: &str, status: &str) -> Result<KnowledgeDocumentRef, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .set_knowledge_review_status(id, status)
            .map_err(|error| error.to_string())
            .and_then(|_| {
                store
                    .knowledge_document(id)
                    .map_err(|error| error.to_string())?
                    .map(knowledge_document_ref)
                    .ok_or_else(|| format!("knowledge document not found: {id}"))
            })
    }

    #[allow(clippy::too_many_arguments)]
    fn update_knowledge_detailed(
        &self,
        id: &str,
        title: Option<&str>,
        content: Option<&str>,
        kind: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
        confidence: Option<&str>,
        supersedes: Option<&str>,
        review_status: Option<&str>,
    ) -> Result<KnowledgeDocumentRef, String> {
        let store = self
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let existing = store
            .knowledge_document(id)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| format!("knowledge document not found: {id}"))?;
        if let Some(kind) = kind {
            store
                .set_knowledge_kind(id, kind)
                .map_err(|error| error.to_string())?;
        }
        store
            .update_knowledge_document_with_metadata(
                id,
                title,
                content,
                source,
                tags,
                confidence,
                None,
                supersedes,
                review_status,
            )
            .map_err(|error| error.to_string())?;
        let _ = existing;
        store
            .knowledge_document(id)
            .map_err(|error| error.to_string())?
            .map(knowledge_document_ref)
            .ok_or_else(|| format!("knowledge document not found: {id}"))
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
        supersedes: document.supersedes,
        review_status: document.review_status,
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
        supersedes: document.summary.supersedes,
        review_status: document.summary.review_status,
    }
}

struct DesktopLsp {
    manager: Result<Arc<openbad_lsp::LspManager>, String>,
}

impl DesktopLsp {
    fn new(root: PathBuf) -> Self {
        Self {
            manager: openbad_lsp::LspManager::new(root)
                .map(Arc::new)
                .map_err(|error| error.to_string()),
        }
    }

    fn manager(&self) -> Result<Arc<openbad_lsp::LspManager>, String> {
        self.manager.as_ref().map(Arc::clone).map_err(Clone::clone)
    }
}

fn tool_locations(locations: Vec<openbad_lsp::LspLocation>) -> Vec<ToolLspLocation> {
    locations
        .into_iter()
        .map(|location| ToolLspLocation {
            path: location.path.display().to_string(),
            line: location.line,
            character: location.character,
        })
        .collect()
}

#[async_trait::async_trait]
impl LspService for DesktopLsp {
    async fn definition(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<ToolLspLocation>, String> {
        let manager = self.manager()?;
        let path = path.to_path_buf();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .definition(&path, line, character)
                .map(tool_locations)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn references(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<ToolLspLocation>, String> {
        let manager = self.manager()?;
        let path = path.to_path_buf();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .references(&path, line, character)
                .map(tool_locations)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn implementation(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<ToolLspLocation>, String> {
        let manager = self.manager()?;
        let path = path.to_path_buf();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .implementation(&path, line, character)
                .map(tool_locations)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn hover(&self, path: &Path, line: u32, character: u32) -> Result<String, String> {
        let manager = self.manager()?;
        let path = path.to_path_buf();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .hover(&path, line, character)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }
}

struct DesktopMcp {
    manager: Arc<openbad_mcp::McpManager>,
}

impl DesktopMcp {
    fn new(manager: Arc<openbad_mcp::McpManager>) -> Self {
        Self { manager }
    }
}

fn mcp_server_summary(state: &openbad_mcp::McpServerState) -> McpServerSummary {
    McpServerSummary {
        name: state.config.name.clone(),
        transport: match state.config.transport {
            openbad_mcp::McpTransport::Stdio => "stdio".to_owned(),
            openbad_mcp::McpTransport::Http => "http".to_owned(),
        },
        command: state.config.command.clone(),
        args: state.config.args.clone(),
        url: state.config.url.clone(),
        enabled: state.enabled,
        allowed_tools: state.config.allowed_tools.clone(),
        env_keys: state.config.env.keys().cloned().collect(),
        timeout_seconds: state.config.timeout_seconds,
    }
}

fn mcp_tool_summary(tool: openbad_mcp::McpToolInfo) -> McpToolSummary {
    McpToolSummary {
        server: tool.server,
        name: tool.name,
        description: tool.description,
    }
}

fn mcp_server_config_from_spec(
    spec: &McpServerSpec,
    base: Option<openbad_mcp::McpServerConfig>,
) -> Result<openbad_mcp::McpServerConfig, String> {
    let creating = base.is_none();
    let mut config =
        base.unwrap_or_else(|| openbad_mcp::McpServerConfig::stdio(spec.name.clone(), ""));
    config.name = spec.name.clone();
    if let Some(transport) = spec.transport.as_deref() {
        config.transport = match transport.to_ascii_lowercase().as_str() {
            "stdio" => openbad_mcp::McpTransport::Stdio,
            "http" => openbad_mcp::McpTransport::Http,
            other => return Err(format!("unsupported MCP transport: {other}")),
        };
    } else if creating {
        config.transport = if spec.command.is_none() && spec.url.is_some() {
            openbad_mcp::McpTransport::Http
        } else {
            openbad_mcp::McpTransport::Stdio
        };
    }
    if let Some(command) = &spec.command {
        config.command = (!command.trim().is_empty()).then(|| command.clone());
    }
    if let Some(args) = &spec.args {
        config.args = args.clone();
    }
    if let Some(env) = &spec.env {
        config.env = env.clone();
    }
    if let Some(cwd) = &spec.cwd {
        config.cwd = (!cwd.trim().is_empty()).then(|| cwd.clone());
    }
    if let Some(url) = &spec.url {
        config.url = (!url.trim().is_empty()).then(|| url.clone());
    }
    if let Some(allowed) = &spec.allowed_tools {
        config.allowed_tools = allowed.clone();
    }
    if let Some(timeout) = spec.timeout_seconds {
        config.timeout_seconds = (timeout > 0).then_some(timeout);
    }
    config
        .validate()
        .map_err(|error| format!("invalid MCP server: {error}"))?;
    Ok(config)
}

fn probe_mcp_server(
    manager: &openbad_mcp::McpManager,
    name: &str,
) -> (Vec<McpToolSummary>, Option<String>) {
    match manager.probe(name) {
        Ok(tools) => (tools.into_iter().map(mcp_tool_summary).collect(), None),
        Err(error) => (Vec::new(), Some(error.to_string())),
    }
}

#[async_trait::async_trait]
impl McpService for DesktopMcp {
    async fn catalog(&self) -> Result<Vec<McpToolSummary>, String> {
        let manager = Arc::clone(&self.manager);
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .catalog()
                .map(|tools| tools.into_iter().map(mcp_tool_summary).collect())
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn call(&self, server: &str, tool: &str, arguments: Value) -> Result<String, String> {
        let manager = Arc::clone(&self.manager);
        let server = server.to_owned();
        let tool = tool.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .call(&server, &tool, arguments)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn servers(&self) -> Result<Vec<McpServerSummary>, String> {
        let manager = Arc::clone(&self.manager);
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .servers()
                .map(|servers| servers.iter().map(mcp_server_summary).collect())
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn add_server(&self, spec: McpServerSpec) -> Result<McpServerChange, String> {
        let manager = Arc::clone(&self.manager);
        tauri::async_runtime::spawn_blocking(move || {
            let config = mcp_server_config_from_spec(&spec, None)?;
            let state = manager
                .add_server(config, spec.enabled.unwrap_or(true))
                .map_err(|error| error.to_string())?;
            let (tools, warning) = probe_mcp_server(&manager, &state.config.name);
            Ok(McpServerChange {
                server: mcp_server_summary(&state),
                tools,
                warning,
            })
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn update_server(
        &self,
        name: &str,
        spec: McpServerSpec,
    ) -> Result<McpServerChange, String> {
        let manager = Arc::clone(&self.manager);
        let name = name.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            let current = manager
                .servers()
                .map_err(|error| error.to_string())?
                .into_iter()
                .find(|server| server.config.name == name)
                .ok_or_else(|| format!("unknown MCP server: {name}"))?;
            let config = mcp_server_config_from_spec(&spec, Some(current.config))?;
            manager
                .update_server(&name, config)
                .map_err(|error| error.to_string())?;
            let state = match spec.enabled {
                Some(enabled) => manager
                    .set_server_enabled(&name, enabled)
                    .map_err(|error| error.to_string())?,
                None => manager
                    .servers()
                    .map_err(|error| error.to_string())?
                    .into_iter()
                    .find(|server| server.config.name == name)
                    .ok_or_else(|| format!("unknown MCP server: {name}"))?,
            };
            let (tools, warning) = probe_mcp_server(&manager, &name);
            Ok(McpServerChange {
                server: mcp_server_summary(&state),
                tools,
                warning,
            })
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn remove_server(&self, name: &str) -> Result<(), String> {
        let manager = Arc::clone(&self.manager);
        let name = name.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .remove_server(&name)
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn test_server(&self, name: &str) -> Result<Vec<McpToolSummary>, String> {
        let manager = Arc::clone(&self.manager);
        let name = name.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .probe(&name)
                .map(|tools| tools.into_iter().map(mcp_tool_summary).collect())
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn set_server_enabled(
        &self,
        name: &str,
        enabled: bool,
    ) -> Result<McpServerChange, String> {
        let manager = Arc::clone(&self.manager);
        let name = name.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            let state = manager
                .set_server_enabled(&name, enabled)
                .map_err(|error| error.to_string())?;
            let (tools, warning) = probe_mcp_server(&manager, &name);
            Ok(McpServerChange {
                server: mcp_server_summary(&state),
                tools,
                warning,
            })
        })
        .await
        .map_err(|error| error.to_string())?
    }
}

struct DesktopTerminal {
    manager: Arc<openbad_terminal::TerminalManager>,
}

#[async_trait::async_trait]
impl TerminalService for DesktopTerminal {
    async fn create(&self, cwd: &Path, shell: Option<&str>) -> Result<String, String> {
        let manager = Arc::clone(&self.manager);
        let cwd = cwd.to_path_buf();
        let shell = shell.map(str::to_owned);
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .create(&cwd, shell.as_deref())
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn send(&self, id: &str, data: &str) -> Result<(), String> {
        let manager = Arc::clone(&self.manager);
        let id = id.to_owned();
        let data = data.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager.send(&id, &data).map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn read(&self, id: &str) -> Result<TerminalRead, String> {
        let manager = Arc::clone(&self.manager);
        let id = id.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager
                .read(&id)
                .map(|output| TerminalRead {
                    content: output.content,
                    running: output.running,
                    truncated: output.truncated,
                })
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn close(&self, id: &str) -> Result<(), String> {
        let manager = Arc::clone(&self.manager);
        let id = id.to_owned();
        tauri::async_runtime::spawn_blocking(move || {
            manager.close(&id).map_err(|error| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    async fn close_all(&self) {
        let manager = Arc::clone(&self.manager);
        let _ = tauri::async_runtime::spawn_blocking(move || manager.close_all()).await;
    }
}

#[derive(Clone)]
struct SubagentSettings {
    store: Arc<Mutex<Store>>,
    api_key: String,
    workspace_id: WorkspaceId,
    workspace_path: PathBuf,
    parent_session: SessionId,
    model: String,
    thinking: bool,
    reasoning_effort: Option<ReasoningEffort>,
    base_url: String,
    user_id: String,
    grants: Vec<OutsideGrant>,
    terminal: Arc<openbad_terminal::TerminalManager>,
    mcp: Arc<openbad_mcp::McpManager>,
}

struct SubagentRunner {
    settings: Arc<SubagentSettings>,
    active: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
}

fn subagent_info(record: SubagentRecord) -> SubagentInfo {
    SubagentInfo {
        id: record.id,
        title: record.title,
        task: record.task,
        model: record.model,
        status: record.status,
        log: record.log,
        result: record.result,
        error: record.error,
        steps: record.steps,
        created_at: record.created_at,
        updated_at: record.updated_at,
        finished_at: record.finished_at,
    }
}

impl SubagentRuntime for SubagentRunner {
    fn spawn(&self, spec: SubagentSpec) -> Result<SubagentInfo, String> {
        let id = Uuid::now_v7().to_string();
        let now = now_millis();
        let record = SubagentRecord {
            id: id.clone(),
            workspace_id: self.settings.workspace_id,
            parent_session_id: Some(self.settings.parent_session.to_string()),
            title: spec.title.clone(),
            task: spec.task.clone(),
            model: spec
                .model
                .clone()
                .unwrap_or_else(|| self.settings.model.clone()),
            status: "running".to_owned(),
            log: String::new(),
            result: String::new(),
            error: None,
            steps: 0,
            created_at: now,
            updated_at: now,
            finished_at: None,
        };
        {
            let store = self
                .settings
                .store
                .lock()
                .map_err(|error| format!("store lock poisoned: {error}"))?;
            let _ = store.prune_finished_subagents(
                self.settings.workspace_id,
                now_millis() - subagent_retention_ms(),
            );
            store
                .create_subagent(record.clone())
                .map_err(|error| error.to_string())?;
        }
        let cancel = Arc::new(AtomicBool::new(false));
        if let Ok(mut active) = self.active.lock() {
            active.insert(id.clone(), Arc::clone(&cancel));
        }
        let settings = Arc::clone(&self.settings);
        let active = Arc::clone(&self.active);
        let task_id = id.clone();
        tauri::async_runtime::spawn(async move {
            run_subagent(settings, task_id, spec, cancel).await;
            if let Ok(mut active) = active.lock() {
                active.remove(&id);
            }
        });
        Ok(subagent_info(record))
    }

    fn list(&self, limit: usize) -> Result<Vec<SubagentInfo>, String> {
        let store = self
            .settings
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let _ = store.prune_finished_subagents(
            self.settings.workspace_id,
            now_millis() - subagent_retention_ms(),
        );
        store
            .list_subagents(self.settings.workspace_id, limit as u32)
            .map(|records| records.into_iter().map(subagent_info).collect())
            .map_err(|error| error.to_string())
    }

    fn get(&self, id: &str) -> Result<Option<SubagentInfo>, String> {
        let store = self
            .settings
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .get_subagent(id)
            .map(|record| record.map(subagent_info))
            .map_err(|error| error.to_string())
    }

    fn cancel(&self, id: &str) -> Result<SubagentInfo, String> {
        if let Ok(active) = self.active.lock() {
            if let Some(flag) = active.get(id) {
                flag.store(true, Ordering::SeqCst);
            }
        }
        let store = self
            .settings
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        store
            .update_subagent(
                id,
                SubagentUpdate {
                    status: Some("cancelled".to_owned()),
                    append_log: Some("\n[cancelled by user]\n".to_owned()),
                    ..SubagentUpdate::default()
                },
            )
            .map(subagent_info)
            .map_err(|error| error.to_string())
    }
}

fn update_subagent_record(store: &Arc<Mutex<Store>>, id: &str, update: SubagentUpdate) {
    if let Ok(store) = store.lock() {
        let _ = store.update_subagent(id, update);
    }
}

struct SubagentObserver {
    store: Arc<Mutex<Store>>,
    id: String,
    cancel: Arc<AtomicBool>,
}

impl AgentObserver for SubagentObserver {
    fn on_step(&self, step: u32) {
        update_subagent_record(
            &self.store,
            &self.id,
            SubagentUpdate {
                status: Some("running".to_owned()),
                steps: Some(step),
                ..SubagentUpdate::default()
            },
        );
    }

    fn on_tool_call(&self, step: u32, call: &openbad_llm::chat::ToolCall) {
        let args = truncate_for_ui(&call.function.arguments);
        update_subagent_record(
            &self.store,
            &self.id,
            SubagentUpdate {
                append_log: Some(format!(
                    "\n[step {step}] -> {}({args})\n",
                    call.function.name
                )),
                ..SubagentUpdate::default()
            },
        );
    }

    fn on_tool_result(
        &self,
        step: u32,
        call: &openbad_llm::chat::ToolCall,
        content: &str,
        is_error: bool,
        _metadata: &Value,
    ) {
        let name = call.function.name.as_str();
        let result = truncate_for_ui(content);
        update_subagent_record(
            &self.store,
            &self.id,
            SubagentUpdate {
                append_log: Some(format!(
                    "[step {step}] <- {name} {}: {result}\n",
                    if is_error { "error" } else { "ok" }
                )),
                ..SubagentUpdate::default()
            },
        );
    }

    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

async fn run_subagent(
    settings: Arc<SubagentSettings>,
    id: String,
    spec: SubagentSpec,
    cancel: Arc<AtomicBool>,
) {
    let workspace = Arc::new(WorkspaceService::new(settings.workspace_path.clone()));
    let mut client = DeepSeekClient::new(ReqwestTransport::new(), settings.api_key.clone())
        .with_model(spec.model.unwrap_or_else(|| settings.model.clone()))
        .with_base_url(settings.base_url.clone())
        .with_user_id(settings.user_id.clone())
        .with_thinking(settings.thinking);
    if let Some(effort) = settings.reasoning_effort {
        client = client.with_reasoning_effort(effort);
    }
    let agent = default_agent(
        client,
        workspace,
        AgentConfig {
            policy: ToolPolicy::from_flags(spec.allow_writes, spec.allow_commands),
            max_steps: 0,
            stream: false,
            ..AgentConfig::default()
        },
    )
    .with_memory(Arc::new(WorkspaceMemory {
        store: Arc::clone(&settings.store),
        workspace_id: settings.workspace_id,
    }))
    .with_project(Arc::new(StoreProjectAreas {
        store: Arc::clone(&settings.store),
        workspace_id: settings.workspace_id,
    }))
    .with_search(Arc::new(StoreSearch {
        store: Arc::clone(&settings.store),
        workspace_id: settings.workspace_id,
    }))
    .with_outside_grants(settings.grants.clone())
    .with_lsp(Arc::new(DesktopLsp::new(settings.workspace_path.clone())))
    .with_sessions(Arc::new(WorkspaceSessions {
        store: Arc::clone(&settings.store),
        workspace_id: settings.workspace_id,
    }))
    .with_mcp(Arc::new(DesktopMcp::new(Arc::clone(&settings.mcp))))
    .with_terminal(Arc::new(DesktopTerminal {
        manager: Arc::clone(&settings.terminal),
    }))
    .with_redactions(vec![settings.api_key.clone()]);

    let observer = SubagentObserver {
        store: Arc::clone(&settings.store),
        id: id.clone(),
        cancel: Arc::clone(&cancel),
    };
    let prompt = format!(
        "You are a directed subagent spawned by the main OpenBaD agent. Work only on the task below. You share the parent workspace and access mode, cannot spawn further subagents, and must not ask the user questions. Use tools as needed, verify your work, and finish with a concise report covering what changed, what you checked, and any remaining risk.\n\nTask:\n{}",
        spec.task
    );
    let outcome = agent
        .run_with_observer(vec![ChatMessage::user(prompt)], &observer)
        .await;
    let update = match outcome {
        Ok(run) => SubagentUpdate {
            status: Some("completed".to_owned()),
            result: Some(run.text),
            steps: Some(run.steps),
            append_log: Some("\n[completed]\n".to_owned()),
            ..SubagentUpdate::default()
        },
        Err(error) if cancel.load(Ordering::SeqCst) || matches!(error, AgentError::Cancelled) => {
            SubagentUpdate {
                status: Some("cancelled".to_owned()),
                error: Some(error.to_string()),
                append_log: Some("\n[cancelled]\n".to_owned()),
                ..SubagentUpdate::default()
            }
        }
        Err(error) => {
            let message = error.to_string().replace(&settings.api_key, "[redacted]");
            SubagentUpdate {
                status: Some("failed".to_owned()),
                error: Some(message.clone()),
                append_log: Some(format!("\n[failed] {message}\n")),
                ..SubagentUpdate::default()
            }
        }
    };
    update_subagent_record(&settings.store, &id, update);
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
        #[serde(default)]
        call_id: String,
        name: String,
        arguments: String,
    },
    ToolResult {
        step: u32,
        #[serde(default)]
        call_id: String,
        name: String,
        content: String,
        is_error: bool,
        metadata: Value,
    },
    Usage {
        usage: UsageTotals,
    },
    Compaction {
        before_bytes: usize,
        after_bytes: usize,
    },
    AttemptFailed {
        step: u32,
        attempt: u32,
        message: String,
        retry_in_ms: Option<u64>,
    },
    ToolStatus {
        step: u32,
        #[serde(default)]
        call_id: String,
        name: String,
        status: String,
        duration_ms: Option<u64>,
    },
    ApprovalRequest {
        request_id: String,
        tool: String,
        risk: String,
        detail: String,
    },
    ApprovalDecision {
        request_id: String,
        allowed: bool,
        decision: String,
    },
}

struct ChannelObserver {
    channel: Channel<AgentEvent>,
    cancel: Arc<AtomicBool>,
    compaction_reported: AtomicBool,
    journal: Option<RunJournal>,
    /// Per-call duration and error flag, keyed by tool call id. Settlement
    /// folds these into the durable `tool/result` payload so a reloaded
    /// transcript renders the same cards as the live one.
    tool_outcomes: std::sync::Mutex<std::collections::HashMap<String, ToolOutcome>>,
}

#[derive(Clone, Copy, Default)]
struct ToolOutcome {
    duration_ms: Option<u64>,
    is_error: bool,
}

/// Durable lifecycle recording for the run that owns an observer. Live events
/// remain ephemeral; these are the events that must survive a restart.
struct RunJournal {
    store: Arc<Mutex<Store>>,
    session: SessionId,
    turn: u32,
    /// Surface message sequences loaded before the turn started. Compaction
    /// resolves its shadowed boundary against these.
    surface_seqs: Vec<Option<u64>>,
}

impl ChannelObserver {
    fn record(&self, kind: SessionEventKind, step: Option<u32>, payload: Value) {
        let Some(journal) = &self.journal else {
            return;
        };
        let Ok(store) = journal.store.lock() else {
            return;
        };
        let mut event = NewEvent::new(kind)
            .with_turn(journal.turn)
            .with_payload(payload);
        if let Some(step) = step {
            event = event.with_step(step);
        }
        let _ = store.append_event(journal.session, event);
    }
}

impl AgentObserver for ChannelObserver {
    fn on_step_start(&self, step: u32) {
        let _ = self.channel.send(AgentEvent::Step { step });
        self.record(
            SessionEventKind::StepStart,
            Some(step),
            json!({ "step": step }),
        );
    }

    fn on_step_end(&self, outcome: &StepOutcome) {
        let payload =
            serde_json::to_value(outcome).unwrap_or_else(|_| json!({ "step": outcome.step }));
        self.record(SessionEventKind::StepEnd, Some(outcome.step), payload);
        let usage = outcome.usage;
        let billed = usage.input_tokens
            + usage.cache_read_tokens
            + usage.cache_write_tokens
            + usage.output_tokens;
        if billed > 0 {
            self.record(
                SessionEventKind::UsageRecorded,
                Some(outcome.step),
                json!({
                    "input_tokens": usage.input_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": usage.cost_usd,
                }),
            );
        }
    }

    fn on_step_settled(&self, settlement: &StepSettlement<'_>) {
        let Some(journal) = &self.journal else {
            return;
        };
        let outcomes = self
            .tool_outcomes
            .lock()
            .map(|outcomes| outcomes.clone())
            .unwrap_or_default();
        let Ok(store) = journal.store.lock() else {
            return;
        };
        let drafts = settlement
            .messages
            .iter()
            .filter_map(|message| {
                let mut payload = match message.role {
                    ChatRole::User => json!({
                        "content": message.content.clone().unwrap_or_default(),
                    }),
                    ChatRole::Assistant => json!({
                        "content": message.content,
                        "reasoning_content": message.reasoning_content,
                        "tool_calls": message.tool_calls,
                    }),
                    ChatRole::Tool => {
                        let call_id = message.tool_call_id.clone().unwrap_or_default();
                        let outcome = outcomes.get(&call_id).copied().unwrap_or_default();
                        json!({
                            "content": message.content.clone().unwrap_or_default(),
                            "tool_call_id": call_id.clone(),
                            "call_id": call_id,
                            "duration_ms": outcome.duration_ms,
                            "is_error": outcome.is_error,
                        })
                    }
                    _ => return None,
                };
                let kind = match message.role {
                    ChatRole::User => SessionEventKind::UserMessage,
                    ChatRole::Assistant => SessionEventKind::AssistantMessage,
                    ChatRole::Tool => SessionEventKind::ToolResult,
                    _ => return None,
                };
                if settlement.interrupted && message.role == ChatRole::Assistant {
                    payload["interrupted"] = json!(true);
                }
                Some(
                    NewEvent::new(kind)
                        .with_turn(journal.turn)
                        .with_step(settlement.step)
                        .with_payload(payload),
                )
            })
            .collect::<Vec<_>>();
        let _ = store.append_events(journal.session, drafts);
    }

    fn on_tool_status(
        &self,
        step: u32,
        call: &openbad_llm::chat::ToolCall,
        status: ToolStatus,
        duration_ms: Option<u64>,
    ) {
        if let (ToolStatus::Settled, Some(duration_ms)) = (status, duration_ms) {
            if let Ok(mut outcomes) = self.tool_outcomes.lock() {
                outcomes.entry(call.id.clone()).or_default().duration_ms = Some(duration_ms);
            }
        }
        let _ = self.channel.send(AgentEvent::ToolStatus {
            step,
            call_id: call.id.clone(),
            name: call.function.name.clone(),
            status: format!("{status:?}").to_lowercase(),
            duration_ms,
        });
    }

    fn on_approval_request(&self, step: u32, request: &ApprovalRequest) {
        let risk = format!("{:?}", request.risk).to_lowercase();
        let _ = self.channel.send(AgentEvent::ApprovalRequest {
            request_id: request.id.to_string(),
            tool: request.title.clone(),
            risk: risk.clone(),
            detail: request.detail.clone(),
        });
        self.record(
            SessionEventKind::ApprovalRequest,
            Some(step),
            json!({
                "request_id": request.id.to_string(),
                "tool": request.title,
                "risk": risk,
                "detail": request.detail,
            }),
        );
    }

    fn on_approval_decision(
        &self,
        step: u32,
        request: &ApprovalRequest,
        decision: ApprovalDecision,
    ) {
        let name = format!("{decision:?}").to_lowercase();
        let _ = self.channel.send(AgentEvent::ApprovalDecision {
            request_id: request.id.to_string(),
            allowed: decision.is_allowed(),
            decision: name.clone(),
        });
        self.record(
            SessionEventKind::ApprovalDecision,
            Some(step),
            json!({
                "request_id": request.id.to_string(),
                "tool": request.title,
                "allowed": decision.is_allowed(),
                "decision": name,
            }),
        );
    }

    fn on_compaction_settled(&self, record: &CompactionRecord) {
        let Some(journal) = &self.journal else {
            return;
        };
        let Ok(store) = journal.store.lock() else {
            return;
        };
        let _ = persist_compaction(
            &store,
            journal.session,
            journal.turn,
            record,
            &journal.surface_seqs,
        );
    }

    fn on_attempt_failed(&self, failure: &AttemptFailure) {
        let _ = self.channel.send(AgentEvent::AttemptFailed {
            step: failure.step,
            attempt: failure.attempt,
            message: failure.message.clone(),
            retry_in_ms: failure.retry_in_ms,
        });
        let payload =
            serde_json::to_value(failure).unwrap_or_else(|_| json!({ "step": failure.step }));
        self.record(
            SessionEventKind::AssistantAttempt,
            Some(failure.step),
            payload,
        );
    }

    fn on_request(
        &self,
        step: u32,
        envelope: &RequestEnvelope,
        message_count: usize,
        estimated_input_tokens: usize,
    ) {
        let mut payload = serde_json::to_value(envelope).unwrap_or_else(|_| json!({}));
        if let Some(object) = payload.as_object_mut() {
            object.insert("messages".to_owned(), json!(message_count));
            object.insert(
                "estimated_input_tokens".to_owned(),
                json!(estimated_input_tokens),
            );
        }
        self.record(SessionEventKind::RequestHeader, Some(step), payload);
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
            call_id: call.id.clone(),
            name: call.function.name.clone(),
            arguments: call.function.arguments.clone(),
        });
    }

    fn on_tool_result(
        &self,
        step: u32,
        call: &openbad_llm::chat::ToolCall,
        content: &str,
        is_error: bool,
        metadata: &Value,
    ) {
        if let Ok(mut outcomes) = self.tool_outcomes.lock() {
            outcomes.entry(call.id.clone()).or_default().is_error = is_error;
        }
        let _ = self.channel.send(AgentEvent::ToolResult {
            step,
            call_id: call.id.clone(),
            name: call.function.name.clone(),
            content: content.to_owned(),
            is_error,
            metadata: metadata.clone(),
        });
    }

    fn on_usage(&self, usage: &UsageTotals) {
        let _ = self.channel.send(AgentEvent::Usage { usage: *usage });
    }

    fn on_compaction(&self, before_bytes: usize, after_bytes: usize) {
        if after_bytes < before_bytes && !self.compaction_reported.swap(true, Ordering::Relaxed) {
            let _ = self.channel.send(AgentEvent::Compaction {
                before_bytes,
                after_bytes,
            });
        }
    }

    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

/// Approval gate for the desktop app. It waits for the UI, but never longer
/// than the run: cancellation wins over a pending prompt.
struct DesktopApprovals {
    pending: Arc<Mutex<HashMap<Uuid, std::sync::mpsc::Sender<ApprovalDecision>>>>,
    cancel: Arc<AtomicBool>,
    cache: Arc<Mutex<ApprovalCache>>,
    session: SessionId,
    workspace: String,
}

impl DesktopApprovals {
    fn cached(&self, tool: &str) -> Option<ApprovalDecision> {
        let cache = self.cache.lock().ok()?;
        cache
            .sessions
            .get(&(self.session, tool.to_owned()))
            .or_else(|| {
                cache
                    .workspaces
                    .get(&(self.workspace.clone(), tool.to_owned()))
            })
            .copied()
    }

    fn register(&self, id: Uuid, sender: std::sync::mpsc::Sender<ApprovalDecision>) -> bool {
        let Ok(mut pending) = self.pending.lock() else {
            return false;
        };
        pending.insert(id, sender);
        true
    }

    fn forget(&self, id: Uuid) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.remove(&id);
        }
    }

    fn remember(&self, tool: &str, decision: ApprovalDecision) {
        let Ok(mut cache) = self.cache.lock() else {
            return;
        };
        match decision {
            ApprovalDecision::AllowSession => {
                cache
                    .sessions
                    .insert((self.session, tool.to_owned()), decision);
            }
            ApprovalDecision::AllowWorkspace => {
                cache
                    .workspaces
                    .insert((self.workspace.clone(), tool.to_owned()), decision);
            }
            _ => {}
        }
    }
}

#[async_trait::async_trait]
impl ApprovalGate for DesktopApprovals {
    async fn decide(&self, request: &ApprovalRequest) -> ApprovalDecision {
        if let Some(decision) = self.cached(&request.title) {
            return decision;
        }
        let (sender, receiver) = std::sync::mpsc::channel();
        if !self.register(request.id, sender) {
            return ApprovalDecision::Expired;
        }

        loop {
            if self.cancel.load(Ordering::SeqCst) {
                self.forget(request.id);
                return ApprovalDecision::Cancelled;
            }
            match receiver.try_recv() {
                Ok(decision) => {
                    self.remember(&request.title, decision);
                    return decision;
                }
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    return ApprovalDecision::Expired;
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                }
            }
        }
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
    /// Wall-clock time the turn took, in milliseconds.
    pub duration_ms: u64,
    /// Files staged inside the workspace for this turn.
    pub attachments: Vec<UploadedFile>,
}

#[derive(Serialize)]
pub struct UiMessage {
    /// Store sequence of the message this bubble came from.
    pub seq: u64,
    pub role: String,
    pub text: String,
    #[serde(default)]
    pub attachments: Vec<String>,
    pub reasoning: Option<String>,
    /// Set on the last assistant message of a completed turn.
    pub duration_ms: Option<u64>,
    /// The turn was cut short; this text is all that was delivered.
    #[serde(default)]
    pub interrupted: bool,
    /// Tool calls made by this assistant message, joined with their results.
    #[serde(default)]
    pub tool_calls: Vec<UiToolCall>,
}

#[derive(Serialize)]
pub struct UiToolCall {
    pub call_id: String,
    pub name: String,
    pub arguments: String,
    /// `queued`, `running`, `settled`, `failed`, or `interrupted`.
    pub status: String,
    pub result: String,
    pub is_error: bool,
    pub duration_ms: Option<u64>,
}

struct StoredToolResult {
    content: String,
    is_error: bool,
    duration_ms: Option<u64>,
    interrupted: bool,
}

#[derive(Serialize)]
pub struct SessionView {
    pub session_id: String,
    /// The session this one was forked from, when it was.
    pub forked_from_session: Option<String>,
    pub forked_from_seq: Option<u64>,
    pub messages: Vec<UiMessage>,
    /// Sum of every completed turn duration, in milliseconds.
    pub total_duration_ms: u64,
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
fn archived_sessions(state: State<'_, AppState>) -> Result<Vec<SessionSummary>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .list_archived_sessions(50)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn archive_session(
    state: State<'_, AppState>,
    session_id: String,
    archived: bool,
) -> Result<(), String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .set_session_archived(session, archived)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn delete_session(state: State<'_, AppState>, session_id: String) -> Result<(), String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .delete_session(session)
        .map_err(|error| error.to_string())
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

#[derive(Debug, Clone, Serialize)]
pub struct ProjectAreaCounts {
    pub workspace_files: u64,
    pub agents: u64,
    pub running_agents: u64,
    pub references: u64,
    pub knowledge: u64,
    pub memories: u64,
    pub todos_open: u64,
    pub todos_total: u64,
    pub questions_open: u64,
    pub questions_total: u64,
    pub plan_steps: u64,
    pub plan_open_steps: u64,
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
async fn project_todos(workspace_path: String) -> Result<Vec<ProjectTodo>, String> {
    off_thread(move || read_project_todos(workspace_path)).await
}

fn read_project_todos(workspace_path: String) -> Result<Vec<ProjectTodo>, String> {
    let path = PathBuf::from(&workspace_path)
        .join(".openbad")
        .join("todos.json");
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let content = std::fs::read_to_string(&path).map_err(|error| error.to_string())?;
    serde_json::from_str(&content).map_err(|error| format!("invalid .openbad/todos.json: {error}"))
}

/// Warns when an imported project enables MCP servers this machine has not
/// defined globally.
fn mcp_transfer_warnings(state: &AppState, workspace_root: &Path) -> Vec<String> {
    let Ok(project) = openbad_mcp::McpProjectConfig::load(workspace_root) else {
        return Vec::new();
    };
    if project.enabled.is_empty() {
        return Vec::new();
    }
    let Ok(manager) = state.mcp.get(workspace_root) else {
        return Vec::new();
    };
    let Ok(servers) = manager.servers() else {
        return Vec::new();
    };
    let defined = servers
        .iter()
        .map(|server| server.config.name.as_str())
        .collect::<std::collections::HashSet<_>>();
    let missing = project
        .enabled
        .iter()
        .filter(|name| !defined.contains(name.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if missing.is_empty() {
        Vec::new()
    } else {
        vec![format!(
            "Enabled MCP servers missing on this machine: {}",
            missing.join(", ")
        )]
    }
}

/// Packs a project into a portable bundle for another machine.
#[tauri::command]
async fn export_project(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    workspace_path: String,
    destination: String,
) -> Result<openbad_transfer::ExportReport, String> {
    let store = Arc::clone(&state.store);
    let version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        let store = store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let workspace = project_workspace(&store, &workspace_path)?;
        openbad_transfer::export_project(&store, workspace, Path::new(&destination), &version)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Imports a bundle into a new project directory under `destination_parent`.
#[tauri::command]
async fn import_project(
    state: State<'_, AppState>,
    bundle_path: String,
    destination_parent: String,
) -> Result<openbad_transfer::ImportReport, String> {
    let manifest = openbad_transfer::bundle_manifest(Path::new(&bundle_path))
        .map_err(|error| error.to_string())?;
    let destination = PathBuf::from(&destination_parent).join(
        openbad_transfer::safe_project_directory_name(&manifest.project_name),
    );
    let store = Arc::clone(&state.store);
    let bundle_for_import = bundle_path.clone();
    let destination_for_import = destination.clone();
    let mut report = tauri::async_runtime::spawn_blocking(move || {
        let store = store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        openbad_transfer::import_project(
            &store,
            Path::new(&bundle_for_import),
            &destination_for_import,
        )
        .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;
    report
        .warnings
        .extend(mcp_transfer_warnings(&state, &destination));
    Ok(report)
}

/// Merges a bundle into an existing project: files overwrite, extras stay,
/// and chats append.
#[tauri::command]
async fn import_project_into(
    state: State<'_, AppState>,
    workspace_path: String,
    bundle_path: String,
) -> Result<openbad_transfer::ImportReport, String> {
    let store = Arc::clone(&state.store);
    let workspace_for_import = workspace_path.clone();
    let mut report = tauri::async_runtime::spawn_blocking(move || {
        let store = store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let workspace = project_workspace(&store, &workspace_for_import)?;
        openbad_transfer::import_project_merge(&store, workspace, Path::new(&bundle_path))
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;
    report
        .warnings
        .extend(mcp_transfer_warnings(&state, Path::new(&workspace_path)));
    Ok(report)
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

fn project_area_counts_payload(
    store: &Store,
    workspace_path: &str,
) -> Result<ProjectAreaCounts, String> {
    let root = PathBuf::from(workspace_path);
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let workspace = project_workspace(store, workspace_path)?;
    let _ = store.prune_finished_subagents(workspace, now_millis() - subagent_retention_ms());
    let overview = store
        .project_overview(workspace)
        .map_err(|error| error.to_string())?;
    let agents = store
        .list_subagents(workspace, 200)
        .map_err(|error| error.to_string())?;
    let todos: Vec<ProjectTodo> = {
        let path = root.join(".openbad").join("todos.json");
        match std::fs::read_to_string(path) {
            Ok(content) => serde_json::from_str(&content).unwrap_or_default(),
            Err(_) => Vec::new(),
        }
    };
    let questions = openbad_workspace::load_questions(&root).unwrap_or_default();
    let plan = openbad_workspace::load_plan(&root).ok().flatten();
    let todos_open = todos
        .iter()
        .filter(|todo| todo.state != "done" && todo.state != "dropped")
        .count() as u64;
    let questions_open = questions
        .iter()
        .filter(|question| question.answer.is_none())
        .count() as u64;
    let plan_steps = plan
        .as_ref()
        .map(|plan| plan.steps.len() as u64)
        .unwrap_or_default();
    let plan_open_steps = plan
        .as_ref()
        .map(|plan| {
            plan.steps
                .iter()
                .filter(|step| step.status != "done")
                .count() as u64
        })
        .unwrap_or_default();
    Ok(ProjectAreaCounts {
        workspace_files: overview.index.files,
        agents: agents.len() as u64,
        running_agents: agents
            .iter()
            .filter(|agent| agent.status == "running")
            .count() as u64,
        references: overview.repos,
        knowledge: overview.knowledge_documents,
        memories: overview.memories,
        todos_open,
        todos_total: todos.len() as u64,
        questions_open,
        questions_total: questions.len() as u64,
        plan_steps,
        plan_open_steps,
    })
}

#[tauri::command]
fn project_area_counts(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<ProjectAreaCounts, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    project_area_counts_payload(&store, &workspace_path)
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
fn project_agents(
    state: State<'_, AppState>,
    workspace_path: String,
    limit: Option<u32>,
) -> Result<Vec<SubagentRecord>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let _ = store.prune_finished_subagents(workspace, now_millis() - subagent_retention_ms());
    store
        .list_subagents(workspace, limit.unwrap_or(50))
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn cancel_agent(state: State<'_, AppState>, id: String) -> Result<SubagentRecord, String> {
    if let Ok(active) = state.subagent_cancels.lock() {
        if let Some(flag) = active.get(&id) {
            flag.store(true, Ordering::SeqCst);
        }
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .update_subagent(
            &id,
            SubagentUpdate {
                status: Some("cancelled".to_owned()),
                append_log: Some("\n[cancelled by user]\n".to_owned()),
                ..SubagentUpdate::default()
            },
        )
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
fn set_project_repo_read_only(
    state: State<'_, AppState>,
    repo_id: String,
    read_only: bool,
) -> Result<(), String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .set_project_repo_read_only(&repo_id, read_only)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn outside_access_grants(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<openbad_store::types::OutsideAccessGrant>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .list_outside_access_grants(workspace)
        .map_err(|error| error.to_string())
}

fn canonical_grant_path(raw: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        return Err("filesystem grants require an absolute path".to_owned());
    }
    let path = if path.exists() {
        path.canonicalize()
            .map_err(|error| format!("cannot resolve {raw}: {error}"))?
    } else {
        let parent = path
            .parent()
            .ok_or_else(|| format!("path has no parent: {raw}"))?
            .canonicalize()
            .map_err(|error| format!("cannot resolve parent of {raw}: {error}"))?;
        parent.join(
            path.file_name()
                .ok_or_else(|| format!("path has no file name: {raw}"))?,
        )
    };
    if path.parent().is_none() {
        return Err("refusing to grant the filesystem root".to_owned());
    }
    Ok(path)
}

#[tauri::command]
fn grant_outside_access(
    state: State<'_, AppState>,
    workspace_path: String,
    path: String,
    write_access: bool,
    reason: String,
) -> Result<openbad_store::types::OutsideAccessGrant, String> {
    let path = canonical_grant_path(&path)?;
    let reason = reason.trim();
    let reason = if reason.is_empty() {
        "granted from the OpenBaD access prompt"
    } else {
        reason
    };
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .add_outside_access_grant(workspace, &path.to_string_lossy(), write_access, reason)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn revoke_outside_access(
    state: State<'_, AppState>,
    workspace_path: String,
    grant_id: String,
) -> Result<(), String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .remove_outside_access_grant(workspace, &grant_id)
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
fn project_plan(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Option<openbad_workspace::Plan>, String> {
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
    openbad_workspace::load_plan(&path).map_err(|error| error.to_string())
}

#[tauri::command]
fn approve_project_plan(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<openbad_workspace::Plan, String> {
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
    openbad_workspace::approve_plan(&path).map_err(|error| error.to_string())
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
fn write_export_file(path: String, content: String) -> Result<String, String> {
    let path = PathBuf::from(&path);
    if !path.is_absolute() {
        return Err("export path must be absolute".to_owned());
    }
    if path.is_dir() {
        return Err(format!("export path is a directory: {}", path.display()));
    }
    if let Some(parent) = path.parent() {
        if !parent.is_dir() {
            return Err(format!(
                "export directory does not exist: {}",
                parent.display()
            ));
        }
    }
    std::fs::write(&path, content)
        .map_err(|error| format!("could not write {}: {error}", path.display()))?;
    Ok(path.display().to_string())
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
    kind: Option<String>,
) -> Result<KnowledgeDocumentSummary, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    store
        .add_knowledge_document_with_metadata(
            workspace,
            &title,
            kind.as_deref().unwrap_or("note"),
            None,
            tags,
            &content,
            confidence.as_deref().unwrap_or("inferred"),
            None,
            None,
            "proposed",
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

const MAX_UPLOAD_BYTES: u64 = 256 * 1024 * 1024;
const MAX_UPLOAD_FILES: usize = 50;
const MAX_KNOWLEDGE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_INLINE_BYTES: usize = 256 * 1024;
const MAX_CHAT_EXTRACTED_BYTES: usize = 192 * 1024;
const MAX_CHAT_ATTACHMENTS: usize = 8;
const MAX_OCR_IMAGE_BYTES: u64 = 25 * 1024 * 1024;
const MAX_PDF_BYTES: u64 = 25 * 1024 * 1024;
/// OCR is per-file and runs under a command that has no other progress signal.
const OCR_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);
const UPLOADED_REFERENCES_NAME: &str = "Uploaded documents";

/// One document supplied by the UI, either as a filesystem path or as inline text.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadSource {
    pub name: String,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub content: Option<String>,
    /// Base64-encoded bytes for clipboard images and other binary paste/drop
    /// payloads that do not have a filesystem path.
    #[serde(default)]
    pub content_base64: Option<String>,
    #[serde(default)]
    pub mime: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadedFile {
    pub name: String,
    pub path: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadReport {
    pub files: Vec<UploadedFile>,
    pub skipped: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReferenceUpload {
    pub repo: ProjectRepo,
    pub report: UploadReport,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeImportReport {
    pub documents: Vec<KnowledgeDocumentSummary>,
    pub skipped: Vec<String>,
}

fn safe_upload_name(name: &str) -> Result<String, String> {
    let leaf = name.rsplit(['/', '\\']).next().unwrap_or_default().trim();
    let cleaned: String = leaf
        .chars()
        .map(|character| {
            if character.is_control()
                || matches!(character, '<' | '>' | ':' | '"' | '|' | '?' | '*')
            {
                '_'
            } else {
                character
            }
        })
        .collect();
    if cleaned.is_empty() || cleaned == "." || cleaned == ".." {
        return Err(format!("upload name is not a file name: {name}"));
    }
    Ok(cleaned)
}

fn upload_name_with_extension(name: &str, mime: Option<&str>) -> String {
    let path = Path::new(name);
    if path.extension().is_some() {
        return name.to_owned();
    }
    let extension = match mime.unwrap_or_default() {
        "image/png" => Some("png"),
        "image/jpeg" | "image/jpg" => Some("jpg"),
        "image/gif" => Some("gif"),
        "image/webp" => Some("webp"),
        "image/bmp" => Some("bmp"),
        "image/svg+xml" => Some("svg"),
        "application/pdf" => Some("pdf"),
        "text/plain" => Some("txt"),
        "text/markdown" => Some("md"),
        "application/json" => Some("json"),
        _ => None,
    };
    match extension {
        Some(extension) => format!("{name}.{extension}"),
        None => name.to_owned(),
    }
}

fn load_upload(source: &UploadSource, max_bytes: u64) -> Result<(String, Vec<u8>), String> {
    if let Some(path) = source
        .path
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let canonical = std::fs::canonicalize(path)
            .map_err(|error| format!("upload source {path}: {error}"))?;
        let metadata = std::fs::metadata(&canonical).map_err(|error| error.to_string())?;
        if !metadata.is_file() {
            return Err(format!("uploads must be files, not folders: {path}"));
        }
        if metadata.len() > max_bytes {
            return Err(format!(
                "{} is {} bytes; the maximum upload is {} bytes",
                canonical.display(),
                metadata.len(),
                max_bytes
            ));
        }
        let name = canonical
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("upload");
        let bytes = std::fs::read(&canonical).map_err(|error| error.to_string())?;
        return Ok((safe_upload_name(name)?, bytes));
    }
    if let Some(content) = &source.content {
        if content.len() as u64 > max_bytes {
            return Err(format!(
                "{} is {} bytes; the maximum upload is {} bytes",
                source.name,
                content.len(),
                max_bytes
            ));
        }
        return Ok((safe_upload_name(&source.name)?, content.as_bytes().to_vec()));
    }
    if let Some(encoded) = &source.content_base64 {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(encoded.trim())
            .map_err(|error| format!("{} is not valid base64: {error}", source.name))?;
        if bytes.len() as u64 > max_bytes {
            return Err(format!(
                "{} is {} bytes; the maximum upload is {} bytes",
                source.name,
                bytes.len(),
                max_bytes
            ));
        }
        let name = upload_name_with_extension(&source.name, source.mime.as_deref());
        return Ok((safe_upload_name(&name)?, bytes));
    }
    Err(format!(
        "{} has no path, text content, or base64 content to upload",
        source.name
    ))
}

fn path_is_free(path: &Path) -> bool {
    // symlink_metadata also reports dangling symlinks, so an upload never
    // writes through a link planted in the destination directory.
    std::fs::symlink_metadata(path).is_err()
}

fn unique_upload_path(directory: &Path, name: &str) -> PathBuf {
    let candidate = directory.join(name);
    if path_is_free(&candidate) {
        return candidate;
    }
    let path = Path::new(name);
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("upload");
    let extension = path.extension().and_then(|value| value.to_str());
    for attempt in 1..1_000 {
        let filename = match extension {
            Some(extension) => format!("{stem}-{attempt}.{extension}"),
            None => format!("{stem}-{attempt}"),
        };
        let candidate = directory.join(filename);
        if path_is_free(&candidate) {
            return candidate;
        }
    }
    directory.join(format!("{stem}-{}.upload", Uuid::now_v7()))
}

fn stream_path_upload(directory: &Path, path: &str) -> Result<UploadedFile, String> {
    let canonical =
        std::fs::canonicalize(path).map_err(|error| format!("upload source {path}: {error}"))?;
    let metadata = std::fs::metadata(&canonical).map_err(|error| error.to_string())?;
    if !metadata.is_file() {
        return Err(format!("uploads must be files, not folders: {path}"));
    }
    if metadata.len() > MAX_UPLOAD_BYTES {
        return Err(format!(
            "{} is {} bytes; the maximum upload is {} bytes",
            canonical.display(),
            metadata.len(),
            MAX_UPLOAD_BYTES
        ));
    }
    let name = safe_upload_name(
        canonical
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("upload"),
    )?;
    let destination = unique_upload_path(directory, &name);
    std::fs::copy(&canonical, &destination).map_err(|error| format!("{name}: {error}"))?;
    Ok(UploadedFile {
        name: destination
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(&name)
            .to_owned(),
        path: destination.to_string_lossy().into_owned(),
        bytes: metadata.len(),
    })
}

fn write_uploads(directory: &Path, sources: &[UploadSource]) -> Result<UploadReport, String> {
    if sources.is_empty() {
        return Err("no documents selected".to_owned());
    }
    if sources.len() > MAX_UPLOAD_FILES {
        return Err(format!(
            "upload at most {MAX_UPLOAD_FILES} documents at once"
        ));
    }
    std::fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    let mut report = UploadReport::default();
    for source in sources {
        if let Some(path) = source
            .path
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            // Copy from disk instead of buffering the whole file so large media
            // uploads do not need to fit in memory.
            match stream_path_upload(directory, path) {
                Ok(file) => report.files.push(file),
                Err(error) => report.skipped.push(error),
            }
            continue;
        }
        let (name, bytes) = match load_upload(source, MAX_UPLOAD_BYTES) {
            Ok(upload) => upload,
            Err(error) => {
                report.skipped.push(error);
                continue;
            }
        };
        let destination = unique_upload_path(directory, &name);
        match std::fs::write(&destination, &bytes) {
            Ok(()) => report.files.push(UploadedFile {
                name: destination
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or(&name)
                    .to_owned(),
                path: destination.to_string_lossy().into_owned(),
                bytes: bytes.len() as u64,
            }),
            Err(error) => report.skipped.push(format!("{name}: {error}")),
        }
    }
    Ok(report)
}

fn uploaded_references_directory(workspace_root: &Path) -> Result<PathBuf, String> {
    let workspaces = default_workspaces_directory()?;
    let base = match workspaces.parent() {
        Some(parent) => parent.join("references"),
        None => workspaces.join("references"),
    };
    std::fs::create_dir_all(&base).map_err(|error| error.to_string())?;
    let slug = project_slug(workspace_name(workspace_root));
    for attempt in 0..1_000 {
        let folder = if attempt == 0 {
            slug.clone()
        } else {
            format!("{slug}-{}", attempt + 1)
        };
        let candidate = base.join(folder);
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
    Err("could not find an available reference directory".to_owned())
}

#[tauri::command]
fn import_workspace_files(
    workspace_path: String,
    files: Vec<UploadSource>,
) -> Result<UploadReport, String> {
    let root = PathBuf::from(&workspace_path);
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    write_uploads(&root, &files)
}

#[tauri::command]
async fn import_reference_files(
    state: State<'_, AppState>,
    workspace_path: String,
    files: Vec<UploadSource>,
) -> Result<ReferenceUpload, String> {
    let store = state.store.clone();
    off_thread(move || store_reference_files(store, workspace_path, files)).await
}

fn store_reference_files(
    store: std::sync::Arc<std::sync::Mutex<Store>>,
    workspace_path: String,
    files: Vec<UploadSource>,
) -> Result<ReferenceUpload, String> {
    let root = PathBuf::from(&workspace_path);
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let existing = store
        .list_project_repos(workspace)
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|repo| repo.name == UPLOADED_REFERENCES_NAME && Path::new(&repo.path).is_dir());
    let (repo, directory) = match existing {
        Some(repo) => {
            let directory = PathBuf::from(&repo.path);
            (repo, directory)
        }
        None => {
            let directory = uploaded_references_directory(&root)?;
            let repo = store
                .add_project_repo(
                    workspace,
                    UPLOADED_REFERENCES_NAME,
                    &directory.to_string_lossy(),
                )
                .map_err(|error| error.to_string())?;
            (repo, directory)
        }
    };
    let report = write_uploads(&directory, &files)?;
    Ok(ReferenceUpload { repo, report })
}

fn import_knowledge_source(
    store: &Store,
    workspace: WorkspaceId,
    source: &UploadSource,
    tags: &[String],
) -> Result<KnowledgeDocumentSummary, String> {
    let (name, bytes) = load_upload(source, MAX_KNOWLEDGE_BYTES)?;
    let content =
        String::from_utf8(bytes).map_err(|error| format!("{name} must be UTF-8 text: {error}"))?;
    let source_hash = blake3::hash(content.as_bytes()).to_hex().to_string();
    let title = Path::new(&name)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("Imported document")
        .to_owned();
    let kind = Path::new(&name)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("file")
        .to_owned();
    store
        .add_knowledge_document_with_confidence(
            workspace,
            &title,
            &kind,
            source.path.as_deref(),
            tags.to_vec(),
            &content,
            "verified",
            Some(&source_hash),
        )
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn import_knowledge_files(
    state: State<'_, AppState>,
    workspace_path: String,
    files: Vec<UploadSource>,
    tags: Vec<String>,
) -> Result<KnowledgeImportReport, String> {
    if files.is_empty() {
        return Err("no documents selected".to_owned());
    }
    if files.len() > MAX_UPLOAD_FILES {
        return Err(format!(
            "import at most {MAX_UPLOAD_FILES} documents at once"
        ));
    }
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, &workspace_path)?;
    let mut report = KnowledgeImportReport::default();
    for source in &files {
        match import_knowledge_source(&store, workspace, source, &tags) {
            Ok(document) => report.documents.push(document),
            Err(error) => report.skipped.push(error),
        }
    }
    Ok(report)
}

fn xml_attr(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
}

fn truncate_chars(text: &str, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text.to_owned();
    }
    let mut end = max_bytes;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}…", &text[..end])
}

fn ocr_image(path: &Path) -> Option<String> {
    let mut command = std::process::Command::new("tesseract");
    command
        .arg(path)
        .arg("stdout")
        .arg("-l")
        .arg("eng")
        .arg("--psm")
        .arg("3");
    let output = crate::process::run_bounded(
        &mut command,
        OCR_TIMEOUT,
        &format!("tesseract {}", path.display()),
    )
    .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if text.is_empty() {
        None
    } else {
        Some(truncate_chars(&text, 64 * 1024))
    }
}

fn pdf_text(path: &Path) -> Option<String> {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        pdf_extract::extract_text(path)
    }))
    .ok()?
    .ok()?;
    let text = result.trim().to_owned();
    if text.is_empty() {
        None
    } else {
        Some(truncate_chars(&text, 96 * 1024))
    }
}

fn attachment_extension(path: &Path) -> String {
    path.extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
}

/// Stages chat attachments in `<workspace>/uploads` so any file type is kept in
/// the workspace, then builds the model-visible message. Text files are inlined
/// within a bounded budget; everything else is referenced by workspace path and
/// stays available to the file tools.
fn compose_chat_message(
    workspace_root: &Path,
    prompt: &str,
    attachments: &[UploadSource],
) -> Result<(String, Vec<UploadedFile>), String> {
    if attachments.is_empty() {
        return Ok((prompt.to_owned(), Vec::new()));
    }
    if attachments.len() > MAX_CHAT_ATTACHMENTS {
        return Err(format!(
            "attach at most {MAX_CHAT_ATTACHMENTS} files per message"
        ));
    }
    let report = write_uploads(&workspace_root.join("uploads"), attachments)?;
    if report.files.is_empty() {
        return Err(if report.skipped.is_empty() {
            "no files could be attached".to_owned()
        } else {
            report.skipped.join("; ")
        });
    }
    let mut message = prompt.trim_end().to_owned();
    if !message.is_empty() {
        message.push_str(
            "

",
        );
    }
    message.push_str(
        "The user attached these files. They are saved inside the workspace and can be read with the file tools:
",
    );
    let mut inline_budget = MAX_CHAT_ATTACHMENT_INLINE_BYTES;
    let mut extracted_budget = MAX_CHAT_EXTRACTED_BYTES;
    let mut blocks = Vec::new();
    for file in &report.files {
        let path = Path::new(&file.path);
        let relative = path
            .strip_prefix(workspace_root)
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_else(|_| file.path.clone());
        message.push_str(&format!(
            "- {relative} ({} bytes)
",
            file.bytes
        ));
        let extension = attachment_extension(path);
        let is_image = matches!(
            extension.as_str(),
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "svg"
        );
        let is_pdf = extension == "pdf";
        let Ok(bytes) = std::fs::read(path) else {
            continue;
        };
        if is_image {
            let ocr = if file.bytes <= MAX_OCR_IMAGE_BYTES {
                ocr_image(path)
            } else {
                None
            };
            let body = match ocr {
                Some(text) if extracted_budget > 0 => {
                    let text = truncate_chars(&text, extracted_budget);
                    extracted_budget = extracted_budget.saturating_sub(text.len());
                    text
                }
                _ => "[image attached; no text was extracted]".to_owned(),
            };
            blocks.push(format!(
                "\n<attached_image name=\"{}\">\n{}\n</attached_image>\n",
                xml_attr(&relative),
                body
            ));
            continue;
        }
        if is_pdf {
            let extracted = if file.bytes <= MAX_PDF_BYTES {
                pdf_text(path)
            } else {
                None
            };
            let body = match extracted {
                Some(text) if extracted_budget > 0 => {
                    let text = truncate_chars(&text, extracted_budget);
                    extracted_budget = extracted_budget.saturating_sub(text.len());
                    text
                }
                _ => "[PDF attached; no text could be extracted]".to_owned(),
            };
            blocks.push(format!(
                "\n<attached_pdf name=\"{}\">\n{}\n</attached_pdf>\n",
                xml_attr(&relative),
                body
            ));
            continue;
        }
        let Ok(content) = String::from_utf8(bytes) else {
            blocks.push(format!(
                "\n<attached_binary name=\"{}\">binary attachment; use the file tools to inspect it</attached_binary>\n",
                xml_attr(&relative)
            ));
            continue;
        };
        if content.len() > inline_budget {
            continue;
        }
        inline_budget = inline_budget.saturating_sub(content.len());
        blocks.push(format!(
            "\n<attached_document name=\"{}\">\n{}\n</attached_document>\n",
            xml_attr(&relative),
            content.trim_end()
        ));
    }
    for block in blocks {
        message.push_str(&block);
    }
    Ok((message, report.files))
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
async fn project_files(workspace_path: String) -> Result<Vec<String>, String> {
    off_thread(move || scan_project_files(workspace_path)).await
}

fn scan_project_files(workspace_path: String) -> Result<Vec<String>, String> {
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
async fn search_project(
    state: State<'_, AppState>,
    workspace_path: String,
    query: String,
    limit: Option<u32>,
) -> Result<Vec<ProjectHit>, String> {
    let store = state.store.clone();
    off_thread(move || {
        let store = store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
        let workspace = project_workspace(&store, &workspace_path)?;
        store
            .retrieve_project(workspace, &query, limit.unwrap_or(12).clamp(1, 50))
            .map_err(|error| error.to_string())
    })
    .await
}

#[tauri::command]
async fn project_file(
    workspace_path: String,
    path: String,
) -> Result<preview::ProjectFileView, String> {
    off_thread(move || {
        let workspace = WorkspaceService::new(PathBuf::from(&workspace_path));
        let resolved = workspace
            .resolve(Path::new(&path))
            .map_err(|error| error.to_string())?;
        let relative = resolved
            .strip_prefix(workspace.root())
            .unwrap_or(&resolved)
            .to_string_lossy()
            .into_owned();
        preview::preview_file(&resolved, relative)
    })
    .await
}

/// Resolves a UI reference to an existing path inside the workspace or one of
/// its registered reference folders.
fn resolve_reveal_target(
    workspace_root: &Path,
    reference_roots: &[PathBuf],
    path: &str,
) -> Result<PathBuf, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err("path must not be empty".to_owned());
    }
    let candidate = Path::new(trimmed);
    let candidate = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        workspace_root.join(candidate)
    };
    let target = candidate
        .canonicalize()
        .map_err(|error| format!("path is not available: {error}"))?;
    let mut roots = Vec::with_capacity(reference_roots.len() + 1);
    if let Ok(root) = workspace_root.canonicalize() {
        roots.push(root);
    }
    for reference in reference_roots {
        if let Ok(root) = reference.canonicalize() {
            roots.push(root);
        }
    }
    if !roots.iter().any(|root| target.starts_with(root)) {
        return Err("path is outside this project".to_owned());
    }
    Ok(target)
}

fn reference_roots(
    state: &State<'_, AppState>,
    workspace_path: &str,
) -> Result<Vec<PathBuf>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let workspace = project_workspace(&store, workspace_path)?;
    Ok(store
        .list_project_repos(workspace)
        .map(|repos| {
            repos
                .into_iter()
                .map(|repo| PathBuf::from(repo.path))
                .collect()
        })
        .unwrap_or_default())
}

fn open_in_file_manager(target: &Path) -> Result<(), String> {
    let containing = if target.is_dir() {
        target.to_path_buf()
    } else {
        target
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| target.to_path_buf())
    };
    let mut command = reveal_command(target, &containing);
    command
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not open the file manager: {error}"))
}

#[cfg(target_os = "macos")]
fn reveal_command(target: &Path, containing: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("open");
    if target.is_dir() {
        command.arg(containing);
    } else {
        command.arg("-R").arg(target);
    }
    command
}

#[cfg(target_os = "windows")]
fn reveal_command(target: &Path, containing: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("explorer");
    if target.is_dir() {
        command.arg(containing);
    } else {
        command.arg(format!("/select,{}", target.display()));
    }
    command
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn reveal_command(_target: &Path, containing: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("xdg-open");
    command.arg(containing);
    command
}

fn open_with_system(target: &Path) -> Result<(), String> {
    let mut command = open_command(target);
    command
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not open the file: {error}"))
}

#[cfg(target_os = "macos")]
fn open_command(target: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("open");
    command.arg(target);
    command
}

#[cfg(target_os = "windows")]
fn open_command(target: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("cmd");
    command.args(["/C", "start", ""]).arg(target);
    command
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn open_command(target: &Path) -> std::process::Command {
    let mut command = std::process::Command::new("xdg-open");
    command.arg(target);
    command
}

/// Opens the OS file manager at a workspace or reference-folder path.
#[tauri::command]
fn reveal_path(
    state: State<'_, AppState>,
    workspace_path: String,
    path: String,
) -> Result<String, String> {
    let raw_root = PathBuf::from(&workspace_path);
    if !raw_root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let references = reference_roots(&state, &workspace_path)?;
    let target = resolve_reveal_target(&raw_root, &references, &path)?;
    open_in_file_manager(&target)?;
    Ok(target.to_string_lossy().into_owned())
}

/// Opens a workspace or reference-file with the OS default application.
#[tauri::command]
fn open_path(
    state: State<'_, AppState>,
    workspace_path: String,
    path: String,
) -> Result<String, String> {
    let raw_root = PathBuf::from(&workspace_path);
    if !raw_root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let references = reference_roots(&state, &workspace_path)?;
    let target = resolve_reveal_target(&raw_root, &references, &path)?;
    if !target.is_file() {
        return Err("only files can be opened with the system handler".to_owned());
    }
    open_with_system(&target)?;
    Ok(target.to_string_lossy().into_owned())
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
    view_for(&store, session)
}

fn view_for(store: &Store, session: SessionId) -> Result<SessionView, String> {
    let _ = repair_interrupted_turn(store, session);
    let messages = store
        .load_messages(session)
        .map_err(|error| error.to_string())?;
    let turns = store.load_turns(session).unwrap_or_default();
    let (messages, total_duration_ms) = build_ui_messages(&messages, &turns);
    let origin = store.session_fork_origin(session).unwrap_or(None);
    Ok(SessionView {
        session_id: session.to_string(),
        forked_from_session: origin.map(|(source, _)| source.to_string()),
        forked_from_seq: origin.map(|(_, seq)| seq),
        messages,
        total_duration_ms,
    })
}

/// Branch the conversation before `seq`, which must be a user message.
#[tauri::command]
fn fork_session(
    state: State<'_, AppState>,
    session_id: String,
    seq: u64,
    title: Option<String>,
) -> Result<SessionView, String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let fork = store
        .fork_session(session, seq, title)
        .map_err(|error| error.to_string())?;
    view_for(&store, fork)
}

/// Pure transcript projection: stored messages and turns become the bubbles
/// the UI renders, including the tool cards the live stream produced.
fn build_ui_messages(
    messages: &[SessionMessage],
    turns: &[openbad_store::types::SessionTurn],
) -> (Vec<UiMessage>, u64) {
    let (turn_durations, total_duration_ms) = summarize_turn_durations(turns);
    // Tool results are separate stored messages; the transcript shows them on
    // the assistant message that requested them.
    let mut tool_results: std::collections::HashMap<String, StoredToolResult> =
        std::collections::HashMap::new();
    for message in messages {
        if message.role != "tool" || message.source.as_deref() == Some("compaction") {
            continue;
        }
        let Some(call_id) = message.content.get("tool_call_id").and_then(Value::as_str) else {
            continue;
        };
        tool_results.insert(
            call_id.to_owned(),
            StoredToolResult {
                content: message
                    .content
                    .get("content")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                is_error: message
                    .content
                    .get("is_error")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                duration_ms: message.content.get("duration_ms").and_then(Value::as_u64),
                interrupted: message
                    .content
                    .get("interrupted")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            },
        );
    }
    let mut ui_messages: Vec<UiMessage> = Vec::new();
    let mut ui_turns: Vec<Option<u32>> = Vec::new();
    for message in messages {
        // Compaction checkpoints are model context, not chat bubbles. The
        // original messages remain in the transcript for audit.
        if message.source.as_deref() == Some("compaction") {
            continue;
        }
        let ui = match message.role.as_str() {
            "user" => {
                let payload = &message.content;
                Some(UiMessage {
                    seq: message.seq,
                    role: "user".to_owned(),
                    text: payload
                        .get("display")
                        .and_then(Value::as_str)
                        .or_else(|| payload.get("content").and_then(Value::as_str))
                        .unwrap_or_default()
                        .to_owned(),
                    attachments: payload
                        .get("attachments")
                        .and_then(Value::as_array)
                        .map(|items| {
                            items
                                .iter()
                                .filter_map(Value::as_str)
                                .map(str::to_owned)
                                .collect()
                        })
                        .unwrap_or_default(),
                    reasoning: None,
                    duration_ms: None,
                    interrupted: false,
                    tool_calls: Vec::new(),
                })
            }
            "assistant" => {
                let turn = message.turn;
                let Some(chat) = chat_message_from_session(message).ok() else {
                    continue;
                };
                let tool_calls = chat
                    .tool_calls
                    .unwrap_or_default()
                    .into_iter()
                    .map(|call| {
                        let result = tool_results.get(&call.id);
                        UiToolCall {
                            status: match result {
                                Some(result) if result.interrupted => "interrupted",
                                Some(result) if result.is_error => "failed",
                                Some(_) => "settled",
                                None => "queued",
                            }
                            .to_owned(),
                            call_id: call.id,
                            name: call.function.name,
                            arguments: call.function.arguments,
                            result: result.map(|item| item.content.clone()).unwrap_or_default(),
                            is_error: result.map(|item| item.is_error).unwrap_or(false),
                            duration_ms: result.and_then(|item| item.duration_ms),
                        }
                    })
                    .collect::<Vec<_>>();
                Some(UiMessage {
                    seq: message.seq,
                    role: "assistant".to_owned(),
                    text: chat
                        .content
                        .filter(|text| !text.is_empty())
                        .unwrap_or_default(),
                    attachments: Vec::new(),
                    reasoning: chat.reasoning_content,
                    duration_ms: turn.and_then(|turn| turn_durations.get(&turn).copied()),
                    interrupted: false,
                    tool_calls,
                })
            }
            _ => None,
        };
        if let Some(ui) = ui {
            // The live stream keeps one bubble per turn; stored messages arrive
            // one per step, so consecutive assistant messages of the same turn
            // merge here to reload the same transcript the user watched.
            let same_turn = message.turn.is_some()
                && ui.role == "assistant"
                && ui_turns.last().copied().flatten() == message.turn
                && ui_messages
                    .last()
                    .is_some_and(|last| last.role == "assistant");
            if same_turn {
                let last = ui_messages.last_mut().expect("checked above");
                if !ui.text.is_empty() {
                    if !last.text.is_empty() {
                        last.text.push_str("\n\n");
                    }
                    last.text.push_str(&ui.text);
                }
                if let Some(reasoning) = ui.reasoning.filter(|text| !text.is_empty()) {
                    match last.reasoning.as_mut() {
                        Some(existing) if !existing.is_empty() => {
                            existing.push_str("\n\n");
                            existing.push_str(&reasoning);
                        }
                        Some(existing) => *existing = reasoning,
                        None => last.reasoning = Some(reasoning),
                    }
                }
                last.tool_calls.extend(ui.tool_calls);
            } else {
                ui_messages.push(ui);
                ui_turns.push(message.turn);
            }
        }
    }
    let interrupted_turns: HashSet<u32> = turns
        .iter()
        .filter(|turn| turn.reason.as_deref() == Some("interrupted"))
        .map(|turn| turn.turn)
        .collect();
    let mut marked = HashSet::new();
    for (message, turn) in ui_messages.iter_mut().zip(ui_turns.iter()).rev() {
        let Some(turn) = turn else {
            continue;
        };
        if message.role == "assistant" && interrupted_turns.contains(turn) && marked.insert(*turn) {
            message.interrupted = true;
        }
    }
    if let Some(last) = turns.last() {
        let has_assistant = ui_messages
            .iter()
            .zip(ui_turns.iter())
            .any(|(message, turn)| *turn == Some(last.turn) && message.role == "assistant");
        if interrupted_turns.contains(&last.turn) && !has_assistant {
            ui_messages.push(UiMessage {
                seq: 0,
                role: "assistant".to_owned(),
                text: "Stopped before any output.".to_owned(),
                attachments: Vec::new(),
                reasoning: None,
                duration_ms: None,
                interrupted: true,
                tool_calls: Vec::new(),
            });
            ui_turns.push(Some(last.turn));
        }
    }
    for message in ui_messages.iter_mut() {
        if message.role == "assistant" && message.text.is_empty() && message.tool_calls.is_empty() {
            message.text = "(tool activity)".to_owned();
        }
    }
    keep_last_assistant_duration_per_turn(&mut ui_messages, &ui_turns);
    (ui_messages, total_duration_ms)
}

fn summarize_turn_durations(
    turns: &[openbad_store::types::SessionTurn],
) -> (std::collections::HashMap<u32, u64>, u64) {
    let mut durations = std::collections::HashMap::new();
    let mut total = 0_u64;
    for turn in turns {
        if let Some(ended) = turn.ended_at {
            let duration = u64::try_from(ended.saturating_sub(turn.started_at)).unwrap_or(0);
            total = total.saturating_add(duration);
            durations.insert(turn.turn, duration);
        }
    }
    (durations, total)
}

/// Only the final assistant message of a turn carries the turn duration, so a
/// turn with tool activity reads as one timed response instead of several.
fn keep_last_assistant_duration_per_turn(messages: &mut [UiMessage], turns: &[Option<u32>]) {
    let mut seen_turns = std::collections::HashSet::new();
    for (message, turn) in messages.iter_mut().zip(turns.iter()).rev() {
        let Some(turn) = turn else {
            continue;
        };
        if message.role == "assistant" && seen_turns.insert(*turn) {
            continue;
        }
        message.duration_ms = None;
    }
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
fn set_session_access_mode(
    state: State<'_, AppState>,
    session_id: String,
    access_mode: String,
) -> Result<(), String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store
        .set_session_access_mode(session, &access_mode)
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
fn cancel_chat(state: State<'_, AppState>, run_id: String) -> Result<(), String> {
    cancel_registered_run(&state.runs, &run_id)?;
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

#[derive(Debug, Clone, Serialize)]
pub struct McpServerView {
    pub name: String,
    pub transport: String,
    pub command: Option<String>,
    pub args: Vec<String>,
    pub env: std::collections::BTreeMap<String, String>,
    pub cwd: Option<String>,
    pub url: Option<String>,
    pub allowed_tools: Vec<String>,
    pub enabled: bool,
    pub timeout_seconds: Option<u64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct McpToolView {
    pub server: String,
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct McpServerChangeView {
    pub server: McpServerView,
    pub tools: Vec<McpToolView>,
    pub warning: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct McpServerInput {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub transport: Option<String>,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Option<Vec<String>>,
    #[serde(default)]
    pub env: Option<std::collections::BTreeMap<String, String>>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub allowed_tools: Option<Vec<String>>,
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub timeout_seconds: Option<u64>,
}

fn mcp_server_view(state: &openbad_mcp::McpServerState) -> McpServerView {
    let config = &state.config;
    McpServerView {
        name: config.name.clone(),
        transport: match config.transport {
            openbad_mcp::McpTransport::Stdio => "stdio".to_owned(),
            openbad_mcp::McpTransport::Http => "http".to_owned(),
        },
        command: config.command.clone(),
        args: config.args.clone(),
        env: config.env.clone(),
        cwd: config.cwd.clone(),
        url: config.url.clone(),
        allowed_tools: config.allowed_tools.clone(),
        enabled: state.enabled,
        timeout_seconds: config.timeout_seconds,
    }
}

fn mcp_tool_view(tool: openbad_mcp::McpToolInfo) -> McpToolView {
    McpToolView {
        server: tool.server,
        name: tool.name,
        description: tool.description,
    }
}

fn mcp_input_spec(name: String, input: McpServerInput) -> McpServerSpec {
    McpServerSpec {
        name,
        transport: input.transport,
        command: input.command,
        args: input.args,
        env: input.env,
        cwd: input.cwd,
        url: input.url,
        allowed_tools: input.allowed_tools,
        enabled: input.enabled,
        timeout_seconds: input.timeout_seconds,
    }
}

#[tauri::command]
fn mcp_servers(
    state: State<'_, AppState>,
    workspace_path: String,
) -> Result<Vec<McpServerView>, String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    manager
        .servers()
        .map(|servers| servers.iter().map(mcp_server_view).collect())
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn add_mcp_server(
    state: State<'_, AppState>,
    workspace_path: String,
    server: McpServerInput,
) -> Result<McpServerChangeView, String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    let name = server.name.trim().to_owned();
    if name.is_empty() {
        return Err("server name must not be empty".to_owned());
    }
    let spec = mcp_input_spec(name.clone(), server);
    tauri::async_runtime::spawn_blocking(move || {
        let config = mcp_server_config_from_spec(&spec, None)?;
        let state = manager
            .add_server(config, spec.enabled.unwrap_or(true))
            .map_err(|error| error.to_string())?;
        let (tools, warning) = probe_mcp_server(&manager, &name);
        Ok(McpServerChangeView {
            server: mcp_server_view(&state),
            tools: tools
                .into_iter()
                .map(|tool| McpToolView {
                    server: tool.server,
                    name: tool.name,
                    description: tool.description,
                })
                .collect(),
            warning,
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn update_mcp_server(
    state: State<'_, AppState>,
    workspace_path: String,
    name: String,
    server: McpServerInput,
) -> Result<McpServerChangeView, String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    let spec = mcp_input_spec(name.clone(), server);
    tauri::async_runtime::spawn_blocking(move || {
        let current = manager
            .servers()
            .map_err(|error| error.to_string())?
            .into_iter()
            .find(|server| server.config.name == name)
            .ok_or_else(|| format!("unknown MCP server: {name}"))?;
        let config = mcp_server_config_from_spec(&spec, Some(current.config))?;
        manager
            .update_server(&name, config)
            .map_err(|error| error.to_string())?;
        let state = match spec.enabled {
            Some(enabled) => manager
                .set_server_enabled(&name, enabled)
                .map_err(|error| error.to_string())?,
            None => manager
                .servers()
                .map_err(|error| error.to_string())?
                .into_iter()
                .find(|server| server.config.name == name)
                .ok_or_else(|| format!("unknown MCP server: {name}"))?,
        };
        let (tools, warning) = probe_mcp_server(&manager, &name);
        Ok(McpServerChangeView {
            server: mcp_server_view(&state),
            tools: tools
                .into_iter()
                .map(|tool| McpToolView {
                    server: tool.server,
                    name: tool.name,
                    description: tool.description,
                })
                .collect(),
            warning,
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn remove_mcp_server(
    state: State<'_, AppState>,
    workspace_path: String,
    name: String,
) -> Result<(), String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    tauri::async_runtime::spawn_blocking(move || {
        manager
            .remove_server(&name)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn set_mcp_server_enabled(
    state: State<'_, AppState>,
    workspace_path: String,
    name: String,
    enabled: bool,
) -> Result<McpServerView, String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    manager
        .set_server_enabled(&name, enabled)
        .map(|server| mcp_server_view(&server))
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn test_mcp_server(
    state: State<'_, AppState>,
    workspace_path: String,
    name: String,
) -> Result<Vec<McpToolView>, String> {
    let manager = state.mcp.get(Path::new(&workspace_path))?;
    tauri::async_runtime::spawn_blocking(move || {
        manager
            .probe(&name)
            .map(|tools| tools.into_iter().map(mcp_tool_view).collect())
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

fn update_service() -> openbad_update::UpdateService {
    let repo = std::env::var("OPENBAD_UPDATE_REPO")
        .ok()
        .filter(|repo| !repo.trim().is_empty())
        .unwrap_or_else(|| openbad_update::DEFAULT_REPO.to_owned());
    let mut service = openbad_update::UpdateService::new(repo)
        .with_self_installable(openbad_update::running_app_image().is_some());
    if let Ok(api_base) = std::env::var("OPENBAD_UPDATE_API") {
        if !api_base.trim().is_empty() {
            service = service.with_api_base(api_base);
        }
    }
    service
}

#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<openbad_update::UpdateStatus, String> {
    let current = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        update_service()
            .check(&current)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn install_update(
    app: tauri::AppHandle,
    version: String,
) -> Result<openbad_update::InstallOutcome, String> {
    let current = app.package_info().version.to_string();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        let service = update_service();
        let status = service.check(&current).map_err(|error| error.to_string())?;
        let openbad_update::UpdateStatus::Available {
            latest,
            release,
            asset,
            installer,
            ..
        } = status
        else {
            return Err("no update is available".to_owned());
        };
        if latest != version {
            return Err("the available update changed; check again".to_owned());
        }
        let downloads = openbad_update::downloads_directory();
        let downloaded = service
            .download(&release, &asset, &downloads)
            .map_err(|error| error.to_string())?;
        let app_image = openbad_update::running_app_image();
        service
            .install(
                &asset,
                installer,
                &downloaded,
                app_image.as_deref(),
                &downloads,
            )
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;

    if let openbad_update::InstallOutcome::AppImageReplaced { path } = &outcome {
        std::process::Command::new(path)
            .spawn()
            .map_err(|error| format!("could not restart the updated app: {error}"))?;
        app.exit(0);
    }
    Ok(outcome)
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatInput {
    /// UI-generated identifier for this turn, used to scope cancellation,
    /// steering, and one-run-per-project enforcement.
    #[serde(default)]
    pub run_id: String,
    pub session_id: Option<String>,
    pub workspace_path: String,
    pub message: String,
    /// Re-run the failed turn against the existing surface instead of
    /// appending another user message.
    #[serde(default)]
    pub resume: bool,
    pub allow_writes: bool,
    pub allow_commands: bool,
    /// Explicit policy preset. Falls back to the boolean flags when absent.
    #[serde(default)]
    pub policy: Option<String>,
    /// Ask, plan, or agent. Ask and plan override the access policy.
    #[serde(default)]
    pub mode: Option<String>,
    pub max_steps: Option<u32>,
    pub model: Option<String>,
    pub thinking: Option<bool>,
    pub reasoning_effort: Option<String>,
    #[serde(default)]
    pub attachments: Vec<UploadSource>,
}

#[tauri::command]
fn steer_chat(state: State<'_, AppState>, run_id: String, message: String) -> Result<(), String> {
    let message = message.trim();
    if message.is_empty() {
        return Err("steering message must not be empty".to_owned());
    }
    if steer_registered_run(&state.runs, &run_id, message)? {
        Ok(())
    } else {
        Err("no active turn to steer".to_owned())
    }
}

#[tauri::command]
fn respond_to_approval(
    state: State<'_, AppState>,
    request_id: String,
    decision: String,
) -> Result<(), String> {
    let id = Uuid::parse_str(&request_id)
        .map_err(|error| format!("invalid approval request id: {error}"))?;
    let decision = match decision.as_str() {
        "allow_once" => ApprovalDecision::AllowOnce,
        "allow_session" => ApprovalDecision::AllowSession,
        "allow_workspace" => ApprovalDecision::AllowWorkspace,
        "deny" => ApprovalDecision::Deny,
        other => return Err(format!("unknown approval decision: {other}")),
    };
    let sender = state
        .pending_approvals
        .lock()
        .map_err(|error| format!("approval lock poisoned: {error}"))?
        .remove(&id)
        .ok_or_else(|| "no approval request with that id".to_owned())?;
    sender
        .send(decision)
        .map_err(|_| "the approval request expired".to_owned())
}

#[tauri::command]
async fn chat(
    state: State<'_, AppState>,
    request: ChatInput,
    on_event: Channel<AgentEvent>,
) -> Result<ChatReply, String> {
    let ChatInput {
        run_id,
        session_id,
        workspace_path,
        message,
        allow_writes,
        allow_commands,
        policy,
        mode,
        resume,
        max_steps,
        model,
        thinking,
        reasoning_effort,
        attachments,
    } = request;
    let prompt = message.trim().to_owned();
    if !resume && prompt.is_empty() && attachments.is_empty() {
        return Err("message must not be empty".to_owned());
    }
    let started = std::time::Instant::now();
    let mode = mode.unwrap_or_else(|| "agent".to_owned());
    let policy = match mode.as_str() {
        "ask" => ToolPolicy::ReadOnly,
        "plan" => ToolPolicy::WritesOnly,
        _ => policy
            .as_deref()
            .and_then(ToolPolicy::from_access_mode)
            .unwrap_or_else(|| ToolPolicy::from_flags(allow_writes, allow_commands)),
    };
    let extra_instructions = match mode.as_str() {
        "ask" => Some(
            "You are in Ask mode. Answer questions about the workspace and do not modify files or run commands; describe any change instead.".to_owned(),
        ),
        "plan" => Some(
            "You are in Plan mode. Investigate the workspace and produce a concrete, ordered plan, using the plan tools to persist it. Do not run commands or change non-plan files.".to_owned(),
        ),
        _ => None,
    };
    let workspace_path = PathBuf::from(workspace_path);
    if !workspace_path.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            workspace_path.display()
        ));
    }
    let run_id = if run_id.trim().is_empty() {
        Uuid::now_v7().to_string()
    } else {
        run_id
    };
    // This guard lives until the end of the turn and removes the run entry on
    // every exit path, including errors and cancellation.
    let run_guard = begin_run(&state.runs, &run_id, session_id.as_deref())?;
    let cancel = run_guard.cancel_token();
    let title_source = if prompt.is_empty() {
        "Attached documents".to_owned()
    } else {
        prompt.clone()
    };
    let (message, staged_attachments) = if resume {
        (String::new(), Vec::new())
    } else {
        compose_chat_message(&workspace_path, &prompt, &attachments)?
    };

    let api_key = resolve_api_key(&state)?;
    let redactions = vec![api_key.clone()];

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
                let title = truncate_title(&title_source);
                let session = store
                    .create_session(Some(workspace), Some(&title))
                    .map_err(|error| error.to_string())?;
                (session, workspace.to_string())
            }
        };

        repair_interrupted_turn(&store, session)?;
        if mode == "agent" {
            store
                .set_session_access_mode(session, policy.access_mode())
                .map_err(|error| error.to_string())?;
        }

        let turn = store
            .load_turns(session)
            .map_err(|error| error.to_string())?
            .len() as u32
            + 1;
        let (mut history, surface_seqs) = load_history(&store, session)?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(turn),
            )
            .map_err(|error| error.to_string())?;
        if !resume {
            store
                .append_event(
                    session,
                    NewEvent::new(SessionEventKind::UserMessage)
                        .with_turn(turn)
                        .with_step(1)
                        .with_payload(json!({
                            "content": message,
                            "display": prompt,
                            "attachments": staged_attachments
                                .iter()
                                .map(|attachment| {
                                    Path::new(&attachment.path)
                                        .strip_prefix(&workspace_path)
                                        .map(|path| path.to_string_lossy().into_owned())
                                        .unwrap_or_else(|_| attachment.name.clone())
                                })
                                .collect::<Vec<_>>(),
                        })),
                )
                .map_err(|error| error.to_string())?;
            history.push(ChatMessage::user(message.clone()));
        }

        let user_id = ensure_user_id(&store)?;
        let indexed = workspace_id
            .parse()
            .ok()
            .and_then(|workspace: WorkspaceId| store.workspace_index_summary(workspace).ok())
            .is_some_and(|summary| summary.files > 0);
        (
            session,
            workspace_id,
            (history, user_id, surface_seqs),
            turn,
            indexed,
        )
    };

    let (history, user_id, surface_seqs) = history;
    let base_url = std::env::var("OPENBAD_DEEPSEEK_BASE_URL")
        .unwrap_or_else(|_| "https://api.deepseek.com".to_owned());
    let model_name = model.unwrap_or_else(|| FLASH_MODEL.to_owned());
    let thinking_enabled = thinking.unwrap_or(true);
    let effort = reasoning_effort.map(|effort| match effort.as_str() {
        "low" => ReasoningEffort::Low,
        "max" => ReasoningEffort::Max,
        _ => ReasoningEffort::High,
    });
    let mut client = DeepSeekClient::new(ReqwestTransport::new(), api_key.clone())
        .with_model(model_name.clone())
        .with_base_url(base_url.clone())
        .with_user_id(user_id.clone());
    if let Some(thinking) = thinking {
        client = client.with_thinking(thinking);
    }
    if let Some(effort) = effort {
        client = client.with_reasoning_effort(effort);
    }
    let index_root = workspace_path.clone();
    let mcp_manager = state.mcp.get(Path::new(&workspace_path))?;
    let mut agent = default_agent(
        client,
        Arc::new(WorkspaceService::new(workspace_path.clone())),
        AgentConfig {
            policy,
            extra_instructions,
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
        let grants = {
            let store = state
                .store
                .lock()
                .map_err(|error| format!("store lock poisoned: {error}"))?;
            store
                .list_outside_access_grants(workspace_id)
                .map_err(|error| error.to_string())?
        };
        let subagent_grants: Vec<OutsideGrant> = grants
            .iter()
            .map(|grant| OutsideGrant {
                id: grant.id.clone(),
                path: PathBuf::from(&grant.path),
                write_access: grant.write_access,
                reason: grant.reason.clone(),
            })
            .collect();
        agent = agent
            .with_outside_grants(subagent_grants.clone())
            .with_lsp(Arc::new(DesktopLsp::new(workspace_path.clone())))
            .with_sessions(Arc::new(WorkspaceSessions {
                store: Arc::clone(&state.store),
                workspace_id,
            }))
            .with_mcp(Arc::new(DesktopMcp::new(Arc::clone(&mcp_manager))))
            .with_terminal(Arc::new(DesktopTerminal {
                manager: Arc::clone(&state.terminal),
            }))
            .with_subagents(Arc::new(SubagentRunner {
                settings: Arc::new(SubagentSettings {
                    store: Arc::clone(&state.store),
                    api_key: api_key.clone(),
                    workspace_id,
                    workspace_path: workspace_path.clone(),
                    parent_session: session,
                    model: model_name.clone(),
                    thinking: thinking_enabled,
                    reasoning_effort: effort,
                    base_url: base_url.clone(),
                    user_id: user_id.clone(),
                    grants: subagent_grants,
                    terminal: Arc::clone(&state.terminal),
                    mcp: Arc::clone(&mcp_manager),
                }),
                active: Arc::clone(&state.subagent_cancels),
            }));
    }
    agent = agent
        .with_inbox(run_guard.inbox())
        .with_approvals(Arc::new(DesktopApprovals {
            pending: Arc::clone(&state.pending_approvals),
            cancel: Arc::clone(&cancel),
            cache: Arc::clone(&state.approvals),
            session,
            workspace: workspace_path.to_string_lossy().into_owned(),
        }))
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
        cancel,
        compaction_reported: AtomicBool::new(false),
        tool_outcomes: std::sync::Mutex::new(std::collections::HashMap::new()),
        journal: Some(RunJournal {
            store: Arc::clone(&state.store),
            session,
            turn,
            surface_seqs,
        }),
    };
    let outcome = agent.run_with_observer(history, &observer).await;
    run_guard.finish_inbox();
    let run = match outcome {
        Ok(run) => run,
        Err(error) => {
            if let Ok(store) = state.store.lock() {
                if !matches!(error, AgentError::Cancelled) {
                    let _ = store.append_event(
                        session,
                        NewEvent::new(SessionEventKind::Error)
                            .with_turn(turn)
                            .with_payload(json!({"message": error.to_string()})),
                    );
                }
                let reason = if matches!(error, AgentError::Cancelled) {
                    "cancelled"
                } else {
                    "error"
                };
                let discarded = if matches!(error, AgentError::Cancelled) {
                    run_guard
                        .inbox()
                        .discard_pending()
                        .into_iter()
                        .map(|entry| entry.id.to_string())
                        .collect::<Vec<_>>()
                } else {
                    Vec::new()
                };
                let _ = store.append_event(
                    session,
                    NewEvent::new(SessionEventKind::TurnEnd)
                        .with_turn(turn)
                        .with_payload(json!({
                            "reason": reason,
                            "discarded": discarded,
                        })),
                );
            }
            return Err(error.to_string());
        }
    };

    let mut tool_trace = Vec::new();
    for message in &run.new_messages {
        if message.role == ChatRole::Tool {
            if let Some(content) = &message.content {
                if !content.is_empty() {
                    tool_trace.push(truncate_for_ui(content));
                }
            }
        }
    }
    {
        // Messages, usage, and compaction were settled step by step during the
        // run. Only the turn terminator remains.
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;
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
        duration_ms: u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
        attachments: staged_attachments,
    })
}

fn resolve_api_key(state: &AppState) -> Result<String, String> {
    if let Ok(Some(secret)) = state.secrets.get(API_KEY_REFERENCE) {
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

/// Load the model-visible history and, in the same order, the store sequence
/// of each message. Checkpoints map to `None` because they are always shadowed
/// as a set when a newer checkpoint replaces them.
/// Closes a turn that the process never finished. Every assistant tool call is
/// paired with a result, and the turn is terminated as interrupted, so the next
/// request is built from a consistent history.
fn repair_interrupted_turn(store: &Store, session: SessionId) -> Result<bool, String> {
    let turns = store
        .load_turns(session)
        .map_err(|error| error.to_string())?;
    let Some(last) = turns.last() else {
        return Ok(false);
    };
    if last.ended_at.is_some() {
        return Ok(false);
    }
    let messages = store
        .load_messages(session)
        .map_err(|error| error.to_string())?;
    let mut answered = HashSet::new();
    let mut pending = Vec::new();
    for message in messages
        .iter()
        .filter(|message| message.turn == Some(last.turn))
    {
        match message.role.as_str() {
            "assistant" => {
                let Some(calls) = message.content.get("tool_calls").and_then(Value::as_array)
                else {
                    continue;
                };
                for call in calls {
                    let Some(id) = call.get("id").and_then(Value::as_str) else {
                        continue;
                    };
                    let name = call
                        .pointer("/function/name")
                        .and_then(Value::as_str)
                        .unwrap_or("tool");
                    pending.push((id.to_owned(), name.to_owned()));
                }
            }
            "tool" => {
                if let Some(id) = message.content.get("tool_call_id").and_then(Value::as_str) {
                    answered.insert(id.to_owned());
                }
            }
            _ => {}
        }
    }
    let mut drafts = Vec::new();
    for (id, name) in pending {
        if answered.contains(&id) {
            continue;
        }
        drafts.push(
            NewEvent::new(SessionEventKind::ToolResult)
                .with_turn(last.turn)
                .with_payload(json!({
                    "content": format!(
                        "error: the app stopped before `{name}` returned. Its outcome is unknown; retry only if it is safe to repeat."
                    ),
                    "tool_call_id": id,
                    "interrupted": true,
                })),
        );
    }
    drafts.push(
        NewEvent::new(SessionEventKind::TurnEnd)
            .with_turn(last.turn)
            .with_payload(json!({"reason": "interrupted"})),
    );
    store
        .append_events(session, drafts)
        .map_err(|error| error.to_string())?;
    Ok(true)
}

fn load_history(
    store: &Store,
    session: SessionId,
) -> Result<(Vec<ChatMessage>, Vec<Option<u64>>), String> {
    let messages = store
        .load_surface_messages(session)
        .map_err(|error| error.to_string())?;
    let entries = messages
        .iter()
        .enumerate()
        .filter(|(index, message)| !(*index == 0 && message.role == "system"))
        .map(|(_, message)| {
            (message.source.as_deref() != Some("compaction")).then_some(message.seq)
        })
        .collect();
    let history = messages
        .iter()
        .map(chat_message_from_session)
        .collect::<Result<Vec<_>, _>>()?;
    Ok((history, entries))
}

/// The store sequence through which a checkpoint shadows messages. Older
/// checkpoints are shadowed by the projection regardless of this value.
fn shadowed_through_seq(entries: &[Option<u64>], shadowed_messages: usize) -> u64 {
    entries
        .iter()
        .take(shadowed_messages)
        .filter_map(|seq| *seq)
        .max()
        .unwrap_or(0)
}

fn persist_compaction(
    store: &Store,
    session: SessionId,
    turn: u32,
    compaction: &openbad_agent::CompactionRecord,
    surface_seqs: &[Option<u64>],
) -> Result<(), String> {
    let cutoff = shadowed_through_seq(surface_seqs, compaction.shadowed_messages);
    let payload = json!({
        "shadowed_messages": compaction.shadowed_messages,
        "shadowed_through_seq": cutoff,
        "before_bytes": compaction.before_bytes,
        "after_bytes": compaction.after_bytes,
    });
    store
        .append_event(
            session,
            NewEvent::new(SessionEventKind::CompactionStart)
                .with_turn(turn)
                .with_payload(payload.clone()),
        )
        .map_err(|error| error.to_string())?;
    store
        .append_event(
            session,
            NewEvent::new(SessionEventKind::CompactionSummary)
                .with_turn(turn)
                .with_payload(json!({
                    "content": compaction.summary,
                    "source": "compaction",
                    "shadowed_messages": compaction.shadowed_messages,
                    "shadowed_through_seq": cutoff,
                    "surface_op": {
                        "replace": {
                            "start_seq": 0,
                            "end_seq": cutoff
                        }
                    },
                    "before_bytes": compaction.before_bytes,
                    "after_bytes": compaction.after_bytes,
                })),
        )
        .map_err(|error| error.to_string())?;
    store
        .append_event(
            session,
            NewEvent::new(SessionEventKind::CompactionEnd)
                .with_turn(turn)
                .with_payload(payload),
        )
        .map_err(|error| error.to_string())?;
    Ok(())
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
            let mut tool_calls = payload
                .get("tool_calls")
                .filter(|value| value.is_array())
                .cloned()
                .map(serde_json::from_value::<Vec<ToolCall>>)
                .transpose()
                .map_err(|error| format!("invalid tool calls in history: {error}"))?;
            if let Some(calls) = tool_calls.as_mut() {
                openbad_agent::sanitize_tool_arguments(calls);
            }
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
            runs: Arc::new(Mutex::new(RunRegistry::default())),
            approvals: Arc::new(Mutex::new(ApprovalCache::default())),
            pending_approvals: Arc::new(Mutex::new(HashMap::new())),
            subagent_cancels: Arc::new(Mutex::new(HashMap::new())),
            terminal: Arc::new(openbad_terminal::TerminalManager::new()),
            mcp: McpManagers::default(),
        })
        .invoke_handler(tauri::generate_handler![
            health,
            foundation,
            list_projects,
            add_project,
            create_project,
            export_project,
            import_project,
            import_project_into,
            workspaces_directory,
            rename_project,
            remove_project,
            rename_session,
            set_session_access_mode,
            archived_sessions,
            archive_session,
            delete_session,
            workspace_index_status,
            refresh_workspace_index,
            workspace_default,
            project_agents,
            cancel_agent,
            save_api_key,
            clear_api_key,
            clipboard::read_clipboard_image,
            cancel_chat,
            respond_to_approval,
            steer_chat,
            api_key_status,
            mcp_servers,
            add_mcp_server,
            update_mcp_server,
            remove_mcp_server,
            set_mcp_server_enabled,
            test_mcp_server,
            check_for_updates,
            install_update,
            sessions,
            search_sessions,
            memories,
            project_overview,
            project_area_counts,
            project_manifest,
            project_todos,
            project_plan,
            approve_project_plan,
            project_questions,
            answer_project_question,
            project_repos,
            add_project_repo,
            remove_project_repo,
            set_project_repo_read_only,
            git::git_repo_status,
            git::git_project_repos,
            git::git_init,
            git::git_checkout,
            git::git_fetch,
            git::git_pull,
            git::git_push,
            git::git_stage,
            git::git_unstage,
            git::git_commit,
            git::git_diff,
            git::git_log,
            services::services_status,
            services::open_service_url,
            outside_access_grants,
            grant_outside_access,
            revoke_outside_access,
            knowledge_documents,
            knowledge_stale_documents,
            knowledge_document,
            write_export_file,
            add_knowledge_note,
            import_knowledge_file,
            import_knowledge_files,
            import_workspace_files,
            import_reference_files,
            delete_knowledge_document,
            search_project,
            project_files,
            project_file,
            reveal_path,
            open_path,
            forget_memory,
            checkpoints,
            checkpoint_diff,
            restore_checkpoint,
            session_view,
            fork_session,
            chat
        ])
        .run(tauri::generate_context!())
        .expect("error while running OpenBaD Desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compaction_cutoff_ignores_prior_checkpoint_sequences() {
        let entries = vec![None, Some(10), Some(11), Some(12)];
        assert_eq!(shadowed_through_seq(&entries, 1), 0);
        assert_eq!(shadowed_through_seq(&entries, 3), 11);
        assert_eq!(shadowed_through_seq(&entries, 9), 12);
    }

    #[test]
    fn replay_matches_the_projected_history() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("replay", "/tmp/openbad-replay")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("replay"))
            .expect("session");
        store
            .append_events(
                session,
                vec![
                    NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
                    NewEvent::new(SessionEventKind::UserMessage)
                        .with_turn(1)
                        .with_step(1)
                        .with_payload(json!({"content": "first question"})),
                    NewEvent::new(SessionEventKind::AssistantMessage)
                        .with_turn(1)
                        .with_step(1)
                        .with_payload(json!({"content": "first answer"})),
                    NewEvent::new(SessionEventKind::TurnEnd)
                        .with_turn(1)
                        .with_payload(json!({"reason": "complete"})),
                    NewEvent::new(SessionEventKind::TurnStart).with_turn(2),
                    NewEvent::new(SessionEventKind::UserMessage)
                        .with_turn(2)
                        .with_step(1)
                        .with_payload(json!({"content": "second question"})),
                    NewEvent::new(SessionEventKind::CompactionSummary)
                        .with_turn(2)
                        .with_payload(json!({
                            "content": "summary",
                            "source": "compaction",
                            "shadowed_messages": 2,
                            "shadowed_through_seq": 3,
                            "surface_op": {"replace": {"start_seq": 0, "end_seq": 3}}
                        })),
                    NewEvent::new(SessionEventKind::AssistantMessage)
                        .with_turn(2)
                        .with_step(1)
                        .with_payload(json!({"content": "second answer"})),
                    NewEvent::new(SessionEventKind::TurnEnd)
                        .with_turn(2)
                        .with_payload(json!({"reason": "complete"})),
                ],
            )
            .expect("seed");

        let (history, _) = load_history(&store, session).expect("history");
        let events = store.load_events(session).expect("events");
        let replayed = openbad_llm::derive_messages(&events)
            .into_iter()
            .map(|entry| entry.message)
            .collect::<Vec<_>>();

        let replayed_json = replayed
            .iter()
            .map(|message| serde_json::to_string(message).expect("replay json"))
            .collect::<Vec<_>>();
        let history_json = history
            .iter()
            .map(|message| serde_json::to_string(message).expect("history json"))
            .collect::<Vec<_>>();

        assert_eq!(
            replayed_json, history_json,
            "replay must be byte-identical to the projected history"
        );
        assert_eq!(replayed.len(), 3);
    }

    #[test]
    fn repairs_a_turn_interrupted_mid_tool_call() {
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("repair", "/tmp/openbad-repair")
            .expect("workspace");
        let session = store
            .create_session(Some(workspace), Some("repair"))
            .expect("session");
        store
            .append_events(
                session,
                vec![
                    NewEvent::new(SessionEventKind::TurnStart).with_turn(1),
                    NewEvent::new(SessionEventKind::UserMessage)
                        .with_turn(1)
                        .with_step(1)
                        .with_payload(json!({"content": "read it"})),
                    NewEvent::new(SessionEventKind::AssistantMessage)
                        .with_turn(1)
                        .with_step(1)
                        .with_payload(json!({
                            "content": "Partial answer",
                            "tool_calls": [{
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{\"path\":\"a.txt\"}"
                                }
                            }]
                        })),
                ],
            )
            .expect("seed");

        assert!(repair_interrupted_turn(&store, session).expect("repair"));

        let turns = store.load_turns(session).expect("turns");
        assert_eq!(
            turns.last().expect("turn").reason.as_deref(),
            Some("interrupted")
        );
        let messages = store.load_messages(session).expect("messages");
        let tool = messages
            .iter()
            .find(|message| message.role == "tool")
            .expect("tool result");
        assert_eq!(
            tool.content.get("tool_call_id").and_then(Value::as_str),
            Some("call-1")
        );
        assert!(
            tool.content
                .get("content")
                .and_then(Value::as_str)
                .is_some_and(|content| content.contains("outcome is unknown"))
        );
        let (history, _) = load_history(&store, session).expect("history");
        assert!(history.iter().any(|message| {
            message.role == ChatRole::Assistant
                && message.content.as_deref() == Some("Partial answer")
        }));
        assert!(history.iter().any(|message| {
            message.role == ChatRole::Tool && message.tool_call_id.as_deref() == Some("call-1")
        }));
        assert!(!repair_interrupted_turn(&store, session).expect("idempotent"));
    }

    #[test]
    fn project_files_lists_live_workspace_entries() {
        let root = tempfile::tempdir().expect("tempdir");
        std::fs::create_dir_all(root.path().join("src")).expect("src");
        std::fs::write(root.path().join("README.md"), "readme").expect("readme");
        std::fs::write(root.path().join("src/main.rs"), "fn main() {}").expect("main");
        let files = scan_project_files(root.path().to_string_lossy().into_owned()).expect("files");
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
        assert_eq!(payload.doctor.schema_version, "0016_session_fork");
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
    fn project_area_counts_include_sidecars_and_agents() {
        let root = tempfile::tempdir().expect("tempdir");
        let sidecars = root.path().join(".openbad");
        std::fs::create_dir_all(&sidecars).expect("sidecars");
        std::fs::write(
            sidecars.join("todos.json"),
            r#"[{"id":"t1","text":"open","state":"todo"},{"id":"t2","text":"done","state":"done"}]"#,
        )
        .expect("todos");
        std::fs::write(
            sidecars.join("questions.json"),
            r#"[{"id":"q1","question":"pick","options":[],"multi":false,"created_at":1},{"id":"q2","question":"answered","options":[],"multi":false,"created_at":1,"answer":"a","answered_at":2}]"#,
        )
        .expect("questions");
        std::fs::write(
            sidecars.join("plan.json"),
            r#"{"goal":"ship","steps":[{"id":"s1","text":"one","status":"done"},{"id":"s2","text":"two","status":"todo"}],"created_at":1,"updated_at":2}"#,
        )
        .expect("plan");

        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .ensure_workspace("demo", &root.path().to_string_lossy())
            .expect("workspace");
        let now = now_millis();
        store
            .create_subagent(SubagentRecord {
                id: "agent-1".to_owned(),
                workspace_id: workspace,
                parent_session_id: None,
                title: "Worker".to_owned(),
                task: "Inspect".to_owned(),
                model: "deepseek-flash".to_owned(),
                status: "running".to_owned(),
                log: String::new(),
                result: String::new(),
                error: None,
                steps: 1,
                created_at: now,
                updated_at: now,
                finished_at: None,
            })
            .expect("subagent");

        store
            .create_subagent(SubagentRecord {
                id: "old-agent".to_owned(),
                workspace_id: workspace,
                parent_session_id: None,
                title: "Finished worker".to_owned(),
                task: "Done".to_owned(),
                model: "deepseek-flash".to_owned(),
                status: "completed".to_owned(),
                log: String::new(),
                result: "done".to_owned(),
                error: None,
                steps: 2,
                created_at: now - 120_000,
                updated_at: now - 120_000,
                finished_at: Some(now - 120_000),
            })
            .expect("finished subagent");

        let counts =
            project_area_counts_payload(&store, &root.path().to_string_lossy()).expect("counts");
        assert!(store.get_subagent("old-agent").expect("old").is_none());
        assert_eq!(counts.agents, 1);
        assert_eq!(counts.running_agents, 1);
        assert_eq!(counts.todos_total, 2);
        assert_eq!(counts.todos_open, 1);
        assert_eq!(counts.questions_total, 2);
        assert_eq!(counts.questions_open, 1);
        assert_eq!(counts.plan_steps, 2);
        assert_eq!(counts.plan_open_steps, 1);
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
            "runId": "run-1",
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
        assert_eq!(input.run_id, "run-1");
        assert_eq!(input.workspace_path, "/tmp/work");
        assert!(input.allow_writes);
        assert_eq!(input.max_steps, Some(32));
        assert_eq!(input.model.as_deref(), Some("deepseek-v4-pro"));
        assert_eq!(input.reasoning_effort.as_deref(), Some("max"));
    }

    #[test]
    fn runs_are_scoped_to_a_session_and_cancel_independently() {
        let registry = Arc::new(Mutex::new(RunRegistry::default()));
        let first = begin_run(&registry, "run-a", Some("session-1")).expect("first run");
        let second = begin_run(&registry, "run-b", Some("session-2"))
            .expect("a second session runs concurrently");

        let duplicate = begin_run(&registry, "run-c", Some("session-1"))
            .err()
            .expect("one run per session");
        assert!(duplicate.contains("already running"));

        assert!(!first.cancel_token().load(Ordering::SeqCst));
        assert!(!second.cancel_token().load(Ordering::SeqCst));
        assert!(cancel_registered_run(&registry, "run-a").expect("cancel"));
        assert!(first.cancel_token().load(Ordering::SeqCst));
        assert!(
            !second.cancel_token().load(Ordering::SeqCst),
            "cancelling one run must not cancel another"
        );

        assert!(
            steer_registered_run(&registry, "run-b", "focus on tests").expect("steer"),
            "the target run accepts steering"
        );
        assert!(
            steer_registered_run(&registry, "run-b", "and keep going").expect("steer"),
            "the active inbox accepts more than one steering message"
        );

        first.finish_inbox();
        assert!(
            !steer_registered_run(&registry, "run-a", "too late").expect("registry entry"),
            "steering closes as soon as the agent loop returns"
        );
        drop(first);
        let replacement = begin_run(&registry, "run-c", Some("session-1"))
            .expect("session is free after the run guard drops");
        drop(replacement);
        assert!(steer_registered_run(&registry, "run-a", "late").is_err());
    }

    #[test]
    fn approval_cache_scopes_decisions() {
        let approvals = DesktopApprovals {
            pending: Arc::new(Mutex::new(HashMap::new())),
            cancel: Arc::new(AtomicBool::new(false)),
            cache: Arc::new(Mutex::new(ApprovalCache::default())),
            session: SessionId::from(Uuid::nil()),
            workspace: "/tmp/approval-workspace".to_owned(),
        };

        assert!(approvals.cached("shell").is_none());
        approvals.remember("shell", ApprovalDecision::AllowOnce);
        assert!(
            approvals.cached("shell").is_none(),
            "allow once must not become a standing approval"
        );
        approvals.remember("shell", ApprovalDecision::AllowSession);
        assert_eq!(
            approvals.cached("shell"),
            Some(ApprovalDecision::AllowSession)
        );
        approvals.remember("write_file", ApprovalDecision::AllowWorkspace);
        assert_eq!(
            approvals.cached("write_file"),
            Some(ApprovalDecision::AllowWorkspace)
        );
    }

    #[test]
    fn anonymous_runs_do_not_collide() {
        let registry = Arc::new(Mutex::new(RunRegistry::default()));
        let first = begin_run(&registry, "run-a", None).expect("first anonymous run");
        let second = begin_run(&registry, "run-b", None).expect("second anonymous run");
        assert_eq!(first.inbox().len(), 0);
        assert_eq!(second.inbox().len(), 0);
    }

    #[test]
    fn cancellation_before_registration_is_not_lost() {
        let registry = Arc::new(Mutex::new(RunRegistry::default()));
        assert!(!cancel_registered_run(&registry, "early-stop").expect("cancel"));
        let run = begin_run(&registry, "early-stop", None).expect("run");
        assert!(
            run.cancel_token().load(Ordering::SeqCst),
            "a stop that arrives before chat registers the run still cancels it"
        );
    }

    #[test]
    fn filesystem_grants_require_absolute_paths_and_resolve_symlinks() {
        let directory = tempfile::tempdir().expect("tempdir");
        let file = directory.path().join("notes.txt");
        std::fs::write(&file, "notes").expect("file");
        let resolved = canonical_grant_path(&file.to_string_lossy()).expect("grant path");
        assert_eq!(resolved, file.canonicalize().expect("canonical"));

        assert!(canonical_grant_path("relative/path").is_err());
        assert!(canonical_grant_path("/").is_err());
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

    #[test]
    fn stored_history_repairs_invalid_tool_arguments() {
        let session_message = SessionMessage {
            session_id: SessionId::new(),
            seq: 1,
            turn: Some(1),
            step: Some(1),
            role: "assistant".to_owned(),
            content: json!({
                "content": null,
                "reasoning_content": null,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": "{\"path\": }",
                    },
                }],
            }),
            source: None,
            created_at: 0,
        };

        let restored = chat_message_from_session(&session_message).expect("restore");
        assert_eq!(
            restored.tool_calls.expect("tool calls")[0]
                .function
                .arguments,
            "{}"
        );
    }

    #[test]
    fn uploads_copy_documents_with_unique_names() {
        let workspace = tempfile::tempdir().expect("workspace");
        let first = tempfile::tempdir().expect("first");
        let second = tempfile::tempdir().expect("second");
        std::fs::write(first.path().join("notes.md"), "first").expect("first file");
        std::fs::write(second.path().join("notes.md"), "second").expect("second file");
        let sources = vec![
            UploadSource {
                name: "notes.md".to_owned(),
                path: Some(first.path().join("notes.md").to_string_lossy().into_owned()),
                content: None,
                content_base64: None,
                mime: None,
            },
            UploadSource {
                name: "notes.md".to_owned(),
                path: Some(
                    second
                        .path()
                        .join("notes.md")
                        .to_string_lossy()
                        .into_owned(),
                ),
                content: None,
                content_base64: None,
                mime: None,
            },
            UploadSource {
                name: "inline.txt".to_owned(),
                path: None,
                content: Some("inline body".to_owned()),
                content_base64: None,
                mime: None,
            },
        ];

        let report = write_uploads(workspace.path(), &sources).expect("upload");
        assert!(report.skipped.is_empty(), "{:?}", report.skipped);
        assert_eq!(report.files.len(), 3);
        assert_eq!(report.files[0].name, "notes.md");
        assert_eq!(report.files[1].name, "notes-1.md");
        assert_eq!(report.files[2].name, "inline.txt");
        assert_eq!(
            std::fs::read_to_string(workspace.path().join("notes-1.md")).expect("second"),
            "second"
        );
    }

    #[test]
    fn uploads_skip_folders_and_escape_attempts() {
        let workspace = tempfile::tempdir().expect("workspace");
        let source = tempfile::tempdir().expect("source");
        let sources = vec![
            UploadSource {
                name: "folder".to_owned(),
                path: Some(source.path().to_string_lossy().into_owned()),
                content: None,
                content_base64: None,
                mime: None,
            },
            UploadSource {
                name: "../../etc/passwd".to_owned(),
                path: None,
                content: Some("safe".to_owned()),
                content_base64: None,
                mime: None,
            },
        ];

        let report = write_uploads(workspace.path(), &sources).expect("upload");
        assert_eq!(report.files.len(), 1);
        assert_eq!(report.skipped.len(), 1);
        assert!(report.skipped[0].contains("must be files"));
        assert_eq!(report.files[0].name, "passwd");
        assert!(workspace.path().join("passwd").is_file());
    }

    #[cfg(unix)]
    #[test]
    fn uploads_do_not_follow_planted_symlinks() {
        let workspace = tempfile::tempdir().expect("workspace");
        let target = tempfile::tempdir().expect("target");
        std::os::unix::fs::symlink(
            target.path().join("escape.md"),
            workspace.path().join("notes.md"),
        )
        .expect("symlink");

        let report = write_uploads(
            workspace.path(),
            &[UploadSource {
                name: "notes.md".to_owned(),
                path: None,
                content: Some("safe".to_owned()),
                content_base64: None,
                mime: None,
            }],
        )
        .expect("upload");

        assert_eq!(report.files.len(), 1);
        assert_eq!(report.files[0].name, "notes-1.md");
        assert!(!target.path().join("escape.md").exists());
    }

    #[test]
    fn large_path_uploads_are_streamed_into_the_workspace() {
        let workspace = tempfile::tempdir().expect("workspace");
        let source_dir = tempfile::tempdir().expect("source");
        let source = source_dir.path().join("clip.mov");
        let file = std::fs::File::create(&source).expect("movie");
        file.set_len(9 * 1024 * 1024).expect("sparse movie");
        drop(file);
        let report = write_uploads(
            &workspace.path().join("uploads"),
            &[UploadSource {
                name: "clip.mov".to_owned(),
                path: Some(source.to_string_lossy().into_owned()),
                content: None,
                content_base64: None,
                mime: None,
            }],
        )
        .expect("upload");
        assert!(report.skipped.is_empty(), "{:?}", report.skipped);
        assert_eq!(report.files.len(), 1);
        assert_eq!(report.files[0].name, "clip.mov");
        assert_eq!(report.files[0].bytes, 9 * 1024 * 1024);
        assert!(workspace.path().join("uploads/clip.mov").is_file());
    }

    #[test]
    fn chat_attachments_are_staged_in_the_workspace() {
        let workspace = tempfile::tempdir().expect("workspace");
        let (message, files) = compose_chat_message(
            workspace.path(),
            "Review this",
            &[UploadSource {
                name: "design.md".to_owned(),
                path: None,
                content: Some("# Design\nbody".to_owned()),
                content_base64: None,
                mime: None,
            }],
        )
        .expect("message");
        assert!(message.starts_with("Review this\n\n"));
        assert!(message.contains("- uploads/design.md (13 bytes)"));
        assert!(message.contains("<attached_document name=\"uploads/design.md\">"));
        assert!(message.contains("# Design\nbody"));
        assert_eq!(files.len(), 1);
        assert!(workspace.path().join("uploads/design.md").is_file());
        assert_eq!(
            compose_chat_message(workspace.path(), "", &[])
                .expect("plain")
                .0,
            ""
        );

        let too_many = (0..=MAX_CHAT_ATTACHMENTS)
            .map(|index| UploadSource {
                name: format!("note-{index}.md"),
                path: None,
                content: Some("x".to_owned()),
                content_base64: None,
                mime: None,
            })
            .collect::<Vec<_>>();
        assert!(compose_chat_message(workspace.path(), "Review", &too_many).is_err());
    }

    #[test]
    fn binary_chat_attachments_are_staged_but_not_inlined() {
        let workspace = tempfile::tempdir().expect("workspace");
        let source = workspace.path().join("diagram.png");
        std::fs::write(&source, [0x89, b'P', b'N', b'G', 0x00, 0xff]).expect("png");
        let (message, files) = compose_chat_message(
            workspace.path(),
            "Look at this",
            &[UploadSource {
                name: "diagram.png".to_owned(),
                path: Some(source.to_string_lossy().into_owned()),
                content: None,
                content_base64: None,
                mime: None,
            }],
        )
        .expect("message");
        assert!(message.contains("- uploads/diagram.png (6 bytes)"));
        assert!(!message.contains("<attached_document"));
        assert_eq!(files.len(), 1);
        assert_eq!(
            std::fs::read(workspace.path().join("uploads/diagram.png")).expect("staged"),
            vec![0x89, b'P', b'N', b'G', 0x00, 0xff]
        );
    }

    #[test]
    fn knowledge_uploads_require_utf8_and_index_the_document() {
        let root = tempfile::tempdir().expect("workspace");
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .ensure_workspace("demo", &root.path().to_string_lossy())
            .expect("workspace");

        let document = import_knowledge_source(
            &store,
            workspace,
            &UploadSource {
                name: "release-notes.md".to_owned(),
                path: None,
                content: Some("# Release\nShip it".to_owned()),
                content_base64: None,
                mime: None,
            },
            &["docs".to_owned()],
        )
        .expect("document");
        assert_eq!(document.title, "release-notes");
        assert_eq!(document.kind, "md");
        assert_eq!(document.tags, vec!["docs".to_owned()]);
        assert_eq!(document.confidence, "verified");
        assert!(document.chunks >= 1);

        let binary = tempfile::tempdir().expect("binary");
        let path = binary.path().join("scan.pdf");
        std::fs::write(&path, [0xff, 0xfe, 0x00]).expect("binary");
        let error = import_knowledge_source(
            &store,
            workspace,
            &UploadSource {
                name: "scan.pdf".to_owned(),
                path: Some(path.to_string_lossy().into_owned()),
                content: None,
                content_base64: None,
                mime: None,
            },
            &[],
        )
        .expect_err("binary documents are rejected");
        assert!(error.contains("UTF-8"));
    }

    #[test]
    fn upload_names_only_keep_the_file_component() {
        assert_eq!(safe_upload_name("a/b/c.txt").expect("name"), "c.txt");
        assert_eq!(safe_upload_name("..\\..\\c.txt").expect("name"), "c.txt");
        assert!(safe_upload_name("..").is_err());
        assert!(safe_upload_name("   ").is_err());
    }

    #[test]
    fn turn_durations_persist_on_the_last_assistant_message() {
        use openbad_core::SessionId;
        use openbad_store::types::SessionTurn;
        use uuid::Uuid;

        let session = SessionId::from(Uuid::nil());
        let turn = |number: u32, started: i64, ended: Option<i64>| SessionTurn {
            session_id: session,
            turn: number,
            started_at: started,
            ended_at: ended,
            reason: None,
            steps: 1,
            input_tokens: 0,
            cache_read_tokens: 0,
            output_tokens: 0,
            cost_usd: 0.0,
        };
        let (durations, total) = summarize_turn_durations(&[
            turn(1, 1_000, Some(4_000)),
            turn(2, 10_000, Some(12_500)),
            turn(3, 20_000, None),
        ]);
        assert_eq!(durations.get(&1), Some(&3_000));
        assert_eq!(durations.get(&2), Some(&2_500));
        assert_eq!(durations.get(&3), None);
        assert_eq!(total, 5_500);

        let message = |role: &str, duration: Option<u64>| UiMessage {
            seq: 0,
            role: role.to_owned(),
            text: String::new(),
            attachments: Vec::new(),
            reasoning: None,
            duration_ms: duration,
            interrupted: false,
            tool_calls: Vec::new(),
        };
        let mut messages = vec![
            message("user", None),
            message("assistant", Some(3_000)),
            message("assistant", Some(3_000)),
            message("user", None),
            message("assistant", Some(2_500)),
        ];
        keep_last_assistant_duration_per_turn(
            &mut messages,
            &[Some(1), Some(1), Some(1), Some(2), Some(2)],
        );
        assert_eq!(messages[1].duration_ms, None);
        assert_eq!(messages[2].duration_ms, Some(3_000));
        assert_eq!(messages[4].duration_ms, Some(2_500));
    }

    #[test]
    fn reveal_targets_stay_inside_the_project() {
        let workspace = tempfile::tempdir().expect("workspace");
        std::fs::create_dir_all(workspace.path().join("src")).expect("src");
        std::fs::write(workspace.path().join("src/main.rs"), "fn main() {}").expect("file");
        let reference = tempfile::tempdir().expect("reference");
        std::fs::write(reference.path().join("README.md"), "# ref").expect("ref file");

        let root = workspace.path().canonicalize().expect("root");
        let inside = resolve_reveal_target(&root, &[], "src/main.rs").expect("inside");
        assert!(inside.ends_with("src/main.rs"));
        assert!(
            resolve_reveal_target(&root, &[], "src")
                .expect("dir")
                .is_dir()
        );
        assert!(
            resolve_reveal_target(&root, &[reference.path().to_path_buf()], "README.md").is_err(),
            "reference files are resolved against the workspace only"
        );
        let absolute = reference
            .path()
            .join("README.md")
            .to_string_lossy()
            .into_owned();
        assert!(
            resolve_reveal_target(&root, &[], &absolute).is_err(),
            "absolute paths outside the project are rejected"
        );
        assert!(resolve_reveal_target(&root, &[reference.path().to_path_buf()], &absolute).is_ok());
        assert!(resolve_reveal_target(&root, &[], "missing.txt").is_err());
        assert!(resolve_reveal_target(&root, &[], "  ").is_err());
    }

    #[test]
    fn reloaded_transcripts_keep_their_tool_cards() {
        let session = SessionId::new();
        let message = |seq: u64, step: u32, role: &str, content: Value| SessionMessage {
            session_id: session,
            seq,
            turn: Some(1),
            step: Some(step),
            role: role.to_owned(),
            content,
            source: None,
            created_at: 0,
        };
        let call = |id: &str, name: &str| {
            json!({
                "id": id,
                "type": "function",
                "function": {"name": name, "arguments": "{\"path\":\"a.txt\"}"},
            })
        };
        let messages = vec![
            message(1, 1, "user", json!({"content": "read it"})),
            message(
                2,
                1,
                "assistant",
                json!({
                    "content": "",
                    "reasoning_content": null,
                    "tool_calls": [call("call-ok", "read_file")],
                }),
            ),
            message(
                3,
                1,
                "tool",
                json!({
                    "content": "hello",
                    "tool_call_id": "call-ok",
                    "duration_ms": 42,
                    "is_error": false,
                }),
            ),
            message(
                4,
                2,
                "assistant",
                json!({
                    "content": "",
                    "reasoning_content": null,
                    "tool_calls": [call("call-bad", "write_file")],
                }),
            ),
            message(
                5,
                2,
                "tool",
                json!({
                    "content": "error: denied",
                    "tool_call_id": "call-bad",
                    "duration_ms": 7,
                    "is_error": true,
                }),
            ),
            message(
                6,
                3,
                "assistant",
                json!({"content": "Done.", "reasoning_content": null, "tool_calls": null}),
            ),
        ];

        let (ui, _) = build_ui_messages(&messages, &[]);

        // One bubble per turn, exactly as the live stream renders it.
        assert_eq!(ui.len(), 2);
        let cards = ui[1]
            .tool_calls
            .iter()
            .map(|call| {
                (
                    call.call_id.as_str(),
                    call.name.as_str(),
                    call.status.as_str(),
                    call.result.as_str(),
                    call.is_error,
                    call.duration_ms,
                )
            })
            .collect::<Vec<_>>();
        assert_eq!(
            cards,
            vec![
                ("call-ok", "read_file", "settled", "hello", false, Some(42)),
                (
                    "call-bad",
                    "write_file",
                    "failed",
                    "error: denied",
                    true,
                    Some(7)
                ),
            ]
        );
        assert_eq!(ui[1].text, "Done.");
    }

    #[test]
    fn mcp_specs_infer_transport_and_preserve_unspecified_fields() {
        let spec = McpServerSpec {
            name: "docs".to_owned(),
            command: Some("npx".to_owned()),
            args: Some(vec!["-y".to_owned(), "@example/mcp-docs".to_owned()]),
            ..McpServerSpec::default()
        };
        let config = mcp_server_config_from_spec(&spec, None).expect("stdio config");
        assert_eq!(config.transport, openbad_mcp::McpTransport::Stdio);
        assert_eq!(config.args, vec!["-y", "@example/mcp-docs"]);

        let spec = McpServerSpec {
            name: "remote".to_owned(),
            url: Some("https://mcp.example.com/rpc".to_owned()),
            ..McpServerSpec::default()
        };
        let config = mcp_server_config_from_spec(&spec, None).expect("http config");
        assert_eq!(config.transport, openbad_mcp::McpTransport::Http);

        let existing = openbad_mcp::McpServerConfig::stdio("docs", "npx");
        let patch = McpServerSpec {
            name: "docs".to_owned(),
            enabled: Some(false),
            timeout_seconds: Some(30),
            ..McpServerSpec::default()
        };
        let updated = mcp_server_config_from_spec(&patch, Some(existing)).expect("patched config");
        assert_eq!(updated.command.as_deref(), Some("npx"));
        assert_eq!(updated.timeout_seconds, Some(30));
    }

    #[test]
    fn mcp_specs_reject_invalid_servers() {
        let spec = McpServerSpec {
            name: "bad name".to_owned(),
            ..McpServerSpec::default()
        };
        assert!(mcp_server_config_from_spec(&spec, None).is_err());

        let spec = McpServerSpec {
            name: "docs".to_owned(),
            ..McpServerSpec::default()
        };
        assert!(mcp_server_config_from_spec(&spec, None).is_err());

        let spec = McpServerSpec {
            name: "remote".to_owned(),
            transport: Some("http".to_owned()),
            url: Some("ftp://example.com/rpc".to_owned()),
            ..McpServerSpec::default()
        };
        assert!(mcp_server_config_from_spec(&spec, None).is_err());
    }
}
