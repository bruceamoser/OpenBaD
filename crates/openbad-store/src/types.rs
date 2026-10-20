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
pub struct SessionSummary {
    pub id: SessionId,
    pub workspace_id: Option<WorkspaceId>,
    pub workspace_path: Option<String>,
    pub title: Option<String>,
    pub status: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub last_turn: u32,
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
