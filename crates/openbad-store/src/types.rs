use openbad_core::{ModelRouteId, ProviderId, SessionId, WorkspaceId};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NewWorkspace {
    pub name: String,
    pub path: String,
}

impl NewWorkspace {
    #[must_use]
    pub fn new(name: impl Into<String>, path: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            path: path.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NewSession {
    pub workspace_id: Option<WorkspaceId>,
    pub title: Option<String>,
}

impl NewSession {
    #[must_use]
    pub fn new(workspace_id: Option<WorkspaceId>, title: Option<String>) -> Self {
        Self {
            workspace_id,
            title,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionMessage {
    pub session_id: SessionId,
    pub seq: u64,
    pub turn: Option<u32>,
    pub step: Option<u32>,
    pub role: String,
    pub content: Value,
    pub source: Option<String>,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionTurn {
    pub session_id: SessionId,
    pub turn: u32,
    pub started_at: i64,
    pub ended_at: Option<i64>,
    pub reason: Option<String>,
    pub steps: u32,
    pub input_tokens: u64,
    pub cache_read_tokens: u64,
    pub output_tokens: u64,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NewProvider {
    pub kind: String,
    pub name: String,
    pub base_url: Option<String>,
    pub keychain_ref: Option<String>,
}

impl NewProvider {
    #[must_use]
    pub fn new(kind: impl Into<String>, name: impl Into<String>) -> Self {
        Self {
            kind: kind.into(),
            name: name.into(),
            base_url: None,
            keychain_ref: None,
        }
    }

    #[must_use]
    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = Some(base_url.into());
        self
    }

    #[must_use]
    pub fn with_keychain_ref(mut self, keychain_ref: impl Into<String>) -> Self {
        self.keychain_ref = Some(keychain_ref.into());
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRecord {
    pub id: ProviderId,
    pub kind: String,
    pub name: String,
    pub base_url: Option<String>,
    pub keychain_ref: Option<String>,
    pub enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NewModelRoute {
    pub name: String,
    pub task_class: String,
    pub provider_id: ProviderId,
    pub model: String,
    pub context_window: u32,
    pub max_output_tokens: u32,
    pub pricing: Value,
    pub peak_pricing: Value,
    pub cache_mode: String,
    pub api_format: String,
    pub thinking_default: String,
    pub capabilities: Value,
}

impl NewModelRoute {
    #[must_use]
    pub fn new(
        name: impl Into<String>,
        task_class: impl Into<String>,
        provider_id: ProviderId,
        model: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            task_class: task_class.into(),
            provider_id,
            model: model.into(),
            context_window: 1_000_000,
            max_output_tokens: 384_000,
            pricing: Value::Object(serde_json::Map::new()),
            peak_pricing: Value::Object(serde_json::Map::new()),
            cache_mode: "automatic".to_owned(),
            api_format: "openai".to_owned(),
            thinking_default: "enabled".to_owned(),
            capabilities: Value::Object(serde_json::Map::new()),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelRouteRecord {
    pub id: ModelRouteId,
    pub name: String,
    pub task_class: String,
    pub provider_id: ProviderId,
    pub model: String,
    pub context_window: u32,
    pub max_output_tokens: u32,
    pub pricing: Value,
    pub peak_pricing: Value,
    pub cache_mode: String,
    pub api_format: String,
    pub thinking_default: String,
    pub capabilities: Value,
    pub enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceSummary {
    pub id: WorkspaceId,
    pub name: String,
    pub path: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub session_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionSummary {
    pub id: SessionId,
    pub workspace_id: Option<WorkspaceId>,
    pub workspace_path: Option<String>,
    pub title: Option<String>,
    pub status: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub last_turn: u32,
    pub access_mode: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutsideAccessGrant {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub path: String,
    pub write_access: bool,
    pub reason: String,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DoctorReport {
    pub schema_version: String,
    pub integrity: String,
    pub database_bytes: i64,
    pub event_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditReport {
    pub valid: bool,
    pub records: u64,
    pub first_invalid: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AuditRecord {
    pub id: Uuid,
    pub ts: i64,
    pub actor: String,
    pub action: String,
    pub target: Option<String>,
    pub decision: Option<String>,
    pub policy: Option<String>,
    pub metadata: Value,
    pub prev_hash: Vec<u8>,
    pub hash: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexedSymbol {
    pub name: String,
    pub kind: String,
    pub line: u32,
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexedFile {
    pub path: String,
    pub hash: String,
    pub size: u64,
    pub modified_at: i64,
    pub language: Option<String>,
    pub content: String,
    pub symbols: Vec<IndexedSymbol>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IndexHit {
    pub path: String,
    pub snippet: String,
    pub score: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SymbolHit {
    pub path: String,
    pub name: String,
    pub kind: String,
    pub line: u32,
    pub signature: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct IndexSummary {
    pub files: u64,
    pub symbols: u64,
    pub bytes: u64,
    pub indexed_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileCheckpointSummary {
    pub id: String,
    pub path: String,
    pub existed: bool,
    pub bytes: u64,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileCheckpoint {
    pub id: String,
    pub session_id: SessionId,
    pub path: String,
    pub existed: bool,
    pub content: Option<Vec<u8>>,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryRecord {
    pub id: String,
    pub content: String,
    pub tags: Vec<String>,
    pub pinned: bool,
    pub created_at: i64,
    pub updated_at: i64,
    pub access_count: u32,
    pub origin_turn: Option<u32>,
    pub origin_task: Option<String>,
    pub author: String,
    pub scope: String,
    pub supersedes: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectRepo {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub name: String,
    pub path: String,
    pub read_only: bool,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeDocumentSummary {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub title: String,
    pub kind: String,
    pub source: Option<String>,
    pub tags: Vec<String>,
    pub characters: u64,
    pub chunks: u64,
    pub confidence: String,
    pub source_hash: Option<String>,
    pub supersedes: Option<String>,
    pub review_status: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeDocument {
    pub summary: KnowledgeDocumentSummary,
    pub content: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProjectHit {
    pub source: String,
    pub title: String,
    pub snippet: String,
    pub locator: String,
    pub score: f64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectOverview {
    pub repos: u64,
    pub knowledge_documents: u64,
    pub knowledge_characters: u64,
    pub memories: u64,
    pub index: IndexSummary,
}

/// A background subagent spawned by the main agent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubagentRecord {
    pub id: String,
    pub workspace_id: WorkspaceId,
    pub parent_session_id: Option<String>,
    pub title: String,
    pub task: String,
    pub model: String,
    pub status: String,
    pub log: String,
    pub result: String,
    pub error: Option<String>,
    pub steps: u32,
    pub created_at: i64,
    pub updated_at: i64,
    pub finished_at: Option<i64>,
}

/// A patch applied to a subagent record.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubagentUpdate {
    pub status: Option<String>,
    pub append_log: Option<String>,
    pub result: Option<String>,
    pub error: Option<String>,
    pub steps: Option<u32>,
}
