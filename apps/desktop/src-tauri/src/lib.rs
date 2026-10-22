use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use openbad_agent::{AgentConfig, AgentError, AgentObserver, SteeringInbox, default_agent};
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
    cancel: Arc<AtomicBool>,
    steering: Arc<SteeringInbox>,
    subagent_cancels: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    terminal: Arc<openbad_terminal::TerminalManager>,
    mcp: McpManagers,
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

fn mcp_server_summary(config: &openbad_mcp::McpServerConfig) -> McpServerSummary {
    McpServerSummary {
        name: config.name.clone(),
        transport: match config.transport {
            openbad_mcp::McpTransport::Stdio => "stdio".to_owned(),
            openbad_mcp::McpTransport::Http => "http".to_owned(),
        },
        command: config.command.clone(),
        args: config.args.clone(),
        url: config.url.clone(),
        enabled: config.enabled,
        allowed_tools: config.allowed_tools.clone(),
        env_keys: config.env.keys().cloned().collect(),
        timeout_seconds: config.timeout_seconds,
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
    if let Some(enabled) = spec.enabled {
        config.enabled = enabled;
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
            manager
                .add_server(config.clone())
                .map_err(|error| error.to_string())?;
            let (tools, warning) = probe_mcp_server(&manager, &config.name);
            Ok(McpServerChange {
                server: mcp_server_summary(&config),
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
                .find(|server| server.name == name)
                .ok_or_else(|| format!("unknown MCP server: {name}"))?;
            let config = mcp_server_config_from_spec(&spec, Some(current))?;
            manager
                .update_server(&name, config.clone())
                .map_err(|error| error.to_string())?;
            let (tools, warning) = probe_mcp_server(&manager, &name);
            Ok(McpServerChange {
                server: mcp_server_summary(&config),
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
        name: &str,
        content: &str,
        is_error: bool,
        _metadata: &Value,
    ) {
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
            allow_writes: spec.allow_writes,
            allow_commands: spec.allow_commands,
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
    #[serde(default)]
    pub attachments: Vec<String>,
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

const MAX_UPLOAD_BYTES: u64 = 8 * 1024 * 1024;
const MAX_UPLOAD_FILES: usize = 50;
const MAX_KNOWLEDGE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_BYTES: u64 = 1024 * 1024;
const MAX_CHAT_ATTACHMENTS: usize = 8;
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
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    Err(format!("{} has no path or content to upload", source.name))
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
fn import_reference_files(
    state: State<'_, AppState>,
    workspace_path: String,
    files: Vec<UploadSource>,
) -> Result<ReferenceUpload, String> {
    let root = PathBuf::from(&workspace_path);
    if !root.is_dir() {
        return Err(format!("workspace is not a directory: {workspace_path}"));
    }
    let store = state
        .store
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

fn compose_chat_message(prompt: &str, attachments: &[UploadSource]) -> Result<String, String> {
    if attachments.is_empty() {
        return Ok(prompt.to_owned());
    }
    if attachments.len() > MAX_CHAT_ATTACHMENTS {
        return Err(format!(
            "attach at most {MAX_CHAT_ATTACHMENTS} documents per message"
        ));
    }
    let mut message = prompt.trim_end().to_owned();
    if !message.is_empty() {
        message.push_str("\n\n");
    }
    message.push_str("The user attached these documents:\n");
    for attachment in attachments {
        let (name, bytes) = load_upload(attachment, MAX_CHAT_ATTACHMENT_BYTES)?;
        let content = String::from_utf8(bytes)
            .map_err(|_| format!("{name} is not UTF-8 text and cannot be attached"))?;
        let name: String = name
            .chars()
            .map(|character| match character {
                '<' | '>' | '"' | '&' => '_',
                other => other,
            })
            .collect();
        message.push_str(&format!(
            "\n<attached_document name=\"{name}\">\n{}\n</attached_document>\n",
            content.trim_end()
        ));
    }
    Ok(message)
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
    let messages = store
        .load_messages(session)
        .map_err(|error| error.to_string())?;
    Ok(SessionView {
        session_id,
        messages: messages
            .iter()
            .filter_map(|message| match message.role.as_str() {
                "user" => {
                    let payload = &message.content;
                    Some(UiMessage {
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
                    })
                }
                "assistant" => {
                    let message = chat_message_from_session(message).ok()?;
                    Some(UiMessage {
                        role: "assistant".to_owned(),
                        text: message
                            .content
                            .filter(|text| !text.is_empty())
                            .unwrap_or_else(|| "(tool activity)".to_owned()),
                        attachments: Vec::new(),
                        reasoning: message.reasoning_content,
                    })
                }
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

fn mcp_server_view(config: &openbad_mcp::McpServerConfig) -> McpServerView {
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
        enabled: config.enabled,
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
        manager
            .add_server(config.clone())
            .map_err(|error| error.to_string())?;
        let (tools, warning) = probe_mcp_server(&manager, &name);
        Ok(McpServerChangeView {
            server: mcp_server_view(&config),
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
            .find(|server| server.name == name)
            .ok_or_else(|| format!("unknown MCP server: {name}"))?;
        let config = mcp_server_config_from_spec(&spec, Some(current))?;
        manager
            .update_server(&name, config.clone())
            .map_err(|error| error.to_string())?;
        let (tools, warning) = probe_mcp_server(&manager, &name);
        Ok(McpServerChangeView {
            server: mcp_server_view(&config),
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
    let mut service = openbad_update::UpdateService::new(repo);
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
    pub session_id: Option<String>,
    pub workspace_path: String,
    pub message: String,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub max_steps: Option<u32>,
    pub model: Option<String>,
    pub thinking: Option<bool>,
    pub reasoning_effort: Option<String>,
    #[serde(default)]
    pub attachments: Vec<UploadSource>,
}

#[tauri::command]
fn steer_chat(state: State<'_, AppState>, message: String) -> Result<(), String> {
    let message = message.trim();
    if message.is_empty() {
        return Err("steering message must not be empty".to_owned());
    }
    if state.steering.push(message.to_owned()) {
        Ok(())
    } else {
        Err("no active turn to steer".to_owned())
    }
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
        attachments,
    } = request;
    let prompt = message.trim().to_owned();
    if prompt.is_empty() && attachments.is_empty() {
        return Err("message must not be empty".to_owned());
    }
    let workspace_path = PathBuf::from(workspace_path);
    if !workspace_path.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            workspace_path.display()
        ));
    }
    let title_source = if prompt.is_empty() {
        "Attached documents".to_owned()
    } else {
        prompt.clone()
    };
    let message = compose_chat_message(&prompt, &attachments)?;

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
                let title = truncate_title(&title_source);
                let session = store
                    .create_session(Some(workspace), Some(&title))
                    .map_err(|error| error.to_string())?;
                (session, workspace.to_string())
            }
        };

        let access_mode = if allow_commands {
            "full"
        } else if allow_writes {
            "edit"
        } else {
            "read"
        };
        store
            .set_session_access_mode(session, access_mode)
            .map_err(|error| error.to_string())?;

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
                    .with_payload(json!({
                        "content": message,
                        "display": prompt,
                        "attachments": attachments
                            .iter()
                            .map(|attachment| attachment.name.clone())
                            .collect::<Vec<_>>(),
                    })),
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
        .with_steering(Arc::clone(&state.steering))
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
    state.steering.begin();
    let outcome = agent.run_with_observer(history, &observer).await;
    state.steering.end();
    let run = match outcome {
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
                ChatRole::User => {
                    store
                        .append_event(
                            session,
                            NewEvent::new(SessionEventKind::UserMessage)
                                .with_turn(turn)
                                .with_step(run.steps)
                                .with_payload(json!({
                                    "content": message.content.clone().unwrap_or_default()
                                })),
                        )
                        .map_err(|error| error.to_string())?;
                }
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
            cancel: Arc::new(AtomicBool::new(false)),
            steering: Arc::new(SteeringInbox::default()),
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
            cancel_chat,
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
        assert_eq!(payload.doctor.schema_version, "0013_session_fts");
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
            },
            UploadSource {
                name: "inline.txt".to_owned(),
                path: None,
                content: Some("inline body".to_owned()),
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
            },
            UploadSource {
                name: "../../etc/passwd".to_owned(),
                path: None,
                content: Some("safe".to_owned()),
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
            }],
        )
        .expect("upload");

        assert_eq!(report.files.len(), 1);
        assert_eq!(report.files[0].name, "notes-1.md");
        assert!(!target.path().join("escape.md").exists());
    }

    #[test]
    fn chat_attachments_are_embedded_and_bounded() {
        let message = compose_chat_message(
            "Review this",
            &[UploadSource {
                name: "design.md".to_owned(),
                path: None,
                content: Some("# Design\nbody".to_owned()),
            }],
        )
        .expect("message");
        assert!(message.starts_with("Review this\n\n"));
        assert!(message.contains("<attached_document name=\"design.md\">"));
        assert!(message.contains("# Design\nbody"));
        assert_eq!(compose_chat_message("", &[]).expect("plain"), "");

        let oversized = UploadSource {
            name: "huge.md".to_owned(),
            path: None,
            content: Some("x".repeat(MAX_CHAT_ATTACHMENT_BYTES as usize + 1)),
        };
        assert!(compose_chat_message("Review", &[oversized]).is_err());
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
    fn mcp_specs_infer_transport_and_preserve_unspecified_fields() {
        let spec = McpServerSpec {
            name: "docs".to_owned(),
            command: Some("npx".to_owned()),
            args: Some(vec!["-y".to_owned(), "@example/mcp-docs".to_owned()]),
            ..McpServerSpec::default()
        };
        let config = mcp_server_config_from_spec(&spec, None).expect("stdio config");
        assert_eq!(config.transport, openbad_mcp::McpTransport::Stdio);
        assert!(config.enabled);
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
        assert!(!updated.enabled);
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
