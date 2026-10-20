//! Tool registry, schemas, and built-in coding tools.
#![recursion_limit = "256"]

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant};

use async_trait::async_trait;
use globset::Glob;
use ignore::WalkBuilder;
use openbad_governance::RiskLevel;
use openbad_workspace::WorkspaceService;
use serde_json::{Value, json};
use wait_timeout::ChildExt;

/// A model-facing tool definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub risk: RiskLevel,
    pub strict: bool,
    pub timeout_seconds: u64,
    pub input_schema: Value,
}

impl ToolDefinition {
    #[must_use]
    pub fn new(name: impl Into<String>, description: impl Into<String>, risk: RiskLevel) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            risk,
            strict: false,
            timeout_seconds: 30,
            input_schema: json!({"type": "object", "properties": {}}),
        }
    }

    #[must_use]
    pub fn with_schema(mut self, input_schema: Value) -> Self {
        self.input_schema = input_schema;
        self
    }

    #[must_use]
    pub fn with_timeout(mut self, timeout_seconds: u64) -> Self {
        self.timeout_seconds = timeout_seconds;
        self
    }

    #[must_use]
    pub fn with_strict(mut self, strict: bool) -> Self {
        self.strict = strict;
        self
    }

    #[must_use]
    pub fn openai_schema(&self) -> Value {
        let mut function = json!({
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        });
        if self.strict {
            function["strict"] = json!(true);
        }
        json!({"type": "function", "function": function})
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ToolResult {
    pub content: String,
    pub is_error: bool,
    pub metadata: Value,
}

impl ToolResult {
    #[must_use]
    pub fn ok(content: impl Into<String>) -> Self {
        Self {
            content: content.into(),
            is_error: false,
            metadata: Value::Null,
        }
    }

    #[must_use]
    pub fn error(content: impl Into<String>) -> Self {
        Self {
            content: content.into(),
            is_error: true,
            metadata: Value::Null,
        }
    }

    #[must_use]
    pub fn with_metadata(mut self, metadata: Value) -> Self {
        self.metadata = metadata;
        self
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ToolError {
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("approval required for tool: {0}")]
    ApprovalRequired(String),
    #[error("path is outside the workspace: {0}")]
    OutsideRoot(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("workspace error: {0}")]
    Workspace(#[from] openbad_workspace::WorkspaceError),
    #[error("tool timed out after {0} seconds")]
    Timeout(u64),
    #[error("tool failed: {0}")]
    Failed(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSearchHit {
    pub path: String,
    pub snippet: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSymbolHit {
    pub path: String,
    pub name: String,
    pub kind: String,
    pub line: u32,
    pub signature: String,
}

/// Optional indexed search supplied by the application. Tools fall back to a
/// live filesystem scan when no index is attached.
pub trait WorkspaceSearch: Send + Sync {
    fn search(&self, query: &str, limit: usize) -> Result<Vec<WorkspaceSearchHit>, String>;
    fn find_symbols(
        &self,
        query: &str,
        kind: Option<&str>,
        limit: usize,
    ) -> Result<Vec<WorkspaceSymbolHit>, String>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryEntry {
    pub id: String,
    pub content: String,
    pub tags: Vec<String>,
    pub pinned: bool,
    pub scope: String,
    pub author: String,
    pub origin_turn: Option<u32>,
    pub origin_task: Option<String>,
    pub supersedes: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryWriteOptions {
    pub scope: String,
    pub author: String,
    pub origin_turn: Option<u32>,
    pub origin_task: Option<String>,
    pub supersedes: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectRepoRef {
    pub id: String,
    pub name: String,
    pub path: String,
    pub read_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KnowledgeEntryRef {
    pub id: String,
    pub title: String,
    pub kind: String,
    pub source: Option<String>,
    pub chunks: u64,
    pub confidence: String,
    pub stale: bool,
    pub supersedes: Option<String>,
    pub review_status: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KnowledgeDocumentRef {
    pub id: String,
    pub title: String,
    pub kind: String,
    pub source: Option<String>,
    pub tags: Vec<String>,
    pub content: String,
    pub chunks: u64,
    pub confidence: String,
    pub stale: bool,
    pub supersedes: Option<String>,
    pub review_status: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ProjectHit {
    pub source: String,
    pub title: String,
    pub snippet: String,
    pub locator: String,
    pub score: f64,
}

/// Project-owned knowledge, linked repositories, and retrieval.
pub trait ProjectAreas: Send + Sync {
    fn list_repos(&self) -> Result<Vec<ProjectRepoRef>, String>;
    fn list_knowledge(&self) -> Result<Vec<KnowledgeEntryRef>, String>;
    fn add_knowledge(
        &self,
        title: &str,
        content: &str,
        source: Option<&str>,
        tags: Vec<String>,
        confidence: &str,
    ) -> Result<KnowledgeEntryRef, String>;
    #[allow(clippy::too_many_arguments)]
    fn add_knowledge_detailed(
        &self,
        title: &str,
        content: &str,
        _kind: &str,
        source: Option<&str>,
        tags: Vec<String>,
        confidence: &str,
        _supersedes: Option<&str>,
        _review_status: &str,
    ) -> Result<KnowledgeEntryRef, String> {
        self.add_knowledge(title, content, source, tags, confidence)
    }
    fn review_knowledge(&self, _id: &str, _status: &str) -> Result<KnowledgeDocumentRef, String> {
        Err("knowledge review is not available".to_owned())
    }
    #[allow(clippy::too_many_arguments)]
    fn update_knowledge_detailed(
        &self,
        id: &str,
        title: Option<&str>,
        content: Option<&str>,
        _kind: Option<&str>,
        source: Option<&str>,
        tags: Option<Vec<String>>,
        confidence: Option<&str>,
        _supersedes: Option<&str>,
        _review_status: Option<&str>,
    ) -> Result<KnowledgeDocumentRef, String> {
        self.update_knowledge(id, title, content, source, tags, confidence)
    }
    fn get_knowledge(&self, _id: &str) -> Result<Option<KnowledgeDocumentRef>, String> {
        Err("knowledge document lookup is not available".to_owned())
    }
    fn update_knowledge(
        &self,
        _id: &str,
        _title: Option<&str>,
        _content: Option<&str>,
        _source: Option<&str>,
        _tags: Option<Vec<String>>,
        _confidence: Option<&str>,
    ) -> Result<KnowledgeDocumentRef, String> {
        Err("knowledge updates are not available".to_owned())
    }
    fn delete_knowledge(&self, _id: &str) -> Result<(), String> {
        Err("knowledge deletes are not available".to_owned())
    }
    fn retrieve(&self, query: &str, limit: usize) -> Result<Vec<ProjectHit>, String>;
}

pub trait MemoryStore: Send + Sync {
    fn remember(
        &self,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
    ) -> Result<MemoryEntry, String>;
    fn recall(&self, query: Option<&str>, limit: usize) -> Result<Vec<MemoryEntry>, String>;
    fn remember_scoped(
        &self,
        content: &str,
        tags: Vec<String>,
        pinned: bool,
        _options: MemoryWriteOptions,
    ) -> Result<MemoryEntry, String> {
        self.remember(content, tags, pinned)
    }
    fn update(
        &self,
        _id: &str,
        _content: Option<&str>,
        _tags: Option<Vec<String>>,
        _pinned: Option<bool>,
        _scope: Option<&str>,
    ) -> Result<MemoryEntry, String> {
        Err("memory updates are not available".to_owned())
    }
    fn list(&self, limit: usize) -> Result<Vec<MemoryEntry>, String> {
        self.recall(None, limit)
    }
    fn forget(&self, _id: &str) -> Result<(), String> {
        Err("memory deletion is not available".to_owned())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub path: PathBuf,
    pub builtin: bool,
}

const BUILTIN_SKILLS: &[&str] = &[
    include_str!("../assets/skills/github-workflow.md"),
    include_str!("../assets/skills/github-releases-pages.md"),
    include_str!("../assets/skills/plugin-development.md"),
    include_str!("../assets/skills/asciidoc-rendering.md"),
];

#[must_use]
pub fn builtin_skill_content(name: &str) -> Option<&'static str> {
    BUILTIN_SKILLS.iter().copied().find(|content| {
        let (skill_name, _) = parse_skill_frontmatter(content, "");
        skill_name == name
    })
}

/// Discover built-in skills plus workspace skills from `.openbad/skills`,
/// `.claude/skills`, and `.openbad/plugins/*/SKILL.md`. Workspace skills win
/// when they define the same name.
#[must_use]
pub fn discover_skills(root: &Path) -> Vec<Skill> {
    let mut skills = BTreeMap::new();
    for content in BUILTIN_SKILLS {
        let (name, description) = parse_skill_frontmatter(content, "skill");
        skills.insert(
            name.clone(),
            Skill {
                path: PathBuf::from(format!("<builtin:{name}>")),
                name,
                description,
                builtin: true,
            },
        );
    }
    let Ok(canonical_root) = root.canonicalize() else {
        return skills.into_values().collect();
    };
    for relative in [".claude/skills", ".openbad/skills"] {
        let directory = root.join(relative);
        let Ok(entries) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            let candidate = if path.is_dir() {
                path.join("SKILL.md")
            } else if path.extension().is_some_and(|extension| extension == "md") {
                path
            } else {
                continue;
            };
            if !candidate.is_file() {
                continue;
            }
            let Ok(candidate) = candidate.canonicalize() else {
                continue;
            };
            if !candidate.starts_with(&canonical_root) {
                continue;
            }
            let Ok(content) = std::fs::read_to_string(&candidate) else {
                continue;
            };
            let fallback = candidate
                .parent()
                .and_then(Path::file_name)
                .and_then(|name| name.to_str())
                .unwrap_or("skill");
            let (name, description) = parse_skill_frontmatter(&content, fallback);
            skills.insert(
                name.clone(),
                Skill {
                    name,
                    description,
                    path: candidate,
                    builtin: false,
                },
            );
        }
    }
    let plugins = root.join(".openbad").join("plugins");
    if let Ok(entries) = std::fs::read_dir(&plugins) {
        for entry in entries.filter_map(Result::ok) {
            let candidate = entry.path().join("SKILL.md");
            let Ok(candidate) = candidate.canonicalize() else {
                continue;
            };
            if !candidate.starts_with(&canonical_root) || !candidate.is_file() {
                continue;
            }
            let Ok(content) = std::fs::read_to_string(&candidate) else {
                continue;
            };
            let fallback = candidate
                .parent()
                .and_then(Path::file_name)
                .and_then(|name| name.to_str())
                .unwrap_or("plugin");
            let (name, description) = parse_skill_frontmatter(&content, fallback);
            skills.insert(
                name.clone(),
                Skill {
                    name,
                    description,
                    path: candidate,
                    builtin: false,
                },
            );
        }
    }
    skills.into_values().collect()
}

fn parse_skill_frontmatter(content: &str, fallback: &str) -> (String, String) {
    let mut name = fallback.to_owned();
    let mut description = String::new();
    let mut lines = content.lines();
    if lines.next().is_some_and(|line| line.trim() == "---") {
        for line in lines {
            let line = line.trim();
            if line == "---" {
                break;
            }
            if let Some(value) = line.strip_prefix("name:") {
                name = value.trim().trim_matches('"').to_owned();
            } else if let Some(value) = line.strip_prefix("description:") {
                description = value.trim().trim_matches('"').to_owned();
            }
        }
    }
    (name, description)
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PluginAction {
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub input_schema: Option<Value>,
}

fn default_plugin_timeout() -> u64 {
    60
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PluginManifest {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub version: String,
    pub command: String,
    #[serde(default)]
    pub test_command: Option<String>,
    #[serde(default = "default_plugin_timeout")]
    pub timeout_seconds: u64,
    #[serde(default)]
    pub actions: BTreeMap<String, PluginAction>,
    #[serde(default)]
    pub permissions: Vec<String>,
}

impl PluginManifest {
    #[must_use]
    pub fn action_names(&self) -> Vec<String> {
        self.actions.keys().cloned().collect()
    }
}

fn valid_plugin_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('.')
        && name.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
        })
}

fn plugin_root(root: &Path) -> PathBuf {
    root.join(".openbad").join("plugins")
}

fn plugin_directory(root: &Path, name: &str) -> Result<PathBuf, ToolError> {
    if !valid_plugin_name(name) {
        return Err(ToolError::InvalidArgument(format!(
            "invalid plugin name: {name}"
        )));
    }
    let plugins = plugin_root(root);
    let directory = plugins.join(name);
    let canonical_plugins = plugins
        .canonicalize()
        .map_err(|error| ToolError::Failed(format!("plugin directory is unavailable: {error}")))?;
    let canonical = directory
        .canonicalize()
        .map_err(|error| ToolError::Failed(format!("plugin not found: {name} ({error})")))?;
    if !canonical.starts_with(&canonical_plugins) {
        return Err(ToolError::Failed(format!(
            "plugin directory escapes .openbad/plugins: {name}"
        )));
    }
    Ok(canonical)
}

/// Discover plugins declared under `.openbad/plugins/*/plugin.yaml`.
#[must_use]
pub fn discover_plugins(root: &Path) -> Vec<PluginManifest> {
    let plugins = plugin_root(root);
    let Ok(entries) = std::fs::read_dir(&plugins) else {
        return Vec::new();
    };
    let mut found = BTreeMap::new();
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path().join("plugin.yaml");
        let Ok(content) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(manifest) = serde_yaml::from_str::<PluginManifest>(&content) else {
            continue;
        };
        found.insert(manifest.name.clone(), manifest);
    }
    found.into_values().collect()
}

fn load_plugin(root: &Path, name: &str) -> Result<(PluginManifest, PathBuf), ToolError> {
    let directory = plugin_directory(root, name)?;
    let path = directory.join("plugin.yaml");
    let content = std::fs::read_to_string(&path)
        .map_err(|error| ToolError::Failed(format!("cannot read {}: {error}", path.display())))?;
    let manifest: PluginManifest = serde_yaml::from_str(&content)
        .map_err(|error| ToolError::Failed(format!("invalid plugin.yaml: {error}")))?;
    if manifest.name != name {
        return Err(ToolError::Failed(format!(
            "plugin.yaml name {} does not match directory {name}",
            manifest.name
        )));
    }
    if manifest.command.trim().is_empty() {
        return Err(ToolError::Failed(format!(
            "plugin {name} has an empty command"
        )));
    }
    Ok((manifest, directory))
}

fn plugin_command_class(manifest: &PluginManifest) -> CommandClass {
    if manifest
        .permissions
        .iter()
        .any(|permission| permission.eq_ignore_ascii_case("destructive"))
    {
        CommandClass::Destructive
    } else if manifest
        .permissions
        .iter()
        .any(|permission| permission.eq_ignore_ascii_case("network"))
    {
        CommandClass::Network
    } else if manifest
        .permissions
        .iter()
        .any(|permission| permission.eq_ignore_ascii_case("write"))
    {
        CommandClass::WriteWorkspace
    } else {
        classify_command(&manifest.command)
    }
}

struct PluginRunOutcome {
    content: String,
    metadata: Value,
    is_error: bool,
}

#[allow(clippy::too_many_lines)]
fn run_plugin_process(
    root: &Path,
    manifest: &PluginManifest,
    directory: &Path,
    action: &str,
    arguments: &Value,
    timeout_seconds: u64,
    max_output_bytes: usize,
) -> Result<PluginRunOutcome, ToolError> {
    let payload = json!({
        "plugin": manifest.name,
        "action": action,
        "arguments": arguments,
        "workspace": root,
    });
    let mut process = if cfg!(windows) {
        let mut process = std::process::Command::new("cmd");
        process.args(["/C", &manifest.command]);
        process
    } else {
        let mut process = std::process::Command::new("sh");
        process.args(["-c", &manifest.command]);
        process
    };
    process
        .current_dir(directory)
        .env("OPENBAD_WORKSPACE", root)
        .env("OPENBAD_PLUGIN", &manifest.name)
        .env("OPENBAD_PLUGIN_ACTION", action)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        process.process_group(0);
    }
    let started = Instant::now();
    let mut child = process.spawn().map_err(|error| {
        ToolError::Failed(format!("cannot start plugin {}: {error}", manifest.name))
    })?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(payload.to_string().as_bytes())?;
    }
    let stdout_reader = child.stdout.take().map(|mut stream| {
        std::thread::spawn(move || {
            let mut output = String::new();
            let _ = stream.read_to_string(&mut output);
            output
        })
    });
    let stderr_reader = child.stderr.take().map(|mut stream| {
        std::thread::spawn(move || {
            let mut output = String::new();
            let _ = stream.read_to_string(&mut output);
            output
        })
    });
    let status = child.wait_timeout(Duration::from_secs(timeout_seconds))?;
    kill_process_tree(&mut child);
    let status = match status {
        Some(status) => status,
        None => {
            let _ = child.wait();
            return Err(ToolError::Timeout(timeout_seconds));
        }
    };
    let stdout = stdout_reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default();
    let stderr = stderr_reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default();
    let duration_ms = started.elapsed().as_millis();
    let exit_code = status.code().unwrap_or(-1);
    let trimmed = stdout.trim();
    let parsed = serde_json::from_str::<Value>(trimmed).ok();
    let mut is_error = !status.success();
    let mut metadata = json!({});
    let content = match parsed {
        Some(Value::Object(mut object)) => {
            if object.get("ok").and_then(Value::as_bool) == Some(false)
                || object.get("is_error").and_then(Value::as_bool) == Some(true)
            {
                is_error = true;
            }
            if let Some(value) = object.remove("metadata") {
                metadata = value;
            }
            match object.remove("content") {
                Some(Value::String(text)) => text,
                Some(other) => other.to_string(),
                None => match object.get("data") {
                    Some(data) => serde_json::to_string_pretty(data).unwrap_or_default(),
                    None => trimmed.to_owned(),
                },
            }
        }
        Some(other) => serde_json::to_string_pretty(&other).unwrap_or_default(),
        None => stdout.clone(),
    };
    let mut content = if content.trim().is_empty() {
        if stderr.trim().is_empty() {
            format!(
                "plugin {} action {action} produced no output",
                manifest.name
            )
        } else {
            stderr.clone()
        }
    } else {
        content
    };
    if is_error && !stderr.trim().is_empty() && !content.contains(stderr.trim()) {
        content.push_str("\n\nstderr:\n");
        content.push_str(stderr.trim());
    }
    let content = truncate(&content, max_output_bytes);
    let evidence = record_plugin_run(
        root,
        manifest,
        action,
        exit_code,
        duration_ms,
        &stdout,
        &stderr,
    );
    if let Some(map) = metadata.as_object_mut() {
        map.insert("plugin".to_owned(), json!(manifest.name));
        map.insert("action".to_owned(), json!(action));
        map.insert("exit_code".to_owned(), json!(exit_code));
        map.insert("duration_ms".to_owned(), json!(duration_ms));
        if let Ok(path) = &evidence {
            map.insert("evidence_path".to_owned(), json!(path));
        }
    }
    Ok(PluginRunOutcome {
        content,
        metadata,
        is_error,
    })
}

fn record_plugin_run(
    root: &Path,
    manifest: &PluginManifest,
    action: &str,
    exit_code: i32,
    duration_ms: u128,
    stdout: &str,
    stderr: &str,
) -> Result<PathBuf, std::io::Error> {
    let directory = root.join(".openbad").join("plugin-runs");
    std::fs::create_dir_all(&directory)?;
    let safe_action: String = action
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                character
            } else {
                '-'
            }
        })
        .collect();
    let path = directory.join(format!("{}-{safe_action}.json", now_millis()));
    let record = json!({
        "plugin": manifest.name,
        "action": action,
        "command": manifest.command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout": truncate(stdout, 16 * 1024),
        "stderr": truncate(stderr, 16 * 1024),
        "recorded_at": now_millis(),
    });
    std::fs::write(
        &path,
        serde_json::to_string_pretty(&record).unwrap_or_default(),
    )?;
    Ok(path)
}

pub trait CheckpointSink: Send + Sync {
    fn save(&self, path: &str, existed: bool, content: Option<&[u8]>) -> Result<String, String>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutsideGrant {
    pub id: String,
    pub path: PathBuf,
    pub write_access: bool,
    pub reason: String,
}

pub struct ToolContext {
    pub workspace: Arc<WorkspaceService>,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub max_output_bytes: usize,
    pub search: Option<Arc<dyn WorkspaceSearch>>,
    pub checkpoints: Option<Arc<dyn CheckpointSink>>,
    pub memory: Option<Arc<dyn MemoryStore>>,
    pub project: Option<Arc<dyn ProjectAreas>>,
    turn: AtomicU32,
    read_paths: Mutex<HashSet<PathBuf>>,
    outside_grants: Vec<OutsideGrant>,
}

impl ToolContext {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceService>) -> Self {
        Self {
            workspace,
            allow_writes: false,
            allow_commands: false,
            max_output_bytes: 256 * 1024,
            search: None,
            checkpoints: None,
            memory: None,
            project: None,
            turn: AtomicU32::new(0),
            read_paths: Mutex::new(HashSet::new()),
            outside_grants: Vec::new(),
        }
    }

    pub fn set_outside_grants(&mut self, grants: Vec<OutsideGrant>) {
        self.outside_grants = grants
            .into_iter()
            .map(|grant| OutsideGrant {
                path: grant.path.canonicalize().unwrap_or(grant.path),
                ..grant
            })
            .collect();
    }

    #[must_use]
    pub fn outside_grants(&self) -> &[OutsideGrant] {
        &self.outside_grants
    }

    pub fn resolve_read(&self, path: &Path) -> Result<PathBuf, ToolError> {
        match self.workspace.resolve(path) {
            Ok(resolved) => Ok(resolved),
            Err(openbad_workspace::WorkspaceError::OutsideRoot(_)) => {
                self.resolve_granted(path, false)
            }
            Err(error) => Err(error.into()),
        }
    }

    pub fn resolve_write(&self, path: &Path) -> Result<PathBuf, ToolError> {
        match self.workspace.resolve(path) {
            Ok(resolved) => Ok(resolved),
            Err(openbad_workspace::WorkspaceError::OutsideRoot(_)) => {
                self.resolve_granted(path, true)
            }
            Err(error) => Err(error.into()),
        }
    }

    fn resolve_granted(&self, path: &Path, write: bool) -> Result<PathBuf, ToolError> {
        let candidate = if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.workspace.root().join(path)
        };
        let candidate = normalize_lexical(&candidate);
        let resolved = if candidate.exists() {
            candidate.canonicalize()?
        } else {
            let mut resolved = None;
            let mut ancestor = candidate.parent();
            while let Some(parent) = ancestor {
                if parent.exists() {
                    let canonical = parent.canonicalize()?;
                    let suffix = candidate
                        .strip_prefix(parent)
                        .map_err(|_| ToolError::OutsideRoot(path.display().to_string()))?;
                    resolved = Some(canonical.join(suffix));
                    break;
                }
                ancestor = parent.parent();
            }
            resolved.ok_or_else(|| ToolError::OutsideRoot(path.display().to_string()))?
        };
        if self
            .outside_grants
            .iter()
            .any(|grant| resolved.starts_with(&grant.path) && (!write || grant.write_access))
        {
            Ok(resolved)
        } else {
            Err(ToolError::OutsideRoot(path.display().to_string()))
        }
    }

    pub fn set_turn(&self, turn: u32) {
        self.turn.store(turn, Ordering::Relaxed);
    }

    #[must_use]
    pub fn current_turn(&self) -> Option<u32> {
        let turn = self.turn.load(Ordering::Relaxed);
        (turn > 0).then_some(turn)
    }

    #[must_use]
    pub fn loaded_paths(&self) -> Vec<PathBuf> {
        self.read_paths
            .lock()
            .map(|paths| paths.iter().cloned().collect())
            .unwrap_or_default()
    }

    fn mark_read(&self, path: &Path) {
        if let Ok(mut paths) = self.read_paths.lock() {
            paths.insert(path.to_path_buf());
        }
    }

    fn was_read(&self, path: &Path) -> bool {
        self.read_paths
            .lock()
            .map(|paths| paths.contains(path))
            .unwrap_or(false)
    }
}

#[async_trait]
pub trait Tool: Send + Sync {
    fn definition(&self) -> ToolDefinition;
    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError>;
}

#[derive(Default)]
pub struct ToolRegistry {
    tools: BTreeMap<String, Arc<dyn Tool>>,
}

impl ToolRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn with_builtin_tools() -> Self {
        let mut registry = Self::new();
        registry.register(ReadFileTool);
        registry.register(StatFileTool);
        registry.register(RequestFilesystemAccessTool);
        registry.register(StatRepoFileTool);
        registry.register(ReadFilesTool);
        registry.register(ReadRepoFilesTool);
        registry.register(OutlineTool);
        registry.register(DeleteFileTool);
        registry.register(MoveFileTool);
        registry.register(ListTrashTool);
        registry.register(RestoreTrashTool);
        registry.register(ListDirTool);
        registry.register(GrepTool);
        registry.register(SearchCodeTool);
        registry.register(GlobTool);
        registry.register(FindSymbolsTool);
        registry.register(WriteFileTool);
        registry.register(EditFileTool);
        registry.register(RunCommandTool);
        registry.register(RunTestsTool);
        registry.register(GitStatusTool);
        registry.register(GitDiffTool);
        registry.register(RepoMapTool);
        registry.register(RememberTool);
        registry.register(RecallTool);
        registry.register(ListMemoriesTool);
        registry.register(UpdateMemoryTool);
        registry.register(ForgetMemoryTool);
        registry.register(SearchProjectTool);
        registry.register(AddKnowledgeTool);
        registry.register(ListKnowledgeTool);
        registry.register(GetKnowledgeTool);
        registry.register(UpdateKnowledgeTool);
        registry.register(ExportKnowledgeTool);
        registry.register(ImportKnowledgeTool);
        registry.register(ReviewKnowledgeTool);
        registry.register(DeleteKnowledgeTool);
        registry.register(ListProjectSourcesTool);
        registry.register(ReadRepoFileTool);
        registry.register(ListRepoDirTool);
        registry.register(GlobRepoTool);
        registry.register(GrepRepoTool);
        registry.register(WebFetchTool);
        registry.register(PresentArtifactTool);
        registry.register(UndoLastTool);
        registry.register(InstructionsForTool);
        registry.register(ScaffoldProjectTool);
        registry.register(WorkspaceGcTool);
        registry.register(SessionCheckpointTool);
        registry.register(SessionResumeTool);
        registry.register(ReferencesToTool);
        registry.register(GoldenCompareTool);
        registry.register(GoldenAcceptTool);
        registry.register(PlanWriteTool);
        registry.register(PlanReadTool);
        registry.register(PlanUpdateStepTool);
        registry.register(PlanVerifyTool);
        registry.register(DiffFilesTool);
        registry.register(FileCitationTool);
        registry.register(ArchiveListTool);
        registry.register(ArchiveReadTool);
        registry.register(GitChangesSinceTool);
        registry.register(RegistryLookupTool);
        registry.register(JobStartTool);
        registry.register(JobStatusTool);
        registry.register(JobLogsTool);
        registry.register(JobStopTool);
        registry.register(JobListTool);
        registry.register(ContextReportTool);
        registry.register(RecordClaimTool);
        registry.register(ListClaimsTool);
        registry.register(RecordUnknownTool);
        registry.register(ListUnknownsTool);
        registry.register(PreviewWriteTool);
        registry.register(SnapshotCreateTool);
        registry.register(SnapshotListTool);
        registry.register(SnapshotRestoreTool);
        registry.register(AssertProjectTool);
        registry.register(GitCommitTool);
        registry.register(GitBranchTool);
        registry.register(GitCheckoutTool);
        registry.register(GitLogTool);
        registry.register(GitShowTool);
        registry.register(GitBlameTool);
        registry.register(GitStashTool);
        registry.register(GitWorktreeTool);
        registry.register(RepoHealthTool);
        registry.register(GitCommitContextTool);
        registry.register(GithubTool);
        registry.register(GithubIssueTool);
        registry.register(GithubPrTool);
        registry.register(GithubCiTool);
        registry.register(CanonBanTool);
        registry.register(CanonAddTool);
        registry.register(CanonQueryTool);
        registry.register(CanonLintTool);
        registry.register(GlossaryAddTool);
        registry.register(GlossaryLookupTool);
        registry.register(AdrWriteTool);
        registry.register(AdrListTool);
        registry.register(AdrReadTool);
        registry.register(YamlQueryTool);
        registry.register(CheckInvariantsTool);
        registry.register(AskQuestionTool);
        registry.register(ListQuestionsTool);
        registry.register(PinContextTool);
        registry.register(ExcludeContextTool);
        registry.register(ListContextRulesTool);
        registry.register(ContextMapTool);
        registry.register(RecordDeadEndTool);
        registry.register(ListDeadEndsTool);
        registry.register(ReadProjectManifestTool);
        registry.register(ClassifyPathTool);
        registry.register(RunTaskTool);
        registry.register(ListRecipesTool);
        registry.register(ListGatesTool);
        registry.register(RunGateTool);
        registry.register(RunRecipeTool);
        registry.register(TodoWriteTool);
        registry.register(TodoReadTool);
        registry.register(WriteHandoffTool);
        registry.register(WriteSkillTool);
        registry.register(ListSkillsTool);
        registry.register(ReadSkillTool);
        registry.register(ListPluginsTool);
        registry.register(DescribePluginTool);
        registry.register(CallPluginTool);
        registry.register(TestPluginTool);
        registry.register(WritePluginTool);
        registry.register(InstallBuiltinPluginTool);
        registry
    }

    pub fn register<T: Tool + 'static>(&mut self, tool: T) {
        self.tools
            .insert(tool.definition().name.clone(), Arc::new(tool));
    }

    #[must_use]
    pub fn definitions(&self) -> Vec<ToolDefinition> {
        self.tools.values().map(|tool| tool.definition()).collect()
    }

    #[must_use]
    pub fn openai_tools(&self) -> Vec<Value> {
        self.definitions()
            .iter()
            .map(ToolDefinition::openai_schema)
            .collect()
    }

    pub async fn execute(
        &self,
        name: &str,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let tool = self
            .tools
            .get(name)
            .ok_or_else(|| ToolError::InvalidArgument(format!("unknown tool: {name}")))?;
        tool.execute(arguments, context).await
    }
}

fn string_arg(arguments: &Value, name: &str) -> Result<String, ToolError> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| ToolError::InvalidArgument(format!("missing string argument: {name}")))
}

fn optional_string(arguments: &Value, name: &str) -> Option<String> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .map(str::to_owned)
}

fn optional_usize(arguments: &Value, name: &str) -> Option<usize> {
    arguments
        .get(name)
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CommandClass {
    ReadOnly,
    WriteWorkspace,
    Network,
    Destructive,
    Privileged,
}

impl CommandClass {
    fn label(self) -> &'static str {
        match self {
            Self::ReadOnly => "read-only",
            Self::WriteWorkspace => "write-workspace",
            Self::Network => "network",
            Self::Destructive => "destructive",
            Self::Privileged => "privileged",
        }
    }
}

fn classify_command(command: &str) -> CommandClass {
    let lower = command.to_ascii_lowercase();
    let padded = format!(" {lower} ");
    if [" sudo ", " doas ", " su -", " pkexec "]
        .iter()
        .any(|needle| padded.contains(needle))
    {
        return CommandClass::Privileged;
    }
    let destructive = [
        " rm -rf",
        " rm -fr",
        " rm -r",
        " git reset --hard",
        " git clean -fd",
        " git clean -df",
        " mkfs",
        " dd if=",
        " truncate ",
        " shutdown ",
        " reboot ",
        " chmod -r 777",
        " chown -r",
    ];
    if destructive.iter().any(|needle| padded.contains(needle)) || lower.contains(":(){") {
        return CommandClass::Destructive;
    }
    let network = [
        "curl ",
        "wget ",
        "ssh ",
        "scp ",
        " gh ",
        "git clone",
        "git fetch",
        "git pull",
        "git push",
        "npm install",
        "npm ci",
        "pnpm add",
        "pnpm install",
        "yarn add",
        "cargo install",
        "pip install",
        "uv pip install",
    ];
    if network.iter().any(|needle| padded.contains(needle)) {
        return CommandClass::Network;
    }
    if padded.contains(" > ") || padded.contains(" >> ") || lower.starts_with("tee ") {
        return CommandClass::WriteWorkspace;
    }
    let first = lower
        .split_whitespace()
        .next()
        .unwrap_or_default()
        .rsplit('/')
        .next()
        .unwrap_or_default();
    let read_only = matches!(
        first,
        "ls" | "cat"
            | "head"
            | "tail"
            | "wc"
            | "grep"
            | "rg"
            | "find"
            | "fd"
            | "pwd"
            | "which"
            | "stat"
            | "file"
            | "tree"
            | "jq"
            | "echo"
            | "true"
            | "false"
            | "date"
            | "whoami"
            | "uname"
    ) || (first == "git"
        && [
            "status",
            "diff",
            "log",
            "show",
            "rev-parse",
            "branch",
            "ls-files",
        ]
        .iter()
        .any(|subcommand| lower.contains(&format!("git {subcommand}"))))
        || (first == "cargo"
            && ["metadata", "tree", "locate-project"]
                .iter()
                .any(|subcommand| lower.contains(&format!("cargo {subcommand}"))));
    if read_only {
        CommandClass::ReadOnly
    } else {
        CommandClass::WriteWorkspace
    }
}

fn optional_bool(arguments: &Value, name: &str) -> Option<bool> {
    arguments.get(name).and_then(Value::as_bool)
}

fn head_tail(content: &str, max_bytes: usize) -> String {
    if content.len() <= max_bytes {
        return content.to_owned();
    }
    let mut head_end = max_bytes / 2;
    while !content.is_char_boundary(head_end) {
        head_end -= 1;
    }
    let mut tail_start = content.len() - (max_bytes / 2);
    while !content.is_char_boundary(tail_start) {
        tail_start += 1;
    }
    format!(
        "{}\n… [{} bytes omitted] …\n{}",
        &content[..head_end],
        tail_start - head_end,
        &content[tail_start..]
    )
}

fn truncate(content: &str, max_bytes: usize) -> String {
    if content.len() <= max_bytes {
        return content.to_owned();
    }
    let mut end = max_bytes.min(content.len());
    while !content.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n[truncated at {max_bytes} bytes]", &content[..end])
}

fn string_array_arg(arguments: &Value, name: &str, max: usize) -> Result<Vec<String>, ToolError> {
    let values = arguments
        .get(name)
        .and_then(Value::as_array)
        .ok_or_else(|| ToolError::InvalidArgument(format!("missing array argument: {name}")))?;
    if values.is_empty() {
        return Err(ToolError::InvalidArgument(format!(
            "{name} must contain at least one path"
        )));
    }
    if values.len() > max {
        return Err(ToolError::InvalidArgument(format!(
            "{name} accepts at most {max} paths"
        )));
    }
    values
        .iter()
        .map(|value| {
            value.as_str().map(str::to_owned).ok_or_else(|| {
                ToolError::InvalidArgument(format!("{name} entries must be strings"))
            })
        })
        .collect()
}

fn string_array_arg_or_empty(arguments: &Value, name: &str) -> Result<Vec<String>, ToolError> {
    let Some(values) = arguments.get(name).and_then(Value::as_array) else {
        return Ok(Vec::new());
    };
    values
        .iter()
        .map(|value| {
            value.as_str().map(str::to_owned).ok_or_else(|| {
                ToolError::InvalidArgument(format!("{name} entries must be strings"))
            })
        })
        .collect()
}

fn integer_arg(arguments: &Value, name: &str) -> Result<i64, ToolError> {
    arguments
        .get(name)
        .and_then(Value::as_i64)
        .ok_or_else(|| ToolError::InvalidArgument(format!("missing integer argument: {name}")))
}

fn modified_ms(metadata: &std::fs::Metadata) -> u64 {
    metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |duration| duration.as_millis() as u64)
}

fn file_metadata(path: &Path) -> Result<Value, ToolError> {
    let metadata = std::fs::metadata(path)?;
    let bytes = std::fs::read(path)?;
    let lines = bytes.iter().filter(|byte| **byte == b'\n').count()
        + usize::from(!bytes.is_empty() && !bytes.ends_with(b"\n"));
    Ok(json!({
        "path": path,
        "bytes": metadata.len(),
        "lines": lines,
        "modified_ms": modified_ms(&metadata),
        "hash": blake3::hash(&bytes).to_hex().to_string(),
        "is_file": metadata.is_file()
    }))
}

fn repo_root(
    context: &ToolContext,
    reference: &str,
) -> Result<(ProjectRepoRef, PathBuf), ToolError> {
    let project = context
        .project
        .as_ref()
        .ok_or_else(|| ToolError::Failed("project repositories are not available".to_owned()))?;
    let repos = project.list_repos().map_err(ToolError::Failed)?;
    let repo = repos
        .into_iter()
        .find(|repo| repo.id == reference || repo.name == reference)
        .ok_or_else(|| ToolError::InvalidArgument(format!("unknown repository: {reference}")))?;
    let root = std::fs::canonicalize(&repo.path)?;
    Ok((repo, root))
}

fn resolve_repo_path(root: &Path, relative: &str) -> Result<PathBuf, ToolError> {
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|error| ToolError::InvalidArgument(format!("repository path: {error}")))?;
    if !path.starts_with(root) {
        return Err(ToolError::OutsideRoot(path.display().to_string()));
    }
    Ok(path)
}

fn trash_directory(context: &ToolContext) -> Result<PathBuf, ToolError> {
    Ok(context.workspace.root().join(".openbad/trash"))
}

fn trash_name(context: &ToolContext, path: &Path) -> String {
    let root = context
        .workspace
        .root()
        .canonicalize()
        .unwrap_or_else(|_| context.workspace.root().to_path_buf());
    let relative = path
        .strip_prefix(&root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace(['/', '\\'], "__");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis());
    format!("{now}-{relative}")
}

fn outline_entries(path: &Path, content: &str, max_entries: usize) -> Vec<String> {
    let extension = path
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let mut patterns = Vec::new();
    match extension.as_str() {
        "md" | "markdown" | "mdx" => {
            patterns.push(r"^(#{1,6})\s+(.+)$".to_owned());
        }
        "adoc" | "asciidoc" | "asciidoctor" => {
            patterns.push(r"^(=+)\s+(.+)$".to_owned());
        }
        "yaml" | "yml" => {
            patterns.push(r"^([A-Za-z0-9_.-]+):\s*(.*)$".to_owned());
            patterns.push(r"^\s+-\s+(_id|id|name|type|title|key):\s*(.*)$".to_owned());
        }
        _ => {
            patterns.push(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:fn|struct|enum|trait|impl|class|def|interface|type|const|static|mod|package)\s+([A-Za-z0-9_<>,]+)".to_owned(),
            );
        }
    }
    let patterns = patterns
        .iter()
        .filter_map(|pattern| regex::Regex::new(pattern).ok())
        .collect::<Vec<_>>();
    let mut entries = Vec::new();
    for (index, line) in content.lines().enumerate() {
        if entries.len() >= max_entries {
            break;
        }
        let trimmed = line.trim_end();
        if trimmed.trim().is_empty() {
            continue;
        }
        if patterns.iter().any(|pattern| pattern.is_match(trimmed)) {
            entries.push(format!("{}: {}", index + 1, trimmed.trim()));
        }
    }
    entries
}

fn context_policy(context: &ToolContext) -> Result<openbad_workspace::ContextPolicy, ToolError> {
    openbad_workspace::load_context_policy(context.workspace.root())
        .map_err(|error| ToolError::Failed(error.to_string()))
}

fn ensure_not_excluded(context: &ToolContext, path: &Path) -> Result<(), ToolError> {
    let relative = relative_to_root(context, path);
    let policy = context_policy(context)?;
    if policy.is_excluded(&relative) {
        return Err(ToolError::Failed(format!(
            "{relative} is excluded by .openbad/context.yaml; do not load it"
        )));
    }
    Ok(())
}

fn path_is_excluded(context: &ToolContext, path: &Path) -> bool {
    let relative = relative_to_root(context, path);
    context_policy(context)
        .map(|policy| policy.is_excluded(&relative))
        .unwrap_or(false)
}

fn update_context_rule(
    arguments: &Value,
    context: &ToolContext,
    excluded: bool,
) -> Result<ToolResult, ToolError> {
    if !context.allow_writes {
        return Err(ToolError::ApprovalRequired(if excluded {
            "exclude_context".to_owned()
        } else {
            "pin_context".to_owned()
        }));
    }
    let path = string_arg(arguments, "path")?;
    if path.trim().is_empty() {
        return Err(ToolError::InvalidArgument(
            "path must not be empty".to_owned(),
        ));
    }
    let note = optional_string(arguments, "note");
    let mut policy = context_policy(context)?;
    if optional_bool(arguments, "remove").unwrap_or(false) {
        if !policy.remove(&path) {
            return Err(ToolError::InvalidArgument(format!(
                "no context rule matches {path}"
            )));
        }
    } else if excluded {
        policy.add_excluded(&path, note.as_deref());
    } else {
        policy.add_pinned(&path, note.as_deref());
    }
    openbad_workspace::save_context_policy(context.workspace.root(), &policy)
        .map_err(|error| ToolError::Failed(error.to_string()))?;
    Ok(ToolResult::ok(format!(
        "{} context rule for {path}",
        if optional_bool(arguments, "remove").unwrap_or(false) {
            "removed"
        } else if excluded {
            "excluded"
        } else {
            "pinned"
        }
    ))
    .with_metadata(json!({
        "path": path,
        "pinned": policy.pinned.len(),
        "excluded": policy.excluded.len()
    })))
}

fn parse_diagnostics(kind: &str, output: &str) -> Vec<Value> {
    let mut diagnostics = Vec::new();
    let mut pending: Option<(String, String)> = None;
    let rust = regex::Regex::new(r"^(?P<sev>error|warning)(?:\[[^\]]+\])?: (?P<msg>.*)$").ok();
    let location = regex::Regex::new(r"^\s*-->\s+(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+)").ok();
    let generic = regex::Regex::new(
        r"^(?P<path>[^:\n]+):(?P<line>\d+):(?P<col>\d+)\s*[-:]?\s*(?P<sev>error|warning|note)\s*[:\-]?\s*(?P<msg>.*)$",
    )
    .ok();
    let pytest =
        regex::Regex::new(r"^FAILED (?P<path>[^:]+)::(?P<test>[^ ]+)(?: - (?P<msg>.*))?$").ok();
    for line in output.lines() {
        if matches!(kind, "rust" | "auto") {
            if let (Some(rust), Some(location)) = (&rust, &location) {
                if let Some(captures) = rust.captures(line) {
                    pending = Some((captures["sev"].to_owned(), captures["msg"].to_owned()));
                    continue;
                }
                if let Some(captures) = location.captures(line) {
                    if let Some((severity, message)) = pending.take() {
                        diagnostics.push(json!({
                            "path": captures["path"].trim(),
                            "line": captures["line"].parse::<u64>().unwrap_or(0),
                            "column": captures["col"].parse::<u64>().unwrap_or(0),
                            "severity": severity,
                            "message": message
                        }));
                        continue;
                    }
                }
            }
        }
        if matches!(kind, "pytest" | "auto") {
            if let Some(captures) = pytest.as_ref().and_then(|pytest| pytest.captures(line)) {
                diagnostics.push(json!({
                    "path": captures["path"].trim(),
                    "line": 0,
                    "column": 0,
                    "severity": "error",
                    "message": format!(
                        "{} {}",
                        captures.name("test").map(|test| test.as_str()).unwrap_or_default(),
                        captures.name("msg").map(|msg| msg.as_str()).unwrap_or_default()
                    ).trim()
                }));
                continue;
            }
        }
        if matches!(kind, "eslint" | "typescript" | "auto") {
            if let Some(captures) = generic.as_ref().and_then(|generic| generic.captures(line)) {
                let severity = captures["sev"].to_owned();
                if severity != "note" {
                    diagnostics.push(json!({
                        "path": captures["path"].trim(),
                        "line": captures["line"].parse::<u64>().unwrap_or(0),
                        "column": captures["col"].parse::<u64>().unwrap_or(0),
                        "severity": severity,
                        "message": captures["msg"].trim()
                    }));
                }
            }
        }
    }
    diagnostics
}

static SECRET_PATTERNS: LazyLock<Vec<(regex::Regex, &'static str)>> = LazyLock::new(|| {
    [
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
        (r"sk-[A-Za-z0-9]{20,}", "API key"),
        (r"ghp_[A-Za-z0-9]{20,}", "GitHub token"),
        (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub token"),
        (r"AKIA[0-9A-Z]{16}", "AWS access key"),
        (r"xox[baprs]-[A-Za-z0-9-]{20,}", "Slack token"),
        (r"AIza[0-9A-Za-z\-_]{35}", "Google API key"),
    ]
    .into_iter()
    .filter_map(|(pattern, label)| regex::Regex::new(pattern).ok().map(|regex| (regex, label)))
    .collect()
});

fn detect_secret(content: &str) -> Option<&'static str> {
    SECRET_PATTERNS
        .iter()
        .find(|(regex, _)| regex.is_match(content))
        .map(|(_, label)| *label)
}

fn now_millis() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as i64)
}

fn memory_line(entry: &MemoryEntry, tags: &str) -> String {
    let origin = entry
        .origin_task
        .as_deref()
        .map(|task| format!(" via {task}"))
        .or_else(|| entry.origin_turn.map(|turn| format!(" turn {turn}")))
        .unwrap_or_default();
    let id_line = format!("{}: {}", entry.id, entry.content);
    format!(
        "{}{}[{}]{}{}: {}",
        if entry.pinned { "* " } else { "- " },
        entry.scope,
        entry.author,
        if tags.is_empty() {
            String::new()
        } else {
            format!(" {tags}")
        },
        origin,
        id_line
    )
}

fn likely_memory_conflict(left: &str, right: &str) -> bool {
    let left_tokens = memory_tokens(left);
    let right_tokens = memory_tokens(right);
    if left_tokens.is_empty() || right_tokens.is_empty() {
        return false;
    }
    let intersection = left_tokens.intersection(&right_tokens).count();
    let union = left_tokens.union(&right_tokens).count();
    let similarity = intersection as f64 / union as f64;
    if similarity < 0.55 {
        return false;
    }
    let negation = [
        "never", "not", "avoid", "don't", "do not", "without", "instead",
    ];
    let left_negated = negation
        .iter()
        .any(|needle| left.to_ascii_lowercase().contains(needle));
    let right_negated = negation
        .iter()
        .any(|needle| right.to_ascii_lowercase().contains(needle));
    left_negated != right_negated
}

fn memory_tokens(content: &str) -> HashSet<String> {
    content
        .split(|character: char| !character.is_alphanumeric())
        .map(str::to_ascii_lowercase)
        .filter(|token| token.len() > 3)
        .filter(|token| {
            !matches!(
                token.as_str(),
                "with" | "that" | "this" | "from" | "have" | "should" | "must" | "always"
            )
        })
        .collect()
}

fn relative_to_root(context: &ToolContext, path: &Path) -> String {
    let root = context
        .workspace
        .root()
        .canonicalize()
        .unwrap_or_else(|_| context.workspace.root().to_path_buf());
    path.strip_prefix(&root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn load_policy(
    context: &ToolContext,
) -> Result<Option<openbad_workspace::ProjectPolicy>, ToolError> {
    openbad_workspace::load_project_policy(context.workspace.root())
        .map_err(|error| ToolError::Failed(error.to_string()))
}

fn check_write_policy(context: &ToolContext, path: &Path) -> Result<(), ToolError> {
    let Some(policy) = load_policy(context)? else {
        return Ok(());
    };
    if let Some(reason) = policy.write_denied(&relative_to_root(context, path)) {
        return Err(ToolError::ApprovalRequired(reason));
    }
    Ok(())
}

fn normalize_lexical(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    normalized
}

fn check_command_policy(
    context: &ToolContext,
    class: CommandClass,
    command: &str,
) -> Result<(), ToolError> {
    let Some(policy) = load_policy(context)? else {
        return Ok(());
    };
    if let Some(reason) = policy.command_denied(class.label(), command) {
        return Err(ToolError::ApprovalRequired(reason));
    }
    Ok(())
}

async fn run_validators(context: &ToolContext, path: &Path) -> Result<Option<String>, ToolError> {
    let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
        .map_err(|error| ToolError::Failed(error.to_string()))?;
    let Some(manifest) = manifest else {
        return Ok(None);
    };
    let root = context
        .workspace
        .root()
        .canonicalize()
        .unwrap_or_else(|_| context.workspace.root().to_path_buf());
    let relative = path
        .strip_prefix(&root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    let validators = manifest.validators_for(&relative);
    if validators.is_empty() {
        return Ok(None);
    }
    if !context.allow_commands {
        return Ok(Some(format!(
            "validation skipped: {} validator(s) match {relative} but commands require Full access",
            validators.len()
        )));
    }
    for validator in validators {
        let result = RunCommandTool
            .execute(
                json!({
                    "command": validator.command,
                    "timeout_seconds": 120
                }),
                context,
            )
            .await?;
        if !result.metadata["success"].as_bool().unwrap_or(false) {
            return Err(ToolError::Failed(format!(
                "validation failed for {relative} with `{}`:\n{}",
                validator.command, result.content
            )));
        }
    }
    Ok(Some(format!("validation passed for {relative}")))
}

fn restore_previous(path: &Path, previous: Option<&str>) -> Result<(), ToolError> {
    match previous {
        Some(content) => atomic_write(path, content),
        None => {
            if path.exists() {
                std::fs::remove_file(path)?;
            }
            Ok(())
        }
    }
}

fn scope_warning(context: &ToolContext, path: &Path) -> Option<String> {
    let manifest = openbad_workspace::load_project_manifest(context.workspace.root()).ok()??;
    let root = context
        .workspace
        .root()
        .canonicalize()
        .unwrap_or_else(|_| context.workspace.root().to_path_buf());
    let relative = path
        .strip_prefix(&root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    let class = manifest.classify(&relative);
    class.needs_care().then(|| {
        format!(
            "warning: {relative} is classified as {} in .openbad/project.yaml; confirm it is the intended source of truth",
            class.label()
        )
    })
}

fn checkpoint_before_write(
    context: &ToolContext,
    path: &Path,
) -> Result<Option<String>, ToolError> {
    let Some(checkpoints) = &context.checkpoints else {
        return Ok(None);
    };
    let existed = path.exists();
    let content = if existed {
        Some(std::fs::read(path)?)
    } else {
        None
    };
    let checkpoint = checkpoints
        .save(&path.to_string_lossy(), existed, content.as_deref())
        .map_err(ToolError::Failed)?;
    Ok(Some(checkpoint))
}

pub fn atomic_write(path: &Path, content: &str) -> Result<(), ToolError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let directory = path
        .parent()
        .ok_or_else(|| ToolError::InvalidArgument("path has no parent".to_owned()))?;
    let existing_permissions = std::fs::metadata(path)
        .ok()
        .map(|metadata| metadata.permissions());
    let mut temp = tempfile::NamedTempFile::new_in(directory)?;
    std::io::Write::write_all(&mut temp, content.as_bytes())?;
    if let Some(permissions) = existing_permissions {
        temp.as_file().set_permissions(permissions)?;
    } else {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            temp.as_file()
                .set_permissions(std::fs::Permissions::from_mode(0o644))?;
        }
    }
    #[cfg(windows)]
    {
        let _ = std::fs::remove_file(path);
    }
    temp.persist(path)
        .map_err(|error| ToolError::Io(error.error))?;
    Ok(())
}

struct ReadFileTool;

#[async_trait]
impl Tool for ReadFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_file",
            "Read a UTF-8 text file from the workspace or an approved external path. Returns the requested line range.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path, or an absolute path approved via request_fs_access"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "max_bytes": {"type": "integer", "minimum": 256},
                "budget_tokens": {"type": "integer", "minimum": 64, "description": "Approximate token budget (4 bytes per token)"}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        ensure_not_excluded(context, &path)?;
        let content = std::fs::read_to_string(&path)?;
        context.mark_read(&path);

        let start = optional_usize(&arguments, "start_line").unwrap_or(1).max(1);
        let end = optional_usize(&arguments, "end_line").unwrap_or(usize::MAX);
        if end < start {
            return Err(ToolError::InvalidArgument(
                "end_line must be greater than or equal to start_line".to_owned(),
            ));
        }

        let lines: Vec<&str> = content.lines().collect();
        let total_lines = lines.len();
        let end = end.min(total_lines);
        let selected = lines
            .into_iter()
            .enumerate()
            .filter(|(index, _)| {
                let line = index + 1;
                line >= start && line <= end
            })
            .map(|(_, line)| line)
            .collect::<Vec<_>>()
            .join("\n");
        let shown = selected.lines().count();
        let max_bytes = optional_usize(&arguments, "max_bytes")
            .or_else(|| optional_usize(&arguments, "budget_tokens").map(|tokens| tokens * 4))
            .unwrap_or(context.max_output_bytes)
            .clamp(256, context.max_output_bytes);
        let truncated = selected.len() > max_bytes;

        Ok(
            ToolResult::ok(truncate(&selected, max_bytes)).with_metadata(json!({
                "path": path,
                "lines": shown,
                "total_lines": total_lines,
                "start_line": start.min(total_lines.saturating_add(1)),
                "end_line": end,
                "truncated": truncated,
                "max_bytes": max_bytes,
                "has_more": end < total_lines,
                "next_start_line": if end < total_lines { Some(end + 1) } else { None },
            })),
        )
    }
}

struct StatFileTool;

#[async_trait]
impl Tool for StatFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "stat_file",
            "Report a workspace or approved external file's size, line count, modification time, and BLAKE3 hash without reading its contents into context.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative path, or an absolute path approved via request_fs_access"}},
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        let metadata = file_metadata(&path)?;
        Ok(ToolResult::ok(format!(
            "{}: {} bytes, {} lines, modified {}, blake3 {}",
            path.display(),
            metadata["bytes"],
            metadata["lines"],
            metadata["modified_ms"],
            metadata["hash"].as_str().unwrap_or_default()
        ))
        .with_metadata(metadata))
    }
}

struct StatRepoFileTool;

#[async_trait]
impl Tool for StatRepoFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "stat_repo_file",
            "Report a reference-folder file's size, line count, modification time, and BLAKE3 hash.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Linked repository id or name"},
                "path": {"type": "string", "description": "Repository-relative path"}
            },
            "required": ["repo", "path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let root = repo_root(context, &string_arg(&arguments, "repo")?)?.1;
        let path = resolve_repo_path(&root, &string_arg(&arguments, "path")?)?;
        let metadata = file_metadata(&path)?;
        Ok(ToolResult::ok(format!(
            "{}: {} bytes, {} lines, modified {}, blake3 {}",
            path.display(),
            metadata["bytes"],
            metadata["lines"],
            metadata["modified_ms"],
            metadata["hash"].as_str().unwrap_or_default()
        ))
        .with_metadata(metadata))
    }
}

struct RequestFilesystemAccessTool;

#[async_trait]
impl Tool for RequestFilesystemAccessTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "request_fs_access",
            "Ask the human for access to a file or directory outside the workspace. This records a request; it does not grant access. Stop after requesting and wait for approval in OpenBaD, then retry the operation.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to a file or directory outside the workspace"},
                "write": {"type": "boolean", "description": "Request write access as well as read access"},
                "reason": {"type": "string", "description": "Why this path is needed"}
            },
            "required": ["path", "reason"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let raw = string_arg(&arguments, "path")?;
        let requested = Path::new(&raw);
        if !requested.is_absolute() {
            return Err(ToolError::InvalidArgument(
                "request_fs_access requires an absolute path".to_owned(),
            ));
        }
        let normalized = normalize_lexical(requested);
        let resolved = if normalized.exists() {
            normalized.canonicalize()?
        } else {
            let parent = normalized
                .parent()
                .ok_or_else(|| ToolError::InvalidArgument("path has no parent".to_owned()))?;
            parent.canonicalize()?.join(
                normalized
                    .file_name()
                    .ok_or_else(|| ToolError::InvalidArgument("path has no name".to_owned()))?,
            )
        };
        if resolved.parent().is_none() {
            return Err(ToolError::InvalidArgument(
                "refusing to request the filesystem root".to_owned(),
            ));
        }
        if context.workspace.resolve(&resolved).is_ok() {
            return Ok(ToolResult::ok(format!(
                "{0} is already inside the workspace",
                resolved.display()
            )));
        }
        let write = optional_bool(&arguments, "write").unwrap_or(false);
        let reason = string_arg(&arguments, "reason")?;
        let already_granted = context
            .outside_grants
            .iter()
            .any(|grant| resolved.starts_with(&grant.path) && (!write || grant.write_access));
        if already_granted {
            let access_note = if write && !context.allow_writes {
                " The grant includes write access, but this session is read-only; switch the session access mode to Can edit or Full access before writing."
            } else {
                ""
            };
            return Ok(ToolResult::ok(format!(
                "access already granted for {}{access_note}",
                resolved.display()
            ))
            .with_metadata(json!({
                "outside_access": {
                    "granted": true,
                    "path": resolved,
                    "write": write
                }
            })));
        }
        let mode_note = if write && !context.allow_writes {
            " The human must also switch the session access mode to Can edit or Full access for writes."
        } else {
            ""
        };
        Ok(ToolResult::ok(format!(
            "Filesystem access requested for {}{}. Stop this turn and ask the human to approve it in OpenBaD; retry only after approval.{mode_note}",
            resolved.display(),
            if write { " (read and write)" } else { " (read only)" }
        ))
        .with_metadata(json!({
            "openbad_access_request": {
                "path": resolved,
                "write": write,
                "reason": reason
            }
        })))
    }
}

struct ReadFilesTool;

#[async_trait]
impl Tool for ReadFilesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_files",
            "Read several workspace or approved external files in one call. Each file is capped independently; failures are reported per path.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20
                }
            },
            "required": ["paths"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let paths = string_array_arg(&arguments, "paths", 20)?;
        let cap = (context.max_output_bytes / paths.len()).max(1_024);
        let mut sections = Vec::new();
        let mut files = Vec::new();
        for raw in paths {
            match context.resolve_read(Path::new(&raw)) {
                Ok(path) => match ensure_not_excluded(context, &path)
                    .and_then(|()| std::fs::read_to_string(&path).map_err(ToolError::from))
                {
                    Ok(content) => {
                        context.mark_read(&path);
                        files.push(json!({
                            "path": raw,
                            "bytes": content.len(),
                            "lines": content.lines().count()
                        }));
                        sections.push(format!("--- {raw} ---\n{}", truncate(&content, cap)));
                    }
                    Err(error) => {
                        files.push(json!({"path": raw, "error": error.to_string()}));
                        sections.push(format!("--- {raw} ---\nERROR: {error}"));
                    }
                },
                Err(error) => {
                    files.push(json!({"path": raw, "error": error.to_string()}));
                    sections.push(format!("--- {raw} ---\nERROR: {error}"));
                }
            }
        }
        Ok(
            ToolResult::ok(truncate(&sections.join("\n\n"), context.max_output_bytes))
                .with_metadata(json!({"files": files})),
        )
    }
}

struct ReadRepoFilesTool;

#[async_trait]
impl Tool for ReadRepoFilesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_repo_files",
            "Read several files from one reference folder in a single call, with per-file error isolation.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Linked repository id or name"},
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20
                }
            },
            "required": ["repo", "paths"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let (repo, root) = repo_root(context, &string_arg(&arguments, "repo")?)?;
        let paths = string_array_arg(&arguments, "paths", 20)?;
        let cap = (context.max_output_bytes / paths.len()).max(1_024);
        let mut sections = Vec::new();
        let mut files = Vec::new();
        for raw in paths {
            match resolve_repo_path(&root, &raw).and_then(|path| {
                std::fs::read_to_string(&path)
                    .map(|content| (path, content))
                    .map_err(ToolError::from)
            }) {
                Ok((path, content)) => {
                    files.push(json!({
                        "path": path,
                        "bytes": content.len(),
                        "lines": content.lines().count()
                    }));
                    sections.push(format!("--- {raw} ---\n{}", truncate(&content, cap)));
                }
                Err(error) => {
                    files.push(json!({"path": raw, "error": error.to_string()}));
                    sections.push(format!("--- {raw} ---\nERROR: {error}"));
                }
            }
        }
        Ok(
            ToolResult::ok(truncate(&sections.join("\n\n"), context.max_output_bytes))
                .with_metadata(json!({"repo": repo.name, "files": files})),
        )
    }
}

struct OutlineTool;

#[async_trait]
impl Tool for OutlineTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "outline",
            "Extract headings, YAML identifiers, or code declarations from a workspace or approved external file. Use it to navigate long files before reading.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "repo": {"type": "string", "description": "Optional reference folder id or name"},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 500}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let raw = string_arg(&arguments, "path")?;
        let path = match optional_string(&arguments, "repo") {
            Some(reference) => {
                let root = repo_root(context, &reference)?.1;
                resolve_repo_path(&root, &raw)?
            }
            None => context.resolve_read(Path::new(&raw))?,
        };
        ensure_not_excluded(context, &path)?;
        let content = std::fs::read_to_string(&path)?;
        context.mark_read(&path);
        let cap = optional_usize(&arguments, "max_entries")
            .unwrap_or(200)
            .clamp(1, 500);
        let entries = outline_entries(&path, &content, cap);
        if entries.is_empty() {
            return Ok(
                ToolResult::ok("no outline entries found").with_metadata(json!({
                    "path": path,
                    "entries": 0
                })),
            );
        }
        Ok(
            ToolResult::ok(truncate(&entries.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"path": path, "entries": entries.len()})),
        )
    }
}

struct DeleteFileTool;

#[async_trait]
impl Tool for DeleteFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "delete_file",
            "Delete a workspace file. By default it moves to .openbad/trash so it can be restored; hard deletes require hard=true.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "hard": {"type": "boolean", "description": "Permanently remove instead of using the trash"}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("delete_file".to_owned()));
        }
        let path = context.resolve_write(Path::new(&string_arg(&arguments, "path")?))?;
        check_write_policy(context, &path)?;
        if !path.is_file() {
            return Err(ToolError::InvalidArgument(format!(
                "not a file: {}",
                path.display()
            )));
        }
        let checkpoint = checkpoint_before_write(context, &path)?;
        if optional_bool(&arguments, "hard").unwrap_or(false) {
            std::fs::remove_file(&path)?;
            return Ok(
                ToolResult::ok(format!("permanently deleted {}", path.display()))
                    .with_metadata(json!({"path": path, "hard": true, "checkpoint": checkpoint})),
            );
        }
        let trash = trash_directory(context)?;
        std::fs::create_dir_all(&trash)?;
        let name = trash_name(context, &path);
        std::fs::rename(&path, trash.join(&name))?;
        Ok(
            ToolResult::ok(format!("moved {} to trash as {name}", path.display())).with_metadata(
                json!({
                    "path": path,
                    "hard": false,
                    "trash_name": name,
                    "checkpoint": checkpoint
                }),
            ),
        )
    }
}

struct MoveFileTool;

#[async_trait]
impl Tool for MoveFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "move_file",
            "Move or rename a workspace file. The destination must stay inside the workspace.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"}
            },
            "required": ["from", "to"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("move_file".to_owned()));
        }
        let from = context.resolve_write(Path::new(&string_arg(&arguments, "from")?))?;
        let to = context.resolve_write(Path::new(&string_arg(&arguments, "to")?))?;
        check_write_policy(context, &from)?;
        check_write_policy(context, &to)?;
        if !from.is_file() {
            return Err(ToolError::InvalidArgument(format!(
                "not a file: {}",
                from.display()
            )));
        }
        if to.exists() && !context.was_read(&to) {
            return Err(ToolError::Failed(format!(
                "read the destination before overwriting it: {}",
                to.display()
            )));
        }
        if let Some(parent) = to.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let checkpoint = checkpoint_before_write(context, &from)?;
        if std::fs::rename(&from, &to).is_err() {
            std::fs::copy(&from, &to)?;
            std::fs::remove_file(&from)?;
        }
        Ok(
            ToolResult::ok(format!("moved {} to {}", from.display(), to.display()))
                .with_metadata(json!({"from": from, "to": to, "checkpoint": checkpoint})),
        )
    }
}

struct ListTrashTool;

#[async_trait]
impl Tool for ListTrashTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_trash",
            "List soft-deleted workspace files available for restore.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let trash = trash_directory(context)?;
        if !trash.is_dir() {
            return Ok(ToolResult::ok("trash is empty").with_metadata(json!({"entries": 0})));
        }
        let mut entries = Vec::new();
        for entry in std::fs::read_dir(&trash)? {
            let entry = entry?;
            if !entry.file_type()?.is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let metadata = entry.metadata()?;
            entries.push(json!({
                "name": name,
                "bytes": metadata.len(),
                "modified_ms": modified_ms(&metadata)
            }));
        }
        entries.sort_by(|left, right| {
            left["name"]
                .as_str()
                .unwrap_or_default()
                .cmp(right["name"].as_str().unwrap_or_default())
        });
        if entries.is_empty() {
            return Ok(ToolResult::ok("trash is empty").with_metadata(json!({"entries": 0})));
        }
        let content = entries
            .iter()
            .map(|entry| {
                format!(
                    "- {} ({} bytes)",
                    entry["name"].as_str().unwrap_or_default(),
                    entry["bytes"]
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({"entries": entries})))
    }
}

struct RestoreTrashTool;

#[async_trait]
impl Tool for RestoreTrashTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "restore_trash",
            "Restore a soft-deleted file from .openbad/trash to its original path or an explicit destination.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Trash entry name from list_trash"},
                "to": {"type": "string", "description": "Optional workspace-relative destination"}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("restore_trash".to_owned()));
        }
        let name = string_arg(&arguments, "name")?;
        if name.contains('/') || name.contains('\\') {
            return Err(ToolError::InvalidArgument(
                "trash name must not contain path separators".to_owned(),
            ));
        }
        let source = trash_directory(context)?.join(&name);
        if !source.is_file() {
            return Err(ToolError::InvalidArgument(format!(
                "unknown trash entry: {name}"
            )));
        }
        let relative = match optional_string(&arguments, "to") {
            Some(to) => to,
            None => name
                .split_once('-')
                .map(|(_, path)| path.replace("__", "/"))
                .unwrap_or_else(|| name.clone()),
        };
        let destination = context.resolve_write(Path::new(&relative))?;
        check_write_policy(context, &destination)?;
        if destination.exists() {
            return Err(ToolError::Failed(format!(
                "destination already exists: {}",
                destination.display()
            )));
        }
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::rename(&source, &destination)?;
        Ok(
            ToolResult::ok(format!("restored {name} to {}", destination.display()))
                .with_metadata(json!({"name": name, "path": destination})),
        )
    }
}

struct ListDirTool;

#[async_trait]
impl Tool for ListDirTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_dir",
            "List files and directories in the workspace or an approved external directory, respecting .gitignore.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative directory, or an absolute directory approved via request_fs_access"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let root = context.resolve_read(Path::new(&relative))?;
        if !root.is_dir() {
            return Err(ToolError::InvalidArgument(format!(
                "not a directory: {relative}"
            )));
        }
        let depth = optional_usize(&arguments, "depth")
            .unwrap_or(3)
            .clamp(1, 10);
        let max_entries = optional_usize(&arguments, "max_entries")
            .unwrap_or(500)
            .clamp(1, 2_000);

        let mut entries = Vec::new();
        for entry in WalkBuilder::new(&root)
            .max_depth(Some(depth))
            .build()
            .filter_map(Result::ok)
        {
            if entries.len() >= max_entries {
                break;
            }
            if entry.path() == root {
                continue;
            }
            if path_is_excluded(context, entry.path()) {
                continue;
            }
            let relative = entry
                .path()
                .strip_prefix(context.workspace.root())
                .unwrap_or(entry.path());
            let suffix = if entry.file_type().is_some_and(|kind| kind.is_dir()) {
                "/"
            } else {
                ""
            };
            entries.push(format!("{}{suffix}", relative.display()));
        }
        entries.sort();

        Ok(
            ToolResult::ok(truncate(&entries.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"entries": entries.len(), "root": root})),
        )
    }
}

struct GrepTool;

#[async_trait]
impl Tool for GrepTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "grep",
            "Search workspace or approved external text files for a substring. Respects .gitignore and returns path:line matches.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "description": "Workspace-relative directory, or an absolute directory approved via request_fs_access"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                "case_sensitive": {"type": "boolean"}
            },
            "required": ["query"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = string_arg(&arguments, "query")?;
        if query.is_empty() {
            return Err(ToolError::InvalidArgument(
                "query must not be empty".to_owned(),
            ));
        }
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let root = context.resolve_read(Path::new(&relative))?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(100)
            .clamp(1, 500);
        let case_sensitive = optional_bool(&arguments, "case_sensitive").unwrap_or(true);
        let needle = if case_sensitive {
            query.clone()
        } else {
            query.to_lowercase()
        };

        let mut matches = Vec::new();
        'files: for entry in WalkBuilder::new(&root).build().filter_map(Result::ok) {
            if matches.len() >= max_results {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file())
                || path_is_excluded(context, entry.path())
            {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(metadata) if metadata.len() <= 1024 * 1024 => metadata,
                _ => continue,
            };
            let _ = metadata;
            let content = match std::fs::read(entry.path()) {
                Ok(bytes) => match String::from_utf8(bytes) {
                    Ok(text) => text,
                    Err(_) => continue,
                },
                Err(_) => continue,
            };
            for (index, line) in content.lines().enumerate() {
                let haystack = if case_sensitive {
                    line.to_owned()
                } else {
                    line.to_lowercase()
                };
                if haystack.contains(&needle) {
                    let path = entry
                        .path()
                        .strip_prefix(context.workspace.root())
                        .unwrap_or(entry.path());
                    matches.push(format!("{}:{}: {}", path.display(), index + 1, line.trim()));
                    if matches.len() >= max_results {
                        break 'files;
                    }
                }
            }
        }

        Ok(
            ToolResult::ok(truncate(&matches.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"matches": matches.len()})),
        )
    }
}

struct SearchCodeTool;

#[async_trait]
impl Tool for SearchCodeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "search_code",
            "Search indexed workspace code and text. Faster than grep on large repositories; falls back to a live scan when no index exists.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200}
            },
            "required": ["query"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = string_arg(&arguments, "query")?;
        if query.is_empty() {
            return Err(ToolError::InvalidArgument(
                "query must not be empty".to_owned(),
            ));
        }
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(50)
            .clamp(1, 200);
        if let Some(search) = &context.search {
            let hits = search
                .search(&query, max_results)
                .map_err(ToolError::Failed)?
                .into_iter()
                .filter(|hit| {
                    !context_policy(context)
                        .map(|policy| policy.is_excluded(&hit.path))
                        .unwrap_or(false)
                })
                .collect::<Vec<_>>();
            let content = hits
                .iter()
                .map(|hit| format!("{}: {}", hit.path, hit.snippet.replace('\n', " ")))
                .collect::<Vec<_>>()
                .join("\n");
            return Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
                .with_metadata(json!({"matches": hits.len(), "indexed": true})));
        }
        GrepTool
            .execute(json!({"query": query, "max_results": max_results}), context)
            .await
    }
}

struct GlobTool;

#[async_trait]
impl Tool for GlobTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "glob",
            "Find files in the workspace or an approved external directory whose path matches a glob such as **/*.rs or src/**/*.ts. Respects .gitignore.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Workspace-relative directory, or an absolute directory approved via request_fs_access"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 5000}
            },
            "required": ["pattern"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let pattern = string_arg(&arguments, "pattern")?;
        if pattern.is_empty() {
            return Err(ToolError::InvalidArgument(
                "pattern must not be empty".to_owned(),
            ));
        }
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let root = context.resolve_read(Path::new(&relative))?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(500)
            .clamp(1, 5_000);
        let matcher = Glob::new(&pattern)
            .map_err(|error| ToolError::InvalidArgument(format!("invalid glob: {error}")))?
            .compile_matcher();
        let match_basename = !pattern.contains('/');

        let mut matches = Vec::new();
        for entry in WalkBuilder::new(&root).build().filter_map(Result::ok) {
            if matches.len() >= max_results {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file())
                || path_is_excluded(context, entry.path())
            {
                continue;
            }
            let path = entry
                .path()
                .strip_prefix(context.workspace.root())
                .unwrap_or(entry.path());
            let candidate = if match_basename {
                path.file_name().map(Path::new).unwrap_or(path)
            } else {
                path
            };
            if matcher.is_match(candidate) {
                matches.push(path.display().to_string());
            }
        }
        matches.sort();
        Ok(
            ToolResult::ok(truncate(&matches.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"matches": matches.len()})),
        )
    }
}

struct FindSymbolsTool;

#[async_trait]
impl Tool for FindSymbolsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "find_symbols",
            "Find function, type, class, trait, and module definitions by name across code files.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive symbol name or fragment"},
                "path": {"type": "string", "description": "Workspace-relative directory, or an absolute directory approved via request_fs_access"},
                "kind": {"type": "string", "description": "Optional definition kind such as fn, struct, class, or def"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500}
            },
            "required": ["query"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = string_arg(&arguments, "query")?;
        if query.is_empty() {
            return Err(ToolError::InvalidArgument(
                "query must not be empty".to_owned(),
            ));
        }
        let query = query.to_lowercase();
        let kind_filter = optional_string(&arguments, "kind").map(|kind| kind.to_lowercase());
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let root = context.resolve_read(Path::new(&relative))?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(100)
            .clamp(1, 500);
        if let Some(search) = &context.search {
            let hits = search
                .find_symbols(&query, kind_filter.as_deref(), max_results)
                .map_err(ToolError::Failed)?
                .into_iter()
                .filter(|hit| {
                    !context_policy(context)
                        .map(|policy| policy.is_excluded(&hit.path))
                        .unwrap_or(false)
                })
                .collect::<Vec<_>>();
            let content = hits
                .iter()
                .map(|hit| format!("{}:{}: {}", hit.path, hit.line, hit.signature))
                .collect::<Vec<_>>()
                .join(
                    "
",
                );
            return Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
                .with_metadata(json!({"matches": hits.len(), "indexed": true})));
        }
        let definition = symbol_pattern();

        let mut matches = Vec::new();
        'files: for entry in WalkBuilder::new(&root).build().filter_map(Result::ok) {
            if matches.len() >= max_results {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                continue;
            }
            let path = entry.path();
            if !is_code_file(path) || path_is_excluded(context, path) {
                continue;
            }
            if entry.metadata().map(|m| m.len()).unwrap_or(u64::MAX) > 1024 * 1024 {
                continue;
            }
            let Ok(bytes) = std::fs::read(path) else {
                continue;
            };
            let Ok(content) = String::from_utf8(bytes) else {
                continue;
            };
            let display = path.strip_prefix(context.workspace.root()).unwrap_or(path);
            for (index, line) in content.lines().enumerate() {
                let Some(captures) = definition.captures(line) else {
                    continue;
                };
                let name = captures
                    .name("name")
                    .map(|m| m.as_str())
                    .unwrap_or_default();
                if !name.to_lowercase().contains(&query) {
                    continue;
                }
                let kind = captures
                    .name("kind")
                    .map(|m| m.as_str().to_lowercase())
                    .unwrap_or_default();
                if kind_filter.as_ref().is_some_and(|filter| *filter != kind) {
                    continue;
                }
                matches.push(format!(
                    "{}:{}: {}",
                    display.display(),
                    index + 1,
                    line.trim()
                ));
                if matches.len() >= max_results {
                    break 'files;
                }
            }
        }

        Ok(
            ToolResult::ok(truncate(&matches.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"matches": matches.len()})),
        )
    }
}

fn symbol_pattern() -> regex::Regex {
    regex::Regex::new(
        r"(?x)
        ^\s*
        (?:pub(?:\([^)]*\))?\s+)?
        (?:(?:async|unsafe|export|default|abstract|final)\s+)*
        (?P<kind>fn|func|def|struct|enum|trait|impl|mod|const|static|type|class|interface|function)\s+
        (?:\([^)]*\)\s*)?
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    )
    .expect("symbol regex")
}

fn is_code_file(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|extension| extension.to_str()),
        Some(
            "rs" | "ts"
                | "tsx"
                | "js"
                | "jsx"
                | "mjs"
                | "cjs"
                | "py"
                | "go"
                | "java"
                | "kt"
                | "kts"
                | "c"
                | "h"
                | "cc"
                | "cpp"
                | "hpp"
                | "cs"
                | "rb"
                | "php"
                | "swift"
                | "scala"
                | "lua"
                | "sql"
                | "sh"
                | "bash"
        )
    )
}

struct WriteFileTool;

#[async_trait]
impl Tool for WriteFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "write_file",
            "Write a complete UTF-8 file inside the workspace or an approved external path. Existing files must be read first.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("write_file".to_owned()));
        }
        let path = context.resolve_write(Path::new(&string_arg(&arguments, "path")?))?;
        check_write_policy(context, &path)?;
        if path.exists() && !context.was_read(&path) {
            return Err(ToolError::Failed(format!(
                "read the file before overwriting it: {}",
                path.display()
            )));
        }
        let content = string_arg(&arguments, "content")?;
        if let Some(kind) = detect_secret(&content) {
            return Err(ToolError::Failed(format!(
                "write blocked: content looks like a {kind}; remove or replace the secret before writing"
            )));
        }
        let previous = if path.exists() {
            Some(std::fs::read_to_string(&path)?)
        } else {
            None
        };
        let checkpoint = checkpoint_before_write(context, &path)?;
        let _undo = record_undo(context, &path)?;
        atomic_write(&path, &content)?;
        let validation = match run_validators(context, &path).await {
            Ok(validation) => validation,
            Err(error) => {
                restore_previous(&path, previous.as_deref())?;
                return Err(error);
            }
        };
        let warning = scope_warning(context, &path);
        Ok(ToolResult::ok(format!(
            "wrote {} bytes to {}{}{}",
            content.len(),
            path.display(),
            warning
                .as_deref()
                .map(|warning| format!("\n{warning}"))
                .unwrap_or_default(),
            validation
                .as_deref()
                .map(|validation| format!("\n{validation}"))
                .unwrap_or_default()
        ))
        .with_metadata(json!({
            "path": path,
            "bytes": content.len(),
            "checkpoint": checkpoint,
            "scope_warning": warning,
            "validation": validation
        })))
    }
}

struct EditFileTool;

#[async_trait]
impl Tool for EditFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "edit_file",
            "Replace an exact string in a workspace or approved external file. The file must be read first.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "replace_all": {"type": "boolean"}
            },
            "required": ["path", "old", "new"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("edit_file".to_owned()));
        }
        let path = context.resolve_write(Path::new(&string_arg(&arguments, "path")?))?;
        check_write_policy(context, &path)?;
        if !context.was_read(&path) {
            return Err(ToolError::Failed(format!(
                "read the file before editing it: {}",
                path.display()
            )));
        }
        let old = string_arg(&arguments, "old")?;
        if old.is_empty() {
            return Err(ToolError::InvalidArgument(
                "old must not be empty".to_owned(),
            ));
        }
        let new = string_arg(&arguments, "new")?;
        let replace_all = optional_bool(&arguments, "replace_all").unwrap_or(false);
        let content = std::fs::read_to_string(&path)?;
        let count = content.matches(&old).count();
        if count == 0 {
            return Err(ToolError::Failed("old string was not found".to_owned()));
        }
        if count > 1 && !replace_all {
            return Err(ToolError::Failed(format!(
                "old string occurs {count} times; set replace_all or provide more context"
            )));
        }
        let updated = if replace_all {
            content.replace(&old, &new)
        } else {
            content.replacen(&old, &new, 1)
        };
        if let Some(kind) = detect_secret(&updated) {
            return Err(ToolError::Failed(format!(
                "edit blocked: result looks like a {kind}; remove or replace the secret before writing"
            )));
        }
        let previous = Some(content);
        let checkpoint = checkpoint_before_write(context, &path)?;
        let _undo = record_undo(context, &path)?;
        atomic_write(&path, &updated)?;
        let validation = match run_validators(context, &path).await {
            Ok(validation) => validation,
            Err(error) => {
                restore_previous(&path, previous.as_deref())?;
                return Err(error);
            }
        };
        let warning = scope_warning(context, &path);
        Ok(ToolResult::ok(format!(
            "updated {}{}{}",
            path.display(),
            warning
                .as_deref()
                .map(|warning| format!("\n{warning}"))
                .unwrap_or_default(),
            validation
                .as_deref()
                .map(|validation| format!("\n{validation}"))
                .unwrap_or_default()
        ))
        .with_metadata(json!({
            "path": path,
            "replacements": count,
            "checkpoint": checkpoint,
            "scope_warning": warning,
            "validation": validation
        })))
    }
}

struct RunCommandTool;

#[async_trait]
impl Tool for RunCommandTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_command",
            "Run a shell command in the workspace. Requires Full access. Commands are classified as read-only, write-workspace, network, destructive, or privileged; destructive commands need confirm_destructive=true.",
            RiskLevel::Execute,
        )
        .with_timeout(30)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "description": "Workspace-relative directory, or an approved absolute directory with allow_external_cwd"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
                "confirm_destructive": {"type": "boolean", "description": "Explicitly allow a destructive command such as rm -rf or git reset --hard"},
                "allow_external_cwd": {"type": "boolean", "description": "Allow an absolute cwd outside the workspace"},
                "parse": {"type": "string", "enum": ["auto", "rust", "pytest", "eslint", "typescript"], "description": "Extract structured diagnostics from output"}
            },
            "required": ["command"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "run_command (commands are disabled; switch the composer access mode to Full access)"
                    .to_owned(),
            ));
        }
        let command = string_arg(&arguments, "command")?;
        let class = classify_command(&command);
        if class == CommandClass::Destructive
            && !optional_bool(&arguments, "confirm_destructive").unwrap_or(false)
        {
            return Err(ToolError::ApprovalRequired(
                "run_command (destructive command; set confirm_destructive=true after verifying the blast radius)"
                    .to_owned(),
            ));
        }
        check_command_policy(context, class, &command)?;
        let cwd = match optional_string(&arguments, "cwd") {
            Some(cwd) => {
                let candidate = Path::new(&cwd);
                if candidate.is_absolute() {
                    if !optional_bool(&arguments, "allow_external_cwd").unwrap_or(false) {
                        return Err(ToolError::OutsideRoot(cwd));
                    }
                    let canonical = candidate
                        .canonicalize()
                        .map_err(|error| ToolError::InvalidArgument(format!("cwd: {error}")))?;
                    if !canonical.is_dir() {
                        return Err(ToolError::InvalidArgument(format!(
                            "cwd is not a directory: {cwd}"
                        )));
                    }
                    canonical
                } else {
                    context.resolve_write(candidate)?
                }
            }
            None => context.workspace.root().to_path_buf(),
        };
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .unwrap_or(30)
            .clamp(1, 600) as u64;

        let mut process = if cfg!(windows) {
            let mut process = std::process::Command::new("cmd");
            process.args(["/C", &command]);
            process
        } else {
            let mut process = std::process::Command::new("sh");
            process.args(["-c", &command]);
            process
        };
        process
            .current_dir(cwd)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            process.process_group(0);
        }
        let mut child = process.spawn()?;
        let stdout_reader = child.stdout.take().map(|mut stream| {
            std::thread::spawn(move || {
                let mut output = String::new();
                let _ = stream.read_to_string(&mut output);
                output
            })
        });
        let stderr_reader = child.stderr.take().map(|mut stream| {
            std::thread::spawn(move || {
                let mut output = String::new();
                let _ = stream.read_to_string(&mut output);
                output
            })
        });

        let status = child.wait_timeout(Duration::from_secs(timeout))?;
        kill_process_tree(&mut child);
        let status = match status {
            Some(status) => status,
            None => {
                let _ = child.wait();
                return Err(ToolError::Timeout(timeout));
            }
        };
        let stdout = stdout_reader
            .and_then(|reader| reader.join().ok())
            .unwrap_or_default();
        let stderr = stderr_reader
            .and_then(|reader| reader.join().ok())
            .unwrap_or_default();

        let parse = optional_string(&arguments, "parse").unwrap_or_else(|| "auto".to_owned());
        let diagnostics = parse_diagnostics(&parse, &format!("{stderr}\n{stdout}"));
        let full = format!(
            "$ {command}\nexit: {}\nstdout:\n{stdout}\nstderr:\n{stderr}",
            status.code().unwrap_or(-1)
        );
        let log_directory = context.workspace.root().join(".openbad").join("logs");
        let log_path = log_directory.join(format!("command-{}.log", now_millis()));
        std::fs::create_dir_all(&log_directory)?;
        std::fs::write(&log_path, &full)?;
        let preview_cap = (context.max_output_bytes / 2).max(2_048);
        let content = if full.len() <= preview_cap {
            full.clone()
        } else {
            format!(
                "{}\n… [full output: {} bytes at {}] …\n{}",
                head_tail(&full, preview_cap / 2),
                full.len(),
                log_path.display(),
                head_tail(&full, preview_cap / 2)
            )
        };
        let content = if diagnostics.is_empty() {
            content
        } else {
            let summary = diagnostics
                .iter()
                .take(20)
                .map(|diagnostic| {
                    format!(
                        "- {} {}:{} {}",
                        diagnostic["severity"].as_str().unwrap_or("error"),
                        diagnostic["path"].as_str().unwrap_or_default(),
                        diagnostic["line"].as_u64().unwrap_or(0),
                        diagnostic["message"].as_str().unwrap_or_default()
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            format!("{content}\n\ndiagnostics: {}\n{summary}", diagnostics.len())
        };
        Ok(ToolResult::ok(content).with_metadata(json!({
            "exit_code": status.code(),
            "success": status.success(),
            "command_class": class.label(),
            "output_bytes": full.len(),
            "log_path": log_path,
            "diagnostics": diagnostics
        })))
    }
}

fn kill_process_tree(child: &mut std::process::Child) {
    #[cfg(unix)]
    {
        let process_group = -(child.id() as i32);
        // The child was spawned in its own process group, so this also kills
        // helpers it left behind.
        unsafe {
            libc::kill(process_group, libc::SIGKILL);
        }
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    let _ = child.kill();
}

struct RememberTool;

#[async_trait]
impl Tool for RememberTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "remember",
            "Save one short, durable snippet to workspace memory: a preference, gotcha, constraint, or decision. Memory is indexed and searchable; use add_knowledge for documents. Repeating the same content updates it; contradicting an existing snippet requires its id in supersedes.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "pinned": {"type": "boolean"},
                "scope": {"type": "string", "enum": ["session", "project", "global"], "description": "Durability scope; project by default"},
                "origin_task": {"type": "string", "description": "Task or issue this memory came from"},
                "supersedes": {"type": "string", "description": "Existing memory id this statement replaces"},
                "ttl_hours": {"type": "integer", "minimum": 1, "description": "Optional expiry in hours"}
            },
            "required": ["content"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let memory = context
            .memory
            .as_ref()
            .ok_or_else(|| ToolError::Failed("workspace memory is not available".to_owned()))?;
        let content = string_arg(&arguments, "content")?;
        if content.trim().is_empty() {
            return Err(ToolError::InvalidArgument(
                "content must not be empty".to_owned(),
            ));
        }
        let tags = arguments
            .get("tags")
            .and_then(Value::as_array)
            .map(|tags| {
                tags.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let pinned = optional_bool(&arguments, "pinned").unwrap_or(false);
        let scope = optional_string(&arguments, "scope").unwrap_or_else(|| "project".to_owned());
        if !matches!(scope.as_str(), "session" | "project" | "global") {
            return Err(ToolError::InvalidArgument(
                "scope must be session, project, or global".to_owned(),
            ));
        }
        let supersedes = optional_string(&arguments, "supersedes");
        if supersedes.is_none() {
            let existing = memory.list(100).map_err(ToolError::Failed)?;
            let conflicts = existing
                .iter()
                .filter(|entry| likely_memory_conflict(&content, &entry.content))
                .map(|entry| format!("{}: {}", entry.id, entry.content))
                .collect::<Vec<_>>();
            if !conflicts.is_empty() {
                return Err(ToolError::Failed(format!(
                    "possible contradiction with existing memory; pass supersedes=<id> to replace it explicitly:
{}",
                    conflicts.join("
")
                )));
            }
        }
        let expires_at = optional_usize(&arguments, "ttl_hours")
            .map(|hours| now_millis().saturating_add((hours as i64) * 3_600_000));
        let entry = memory
            .remember_scoped(
                &content,
                tags,
                pinned,
                MemoryWriteOptions {
                    scope: scope.clone(),
                    author: "agent".to_owned(),
                    origin_turn: context.current_turn(),
                    origin_task: optional_string(&arguments, "origin_task"),
                    supersedes,
                    expires_at,
                },
            )
            .map_err(ToolError::Failed)?;
        let tags = if entry.tags.is_empty() {
            String::new()
        } else {
            format!(" [{}]", entry.tags.join(", "))
        };
        Ok(ToolResult::ok(format!(
            "remembered ({}){}{}: {}",
            entry.scope,
            if entry.pinned { " (pinned)" } else { "" },
            tags,
            entry.content
        ))
        .with_metadata(json!({
            "id": entry.id,
            "scope": entry.scope,
            "supersedes": entry.supersedes
        })))
    }
}

struct RecallTool;

#[async_trait]
impl Tool for RecallTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "recall",
            "Search the indexed durable-memory snippets for prior decisions, preferences, constraints, and gotchas. Returns snippets, not knowledge documents.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let memory = context
            .memory
            .as_ref()
            .ok_or_else(|| ToolError::Failed("workspace memory is not available".to_owned()))?;
        let query = optional_string(&arguments, "query");
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(10)
            .clamp(1, 50);
        let entries = memory
            .recall(query.as_deref(), max_results)
            .map_err(ToolError::Failed)?;
        if entries.is_empty() {
            return Ok(ToolResult::ok("no matching memories"));
        }
        let content = entries
            .iter()
            .map(|entry| {
                let tags = if entry.tags.is_empty() {
                    String::new()
                } else {
                    format!(" [{}]", entry.tags.join(", "))
                };
                memory_line(entry, &tags)
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"matches": entries.len()})))
    }
}

struct ListMemoriesTool;

#[async_trait]
impl Tool for ListMemoriesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_memories",
            "List durable project memories with ids, scope, provenance, tags, and pinned state so they can be audited.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let memory = context
            .memory
            .as_ref()
            .ok_or_else(|| ToolError::Failed("workspace memory is not available".to_owned()))?;
        let limit = optional_usize(&arguments, "max_results")
            .unwrap_or(50)
            .clamp(1, 100);
        let entries = memory.list(limit).map_err(ToolError::Failed)?;
        if entries.is_empty() {
            return Ok(ToolResult::ok("no saved memories"));
        }
        let content = entries
            .iter()
            .map(|entry| {
                let tags = if entry.tags.is_empty() {
                    String::new()
                } else {
                    format!(" [{}]", entry.tags.join(", "))
                };
                memory_line(entry, &tags)
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"memories": entries.len()})))
    }
}

struct UpdateMemoryTool;

#[async_trait]
impl Tool for UpdateMemoryTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "update_memory",
            "Correct a memory's content, tags, pinned state, or scope. Use list_memories first to find the id.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "pinned": {"type": "boolean"},
                "scope": {"type": "string", "enum": ["session", "project", "global"]}
            },
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let memory = context
            .memory
            .as_ref()
            .ok_or_else(|| ToolError::Failed("workspace memory is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        let content = optional_string(&arguments, "content");
        let tags = arguments.get("tags").and_then(Value::as_array).map(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        });
        let pinned = optional_bool(&arguments, "pinned");
        let scope = optional_string(&arguments, "scope");
        if content.is_none() && tags.is_none() && pinned.is_none() && scope.is_none() {
            return Err(ToolError::InvalidArgument(
                "at least one of content, tags, pinned, or scope is required".to_owned(),
            ));
        }
        let entry = memory
            .update(&id, content.as_deref(), tags, pinned, scope.as_deref())
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "updated memory {id} ({}, {}, updated): {}",
            entry.scope,
            if entry.pinned { "pinned" } else { "unpinned" },
            entry.content
        ))
        .with_metadata(json!({"id": entry.id, "scope": entry.scope})))
    }
}

struct ForgetMemoryTool;

#[async_trait]
impl Tool for ForgetMemoryTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "forget_memory",
            "Forget a durable project memory by id. Use list_memories first to find the id.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let memory = context
            .memory
            .as_ref()
            .ok_or_else(|| ToolError::Failed("workspace memory is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        memory.forget(&id).map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!("Forgot memory {id}.")))
    }
}

struct SearchProjectTool;

#[async_trait]
impl Tool for SearchProjectTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "search_project",
            "Search the indexed project sources together: knowledge documents, code, and memory snippets. Cite the returned locator; use get_knowledge for full documents and recall for snippet-only searches.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 30}
            },
            "required": ["query"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project retrieval is not available".to_owned()))?;
        let query = string_arg(&arguments, "query")?;
        if query.trim().is_empty() {
            return Err(ToolError::InvalidArgument(
                "query must not be empty".to_owned(),
            ));
        }
        let limit = optional_usize(&arguments, "max_results")
            .unwrap_or(8)
            .clamp(1, 30);
        let hits = project.retrieve(&query, limit).map_err(ToolError::Failed)?;
        if hits.is_empty() {
            return Ok(ToolResult::ok(format!(
                "No project knowledge, code, or memory matched {query:?}."
            )));
        }
        let content = hits
            .iter()
            .enumerate()
            .map(|(index, hit)| {
                format!(
                    "[{}] {} · {} · {}\n{}",
                    index + 1,
                    hit.source,
                    hit.title,
                    hit.locator,
                    hit.snippet
                )
            })
            .collect::<Vec<_>>()
            .join("\n\n");
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "matches": hits.len(),
                "query": query
            })),
        )
    }
}

struct AddKnowledgeTool;

#[async_trait]
impl Tool for AddKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "add_knowledge",
            "Add a long-form project document to the knowledge library: a note, decision, reference, procedure, finding, or glossary entry. Documents are chunked and full-text indexed; use remember for short atomic snippets instead.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "kind": {"type": "string", "enum": ["note", "decision", "reference", "gotcha", "procedure", "glossary", "finding", "question"]},
                "source": {"type": "string", "description": "Optional URL or path"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["verified", "inferred", "uncertain"], "description": "How firmly the content is established; verified means checked against primary sources or code."},
                "supersedes": {"type": "string", "description": "Existing knowledge id this note replaces"},
                "review_status": {"type": "string", "enum": ["proposed", "accepted", "rejected"], "description": "Curated state; proposed by default for agent notes"}
            },
            "required": ["title", "content"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let title = string_arg(&arguments, "title")?;
        let content = string_arg(&arguments, "content")?;
        if content.trim().is_empty() {
            return Err(ToolError::InvalidArgument(
                "content must not be empty".to_owned(),
            ));
        }
        let source = optional_string(&arguments, "source");
        let tags = arguments
            .get("tags")
            .and_then(Value::as_array)
            .map(|tags| {
                tags.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let confidence =
            optional_string(&arguments, "confidence").unwrap_or_else(|| "inferred".to_owned());
        if !matches!(confidence.as_str(), "verified" | "inferred" | "uncertain") {
            return Err(ToolError::InvalidArgument(
                "confidence must be verified, inferred, or uncertain".to_owned(),
            ));
        }
        let kind = optional_string(&arguments, "kind").unwrap_or_else(|| "note".to_owned());
        if !matches!(
            kind.as_str(),
            "note"
                | "decision"
                | "reference"
                | "gotcha"
                | "procedure"
                | "glossary"
                | "finding"
                | "question"
        ) {
            return Err(ToolError::InvalidArgument(
                "kind must be note, decision, reference, gotcha, procedure, glossary, finding, or question"
                    .to_owned(),
            ));
        }
        let review_status =
            optional_string(&arguments, "review_status").unwrap_or_else(|| "proposed".to_owned());
        if !matches!(review_status.as_str(), "proposed" | "accepted" | "rejected") {
            return Err(ToolError::InvalidArgument(
                "review_status must be proposed, accepted, or rejected".to_owned(),
            ));
        }
        let supersedes = optional_string(&arguments, "supersedes");
        if supersedes.is_none() {
            let existing = project.list_knowledge().map_err(ToolError::Failed)?;
            let mut conflicts = Vec::new();
            for existing in existing.iter().take(20) {
                let Ok(Some(document)) = project.get_knowledge(&existing.id) else {
                    continue;
                };
                if likely_memory_conflict(&content, &document.content) {
                    conflicts.push(format!("{}: {}", document.id, document.title));
                }
            }
            if !conflicts.is_empty() {
                return Err(ToolError::Failed(format!(
                    "possible contradiction with existing knowledge; pass supersedes=<id> to replace it explicitly:\n{}",
                    conflicts.join("\n")
                )));
            }
        }
        let entry = project
            .add_knowledge_detailed(
                &title,
                &content,
                &kind,
                source.as_deref(),
                tags,
                &confidence,
                supersedes.as_deref(),
                &review_status,
            )
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "Added {} knowledge \"{}\" ({} chars, {} chunks, confidence {}, {}, id {}).",
            entry.kind,
            entry.title,
            content.chars().count(),
            entry.chunks,
            entry.confidence,
            entry.review_status,
            entry.id
        ))
        .with_metadata(json!({
            "id": entry.id,
            "title": entry.title,
            "chunks": entry.chunks,
            "kind": entry.kind,
            "review_status": entry.review_status
        })))
    }
}

struct ListKnowledgeTool;

#[async_trait]
impl Tool for ListKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_knowledge",
            "List project knowledge documents with ids, kinds, tags, sources, and chunk counts.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let entries = project.list_knowledge().map_err(ToolError::Failed)?;
        if entries.is_empty() {
            return Ok(ToolResult::ok("no knowledge documents"));
        }
        let content = entries
            .iter()
            .map(|entry| {
                let source = entry
                    .source
                    .as_deref()
                    .map_or_else(String::new, |source| format!(" · {source}"));
                format!(
                    "- {} [{}]{} · {} chunks · confidence {}{} · {}",
                    entry.title,
                    entry.kind,
                    source,
                    entry.chunks,
                    entry.confidence,
                    if entry.stale { " · STALE" } else { "" },
                    entry.id
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"documents": entries.len()})))
    }
}

struct GetKnowledgeTool;

#[async_trait]
impl Tool for GetKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "get_knowledge",
            "Read a project knowledge document by id, including its full content and metadata.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        let document = project
            .get_knowledge(&id)
            .map_err(ToolError::Failed)?
            .ok_or_else(|| {
                ToolError::InvalidArgument(format!("unknown knowledge document: {id}"))
            })?;
        let tags = if document.tags.is_empty() {
            String::new()
        } else {
            format!(" [{}]", document.tags.join(", "))
        };
        let stale = if document.stale {
            "\n\nWarning: the source file changed after this document was captured; update the note before relying on it."
        } else {
            ""
        };
        let content = format!(
            "{} [{}]{} · {} chunks · confidence {}{} · {}\n\n{}{}",
            document.title,
            document.kind,
            tags,
            document.chunks,
            document.confidence,
            if document.stale { " · STALE" } else { "" },
            document.id,
            document.content,
            stale
        );
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "id": document.id,
                "title": document.title,
                "chunks": document.chunks
            })),
        )
    }
}

struct UpdateKnowledgeTool;

#[async_trait]
impl Tool for UpdateKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "update_knowledge",
            "Correct a project knowledge document's title, content, kind, source, tags, confidence, supersession, or review status. Omitted fields stay unchanged; content changes rebuild chunks.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "source": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["verified", "inferred", "uncertain"]},
                "kind": {"type": "string", "enum": ["note", "decision", "reference", "gotcha", "procedure", "glossary", "finding", "question"]},
                "supersedes": {"type": "string"},
                "review_status": {"type": "string", "enum": ["proposed", "accepted", "rejected", "superseded"]}
            },
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        let title = optional_string(&arguments, "title");
        let content = optional_string(&arguments, "content");
        let source = optional_string(&arguments, "source");
        let tags = arguments.get("tags").and_then(Value::as_array).map(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        });
        let confidence = optional_string(&arguments, "confidence");
        if let Some(confidence) = confidence.as_deref() {
            if !matches!(confidence, "verified" | "inferred" | "uncertain") {
                return Err(ToolError::InvalidArgument(
                    "confidence must be verified, inferred, or uncertain".to_owned(),
                ));
            }
        }
        let kind = optional_string(&arguments, "kind");
        if let Some(kind) = kind.as_deref() {
            if !matches!(
                kind,
                "note"
                    | "decision"
                    | "reference"
                    | "gotcha"
                    | "procedure"
                    | "glossary"
                    | "finding"
                    | "question"
            ) {
                return Err(ToolError::InvalidArgument(
                    "kind must be note, decision, reference, gotcha, procedure, glossary, finding, or question"
                        .to_owned(),
                ));
            }
        }
        let review_status = optional_string(&arguments, "review_status");
        if let Some(review_status) = review_status.as_deref() {
            if !matches!(
                review_status,
                "proposed" | "accepted" | "rejected" | "superseded"
            ) {
                return Err(ToolError::InvalidArgument(
                    "review_status must be proposed, accepted, rejected, or superseded".to_owned(),
                ));
            }
        }
        let supersedes = optional_string(&arguments, "supersedes");
        if title.is_none()
            && content.is_none()
            && source.is_none()
            && tags.is_none()
            && confidence.is_none()
            && kind.is_none()
            && review_status.is_none()
            && supersedes.is_none()
        {
            return Err(ToolError::InvalidArgument(
                "at least one field is required".to_owned(),
            ));
        }
        let document = project
            .update_knowledge_detailed(
                &id,
                title.as_deref(),
                content.as_deref(),
                kind.as_deref(),
                source.as_deref(),
                tags,
                confidence.as_deref(),
                supersedes.as_deref(),
                review_status.as_deref(),
            )
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "Updated {} knowledge \"{}\" ({} chunks, {}, confidence {}, id {}).",
            document.kind,
            document.title,
            document.chunks,
            document.review_status,
            document.confidence,
            document.id
        ))
        .with_metadata(json!({
            "id": document.id,
            "chunks": document.chunks,
            "confidence": document.confidence,
            "review_status": document.review_status
        })))
    }
}

struct ExportKnowledgeTool;

#[async_trait]
impl Tool for ExportKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "export_knowledge",
            "Export project knowledge to git-friendly Markdown files under knowledge/ with frontmatter.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Default knowledge"},
                "include_all": {"type": "boolean", "description": "Include proposed/rejected/superseded documents"}
            }
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("export_knowledge".to_owned()));
        }
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let directory = context.resolve_write(Path::new(
            &optional_string(&arguments, "directory").unwrap_or_else(|| "knowledge".to_owned()),
        ))?;
        std::fs::create_dir_all(&directory)?;
        let include_all = optional_bool(&arguments, "include_all").unwrap_or(false);
        let entries = project.list_knowledge().map_err(ToolError::Failed)?;
        let mut exported = Vec::new();
        for entry in entries {
            if !include_all && matches!(entry.review_status.as_str(), "rejected" | "superseded") {
                continue;
            }
            let Some(document) = project
                .get_knowledge(&entry.id)
                .map_err(ToolError::Failed)?
            else {
                continue;
            };
            let frontmatter = json!({
                "title": document.title,
                "kind": document.kind,
                "tags": document.tags,
                "confidence": document.confidence,
                "review_status": document.review_status,
                "source": document.source,
                "supersedes": document.supersedes
            });
            let slug = document
                .title
                .to_ascii_lowercase()
                .chars()
                .map(|character| {
                    if character.is_ascii_alphanumeric() {
                        character
                    } else {
                        '-'
                    }
                })
                .collect::<String>()
                .split('-')
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join("-");
            let name = format!("{}-{}.md", slug, &document.id[..document.id.len().min(8)]);
            let body = format!(
                "---\n{}\n---\n\n{}\n",
                serde_json::to_string_pretty(&frontmatter)
                    .map_err(|error| ToolError::Failed(error.to_string()))?,
                document.content
            );
            std::fs::write(directory.join(&name), body)?;
            exported.push(name);
        }
        Ok(ToolResult::ok(format!(
            "exported {} knowledge documents to {}",
            exported.len(),
            directory.display()
        ))
        .with_metadata(json!({"exported": exported})))
    }
}

struct ImportKnowledgeTool;

#[async_trait]
impl Tool for ImportKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "import_knowledge",
            "Import Markdown files from knowledge/ (or a given directory) into the project knowledge library. Frontmatter fields are honored.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Default knowledge"}
            }
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("import_knowledge".to_owned()));
        }
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let directory = context.resolve_read(Path::new(
            &optional_string(&arguments, "directory").unwrap_or_else(|| "knowledge".to_owned()),
        ))?;
        if !directory.is_dir() {
            return Ok(ToolResult::ok("knowledge directory does not exist"));
        }
        let mut imported = Vec::new();
        let mut files = std::fs::read_dir(&directory)?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().and_then(|extension| extension.to_str()) == Some("md"))
            .collect::<Vec<_>>();
        files.sort();
        for path in files {
            let raw = std::fs::read_to_string(&path)?;
            let (frontmatter, content) = split_markdown_frontmatter(&raw);
            let metadata: Value = frontmatter
                .and_then(|frontmatter| {
                    serde_json::from_str(&frontmatter)
                        .ok()
                        .or_else(|| serde_yaml::from_str(&frontmatter).ok())
                })
                .unwrap_or_else(|| json!({}));
            let title = metadata["title"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| {
                    path.file_stem()
                        .and_then(|stem| stem.to_str())
                        .unwrap_or("Imported note")
                        .to_owned()
                });
            let kind = metadata["kind"].as_str().unwrap_or("note");
            let confidence = metadata["confidence"].as_str().unwrap_or("inferred");
            let review_status = metadata["review_status"].as_str().unwrap_or("proposed");
            let source = metadata["source"].as_str();
            let supersedes = metadata["supersedes"].as_str();
            let tags = metadata["tags"]
                .as_array()
                .map(|tags| {
                    tags.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_default();
            let entry = project
                .add_knowledge_detailed(
                    &title,
                    content.trim(),
                    kind,
                    source,
                    tags,
                    confidence,
                    supersedes,
                    review_status,
                )
                .map_err(ToolError::Failed)?;
            imported.push(entry.id);
        }
        Ok(
            ToolResult::ok(format!("imported {} knowledge documents", imported.len()))
                .with_metadata(json!({"imported": imported})),
        )
    }
}

fn split_markdown_frontmatter(raw: &str) -> (Option<String>, String) {
    let trimmed = raw
        .strip_prefix("---\n")
        .or_else(|| raw.strip_prefix("---\r\n"));
    let Some(rest) = trimmed else {
        return (None, raw.to_owned());
    };
    if let Some((frontmatter, body)) = rest.split_once("\n---") {
        let body = body.strip_prefix('\n').unwrap_or(body);
        return (Some(frontmatter.to_owned()), body.to_owned());
    }
    (None, raw.to_owned())
}

struct ReviewKnowledgeTool;

#[async_trait]
impl Tool for ReviewKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "review_knowledge",
            "Accept, reject, propose, or supersede a knowledge document. Rejected and superseded documents leave retrieval.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": ["proposed", "accepted", "rejected", "superseded"]}
            },
            "required": ["id", "status"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        let status = string_arg(&arguments, "status")?;
        let document = project
            .review_knowledge(&id, &status)
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "knowledge {} is now {}",
            document.id, document.review_status
        ))
        .with_metadata(json!({"id": document.id, "review_status": document.review_status})))
    }
}

struct DeleteKnowledgeTool;

#[async_trait]
impl Tool for DeleteKnowledgeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "delete_knowledge",
            "Delete a project knowledge document by id. This removes its chunks from retrieval.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project knowledge is not available".to_owned()))?;
        let id = string_arg(&arguments, "id")?;
        project.delete_knowledge(&id).map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!("Deleted knowledge document {id}.")))
    }
}

struct ListProjectSourcesTool;

#[async_trait]
impl Tool for ListProjectSourcesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_project_sources",
            "List reference folders and knowledge documents in this project. Use read_repo_file to inspect a reference folder.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context
            .project
            .as_ref()
            .ok_or_else(|| ToolError::Failed("project sources are not available".to_owned()))?;
        let repos = project.list_repos().map_err(ToolError::Failed)?;
        let knowledge = project.list_knowledge().map_err(ToolError::Failed)?;
        let mut sections = Vec::new();
        if repos.is_empty() {
            sections.push("References: none linked.".to_owned());
        } else {
            let mut repo_lines = vec!["References:".to_owned()];
            for repo in repos {
                repo_lines.push(format!(
                    "- {} ({}){}",
                    repo.name,
                    repo.path,
                    if repo.read_only { " [read-only]" } else { "" }
                ));
            }
            sections.push(repo_lines.join("\n"));
        }
        if knowledge.is_empty() {
            sections.push("Knowledge: empty.".to_owned());
        } else {
            let mut knowledge_lines = vec!["Knowledge:".to_owned()];
            for entry in knowledge {
                let source = entry
                    .source
                    .map_or_else(String::new, |source| format!(" · {source}"));
                knowledge_lines.push(format!(
                    "- {} [{}]{} · {} chunks · {}",
                    entry.title, entry.kind, source, entry.chunks, entry.id
                ));
            }
            sections.push(knowledge_lines.join("\n"));
        }
        Ok(ToolResult::ok(truncate(
            &sections.join("\n\n"),
            context.max_output_bytes,
        )))
    }
}

struct ReadRepoFileTool;

#[async_trait]
impl Tool for ReadRepoFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_repo_file",
            "Read a UTF-8 file from a project reference folder. The path is confined to that reference.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Linked repository id or name"},
                "path": {"type": "string", "description": "Repository-relative path"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "max_bytes": {"type": "integer", "minimum": 256},
                "budget_tokens": {"type": "integer", "minimum": 64}
            },
            "required": ["repo", "path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let project = context.project.as_ref().ok_or_else(|| {
            ToolError::Failed("project repositories are not available".to_owned())
        })?;
        let reference = string_arg(&arguments, "repo")?;
        let repos = project.list_repos().map_err(ToolError::Failed)?;
        let repo = repos
            .into_iter()
            .find(|repo| repo.id == reference || repo.name == reference)
            .ok_or_else(|| {
                ToolError::InvalidArgument(format!("unknown repository: {reference}"))
            })?;
        let root = std::fs::canonicalize(&repo.path)?;
        let path = root
            .join(string_arg(&arguments, "path")?)
            .canonicalize()
            .map_err(|error| ToolError::InvalidArgument(format!("repository path: {error}")))?;
        if !path.starts_with(&root) {
            return Err(ToolError::OutsideRoot(path.display().to_string()));
        }
        let content = std::fs::read_to_string(&path)?;
        let start = optional_usize(&arguments, "start_line").unwrap_or(1).max(1);
        let end = optional_usize(&arguments, "end_line").unwrap_or(usize::MAX);
        if end < start {
            return Err(ToolError::InvalidArgument(
                "end_line must be greater than or equal to start_line".to_owned(),
            ));
        }
        let lines: Vec<&str> = content.lines().collect();
        let total_lines = lines.len();
        let end = end.min(total_lines);
        let selected = lines
            .into_iter()
            .enumerate()
            .filter(|(index, _)| {
                let line = index + 1;
                line >= start && line <= end
            })
            .map(|(_, line)| line)
            .collect::<Vec<_>>()
            .join("\n");
        let shown = selected.lines().count();
        let max_bytes = optional_usize(&arguments, "max_bytes")
            .or_else(|| optional_usize(&arguments, "budget_tokens").map(|tokens| tokens * 4))
            .unwrap_or(context.max_output_bytes)
            .clamp(256, context.max_output_bytes);
        let truncated = selected.len() > max_bytes;
        Ok(
            ToolResult::ok(truncate(&selected, max_bytes)).with_metadata(json!({
                "repo": repo.name,
                "path": path,
                "lines": shown,
                "total_lines": total_lines,
                "start_line": start.min(total_lines.saturating_add(1)),
                "end_line": end,
                "truncated": truncated,
                "max_bytes": max_bytes,
                "has_more": end < total_lines,
                "next_start_line": if end < total_lines { Some(end + 1) } else { None },
            })),
        )
    }
}

fn project_reference(
    context: &ToolContext,
    arguments: &Value,
) -> Result<ProjectRepoRef, ToolError> {
    let project = context
        .project
        .as_ref()
        .ok_or_else(|| ToolError::Failed("project references are not available".to_owned()))?;
    let reference = string_arg(arguments, "repo")?;
    project
        .list_repos()
        .map_err(ToolError::Failed)?
        .into_iter()
        .find(|repo| repo.id == reference || repo.name == reference)
        .ok_or_else(|| ToolError::InvalidArgument(format!("unknown reference: {reference}")))
}

fn resolve_reference_path(
    repo: &ProjectRepoRef,
    relative: &str,
) -> Result<(PathBuf, PathBuf), ToolError> {
    let root = std::fs::canonicalize(&repo.path)?;
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|error| ToolError::InvalidArgument(format!("reference path: {error}")))?;
    if !path.starts_with(&root) {
        return Err(ToolError::OutsideRoot(path.display().to_string()));
    }
    Ok((root, path))
}

struct ListRepoDirTool;

#[async_trait]
impl Tool for ListRepoDirTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_repo_dir",
            "List a directory inside a project reference folder with relative paths and sizes. Respects .gitignore.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Reference folder id or name"},
                "path": {"type": "string", "description": "Reference-relative directory"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000}
            },
            "required": ["repo"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let repo = project_reference(context, &arguments)?;
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let (root, path) = resolve_reference_path(&repo, &relative)?;
        if !path.is_dir() {
            return Err(ToolError::InvalidArgument(format!(
                "not a directory in {}: {relative}",
                repo.name
            )));
        }
        let depth = optional_usize(&arguments, "depth")
            .unwrap_or(2)
            .clamp(1, 10);
        let max_entries = optional_usize(&arguments, "max_entries")
            .unwrap_or(500)
            .clamp(1, 2_000);
        let mut entries = Vec::new();
        for entry in WalkBuilder::new(&path)
            .max_depth(Some(depth))
            .build()
            .filter_map(Result::ok)
        {
            if entries.len() >= max_entries {
                break;
            }
            if entry.path() == path {
                continue;
            }
            let relative = entry.path().strip_prefix(&root).unwrap_or(entry.path());
            if entry.file_type().is_some_and(|kind| kind.is_dir()) {
                entries.push(format!("{}/", relative.display()));
            } else {
                let size = entry.metadata().map(|metadata| metadata.len()).unwrap_or(0);
                entries.push(format!("{} ({size} bytes)", relative.display()));
            }
        }
        entries.sort();
        Ok(
            ToolResult::ok(truncate(&entries.join("\n"), context.max_output_bytes)).with_metadata(
                json!({
                    "reference": repo.name,
                    "root": relative,
                    "entries": entries.len(),
                    "truncated": entries.len() >= max_entries
                }),
            ),
        )
    }
}

struct GlobRepoTool;

#[async_trait]
impl Tool for GlobRepoTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "glob_repo",
            "Find files inside a project reference folder by glob such as src/packs/*.yaml or docs/**/*.md.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Reference folder id or name"},
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Reference-relative directory"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 5000}
            },
            "required": ["repo", "pattern"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let repo = project_reference(context, &arguments)?;
        let pattern = string_arg(&arguments, "pattern")?;
        if pattern.is_empty() {
            return Err(ToolError::InvalidArgument(
                "pattern must not be empty".to_owned(),
            ));
        }
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let (root, path) = resolve_reference_path(&repo, &relative)?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(500)
            .clamp(1, 5_000);
        let matcher = Glob::new(&pattern)
            .map_err(|error| ToolError::InvalidArgument(format!("invalid glob: {error}")))?
            .compile_matcher();
        let match_basename = !pattern.contains('/');
        let mut matches = Vec::new();
        for entry in WalkBuilder::new(&path).build().filter_map(Result::ok) {
            if matches.len() >= max_results {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                continue;
            }
            let entry_path = entry.path().strip_prefix(&root).unwrap_or(entry.path());
            let candidate = if match_basename {
                entry_path.file_name().map(Path::new).unwrap_or(entry_path)
            } else {
                entry_path
            };
            if matcher.is_match(candidate) {
                matches.push(entry_path.display().to_string());
            }
        }
        matches.sort();
        Ok(
            ToolResult::ok(truncate(&matches.join("\n"), context.max_output_bytes)).with_metadata(
                json!({
                    "reference": repo.name,
                    "matches": matches.len(),
                    "truncated": matches.len() >= max_results
                }),
            ),
        )
    }
}

struct GrepRepoTool;

#[async_trait]
impl Tool for GrepRepoTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "grep_repo",
            "Search text inside a project reference folder. Returns path:line matches, case-insensitive by default.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Reference folder id or name"},
                "query": {"type": "string"},
                "path": {"type": "string", "description": "Reference-relative directory"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                "case_sensitive": {"type": "boolean"}
            },
            "required": ["repo", "query"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let repo = project_reference(context, &arguments)?;
        let query = string_arg(&arguments, "query")?;
        if query.is_empty() {
            return Err(ToolError::InvalidArgument(
                "query must not be empty".to_owned(),
            ));
        }
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let (root, path) = resolve_reference_path(&repo, &relative)?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(100)
            .clamp(1, 500);
        let case_sensitive = optional_bool(&arguments, "case_sensitive").unwrap_or(false);
        let needle = if case_sensitive {
            query.clone()
        } else {
            query.to_lowercase()
        };
        let mut matches = Vec::new();
        'files: for entry in WalkBuilder::new(&path).build().filter_map(Result::ok) {
            if matches.len() >= max_results {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file())
                || path_is_excluded(context, entry.path())
            {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(metadata) if metadata.len() <= 1024 * 1024 => metadata,
                _ => continue,
            };
            let _ = metadata;
            let content = match std::fs::read(entry.path()) {
                Ok(bytes) => match String::from_utf8(bytes) {
                    Ok(text) => text,
                    Err(_) => continue,
                },
                Err(_) => continue,
            };
            for (index, line) in content.lines().enumerate() {
                let haystack = if case_sensitive {
                    line.to_owned()
                } else {
                    line.to_lowercase()
                };
                if haystack.contains(&needle) {
                    let entry_path = entry.path().strip_prefix(&root).unwrap_or(entry.path());
                    matches.push(format!(
                        "{}:{}: {}",
                        entry_path.display(),
                        index + 1,
                        line.trim()
                    ));
                    if matches.len() >= max_results {
                        break 'files;
                    }
                }
            }
        }
        Ok(
            ToolResult::ok(truncate(&matches.join("\n"), context.max_output_bytes)).with_metadata(
                json!({
                    "reference": repo.name,
                    "matches": matches.len(),
                    "truncated": matches.len() >= max_results
                }),
            ),
        )
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct WebCacheEntry {
    hash: String,
    fetched_at: i64,
    title: Option<String>,
    content_type: Option<String>,
    final_url: String,
}

fn web_cache_dir(context: &ToolContext) -> std::path::PathBuf {
    context.workspace.root().join(".openbad").join("web-cache")
}

fn load_web_cache(context: &ToolContext) -> HashMap<String, WebCacheEntry> {
    let path = web_cache_dir(context).join("index.json");
    std::fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok())
        .unwrap_or_default()
}

fn save_web_cache(
    context: &ToolContext,
    url: &str,
    body: &str,
    entry: &WebCacheEntry,
) -> Result<(), ToolError> {
    let directory = web_cache_dir(context);
    std::fs::create_dir_all(&directory)?;
    std::fs::write(directory.join(format!("{}.txt", entry.hash)), body)?;
    let mut cache = load_web_cache(context);
    cache.insert(url.to_owned(), entry.clone());
    std::fs::write(
        directory.join("index.json"),
        serde_json::to_string_pretty(&cache)
            .map_err(|error| ToolError::Failed(error.to_string()))?,
    )?;
    Ok(())
}

fn html_to_text(html: &str) -> String {
    let scripts =
        regex::Regex::new(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>").expect("regex");
    let breaks =
        regex::Regex::new(r"(?i)</?(p|div|br|li|tr|h[1-6]|section|article)[^>]*>").expect("regex");
    let tags = regex::Regex::new(r"(?s)<[^>]+>").expect("regex");
    let text = scripts.replace_all(html, " ");
    let text = breaks.replace_all(&text, "\n");
    let text = tags.replace_all(&text, " ");
    let decoded = text
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ");
    decoded
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn html_title(html: &str) -> Option<String> {
    let title = regex::Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
    title
        .captures(html)?
        .get(1)
        .map(|capture| html_to_text(capture.as_str()))
        .filter(|title| !title.is_empty())
}

struct WebFetchTool;

#[async_trait]
impl Tool for WebFetchTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "web_fetch",
            "Fetch an http(s) URL, extract readable text (HTML gets a title and stripped body), and cache it by hash under .openbad/web-cache. Requires Full access and respects network_hosts policy.",
            RiskLevel::Read,
        )
        .with_timeout(30)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "format": {"type": "string", "enum": ["text", "html"], "description": "Return readable text (default) or raw HTML"},
                "prefer_cache": {"type": "boolean", "description": "Reuse a cached fetch when younger than max_age_hours (default true)"},
                "max_age_hours": {"type": "integer", "minimum": 1, "maximum": 720},
                "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 1048576},
                "max_tokens": {"type": "integer", "minimum": 256, "description": "Approximate output budget (4 bytes per token)"},
                "budget_tokens": {"type": "integer", "minimum": 256}
            },
            "required": ["url"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "web_fetch (network reads require Full access)".to_owned(),
            ));
        }
        let raw = string_arg(&arguments, "url")?;
        let url = reqwest::Url::parse(&raw)
            .map_err(|error| ToolError::InvalidArgument(format!("invalid URL: {error}")))?;
        if !matches!(url.scheme(), "http" | "https") {
            return Err(ToolError::InvalidArgument(
                "web_fetch supports http and https only".to_owned(),
            ));
        }
        let host = url
            .host_str()
            .ok_or_else(|| ToolError::InvalidArgument("URL has no host".to_owned()))?
            .to_owned();
        if let Some(policy) = load_policy(context)? {
            if let Some(reason) = policy.network_denied(&host) {
                return Err(ToolError::ApprovalRequired(reason));
            }
        }
        let format = optional_string(&arguments, "format").unwrap_or_else(|| "text".to_owned());
        let prefer_cache = optional_bool(&arguments, "prefer_cache").unwrap_or(true);
        let max_age_hours = optional_usize(&arguments, "max_age_hours")
            .unwrap_or(24)
            .clamp(1, 720);
        let max_bytes = optional_usize(&arguments, "max_bytes")
            .or_else(|| {
                optional_usize(&arguments, "max_tokens")
                    .or_else(|| optional_usize(&arguments, "budget_tokens"))
                    .map(|tokens| tokens * 4)
            })
            .unwrap_or(250_000)
            .clamp(1_024, 1_048_576);
        let cache = load_web_cache(context);
        if prefer_cache {
            if let Some(entry) = cache.get(&raw) {
                let age = now_millis().saturating_sub(entry.fetched_at);
                if age <= (max_age_hours as i64) * 3_600_000 {
                    let path = web_cache_dir(context).join(format!("{}.txt", entry.hash));
                    if let Ok(body) = std::fs::read_to_string(path) {
                        let body = if format == "html" {
                            body
                        } else {
                            html_to_text(&body)
                        };
                        let title = entry.title.clone().unwrap_or_else(|| raw.clone());
                        return Ok(ToolResult::ok(truncate(
                            &format!("{title}\n{}\n\n{body}", entry.final_url),
                            max_bytes,
                        ))
                        .with_metadata(json!({
                            "url": raw,
                            "final_url": entry.final_url,
                            "title": entry.title,
                            "content_type": entry.content_type,
                            "hash": entry.hash,
                            "fetched_at": entry.fetched_at,
                            "cached": true,
                            "max_bytes": max_bytes,
                            "max_tokens": max_bytes / 4
                        })));
                    }
                }
            }
        }

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .user_agent("OpenBaD/0.1 (+https://github.com/bruceamoser/OpenBaD)")
            .build()
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| ToolError::Failed(format!("web_fetch: {error}")))?;
        let status = response.status();
        let final_url = response.url().to_string();
        let final_host = response.url().host_str().unwrap_or_default().to_owned();
        if let Some(policy) = load_policy(context)? {
            if let Some(reason) = policy.network_denied(&final_host) {
                return Err(ToolError::ApprovalRequired(format!(
                    "redirect blocked: {reason}"
                )));
            }
        }
        if response
            .content_length()
            .is_some_and(|length| length > 2_097_152)
        {
            return Err(ToolError::Failed(
                "response is larger than 2 MiB".to_owned(),
            ));
        }
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let bytes = response
            .bytes()
            .await
            .map_err(|error| ToolError::Failed(format!("web_fetch body: {error}")))?;
        let raw_body = String::from_utf8_lossy(&bytes).into_owned();
        let title = html_title(&raw_body);
        let text = if content_type
            .as_deref()
            .is_some_and(|content_type| content_type.contains("html"))
            || raw_body.trim_start().starts_with('<')
        {
            html_to_text(&raw_body)
        } else {
            raw_body.clone()
        };
        let hash = blake3::hash(bytes.as_ref()).to_hex().to_string();
        let fetched_at = now_millis();
        let entry = WebCacheEntry {
            hash: hash.clone(),
            fetched_at,
            title: title.clone(),
            content_type: content_type.clone(),
            final_url: final_url.clone(),
        };
        save_web_cache(context, &raw, &raw_body, &entry)?;
        let body = if format == "html" { &raw_body } else { &text };
        let heading = title.clone().unwrap_or_else(|| raw.clone());
        Ok(ToolResult::ok(truncate(
            &format!("{heading}\n{final_url}\n\n{body}"),
            max_bytes,
        ))
        .with_metadata(json!({
            "url": raw,
            "final_url": final_url,
            "status": status.as_u16(),
            "title": title,
            "content_type": content_type,
            "hash": hash,
            "fetched_at": fetched_at,
            "cached": false,
            "max_bytes": max_bytes,
            "max_tokens": max_bytes / 4
        })))
    }
}

struct PresentArtifactTool;

#[async_trait]
impl Tool for PresentArtifactTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "present_artifact",
            "Open an artifact beside the conversation: a web page, image, markdown note, diff, or workspace file. Use this when visual inspection helps more than prose.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["web", "image", "markdown", "diff", "file"]},
                "title": {"type": "string"},
                "uri": {"type": "string", "description": "URL, workspace-relative path, or knowledge:<document_id>"},
                "content": {"type": "string", "description": "Inline markdown or diff content"},
                "language": {"type": "string"}
            },
            "required": ["kind", "title"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        _context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let kind = string_arg(&arguments, "kind")?;
        if !matches!(
            kind.as_str(),
            "web" | "image" | "markdown" | "diff" | "file"
        ) {
            return Err(ToolError::InvalidArgument(format!(
                "unsupported artifact kind: {kind}"
            )));
        }
        let title = string_arg(&arguments, "title")?;
        let uri = optional_string(&arguments, "uri");
        let content = optional_string(&arguments, "content");
        if uri.is_none() && content.is_none() {
            return Err(ToolError::InvalidArgument(
                "uri or content is required".to_owned(),
            ));
        }
        if kind == "web" {
            let uri = uri
                .as_deref()
                .ok_or_else(|| ToolError::InvalidArgument("web artifacts need a uri".to_owned()))?;
            if !uri.starts_with("https://") && !uri.starts_with("http://") {
                return Err(ToolError::InvalidArgument(
                    "web artifact uri must be http(s)".to_owned(),
                ));
            }
        }
        let language = optional_string(&arguments, "language");
        let artifact = json!({
            "kind": kind,
            "title": title,
            "uri": uri,
            "content": content,
            "language": language,
        });
        Ok(ToolResult::ok(format!(
            "Presented {kind} artifact \"{title}\" beside the chat."
        ))
        .with_metadata(json!({"openbad_artifact": artifact})))
    }
}

fn sh_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

async fn run_tool_shell(context: &ToolContext, command: String) -> Result<ToolResult, ToolError> {
    RunCommandTool
        .execute(json!({"command": command, "timeout_seconds": 300}), context)
        .await
}

struct ContextReportTool;

#[async_trait]
impl Tool for ContextReportTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "context_report",
            "Estimate what this turn is holding by category: loaded files, project knowledge, memories, rules, and ruled-out findings.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let loaded = context.loaded_paths();
        let loaded_bytes = loaded
            .iter()
            .map(|path| {
                std::fs::metadata(path)
                    .map(|metadata| metadata.len())
                    .unwrap_or(0)
            })
            .sum::<u64>();
        let (knowledge_count, knowledge_chars) = context
            .project
            .as_ref()
            .map(|project| {
                project
                    .list_knowledge()
                    .map(|entries| {
                        (
                            entries.len(),
                            entries
                                .iter()
                                .map(|entry| entry.chunks * 1_400)
                                .sum::<u64>(),
                        )
                    })
                    .unwrap_or((0, 0))
            })
            .unwrap_or((0, 0));
        let (memory_count, memory_chars) = context
            .memory
            .as_ref()
            .map(|memory| {
                memory
                    .list(100)
                    .map(|entries| {
                        (
                            entries.len(),
                            entries
                                .iter()
                                .map(|entry| entry.content.chars().count() as u64)
                                .sum::<u64>(),
                        )
                    })
                    .unwrap_or((0, 0))
            })
            .unwrap_or((0, 0));
        let policy = context_policy(context)?;
        let dead_ends = openbad_workspace::load_dead_ends(context.workspace.root())
            .map(|entries| entries.len())
            .unwrap_or(0);
        let claims = openbad_workspace::load_claims(context.workspace.root())
            .map(|entries| entries.len())
            .unwrap_or(0);
        let unknowns = openbad_workspace::load_unknowns(context.workspace.root())
            .map(|entries| entries.len())
            .unwrap_or(0);
        let estimated_tokens = (loaded_bytes + knowledge_chars + memory_chars) / 4;
        let content = format!(
            "loaded files: {} ({} bytes)\nproject knowledge: {} documents (~{} chars)\nmemories: {} (~{} chars)\npinned rules: {} · excluded rules: {}\nrules to avoid: {} dead ends\nclaims: {} · known unknowns: {}\nestimated evidence tokens: ~{}\noutput cap: {} bytes",
            loaded.len(),
            loaded_bytes,
            knowledge_count,
            knowledge_chars,
            memory_count,
            memory_chars,
            policy.pinned.len(),
            policy.excluded.len(),
            dead_ends,
            claims,
            unknowns,
            estimated_tokens,
            context.max_output_bytes
        );
        Ok(ToolResult::ok(content).with_metadata(json!({
            "loaded_files": loaded.len(),
            "loaded_bytes": loaded_bytes,
            "knowledge_documents": knowledge_count,
            "memories": memory_count,
            "pinned_rules": policy.pinned.len(),
            "excluded_rules": policy.excluded.len(),
            "dead_ends": dead_ends,
            "claims": claims,
            "unknowns": unknowns,
            "estimated_tokens": estimated_tokens
        })))
    }
}

struct RecordClaimTool;

#[async_trait]
impl Tool for RecordClaimTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "record_claim",
            "Record a structured claim with provenance (executed, read, inferred, assumed), evidence, and how to check it.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "claim": {"type": "string"},
                "confidence": {"type": "string", "enum": ["executed", "read", "inferred", "assumed"]},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "how_to_check": {"type": "string"}
            },
            "required": ["claim", "confidence"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("record_claim".to_owned()));
        }
        let claim = string_arg(&arguments, "claim")?;
        let confidence = string_arg(&arguments, "confidence")?;
        if !openbad_workspace::validate_confidence(&confidence) {
            return Err(ToolError::InvalidArgument(
                "confidence must be executed, read, inferred, or assumed".to_owned(),
            ));
        }
        let mut claims = openbad_workspace::load_claims(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let entry = openbad_workspace::Claim {
            id: format!("claim-{}", now_millis()),
            claim,
            confidence,
            evidence: arguments
                .get("evidence")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_default(),
            how_to_check: optional_string(&arguments, "how_to_check"),
            created_at: now_millis(),
        };
        claims.push(entry.clone());
        openbad_workspace::save_claims(context.workspace.root(), &claims)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "recorded claim {} ({})",
            entry.id, entry.confidence
        ))
        .with_metadata(json!({"id": entry.id, "confidence": entry.confidence})))
    }
}

struct ListClaimsTool;

#[async_trait]
impl Tool for ListClaimsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_claims",
            "List recorded claims and provenance.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"query": {"type": "string"}}
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = optional_string(&arguments, "query").map(|query| query.to_ascii_lowercase());
        let claims = openbad_workspace::load_claims(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let visible = claims
            .iter()
            .filter(|claim| {
                query
                    .as_ref()
                    .is_none_or(|query| claim.claim.to_ascii_lowercase().contains(query))
            })
            .collect::<Vec<_>>();
        if visible.is_empty() {
            return Ok(ToolResult::ok("no claims"));
        }
        Ok(ToolResult::ok(
            visible
                .iter()
                .map(|claim| {
                    format!(
                        "- [{}] {}: {}{}",
                        claim.confidence,
                        claim.id,
                        claim.claim,
                        claim
                            .how_to_check
                            .as_deref()
                            .map(|check| format!(" (check: {check})"))
                            .unwrap_or_default()
                    )
                })
                .collect::<Vec<_>>()
                .join("\n"),
        ))
    }
}

struct RecordUnknownTool;

#[async_trait]
impl Tool for RecordUnknownTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "record_unknown",
            "Record a known unknown: a question or gap that is not yet resolved.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"}
            },
            "required": ["question"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("record_unknown".to_owned()));
        }
        let question = string_arg(&arguments, "question")?;
        let mut unknowns = openbad_workspace::load_unknowns(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let unknown = openbad_workspace::Unknown {
            id: format!("unknown-{}", now_millis()),
            question,
            context: optional_string(&arguments, "context"),
            created_at: now_millis(),
        };
        unknowns.push(unknown.clone());
        openbad_workspace::save_unknowns(context.workspace.root(), &unknowns)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!("recorded unknown {}", unknown.id))
            .with_metadata(json!({"id": unknown.id})))
    }
}

struct ListUnknownsTool;

#[async_trait]
impl Tool for ListUnknownsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_unknowns",
            "List unresolved known unknowns.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let unknowns = openbad_workspace::load_unknowns(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        if unknowns.is_empty() {
            return Ok(ToolResult::ok("no known unknowns"));
        }
        Ok(ToolResult::ok(
            unknowns
                .iter()
                .map(|unknown| format!("- [{}] {}", unknown.id, unknown.question))
                .collect::<Vec<_>>()
                .join("\n"),
        ))
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct JobRecord {
    id: String,
    command: String,
    pid: u32,
    started_at: i64,
    log_path: String,
    #[serde(default)]
    stopped: bool,
}

fn jobs_directory(context: &ToolContext) -> std::path::PathBuf {
    context.workspace.root().join(".openbad").join("jobs")
}

fn load_job(context: &ToolContext, id: &str) -> Result<JobRecord, ToolError> {
    let path = jobs_directory(context).join(format!("{id}.json"));
    let content = std::fs::read_to_string(path)
        .map_err(|error| ToolError::InvalidArgument(format!("unknown job {id}: {error}")))?;
    serde_json::from_str(&content).map_err(|error| ToolError::Failed(error.to_string()))
}

fn job_alive(pid: u32) -> bool {
    let path = std::path::Path::new("/proc")
        .join(pid.to_string())
        .join("stat");
    let Ok(stat) = std::fs::read_to_string(path) else {
        return false;
    };
    stat.split_whitespace().nth(2) != Some("Z")
}

struct UndoLastTool;

#[async_trait]
impl Tool for UndoLastTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "undo_last",
            "Restore the most recent write/edit undo record from .openbad/undo. Requires writes.",
            RiskLevel::Write,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("undo_last".to_owned()));
        }
        let directory = undo_directory(context);
        let mut records = if directory.is_dir() {
            std::fs::read_dir(&directory)?
                .filter_map(Result::ok)
                .filter(|entry| {
                    entry
                        .path()
                        .extension()
                        .and_then(|extension| extension.to_str())
                        == Some("json")
                })
                .collect::<Vec<_>>()
        } else {
            Vec::new()
        };
        records.sort_by_key(|entry| entry.file_name());
        let Some(record) = records.pop() else {
            return Ok(ToolResult::ok("undo stack is empty"));
        };
        let content = std::fs::read_to_string(record.path())?;
        let value: Value =
            serde_json::from_str(&content).map_err(|error| ToolError::Failed(error.to_string()))?;
        let path = context.resolve_write(Path::new(value["path"].as_str().unwrap_or_default()))?;
        check_write_policy(context, &path)?;
        let existed = value["existed"].as_bool().unwrap_or(false);
        if existed {
            atomic_write(&path, value["content"].as_str().unwrap_or_default())?;
        } else if path.exists() {
            std::fs::remove_file(&path)?;
        }
        let done = record.path().with_extension("done");
        std::fs::rename(record.path(), done)?;
        Ok(
            ToolResult::ok(format!("undid last write to {}", path.display()))
                .with_metadata(json!({"path": path, "existed": existed})),
        )
    }
}

struct InstructionsForTool;

#[async_trait]
impl Tool for InstructionsForTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "instructions_for",
            "Discover scoped instruction files (AGENTS.md, CLAUDE.md, .openbad/instructions.md) from a path's ancestors, root-first.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "repo": {"type": "string", "description": "Optional reference folder id or name"}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let raw = string_arg(&arguments, "path")?;
        let (path, root) = match optional_string(&arguments, "repo") {
            Some(reference) => {
                let root = repo_root(context, &reference)?.1;
                (resolve_repo_path(&root, &raw)?, root)
            }
            None => {
                let own_root = context
                    .workspace
                    .root()
                    .canonicalize()
                    .unwrap_or_else(|_| context.workspace.root().to_path_buf());
                match context.resolve_read(Path::new(&raw)) {
                    Ok(path) => (path, own_root),
                    Err(error) => {
                        let mut resolved = None;
                        if let Some(project) = &context.project {
                            if let Ok(repos) = project.list_repos() {
                                for repo in repos {
                                    let prefix = format!("{}/", repo.name);
                                    if let Some(remainder) = raw.strip_prefix(&prefix) {
                                        if let Ok(root) = std::fs::canonicalize(&repo.path) {
                                            if let Ok(path) = resolve_repo_path(&root, remainder) {
                                                resolved = Some((path, root));
                                                break;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        resolved.ok_or(error)?
                    }
                }
            }
        };
        let start = if path.is_dir() {
            path.clone()
        } else {
            path.parent().unwrap_or(&path).to_path_buf()
        };
        let mut directories = start
            .ancestors()
            .take_while(|directory| directory.starts_with(&root))
            .collect::<Vec<_>>();
        directories.reverse();
        let mut sections = Vec::new();
        for directory in directories {
            for name in ["AGENTS.md", "CLAUDE.md", ".openbad/instructions.md"] {
                let candidate = directory.join(name);
                if !candidate.is_file() {
                    continue;
                }
                if let Ok(content) = std::fs::read_to_string(&candidate) {
                    sections.push(format!(
                        "--- {} ---\n{}",
                        relative_to_root(context, &candidate),
                        content
                    ));
                }
            }
        }
        if sections.is_empty() {
            return Ok(ToolResult::ok("no scoped instructions"));
        }
        Ok(
            ToolResult::ok(truncate(&sections.join("\n\n"), context.max_output_bytes))
                .with_metadata(json!({"files": sections.len()})),
        )
    }
}

fn undo_directory(context: &ToolContext) -> std::path::PathBuf {
    context.workspace.root().join(".openbad").join("undo")
}

fn record_undo(
    context: &ToolContext,
    path: &Path,
) -> Result<Option<std::path::PathBuf>, ToolError> {
    let existed = path.exists();
    let content = if existed {
        Some(std::fs::read_to_string(path)?)
    } else {
        None
    };
    let directory = undo_directory(context);
    std::fs::create_dir_all(&directory)?;
    let name = format!(
        "{}-{}.json",
        now_millis(),
        relative_to_root(context, path).replace('/', "__")
    );
    let record = directory.join(name);
    std::fs::write(
        &record,
        serde_json::to_string_pretty(&json!({
            "path": relative_to_root(context, path),
            "existed": existed,
            "content": content,
            "created_at": now_millis()
        }))
        .map_err(|error| ToolError::Failed(error.to_string()))?,
    )?;
    Ok(Some(record))
}

struct ScaffoldProjectTool;

#[async_trait]
impl Tool for ScaffoldProjectTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "scaffold_project",
            "Create a starter project skeleton: manifest, README, docs, scratch, artifacts, and knowledge directories. Refuses to overwrite unless force=true.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["software", "docs", "creative", "general"]},
                "force": {"type": "boolean"}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("scaffold_project".to_owned()));
        }
        let name = string_arg(&arguments, "name")?;
        let kind = optional_string(&arguments, "kind").unwrap_or_else(|| "general".to_owned());
        let force = optional_bool(&arguments, "force").unwrap_or(false);
        let manifest_path = context.workspace.root().join(".openbad/project.yaml");
        if manifest_path.exists() && !force {
            return Err(ToolError::Failed(
                "project manifest already exists; pass force=true to replace it".to_owned(),
            ));
        }
        check_write_policy(context, &manifest_path)?;
        let (scope, commands) = match kind.as_str() {
            "software" => (
                "authoritative: [\"src/**\", \"apps/**\", \"docs/**\"]\n  generated: [\"target/**\", \"dist/**\"]\n  scratch: [\"scratch/**\"]",
                "commands:\n  verify: cargo test --workspace",
            ),
            "docs" => (
                "authoritative: [\"docs/**\", \"knowledge/**\"]\n  generated: [\"artifacts/**\"]\n  scratch: [\"scratch/**\"]",
                "commands: {}",
            ),
            "creative" => (
                "authoritative: [\"src/**\", \"content/**\", \"docs/**\"]\n  generated: [\"artifacts/**\"]\n  scratch: [\"scratch/**\"]",
                "commands: {}",
            ),
            _ => (
                "authoritative: [\"docs/**\", \"knowledge/**\"]\n  generated: [\"artifacts/**\"]\n  scratch: [\"scratch/**\"]",
                "commands: {}",
            ),
        };
        let manifest = format!(
            "name: {name}\ndescription: Scaffolded {kind} project\nscope:\n  {scope}\n{commands}\n"
        );
        std::fs::create_dir_all(context.workspace.root().join(".openbad"))?;
        std::fs::write(&manifest_path, manifest)?;
        let readme = context.workspace.root().join("README.md");
        if !readme.exists() || force {
            std::fs::write(
                &readme,
                format!(
                    "# {name}\n\nScaffolded with OpenBaD.\n\n- `docs/` — design and decisions\n- `knowledge/` — curated notes\n- `scratch/` — ephemeral work\n- `artifacts/` — published outputs\n"
                ),
            )?;
        }
        for directory in ["docs", "knowledge", "scratch", "artifacts"] {
            std::fs::create_dir_all(context.workspace.root().join(directory))?;
        }
        std::fs::write(
            context.workspace.root().join("docs/README.md"),
            "# Docs\n\nProject documentation index.\n",
        )
        .ok();
        Ok(ToolResult::ok(format!("scaffolded {kind} project {name}"))
            .with_metadata(json!({"name": name, "kind": kind, "manifest": manifest_path})))
    }
}

struct WorkspaceGcTool;

#[async_trait]
impl Tool for WorkspaceGcTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "workspace_gc",
            "Report large or generated workspace files. With delete=true and confirm=true, remove files under scratch/artifacts/dist/target.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "max_mb": {"type": "integer", "minimum": 1, "maximum": 10000},
                "delete": {"type": "boolean"},
                "confirm": {"type": "boolean"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let max_mb = optional_usize(&arguments, "max_mb")
            .unwrap_or(10)
            .clamp(1, 10_000);
        let max_bytes = max_mb as u64 * 1024 * 1024;
        let delete = optional_bool(&arguments, "delete").unwrap_or(false);
        if delete && !optional_bool(&arguments, "confirm").unwrap_or(false) {
            return Err(ToolError::ApprovalRequired(
                "workspace_gc delete requires confirm=true".to_owned(),
            ));
        }
        let roots = ["scratch", "artifacts", "dist", "target"];
        let mut candidates = Vec::new();
        let mut total = 0_u64;
        for root in roots {
            let path = context.workspace.root().join(root);
            if !path.is_dir() {
                continue;
            }
            for entry in WalkBuilder::new(&path).build().filter_map(Result::ok) {
                if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                    continue;
                }
                let size = entry.metadata().map(|metadata| metadata.len()).unwrap_or(0);
                total += size;
                if size >= max_bytes {
                    candidates.push((entry.path().to_path_buf(), size));
                }
            }
        }
        candidates.sort_by_key(|(_, size)| std::cmp::Reverse(*size));
        let mut removed = Vec::new();
        if delete {
            for (path, _) in &candidates {
                check_write_policy(context, path)?;
                if std::fs::remove_file(path).is_ok() {
                    removed.push(relative_to_root(context, path));
                }
            }
        }
        let content = candidates
            .iter()
            .take(100)
            .map(|(path, size)| {
                format!(
                    "{}: {} MB",
                    relative_to_root(context, path),
                    size / 1_048_576
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(if candidates.is_empty() {
            "no large generated files".to_owned()
        } else {
            content
        })
        .with_metadata(json!({
            "candidates": candidates.len(),
            "total_bytes": total,
            "removed": removed
        })))
    }
}

struct SessionCheckpointTool;

#[async_trait]
impl Tool for SessionCheckpointTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "session_checkpoint",
            "Capture plan, todos, handoff, claims, unknowns, and recent audit entries under .openbad/checkpoints.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("session_checkpoint".to_owned()));
        }
        let label = safe_label(&string_arg(&arguments, "label")?)?;
        let root = context.workspace.root();
        let read = |path: &Path| std::fs::read_to_string(path).unwrap_or_default();
        let checkpoint = json!({
            "label": label,
            "created_at": now_millis(),
            "plan": openbad_workspace::load_plan(root).ok().flatten(),
            "todos": read(&root.join(".openbad/todos.json")),
            "handoff": read(&root.join(".openbad/handoff.md")),
            "claims": openbad_workspace::load_claims(root).unwrap_or_default(),
            "unknowns": openbad_workspace::load_unknowns(root).unwrap_or_default(),
            "audit_tail": read(&root.join(".openbad/audit.jsonl"))
                .lines()
                .rev()
                .take(25)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
        });
        let directory = root.join(".openbad/checkpoints");
        std::fs::create_dir_all(&directory)?;
        let path = directory.join(format!("{label}.json"));
        std::fs::write(
            &path,
            serde_json::to_string_pretty(&checkpoint)
                .map_err(|error| ToolError::Failed(error.to_string()))?,
        )?;
        Ok(ToolResult::ok(format!("checkpoint {label} saved"))
            .with_metadata(json!({"label": label, "path": path})))
    }
}

struct SessionResumeTool;

#[async_trait]
impl Tool for SessionResumeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "session_resume",
            "Read a session checkpoint summary, or list checkpoints when no label is given.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let directory = context.workspace.root().join(".openbad/checkpoints");
        if !directory.is_dir() {
            return Ok(ToolResult::ok("no session checkpoints"));
        }
        let label = optional_string(&arguments, "label");
        if label.is_none() {
            let mut names = std::fs::read_dir(&directory)?
                .filter_map(Result::ok)
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .collect::<Vec<_>>();
            names.sort();
            return Ok(ToolResult::ok(names.join("\n")));
        }
        let label = safe_label(&label.unwrap_or_default())?;
        let content = std::fs::read_to_string(directory.join(format!("{label}.json")))
            .map_err(|error| ToolError::InvalidArgument(format!("unknown checkpoint: {error}")))?;
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"label": label})))
    }
}

struct ReferencesToTool;

#[async_trait]
impl Tool for ReferencesToTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "references_to",
            "Find likely references to a file by searching for its stem across the workspace (blast-radius preview).",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = string_arg(&arguments, "path")?;
        let stem = Path::new(&path)
            .file_stem()
            .and_then(|stem| stem.to_str())
            .unwrap_or(&path)
            .to_owned();
        let result = GrepTool
            .execute(
                json!({
                    "query": stem,
                    "max_results": optional_usize(&arguments, "max_results").unwrap_or(100),
                    "case_sensitive": false
                }),
                context,
            )
            .await?;
        let metadata = result.metadata.clone();
        Ok(result.with_metadata(json!({"stem": stem, "matches": metadata})))
    }
}

struct GoldenCompareTool;

#[async_trait]
impl Tool for GoldenCompareTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "golden_compare",
            "Compare a generated file to a committed baseline and return a unified diff.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "baseline": {"type": "string"}
            },
            "required": ["path", "baseline"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        let baseline = context.resolve_read(Path::new(&string_arg(&arguments, "baseline")?))?;
        let current = std::fs::read_to_string(&path)?;
        let expected = std::fs::read_to_string(&baseline)?;
        let diff = similar::TextDiff::from_lines(&expected, &current)
            .unified_diff()
            .context_radius(3)
            .header("baseline", "current")
            .to_string();
        Ok(ToolResult::ok(if current == expected {
            "golden: pass".to_owned()
        } else {
            diff
        })
        .with_metadata(json!({"matches": current == expected})))
    }
}

struct GoldenAcceptTool;

#[async_trait]
impl Tool for GoldenAcceptTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "golden_accept",
            "Promote a generated file to the committed baseline. Requires confirm=true.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "baseline": {"type": "string"},
                "confirm": {"type": "boolean"}
            },
            "required": ["path", "baseline"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("golden_accept".to_owned()));
        }
        if !optional_bool(&arguments, "confirm").unwrap_or(false) {
            return Err(ToolError::ApprovalRequired(
                "golden_accept requires confirm=true".to_owned(),
            ));
        }
        let path = context.resolve_write(Path::new(&string_arg(&arguments, "path")?))?;
        let baseline = context.resolve_write(Path::new(&string_arg(&arguments, "baseline")?))?;
        check_write_policy(context, &baseline)?;
        if let Some(parent) = baseline.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::copy(&path, &baseline)?;
        Ok(ToolResult::ok(format!(
            "accepted {} as golden",
            baseline.display()
        )))
    }
}

struct PlanWriteTool;

#[async_trait]
impl Tool for PlanWriteTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plan_write",
            "Write a draft plan to .openbad/plan.json. A human must approve it in Project → Plan before execution.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "verify_with": {"type": "string", "description": "Recipe or command that proves the step"}
                        },
                        "required": ["text"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["goal", "steps"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("plan_write".to_owned()));
        }
        let goal = string_arg(&arguments, "goal")?;
        let raw_steps = arguments
            .get("steps")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if raw_steps.is_empty() {
            return Err(ToolError::InvalidArgument(
                "plan must contain at least one step".to_owned(),
            ));
        }
        let existing = openbad_workspace::load_plan(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let steps = raw_steps
            .iter()
            .enumerate()
            .map(|(index, step)| openbad_workspace::PlanStep {
                id: step["id"]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("{}", index + 1)),
                text: step["text"].as_str().unwrap_or_default().to_owned(),
                status: "todo".to_owned(),
                verify_with: step["verify_with"].as_str().map(str::to_owned),
                evidence: None,
            })
            .collect::<Vec<_>>();
        let now = now_millis();
        let plan = openbad_workspace::Plan {
            goal,
            status: "draft".to_owned(),
            steps,
            created_at: existing.as_ref().map_or(now, |plan| plan.created_at),
            updated_at: now,
            approved_at: None,
        };
        openbad_workspace::save_plan(context.workspace.root(), &plan)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "draft plan saved with {} steps; ask the human to approve it in Project → Plan",
            plan.steps.len()
        ))
        .with_metadata(json!({
            "goal": plan.goal,
            "steps": plan.steps.len(),
            "status": plan.status
        })))
    }
}

struct PlanReadTool;

#[async_trait]
impl Tool for PlanReadTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plan_read",
            "Read the current plan and step state.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        match openbad_workspace::load_plan(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
        {
            Some(plan) => Ok(ToolResult::ok(openbad_workspace::plan_summary(&plan))
                .with_metadata(json!({"status": plan.status, "steps": plan.steps.len()}))),
            None => Ok(ToolResult::ok("no plan")),
        }
    }
}

struct PlanUpdateStepTool;

#[async_trait]
impl Tool for PlanUpdateStepTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plan_update_step",
            "Update a plan step status and optional evidence.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": ["todo", "doing", "done", "blocked", "dropped"]},
                "evidence": {"type": "string"}
            },
            "required": ["id", "status"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("plan_update_step".to_owned()));
        }
        let plan = openbad_workspace::update_step(
            context.workspace.root(),
            &string_arg(&arguments, "id")?,
            &string_arg(&arguments, "status")?,
            optional_string(&arguments, "evidence").as_deref(),
        )
        .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(openbad_workspace::plan_summary(&plan)))
    }
}

struct PlanVerifyTool;

#[async_trait]
impl Tool for PlanVerifyTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plan_verify",
            "Run a plan step's verify_with recipe or command and record pass/fail evidence.",
            RiskLevel::Execute,
        )
        .with_timeout(600)
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let id = string_arg(&arguments, "id")?;
        let plan = openbad_workspace::load_plan(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| ToolError::Failed("no plan".to_owned()))?;
        let step = plan
            .steps
            .iter()
            .find(|step| step.id == id)
            .ok_or_else(|| ToolError::InvalidArgument(format!("unknown plan step: {id}")))?;
        let check = step
            .verify_with
            .clone()
            .ok_or_else(|| ToolError::InvalidArgument(format!("step {id} has no verify_with")))?;
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let recipe = manifest
            .as_ref()
            .and_then(|manifest| manifest.recipe(&check));
        let mut command = json!({
            "command": recipe.as_ref().map(|recipe| recipe.command.clone()).unwrap_or_else(|| check.clone()),
            "timeout_seconds": 300
        });
        if let Some(cwd) = recipe.as_ref().and_then(|recipe| recipe.cwd.clone()) {
            command["cwd"] = json!(cwd);
            command["allow_external_cwd"] = json!(true);
        }
        let result = RunCommandTool.execute(command, context).await?;
        let success = result.metadata["success"].as_bool().unwrap_or(false);
        let evidence = format!("{}: {}", check, if success { "passed" } else { "failed" });
        let plan = openbad_workspace::update_step(
            context.workspace.root(),
            &id,
            if success { "done" } else { "blocked" },
            Some(&evidence),
        )
        .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "step {id}: {}\n{}",
            if success { "passed" } else { "failed" },
            result.content
        ))
        .with_metadata(json!({
            "id": id,
            "success": success,
            "plan_status": plan.status
        })))
    }
}

struct JobStartTool;

#[async_trait]
impl Tool for JobStartTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "job_start",
            "Start a background command and return a job id. Logs stream to .openbad/jobs/<id>.log. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "confirm_destructive": {"type": "boolean"}
            },
            "required": ["command"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "job_start (requires Full access)".to_owned(),
            ));
        }
        let command = string_arg(&arguments, "command")?;
        let class = classify_command(&command);
        if class == CommandClass::Destructive
            && !optional_bool(&arguments, "confirm_destructive").unwrap_or(false)
        {
            return Err(ToolError::ApprovalRequired(
                "job_start (destructive command; set confirm_destructive=true)".to_owned(),
            ));
        }
        check_command_policy(context, class, &command)?;
        let directory = jobs_directory(context);
        std::fs::create_dir_all(&directory)?;
        let id = format!("job-{}", now_millis());
        let log_path = directory.join(format!("{id}.log"));
        let stdout = std::fs::File::create(&log_path)?;
        let stderr = stdout.try_clone()?;
        let mut process = std::process::Command::new("sh");
        process
            .args(["-c", &command])
            .current_dir(context.workspace.root())
            .stdout(stdout)
            .stderr(stderr)
            .stdin(std::process::Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            process.process_group(0);
        }
        let mut child = process.spawn()?;
        let pid = child.id();
        let record = JobRecord {
            id: id.clone(),
            command: command.clone(),
            pid,
            started_at: now_millis(),
            log_path: log_path.to_string_lossy().into_owned(),
            stopped: false,
        };
        std::fs::write(
            directory.join(format!("{id}.json")),
            serde_json::to_string_pretty(&record)
                .map_err(|error| ToolError::Failed(error.to_string()))?,
        )?;
        let _ = &mut child;
        Ok(ToolResult::ok(format!("started {id} (pid {pid})"))
            .with_metadata(json!({"id": id, "pid": pid, "log_path": log_path})))
    }
}

struct JobStatusTool;

#[async_trait]
impl Tool for JobStatusTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "job_status",
            "Report whether a background job is running.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let id = string_arg(&arguments, "id")?;
        let record = load_job(context, &id)?;
        let alive = !record.stopped && job_alive(record.pid);
        Ok(ToolResult::ok(format!(
            "{id}: {} (pid {})",
            if alive { "running" } else { "exited" },
            record.pid
        ))
        .with_metadata(json!({
            "id": record.id,
            "command": record.command,
            "pid": record.pid,
            "running": alive,
            "stopped": record.stopped,
            "started_at": record.started_at,
            "log_path": record.log_path
        })))
    }
}

struct JobLogsTool;

#[async_trait]
impl Tool for JobLogsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "job_logs",
            "Read the tail of a background job log.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 256, "maximum": 65536}
            },
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let id = string_arg(&arguments, "id")?;
        let record = load_job(context, &id)?;
        let max_bytes = optional_usize(&arguments, "max_bytes")
            .unwrap_or(8_192)
            .clamp(256, 65_536);
        let content = std::fs::read_to_string(&record.log_path).unwrap_or_default();
        let tail = if content.len() <= max_bytes {
            content
        } else {
            let start = content.len().saturating_sub(max_bytes);
            let start = content
                .char_indices()
                .find(|(index, _)| *index >= start)
                .map(|(index, _)| index)
                .unwrap_or(content.len());
            format!("…\n{}", &content[start..])
        };
        Ok(
            ToolResult::ok(truncate(&tail, max_bytes)).with_metadata(json!({
                "id": record.id,
                "running": !record.stopped && job_alive(record.pid),
                "stopped": record.stopped
            })),
        )
    }
}

struct JobStopTool;

#[async_trait]
impl Tool for JobStopTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "job_stop",
            "Stop a background job process group.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "job_stop (requires Full access)".to_owned(),
            ));
        }
        let id = string_arg(&arguments, "id")?;
        let mut record = load_job(context, &id)?;
        #[cfg(unix)]
        {
            unsafe {
                libc::kill(-(record.pid as i32), libc::SIGKILL);
            }
        }
        #[cfg(windows)]
        {
            let _ = std::process::Command::new("taskkill")
                .args(["/PID", &record.pid.to_string(), "/T", "/F"])
                .status();
        }
        record.stopped = true;
        std::fs::write(
            jobs_directory(context).join(format!("{id}.json")),
            serde_json::to_string_pretty(&record)
                .map_err(|error| ToolError::Failed(error.to_string()))?,
        )?;
        Ok(ToolResult::ok(format!("stopped {id}")))
    }
}

struct JobListTool;

#[async_trait]
impl Tool for JobListTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new("job_list", "List background jobs.", RiskLevel::Read)
            .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let directory = jobs_directory(context);
        if !directory.is_dir() {
            return Ok(ToolResult::ok("no jobs"));
        }
        let mut records = std::fs::read_dir(directory)?
            .filter_map(Result::ok)
            .filter(|entry| {
                entry
                    .path()
                    .extension()
                    .and_then(|extension| extension.to_str())
                    == Some("json")
            })
            .filter_map(|entry| std::fs::read_to_string(entry.path()).ok())
            .filter_map(|content| serde_json::from_str::<JobRecord>(&content).ok())
            .collect::<Vec<_>>();
        records.sort_by_key(|record| record.started_at);
        if records.is_empty() {
            return Ok(ToolResult::ok("no jobs"));
        }
        Ok(ToolResult::ok(
            records
                .iter()
                .map(|record| {
                    format!(
                        "- {}: {} ({})",
                        record.id,
                        if job_alive(record.pid) {
                            "running"
                        } else {
                            "exited"
                        },
                        record.command
                    )
                })
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .with_metadata(json!({"jobs": records.len()})))
    }
}

struct DiffFilesTool;

#[async_trait]
impl Tool for DiffFilesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "diff_files",
            "Return a unified diff between two workspace or reference-folder files.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
                "repo_a": {"type": "string"},
                "repo_b": {"type": "string"},
                "context_lines": {"type": "integer", "minimum": 0, "maximum": 20}
            },
            "required": ["a", "b"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let a = string_arg(&arguments, "a")?;
        let b = string_arg(&arguments, "b")?;
        let path_a = match optional_string(&arguments, "repo_a") {
            Some(reference) => resolve_repo_path(&repo_root(context, &reference)?.1, &a)?,
            None => context.resolve_read(Path::new(&a))?,
        };
        let path_b = match optional_string(&arguments, "repo_b") {
            Some(reference) => resolve_repo_path(&repo_root(context, &reference)?.1, &b)?,
            None => context.resolve_read(Path::new(&b))?,
        };
        ensure_not_excluded(context, &path_a)?;
        ensure_not_excluded(context, &path_b)?;
        let content_a = std::fs::read_to_string(&path_a)?;
        let content_b = std::fs::read_to_string(&path_b)?;
        let diff = similar::TextDiff::from_lines(&content_a, &content_b)
            .unified_diff()
            .context_radius(
                optional_usize(&arguments, "context_lines")
                    .unwrap_or(3)
                    .min(20),
            )
            .header(&a, &b)
            .to_string();
        Ok(
            ToolResult::ok(truncate(&diff, context.max_output_bytes)).with_metadata(json!({
                "a": path_a,
                "b": path_b,
                "changed": content_a != content_b
            })),
        )
    }
}

struct FileCitationTool;

#[async_trait]
impl Tool for FileCitationTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "file_citation",
            "Create a stable citation locator for a workspace or reference file: path@blake3:start-end.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "repo": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let raw = string_arg(&arguments, "path")?;
        let path = match optional_string(&arguments, "repo") {
            Some(reference) => resolve_repo_path(&repo_root(context, &reference)?.1, &raw)?,
            None => context.resolve_read(Path::new(&raw))?,
        };
        ensure_not_excluded(context, &path)?;
        let bytes = std::fs::read(&path)?;
        let hash = blake3::hash(&bytes).to_hex().to_string();
        let start = optional_usize(&arguments, "start_line").unwrap_or(1);
        let end = optional_usize(&arguments, "end_line");
        let lines = bytes.iter().filter(|byte| **byte == b'\n').count()
            + usize::from(!bytes.is_empty() && !bytes.ends_with(b"\n"));
        let suffix = match end {
            Some(end) => format!("L{start}-L{}", end.min(lines)),
            None => format!("L{start}-L{lines}"),
        };
        let locator = format!("{raw}@{hash}:{suffix}");
        Ok(ToolResult::ok(locator.clone()).with_metadata(json!({
            "locator": locator,
            "path": path,
            "hash": hash,
            "lines": lines
        })))
    }
}

struct ArchiveListTool;

#[async_trait]
impl Tool for ArchiveListTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "archive_list",
            "List entries in a zip archive without extracting it.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        ensure_not_excluded(context, &path)?;
        let file = std::fs::File::open(&path)?;
        let mut archive = zip::ZipArchive::new(file)
            .map_err(|error| ToolError::Failed(format!("zip archive: {error}")))?;
        let max_entries = optional_usize(&arguments, "max_entries")
            .unwrap_or(500)
            .clamp(1, 2_000);
        let mut entries = Vec::new();
        for index in 0..archive.len().min(max_entries) {
            let entry = archive
                .by_index(index)
                .map_err(|error| ToolError::Failed(error.to_string()))?;
            entries.push(format!(
                "{}{} ({} bytes)",
                entry.name(),
                if entry.is_dir() { "/" } else { "" },
                entry.size()
            ));
        }
        Ok(ToolResult::ok(entries.join("\n")).with_metadata(json!({
            "entries": entries.len(),
            "total_entries": archive.len()
        })))
    }
}

struct ArchiveReadTool;

#[async_trait]
impl Tool for ArchiveReadTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "archive_read",
            "Read one UTF-8 entry from a zip archive without extracting to disk.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "entry": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 256, "maximum": 1048576}
            },
            "required": ["path", "entry"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        ensure_not_excluded(context, &path)?;
        let entry = string_arg(&arguments, "entry")?;
        let file = std::fs::File::open(&path)?;
        let mut archive = zip::ZipArchive::new(file)
            .map_err(|error| ToolError::Failed(format!("zip archive: {error}")))?;
        let mut stream = archive
            .by_name(&entry)
            .map_err(|error| ToolError::InvalidArgument(format!("archive entry: {error}")))?;
        let max_bytes = optional_usize(&arguments, "max_bytes")
            .unwrap_or(256_000)
            .clamp(256, 1_048_576);
        let mut bytes = Vec::new();
        std::io::Read::take(&mut stream, max_bytes as u64).read_to_end(&mut bytes)?;
        let content = String::from_utf8_lossy(&bytes).into_owned();
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "entry": entry,
                "bytes": bytes.len()
            })),
        )
    }
}

struct GitChangesSinceTool;

#[async_trait]
impl Tool for GitChangesSinceTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_changes_since",
            "Show commits and changed paths since a revision.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"rev": {"type": "string"}},
            "required": ["rev"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let rev = string_arg(&arguments, "rev")?;
        let names = run_tool_shell(
            context,
            format!("git diff --name-status {}..HEAD", sh_quote(&rev)),
        )
        .await?;
        let log = run_tool_shell(
            context,
            format!("git log --oneline {}..HEAD", sh_quote(&rev)),
        )
        .await?;
        Ok(
            ToolResult::ok(format!("{}\n{}", log.content, names.content))
                .with_metadata(json!({"names": names.metadata, "log": log.metadata})),
        )
    }
}

struct RegistryLookupTool;

async fn fetch_registry_json(context: &ToolContext, url: &str) -> Result<Value, ToolError> {
    let parsed = reqwest::Url::parse(url)
        .map_err(|error| ToolError::InvalidArgument(format!("invalid registry URL: {error}")))?;
    let host = parsed.host_str().unwrap_or_default().to_owned();
    if let Some(policy) = load_policy(context)? {
        if let Some(reason) = policy.network_denied(&host) {
            return Err(ToolError::ApprovalRequired(reason));
        }
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .user_agent("OpenBaD/0.1")
        .build()
        .map_err(|error| ToolError::Failed(error.to_string()))?;
    let response = client
        .get(parsed)
        .send()
        .await
        .map_err(|error| ToolError::Failed(format!("registry request: {error}")))?;
    if !response.status().is_success() {
        return Err(ToolError::Failed(format!(
            "registry returned {}",
            response.status()
        )));
    }
    let body = response
        .text()
        .await
        .map_err(|error| ToolError::Failed(format!("registry body: {error}")))?;
    serde_json::from_str(&body)
        .map_err(|error| ToolError::Failed(format!("registry JSON: {error}")))
}

#[async_trait]
impl Tool for RegistryLookupTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "registry_lookup",
            "Look up package metadata in crates.io, npm, or PyPI with network policy enforcement.",
            RiskLevel::Read,
        )
        .with_timeout(30)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "ecosystem": {"type": "string", "enum": ["crates", "npm", "pypi"]},
                "package": {"type": "string"},
                "version": {"type": "string", "description": "Optional version to confirm exists"}
            },
            "required": ["ecosystem", "package"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "registry_lookup (network reads require Full access)".to_owned(),
            ));
        }
        let ecosystem = string_arg(&arguments, "ecosystem")?;
        let package = string_arg(&arguments, "package")?;
        if !package.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | '/' | '@')
        }) {
            return Err(ToolError::InvalidArgument(
                "invalid package name".to_owned(),
            ));
        }
        let version = optional_string(&arguments, "version");
        let (_, latest, versions) = match ecosystem.as_str() {
            "crates" => {
                let value = fetch_registry_json(
                    context,
                    &format!("https://crates.io/api/v1/crates/{package}"),
                )
                .await?;
                let latest = value["crate"]["max_version"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned();
                let versions = value["versions"]
                    .as_array()
                    .map(|versions| {
                        versions
                            .iter()
                            .filter_map(|version| version["num"].as_str().map(str::to_owned))
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                (String::new(), latest, versions)
            }
            "npm" => {
                let value =
                    fetch_registry_json(context, &format!("https://registry.npmjs.org/{package}"))
                        .await?;
                let latest = value["dist-tags"]["latest"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned();
                let versions = value["versions"]
                    .as_object()
                    .map(|versions| versions.keys().cloned().collect::<Vec<_>>())
                    .unwrap_or_default();
                (String::new(), latest, versions)
            }
            "pypi" => {
                let value =
                    fetch_registry_json(context, &format!("https://pypi.org/pypi/{package}/json"))
                        .await?;
                let latest = value["info"]["version"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned();
                let versions = value["releases"]
                    .as_object()
                    .map(|versions| versions.keys().cloned().collect::<Vec<_>>())
                    .unwrap_or_default();
                (String::new(), latest, versions)
            }
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown ecosystem: {other}"
                )));
            }
        };
        let found = version
            .as_deref()
            .map(|version| versions.iter().any(|item| item == version));
        if found == Some(false) {
            return Err(ToolError::Failed(format!(
                "{ecosystem} package {package} has no version {}",
                version.as_deref().unwrap_or_default()
            )));
        }
        let mut versions = versions;
        versions.sort();
        versions.reverse();
        let content = format!(
            "{ecosystem}:{package} latest={latest}{} ({} versions)",
            version
                .as_deref()
                .map(|version| format!(" requested={version} exists={}", found.unwrap_or(false)))
                .unwrap_or_default(),
            versions.len()
        );
        Ok(ToolResult::ok(content).with_metadata(json!({
            "ecosystem": ecosystem,
            "package": package,
            "latest": latest,
            "requested": version,
            "exists": found,
            "versions": versions.into_iter().take(20).collect::<Vec<_>>()
        })))
    }
}

struct PreviewWriteTool;

#[async_trait]
impl Tool for PreviewWriteTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "preview_write",
            "Return a unified diff for a proposed write without changing the file.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_write(Path::new(&string_arg(&arguments, "path")?))?;
        ensure_not_excluded(context, &path)?;
        let old = if path.exists() {
            std::fs::read_to_string(&path)?
        } else {
            String::new()
        };
        let new = string_arg(&arguments, "content")?;
        let diff = similar::TextDiff::from_lines(&old, &new)
            .unified_diff()
            .context_radius(3)
            .header(
                &format!("a/{}", relative_to_root(context, &path)),
                &format!("b/{}", relative_to_root(context, &path)),
            )
            .to_string();
        Ok(
            ToolResult::ok(truncate(&diff, context.max_output_bytes)).with_metadata(json!({
                "path": path,
                "changed": old != new,
                "added_lines": new.lines().count().saturating_sub(old.lines().count()),
                "removed_lines": old.lines().count().saturating_sub(new.lines().count())
            })),
        )
    }
}

struct SnapshotCreateTool;

#[async_trait]
impl Tool for SnapshotCreateTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "snapshot_create",
            "Copy the workspace (excluding .git, target, node_modules, dist, and .openbad) into .openbad/snapshots/<label>.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "replace": {"type": "boolean"}
            },
            "required": ["label"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("snapshot_create".to_owned()));
        }
        let label = safe_label(&string_arg(&arguments, "label")?)?;
        let directory = snapshot_root(context).join(&label);
        if directory.exists() && !optional_bool(&arguments, "replace").unwrap_or(false) {
            return Err(ToolError::InvalidArgument(format!(
                "snapshot already exists: {label}"
            )));
        }
        if directory.exists() {
            std::fs::remove_dir_all(&directory)?;
        }
        let summary = copy_tree(context.workspace.root(), &directory, context)?;
        Ok(ToolResult::ok(format!(
            "created snapshot {label} ({} files, {} bytes)",
            summary.files, summary.bytes
        ))
        .with_metadata(json!({"label": label, "files": summary.files, "bytes": summary.bytes})))
    }
}

struct SnapshotListTool;

#[async_trait]
impl Tool for SnapshotListTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "snapshot_list",
            "List workspace snapshots.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let root = snapshot_root(context);
        if !root.is_dir() {
            return Ok(ToolResult::ok("no snapshots"));
        }
        let mut names = std::fs::read_dir(root)?
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false))
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        names.sort();
        if names.is_empty() {
            return Ok(ToolResult::ok("no snapshots"));
        }
        Ok(ToolResult::ok(names.join("\n")).with_metadata(json!({"snapshots": names})))
    }
}

struct SnapshotRestoreTool;

#[async_trait]
impl Tool for SnapshotRestoreTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "snapshot_restore",
            "Restore files from a snapshot. Requires confirm=true; extra files are not deleted.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "confirm": {"type": "boolean"}
            },
            "required": ["label"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("snapshot_restore".to_owned()));
        }
        if !optional_bool(&arguments, "confirm").unwrap_or(false) {
            return Err(ToolError::ApprovalRequired(
                "snapshot_restore requires confirm=true".to_owned(),
            ));
        }
        let label = safe_label(&string_arg(&arguments, "label")?)?;
        let directory = snapshot_root(context).join(&label);
        if !directory.is_dir() {
            return Err(ToolError::InvalidArgument(format!(
                "unknown snapshot: {label}"
            )));
        }
        let summary = copy_tree(&directory, context.workspace.root(), context)?;
        Ok(ToolResult::ok(format!(
            "restored snapshot {label} ({} files, {} bytes)",
            summary.files, summary.bytes
        ))
        .with_metadata(json!({"label": label, "files": summary.files})))
    }
}

struct AssertProjectTool;

#[async_trait]
impl Tool for AssertProjectTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "assert_project",
            "Structural assertions: count files by glob, validate JSON/YAML schema fields, or check local Markdown links.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["count", "schema", "links"]},
                "pattern": {"type": "string", "description": "Glob for count"},
                "expected": {"type": "integer", "description": "Expected file count"},
                "min": {"type": "integer"},
                "max": {"type": "integer"},
                "path": {"type": "string"},
                "required": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["action"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = string_arg(&arguments, "action")?;
        match action.as_str() {
            "count" => {
                let pattern = string_arg(&arguments, "pattern")?;
                let matcher = Glob::new(&pattern)
                    .map_err(|error| ToolError::InvalidArgument(format!("invalid glob: {error}")))?
                    .compile_matcher();
                let mut count = 0;
                for entry in WalkBuilder::new(context.workspace.root())
                    .max_depth(Some(12))
                    .build()
                    .filter_map(Result::ok)
                {
                    if !entry.file_type().is_some_and(|kind| kind.is_file())
                        || path_is_excluded(context, entry.path())
                    {
                        continue;
                    }
                    let relative = relative_to_root(context, entry.path());
                    if matcher.is_match(&relative) || matcher.is_match(entry.file_name()) {
                        count += 1;
                    }
                }
                let expected = optional_usize(&arguments, "expected");
                let min = optional_usize(&arguments, "min").unwrap_or(0);
                let max = optional_usize(&arguments, "max").unwrap_or(usize::MAX);
                let success = expected.is_none_or(|expected| count == expected)
                    && count >= min
                    && count <= max;
                Ok(ToolResult::ok(format!(
                    "count {pattern}: {count} ({} )",
                    if success { "pass" } else { "fail" }
                ))
                .with_metadata(json!({"action": "count", "count": count, "success": success})))
            }
            "schema" => {
                let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
                ensure_not_excluded(context, &path)?;
                let content = std::fs::read_to_string(&path)?;
                let value: serde_json::Value = serde_json::from_str(&content)
                    .or_else(|_| {
                        serde_yaml::from_str::<serde_yaml::Value>(&content)
                            .map(|value| serde_json::to_value(value).unwrap_or_default())
                            .map_err(|_| serde_json::Error::io(std::io::Error::other("schema")))
                    })
                    .map_err(|error| ToolError::Failed(format!("parse {path:?}: {error}")))?;
                let required = string_array_arg_or_empty(&arguments, "required")?;
                let missing = required
                    .iter()
                    .filter(|field| value.get(field.as_str()).is_none())
                    .cloned()
                    .collect::<Vec<_>>();
                Ok(ToolResult::ok(format!(
                    "schema {}: {}",
                    path.display(),
                    if missing.is_empty() {
                        "pass".to_owned()
                    } else {
                        format!("missing fields: {}", missing.join(", "))
                    }
                ))
                .with_metadata(
                    json!({"action": "schema", "missing": missing, "success": missing.is_empty()}),
                ))
            }
            "links" => {
                let mut broken = Vec::new();
                let root = context.workspace.root();
                let link = regex::Regex::new(r"\[[^\]]*\]\(([^)]+)\)").expect("link regex");
                for entry in WalkBuilder::new(root)
                    .max_depth(Some(8))
                    .build()
                    .filter_map(Result::ok)
                {
                    if !entry.file_type().is_some_and(|kind| kind.is_file())
                        || path_is_excluded(context, entry.path())
                        || entry
                            .path()
                            .extension()
                            .and_then(|extension| extension.to_str())
                            != Some("md")
                    {
                        continue;
                    }
                    let Ok(content) = std::fs::read_to_string(entry.path()) else {
                        continue;
                    };
                    let directory = entry.path().parent().unwrap_or(root);
                    for capture in link.captures_iter(&content) {
                        let target = capture[1].split('#').next().unwrap_or_default();
                        if target.is_empty()
                            || target.starts_with("http://")
                            || target.starts_with("https://")
                            || target.starts_with("mailto:")
                            || target.starts_with('/')
                        {
                            continue;
                        }
                        let resolved = directory.join(target);
                        if !resolved.exists() {
                            broken.push(format!(
                                "{}: {}",
                                relative_to_root(context, entry.path()),
                                target
                            ));
                        }
                    }
                }
                Ok(ToolResult::ok(if broken.is_empty() {
                    "links: pass".to_owned()
                } else {
                    broken.join("\n")
                })
                .with_metadata(
                    json!({"action": "links", "broken": broken, "success": broken.is_empty()}),
                ))
            }
            other => Err(ToolError::InvalidArgument(format!(
                "unknown assertion action: {other}"
            ))),
        }
    }
}

fn safe_label(label: &str) -> Result<String, ToolError> {
    let label = label.trim();
    if label.is_empty()
        || !label.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
        })
    {
        return Err(ToolError::InvalidArgument(
            "label must contain only letters, digits, dashes, underscores, or dots".to_owned(),
        ));
    }
    Ok(label.to_owned())
}

fn snapshot_root(context: &ToolContext) -> std::path::PathBuf {
    context.workspace.root().join(".openbad").join("snapshots")
}

struct CopySummary {
    files: usize,
    bytes: u64,
}

fn copy_tree(
    source: &Path,
    destination: &Path,
    context: &ToolContext,
) -> Result<CopySummary, ToolError> {
    const MAX_FILES: usize = 10_000;
    const MAX_BYTES: u64 = 256 * 1024 * 1024;
    let mut files = 0;
    let mut bytes = 0_u64;
    for entry in WalkBuilder::new(source)
        .max_depth(Some(20))
        .hidden(false)
        .build()
        .filter_map(Result::ok)
    {
        let path = entry.path();
        let relative = path.strip_prefix(source).unwrap_or(path);
        if relative.as_os_str().is_empty()
            || relative.components().any(|component| {
                matches!(
                    component.as_os_str().to_str(),
                    Some(".git" | "target" | "node_modules" | "dist" | ".openbad")
                )
            })
        {
            continue;
        }
        let target = destination.join(relative);
        if entry.file_type().is_some_and(|kind| kind.is_dir()) {
            std::fs::create_dir_all(&target)?;
            continue;
        }
        if !entry.file_type().is_some_and(|kind| kind.is_file()) {
            continue;
        }
        if files >= MAX_FILES || bytes > MAX_BYTES {
            break;
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::copy(path, &target)?;
        bytes += entry.metadata().map(|metadata| metadata.len()).unwrap_or(0);
        files += 1;
    }
    let _ = context;
    Ok(CopySummary { files, bytes })
}

struct GitCommitTool;

#[async_trait]
impl Tool for GitCommitTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_commit",
            "Stage paths (or all changes) and create a git commit. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "all": {"type": "boolean"}
            },
            "required": ["message"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let message = string_arg(&arguments, "message")?;
        let paths = string_array_arg_or_empty(&arguments, "paths")?;
        let all = optional_bool(&arguments, "all").unwrap_or(false);
        let mut command = String::from("git add -- ");
        if all {
            command = "git add -A".to_owned();
        } else if paths.is_empty() {
            return Err(ToolError::InvalidArgument(
                "provide paths or all=true before committing".to_owned(),
            ));
        } else {
            command.push_str(
                &paths
                    .iter()
                    .map(|path| sh_quote(path))
                    .collect::<Vec<_>>()
                    .join(" "),
            );
        }
        let add = run_tool_shell(context, command).await?;
        if !add.metadata["success"].as_bool().unwrap_or(false) {
            return Ok(add);
        }
        let commit =
            run_tool_shell(context, format!("git commit -m {}", sh_quote(&message))).await?;
        let commit_metadata = commit.metadata.clone();
        let staged_metadata = add.metadata;
        Ok(commit.with_metadata(json!({
            "staged": staged_metadata,
            "commit": commit_metadata,
            "message": message
        })))
    }
}

struct GitBranchTool;

#[async_trait]
impl Tool for GitBranchTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_branch",
            "List branches, or create a branch when name is provided.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        match optional_string(&arguments, "name") {
            Some(name) => run_tool_shell(context, format!("git branch {}", sh_quote(&name))).await,
            None => run_tool_shell(context, "git branch --list".to_owned()).await,
        }
    }
}

struct GitCheckoutTool;

#[async_trait]
impl Tool for GitCheckoutTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_checkout",
            "Switch branches, or create and switch with create=true.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "branch": {"type": "string"},
                "create": {"type": "boolean"}
            },
            "required": ["branch"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let branch = string_arg(&arguments, "branch")?;
        let command = if optional_bool(&arguments, "create").unwrap_or(false) {
            format!("git checkout -b {}", sh_quote(&branch))
        } else {
            format!("git checkout {}", sh_quote(&branch))
        };
        run_tool_shell(context, command).await
    }
}

struct GitLogTool;

#[async_trait]
impl Tool for GitLogTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_log",
            "Show recent commits, optionally filtered by path or message.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "path": {"type": "string"},
                "grep": {"type": "string"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let limit = optional_usize(&arguments, "limit")
            .unwrap_or(20)
            .clamp(1, 100);
        let mut command = format!("git log -n {limit} --oneline");
        if let Some(grep) = optional_string(&arguments, "grep") {
            command.push_str(&format!(" --grep={}", sh_quote(&grep)));
        }
        if let Some(path) = optional_string(&arguments, "path") {
            command.push_str(&format!(" -- {}", sh_quote(&path)));
        }
        run_tool_shell(context, command).await
    }
}

struct GitShowTool;

#[async_trait]
impl Tool for GitShowTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_show",
            "Show a commit or a file at a revision.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "rev": {"type": "string"},
                "path": {"type": "string"}
            },
            "required": ["rev"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let rev = string_arg(&arguments, "rev")?;
        let target = optional_string(&arguments, "path")
            .map(|path| format!("{rev}:{path}"))
            .unwrap_or(rev);
        run_tool_shell(context, format!("git show {}", sh_quote(&target))).await
    }
}

struct GitBlameTool;

#[async_trait]
impl Tool for GitBlameTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_blame",
            "Show line authorship for a file, optionally bounded by line range.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = string_arg(&arguments, "path")?;
        let range = match (
            optional_usize(&arguments, "start_line"),
            optional_usize(&arguments, "end_line"),
        ) {
            (Some(start), Some(end)) => format!("-L {start},{end} "),
            _ => String::new(),
        };
        run_tool_shell(context, format!("git blame {range}-- {}", sh_quote(&path))).await
    }
}

struct GitStashTool;

#[async_trait]
impl Tool for GitStashTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_stash",
            "List, push, pop, apply, or drop a git stash.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "push", "pop", "apply", "drop"]},
                "message": {"type": "string"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "list".to_owned());
        let command = match action.as_str() {
            "list" => "git stash list".to_owned(),
            "push" => format!(
                "git stash push{}",
                optional_string(&arguments, "message")
                    .map(|message| format!(" -m {}", sh_quote(&message)))
                    .unwrap_or_default()
            ),
            "pop" => "git stash pop".to_owned(),
            "apply" => "git stash apply".to_owned(),
            "drop" => "git stash drop".to_owned(),
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown stash action: {other}"
                )));
            }
        };
        run_tool_shell(context, command).await
    }
}

struct GitWorktreeTool;

#[async_trait]
impl Tool for GitWorktreeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_worktree",
            "Manage git worktrees: list, add, or remove.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove"]},
                "path": {"type": "string"},
                "branch": {"type": "string"},
                "create_branch": {"type": "boolean"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "list".to_owned());
        let command = match action.as_str() {
            "list" => "git worktree list".to_owned(),
            "add" => {
                let path = string_arg(&arguments, "path")?;
                let branch = string_arg(&arguments, "branch")?;
                if optional_bool(&arguments, "create_branch").unwrap_or(false) {
                    format!(
                        "git worktree add -b {} {}",
                        sh_quote(&branch),
                        sh_quote(&path)
                    )
                } else {
                    format!("git worktree add {} {}", sh_quote(&path), sh_quote(&branch))
                }
            }
            "remove" => {
                let path = string_arg(&arguments, "path")?;
                format!("git worktree remove {}", sh_quote(&path))
            }
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown worktree action: {other}"
                )));
            }
        };
        run_tool_shell(context, command).await
    }
}

struct RepoHealthTool;

#[async_trait]
impl Tool for RepoHealthTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "repo_health",
            "Report branch, ahead/behind, modified, staged, and untracked state for the workspace repository.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let status =
            run_tool_shell(context, "git status --porcelain=v1 --branch".to_owned()).await?;
        let divergence = run_tool_shell(
            context,
            "git rev-list --left-right --count @{upstream}...HEAD".to_owned(),
        )
        .await?;
        Ok(
            ToolResult::ok(format!("{}\n{}", status.content, divergence.content)).with_metadata(
                json!({
                    "status": status.metadata,
                    "divergence": divergence.metadata
                }),
            ),
        )
    }
}

struct GitCommitContextTool;

#[async_trait]
impl Tool for GitCommitContextTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_commit_context",
            "Collect branch, recent commit, and staged/unstaged stats to draft a conventional commit message.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let branch = run_tool_shell(context, "git branch --show-current".to_owned()).await?;
        let last = run_tool_shell(context, "git log -1 --oneline".to_owned()).await?;
        let unstaged = run_tool_shell(context, "git diff --stat".to_owned()).await?;
        let staged = run_tool_shell(context, "git diff --cached --stat".to_owned()).await?;
        Ok(ToolResult::ok(format!(
            "branch:\n{}\nlast commit:\n{}\nunstaged:\n{}\nstaged:\n{}",
            branch.content, last.content, unstaged.content, staged.content
        )))
    }
}

fn github_slug(arguments: &Value) -> Result<Option<String>, ToolError> {
    if let Some(repository) = optional_string(arguments, "repository") {
        let repository = repository.trim();
        let mut parts = repository.split('/');
        let owner = parts.next().unwrap_or_default();
        let name = parts.next().unwrap_or_default();
        if owner.is_empty() || name.is_empty() || parts.next().is_some() {
            return Err(ToolError::InvalidArgument(format!(
                "repository must be owner/name: {repository}"
            )));
        }
        return Ok(Some(repository.to_owned()));
    }
    match (
        optional_string(arguments, "owner"),
        optional_string(arguments, "repo"),
    ) {
        (Some(owner), Some(repo)) => Ok(Some(format!("{owner}/{repo}"))),
        (None, None) => Ok(None),
        _ => Err(ToolError::InvalidArgument(
            "provide both owner and repo, or a repository slug".to_owned(),
        )),
    }
}

fn github_repo_flag(arguments: &Value) -> Result<String, ToolError> {
    Ok(github_slug(arguments)?
        .map(|repository| format!(" --repo {}", sh_quote(&repository)))
        .unwrap_or_default())
}

fn github_api_repo(arguments: &Value) -> Result<String, ToolError> {
    Ok(github_slug(arguments)?.unwrap_or_else(|| "{owner}/{repo}".to_owned()))
}

fn github_target(arguments: &Value) -> Result<String, ToolError> {
    github_slug(arguments)?
        .map(|repository| sh_quote(&repository))
        .ok_or_else(|| {
            ToolError::InvalidArgument(
                "this action requires repository=owner/name or owner and repo".to_owned(),
            )
        })
}

fn push_flag(command: &mut String, flag: &str, value: Option<String>) {
    if let Some(value) = value {
        command.push_str(&format!(" {flag} {}", sh_quote(&value)));
    }
}

fn push_bool(command: &mut String, flag: &str, enabled: bool) {
    if enabled {
        command.push(' ');
        command.push_str(flag);
    }
}

fn push_values(command: &mut String, flag: &str, values: &[String]) {
    for value in values {
        command.push_str(&format!(" {flag} {}", sh_quote(value)));
    }
}

fn github_number(arguments: &Value) -> Result<String, ToolError> {
    if let Some(number) = arguments.get("number").and_then(Value::as_u64) {
        return Ok(number.to_string());
    }
    if let Some(number) = arguments.get("number").and_then(Value::as_str) {
        return Ok(number.to_owned());
    }
    Err(ToolError::InvalidArgument(
        "missing number argument".to_owned(),
    ))
}

fn github_relative_path(raw: &str) -> Result<String, ToolError> {
    let path = Path::new(raw);
    if path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                std::path::Component::ParentDir
                    | std::path::Component::RootDir
                    | std::path::Component::Prefix(_)
            )
        })
    {
        return Err(ToolError::InvalidArgument(format!(
            "path must stay inside the workspace: {raw}"
        )));
    }
    Ok(raw.to_owned())
}

fn github_file_paths(arguments: &Value) -> Result<Vec<String>, ToolError> {
    string_array_arg_or_empty(arguments, "files")?
        .iter()
        .map(|path| github_relative_path(path))
        .collect()
}

fn github_json_flag(arguments: &Value, default: &str) -> String {
    let fields = arguments
        .get("json")
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(",")
        })
        .unwrap_or_default();
    if fields.is_empty() {
        default.to_owned()
    } else {
        fields
    }
}

fn github_api_query(arguments: &Value) -> String {
    let mut query = String::new();
    if optional_bool(arguments, "paginate").unwrap_or(false) {
        query.push_str(" --paginate");
    }
    if optional_bool(arguments, "slurp").unwrap_or(false) {
        query.push_str(" --slurp");
    }
    push_flag(
        &mut query,
        "--jq",
        optional_string(arguments, "jq").filter(|value| !value.trim().is_empty()),
    );
    query
}

fn github_api_body_command(method: &str, endpoint: &str, body: &Value) -> String {
    let payload = serde_json::to_string(body).unwrap_or_else(|_| "{}".to_owned());
    format!(
        "printf '%s' {} | gh api --method {} {} --input -",
        sh_quote(&payload),
        method.to_ascii_uppercase(),
        sh_quote(endpoint)
    )
}

fn github_api_fields(arguments: &Value) -> Result<String, ToolError> {
    let Some(fields) = arguments.get("fields").and_then(Value::as_object) else {
        return Ok(String::new());
    };
    let mut command = String::new();
    for (key, value) in fields {
        match value {
            Value::String(text) => {
                command.push_str(&format!(" -f {}={}", sh_quote(key), sh_quote(text)));
            }
            Value::Bool(flag) => {
                command.push_str(&format!(" -F {}={flag}", sh_quote(key)));
            }
            Value::Number(number) => {
                command.push_str(&format!(" -F {}={number}", sh_quote(key)));
            }
            Value::Array(values) => {
                for value in values {
                    let text = value
                        .as_str()
                        .map_or_else(|| value.to_string(), str::to_owned);
                    command.push_str(&format!(" -f {}[]={}", sh_quote(key), sh_quote(&text)));
                }
            }
            Value::Null => {}
            other => {
                command.push_str(&format!(
                    " -f {}={}",
                    sh_quote(key),
                    sh_quote(&other.to_string())
                ));
            }
        }
    }
    Ok(command)
}

fn github_api_command(arguments: &Value) -> Result<String, ToolError> {
    let endpoint = string_arg(arguments, "endpoint")?;
    let has_body = arguments.get("body").is_some_and(Value::is_object);
    let method = optional_string(arguments, "method")
        .unwrap_or_else(|| if has_body { "POST" } else { "GET" }.to_owned())
        .to_ascii_uppercase();
    if method == "DELETE" {
        github_require_confirm(arguments, "github api delete")?;
    }
    if let Some(body) = arguments.get("body").filter(|body| body.is_object()) {
        let mut command = github_api_body_command(&method, &endpoint, body);
        command.push_str(&github_api_query(arguments));
        return Ok(command);
    }
    let mut command = format!(
        "gh api --method {method} {}{}",
        sh_quote(&endpoint),
        github_api_fields(arguments)?
    );
    command.push_str(&github_api_query(arguments));
    Ok(command)
}

fn github_require_confirm(arguments: &Value, action: &str) -> Result<(), ToolError> {
    if optional_bool(arguments, "confirm").unwrap_or(false) {
        Ok(())
    } else {
        Err(ToolError::ApprovalRequired(format!(
            "{action} requires confirm=true after checking the blast radius"
        )))
    }
}

fn github_branch_encoded(branch: &str) -> String {
    branch
        .chars()
        .map(|character| match character {
            '/' => "%2F".to_owned(),
            '#' => "%23".to_owned(),
            '?' => "%3F".to_owned(),
            '%' => "%25".to_owned(),
            ' ' => "%20".to_owned(),
            other => other.to_string(),
        })
        .collect()
}

fn github_repo_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let command = match action {
        "view" => format!(
            "gh repo view{flag} --json {}",
            github_json_flag(
                arguments,
                "nameWithOwner,description,defaultBranchRef,url,visibility,isPrivate,isFork,stargazerCount,forkCount,updatedAt,repositoryTopics"
            )
        ),
        "list" => {
            let mut command = format!(
                "gh repo list{} --limit {} --json {}",
                optional_string(arguments, "owner")
                    .map(|owner| format!(" {}", sh_quote(&owner)))
                    .unwrap_or_default(),
                optional_usize(arguments, "limit")
                    .unwrap_or(30)
                    .clamp(1, 100),
                github_json_flag(
                    arguments,
                    "nameWithOwner,description,visibility,isPrivate,updatedAt,stargazerCount"
                )
            );
            push_bool(
                &mut command,
                "--fork",
                optional_bool(arguments, "forks").unwrap_or(false),
            );
            command
        }
        "create" => {
            let name = string_arg(arguments, "name")?;
            let mut command = format!("gh repo create {}", sh_quote(&name));
            push_bool(
                &mut command,
                "--private",
                optional_bool(arguments, "private").unwrap_or(true),
            );
            push_bool(
                &mut command,
                "--public",
                optional_bool(arguments, "private") == Some(false),
            );
            push_flag(
                &mut command,
                "--description",
                optional_string(arguments, "description"),
            );
            command
        }
        "fork" => {
            let target = github_target(arguments)?;
            format!("gh repo fork {target} --clone=false")
        }
        "edit" => {
            let mut command = format!("gh repo edit{flag}");
            push_flag(
                &mut command,
                "--description",
                optional_string(arguments, "description"),
            );
            push_flag(
                &mut command,
                "--visibility",
                optional_string(arguments, "visibility"),
            );
            push_values(
                &mut command,
                "--add-topic",
                &string_array_arg_or_empty(arguments, "add_topics")?,
            );
            push_values(
                &mut command,
                "--remove-topic",
                &string_array_arg_or_empty(arguments, "remove_topics")?,
            );
            command
        }
        "delete" => {
            github_require_confirm(arguments, "github repo delete")?;
            format!("gh repo delete {} --yes", github_target(arguments)?)
        }
        "archive" => format!("gh repo archive {} --yes", github_target(arguments)?),
        "unarchive" => format!("gh repo unarchive {} --yes", github_target(arguments)?),
        "rename" => format!(
            "gh repo rename {} --repo {} --yes",
            sh_quote(&string_arg(arguments, "new_name")?),
            github_target(arguments)?
        ),
        "clone" => {
            let target = github_target(arguments)?;
            let mut command = format!("gh repo clone {target}");
            push_flag(&mut command, "", optional_string(arguments, "directory"));
            command
        }
        "sync" => {
            let mut command = format!("gh repo sync{flag}");
            push_flag(
                &mut command,
                "--source",
                optional_string(arguments, "source"),
            );
            command
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown repo action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_branch_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let repo = github_api_repo(arguments)?;
    let branch = optional_string(arguments, "branch");
    let command = match action {
        "list" => {
            let limit = optional_usize(arguments, "limit")
                .unwrap_or(30)
                .clamp(1, 100);
            format!(
                "gh api {} --jq {}",
                sh_quote(&format!("repos/{repo}/branches?per_page={limit}")),
                sh_quote(".[] | {name: .name, protected: .protected, sha: .commit.sha}")
            )
        }
        "get" => {
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            format!(
                "gh api {}",
                sh_quote(&format!(
                    "repos/{repo}/branches/{}",
                    github_branch_encoded(&branch)
                ))
            )
        }
        "create" => {
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            if let Some(sha) = optional_string(arguments, "sha") {
                format!(
                    "gh api --method POST {} -f ref={} -f sha={}",
                    sh_quote(&format!("repos/{repo}/git/refs")),
                    sh_quote(&format!("refs/heads/{branch}")),
                    sh_quote(&sha)
                )
            } else if let Some(base) = optional_string(arguments, "base") {
                format!(
                    "sha=$(gh api {} --jq .object.sha) && gh api --method POST {} -f ref={} -f sha=\"$sha\"",
                    sh_quote(&format!("repos/{repo}/git/ref/heads/{base}")),
                    sh_quote(&format!("repos/{repo}/git/refs")),
                    sh_quote(&format!("refs/heads/{branch}"))
                )
            } else {
                return Err(ToolError::InvalidArgument(
                    "branch create requires sha or base".to_owned(),
                ));
            }
        }
        "delete" => {
            github_require_confirm(arguments, "github branch delete")?;
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            format!(
                "gh api --method DELETE {}",
                sh_quote(&format!("repos/{repo}/git/refs/heads/{branch}"))
            )
        }
        "protection" => {
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            format!(
                "gh api {}",
                sh_quote(&format!(
                    "repos/{repo}/branches/{}/protection",
                    github_branch_encoded(&branch)
                ))
            )
        }
        "protect" => {
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            let body = arguments
                .get("body")
                .filter(|body| body.is_object())
                .ok_or_else(|| {
                    ToolError::InvalidArgument(
                        "branch protect requires a body object with the protection settings"
                            .to_owned(),
                    )
                })?;
            github_api_body_command(
                "PUT",
                &format!(
                    "repos/{repo}/branches/{}/protection",
                    github_branch_encoded(&branch)
                ),
                body,
            )
        }
        "rename" => {
            let branch = branch
                .ok_or_else(|| ToolError::InvalidArgument("branch is required".to_owned()))?;
            let new_name = string_arg(arguments, "new_name")?;
            format!(
                "gh api --method POST {} -f new_name={}",
                sh_quote(&format!(
                    "repos/{repo}/branches/{}/rename",
                    github_branch_encoded(&branch)
                )),
                sh_quote(&new_name)
            )
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown branch action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_issue_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let command = match action {
        "list" => {
            let mut command = format!(
                "gh issue list{flag} --limit {} --state {} --json {}",
                optional_usize(arguments, "limit")
                    .unwrap_or(20)
                    .clamp(1, 100),
                optional_string(arguments, "state").unwrap_or_else(|| "open".to_owned()),
                github_json_flag(
                    arguments,
                    "number,title,state,labels,assignees,author,updatedAt,url"
                )
            );
            push_flag(
                &mut command,
                "--search",
                optional_string(arguments, "query"),
            );
            push_values(
                &mut command,
                "--label",
                &string_array_arg_or_empty(arguments, "labels")?,
            );
            push_flag(
                &mut command,
                "--assignee",
                optional_string(arguments, "assignee"),
            );
            command
        }
        "get" => format!(
            "gh issue view {}{flag} --json {}",
            github_number(arguments)?,
            github_json_flag(
                arguments,
                "number,title,body,state,labels,assignees,milestone,comments,author,createdAt,updatedAt,url"
            )
        ),
        "create" => {
            let mut command = format!(
                "gh issue create{flag} --title {} --body {}",
                sh_quote(&string_arg(arguments, "title")?),
                sh_quote(&optional_string(arguments, "body").unwrap_or_default())
            );
            push_values(
                &mut command,
                "--label",
                &string_array_arg_or_empty(arguments, "labels")?,
            );
            push_values(
                &mut command,
                "--assignee",
                &string_array_arg_or_empty(arguments, "assignees")?,
            );
            push_flag(
                &mut command,
                "--milestone",
                optional_string(arguments, "milestone"),
            );
            command
        }
        "update" => {
            let number = github_number(arguments)?;
            let state = optional_string(arguments, "state");
            if state.as_deref() == Some("closed") {
                github_require_confirm(arguments, "github issue close")?;
            }
            if let Some(state) = state.as_deref()
                && !matches!(state, "open" | "closed")
            {
                return Err(ToolError::InvalidArgument(format!(
                    "state must be open or closed: {state}"
                )));
            }
            let mut edits = String::new();
            push_flag(&mut edits, "--title", optional_string(arguments, "title"));
            push_flag(&mut edits, "--body", optional_string(arguments, "body"));
            push_values(
                &mut edits,
                "--add-label",
                &string_array_arg_or_empty(arguments, "add_labels")?,
            );
            push_values(
                &mut edits,
                "--remove-label",
                &string_array_arg_or_empty(arguments, "remove_labels")?,
            );
            push_values(
                &mut edits,
                "--add-assignee",
                &string_array_arg_or_empty(arguments, "add_assignees")?,
            );
            push_values(
                &mut edits,
                "--remove-assignee",
                &string_array_arg_or_empty(arguments, "remove_assignees")?,
            );
            push_flag(
                &mut edits,
                "--milestone",
                optional_string(arguments, "milestone"),
            );
            let state_command = match state.as_deref() {
                Some("closed") => Some(format!("gh issue close {number}{flag}")),
                Some("open") => Some(format!("gh issue reopen {number}{flag}")),
                _ => None,
            };
            match (edits.trim().is_empty(), state_command) {
                (true, Some(command)) => command,
                (true, None) => {
                    return Err(ToolError::InvalidArgument(
                        "issue update requires a title, body, labels, assignees, milestone, or state"
                            .to_owned(),
                    ));
                }
                (false, Some(state_command)) => {
                    format!("gh issue edit {number}{flag}{edits} && {state_command}")
                }
                (false, None) => format!("gh issue edit {number}{flag}{edits}"),
            }
        }
        "comment" => format!(
            "gh issue comment {}{flag} --body {}",
            github_number(arguments)?,
            sh_quote(&string_arg(arguments, "body")?)
        ),
        "close" => {
            github_require_confirm(arguments, "github issue close")?;
            let mut command = format!("gh issue close {}{flag}", github_number(arguments)?);
            push_flag(
                &mut command,
                "--reason",
                optional_string(arguments, "reason"),
            );
            command
        }
        "reopen" => format!("gh issue reopen {}{flag}", github_number(arguments)?),
        "delete" => {
            github_require_confirm(arguments, "github issue delete")?;
            format!("gh issue delete {}{flag} --yes", github_number(arguments)?)
        }
        "lock" => {
            let mut command = format!("gh issue lock {}{flag}", github_number(arguments)?);
            push_flag(
                &mut command,
                "--reason",
                optional_string(arguments, "reason"),
            );
            command
        }
        "unlock" => format!("gh issue unlock {}{flag}", github_number(arguments)?),
        "pin" => format!("gh issue pin {}{flag}", github_number(arguments)?),
        "unpin" => format!("gh issue unpin {}{flag}", github_number(arguments)?),
        "comments" => format!(
            "gh issue view {}{flag} --comments",
            github_number(arguments)?
        ),
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown issue action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_label_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let command = match action {
        "list" => format!(
            "gh label list{flag} --limit {} --json name,color,description",
            optional_usize(arguments, "limit")
                .unwrap_or(100)
                .clamp(1, 100)
        ),
        "create" => {
            let mut command = format!(
                "gh label create {}{flag} --color {}",
                sh_quote(&string_arg(arguments, "name")?),
                sh_quote(
                    &optional_string(arguments, "color").unwrap_or_else(|| "ededed".to_owned())
                )
            );
            push_flag(
                &mut command,
                "--description",
                optional_string(arguments, "description"),
            );
            push_bool(
                &mut command,
                "--force",
                optional_bool(arguments, "force").unwrap_or(false),
            );
            command
        }
        "edit" => {
            let mut command = format!(
                "gh label edit {}{flag}",
                sh_quote(&string_arg(arguments, "name")?)
            );
            push_flag(
                &mut command,
                "--new-name",
                optional_string(arguments, "new_name"),
            );
            push_flag(&mut command, "--color", optional_string(arguments, "color"));
            push_flag(
                &mut command,
                "--description",
                optional_string(arguments, "description"),
            );
            command
        }
        "delete" => {
            github_require_confirm(arguments, "github label delete")?;
            format!(
                "gh label delete {}{flag} --yes",
                sh_quote(&string_arg(arguments, "name")?)
            )
        }
        "clone" => {
            let mut command = format!(
                "gh label clone {}{flag}",
                sh_quote(&string_arg(arguments, "source")?)
            );
            push_bool(
                &mut command,
                "--force",
                optional_bool(arguments, "force").unwrap_or(false),
            );
            command
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown label action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_milestone_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let repo = github_api_repo(arguments)?;
    let command = match action {
        "list" => format!(
            "gh api {} --jq {}",
            sh_quote(&format!(
                "repos/{repo}/milestones?state={}&per_page={}",
                optional_string(arguments, "state").unwrap_or_else(|| "open".to_owned()),
                optional_usize(arguments, "limit")
                    .unwrap_or(30)
                    .clamp(1, 100)
            )),
            sh_quote(
                ".[] | {number: .number, title: .title, state: .state, due_on: .due_on, open_issues: .open_issues, closed_issues: .closed_issues}"
            )
        ),
        "create" => {
            let mut command = format!(
                "gh api --method POST {} -f title={}",
                sh_quote(&format!("repos/{repo}/milestones")),
                sh_quote(&string_arg(arguments, "title")?)
            );
            push_flag(
                &mut command,
                "-f",
                optional_string(arguments, "description")
                    .map(|value| format!("description={value}")),
            );
            push_flag(
                &mut command,
                "-f",
                optional_string(arguments, "due_on").map(|value| format!("due_on={value}")),
            );
            command
        }
        "update" | "close" | "open" => {
            let number = github_number(arguments)?;
            let mut command = format!(
                "gh api --method PATCH {}",
                sh_quote(&format!("repos/{repo}/milestones/{number}"))
            );
            let state = match action {
                "close" => Some("closed".to_owned()),
                "open" => Some("open".to_owned()),
                _ => optional_string(arguments, "state"),
            };
            push_flag(
                &mut command,
                "-f",
                state.map(|value| format!("state={value}")),
            );
            push_flag(
                &mut command,
                "-f",
                optional_string(arguments, "title").map(|value| format!("title={value}")),
            );
            push_flag(
                &mut command,
                "-f",
                optional_string(arguments, "description")
                    .map(|value| format!("description={value}")),
            );
            push_flag(
                &mut command,
                "-f",
                optional_string(arguments, "due_on").map(|value| format!("due_on={value}")),
            );
            command
        }
        "delete" => {
            github_require_confirm(arguments, "github milestone delete")?;
            format!(
                "gh api --method DELETE {}",
                sh_quote(&format!(
                    "repos/{repo}/milestones/{}",
                    github_number(arguments)?
                ))
            )
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown milestone action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_pr_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let command = match action {
        "list" => {
            let mut command = format!(
                "gh pr list{flag} --limit {} --state {} --json {}",
                optional_usize(arguments, "limit")
                    .unwrap_or(20)
                    .clamp(1, 100),
                optional_string(arguments, "state").unwrap_or_else(|| "open".to_owned()),
                github_json_flag(
                    arguments,
                    "number,title,state,isDraft,headRefName,baseRefName,author,updatedAt,url"
                )
            );
            push_flag(
                &mut command,
                "--search",
                optional_string(arguments, "query"),
            );
            push_flag(&mut command, "--base", optional_string(arguments, "base"));
            push_flag(&mut command, "--head", optional_string(arguments, "head"));
            command
        }
        "view" => format!(
            "gh pr view {}{flag} --json {}",
            github_number(arguments)?,
            github_json_flag(
                arguments,
                "number,title,body,state,isDraft,mergeable,mergeStateStatus,headRefName,baseRefName,author,labels,assignees,reviewRequests,reviews,files,commits,statusCheckRollup,url,createdAt,updatedAt"
            )
        ),
        "diff" => {
            let mut command = format!("gh pr diff {}{flag}", github_number(arguments)?);
            push_bool(
                &mut command,
                "--name-only",
                optional_bool(arguments, "name_only").unwrap_or(false),
            );
            command
        }
        "files" => format!(
            "gh pr view {}{flag} --json files",
            github_number(arguments)?
        ),
        "commits" => format!(
            "gh pr view {}{flag} --json commits",
            github_number(arguments)?
        ),
        "comments" => format!("gh pr view {}{flag} --comments", github_number(arguments)?),
        "create" => {
            let mut command = format!(
                "gh pr create{flag} --title {} --body {}",
                sh_quote(&string_arg(arguments, "title")?),
                sh_quote(&optional_string(arguments, "body").unwrap_or_default())
            );
            push_flag(&mut command, "--base", optional_string(arguments, "base"));
            push_flag(&mut command, "--head", optional_string(arguments, "head"));
            push_bool(
                &mut command,
                "--draft",
                optional_bool(arguments, "draft").unwrap_or(false),
            );
            push_values(
                &mut command,
                "--label",
                &string_array_arg_or_empty(arguments, "labels")?,
            );
            push_values(
                &mut command,
                "--assignee",
                &string_array_arg_or_empty(arguments, "assignees")?,
            );
            push_values(
                &mut command,
                "--reviewer",
                &string_array_arg_or_empty(arguments, "reviewers")?,
            );
            push_flag(
                &mut command,
                "--milestone",
                optional_string(arguments, "milestone"),
            );
            command
        }
        "update" => {
            let mut command = format!("gh pr edit {}{flag}", github_number(arguments)?);
            push_flag(&mut command, "--title", optional_string(arguments, "title"));
            push_flag(&mut command, "--body", optional_string(arguments, "body"));
            push_flag(&mut command, "--base", optional_string(arguments, "base"));
            push_values(
                &mut command,
                "--add-label",
                &string_array_arg_or_empty(arguments, "add_labels")?,
            );
            push_values(
                &mut command,
                "--remove-label",
                &string_array_arg_or_empty(arguments, "remove_labels")?,
            );
            push_values(
                &mut command,
                "--add-assignee",
                &string_array_arg_or_empty(arguments, "add_assignees")?,
            );
            push_values(
                &mut command,
                "--remove-assignee",
                &string_array_arg_or_empty(arguments, "remove_assignees")?,
            );
            push_values(
                &mut command,
                "--add-reviewer",
                &string_array_arg_or_empty(arguments, "add_reviewers")?,
            );
            push_values(
                &mut command,
                "--remove-reviewer",
                &string_array_arg_or_empty(arguments, "remove_reviewers")?,
            );
            command
        }
        "comment" => format!(
            "gh pr comment {}{flag} --body {}",
            github_number(arguments)?,
            sh_quote(&string_arg(arguments, "body")?)
        ),
        "review" => {
            let event = optional_string(arguments, "event").unwrap_or_else(|| "comment".to_owned());
            if matches!(event.as_str(), "approve" | "request-changes") {
                github_require_confirm(arguments, &format!("github pr review {event}"))?;
            }
            let mut command = format!("gh pr review {}{flag}", github_number(arguments)?);
            command.push_str(match event.as_str() {
                "approve" => " --approve",
                "request-changes" => " --request-changes",
                "comment" => " --comment",
                other => {
                    return Err(ToolError::InvalidArgument(format!(
                        "unknown review event: {other}"
                    )));
                }
            });
            push_flag(&mut command, "--body", optional_string(arguments, "body"));
            command
        }
        "merge" => {
            github_require_confirm(arguments, "github pr merge")?;
            let mut command = format!("gh pr merge {}{flag}", github_number(arguments)?);
            let method =
                optional_string(arguments, "merge_method").unwrap_or_else(|| "squash".to_owned());
            command.push_str(match method.as_str() {
                "squash" => " --squash",
                "merge" => " --merge",
                "rebase" => " --rebase",
                other => {
                    return Err(ToolError::InvalidArgument(format!(
                        "unknown merge method: {other}"
                    )));
                }
            });
            push_bool(
                &mut command,
                "--delete-branch",
                optional_bool(arguments, "delete_branch").unwrap_or(false),
            );
            push_bool(
                &mut command,
                "--auto",
                optional_bool(arguments, "auto").unwrap_or(false),
            );
            push_flag(
                &mut command,
                "--subject",
                optional_string(arguments, "title"),
            );
            push_flag(&mut command, "--body", optional_string(arguments, "body"));
            command
        }
        "close" => {
            github_require_confirm(arguments, "github pr close")?;
            format!("gh pr close {}{flag}", github_number(arguments)?)
        }
        "reopen" => format!("gh pr reopen {}{flag}", github_number(arguments)?),
        "ready" => format!("gh pr ready {}{flag}", github_number(arguments)?),
        "checks" => {
            let mut command = format!("gh pr checks {}{flag}", github_number(arguments)?);
            push_bool(
                &mut command,
                "--watch",
                optional_bool(arguments, "watch").unwrap_or(false),
            );
            command
        }
        "checkout" => format!("gh pr checkout {}{flag}", github_number(arguments)?),
        "update-branch" => format!("gh pr update-branch {}{flag}", github_number(arguments)?),
        "lock" => format!("gh pr lock {}{flag}", github_number(arguments)?),
        "unlock" => format!("gh pr unlock {}{flag}", github_number(arguments)?),
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown pr action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_workflow_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let command = match action {
        "list" => format!(
            "gh workflow list{flag} --all --limit {} --json id,name,path,state",
            optional_usize(arguments, "limit")
                .unwrap_or(50)
                .clamp(1, 100)
        ),
        "view" => {
            let workflow = string_arg(arguments, "workflow")?;
            let mut command = format!("gh workflow view {}{flag}", sh_quote(&workflow));
            push_bool(
                &mut command,
                "--yaml",
                optional_bool(arguments, "yaml").unwrap_or(false),
            );
            command
        }
        "dispatch" => {
            let workflow = string_arg(arguments, "workflow")?;
            let mut command = format!("gh workflow run {}{flag}", sh_quote(&workflow));
            push_flag(&mut command, "--ref", optional_string(arguments, "ref"));
            if let Some(inputs) = arguments.get("inputs").and_then(Value::as_object) {
                for (key, value) in inputs {
                    let text = value
                        .as_str()
                        .map_or_else(|| value.to_string(), str::to_owned);
                    command.push_str(&format!(" -f {}={}", sh_quote(key), sh_quote(&text)));
                }
            }
            command
        }
        "enable" => format!(
            "gh workflow enable {}{flag}",
            sh_quote(&string_arg(arguments, "workflow")?)
        ),
        "disable" => format!(
            "gh workflow disable {}{flag}",
            sh_quote(&string_arg(arguments, "workflow")?)
        ),
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown workflow action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_run_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let run_id = || string_arg(arguments, "run_id");
    let command = match action {
        "list" => {
            let mut command = format!(
                "gh run list{flag} --limit {} --json {}",
                optional_usize(arguments, "limit")
                    .unwrap_or(20)
                    .clamp(1, 100),
                github_json_flag(
                    arguments,
                    "databaseId,name,workflowName,status,conclusion,headBranch,event,createdAt,url"
                )
            );
            push_flag(
                &mut command,
                "--workflow",
                optional_string(arguments, "workflow"),
            );
            push_flag(
                &mut command,
                "--branch",
                optional_string(arguments, "branch"),
            );
            push_flag(
                &mut command,
                "--status",
                optional_string(arguments, "status"),
            );
            command
        }
        "view" => format!(
            "gh run view {}{flag} --json {}",
            run_id()?,
            github_json_flag(
                arguments,
                "databaseId,name,workflowName,status,conclusion,headBranch,headSha,event,createdAt,updatedAt,jobs,url"
            )
        ),
        "logs" => {
            let mut command = format!("gh run view {}{flag}", run_id()?);
            push_bool(
                &mut command,
                "--log-failed",
                optional_bool(arguments, "failed_only").unwrap_or(false),
            );
            if !optional_bool(arguments, "failed_only").unwrap_or(false) {
                command.push_str(" --log");
            }
            command
        }
        "artifacts" => format!(
            "gh api {} --jq {}",
            sh_quote(&format!(
                "repos/{}/actions/runs/{}/artifacts",
                github_api_repo(arguments)?,
                run_id()?
            )),
            sh_quote(
                ".artifacts[] | {id: .id, name: .name, size_in_bytes: .size_in_bytes, expired: .expired}"
            )
        ),
        "download" => {
            let mut command = format!("gh run download {}{flag}", run_id()?);
            push_flag(
                &mut command,
                "--name",
                optional_string(arguments, "artifact"),
            );
            if let Some(directory) = optional_string(arguments, "directory") {
                command.push_str(&format!(
                    " --dir {}",
                    sh_quote(&github_relative_path(&directory)?)
                ));
            }
            command
        }
        "cancel" => {
            github_require_confirm(arguments, "github run cancel")?;
            format!("gh run cancel {}{flag}", run_id()?)
        }
        "rerun" => {
            let mut command = format!("gh run rerun {}{flag}", run_id()?);
            push_bool(
                &mut command,
                "--failed",
                optional_bool(arguments, "failed_only").unwrap_or(false),
            );
            command
        }
        "delete" => {
            github_require_confirm(arguments, "github run delete")?;
            format!("gh run delete {}{flag}", run_id()?)
        }
        "watch" => format!(
            "gh run watch {}{flag} --exit-status --interval 10",
            run_id()?
        ),
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown run action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_release_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let flag = github_repo_flag(arguments)?;
    let tag = || string_arg(arguments, "tag");
    let command = match action {
        "list" => format!(
            "gh release list{flag} --limit {} --json tagName,name,isDraft,isPrerelease,publishedAt",
            optional_usize(arguments, "limit")
                .unwrap_or(30)
                .clamp(1, 100)
        ),
        "view" => format!(
            "gh release view {}{flag} --json {}",
            sh_quote(&tag()?),
            github_json_flag(
                arguments,
                "tagName,name,body,isDraft,isPrerelease,publishedAt,url,author,assets"
            )
        ),
        "create" => {
            let mut command = format!("gh release create {}{flag}", sh_quote(&tag()?));
            push_flag(&mut command, "--title", optional_string(arguments, "title"));
            push_flag(&mut command, "--notes", optional_string(arguments, "notes"));
            push_bool(
                &mut command,
                "--generate-notes",
                optional_bool(arguments, "generate_notes").unwrap_or(false),
            );
            push_bool(
                &mut command,
                "--draft",
                optional_bool(arguments, "draft").unwrap_or(false),
            );
            push_bool(
                &mut command,
                "--prerelease",
                optional_bool(arguments, "prerelease").unwrap_or(false),
            );
            push_bool(
                &mut command,
                "--latest",
                optional_bool(arguments, "latest").unwrap_or(false),
            );
            push_flag(
                &mut command,
                "--target",
                optional_string(arguments, "target"),
            );
            for file in github_file_paths(arguments)? {
                command.push(' ');
                command.push_str(&sh_quote(&file));
            }
            command
        }
        "edit" => {
            let mut command = format!("gh release edit {}{flag}", sh_quote(&tag()?));
            push_flag(&mut command, "--title", optional_string(arguments, "title"));
            push_flag(&mut command, "--notes", optional_string(arguments, "notes"));
            if let Some(draft) = optional_bool(arguments, "draft") {
                command.push_str(if draft { " --draft" } else { " --draft=false" });
            }
            if let Some(prerelease) = optional_bool(arguments, "prerelease") {
                command.push_str(if prerelease {
                    " --prerelease"
                } else {
                    " --prerelease=false"
                });
            }
            push_flag(
                &mut command,
                "--target",
                optional_string(arguments, "target"),
            );
            command
        }
        "upload" => {
            let mut command = format!("gh release upload {}{flag}", sh_quote(&tag()?));
            let files = github_file_paths(arguments)?;
            if files.is_empty() {
                return Err(ToolError::InvalidArgument(
                    "release upload requires files".to_owned(),
                ));
            }
            for file in files {
                command.push(' ');
                command.push_str(&sh_quote(&file));
            }
            push_bool(
                &mut command,
                "--clobber",
                optional_bool(arguments, "clobber").unwrap_or(false),
            );
            command
        }
        "delete" => {
            github_require_confirm(arguments, "github release delete")?;
            let mut command = format!("gh release delete {}{flag} --yes", sh_quote(&tag()?));
            push_bool(
                &mut command,
                "--cleanup-tag",
                optional_bool(arguments, "cleanup_tag").unwrap_or(false),
            );
            command
        }
        "download" => {
            let mut command = format!("gh release download {}{flag}", sh_quote(&tag()?));
            push_flag(
                &mut command,
                "--pattern",
                optional_string(arguments, "pattern"),
            );
            if let Some(directory) = optional_string(arguments, "directory") {
                command.push_str(&format!(
                    " --dir {}",
                    sh_quote(&github_relative_path(&directory)?)
                ));
            }
            command
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown release action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_pages_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let repo = github_api_repo(arguments)?;
    let command = match action {
        "status" | "site" => format!(
            "gh api {} --jq {}",
            sh_quote(&format!("repos/{repo}/pages")),
            sh_quote(
                "{html_url: .html_url, status: .status, cname: .cname, branch: .source.branch, path: .source.path, build_type: .build_type}"
            )
        ),
        "builds" => format!(
            "gh api {} --jq {}",
            sh_quote(&format!("repos/{repo}/pages/builds?per_page=20")),
            sh_quote(
                ".[] | {status: .status, url: .url, created_at: .created_at, updated_at: .updated_at, error: .error.message}"
            )
        ),
        "build" | "request-build" => {
            github_api_body_command("POST", &format!("repos/{repo}/pages/builds"), &json!({}))
        }
        "unpublish" => {
            github_require_confirm(arguments, "github pages unpublish")?;
            format!(
                "gh api --method DELETE {}",
                sh_quote(&format!("repos/{repo}/pages"))
            )
        }
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown pages action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_search_command(arguments: &Value, action: &str) -> Result<String, ToolError> {
    let query = string_arg(arguments, "query")?;
    let flag = github_repo_flag(arguments)?;
    let limit = optional_usize(arguments, "limit")
        .unwrap_or(20)
        .clamp(1, 100);
    let command = match action {
        "repos" => format!(
            "gh search repos {} --limit {limit} --json nameWithOwner,description,isPrivate,stargazerCount,url",
            sh_quote(&query)
        ),
        "issues" => format!(
            "gh search issues {}{flag} --limit {limit} --json number,title,state,repository,url,updatedAt",
            sh_quote(&query)
        ),
        "prs" => format!(
            "gh search prs {}{flag} --limit {limit} --json number,title,state,repository,url,updatedAt",
            sh_quote(&query)
        ),
        "code" => format!(
            "gh search code {}{flag} --limit {limit} --json path,repository,url",
            sh_quote(&query)
        ),
        "commits" => format!(
            "gh search commits {}{flag} --limit {limit} --json sha,commit,repository,url",
            sh_quote(&query)
        ),
        other => {
            return Err(ToolError::InvalidArgument(format!(
                "unknown search action: {other}"
            )));
        }
    };
    Ok(command)
}

fn github_command(arguments: &Value) -> Result<String, ToolError> {
    let area = optional_string(arguments, "area").unwrap_or_else(|| "status".to_owned());
    let action = optional_string(arguments, "action").unwrap_or_else(|| match area.as_str() {
        "status" => "status".to_owned(),
        "repo" => "view".to_owned(),
        "branch" | "issue" | "label" | "milestone" | "pr" | "workflow" | "run" | "release" => {
            "list".to_owned()
        }
        "pages" => "status".to_owned(),
        "search" => "issues".to_owned(),
        "api" => "get".to_owned(),
        _ => "view".to_owned(),
    });
    match area.as_str() {
        "status" => {
            let flag = github_repo_flag(arguments)?;
            Ok(format!(
                "gh auth status && {{ printf '\\n---\\n'; gh repo view{flag} --json nameWithOwner,defaultBranchRef,url,visibility,isPrivate 2>/dev/null || echo 'no GitHub repository in the current workspace'; }}"
            ))
        }
        "repo" => github_repo_command(arguments, &action),
        "branch" => github_branch_command(arguments, &action),
        "issue" => github_issue_command(arguments, &action),
        "label" => github_label_command(arguments, &action),
        "milestone" => github_milestone_command(arguments, &action),
        "pr" => github_pr_command(arguments, &action),
        "workflow" => github_workflow_command(arguments, &action),
        "run" => github_run_command(arguments, &action),
        "release" => github_release_command(arguments, &action),
        "pages" => github_pages_command(arguments, &action),
        "search" => github_search_command(arguments, &action),
        "api" => github_api_command(arguments),
        other => Err(ToolError::InvalidArgument(format!(
            "unknown GitHub area: {other}"
        ))),
    }
}

struct GithubTool;

#[async_trait]
impl Tool for GithubTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "github",
            "Full GitHub surface through gh: status, repo, branch, issue, label, milestone, pr (review/merge), workflow, run, release, pages, search, and generic api. Destructive actions require confirm=true. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_timeout(300)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "area": {
                    "type": "string",
                    "enum": ["status", "repo", "branch", "issue", "label", "milestone", "pr", "workflow", "run", "release", "pages", "search", "api"],
                    "description": "GitHub resource family."
                },
                "action": {
                    "type": "string",
                    "description": "Action within the area. repo: view/list/create/fork/edit/delete/archive/unarchive/rename/clone/sync. branch: list/get/create/delete/protection/protect/rename. issue: list/get/create/update/comment/close/reopen/delete/lock/unlock/pin/unpin/comments. label: list/create/edit/delete/clone. milestone: list/create/update/close/open/delete. pr: list/view/diff/files/commits/comments/create/update/comment/review/merge/close/reopen/ready/checks/checkout/update-branch/lock/unlock. workflow: list/view/dispatch/enable/disable. run: list/view/logs/artifacts/download/cancel/rerun/delete/watch. release: list/view/create/edit/upload/delete/download. pages: status/site/builds/build/request-build/unpublish. search: repos/issues/prs/code/commits. api: get/post/patch/put/delete."
                },
                "repository": {"type": "string", "description": "owner/name; defaults to the workspace repository."},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "number": {"type": "integer", "minimum": 1},
                "run_id": {"type": "string"},
                "workflow": {"type": "string"},
                "branch": {"type": "string"},
                "base": {"type": "string"},
                "head": {"type": "string"},
                "sha": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": ["string", "object"], "description": "Text body, or a JSON object for branch protection/api requests."},
                "notes": {"type": "string"},
                "description": {"type": "string"},
                "query": {"type": "string"},
                "endpoint": {"type": "string"},
                "method": {"type": "string"},
                "jq": {"type": "string"},
                "fields": {"type": "object"},
                "inputs": {"type": "object"},
                "tag": {"type": "string"},
                "name": {"type": "string"},
                "new_name": {"type": "string"},
                "source": {"type": "string"},
                "target": {"type": "string"},
                "ref": {"type": "string"},
                "state": {"type": "string"},
                "status": {"type": "string"},
                "visibility": {"type": "string"},
                "color": {"type": "string"},
                "milestone": {"type": "string"},
                "assignee": {"type": "string"},
                "pattern": {"type": "string"},
                "artifact": {"type": "string"},
                "directory": {"type": "string"},
                "merge_method": {"type": "string", "enum": ["squash", "merge", "rebase"]},
                "event": {"type": "string", "enum": ["comment", "approve", "request-changes"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "assignees": {"type": "array", "items": {"type": "string"}},
                "reviewers": {"type": "array", "items": {"type": "string"}},
                "add_labels": {"type": "array", "items": {"type": "string"}},
                "remove_labels": {"type": "array", "items": {"type": "string"}},
                "add_assignees": {"type": "array", "items": {"type": "string"}},
                "remove_assignees": {"type": "array", "items": {"type": "string"}},
                "add_reviewers": {"type": "array", "items": {"type": "string"}},
                "remove_reviewers": {"type": "array", "items": {"type": "string"}},
                "add_topics": {"type": "array", "items": {"type": "string"}},
                "remove_topics": {"type": "array", "items": {"type": "string"}},
                "files": {"type": "array", "items": {"type": "string"}},
                "json": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "private": {"type": "boolean"},
                "draft": {"type": "boolean"},
                "prerelease": {"type": "boolean"},
                "latest": {"type": "boolean"},
                "generate_notes": {"type": "boolean"},
                "cleanup_tag": {"type": "boolean"},
                "clobber": {"type": "boolean"},
                "delete_branch": {"type": "boolean"},
                "auto": {"type": "boolean"},
                "watch": {"type": "boolean"},
                "failed_only": {"type": "boolean"},
                "yaml": {"type": "boolean"},
                "name_only": {"type": "boolean"},
                "forks": {"type": "boolean"},
                "force": {"type": "boolean"},
                "paginate": {"type": "boolean"},
                "slurp": {"type": "boolean"},
                "confirm": {"type": "boolean"},
                "dry_run": {"type": "boolean"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        mut arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "github (commands are disabled; switch the composer access mode to Full access)"
                    .to_owned(),
            ));
        }
        if let Some(body) = arguments.get("body").and_then(Value::as_object) {
            let area = optional_string(&arguments, "area").unwrap_or_default();
            if area != "branch" && area != "api" {
                return Err(ToolError::InvalidArgument(
                    "body objects are only supported for branch protection and api".to_owned(),
                ));
            }
            if area == "api" {
                let mut object = serde_json::Map::new();
                for (key, value) in body {
                    object.insert(key.clone(), value.clone());
                }
                arguments["body"] = Value::Object(object);
            }
        }
        let command = github_command(&arguments)?;
        if optional_bool(&arguments, "dry_run").unwrap_or(false) {
            return Ok(
                ToolResult::ok(format!("dry run: {command}")).with_metadata(json!({
                    "dry_run": true,
                    "command": command,
                })),
            );
        }
        let class = classify_command(&command);
        check_command_policy(context, class, &command)?;
        run_tool_shell(context, command).await
    }
}

struct GithubIssueTool;

#[async_trait]
impl Tool for GithubIssueTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "github_issue",
            "List, view, create, comment on, or close GitHub issues through gh.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "create", "comment", "close"]},
                "number": {"type": "integer", "minimum": 1},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "query": {"type": "string"},
                "confirm": {"type": "boolean"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "list".to_owned());
        let command = match action.as_str() {
            "list" => {
                let limit = 20;
                let state =
                    optional_string(&arguments, "state").unwrap_or_else(|| "open".to_owned());
                let mut command = format!(
                    "gh issue list --limit {limit} --state {state} --json number,title,state,labels"
                );
                if let Some(query) = optional_string(&arguments, "query") {
                    command.push_str(&format!(" --search {}", sh_quote(&query)));
                }
                command
            }
            "get" => format!(
                "gh issue view {} --json number,title,body,state,labels,comments",
                integer_arg(&arguments, "number")?
            ),
            "create" => {
                let title = string_arg(&arguments, "title")?;
                let body = string_arg(&arguments, "body")?;
                let mut command = format!(
                    "gh issue create --title {} --body {}",
                    sh_quote(&title),
                    sh_quote(&body)
                );
                for label in string_array_arg_or_empty(&arguments, "labels")? {
                    command.push_str(&format!(" --label {}", sh_quote(&label)));
                }
                command
            }
            "comment" => format!(
                "gh issue comment {} --body {}",
                integer_arg(&arguments, "number")?,
                sh_quote(&string_arg(&arguments, "body")?)
            ),
            "close" => {
                if !optional_bool(&arguments, "confirm").unwrap_or(false) {
                    return Err(ToolError::ApprovalRequired(
                        "github_issue close requires confirm=true".to_owned(),
                    ));
                }
                format!("gh issue close {}", integer_arg(&arguments, "number")?)
            }
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown issue action: {other}"
                )));
            }
        };
        run_tool_shell(context, command).await
    }
}

struct GithubPrTool;

#[async_trait]
impl Tool for GithubPrTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "github_pr",
            "View, diff, create, comment on, check, or merge GitHub pull requests through gh. Merge requires confirm=true.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["view", "diff", "create", "comment", "checks", "merge"]},
                "number": {"type": "integer", "minimum": 1},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "base": {"type": "string"},
                "head": {"type": "string"},
                "draft": {"type": "boolean"},
                "confirm": {"type": "boolean"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "view".to_owned());
        let command = match action.as_str() {
            "view" => format!(
                "gh pr view {} --json number,title,body,state,mergeable,reviews,files,statusCheckRollup",
                integer_arg(&arguments, "number")?
            ),
            "diff" => format!("gh pr diff {}", integer_arg(&arguments, "number")?),
            "create" => {
                let title = string_arg(&arguments, "title")?;
                let body = string_arg(&arguments, "body")?;
                let mut command = format!(
                    "gh pr create --title {} --body {}",
                    sh_quote(&title),
                    sh_quote(&body)
                );
                if let Some(base) = optional_string(&arguments, "base") {
                    command.push_str(&format!(" --base {}", sh_quote(&base)));
                }
                if let Some(head) = optional_string(&arguments, "head") {
                    command.push_str(&format!(" --head {}", sh_quote(&head)));
                }
                if optional_bool(&arguments, "draft").unwrap_or(false) {
                    command.push_str(" --draft");
                }
                command
            }
            "comment" => format!(
                "gh pr comment {} --body {}",
                integer_arg(&arguments, "number")?,
                sh_quote(&string_arg(&arguments, "body")?)
            ),
            "checks" => format!("gh pr checks {}", integer_arg(&arguments, "number")?),
            "merge" => {
                if !optional_bool(&arguments, "confirm").unwrap_or(false) {
                    return Err(ToolError::ApprovalRequired(
                        "github_pr merge requires confirm=true".to_owned(),
                    ));
                }
                format!(
                    "gh pr merge {} --squash --delete-branch",
                    integer_arg(&arguments, "number")?
                )
            }
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown pull request action: {other}"
                )));
            }
        };
        run_tool_shell(context, command).await
    }
}

struct GithubCiTool;

#[async_trait]
impl Tool for GithubCiTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "github_ci",
            "List recent workflow runs or fetch failed logs for a run through gh.",
            RiskLevel::Execute,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "logs"]},
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "list".to_owned());
        let command = match action.as_str() {
            "list" => format!(
                "gh run list --limit {} --json databaseId,name,status,conclusion,headBranch,createdAt",
                optional_usize(&arguments, "limit")
                    .unwrap_or(10)
                    .clamp(1, 50)
            ),
            "logs" => format!(
                "gh run view {} --log-failed",
                string_arg(&arguments, "run_id")?
            ),
            other => {
                return Err(ToolError::InvalidArgument(format!(
                    "unknown CI action: {other}"
                )));
            }
        };
        run_tool_shell(context, command).await
    }
}

struct CanonBanTool;

#[async_trait]
impl Tool for CanonBanTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "canon_ban",
            "Add or remove a banned phrase in .openbad/canon.yaml with its canonical replacement and citation.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "phrase": {"type": "string"},
                "replacement": {"type": "string"},
                "citation": {"type": "string"},
                "remove": {"type": "boolean"}
            },
            "required": ["phrase"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("canon_ban".to_owned()));
        }
        let phrase = string_arg(&arguments, "phrase")?;
        let mut canon = openbad_workspace::load_canon(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        if optional_bool(&arguments, "remove").unwrap_or(false) {
            let before = canon.banned_phrases.len();
            canon
                .banned_phrases
                .retain(|banned| !banned.phrase.eq_ignore_ascii_case(&phrase));
            if canon.banned_phrases.len() == before {
                return Err(ToolError::InvalidArgument(format!(
                    "banned phrase not found: {phrase}"
                )));
            }
        } else {
            let replacement = string_arg(&arguments, "replacement")?;
            let entry = openbad_workspace::BannedPhrase {
                phrase: phrase.clone(),
                replacement,
                citation: optional_string(&arguments, "citation"),
            };
            if let Some(existing) = canon
                .banned_phrases
                .iter_mut()
                .find(|banned| banned.phrase.eq_ignore_ascii_case(&phrase))
            {
                *existing = entry;
            } else {
                canon.banned_phrases.push(entry);
            }
        }
        openbad_workspace::save_canon(context.workspace.root(), &canon)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "canon banned phrases: {}",
            canon.banned_phrases.len()
        ))
        .with_metadata(json!({"banned_phrases": canon.banned_phrases.len()})))
    }
}

struct CanonAddTool;

#[async_trait]
impl Tool for CanonAddTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "canon_add",
            "Add or replace a canon-ledger entry in .openbad/canon.yaml. Status: canonical, superseded, disputed, or unknown.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "statement": {"type": "string"},
                "status": {"type": "string", "enum": ["canonical", "superseded", "disputed", "unknown"]},
                "superseded_by": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["id", "statement"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("canon_add".to_owned()));
        }
        let id = string_arg(&arguments, "id")?;
        let statement = string_arg(&arguments, "statement")?;
        let status =
            optional_string(&arguments, "status").unwrap_or_else(|| "canonical".to_owned());
        if !openbad_workspace::validate_canon_status(&status) {
            return Err(ToolError::InvalidArgument(
                "status must be canonical, superseded, disputed, or unknown".to_owned(),
            ));
        }
        let evidence = arguments
            .get("evidence")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let mut canon = openbad_workspace::load_canon(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let entry = openbad_workspace::CanonEntry {
            id: id.clone(),
            statement,
            status,
            superseded_by: optional_string(&arguments, "superseded_by"),
            evidence,
            updated_at: now_millis(),
        };
        if let Some(existing) = canon.entries.iter_mut().find(|entry| entry.id == id) {
            *existing = entry;
        } else {
            canon.entries.push(entry);
        }
        openbad_workspace::save_canon(context.workspace.root(), &canon)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "canon entry {id} saved ({} total)",
            canon.entries.len()
        ))
        .with_metadata(json!({"id": id, "entries": canon.entries.len()})))
    }
}

struct CanonQueryTool;

#[async_trait]
impl Tool for CanonQueryTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "canon_query",
            "Query the canon ledger. By default returns only current canonical entries; set include_all=true to audit superseded/disputed entries.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "include_all": {"type": "boolean"}
            }
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let canon = openbad_workspace::load_canon(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let query = optional_string(&arguments, "query").map(|query| query.to_ascii_lowercase());
        let include_all = optional_bool(&arguments, "include_all").unwrap_or(false);
        let mut entries = canon
            .entries
            .iter()
            .filter(|entry| include_all || entry.status == "canonical")
            .filter(|entry| {
                query.as_ref().is_none_or(|query| {
                    entry.id.to_ascii_lowercase().contains(query)
                        || entry.statement.to_ascii_lowercase().contains(query)
                })
            })
            .collect::<Vec<_>>();
        entries.sort_by(|left, right| left.id.cmp(&right.id));
        if entries.is_empty() {
            return Ok(ToolResult::ok("no canon entries").with_metadata(json!({"entries": 0})));
        }
        let content = entries
            .iter()
            .map(|entry| {
                format!(
                    "- [{}] {} ({}){}",
                    entry.id,
                    entry.statement,
                    entry.status,
                    entry
                        .superseded_by
                        .as_deref()
                        .map(|next| format!(" → {next}"))
                        .unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({"entries": entries.len()})))
    }
}

struct CanonLintTool;

#[async_trait]
impl Tool for CanonLintTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "canon_lint",
            "Scan files for banned canon phrases in .openbad/canon.yaml and report the canonical replacement with citations.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000}
            }
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let canon = openbad_workspace::load_canon(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        if canon.banned_phrases.is_empty() {
            return Ok(ToolResult::ok(
                "no banned phrases declared in .openbad/canon.yaml",
            ));
        }
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(200)
            .clamp(1, 1_000);
        let paths = arguments.get("paths").and_then(Value::as_array);
        let candidates = if let Some(paths) = paths {
            paths
                .iter()
                .filter_map(Value::as_str)
                .map(|path| context.resolve_read(Path::new(path)))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .filter(|path| path.is_file())
                .collect::<Vec<_>>()
        } else {
            let mut files = Vec::new();
            for entry in WalkBuilder::new(context.workspace.root())
                .max_depth(Some(8))
                .build()
                .filter_map(Result::ok)
            {
                if files.len() >= 2_000 {
                    break;
                }
                let path = entry.path();
                if !entry.file_type().is_some_and(|kind| kind.is_file())
                    || path_is_excluded(context, path)
                    || path.components().any(|component| {
                        matches!(
                            component.as_os_str().to_str(),
                            Some(".git" | "target" | "node_modules" | "dist" | ".openbad")
                        )
                    })
                {
                    continue;
                }
                if path
                    .extension()
                    .and_then(|extension| extension.to_str())
                    .is_some_and(|extension| {
                        matches!(
                            extension,
                            "md" | "markdown" | "adoc" | "yaml" | "yml" | "json" | "txt"
                        )
                    })
                {
                    files.push(path.to_path_buf());
                }
            }
            files
        };
        let mut hits = Vec::new();
        for path in candidates {
            if hits.len() >= max_results {
                break;
            }
            let Ok(content) = std::fs::read_to_string(&path) else {
                continue;
            };
            let relative = relative_to_root(context, &path);
            if canon.paths_ignore.iter().any(|pattern| {
                globset::Glob::new(pattern)
                    .ok()
                    .is_some_and(|glob| glob.compile_matcher().is_match(&relative))
            }) {
                continue;
            }
            let mut fenced = false;
            let mut disabled = false;
            for (index, line) in content.lines().enumerate() {
                let trimmed = line.trim();
                if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
                    fenced = !fenced;
                    continue;
                }
                if trimmed == "<!-- canon-lint: off -->" {
                    disabled = true;
                    continue;
                }
                if trimmed == "<!-- canon-lint: on -->" {
                    disabled = false;
                    continue;
                }
                if fenced || disabled {
                    continue;
                }
                for (phrase, replacement, citation) in openbad_workspace::lint_text(&canon, line) {
                    hits.push(json!({
                        "path": relative,
                        "line": index + 1,
                        "phrase": phrase,
                        "replacement": replacement,
                        "citation": citation,
                        "text": line.trim()
                    }));
                    if hits.len() >= max_results {
                        break;
                    }
                }
            }
        }
        if hits.is_empty() {
            return Ok(ToolResult::ok("canon lint clean").with_metadata(json!({"hits": 0})));
        }
        let content = hits
            .iter()
            .map(|hit| {
                format!(
                    "{}:{}: `{}` → `{}`{}",
                    hit["path"].as_str().unwrap_or_default(),
                    hit["line"],
                    hit["phrase"].as_str().unwrap_or_default(),
                    hit["replacement"].as_str().unwrap_or_default(),
                    hit["citation"]
                        .as_str()
                        .map(|citation| format!(" ({citation})"))
                        .unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({"hits": hits})))
    }
}

struct GlossaryAddTool;

#[async_trait]
impl Tool for GlossaryAddTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "glossary_add",
            "Add or replace a glossary entry in .openbad/glossary.yaml.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "expansion": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["term", "definition"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("glossary_add".to_owned()));
        }
        let term = string_arg(&arguments, "term")?;
        let definition = string_arg(&arguments, "definition")?;
        let aliases = arguments
            .get("aliases")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let mut glossary = openbad_workspace::load_glossary(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let entry = openbad_workspace::GlossaryEntry {
            term: term.clone(),
            definition,
            expansion: optional_string(&arguments, "expansion"),
            aliases,
        };
        if let Some(existing) = glossary
            .entries
            .iter_mut()
            .find(|entry| entry.term.eq_ignore_ascii_case(&term))
        {
            *existing = entry;
        } else {
            glossary.entries.push(entry);
        }
        openbad_workspace::save_glossary(context.workspace.root(), &glossary)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!("glossary term {term} saved")))
    }
}

struct GlossaryLookupTool;

#[async_trait]
impl Tool for GlossaryLookupTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "glossary_lookup",
            "Look up glossary terms by term, expansion, or alias.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = string_arg(&arguments, "query")?.to_ascii_lowercase();
        let glossary = openbad_workspace::load_glossary(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let matches = glossary
            .entries
            .iter()
            .filter(|entry| {
                entry.term.to_ascii_lowercase().contains(&query)
                    || entry.definition.to_ascii_lowercase().contains(&query)
                    || entry
                        .expansion
                        .as_deref()
                        .is_some_and(|expansion| expansion.to_ascii_lowercase().contains(&query))
                    || entry
                        .aliases
                        .iter()
                        .any(|alias| alias.to_ascii_lowercase().contains(&query))
            })
            .collect::<Vec<_>>();
        if matches.is_empty() {
            return Ok(ToolResult::ok("no matching glossary terms"));
        }
        Ok(ToolResult::ok(
            matches
                .iter()
                .map(|entry| {
                    format!(
                        "- {}{}: {}",
                        entry.term,
                        entry
                            .expansion
                            .as_deref()
                            .map(|expansion| format!(" ({expansion})"))
                            .unwrap_or_default(),
                        entry.definition
                    )
                })
                .collect::<Vec<_>>()
                .join("\n"),
        ))
    }
}

struct AdrWriteTool;

#[async_trait]
impl Tool for AdrWriteTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "adr_write",
            "Write an architecture decision record under .openbad/adr/.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "context": {"type": "string"},
                "decision": {"type": "string"},
                "consequences": {"type": "string"},
                "status": {"type": "string", "enum": ["proposed", "accepted", "superseded", "rejected"]},
                "supersedes": {"type": "string"}
            },
            "required": ["title", "context", "decision", "consequences"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("adr_write".to_owned()));
        }
        let directory = openbad_workspace::adr_directory(context.workspace.root());
        std::fs::create_dir_all(&directory)?;
        let next = std::fs::read_dir(&directory)?
            .filter_map(Result::ok)
            .filter_map(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .split_once('-')
                    .and_then(|(number, _)| number.parse::<u32>().ok())
            })
            .max()
            .unwrap_or(0)
            + 1;
        let title = string_arg(&arguments, "title")?;
        let slug = title
            .to_ascii_lowercase()
            .chars()
            .map(|character| {
                if character.is_ascii_alphanumeric() {
                    character
                } else {
                    '-'
                }
            })
            .collect::<String>()
            .split('-')
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("-");
        let name = format!("{next:04}-{slug}.md");
        let status = optional_string(&arguments, "status").unwrap_or_else(|| "proposed".to_owned());
        let body = format!(
            "# ADR-{next:04}: {title}\n\n- Status: {status}\n- Date: {}\n{}\n\n## Context\n\n{}\n\n## Decision\n\n{}\n\n## Consequences\n\n{}\n",
            now_millis(),
            optional_string(&arguments, "supersedes")
                .map(|supersedes| format!("- Supersedes: {supersedes}\n"))
                .unwrap_or_default(),
            string_arg(&arguments, "context")?,
            string_arg(&arguments, "decision")?,
            string_arg(&arguments, "consequences")?
        );
        std::fs::write(directory.join(&name), body)?;
        Ok(ToolResult::ok(format!("wrote ADR {name}"))
            .with_metadata(json!({"name": name, "number": next, "status": status})))
    }
}

struct AdrListTool;

#[async_trait]
impl Tool for AdrListTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "adr_list",
            "List architecture decision records.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let directory = openbad_workspace::adr_directory(context.workspace.root());
        if !directory.is_dir() {
            return Ok(ToolResult::ok("no ADRs"));
        }
        let mut names = std::fs::read_dir(directory)?
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .filter(|name| name.ends_with(".md"))
            .collect::<Vec<_>>();
        names.sort();
        if names.is_empty() {
            return Ok(ToolResult::ok("no ADRs"));
        }
        Ok(ToolResult::ok(names.join("\n")).with_metadata(json!({"adrs": names.len()})))
    }
}

struct AdrReadTool;

#[async_trait]
impl Tool for AdrReadTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "adr_read",
            "Read an ADR by number or filename.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"]
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let id = string_arg(&arguments, "id")?;
        let directory = openbad_workspace::adr_directory(context.workspace.root());
        let mut names = std::fs::read_dir(&directory)?
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .filter(|name| name.starts_with(&id) || name.trim_end_matches(".md") == id)
            .collect::<Vec<_>>();
        names.sort();
        let name = names
            .first()
            .ok_or_else(|| ToolError::InvalidArgument(format!("unknown ADR: {id}")))?;
        let content = std::fs::read_to_string(directory.join(name))?;
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"name": name})))
    }
}

fn yaml_scalar(value: &serde_yaml::Value) -> String {
    match value {
        serde_yaml::Value::String(value) => value.clone(),
        serde_yaml::Value::Null => "null".to_owned(),
        other => serde_yaml::to_string(other)
            .unwrap_or_default()
            .trim()
            .to_owned(),
    }
}

fn resolve_yaml_path<'a>(
    value: &'a serde_yaml::Value,
    path: &str,
) -> Option<&'a serde_yaml::Value> {
    let mut current = value;
    for segment in path.split('.').filter(|segment| !segment.is_empty()) {
        current = match current {
            serde_yaml::Value::Mapping(mapping) => {
                mapping.get(serde_yaml::Value::String(segment.to_owned()))?
            }
            serde_yaml::Value::Sequence(items) => items.get(segment.parse::<usize>().ok()?)?,
            _ => return None,
        };
    }
    Some(current)
}

fn flatten_yaml(value: &serde_yaml::Value, prefix: &str, output: &mut Vec<String>) {
    match value {
        serde_yaml::Value::Mapping(mapping) => {
            for (key, value) in mapping {
                let key = yaml_scalar(key);
                let path = if prefix.is_empty() {
                    key
                } else {
                    format!("{prefix}.{key}")
                };
                flatten_yaml(value, &path, output);
            }
        }
        serde_yaml::Value::Sequence(items) => {
            for (index, value) in items.iter().enumerate() {
                let path = format!("{prefix}.{index}");
                flatten_yaml(value, &path, output);
            }
        }
        _ => output.push(format!("{prefix}={}", yaml_scalar(value))),
    }
}

struct YamlQueryTool;

#[async_trait]
impl Tool for YamlQueryTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "yaml_query",
            "Query YAML documents in a file (single or multi-document) with an optional top-level field=value filter, returning selected fields and line numbers (D7).",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "filter": {"type": "string", "description": "Top-level field=value, for example type=card"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = context.resolve_read(Path::new(&string_arg(&arguments, "path")?))?;
        ensure_not_excluded(context, &path)?;
        let content = std::fs::read_to_string(&path)?;
        context.mark_read(&path);
        let filter = optional_string(&arguments, "filter");
        let fields = arguments
            .get("fields")
            .and_then(Value::as_array)
            .map(|fields| {
                fields
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect::<Vec<_>>()
            });
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(100)
            .clamp(1, 500);
        let mut documents = Vec::new();
        let mut start = 0_usize;
        let mut parts = Vec::new();
        for (index, line) in content.lines().enumerate() {
            if line.trim() == "---" {
                parts.push((
                    start,
                    content
                        .lines()
                        .take(index)
                        .skip(start)
                        .collect::<Vec<_>>()
                        .join("\n"),
                ));
                start = index + 1;
            }
        }
        parts.push((
            start,
            content.lines().skip(start).collect::<Vec<_>>().join("\n"),
        ));
        for (offset, part) in parts {
            if part.trim().is_empty() {
                continue;
            }
            let Ok(value) = serde_yaml::from_str::<serde_yaml::Value>(&part) else {
                continue;
            };
            let line = part
                .lines()
                .position(|line| line.trim_start().starts_with("_id:"))
                .map_or(offset + 1, |line| offset + line + 1);
            let values = match value {
                serde_yaml::Value::Sequence(items) => items,
                other => vec![other],
            };
            for item in values {
                if let Some(filter) = &filter {
                    let (path, expected) = filter
                        .split_once('=')
                        .or_else(|| filter.split_once(':'))
                        .unwrap_or((filter.as_str(), ""));
                    let actual = resolve_yaml_path(&item, path.trim());
                    let matches = actual.is_some_and(|actual| {
                        let actual = yaml_scalar(actual);
                        actual == expected.trim() || actual.contains(expected.trim())
                    });
                    if !matches {
                        continue;
                    }
                }
                let field_text = match &fields {
                    Some(fields) => fields
                        .iter()
                        .filter_map(|field| {
                            resolve_yaml_path(&item, field)
                                .map(|value| format!("{field}={}", yaml_scalar(value)))
                        })
                        .collect::<Vec<_>>()
                        .join(" · "),
                    None => match &item {
                        serde_yaml::Value::Mapping(_) => {
                            let mut leaves = Vec::new();
                            flatten_yaml(&item, "", &mut leaves);
                            if leaves.is_empty() {
                                yaml_scalar(&item)
                            } else {
                                leaves.into_iter().take(40).collect::<Vec<_>>().join(" · ")
                            }
                        }
                        _ => ["_id", "name", "type"]
                            .iter()
                            .filter_map(|field| {
                                resolve_yaml_path(&item, field)
                                    .map(|value| format!("{field}={}", yaml_scalar(value)))
                            })
                            .collect::<Vec<_>>()
                            .join(" · "),
                    },
                };
                documents.push(format!("{line}: {field_text}"));
                if documents.len() >= max_results {
                    break;
                }
            }
        }
        if documents.is_empty() {
            return Ok(ToolResult::ok("no matching YAML documents"));
        }
        Ok(ToolResult::ok(documents.join("\n"))
            .with_metadata(json!({"documents": documents.len()})))
    }
}

struct CheckInvariantsTool;

#[async_trait]
impl Tool for CheckInvariantsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "check_invariants",
            "Run every invariant in .openbad/project.yaml that declares a check (recipe name or shell command) and record evidence.",
            RiskLevel::Execute,
        )
        .with_timeout(600)
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| ToolError::Failed("no project manifest".to_owned()))?;
        if manifest.invariants.is_empty() {
            return Ok(ToolResult::ok("no invariants declared"));
        }
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "check_invariants (requires Full access)".to_owned(),
            ));
        }
        let mut checks = Vec::new();
        for invariant in &manifest.invariants {
            let Some(check) = invariant.check.as_deref() else {
                checks.push(json!({
                    "id": invariant.id,
                    "severity": invariant.severity,
                    "status": "skipped",
                    "reason": "no check declared"
                }));
                continue;
            };
            if check.trim().is_empty()
                || matches!(check.trim(), "manual" | "~" | "attested" | "none")
            {
                checks.push(json!({
                    "id": invariant.id,
                    "statement": invariant.statement,
                    "severity": invariant.severity,
                    "check": check,
                    "status": "attested",
                    "reason": "manual check; not executed"
                }));
                continue;
            }
            let recipe = manifest.recipe(check);
            let mut command = json!({
                "command": recipe.as_ref().map(|recipe| recipe.command.clone()).unwrap_or_else(|| check.to_owned()),
                "timeout_seconds": 300
            });
            if let Some(cwd) = recipe.as_ref().and_then(|recipe| recipe.cwd.clone()) {
                command["cwd"] = json!(cwd);
                command["allow_external_cwd"] = json!(true);
            }
            let result = RunCommandTool.execute(command, context).await?;
            checks.push(json!({
                "id": invariant.id,
                "statement": invariant.statement,
                "severity": invariant.severity,
                "check": check,
                "status": if result.metadata["success"].as_bool().unwrap_or(false) { "passed" } else { "failed" },
                "output_preview": result.content.chars().take(1_000).collect::<String>(),
                "log_path": result.metadata["log_path"]
            }));
        }
        let directory = context.workspace.root().join(".openbad").join("invariants");
        std::fs::create_dir_all(&directory)?;
        let path = directory.join(format!("{}.json", now_millis()));
        let evidence = json!({"created_at": now_millis(), "checks": checks});
        std::fs::write(
            &path,
            serde_json::to_string_pretty(&evidence)
                .map_err(|error| ToolError::Failed(error.to_string()))?,
        )?;
        let summary = checks
            .iter()
            .map(|check| {
                format!(
                    "- [{}] {}",
                    check["status"].as_str().unwrap_or_default(),
                    check["id"].as_str().unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(
            ToolResult::ok(format!("{summary}\nevidence: {}", path.display()))
                .with_metadata(evidence),
        )
    }
}

struct AskQuestionTool;

#[async_trait]
impl Tool for AskQuestionTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "ask_question",
            "Queue a structured question for the human in Project → Questions. Use this when a decision blocks correctness instead of guessing. Continue other work or end the turn; the answer appears in the next prompt.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                    "description": "Optional suggested answers"
                },
                "multi": {"type": "boolean", "description": "Allow multiple options"},
                "context": {"type": "string", "description": "Why the decision matters"}
            },
            "required": ["question"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("ask_question".to_owned()));
        }
        let text = string_arg(&arguments, "question")?;
        if text.trim().is_empty() {
            return Err(ToolError::InvalidArgument(
                "question must not be empty".to_owned(),
            ));
        }
        let options = arguments
            .get("options")
            .and_then(Value::as_array)
            .map(|options| {
                options
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .take(8)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let mut questions = openbad_workspace::load_questions(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let question = openbad_workspace::Question {
            id: format!("q-{}", now_millis()),
            question: text.trim().to_owned(),
            options: options.clone(),
            multi: optional_bool(&arguments, "multi").unwrap_or(false),
            context: optional_string(&arguments, "context"),
            created_at: now_millis(),
            answer: None,
            answered_at: None,
        };
        questions.push(question.clone());
        openbad_workspace::save_questions(context.workspace.root(), &questions)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "queued question {} for the human: {}\nAsk them to answer in Project → Questions, then continue in the next turn.",
            question.id, question.question
        ))
        .with_metadata(json!({"id": question.id, "options": options})))
    }
}

struct ListQuestionsTool;

#[async_trait]
impl Tool for ListQuestionsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_questions",
            "List structured questions and their human answers from .openbad/questions.json.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "include_answered": {"type": "boolean", "description": "Default true"}
            }
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let include_answered = optional_bool(&arguments, "include_answered").unwrap_or(true);
        let questions = openbad_workspace::load_questions(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let visible = questions
            .iter()
            .filter(|question| include_answered || question.answer.is_none())
            .collect::<Vec<_>>();
        if visible.is_empty() {
            return Ok(ToolResult::ok("no questions").with_metadata(json!({"questions": 0})));
        }
        let content = visible
            .iter()
            .map(|question| {
                let state = question.answer.as_deref().map_or_else(
                    || "PENDING".to_owned(),
                    |answer| format!("ANSWERED: {answer}"),
                );
                format!(
                    "- [{}] {} ({}){}",
                    question.id,
                    question.question,
                    state,
                    if question.options.is_empty() {
                        String::new()
                    } else {
                        format!(" · options: {}", question.options.join(", "))
                    }
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({"questions": visible.len()})))
    }
}

struct PinContextTool;

#[async_trait]
impl Tool for PinContextTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "pin_context",
            "Keep a path resident in working context. Writes .openbad/context.yaml; use remove=true to drop the rule.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative glob"},
                "note": {"type": "string"},
                "remove": {"type": "boolean"}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        update_context_rule(&arguments, context, false)
    }
}

struct ExcludeContextTool;

#[async_trait]
impl Tool for ExcludeContextTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "exclude_context",
            "Guarantee a path is never loaded. Writes .openbad/context.yaml; use remove=true to drop the rule.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative glob"},
                "note": {"type": "string", "description": "Why this must not be loaded"},
                "remove": {"type": "boolean"}
            },
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        update_context_rule(&arguments, context, true)
    }
}

struct ListContextRulesTool;

#[async_trait]
impl Tool for ListContextRulesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_context_rules",
            "List pinned and excluded context rules from .openbad/context.yaml.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let policy = context_policy(context)?;
        if policy.pinned.is_empty() && policy.excluded.is_empty() {
            return Ok(ToolResult::ok("no context rules"));
        }
        Ok(ToolResult::ok(policy.summary()).with_metadata(json!({
            "pinned": policy.pinned.len(),
            "excluded": policy.excluded.len()
        })))
    }
}

struct ContextMapTool;

#[async_trait]
impl Tool for ContextMapTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "context_map",
            "Report what this turn has loaded, pinned rules, excluded rules, and ruled-out findings.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let policy = context_policy(context)?;
        let dead_ends = openbad_workspace::load_dead_ends(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let mut loaded = context
            .loaded_paths()
            .into_iter()
            .map(|path| relative_to_root(context, &path))
            .collect::<Vec<_>>();
        loaded.sort();
        loaded.dedup();
        let mut content = format!(
            "Holding {} file(s) this turn: {}\nPinned rules: {} · Excluded rules: {} · Ruled out: {}",
            loaded.len(),
            if loaded.is_empty() {
                "none".to_owned()
            } else {
                loaded.join(", ")
            },
            policy.pinned.len(),
            policy.excluded.len(),
            dead_ends.len()
        );
        let summary = policy.summary();
        if !summary.is_empty() {
            content.push('\n');
            content.push_str(&summary);
        }
        let ruled_out = openbad_workspace::dead_end_summary(&dead_ends, 10);
        if !ruled_out.is_empty() {
            content.push('\n');
            content.push_str(&ruled_out);
        }
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "loaded_files": loaded,
                "pinned": policy.pinned,
                "excluded": policy.excluded,
                "dead_ends": dead_ends.len()
            })),
        )
    }
}

struct RecordDeadEndTool;

#[async_trait]
impl Tool for RecordDeadEndTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "record_dead_end",
            "Record a ruled-out path, search, or hypothesis in .openbad/dead-ends.json so future sessions do not re-explore it.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Path, symbol, or hypothesis that was ruled out"},
                "reason": {"type": "string", "description": "Evidence that rules it out"},
                "hypothesis": {"type": "string"}
            },
            "required": ["pattern", "reason"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("record_dead_end".to_owned()));
        }
        let pattern = string_arg(&arguments, "pattern")?;
        let reason = string_arg(&arguments, "reason")?;
        if pattern.trim().is_empty() || reason.trim().is_empty() {
            return Err(ToolError::InvalidArgument(
                "pattern and reason must not be empty".to_owned(),
            ));
        }
        let mut dead_ends = openbad_workspace::load_dead_ends(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let entry = openbad_workspace::DeadEnd {
            pattern: pattern.clone(),
            reason,
            hypothesis: optional_string(&arguments, "hypothesis"),
            created_at: now_millis(),
        };
        if let Some(existing) = dead_ends
            .iter_mut()
            .find(|existing| existing.pattern == pattern)
        {
            *existing = entry;
        } else {
            dead_ends.push(entry);
        }
        openbad_workspace::save_dead_ends(context.workspace.root(), &dead_ends)
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        Ok(ToolResult::ok(format!(
            "recorded ruled-out finding for {pattern} ({} total)",
            dead_ends.len()
        ))
        .with_metadata(json!({"pattern": pattern, "dead_ends": dead_ends.len()})))
    }
}

struct ListDeadEndsTool;

#[async_trait]
impl Tool for ListDeadEndsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_dead_ends",
            "List ruled-out paths and hypotheses recorded in .openbad/dead-ends.json.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"query": {"type": "string"}}
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let query = optional_string(&arguments, "query").map(|query| query.to_lowercase());
        let dead_ends = openbad_workspace::load_dead_ends(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let filtered = dead_ends
            .iter()
            .filter(|entry| {
                query.as_ref().is_none_or(|query| {
                    entry.pattern.to_lowercase().contains(query)
                        || entry.reason.to_lowercase().contains(query)
                        || entry
                            .hypothesis
                            .as_deref()
                            .is_some_and(|hypothesis| hypothesis.to_lowercase().contains(query))
                })
            })
            .collect::<Vec<_>>();
        if filtered.is_empty() {
            return Ok(
                ToolResult::ok("no ruled-out findings").with_metadata(json!({"dead_ends": 0}))
            );
        }
        let count = filtered.len();
        let owned = filtered.into_iter().cloned().collect::<Vec<_>>();
        Ok(
            ToolResult::ok(openbad_workspace::dead_end_summary(&owned, 100))
                .with_metadata(json!({"dead_ends": count})),
        )
    }
}

struct ReadProjectManifestTool;

#[async_trait]
impl Tool for ReadProjectManifestTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_project_manifest",
            "Read the project's .openbad/project.yaml summary, including named tasks, scope map, and invariants.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| {
                ToolError::Failed("no .openbad/project.yaml in this workspace".to_owned())
            })?;
        Ok(ToolResult::ok(truncate(
            &manifest.summary(),
            context.max_output_bytes,
        )))
    }
}

struct ClassifyPathTool;

#[async_trait]
impl Tool for ClassifyPathTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "classify_path",
            "Classify a workspace path using the project manifest scope map (authoritative, historical, generated, vendored, scratch, or unclassified).",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative path"}},
            "required": ["path"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = string_arg(&arguments, "path")?;
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let class = manifest
            .as_ref()
            .map_or(openbad_workspace::ScopeClass::Unclassified, |manifest| {
                manifest.classify(&path)
            });
        let warning = if class.needs_care() {
            Some(format!(
                "warning: {path} is classified as {}; confirm it is the intended source of truth",
                class.label()
            ))
        } else {
            None
        };
        Ok(ToolResult::ok(format!(
            "{path}: {}{}",
            class.label(),
            warning
                .as_deref()
                .map(|warning| format!("\n{warning}"))
                .unwrap_or_default()
        ))
        .with_metadata(json!({"scope": class.label(), "warning": warning})))
    }
}

struct RunTaskTool;

#[async_trait]
impl Tool for RunTaskTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_task",
            "Run a named task declared in .openbad/project.yaml. Requires Full access, like run_command.",
            RiskLevel::Execute,
        )
        .with_timeout(600)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name from the project manifest"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let name = string_arg(&arguments, "name")?;
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| {
                ToolError::Failed("no .openbad/project.yaml in this workspace".to_owned())
            })?;
        let task = manifest.command(&name).ok_or_else(|| {
            ToolError::InvalidArgument(format!(
                "unknown project task: {name}; available tasks: {}",
                manifest
                    .commands
                    .keys()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        })?;
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .map(|value| value as u64)
            .or(task.timeout_seconds)
            .unwrap_or(300);
        let mut command = json!({
            "command": task.command,
            "timeout_seconds": timeout,
        });
        if let Some(cwd) = task.cwd {
            command["cwd"] = json!(cwd);
            command["allow_external_cwd"] = json!(true);
        }
        let result = RunCommandTool.execute(command, context).await?;
        let mut content = format!("task {name}: {}", result.content);
        content.push_str("\nexit metadata: ");
        content.push_str(&result.metadata.to_string());
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(
                json!({"task": name, "command": task.command, "result": result.metadata}),
            ),
        )
    }
}

struct ListGatesTool;

#[async_trait]
impl Tool for ListGatesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_gates",
            "List verification gates declared in .openbad/project.yaml and their ordered recipe checks.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let Some(manifest) = manifest else {
            return Ok(ToolResult::ok("no gates (no .openbad/project.yaml)"));
        };
        if manifest.gates.is_empty() {
            return Ok(ToolResult::ok("no gates declared"));
        }
        let content = manifest
            .gates
            .iter()
            .map(|(name, gate)| {
                format!(
                    "- {name}: {}{}",
                    gate.checks.join(" → "),
                    gate.description
                        .as_deref()
                        .map(|description| format!(" — {description}"))
                        .unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({
            "gates": manifest.gates.keys().cloned().collect::<Vec<_>>()
        })))
    }
}

struct RunGateTool;

#[async_trait]
impl Tool for RunGateTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_gate",
            "Run a named verification gate: every declared recipe in order, with a pass/fail checklist and evidence written to .openbad/gates/. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_timeout(600)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Gate name from the project manifest"}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let name = string_arg(&arguments, "name")?;
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| {
                ToolError::Failed("no .openbad/project.yaml in this workspace".to_owned())
            })?;
        let gate = manifest.gates.get(&name).ok_or_else(|| {
            ToolError::InvalidArgument(format!(
                "unknown gate: {name}; available gates: {}",
                manifest
                    .gates
                    .keys()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        })?;
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "run_gate (verification gates require Full access)".to_owned(),
            ));
        }
        let mut checks = Vec::new();
        let mut passed = 0;
        for check in &gate.checks {
            let started = std::time::Instant::now();
            let Some(recipe) = manifest.recipe(check) else {
                checks.push(json!({
                    "recipe": check,
                    "success": false,
                    "error": "recipe is not declared",
                    "duration_ms": 0
                }));
                continue;
            };
            let result = RunCommandTool
                .execute(
                    json!({
                        "command": recipe.command,
                        "cwd": recipe.cwd,
                        "allow_external_cwd": true,
                        "timeout_seconds": recipe.timeout_seconds.unwrap_or(300)
                    }),
                    context,
                )
                .await?;
            let success = result.metadata["success"].as_bool().unwrap_or(false);
            if success {
                passed += 1;
            }
            checks.push(json!({
                "recipe": check,
                "command": recipe.command,
                "success": success,
                "exit_code": result.metadata["exit_code"],
                "duration_ms": started.elapsed().as_millis() as u64,
                "output_preview": result.content.chars().take(2_000).collect::<String>(),
                "log_path": result.metadata["log_path"]
            }));
        }
        let total = gate.checks.len();
        let evidence = json!({
            "gate": name,
            "description": gate.description,
            "created_at": now_millis(),
            "success": passed == total,
            "passed": passed,
            "total": total,
            "checks": checks
        });
        let directory = context.workspace.root().join(".openbad").join("gates");
        std::fs::create_dir_all(&directory)?;
        let path = directory.join(format!("{name}-{}.json", now_millis()));
        std::fs::write(
            &path,
            serde_json::to_string_pretty(&evidence)
                .map_err(|error| ToolError::Failed(error.to_string()))?,
        )?;
        let checklist = checks
            .iter()
            .map(|check| {
                format!(
                    "- [{}] {}",
                    if check["success"].as_bool().unwrap_or(false) {
                        "pass"
                    } else {
                        "fail"
                    },
                    check["recipe"].as_str().unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(format!(
            "gate {name}: {} ({passed}/{total} checks passed)\n{checklist}\nevidence: {}",
            if passed == total { "PASSED" } else { "FAILED" },
            path.display()
        ))
        .with_metadata(evidence))
    }
}

struct ListRecipesTool;

#[async_trait]
impl Tool for ListRecipesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_recipes",
            "List verification recipes declared in .openbad/project.yaml (lint, test, build, schema checks).",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?;
        let Some(manifest) = manifest else {
            return Ok(ToolResult::ok("no recipes (no .openbad/project.yaml)"));
        };
        if manifest.recipes.is_empty() {
            return Ok(ToolResult::ok("no recipes declared"));
        }
        let content = manifest
            .recipes
            .iter()
            .map(|(name, spec)| {
                let (command, description) = match spec {
                    openbad_workspace::CommandSpec::Shell(command) => (command.clone(), None),
                    openbad_workspace::CommandSpec::Detailed(definition) => {
                        (definition.command.clone(), definition.description.clone())
                    }
                };
                format!(
                    "- {name}: `{command}`{}",
                    description
                        .map(|description| format!(" — {description}"))
                        .unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({
            "recipes": manifest.recipes.keys().cloned().collect::<Vec<_>>()
        })))
    }
}

struct RunRecipeTool;

#[async_trait]
impl Tool for RunRecipeTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_recipe",
            "Run a named verification recipe from .openbad/project.yaml. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_timeout(600)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Recipe name from the project manifest"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let name = string_arg(&arguments, "name")?;
        let manifest = openbad_workspace::load_project_manifest(context.workspace.root())
            .map_err(|error| ToolError::Failed(error.to_string()))?
            .ok_or_else(|| {
                ToolError::Failed("no .openbad/project.yaml in this workspace".to_owned())
            })?;
        let recipe = manifest.recipe(&name).ok_or_else(|| {
            ToolError::InvalidArgument(format!(
                "unknown recipe: {name}; available recipes: {}",
                manifest
                    .recipes
                    .keys()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            ))
        })?;
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .map(|value| value as u64)
            .or(recipe.timeout_seconds)
            .unwrap_or(300);
        let mut command = json!({
            "command": recipe.command,
            "timeout_seconds": timeout,
        });
        if let Some(cwd) = recipe.cwd {
            command["cwd"] = json!(cwd);
            command["allow_external_cwd"] = json!(true);
        }
        let result = RunCommandTool.execute(command, context).await?;
        let success = result.metadata["success"].as_bool().unwrap_or(false);
        Ok(ToolResult::ok(format!(
            "recipe {name}: {}\n{}",
            if success { "passed" } else { "failed" },
            result.content
        ))
        .with_metadata(json!({
            "recipe": name,
            "command": recipe.command,
            "success": success,
            "result": result.metadata
        })))
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct TodoRecord {
    id: String,
    text: String,
    #[serde(default = "todo_state")]
    state: String,
    #[serde(default)]
    blocked_by: Vec<String>,
    #[serde(default)]
    done_when: Option<String>,
    #[serde(default)]
    updated_at: i64,
}

fn todo_state() -> String {
    "todo".to_owned()
}

fn todo_path(context: &ToolContext) -> std::path::PathBuf {
    context.workspace.root().join(".openbad").join("todos.json")
}

struct TodoWriteTool;

#[async_trait]
impl Tool for TodoWriteTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "todo_write",
            "Replace the project's durable todo list in .openbad/todos.json. States: todo, doing, blocked, done, dropped.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "state": {"type": "string", "enum": ["todo", "doing", "blocked", "done", "dropped"]},
                            "blocked_by": {"type": "array", "items": {"type": "string"}},
                            "done_when": {"type": "string"}
                        },
                        "required": ["id", "text"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["items"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired(
                "todo_write (switch the composer access mode to Can edit or Full access)"
                    .to_owned(),
            ));
        }
        let items = arguments
            .get("items")
            .and_then(Value::as_array)
            .ok_or_else(|| ToolError::InvalidArgument("items must be an array".to_owned()))?;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_millis() as i64)
            .unwrap_or(0);
        let mut todos = Vec::new();
        for item in items {
            let id = item
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| ToolError::InvalidArgument("todo id is required".to_owned()))?;
            let text = item
                .get("text")
                .and_then(Value::as_str)
                .ok_or_else(|| ToolError::InvalidArgument("todo text is required".to_owned()))?;
            let state = item
                .get("state")
                .and_then(Value::as_str)
                .unwrap_or("todo")
                .to_owned();
            if !matches!(
                state.as_str(),
                "todo" | "doing" | "blocked" | "done" | "dropped"
            ) {
                return Err(ToolError::InvalidArgument(format!(
                    "invalid todo state: {state}"
                )));
            }
            todos.push(TodoRecord {
                id: id.to_owned(),
                text: text.to_owned(),
                state,
                blocked_by: item
                    .get("blocked_by")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default(),
                done_when: item
                    .get("done_when")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                updated_at: now,
            });
        }
        let path = todo_path(context);
        checkpoint_before_write(context, &path)?;
        let content = serde_json::to_string_pretty(&todos)
            .map_err(|error| ToolError::Failed(format!("could not serialize todos: {error}")))?;
        atomic_write(&path, &content)?;
        Ok(ToolResult::ok(format!(
            "Wrote {} todos to .openbad/todos.json",
            todos.len()
        ))
        .with_metadata(json!({"todos": todos.len(), "path": path})))
    }
}

struct TodoReadTool;

#[async_trait]
impl Tool for TodoReadTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "todo_read",
            "Read the project's durable todo list from .openbad/todos.json.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let path = todo_path(context);
        if !path.is_file() {
            return Ok(ToolResult::ok("no project todos"));
        }
        let content = std::fs::read_to_string(&path)?;
        let todos: Vec<TodoRecord> = serde_json::from_str(&content)
            .map_err(|error| ToolError::Failed(format!("invalid todos.json: {error}")))?;
        if todos.is_empty() {
            return Ok(ToolResult::ok("no project todos"));
        }
        let rendered = todos
            .iter()
            .map(|todo| {
                let blocked = if todo.blocked_by.is_empty() {
                    String::new()
                } else {
                    format!(" (blocked by: {})", todo.blocked_by.join(", "))
                };
                format!(
                    "- [{}] {}: {}{}{}",
                    todo.state,
                    todo.id,
                    todo.text,
                    blocked,
                    todo.done_when
                        .as_deref()
                        .map(|done| format!(" — done when: {done}"))
                        .unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(
            ToolResult::ok(truncate(&rendered, context.max_output_bytes))
                .with_metadata(json!({"todos": todos.len()})),
        )
    }
}

struct WriteHandoffTool;

#[async_trait]
impl Tool for WriteHandoffTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "write_handoff",
            "Write the standard session handoff to .openbad/handoff.md: state, decisions, open questions, next actions, and unverified claims.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "unverified_claims": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["state"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired(
                "write_handoff (switch the composer access mode to Can edit or Full access)"
                    .to_owned(),
            ));
        }
        let state = string_arg(&arguments, "state")?;
        let section = |title: &str, key: &str| {
            let items = arguments
                .get(key)
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(|item| format!("- {item}\n"))
                        .collect::<String>()
                })
                .unwrap_or_default();
            format!(
                "## {title}\n\n{}\n",
                if items.is_empty() {
                    "- None recorded\n"
                } else {
                    &items
                }
            )
        };
        let content = format!(
            "# Session handoff\n\n## State\n\n{state}\n\n{}{}{}{}",
            section("Decisions", "decisions"),
            section("Open questions", "open_questions"),
            section("Next actions", "next_actions"),
            section("Unverified claims", "unverified_claims"),
        );
        let path = context.workspace.root().join(".openbad").join("handoff.md");
        checkpoint_before_write(context, &path)?;
        atomic_write(&path, &content)?;
        Ok(ToolResult::ok("Wrote .openbad/handoff.md").with_metadata(json!({"path": path})))
    }
}

struct WriteSkillTool;

#[async_trait]
impl Tool for WriteSkillTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "write_skill",
            "Write a durable procedure to .openbad/skills/<name>.md with frontmatter and steps, verification, and rollback.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "when_to_use": {"type": "string"},
                "preconditions": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "array", "items": {"type": "string"}},
                "rollback": {"type": "string"}
            },
            "required": ["name", "description"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("write_skill".to_owned()));
        }
        let name = string_arg(&arguments, "name")?;
        let slug = name
            .to_ascii_lowercase()
            .chars()
            .map(|character| {
                if character.is_ascii_alphanumeric() {
                    character
                } else {
                    '-'
                }
            })
            .collect::<String>()
            .split('-')
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("-");
        if slug.is_empty() {
            return Err(ToolError::InvalidArgument(
                "name must contain letters or digits".to_owned(),
            ));
        }
        let description = string_arg(&arguments, "description")?;
        let mut body = format!(
            "---\nname: {name}\ndescription: {}\n---\n\n# {name}\n\n",
            description.replace('\n', " ")
        );
        if let Some(when) = optional_string(&arguments, "when_to_use") {
            body.push_str(&format!("## When to use\n\n{when}\n\n"));
        }
        let list = |title: &str, name: &str| {
            let items = arguments
                .get(name)
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(|item| format!("- {item}"))
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            if items.is_empty() {
                String::new()
            } else {
                format!("## {title}\n\n{items}\n\n")
            }
        };
        body.push_str(&list("Preconditions", "preconditions"));
        body.push_str(&list("Steps", "steps"));
        body.push_str(&list("Verification", "verification"));
        if let Some(rollback) = optional_string(&arguments, "rollback") {
            body.push_str(&format!("## Rollback\n\n{rollback}\n"));
        }
        let directory = context.workspace.root().join(".openbad/skills");
        std::fs::create_dir_all(&directory)?;
        let path = directory.join(format!("{slug}.md"));
        check_write_policy(context, &path)?;
        std::fs::write(&path, body)?;
        Ok(
            ToolResult::ok(format!("skill {name} written to {}", path.display()))
                .with_metadata(json!({"name": name, "path": path})),
        )
    }
}

struct ListSkillsTool;

#[async_trait]
impl Tool for ListSkillsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_skills",
            "List workspace skills with their descriptions.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {},
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let skills = discover_skills(context.workspace.root());
        if skills.is_empty() {
            return Ok(ToolResult::ok("no workspace skills found"));
        }
        let content = skills
            .iter()
            .map(|skill| {
                let suffix = if skill.builtin { " [builtin]" } else { "" };
                if skill.description.is_empty() {
                    format!("- {}{suffix}", skill.name)
                } else {
                    format!("- {}{suffix}: {}", skill.name, skill.description)
                }
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(content).with_metadata(json!({"skills": skills.len()})))
    }
}

struct ReadSkillTool;

#[async_trait]
impl Tool for ReadSkillTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_skill",
            "Load the full instructions for a workspace skill by name.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let name = string_arg(&arguments, "name")?;
        let skill = discover_skills(context.workspace.root())
            .into_iter()
            .find(|skill| skill.name == name)
            .ok_or_else(|| ToolError::Failed(format!("skill not found: {name}")))?;
        if skill.builtin {
            let content = builtin_skill_content(&name).ok_or_else(|| {
                ToolError::Failed(format!("built-in skill content is missing: {name}"))
            })?;
            return Ok(ToolResult::ok(truncate(content, context.max_output_bytes))
                .with_metadata(json!({"name": name, "builtin": true})));
        }
        let canonical = skill.path.canonicalize()?;
        let root = context.workspace.root().canonicalize()?;
        if !canonical.starts_with(&root) {
            return Err(ToolError::Failed(format!(
                "skill path escapes the workspace: {name}"
            )));
        }
        let content = std::fs::read_to_string(&canonical)?;
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"name": skill.name, "path": skill.path})))
    }
}

const BUILTIN_PLUGINS: &[&str] = &["asciidoc"];

fn builtin_plugin_files(name: &str) -> Option<&'static [(&'static str, &'static str)]> {
    match name {
        "asciidoc" => Some(&[
            (
                "plugin.yaml",
                include_str!("../assets/plugins/asciidoc/plugin.yaml"),
            ),
            (
                "main.py",
                include_str!("../assets/plugins/asciidoc/main.py"),
            ),
            (
                "SKILL.md",
                include_str!("../assets/plugins/asciidoc/SKILL.md"),
            ),
            (
                "README.md",
                include_str!("../assets/plugins/asciidoc/README.md"),
            ),
        ]),
        _ => None,
    }
}

struct ListPluginsTool;

#[async_trait]
impl Tool for ListPluginsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plugin_list",
            "List installed workspace plugins with their actions, plus bundled plugins that can be installed.",
            RiskLevel::Read,
        )
        .with_schema(json!({"type": "object", "properties": {}}))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let plugins = discover_plugins(context.workspace.root());
        let installed: Vec<String> = plugins.iter().map(|plugin| plugin.name.clone()).collect();
        let available: Vec<&str> = BUILTIN_PLUGINS
            .iter()
            .copied()
            .filter(|name| !installed.iter().any(|installed| installed == name))
            .collect();
        let mut lines = Vec::new();
        for plugin in &plugins {
            let actions = plugin.action_names();
            let actions = if actions.is_empty() {
                "any action".to_owned()
            } else {
                actions.join(", ")
            };
            lines.push(format!(
                "- {} ({}): {} [actions: {actions}]",
                plugin.name,
                if plugin.version.is_empty() {
                    "unversioned"
                } else {
                    &plugin.version
                },
                plugin.description
            ));
        }
        if !available.is_empty() {
            lines.push(format!(
                "bundled, not installed: {} (use plugin_install_builtin)",
                available.join(", ")
            ));
        }
        if lines.is_empty() {
            return Ok(ToolResult::ok("no plugins installed"));
        }
        Ok(ToolResult::ok(lines.join("\n")).with_metadata(json!({
            "plugins": plugins,
            "bundled": available,
        })))
    }
}

struct DescribePluginTool;

#[async_trait]
impl Tool for DescribePluginTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plugin_describe",
            "Show a plugin's manifest, actions, and input schemas.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let name = string_arg(&arguments, "name")?;
        let (manifest, directory) = load_plugin(context.workspace.root(), &name)?;
        let content = serde_json::to_string_pretty(&manifest).unwrap_or_default();
        Ok(ToolResult::ok(content).with_metadata(json!({
            "name": manifest.name,
            "path": directory,
            "actions": manifest.action_names(),
        })))
    }
}

struct CallPluginTool;

#[async_trait]
impl Tool for CallPluginTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plugin_call",
            "Run one action of a workspace plugin. The plugin receives a JSON request on stdin and must emit JSON with content/metadata. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_timeout(120)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "plugin": {"type": "string"},
                "action": {"type": "string", "default": "run"},
                "arguments": {"type": "object"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
                "confirm": {"type": "boolean", "description": "Required for plugins declaring destructive permission."},
                "dry_run": {"type": "boolean", "description": "Return the command and request without executing."}
            },
            "required": ["plugin"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let root = context.workspace.root();
        let name = string_arg(&arguments, "plugin")?;
        let action = optional_string(&arguments, "action").unwrap_or_else(|| "run".to_owned());
        let plugin_arguments = arguments
            .get("arguments")
            .cloned()
            .unwrap_or_else(|| json!({}));
        if !plugin_arguments.is_object() {
            return Err(ToolError::InvalidArgument(
                "arguments must be an object".to_owned(),
            ));
        }
        let (manifest, directory) = load_plugin(root, &name)?;
        if !manifest.actions.is_empty() && !manifest.actions.contains_key(&action) {
            return Err(ToolError::InvalidArgument(format!(
                "plugin {name} has no action {action}; available: {}",
                manifest.action_names().join(", ")
            )));
        }
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .map_or(manifest.timeout_seconds, |value| value as u64)
            .clamp(1, 600);
        if optional_bool(&arguments, "dry_run").unwrap_or(false) {
            return Ok(ToolResult::ok(format!(
                "dry run: {} (cwd {}) with stdin request {}",
                manifest.command,
                directory.display(),
                json!({
                    "plugin": manifest.name,
                    "action": action,
                    "arguments": plugin_arguments,
                    "workspace": root,
                })
            ))
            .with_metadata(json!({
                "plugin": manifest.name,
                "action": action,
                "command": manifest.command,
                "dry_run": true,
            })));
        }
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "plugin_call (commands are disabled; switch the composer access mode to Full access)"
                    .to_owned(),
            ));
        }
        let class = plugin_command_class(&manifest);
        if class == CommandClass::Destructive
            && !optional_bool(&arguments, "confirm").unwrap_or(false)
        {
            return Err(ToolError::ApprovalRequired(format!(
                "plugin_call {name}:{action} (plugin declares destructive permission; set confirm=true)"
            )));
        }
        check_command_policy(context, class, &manifest.command)?;
        let outcome = run_plugin_process(
            root,
            &manifest,
            &directory,
            &action,
            &plugin_arguments,
            timeout,
            context.max_output_bytes,
        )?;
        let result = ToolResult {
            content: outcome.content,
            is_error: outcome.is_error,
            metadata: outcome.metadata,
        };
        Ok(result)
    }
}

struct TestPluginTool;

#[async_trait]
impl Tool for TestPluginTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plugin_test",
            "Run a plugin's declared test_command and record the result under .openbad/plugin-runs. Requires Full access.",
            RiskLevel::Execute,
        )
        .with_timeout(180)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "plugin": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}
            },
            "required": ["plugin"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "plugin_test (commands are disabled; switch the composer access mode to Full access)"
                    .to_owned(),
            ));
        }
        let root = context.workspace.root();
        let name = string_arg(&arguments, "plugin")?;
        let (manifest, directory) = load_plugin(root, &name)?;
        let test_command = manifest.test_command.clone().ok_or_else(|| {
            ToolError::InvalidArgument(format!("plugin {name} has no test_command"))
        })?;
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .map_or_else(|| manifest.timeout_seconds.max(120), |value| value as u64);
        let test_manifest = PluginManifest {
            command: test_command.clone(),
            actions: BTreeMap::new(),
            ..manifest.clone()
        };
        let class = plugin_command_class(&test_manifest);
        check_command_policy(context, class, &test_command)?;
        let outcome = run_plugin_process(
            root,
            &test_manifest,
            &directory,
            "__test__",
            &json!({}),
            timeout.clamp(1, 600),
            context.max_output_bytes,
        )?;
        let status = if outcome.is_error { "failed" } else { "passed" };
        let content = format!("plugin test {status}: {name}\n\n{}", outcome.content);
        Ok(ToolResult {
            content,
            is_error: outcome.is_error,
            metadata: outcome.metadata,
        })
    }
}

fn plugin_entrypoint(
    name: &str,
    runtime: &str,
    actions: &[String],
) -> Result<(&'static str, String, String), ToolError> {
    match runtime {
        "python" => Ok((
            "main.py",
            format!(
                r#"#!/usr/bin/env python3
"""OpenBaD plugin: {name}."""

import json
import sys

ACTIONS = {actions:?}


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        print(json.dumps({{"ok": False, "is_error": True, "content": f"invalid request: {{error}}"}}))
        sys.exit(1)
    action = request.get("action", "run")
    arguments = request.get("arguments") or {{}}
    if action not in ACTIONS and action != "run":
        print(json.dumps({{"ok": False, "is_error": True, "content": f"unknown action: {{action}}"}}))
        sys.exit(1)
    # Replace this with the real implementation. Keep the JSON contract stable.
    print(json.dumps({{"ok": True, "content": f"{name}:{{action}} received {{json.dumps(arguments)}}"}}))


if __name__ == "__main__":
    main()
"#
            ),
            "python3 -m py_compile main.py".to_owned(),
        )),
        "node" => Ok((
            "main.js",
            format!(
                r#"#!/usr/bin/env node
// OpenBaD plugin: {name}.
const actions = {actions_json};

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {{ input += chunk; }});
process.stdin.on("end", () => {{
  let request;
  try {{
    request = JSON.parse(input || "{{}}");
  }} catch (error) {{
    console.log(JSON.stringify({{ ok: false, is_error: true, content: `invalid request: ${{error}}` }}));
    process.exitCode = 1;
    return;
  }}
  const action = request.action || "run";
  const args = request.arguments || {{}};
  if (!actions.includes(action) && action !== "run") {{
    console.log(JSON.stringify({{ ok: false, is_error: true, content: `unknown action: ${{action}}` }}));
    process.exitCode = 1;
    return;
  }}
  console.log(JSON.stringify({{ ok: true, content: `{name}:${{action}} received ${{JSON.stringify(args)}}` }}));
}});
"#,
                actions_json = serde_json::to_string(actions).unwrap_or_else(|_| "[]".to_owned())
            ),
            "node --check main.js".to_owned(),
        )),
        "shell" => Ok((
            "main.sh",
            format!(
                r#"#!/bin/sh
# OpenBaD plugin: {name}.
# Reads one JSON request on stdin and must print one JSON object on stdout.
request=$(cat)
printf '%s\n' '{{"ok":true,"content":"{name} ran"}}'
"#
            ),
            "sh -n main.sh".to_owned(),
        )),
        other => Err(ToolError::InvalidArgument(format!(
            "unsupported runtime {other}; use python, node, or shell"
        ))),
    }
}

struct WritePluginTool;

#[async_trait]
impl Tool for WritePluginTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "write_plugin",
            "Scaffold a workspace plugin under .openbad/plugins/<name> with plugin.yaml, an entrypoint, a SKILL.md, and a test command. Edit the files afterwards; use plugin_test and plugin_call to verify.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "runtime": {"type": "string", "enum": ["python", "node", "shell"]},
                "actions": {"type": "array", "items": {"type": "string"}},
                "test_command": {"type": "string"},
                "permissions": {"type": "array", "items": {"type": "string", "enum": ["write", "network", "destructive"]}},
                "overwrite": {"type": "boolean"}
            },
            "required": ["name", "description"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired("write_plugin".to_owned()));
        }
        let root = context.workspace.root();
        let name = string_arg(&arguments, "name")?;
        if !valid_plugin_name(&name) {
            return Err(ToolError::InvalidArgument(format!(
                "invalid plugin name: {name}"
            )));
        }
        let mut actions = string_array_arg_or_empty(&arguments, "actions")?;
        if actions.is_empty() {
            actions.push("run".to_owned());
        }
        for action in &actions {
            if !valid_plugin_name(action) {
                return Err(ToolError::InvalidArgument(format!(
                    "invalid action name: {action}"
                )));
            }
        }
        let runtime = optional_string(&arguments, "runtime").unwrap_or_else(|| "python".to_owned());
        let description = string_arg(&arguments, "description")?;
        let directory = plugin_root(root).join(&name);
        if directory.exists() && !optional_bool(&arguments, "overwrite").unwrap_or(false) {
            return Err(ToolError::Failed(format!(
                "plugin already exists: {} (set overwrite=true to replace the manifest and entrypoint)",
                directory.display()
            )));
        }
        if directory.exists() {
            plugin_directory(root, &name)?;
        }
        std::fs::create_dir_all(&directory)?;
        let (entrypoint, body, default_test) = plugin_entrypoint(&name, &runtime, &actions)?;
        let test_command = optional_string(&arguments, "test_command").unwrap_or(default_test);
        let permissions = string_array_arg_or_empty(&arguments, "permissions")?;
        let manifest = PluginManifest {
            name: name.clone(),
            description: description.clone(),
            version: "0.1.0".to_owned(),
            command: match runtime.as_str() {
                "python" => "python3 main.py".to_owned(),
                "node" => "node main.js".to_owned(),
                _ => "sh main.sh".to_owned(),
            },
            test_command: Some(test_command),
            timeout_seconds: 60,
            actions: actions
                .iter()
                .map(|action| {
                    (
                        action.clone(),
                        PluginAction {
                            description: String::new(),
                            input_schema: None,
                        },
                    )
                })
                .collect(),
            permissions,
        };
        let manifest_yaml = serde_yaml::to_string(&manifest)
            .map_err(|error| ToolError::Failed(format!("cannot serialize plugin.yaml: {error}")))?;
        let frontmatter = serde_yaml::to_string(&json!({
            "name": name,
            "description": description,
        }))
        .map_err(|error| ToolError::Failed(format!("cannot serialize SKILL.md: {error}")))?;
        std::fs::write(directory.join("plugin.yaml"), manifest_yaml)?;
        std::fs::write(directory.join(entrypoint), body)?;
        std::fs::write(
            directory.join("SKILL.md"),
            format!(
                "---\n{frontmatter}---\n\n# {name}\n\nCall `plugin_call` with `plugin: \"{name}\"` and an action from: {}.\n",
                actions.join(", ")
            ),
        )?;
        std::fs::write(
            directory.join("README.md"),
            format!(
                "# {name}\n\n{description}\n\nRun a smoke test with `plugin_test`, then call actions with `plugin_call`.\n"
            ),
        )?;
        Ok(ToolResult::ok(format!(
            "plugin {name} scaffolded at {}\nentrypoint: {entrypoint}\nnext: edit the entrypoint, then plugin_test and plugin_call",
            directory.display()
        ))
        .with_metadata(json!({
            "name": name,
            "path": directory,
            "entrypoint": entrypoint,
            "actions": actions,
        })))
    }
}

struct InstallBuiltinPluginTool;

#[async_trait]
impl Tool for InstallBuiltinPluginTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "plugin_install_builtin",
            "Install a bundled plugin into .openbad/plugins. Currently bundles asciidoc (asciidoctor renderer).",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": ["asciidoc"]},
                "overwrite": {"type": "boolean"}
            },
            "required": ["name"],
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_writes {
            return Err(ToolError::ApprovalRequired(
                "plugin_install_builtin".to_owned(),
            ));
        }
        let name = string_arg(&arguments, "name")?;
        let files = builtin_plugin_files(&name).ok_or_else(|| {
            ToolError::InvalidArgument(format!(
                "unknown bundled plugin {name}; available: {}",
                BUILTIN_PLUGINS.join(", ")
            ))
        })?;
        let directory = plugin_root(context.workspace.root()).join(&name);
        if directory.exists() && !optional_bool(&arguments, "overwrite").unwrap_or(false) {
            return Err(ToolError::Failed(format!(
                "plugin already installed: {} (set overwrite=true to refresh it)",
                directory.display()
            )));
        }
        if directory.exists() {
            plugin_directory(context.workspace.root(), &name)?;
        }
        std::fs::create_dir_all(&directory)?;
        for (path, content) in files {
            std::fs::write(directory.join(path), content)?;
        }
        Ok(ToolResult::ok(format!(
            "installed bundled plugin {name} at {}\nnext: plugin_call(plugin=\"{name}\", action=\"check\")",
            directory.display()
        ))
        .with_metadata(json!({"name": name, "path": directory})))
    }
}

struct RunTestsTool;

#[async_trait]
impl Tool for RunTestsTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_tests",
            "Detect and run the workspace test suite (Cargo, pnpm/npm, pytest, Go, or make). Requires command approval.",
            RiskLevel::Execute,
        )
        .with_timeout(120)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative project directory"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        if !context.allow_commands {
            return Err(ToolError::ApprovalRequired(
                "run_tests (commands are disabled; switch the composer access mode to Full access)"
                    .to_owned(),
            ));
        }
        let relative = optional_string(&arguments, "path").unwrap_or_else(|| ".".to_owned());
        let directory = context.resolve_write(Path::new(&relative))?;
        let (command, kind) = detect_test_command(&directory)?;
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .unwrap_or(300)
            .clamp(1, 600);
        let result = RunCommandTool
            .execute(
                json!({
                    "command": command,
                    "cwd": directory.display().to_string(),
                    "timeout_seconds": timeout
                }),
                context,
            )
            .await?;
        let summary = test_failure_summary(&result.content);
        let mut content = format!("{kind}: {command}\n{}", result.content);
        if let Some(summary) = summary {
            content.push_str("\nfailures:\n");
            content.push_str(&summary);
        }
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "kind": kind,
                "command": command,
                "success": result.metadata.get("success").and_then(Value::as_bool),
            })),
        )
    }
}

fn detect_test_command(directory: &Path) -> Result<(String, &'static str), ToolError> {
    if directory.join("Cargo.toml").is_file() {
        let manifest = std::fs::read_to_string(directory.join("Cargo.toml"))?;
        let command = if manifest.contains("[workspace]") {
            "cargo test --workspace"
        } else {
            "cargo test"
        };
        return Ok((command.to_owned(), "cargo"));
    }
    if directory.join("package.json").is_file() {
        let package = std::fs::read_to_string(directory.join("package.json"))?;
        let package: Value = serde_json::from_str(&package)
            .map_err(|error| ToolError::Failed(format!("invalid package.json: {error}")))?;
        if package
            .get("scripts")
            .and_then(|scripts| scripts.get("test"))
            .and_then(Value::as_str)
            .is_none()
        {
            return Err(ToolError::Failed(
                "package.json has no test script".to_owned(),
            ));
        }
        let command = if directory.join("pnpm-lock.yaml").is_file() {
            "pnpm test"
        } else if directory.join("yarn.lock").is_file() {
            "yarn test"
        } else {
            "npm test"
        };
        return Ok((command.to_owned(), "node"));
    }
    if directory.join("pyproject.toml").is_file()
        || directory.join("pytest.ini").is_file()
        || directory.join("setup.cfg").is_file()
        || directory.join("tox.ini").is_file()
    {
        return Ok(("python3 -m pytest -q".to_owned(), "pytest"));
    }
    if directory.join("go.mod").is_file() {
        return Ok(("go test ./...".to_owned(), "go"));
    }
    if directory.join("Makefile").is_file() {
        let makefile = std::fs::read_to_string(directory.join("Makefile"))?;
        if makefile.lines().any(|line| line.trim() == "test:") {
            return Ok(("make test".to_owned(), "make"));
        }
    }
    Err(ToolError::Failed(format!(
        "no recognized test project in {}",
        directory.display()
    )))
}

fn test_failure_summary(output: &str) -> Option<String> {
    let lines: Vec<&str> = output
        .lines()
        .filter(|line| {
            line.contains("test result: FAILED")
                || line.contains("FAILED")
                || line.starts_with("Tests:")
                || line.contains(" failed,")
                || line.trim_start().starts_with("FAILED ")
        })
        .take(12)
        .collect();
    if lines.is_empty() {
        None
    } else {
        Some(lines.join("\n"))
    }
}

struct GitStatusTool;

#[async_trait]
impl Tool for GitStatusTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_status",
            "Show the workspace Git branch and working-tree status.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {},
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        _arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let output = std::process::Command::new("git")
            .current_dir(context.workspace.root())
            .args(["status", "--short", "--branch"])
            .output()?;
        let mut content = String::from_utf8_lossy(&output.stdout).into_owned();
        content.push_str(&String::from_utf8_lossy(&output.stderr));
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "exit_code": output.status.code(),
            })),
        )
    }
}

struct GitDiffTool;

#[async_trait]
impl Tool for GitDiffTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "git_diff",
            "Show the current Git diff, optionally staged or scoped to a path.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "staged": {"type": "boolean"},
                "path": {"type": "string"}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let mut command = std::process::Command::new("git");
        command.current_dir(context.workspace.root()).arg("diff");
        if optional_bool(&arguments, "staged").unwrap_or(false) {
            command.arg("--staged");
        }
        if let Some(path) = optional_string(&arguments, "path") {
            let path = context.resolve_read(Path::new(&path))?;
            command.arg("--").arg(path);
        }
        let output = command.output()?;
        let mut content = String::from_utf8_lossy(&output.stdout).into_owned();
        content.push_str(&String::from_utf8_lossy(&output.stderr));
        Ok(
            ToolResult::ok(truncate(&content, context.max_output_bytes)).with_metadata(json!({
                "exit_code": output.status.code(),
            })),
        )
    }
}

struct RepoMapTool;

#[async_trait]
impl Tool for RepoMapTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "repo_map",
            "List the repository file tree, respecting .gitignore, for orientation.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 5000}
            },
            "additionalProperties": false
        }))
    }

    async fn execute(
        &self,
        arguments: Value,
        context: &ToolContext,
    ) -> Result<ToolResult, ToolError> {
        let max_entries = optional_usize(&arguments, "max_entries")
            .unwrap_or(1_000)
            .clamp(1, 5_000);
        let mut entries = Vec::new();
        for entry in WalkBuilder::new(context.workspace.root())
            .max_depth(Some(20))
            .build()
            .filter_map(Result::ok)
        {
            if entries.len() >= max_entries {
                break;
            }
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                continue;
            }
            let path = entry
                .path()
                .strip_prefix(context.workspace.root())
                .unwrap_or(entry.path());
            entries.push(path.display().to_string());
        }
        entries.sort();
        Ok(
            ToolResult::ok(truncate(&entries.join("\n"), context.max_output_bytes))
                .with_metadata(json!({"files": entries.len()})),
        )
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    fn context(root: &Path) -> ToolContext {
        ToolContext::new(Arc::new(WorkspaceService::new(root)))
    }

    #[tokio::test]
    async fn read_file_returns_a_line_range() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("file.txt"), "one\ntwo\nthree\n").expect("file");
        let context = context(directory.path());

        let result = ReadFileTool
            .execute(
                json!({"path": "file.txt", "start_line": 2, "end_line": 3}),
                &context,
            )
            .await
            .expect("read");
        assert_eq!(result.content, "two\nthree");
        assert_eq!(result.metadata["total_lines"], 3);
        assert_eq!(result.metadata["lines"], 2);
        assert_eq!(result.metadata["has_more"], false);

        let partial = ReadFileTool
            .execute(
                json!({"path": "file.txt", "start_line": 2, "end_line": 2}),
                &context,
            )
            .await
            .expect("partial read");
        assert_eq!(partial.metadata["has_more"], true);
        assert_eq!(partial.metadata["next_start_line"], 3);
    }

    #[tokio::test]
    async fn project_policy_limits_writes_and_command_classes() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::create_dir_all(directory.path().join("src/generated")).expect("src");
        fs::create_dir_all(directory.path().join("docs")).expect("docs");
        fs::write(
            directory.path().join(".openbad/permissions.yaml"),
            r#"
allow_writes: true
allow_paths: ["src/**"]
deny_paths: ["src/generated/**"]
command_classes: ["read-only"]
"#,
        )
        .expect("policy");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;

        WriteFileTool
            .execute(
                json!({"path": "src/allowed.rs", "content": "pub fn ok() {}\n"}),
                &context,
            )
            .await
            .expect("allowed write");

        let blocked = WriteFileTool
            .execute(
                json!({"path": "docs/blocked.md", "content": "no\n"}),
                &context,
            )
            .await
            .expect_err("allowlist blocks docs");
        assert!(blocked.to_string().contains("allow_paths"));

        let generated = WriteFileTool
            .execute(
                json!({"path": "src/generated/blocked.rs", "content": "no\n"}),
                &context,
            )
            .await
            .expect_err("denylist blocks generated");
        assert!(generated.to_string().contains("deny_paths"));

        let denied_command = RunCommandTool
            .execute(json!({"command": "touch src/new-file"}), &context)
            .await
            .expect_err("write-workspace command denied");
        assert!(denied_command.to_string().contains("write-workspace"));

        RunCommandTool
            .execute(json!({"command": "ls src"}), &context)
            .await
            .expect("read-only command");
    }

    #[tokio::test]
    async fn recipes_run_and_failed_validation_rolls_back_writes() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(
            directory.path().join(".openbad/project.yaml"),
            r#"
name: Validation
recipes:
  check: grep -q GOOD src/value.rs
gates:
  release:
    description: Release checks
    checks: [check]
validators:
  - pattern: "src/**/*.rs"
    command: grep -q GOOD src/value.rs
"#,
        )
        .expect("manifest");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;

        let recipes = ListRecipesTool
            .execute(json!({}), &context)
            .await
            .expect("list recipes");
        assert!(recipes.content.contains("check"));
        let gates = ListGatesTool
            .execute(json!({}), &context)
            .await
            .expect("list gates");
        assert!(gates.content.contains("release"));

        WriteFileTool
            .execute(
                json!({"path": "src/value.rs", "content": "GOOD\n"}),
                &context,
            )
            .await
            .expect("valid write");
        let recipe = RunRecipeTool
            .execute(json!({"name": "check"}), &context)
            .await
            .expect("recipe");
        assert!(recipe.content.contains("passed"));
        assert_eq!(recipe.metadata["success"], true);

        ReadFileTool
            .execute(json!({"path": "src/value.rs"}), &context)
            .await
            .expect("read before overwrite");
        let gate = RunGateTool
            .execute(json!({"name": "release"}), &context)
            .await
            .expect("gate");
        assert!(gate.content.contains("PASSED"));
        assert_eq!(gate.metadata["success"], true);
        let evidence = gate.metadata["checks"][0]["log_path"].as_str();
        assert!(evidence.is_some());

        let failed = WriteFileTool
            .execute(
                json!({"path": "src/value.rs", "content": "BAD\n"}),
                &context,
            )
            .await
            .expect_err("validator failure");
        assert!(failed.to_string().contains("validation failed"));
        assert_eq!(
            fs::read_to_string(directory.path().join("src/value.rs")).expect("rollback"),
            "GOOD\n"
        );

        let created = EditFileTool
            .execute(
                json!({"path": "src/value.rs", "old": "GOOD", "new": "BAD"}),
                &context,
            )
            .await
            .expect_err("edit validation failure");
        assert!(created.to_string().contains("validation failed"));
        assert_eq!(
            fs::read_to_string(directory.path().join("src/value.rs")).expect("rollback"),
            "GOOD\n"
        );
    }

    #[tokio::test]
    async fn web_fetch_extracts_text_caches_and_respects_network_policy() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("addr");
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buffer = [0_u8; 1024];
                let _ = std::io::Read::read(&mut stream, &mut buffer);
                let body = "<html><head><title>Docs</title><style>x{}</style></head><body><h1>API</h1><p>Use the &amp; operator.</p><script>bad()</script></body></html>";
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = std::io::Write::write_all(&mut stream, response.as_bytes());
            }
        });
        let directory = tempdir().expect("tempdir");
        let mut tool_context = context(directory.path());
        tool_context.allow_commands = true;
        let url = format!("http://{address}/docs");

        let fetched = WebFetchTool
            .execute(json!({"url": url}), &tool_context)
            .await
            .expect("fetch");
        assert!(fetched.content.contains("Docs"));
        assert!(fetched.content.contains("Use the & operator."));
        assert!(!fetched.content.contains("bad()"));
        assert_eq!(fetched.metadata["cached"], false);
        assert!(fetched.metadata["hash"].as_str().expect("hash").len() == 64);

        let cached = WebFetchTool
            .execute(json!({"url": url, "prefer_cache": true}), &tool_context)
            .await
            .expect("cached fetch");
        assert_eq!(cached.metadata["cached"], true);
        assert!(cached.content.contains("Use the & operator."));

        let denied_directory = tempdir().expect("denied");
        fs::create_dir_all(denied_directory.path().join(".openbad")).expect("openbad");
        fs::write(
            denied_directory.path().join(".openbad/permissions.yaml"),
            "network_hosts: [\"example.com\"]\n",
        )
        .expect("policy");
        let mut denied_context = context(denied_directory.path());
        denied_context.allow_commands = true;
        let denied = WebFetchTool
            .execute(json!({"url": url}), &denied_context)
            .await
            .expect_err("network policy");
        assert!(denied.to_string().contains("network_hosts"));
    }

    #[tokio::test]
    async fn external_cwd_manual_invariants_canon_exclusions_yaml_and_skills() {
        let directory = tempdir().expect("tempdir");
        let external = tempdir().expect("external");
        fs::write(external.path().join("marker.txt"), "ok\n").expect("marker");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(
            directory.path().join(".openbad/project.yaml"),
            format!(
                r#"
name: Demo
recipes:
  external:
    command: test -f marker.txt
    cwd: {}
invariants:
  - id: INV-1
    statement: External recipe works
    check: external
  - id: INV-2
    statement: Human attests this
    check: manual
"#,
                external.path().display()
            ),
        )
        .expect("manifest");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;

        let recipe = RunRecipeTool
            .execute(json!({"name": "external"}), &context)
            .await
            .expect("external recipe");
        assert!(recipe.content.contains("passed"));
        let denied = RunCommandTool
            .execute(
                json!({"command": "pwd", "cwd": external.path().to_string_lossy()}),
                &context,
            )
            .await
            .expect_err("external cwd requires flag");
        assert!(denied.to_string().contains("outside the workspace"));

        let invariants = CheckInvariantsTool
            .execute(json!({}), &context)
            .await
            .expect("invariants");
        assert!(invariants.content.contains("passed"));
        assert!(invariants.content.contains("attested"));

        CanonBanTool
            .execute(
                json!({"phrase": "two phials", "replacement": "one phial", "citation": "rewrite#3"}),
                &context,
            )
            .await
            .expect("canon ban");
        fs::write(
            directory.path().join("clean.md"),
            "This mentions two phials in prose.\n```\ntwo phials\n```\n",
        )
        .expect("clean");
        fs::write(directory.path().join("ignored.md"), "two phials\n").expect("ignored");
        let mut canon = openbad_workspace::load_canon(directory.path()).expect("canon");
        canon.paths_ignore.push("ignored.md".to_owned());
        openbad_workspace::save_canon(directory.path(), &canon).expect("save canon");
        let lint = CanonLintTool
            .execute(json!({}), &context)
            .await
            .expect("lint");
        assert!(lint.content.contains("clean.md"));
        assert!(!lint.content.contains("ignored.md"));

        fs::write(
            directory.path().join("config.yaml"),
            "name: Demo\nscope:\n  authoritative:\n    - src/**\n",
        )
        .expect("config");
        let yaml = YamlQueryTool
            .execute(
                json!({"path": "config.yaml", "filter": "name=Demo"}),
                &context,
            )
            .await
            .expect("yaml");
        assert!(yaml.content.contains("scope.authoritative.0=src/**"));

        let repo_dir = tempdir().expect("repo");
        fs::create_dir_all(repo_dir.path().join("docs")).expect("docs");
        fs::write(repo_dir.path().join("docs/file.md"), "body\n").expect("doc");
        fs::write(repo_dir.path().join("AGENTS.md"), "repo rule\n").expect("agent");
        context.project = Some(Arc::new(FakeProject {
            repos: vec![ProjectRepoRef {
                id: "repo-1".to_owned(),
                name: "reference".to_owned(),
                path: repo_dir.path().to_string_lossy().into_owned(),
                read_only: true,
            }],
            knowledge: Mutex::new(Vec::new()),
            contents: Mutex::new(HashMap::new()),
        }));
        let instructions = InstructionsForTool
            .execute(
                json!({"repo": "reference", "path": "docs/file.md"}),
                &context,
            )
            .await
            .expect("instructions");
        assert!(instructions.content.contains("repo rule"));

        WriteSkillTool
            .execute(
                json!({
                    "name": "Release",
                    "description": "Cut a release safely",
                    "steps": ["Run gate", "Tag"],
                    "verification": ["run_gate release"]
                }),
                &context,
            )
            .await
            .expect("skill");
        assert!(
            discover_skills(directory.path())
                .iter()
                .any(|skill| skill.name == "Release")
        );
    }

    #[tokio::test]
    async fn undo_restores_previous_write_and_instructions_are_scoped() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("docs")).expect("docs");
        fs::write(directory.path().join("file.txt"), "old\n").expect("file");
        fs::write(directory.path().join("AGENTS.md"), "root rule\n").expect("root");
        fs::write(directory.path().join("docs/AGENTS.md"), "docs rule\n").expect("docs");
        let mut context = context(directory.path());
        context.allow_writes = true;

        ReadFileTool
            .execute(json!({"path": "file.txt"}), &context)
            .await
            .expect("read");
        WriteFileTool
            .execute(json!({"path": "file.txt", "content": "new\n"}), &context)
            .await
            .expect("write");
        assert_eq!(
            fs::read_to_string(directory.path().join("file.txt")).expect("new"),
            "new\n"
        );
        UndoLastTool
            .execute(json!({}), &context)
            .await
            .expect("undo");
        assert_eq!(
            fs::read_to_string(directory.path().join("file.txt")).expect("old"),
            "old\n"
        );

        let scoped = InstructionsForTool
            .execute(json!({"path": "docs/file.txt"}), &context)
            .await
            .expect("instructions");
        let root_index = scoped.content.find("root rule").expect("root present");
        let docs_index = scoped.content.find("docs rule").expect("docs present");
        assert!(root_index < docs_index);
    }

    #[tokio::test]
    async fn scaffolding_gc_checkpoints_blast_radius_and_golden() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;

        ScaffoldProjectTool
            .execute(json!({"name": "Demo", "kind": "software"}), &context)
            .await
            .expect("scaffold");
        assert!(directory.path().join(".openbad/project.yaml").exists());
        assert!(directory.path().join("README.md").exists());
        assert!(directory.path().join("docs/README.md").exists());

        fs::write(
            directory.path().join("scratch/big.bin"),
            vec![0_u8; 2 * 1024 * 1024],
        )
        .expect("big");
        let gc = WorkspaceGcTool
            .execute(json!({"max_mb": 1}), &context)
            .await
            .expect("gc");
        assert!(gc.content.contains("scratch/big.bin"));
        WorkspaceGcTool
            .execute(
                json!({"max_mb": 1, "delete": true, "confirm": true}),
                &context,
            )
            .await
            .expect("gc delete");
        assert!(!directory.path().join("scratch/big.bin").exists());

        fs::write(
            directory.path().join(".openbad/todos.json"),
            r#"[{"id":"1","text":"Do it","state":"doing"}]"#,
        )
        .expect("todos");
        SessionCheckpointTool
            .execute(json!({"label": "before-test"}), &context)
            .await
            .expect("checkpoint");
        let resumed = SessionResumeTool
            .execute(json!({"label": "before-test"}), &context)
            .await
            .expect("resume");
        assert!(resumed.content.contains("Do it"));
        let listed = SessionResumeTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("before-test.json"));

        fs::write(directory.path().join("alpha.rs"), "pub fn alpha() {}\n").expect("alpha");
        fs::write(directory.path().join("beta.rs"), "use alpha;\n").expect("beta");
        let references = ReferencesToTool
            .execute(json!({"path": "alpha.rs"}), &context)
            .await
            .expect("references");
        assert!(references.content.contains("alpha.rs"));
        assert!(references.content.contains("beta.rs"));

        fs::write(directory.path().join("current.txt"), "new\n").expect("current");
        fs::write(directory.path().join("golden.txt"), "old\n").expect("golden");
        let compare = GoldenCompareTool
            .execute(
                json!({"path": "current.txt", "baseline": "golden.txt"}),
                &context,
            )
            .await
            .expect("compare");
        assert_eq!(compare.metadata["matches"], false);
        assert!(compare.content.contains("-old"));
        GoldenAcceptTool
            .execute(
                json!({"path": "current.txt", "baseline": "golden.txt", "confirm": true}),
                &context,
            )
            .await
            .expect("accept");
        assert_eq!(
            fs::read_to_string(directory.path().join("golden.txt")).expect("golden"),
            "new\n"
        );
    }

    #[tokio::test]
    async fn plan_tools_draft_update_verify_and_approve() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;
        let written = PlanWriteTool
            .execute(
                json!({
                    "goal": "Ship the feature",
                    "steps": [
                        {"id": "1", "text": "Implement it", "verify_with": "true"},
                        {"id": "2", "text": "Review it"}
                    ]
                }),
                &context,
            )
            .await
            .expect("plan");
        assert!(written.content.contains("draft"));
        let read = PlanReadTool
            .execute(json!({}), &context)
            .await
            .expect("read");
        assert!(read.content.contains("Implement it"));
        assert!(read.content.contains("do not execute"));
        let verified = PlanVerifyTool
            .execute(json!({"id": "1"}), &context)
            .await
            .expect("verify");
        assert_eq!(verified.metadata["success"], true);
        let updated = PlanUpdateStepTool
            .execute(
                json!({"id": "2", "status": "doing", "evidence": "review started"}),
                &context,
            )
            .await
            .expect("update");
        assert!(updated.content.contains("doing") || updated.content.contains("[doing]"));
        openbad_workspace::approve_plan(directory.path()).expect("approve");
        let approved = PlanReadTool
            .execute(json!({}), &context)
            .await
            .expect("approved");
        assert!(approved.content.contains("approved"));
        assert!(!approved.content.contains("do not execute"));
    }

    #[tokio::test]
    async fn background_jobs_and_diagnostic_parsing_work() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_commands = true;
        let started = JobStartTool
            .execute(
                json!({"command": "printf 'hello job\\n'; sleep 0.4"}),
                &context,
            )
            .await
            .expect("job");
        let id = started.metadata["id"].as_str().expect("id").to_owned();
        std::thread::sleep(std::time::Duration::from_millis(150));
        let logs = JobLogsTool
            .execute(json!({"id": id}), &context)
            .await
            .expect("logs");
        assert!(logs.content.contains("hello job"));
        let status = JobStatusTool
            .execute(json!({"id": id}), &context)
            .await
            .expect("status");
        assert!(status.content.contains(&id));
        let listed = JobListTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains(&id));

        let sleeper = JobStartTool
            .execute(json!({"command": "sleep 30"}), &context)
            .await
            .expect("sleeper");
        let sleeper_id = sleeper.metadata["id"].as_str().expect("id").to_owned();
        JobStopTool
            .execute(json!({"id": sleeper_id}), &context)
            .await
            .expect("stop");
        let stopped = JobStatusTool
            .execute(json!({"id": sleeper_id}), &context)
            .await
            .expect("stopped status");
        assert!(stopped.content.contains("exited"));

        let rust = parse_diagnostics(
            "rust",
            "error[E0308]: mismatched types\n  --> src/main.rs:10:5\n",
        );
        assert_eq!(rust[0]["path"], "src/main.rs");
        assert_eq!(rust[0]["line"], 10);
        let eslint = parse_diagnostics("eslint", "src/app.ts:4:9 error Unexpected token\n");
        assert_eq!(eslint[0]["severity"], "error");
    }

    #[tokio::test]
    async fn context_report_budget_claims_and_unknowns_work() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("big.txt"), "line\n".repeat(2_000)).expect("big");
        let mut context = context(directory.path());
        context.allow_writes = true;
        let read = ReadFileTool
            .execute(json!({"path": "big.txt", "budget_tokens": 200}), &context)
            .await
            .expect("budgeted read");
        assert_eq!(read.metadata["truncated"], true);
        assert_eq!(read.metadata["max_bytes"], 800);

        RecordClaimTool
            .execute(
                json!({
                    "claim": "Tests pass",
                    "confidence": "executed",
                    "evidence": ["cargo test"],
                    "how_to_check": "cargo test --workspace"
                }),
                &context,
            )
            .await
            .expect("claim");
        let claims = ListClaimsTool
            .execute(json!({}), &context)
            .await
            .expect("claims");
        assert!(claims.content.contains("executed"));

        RecordUnknownTool
            .execute(
                json!({"question": "Does the release gate include packaging?"}),
                &context,
            )
            .await
            .expect("unknown");
        let unknowns = ListUnknownsTool
            .execute(json!({}), &context)
            .await
            .expect("unknowns");
        assert!(unknowns.content.contains("release gate"));

        let report = ContextReportTool
            .execute(json!({}), &context)
            .await
            .expect("report");
        assert!(report.content.contains("loaded files: 1"));
        assert!(report.content.contains("claims: 1"));
        assert_eq!(report.metadata["unknowns"], 1);
    }

    #[tokio::test]
    async fn archive_diffs_and_file_citations_work() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("a.txt"), "one\n").expect("a");
        fs::write(directory.path().join("b.txt"), "two\n").expect("b");
        let zip_path = directory.path().join("bundle.zip");
        {
            let file = fs::File::create(&zip_path).expect("zip");
            let mut writer = zip::ZipWriter::new(file);
            let options = zip::write::SimpleFileOptions::default();
            writer
                .start_file("nested/readme.md", options)
                .expect("entry");
            std::io::Write::write_all(&mut writer, b"archive hello\n").expect("write");
            writer.finish().expect("finish");
        }
        let context = context(directory.path());

        let listed = ArchiveListTool
            .execute(json!({"path": "bundle.zip"}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("nested/readme.md"));
        let read = ArchiveReadTool
            .execute(
                json!({"path": "bundle.zip", "entry": "nested/readme.md"}),
                &context,
            )
            .await
            .expect("read");
        assert!(read.content.contains("archive hello"));

        let diff = DiffFilesTool
            .execute(json!({"a": "a.txt", "b": "b.txt"}), &context)
            .await
            .expect("diff");
        assert!(diff.content.contains("-one"));
        assert!(diff.content.contains("+two"));

        let citation = FileCitationTool
            .execute(
                json!({"path": "a.txt", "start_line": 1, "end_line": 1}),
                &context,
            )
            .await
            .expect("citation");
        assert!(citation.content.contains("a.txt@"));
        assert!(citation.content.contains("L1-L1"));
    }

    #[tokio::test]
    async fn previews_snapshots_secret_screening_and_assertions_work() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("a.txt"), "one\n").expect("a");
        fs::create_dir_all(directory.path().join("docs")).expect("docs");
        fs::write(
            directory.path().join("docs/guide.md"),
            "[missing](missing.md)\n",
        )
        .expect("guide");
        fs::write(
            directory.path().join("schema.json"),
            r#"{"name":"x","items":[]}"#,
        )
        .expect("schema");
        let mut context = context(directory.path());
        context.allow_writes = true;

        let preview = PreviewWriteTool
            .execute(json!({"path": "a.txt", "content": "one\ntwo\n"}), &context)
            .await
            .expect("preview");
        assert!(preview.content.contains("+two"));
        assert_eq!(
            fs::read_to_string(directory.path().join("a.txt")).expect("untouched"),
            "one\n"
        );

        let blocked = WriteFileTool
            .execute(
                json!({"path": "secret.txt", "content": "sk-123456789012345678901234"}),
                &context,
            )
            .await
            .expect_err("secret");
        assert!(blocked.to_string().contains("API key"));
        assert!(!directory.path().join("secret.txt").exists());

        SnapshotCreateTool
            .execute(json!({"label": "before"}), &context)
            .await
            .expect("snapshot");
        fs::write(directory.path().join("a.txt"), "changed\n").expect("change");
        let listed = SnapshotListTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("before"));
        SnapshotRestoreTool
            .execute(json!({"label": "before", "confirm": true}), &context)
            .await
            .expect("restore");
        assert_eq!(
            fs::read_to_string(directory.path().join("a.txt")).expect("restored"),
            "one\n"
        );

        let count = AssertProjectTool
            .execute(
                json!({"action": "count", "pattern": "*.txt", "expected": 1}),
                &context,
            )
            .await
            .expect("count");
        assert_eq!(count.metadata["success"], true);
        let schema = AssertProjectTool
            .execute(
                json!({"action": "schema", "path": "schema.json", "required": ["name", "items"]}),
                &context,
            )
            .await
            .expect("schema");
        assert_eq!(schema.metadata["success"], true);
        let links = AssertProjectTool
            .execute(json!({"action": "links"}), &context)
            .await
            .expect("links");
        assert_eq!(links.metadata["success"], false);
        assert!(links.content.contains("missing.md"));
    }

    #[tokio::test]
    async fn git_tools_commit_branch_and_inspect_a_repository() {
        let directory = tempdir().expect("tempdir");
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .args(args)
                .current_dir(directory.path())
                .status()
                .expect("git")
        };
        assert!(run(&["init"]).success());
        assert!(run(&["config", "user.email", "test@example.com"]).success());
        assert!(run(&["config", "user.name", "Test"]).success());
        fs::write(directory.path().join("file.txt"), "hello\n").expect("file");
        let mut context = context(directory.path());
        context.allow_commands = true;

        let health = RepoHealthTool
            .execute(json!({}), &context)
            .await
            .expect("health");
        assert!(health.content.contains("file.txt"));
        let commit = GitCommitTool
            .execute(
                json!({"message": "initial commit", "paths": ["file.txt"]}),
                &context,
            )
            .await
            .expect("commit");
        assert_eq!(commit.metadata["commit"]["success"], true);
        let log = GitLogTool
            .execute(json!({"limit": 5}), &context)
            .await
            .expect("log");
        assert!(log.content.contains("initial commit"));
        GitBranchTool
            .execute(json!({"name": "feature"}), &context)
            .await
            .expect("branch");
        GitCheckoutTool
            .execute(json!({"branch": "feature"}), &context)
            .await
            .expect("checkout");
        let context_info = GitCommitContextTool
            .execute(json!({}), &context)
            .await
            .expect("context");
        assert!(context_info.content.contains("feature"));
        let worktrees = GitWorktreeTool
            .execute(json!({"action": "list"}), &context)
            .await
            .expect("worktrees");
        assert!(worktrees.metadata["success"].as_bool().unwrap_or(true));
        let denied = GithubIssueTool
            .execute(json!({"action": "close", "number": 1}), &context)
            .await
            .expect_err("close requires confirm");
        assert!(denied.to_string().contains("confirm"));
    }

    #[tokio::test]
    async fn typed_knowledge_review_supersede_and_markdown_round_trip() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.project = Some(Arc::new(FakeProject {
            repos: Vec::new(),
            knowledge: Mutex::new(Vec::new()),
            contents: Mutex::new(HashMap::new()),
        }));

        let added = AddKnowledgeTool
            .execute(
                json!({
                    "title": "Indent with tabs",
                    "content": "Use tabs for indentation in this project.",
                    "kind": "decision",
                    "confidence": "verified"
                }),
                &context,
            )
            .await
            .expect("typed add");
        assert!(added.content.contains("decision"));
        assert_eq!(added.metadata["review_status"], "proposed");

        let reviewed = ReviewKnowledgeTool
            .execute(json!({"id": "knowledge-1", "status": "accepted"}), &context)
            .await
            .expect("review");
        assert!(reviewed.content.contains("accepted"));

        let conflict = AddKnowledgeTool
            .execute(
                json!({
                    "title": "Indent with spaces",
                    "content": "Never use tabs for indentation in this project."
                }),
                &context,
            )
            .await
            .expect_err("contradiction");
        assert!(conflict.to_string().contains("supersedes"));

        AddKnowledgeTool
            .execute(
                json!({
                    "title": "Indent with spaces",
                    "content": "Use spaces for indentation in this project.",
                    "kind": "decision",
                    "supersedes": "knowledge-1",
                    "review_status": "accepted"
                }),
                &context,
            )
            .await
            .expect("supersede");

        let updated = UpdateKnowledgeTool
            .execute(
                json!({"id": "knowledge-1", "kind": "gotcha", "tags": ["style"]}),
                &context,
            )
            .await
            .expect("typed update");
        assert!(updated.content.contains("gotcha"));

        let exported = ExportKnowledgeTool
            .execute(json!({}), &context)
            .await
            .expect("export");
        assert!(exported.content.contains("exported"));
        let files = fs::read_dir(directory.path().join("knowledge"))
            .expect("knowledge dir")
            .filter_map(Result::ok)
            .count();
        assert!(files > 0);
        let imported = ImportKnowledgeTool
            .execute(json!({}), &context)
            .await
            .expect("import");
        assert!(imported.content.contains("imported"));
    }

    #[tokio::test]
    async fn canon_glossary_adr_yaml_and_invariants_work() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(directory.path().join("src/main.rs"), "pub fn main() {}\n").expect("main");
        fs::write(directory.path().join("docs.md"), "This says two phials.\n").expect("docs");
        fs::write(
            directory.path().join("cards.yaml"),
            "---\n_id: card-a\nname: Alpha\ntype: card\n---\n_id: card-b\nname: Beta\ntype: pack\n",
        )
        .expect("yaml");
        fs::write(
            directory.path().join(".openbad/canon.yaml"),
            r#"
banned_phrases:
  - phrase: two phials
    replacement: one phial
    citation: rewrite#ch3
"#,
        )
        .expect("canon");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;

        let added = CanonAddTool
            .execute(
                json!({"id": "CANON-1", "statement": "The case has one phial.", "evidence": ["rewrite#ch3"]}),
                &context,
            )
            .await
            .expect("canon add");
        assert!(added.content.contains("CANON-1"));
        let queried = CanonQueryTool
            .execute(json!({"query": "phial"}), &context)
            .await
            .expect("canon query");
        assert!(queried.content.contains("one phial"));
        let lint = CanonLintTool
            .execute(json!({"paths": ["docs.md"]}), &context)
            .await
            .expect("canon lint");
        assert!(lint.content.contains("two phials"));
        assert!(lint.content.contains("one phial"));

        GlossaryAddTool
            .execute(
                json!({"term": "CT", "definition": "Containment Truth", "aliases": ["containment truth"]}),
                &context,
            )
            .await
            .expect("glossary add");
        let lookup = GlossaryLookupTool
            .execute(json!({"query": "containment"}), &context)
            .await
            .expect("glossary lookup");
        assert!(lookup.content.contains("CT"));

        let adr = AdrWriteTool
            .execute(
                json!({
                    "title": "Use SQLite",
                    "context": "Desktop storage",
                    "decision": "Use SQLite with a single writer",
                    "consequences": "Simple local durability",
                    "status": "accepted"
                }),
                &context,
            )
            .await
            .expect("adr write");
        assert!(adr.content.contains("0001"));
        let adrs = AdrListTool
            .execute(json!({}), &context)
            .await
            .expect("adr list");
        assert!(adrs.content.contains("0001-use-sqlite.md"));
        let adr_read = AdrReadTool
            .execute(json!({"id": "0001"}), &context)
            .await
            .expect("adr read");
        assert!(adr_read.content.contains("single writer"));

        let yaml = YamlQueryTool
            .execute(
                json!({"path": "cards.yaml", "filter": "type=card"}),
                &context,
            )
            .await
            .expect("yaml query");
        assert!(yaml.content.contains("Alpha"));
        assert!(!yaml.content.contains("Beta"));

        fs::write(
            directory.path().join(".openbad/project.yaml"),
            r#"
invariants:
  - id: INV-1
    statement: Main exists
    check: test -f src/main.rs
"#,
        )
        .expect("manifest");
        let invariants = CheckInvariantsTool
            .execute(json!({}), &context)
            .await
            .expect("invariants");
        assert!(invariants.content.contains("passed"));
    }

    #[tokio::test]
    async fn structured_questions_queue_and_list() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        let asked = AskQuestionTool
            .execute(
                json!({
                    "question": "Which database should we use?",
                    "options": ["SQLite", "Postgres"],
                    "context": "storage choice"
                }),
                &context,
            )
            .await
            .expect("ask");
        assert!(asked.content.contains("queued question"));
        let id = asked.metadata["id"].as_str().expect("id").to_owned();
        let listed = ListQuestionsTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("PENDING"));
        assert!(listed.content.contains("SQLite"));
        openbad_workspace::answer_question(directory.path(), &id, "SQLite").expect("answer");
        let answered = ListQuestionsTool
            .execute(json!({}), &context)
            .await
            .expect("answered");
        assert!(answered.content.contains("ANSWERED: SQLite"));
    }

    #[tokio::test]
    async fn context_rules_exclude_reads_and_track_dead_ends() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("assets")).expect("assets");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(directory.path().join("assets/logo.txt"), "needle asset\n").expect("asset");
        fs::write(directory.path().join("src/main.rs"), "needle source\n").expect("source");
        let mut context = context(directory.path());
        context.allow_writes = true;

        ExcludeContextTool
            .execute(
                json!({"path": "assets/**", "note": "binary blobs"}),
                &context,
            )
            .await
            .expect("exclude");
        PinContextTool
            .execute(
                json!({"path": "src/**", "note": "authoritative code"}),
                &context,
            )
            .await
            .expect("pin");

        let blocked = ReadFileTool
            .execute(json!({"path": "assets/logo.txt"}), &context)
            .await
            .expect_err("excluded read");
        assert!(
            blocked
                .to_string()
                .contains("excluded by .openbad/context.yaml")
        );
        ReadFileTool
            .execute(json!({"path": "src/main.rs"}), &context)
            .await
            .expect("allowed read");

        let grep = GrepTool
            .execute(json!({"query": "needle", "case_sensitive": true}), &context)
            .await
            .expect("grep");
        assert!(grep.content.contains("src/main.rs"));
        assert!(!grep.content.contains("assets/logo.txt"));

        let rules = ListContextRulesTool
            .execute(json!({}), &context)
            .await
            .expect("rules");
        assert!(rules.content.contains("Pinned context"));
        assert!(rules.content.contains("Excluded context"));

        RecordDeadEndTool
            .execute(
                json!({
                    "pattern": "src/legacy/**",
                    "reason": "No parser entry point exists",
                    "hypothesis": "Legacy parser"
                }),
                &context,
            )
            .await
            .expect("dead end");
        let dead_ends = ListDeadEndsTool
            .execute(json!({"query": "legacy"}), &context)
            .await
            .expect("dead ends");
        assert!(dead_ends.content.contains("No parser entry point"));

        let map = ContextMapTool
            .execute(json!({}), &context)
            .await
            .expect("context map");
        assert!(map.content.contains("Holding 1 file"));
        assert!(map.content.contains("src/main.rs"));
        assert!(map.content.contains("Ruled out"));
        assert_eq!(map.metadata["dead_ends"], 1);

        ExcludeContextTool
            .execute(json!({"path": "assets/**", "remove": true}), &context)
            .await
            .expect("remove rule");
        ReadFileTool
            .execute(json!({"path": "assets/logo.txt"}), &context)
            .await
            .expect("allowed after removal");
    }

    #[tokio::test]
    async fn stats_batch_reads_outlines_and_trash_round_trip() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("docs")).expect("docs");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(
            directory.path().join("docs/design.md"),
            "# Title\n\nbody\n## Section\n",
        )
        .expect("markdown");
        fs::write(
            directory.path().join("config.yaml"),
            "name: openbad\nitems:\n  - _id: one\n    type: card\n",
        )
        .expect("yaml");
        fs::write(
            directory.path().join("src/lib.rs"),
            "pub fn alpha() {}\nstruct Beta;\n",
        )
        .expect("rust");
        let mut context = context(directory.path());
        context.allow_writes = true;

        let stat = StatFileTool
            .execute(json!({"path": "docs/design.md"}), &context)
            .await
            .expect("stat");
        assert_eq!(stat.metadata["lines"], 4);
        assert_eq!(stat.metadata["hash"].as_str().expect("hash").len(), 64);

        let batch = ReadFilesTool
            .execute(json!({"paths": ["docs/design.md", "missing.md"]}), &context)
            .await
            .expect("batch");
        assert!(batch.content.contains("# Title"));
        assert!(batch.content.contains("ERROR:"));
        assert_eq!(batch.metadata["files"].as_array().expect("files").len(), 2);

        let markdown = OutlineTool
            .execute(json!({"path": "docs/design.md"}), &context)
            .await
            .expect("markdown outline");
        assert!(markdown.content.contains("1: # Title"));
        assert!(markdown.content.contains("4: ## Section"));

        let yaml = OutlineTool
            .execute(json!({"path": "config.yaml"}), &context)
            .await
            .expect("yaml outline");
        assert!(yaml.content.contains("1: name: openbad"));
        assert!(yaml.content.contains("_id: one"));

        let rust = OutlineTool
            .execute(json!({"path": "src/lib.rs"}), &context)
            .await
            .expect("code outline");
        assert!(rust.content.contains("pub fn alpha"));
        assert!(rust.content.contains("struct Beta"));

        let deleted = DeleteFileTool
            .execute(json!({"path": "docs/design.md"}), &context)
            .await
            .expect("delete");
        assert!(!directory.path().join("docs/design.md").exists());
        let name = deleted.metadata["trash_name"]
            .as_str()
            .expect("trash name")
            .to_owned();
        assert!(
            ListTrashTool
                .execute(json!({}), &context)
                .await
                .expect("trash")
                .content
                .contains(&name)
        );
        RestoreTrashTool
            .execute(json!({"name": name}), &context)
            .await
            .expect("restore");
        assert!(directory.path().join("docs/design.md").exists());

        MoveFileTool
            .execute(
                json!({"from": "docs/design.md", "to": "docs/design-v2.md"}),
                &context,
            )
            .await
            .expect("move");
        assert!(directory.path().join("docs/design-v2.md").exists());
        assert_eq!(classify_command("ls -la"), CommandClass::ReadOnly);
        assert_eq!(
            classify_command("git reset --hard HEAD~1"),
            CommandClass::Destructive
        );
        assert_eq!(
            classify_command("curl https://example.com"),
            CommandClass::Network
        );
        assert_eq!(
            classify_command("sudo systemctl restart x"),
            CommandClass::Privileged
        );

        context.allow_commands = true;
        let denied = RunCommandTool
            .execute(json!({"command": "rm -rf target"}), &context)
            .await
            .expect_err("destructive command");
        assert!(denied.to_string().contains("confirm_destructive"));
    }

    #[tokio::test]
    async fn write_requires_approval_and_a_prior_read() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("file.txt"), "old").expect("file");
        let mut context = context(directory.path());

        let denied = WriteFileTool
            .execute(json!({"path": "file.txt", "content": "new"}), &context)
            .await;
        assert!(matches!(denied, Err(ToolError::ApprovalRequired(_))));

        context.allow_writes = true;
        let unread = WriteFileTool
            .execute(json!({"path": "file.txt", "content": "new"}), &context)
            .await;
        assert!(unread.is_err());

        ReadFileTool
            .execute(json!({"path": "file.txt"}), &context)
            .await
            .expect("read");
        WriteFileTool
            .execute(json!({"path": "file.txt", "content": "new"}), &context)
            .await
            .expect("write");
        assert_eq!(
            fs::read_to_string(directory.path().join("file.txt")).expect("read"),
            "new"
        );
    }

    #[tokio::test]
    async fn edit_file_replaces_exactly_one_match() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("file.txt"), "alpha beta alpha").expect("file");
        let mut context = context(directory.path());
        context.allow_writes = true;

        ReadFileTool
            .execute(json!({"path": "file.txt"}), &context)
            .await
            .expect("read");
        let ambiguous = EditFileTool
            .execute(
                json!({"path": "file.txt", "old": "alpha", "new": "gamma"}),
                &context,
            )
            .await;
        assert!(ambiguous.is_err());

        EditFileTool
            .execute(
                json!({"path": "file.txt", "old": "beta", "new": "delta"}),
                &context,
            )
            .await
            .expect("edit");
        assert_eq!(
            fs::read_to_string(directory.path().join("file.txt")).expect("read"),
            "alpha delta alpha"
        );
    }

    #[tokio::test]
    async fn list_and_grep_respect_the_workspace() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(directory.path().join("src/main.rs"), "fn main() {}\n").expect("file");
        let context = context(directory.path());

        let list = ListDirTool
            .execute(json!({"path": ".", "depth": 3}), &context)
            .await
            .expect("list");
        assert!(list.content.contains("src/main.rs"));

        let grep = GrepTool
            .execute(json!({"query": "fn main"}), &context)
            .await
            .expect("grep");
        assert!(grep.content.contains("src/main.rs:1"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn atomic_writes_preserve_existing_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempdir().expect("tempdir");
        let path = directory.path().join("script.sh");
        fs::write(&path, "#!/bin/sh\n").expect("script");
        fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).expect("chmod");

        atomic_write(&path, "#!/bin/sh\necho hi\n").expect("write");
        let mode = fs::metadata(&path).expect("metadata").permissions().mode() & 0o777;
        assert_eq!(mode, 0o755);

        let new_path = directory.path().join("new.txt");
        atomic_write(&new_path, "new").expect("new file");
        let mode = fs::metadata(&new_path)
            .expect("metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o644);
    }

    #[tokio::test]
    async fn run_command_requires_approval() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        let denied = RunCommandTool
            .execute(json!({"command": "printf hi"}), &context)
            .await;
        assert!(matches!(denied, Err(ToolError::ApprovalRequired(_))));

        context.allow_commands = true;
        let result = RunCommandTool
            .execute(json!({"command": "printf hi"}), &context)
            .await
            .expect("run");
        assert!(result.content.contains("hi"));
        assert!(result.content.contains("exit: 0"));
    }

    #[tokio::test]
    async fn run_command_drains_output_and_enforces_timeout() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_commands = true;

        let large = RunCommandTool
            .execute(
                json!({"command": "yes x | head -c 200000", "timeout_seconds": 10}),
                &context,
            )
            .await
            .expect("large output");
        assert!(large.content.contains("exit: 0"));
        assert!(large.content.contains("full output"));
        assert!(large.content.len() < 200_000);
        let log_path = large.metadata["log_path"].as_str().expect("log path");
        assert!(std::fs::metadata(log_path).expect("log").len() > 100_000);
        assert_eq!(large.metadata["success"], true);

        let started = std::time::Instant::now();
        let timed_out = RunCommandTool
            .execute(
                json!({"command": "sleep 5", "timeout_seconds": 1}),
                &context,
            )
            .await;
        assert!(matches!(timed_out, Err(ToolError::Timeout(1))));
        assert!(started.elapsed() < std::time::Duration::from_secs(4));
    }

    #[tokio::test]
    async fn repo_map_and_git_tools_work() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("file.txt"), "hello").expect("file");
        let context = context(directory.path());

        let map = RepoMapTool.execute(json!({}), &context).await.expect("map");
        assert!(map.content.contains("file.txt"));

        let status = GitStatusTool.execute(json!({}), &context).await;
        // The temp directory may not be a Git repository; either a clean
        // response or a Git error is acceptable, but the tool must not panic.
        assert!(status.is_ok());
    }

    #[tokio::test]
    async fn glob_and_symbol_tools_find_matching_code() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(
            directory.path().join("src/main.rs"),
            "pub struct Config {}\nfn main() {}\n",
        )
        .expect("rust");
        fs::write(
            directory.path().join("src/app.ts"),
            "export function startServer() {}\n",
        )
        .expect("typescript");
        fs::write(directory.path().join("README.md"), "fn not_code").expect("readme");
        let context = context(directory.path());

        let glob = GlobTool
            .execute(json!({"pattern": "*.rs"}), &context)
            .await
            .expect("glob");
        assert!(glob.content.contains("src/main.rs"));
        assert!(!glob.content.contains("README.md"));

        let symbols = FindSymbolsTool
            .execute(json!({"query": "config"}), &context)
            .await
            .expect("symbols");
        assert!(symbols.content.contains("src/main.rs:1: pub struct Config"));

        let functions = FindSymbolsTool
            .execute(json!({"query": "start", "kind": "function"}), &context)
            .await
            .expect("functions");
        assert!(functions.content.contains("startServer"));
    }

    struct FakeSearch;

    impl WorkspaceSearch for FakeSearch {
        fn search(&self, query: &str, _limit: usize) -> Result<Vec<WorkspaceSearchHit>, String> {
            Ok(vec![WorkspaceSearchHit {
                path: "src/indexed.rs".to_owned(),
                snippet: format!("matched {query}"),
            }])
        }

        fn find_symbols(
            &self,
            query: &str,
            _kind: Option<&str>,
            _limit: usize,
        ) -> Result<Vec<WorkspaceSymbolHit>, String> {
            Ok(vec![WorkspaceSymbolHit {
                path: "src/indexed.rs".to_owned(),
                name: query.to_owned(),
                kind: "fn".to_owned(),
                line: 9,
                signature: format!("fn {query}()"),
            }])
        }
    }

    type CheckpointSave = (String, bool, Option<Vec<u8>>);

    struct FakeCheckpoints {
        saves: Mutex<Vec<CheckpointSave>>,
    }

    impl CheckpointSink for FakeCheckpoints {
        fn save(
            &self,
            path: &str,
            existed: bool,
            content: Option<&[u8]>,
        ) -> Result<String, String> {
            self.saves.lock().expect("lock").push((
                path.to_owned(),
                existed,
                content.map(<[u8]>::to_vec),
            ));
            Ok(format!(
                "checkpoint-{}",
                self.saves.lock().expect("lock").len()
            ))
        }
    }

    #[tokio::test]
    async fn write_and_edit_tools_checkpoint_before_mutating() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("file.txt"), "alpha").expect("file");
        let mut context = context(directory.path());
        context.allow_writes = true;
        let checkpoints = Arc::new(FakeCheckpoints {
            saves: Mutex::new(Vec::new()),
        });
        context.checkpoints = Some(checkpoints.clone());

        ReadFileTool
            .execute(json!({"path": "file.txt"}), &context)
            .await
            .expect("read");
        EditFileTool
            .execute(
                json!({"path": "file.txt", "old": "alpha", "new": "beta"}),
                &context,
            )
            .await
            .expect("edit");
        WriteFileTool
            .execute(json!({"path": "new.txt", "content": "created"}), &context)
            .await
            .expect("write");

        let saves = checkpoints.saves.lock().expect("lock");
        assert_eq!(saves.len(), 2);
        assert_eq!(
            saves[0].0,
            directory.path().join("file.txt").to_string_lossy()
        );
        assert!(saves[0].1);
        assert_eq!(saves[0].2.as_deref(), Some(b"alpha".as_slice()));
        assert!(!saves[1].1);
        assert_eq!(saves[1].2, None);
    }

    #[tokio::test]
    async fn indexed_search_tools_prefer_the_workspace_index() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.search = Some(Arc::new(FakeSearch));

        let search = SearchCodeTool
            .execute(json!({"query": "needle"}), &context)
            .await
            .expect("search");
        assert!(search.content.contains("src/indexed.rs: matched needle"));

        let symbols = FindSymbolsTool
            .execute(json!({"query": "lookup"}), &context)
            .await
            .expect("symbols");
        assert!(symbols.content.contains("src/indexed.rs:9: fn lookup()"));
    }

    struct FakeMemory {
        entries: Mutex<Vec<MemoryEntry>>,
    }

    impl MemoryStore for FakeMemory {
        fn remember(
            &self,
            content: &str,
            tags: Vec<String>,
            pinned: bool,
        ) -> Result<MemoryEntry, String> {
            self.remember_scoped(
                content,
                tags,
                pinned,
                MemoryWriteOptions {
                    scope: "project".to_owned(),
                    author: "agent".to_owned(),
                    ..MemoryWriteOptions::default()
                },
            )
        }

        fn remember_scoped(
            &self,
            content: &str,
            tags: Vec<String>,
            pinned: bool,
            options: MemoryWriteOptions,
        ) -> Result<MemoryEntry, String> {
            let entry = MemoryEntry {
                id: "memory-1".to_owned(),
                content: content.to_owned(),
                tags,
                pinned,
                scope: options.scope,
                author: options.author,
                origin_turn: options.origin_turn,
                origin_task: options.origin_task,
                supersedes: options.supersedes,
                expires_at: options.expires_at,
            };
            self.entries.lock().expect("lock").push(entry.clone());
            Ok(entry)
        }

        fn update(
            &self,
            id: &str,
            content: Option<&str>,
            tags: Option<Vec<String>>,
            pinned: Option<bool>,
            scope: Option<&str>,
        ) -> Result<MemoryEntry, String> {
            let mut entries = self.entries.lock().expect("lock");
            let entry = entries
                .iter_mut()
                .find(|entry| entry.id == id)
                .ok_or_else(|| format!("unknown memory: {id}"))?;
            if let Some(content) = content {
                entry.content = content.to_owned();
            }
            if let Some(tags) = tags {
                entry.tags = tags;
            }
            if let Some(pinned) = pinned {
                entry.pinned = pinned;
            }
            if let Some(scope) = scope {
                entry.scope = scope.to_owned();
            }
            Ok(entry.clone())
        }

        fn recall(&self, query: Option<&str>, limit: usize) -> Result<Vec<MemoryEntry>, String> {
            let query = query.unwrap_or_default().to_lowercase();
            Ok(self
                .entries
                .lock()
                .expect("lock")
                .iter()
                .filter(|entry| entry.content.to_lowercase().contains(&query))
                .take(limit)
                .cloned()
                .collect())
        }

        fn forget(&self, id: &str) -> Result<(), String> {
            self.entries
                .lock()
                .expect("lock")
                .retain(|entry| entry.id != id);
            Ok(())
        }
    }

    #[tokio::test]
    async fn remember_and_recall_use_workspace_memory() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.memory = Some(Arc::new(FakeMemory {
            entries: Mutex::new(Vec::new()),
        }));

        let remembered = RememberTool
            .execute(
                json!({
                    "content": "Keep the prompt prefix immutable",
                    "tags": ["cache"],
                    "pinned": true
                }),
                &context,
            )
            .await
            .expect("remember");
        assert!(remembered.content.contains("pinned"));
        assert!(
            remembered
                .content
                .contains("Keep the prompt prefix immutable")
        );
        assert!(remembered.content.contains("(project)"));
        assert_eq!(remembered.metadata["scope"], "project");

        let conflict = RememberTool
            .execute(
                json!({"content": "Never keep the prompt prefix immutable"}),
                &context,
            )
            .await
            .expect_err("contradiction requires supersedes");
        assert!(conflict.to_string().contains("supersedes"));

        let superseding = RememberTool
            .execute(
                json!({
                    "content": "Never keep the prompt prefix immutable",
                    "supersedes": "memory-1",
                    "scope": "session"
                }),
                &context,
            )
            .await
            .expect("explicit supersede");
        assert_eq!(superseding.metadata["supersedes"], "memory-1");

        let updated = UpdateMemoryTool
            .execute(
                json!({"id": "memory-1", "scope": "global", "tags": ["cache", "prompt"]}),
                &context,
            )
            .await
            .expect("update memory");
        assert!(updated.content.contains("global"));

        let recalled = RecallTool
            .execute(json!({"query": "immutable"}), &context)
            .await
            .expect("recall");
        assert!(
            recalled
                .content
                .contains("Keep the prompt prefix immutable")
        );
        assert!(recalled.content.contains("cache"));

        let listed = ListMemoriesTool
            .execute(json!({"max_results": 10}), &context)
            .await
            .expect("list memories");
        assert!(listed.content.contains("memory-1"));
        assert!(listed.content.contains("Keep the prompt prefix immutable"));

        ForgetMemoryTool
            .execute(json!({"id": "memory-1"}), &context)
            .await
            .expect("forget memory");
        assert!(
            ListMemoriesTool
                .execute(json!({}), &context)
                .await
                .expect("list after forget")
                .content
                .contains("no saved memories")
        );
    }

    struct FakeProject {
        repos: Vec<ProjectRepoRef>,
        knowledge: Mutex<Vec<KnowledgeEntryRef>>,
        contents: Mutex<HashMap<String, String>>,
    }

    impl ProjectAreas for FakeProject {
        fn list_repos(&self) -> Result<Vec<ProjectRepoRef>, String> {
            Ok(self.repos.clone())
        }

        fn list_knowledge(&self) -> Result<Vec<KnowledgeEntryRef>, String> {
            Ok(self.knowledge.lock().expect("lock").clone())
        }

        fn add_knowledge(
            &self,
            title: &str,
            content: &str,
            source: Option<&str>,
            _tags: Vec<String>,
            confidence: &str,
        ) -> Result<KnowledgeEntryRef, String> {
            let entry = KnowledgeEntryRef {
                id: "knowledge-1".to_owned(),
                title: title.to_owned(),
                kind: "note".to_owned(),
                source: source.map(str::to_owned),
                chunks: (content.chars().count() / 1_400 + 1) as u64,
                confidence: confidence.to_owned(),
                stale: false,
                supersedes: None,
                review_status: "accepted".to_owned(),
            };
            self.knowledge.lock().expect("lock").push(entry.clone());
            self.contents
                .lock()
                .expect("contents")
                .insert(entry.id.clone(), content.to_owned());
            Ok(entry)
        }

        fn add_knowledge_detailed(
            &self,
            title: &str,
            content: &str,
            kind: &str,
            source: Option<&str>,
            _tags: Vec<String>,
            confidence: &str,
            supersedes: Option<&str>,
            review_status: &str,
        ) -> Result<KnowledgeEntryRef, String> {
            let entry = KnowledgeEntryRef {
                id: "knowledge-1".to_owned(),
                title: title.to_owned(),
                kind: kind.to_owned(),
                source: source.map(str::to_owned),
                chunks: (content.chars().count() / 1_400 + 1) as u64,
                confidence: confidence.to_owned(),
                stale: false,
                supersedes: supersedes.map(str::to_owned),
                review_status: review_status.to_owned(),
            };
            self.knowledge.lock().expect("lock").push(entry.clone());
            self.contents
                .lock()
                .expect("contents")
                .insert(entry.id.clone(), content.to_owned());
            Ok(entry)
        }

        fn review_knowledge(&self, id: &str, status: &str) -> Result<KnowledgeDocumentRef, String> {
            let mut entries = self.knowledge.lock().expect("lock");
            let entry = entries
                .iter_mut()
                .find(|entry| entry.id == id)
                .ok_or_else(|| format!("unknown knowledge document: {id}"))?;
            entry.review_status = status.to_owned();
            Ok(KnowledgeDocumentRef {
                id: entry.id.clone(),
                title: entry.title.clone(),
                kind: entry.kind.clone(),
                source: entry.source.clone(),
                tags: vec!["stored".to_owned()],
                content: "stored knowledge content".to_owned(),
                chunks: entry.chunks,
                confidence: entry.confidence.clone(),
                stale: false,
                supersedes: entry.supersedes.clone(),
                review_status: entry.review_status.clone(),
            })
        }

        fn get_knowledge(&self, id: &str) -> Result<Option<KnowledgeDocumentRef>, String> {
            Ok(self
                .knowledge
                .lock()
                .expect("lock")
                .iter()
                .find(|entry| entry.id == id)
                .map(|entry| KnowledgeDocumentRef {
                    id: entry.id.clone(),
                    title: entry.title.clone(),
                    kind: entry.kind.clone(),
                    source: entry.source.clone(),
                    tags: vec!["stored".to_owned()],
                    content: self
                        .contents
                        .lock()
                        .expect("contents")
                        .get(&entry.id)
                        .cloned()
                        .unwrap_or_else(|| "stored knowledge content".to_owned()),
                    chunks: entry.chunks,
                    confidence: entry.confidence.clone(),
                    stale: false,
                    supersedes: entry.supersedes.clone(),
                    review_status: entry.review_status.clone(),
                }))
        }

        fn update_knowledge(
            &self,
            id: &str,
            title: Option<&str>,
            content: Option<&str>,
            _source: Option<&str>,
            _tags: Option<Vec<String>>,
            confidence: Option<&str>,
        ) -> Result<KnowledgeDocumentRef, String> {
            let mut entries = self.knowledge.lock().expect("lock");
            let entry = entries
                .iter_mut()
                .find(|entry| entry.id == id)
                .ok_or_else(|| format!("unknown knowledge document: {id}"))?;
            if let Some(title) = title {
                entry.title = title.to_owned();
            }
            if let Some(confidence) = confidence {
                entry.confidence = confidence.to_owned();
            }
            if let Some(content) = content {
                self.contents
                    .lock()
                    .expect("contents")
                    .insert(entry.id.clone(), content.to_owned());
            }
            Ok(KnowledgeDocumentRef {
                id: entry.id.clone(),
                title: entry.title.clone(),
                kind: entry.kind.clone(),
                source: entry.source.clone(),
                tags: vec!["stored".to_owned()],
                content: content.unwrap_or("stored knowledge content").to_owned(),
                chunks: entry.chunks,
                confidence: entry.confidence.clone(),
                stale: false,
                supersedes: entry.supersedes.clone(),
                review_status: entry.review_status.clone(),
            })
        }

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
            let mut entries = self.knowledge.lock().expect("lock");
            let entry = entries
                .iter_mut()
                .find(|entry| entry.id == id)
                .ok_or_else(|| format!("unknown knowledge document: {id}"))?;
            if let Some(title) = title {
                entry.title = title.to_owned();
            }
            if let Some(kind) = kind {
                entry.kind = kind.to_owned();
            }
            if let Some(source) = source {
                entry.source = Some(source.to_owned());
            }
            if let Some(confidence) = confidence {
                entry.confidence = confidence.to_owned();
            }
            if let Some(supersedes) = supersedes {
                entry.supersedes = Some(supersedes.to_owned());
            }
            if let Some(review_status) = review_status {
                entry.review_status = review_status.to_owned();
            }
            if let Some(content) = content {
                self.contents
                    .lock()
                    .expect("contents")
                    .insert(entry.id.clone(), content.to_owned());
            }
            let _ = tags;
            Ok(KnowledgeDocumentRef {
                id: entry.id.clone(),
                title: entry.title.clone(),
                kind: entry.kind.clone(),
                source: entry.source.clone(),
                tags: vec!["stored".to_owned()],
                content: content.unwrap_or("stored knowledge content").to_owned(),
                chunks: entry.chunks,
                confidence: entry.confidence.clone(),
                stale: false,
                supersedes: entry.supersedes.clone(),
                review_status: entry.review_status.clone(),
            })
        }

        fn delete_knowledge(&self, id: &str) -> Result<(), String> {
            self.knowledge
                .lock()
                .expect("lock")
                .retain(|entry| entry.id != id);
            Ok(())
        }

        fn retrieve(&self, query: &str, _limit: usize) -> Result<Vec<ProjectHit>, String> {
            Ok(vec![ProjectHit {
                source: "knowledge".to_owned(),
                title: "Architecture".to_owned(),
                snippet: format!("matched {query}"),
                locator: "knowledge-1#0".to_owned(),
                score: 1.0,
            }])
        }
    }

    #[tokio::test]
    async fn project_tools_search_add_and_read_linked_repositories() {
        let directory = tempdir().expect("tempdir");
        let repo_dir = tempdir().expect("repo");
        fs::create_dir_all(repo_dir.path().join("docs")).expect("docs");
        fs::create_dir_all(repo_dir.path().join("src/packs")).expect("packs");
        fs::write(repo_dir.path().join("docs/design.md"), "one\nTwo\nthree\n").expect("file");
        fs::write(repo_dir.path().join("src/packs/a.yaml"), "name: one\n").expect("a");
        fs::write(repo_dir.path().join("src/packs/b.yaml"), "name: two\n").expect("b");
        let mut context = context(directory.path());
        context.project = Some(Arc::new(FakeProject {
            repos: vec![ProjectRepoRef {
                id: "repo-1".to_owned(),
                name: "reference".to_owned(),
                path: repo_dir.path().to_string_lossy().into_owned(),
                read_only: true,
            }],
            knowledge: Mutex::new(Vec::new()),
            contents: Mutex::new(HashMap::new()),
        }));

        let hits = SearchProjectTool
            .execute(json!({"query": "cache"}), &context)
            .await
            .expect("search");
        assert!(hits.content.contains("knowledge-1#0"));
        assert!(hits.content.contains("matched cache"));

        let added = AddKnowledgeTool
            .execute(
                json!({"title": "Decision", "content": "Use the local index.", "confidence": "verified"}),
                &context,
            )
            .await
            .expect("add knowledge");
        assert!(added.content.contains("Decision"));
        assert!(added.content.contains("confidence verified"));
        assert_eq!(added.metadata["chunks"], 1);

        let listed = ListKnowledgeTool
            .execute(json!({}), &context)
            .await
            .expect("list knowledge");
        assert!(listed.content.contains("Decision"));
        assert!(listed.content.contains("confidence verified"));

        let fetched = GetKnowledgeTool
            .execute(json!({"id": "knowledge-1"}), &context)
            .await
            .expect("get knowledge");
        assert!(fetched.content.contains("Use the local index."));

        let updated = UpdateKnowledgeTool
            .execute(
                json!({"id": "knowledge-1", "title": "Decision v2", "content": "updated"}),
                &context,
            )
            .await
            .expect("update knowledge");
        assert!(updated.content.contains("Decision v2"));
        let recalibrated = UpdateKnowledgeTool
            .execute(
                json!({"id": "knowledge-1", "confidence": "uncertain"}),
                &context,
            )
            .await
            .expect("update confidence");
        assert!(recalibrated.content.contains("uncertain"));

        let deleted = DeleteKnowledgeTool
            .execute(json!({"id": "knowledge-1"}), &context)
            .await
            .expect("delete knowledge");
        assert!(deleted.content.contains("Deleted"));
        assert!(
            ListKnowledgeTool
                .execute(json!({}), &context)
                .await
                .expect("list after delete")
                .content
                .contains("no knowledge documents")
        );

        let repo_stat = StatRepoFileTool
            .execute(
                json!({"repo": "reference", "path": "docs/design.md"}),
                &context,
            )
            .await
            .expect("repo stat");
        assert_eq!(repo_stat.metadata["lines"], 3);

        let repo_batch = ReadRepoFilesTool
            .execute(
                json!({"repo": "reference", "paths": ["docs/design.md", "missing.md"]}),
                &context,
            )
            .await
            .expect("repo batch");
        assert!(repo_batch.content.contains("one"));
        assert!(repo_batch.content.contains("ERROR:"));

        let repo_outline = OutlineTool
            .execute(
                json!({"repo": "reference", "path": "docs/design.md"}),
                &context,
            )
            .await
            .expect("repo outline");
        assert!(repo_outline.content.contains("no outline entries"));

        let sources = ListProjectSourcesTool
            .execute(json!({}), &context)
            .await
            .expect("sources");
        assert!(sources.content.contains("reference"));
        assert!(sources.content.contains("Knowledge: empty"));

        let repo_file = ReadRepoFileTool
            .execute(
                json!({"repo": "repo-1", "path": "docs/design.md", "start_line": 2}),
                &context,
            )
            .await
            .expect("read repo");
        assert!(repo_file.content.contains("Two"));
        assert!(!repo_file.content.contains("one"));
        assert_eq!(repo_file.metadata["total_lines"], 3);
        assert_eq!(repo_file.metadata["has_more"], false);

        let listing = ListRepoDirTool
            .execute(json!({"repo": "reference", "depth": 3}), &context)
            .await
            .expect("list reference");
        assert!(listing.content.contains("docs/"));
        assert!(listing.content.contains("src/packs/a.yaml"));

        let globbed = GlobRepoTool
            .execute(
                json!({"repo": "repo-1", "pattern": "src/packs/*.yaml"}),
                &context,
            )
            .await
            .expect("glob reference");
        assert!(globbed.content.contains("src/packs/a.yaml"));
        assert!(globbed.content.contains("src/packs/b.yaml"));

        let grepped = GrepRepoTool
            .execute(
                json!({"repo": "repo-1", "query": "two", "max_results": 10}),
                &context,
            )
            .await
            .expect("grep reference");
        assert!(grepped.content.contains("docs/design.md:2"));

        let escaped = ReadRepoFileTool
            .execute(json!({"repo": "repo-1", "path": "../escape"}), &context)
            .await;
        assert!(escaped.is_err());

        let artifact = PresentArtifactTool
            .execute(
                json!({"kind": "web", "title": "Docs", "uri": "https://example.com"}),
                &context,
            )
            .await
            .expect("artifact");
        assert_eq!(artifact.metadata["openbad_artifact"]["kind"], json!("web"));
    }

    #[tokio::test]
    async fn manifest_tasks_scope_todos_and_handoff_work() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::create_dir_all(directory.path().join("archive")).expect("archive");
        fs::write(directory.path().join("archive/old.md"), "old").expect("old");
        fs::write(
            directory.path().join(".openbad/project.yaml"),
            "name: Demo\ncommands:\n  echo: \"printf hi\"\nscope:\n  historical: [\"archive/**\"]\n",
        )
        .expect("manifest");
        let mut context = context(directory.path());

        let manifest = ReadProjectManifestTool
            .execute(json!({}), &context)
            .await
            .expect("manifest");
        assert!(manifest.content.contains("echo"));

        let scope = ClassifyPathTool
            .execute(json!({"path": "archive/old.md"}), &context)
            .await
            .expect("scope");
        assert!(scope.content.contains("historical"));

        let denied = RunTaskTool.execute(json!({"name": "echo"}), &context).await;
        assert!(denied.is_err());
        context.allow_commands = true;
        let task = RunTaskTool
            .execute(json!({"name": "echo"}), &context)
            .await
            .expect("task");
        assert!(task.content.contains("hi"));

        context.allow_writes = true;
        ReadFileTool
            .execute(json!({"path": "archive/old.md"}), &context)
            .await
            .expect("read old");
        let edited = EditFileTool
            .execute(
                json!({"path": "archive/old.md", "old": "old", "new": "new"}),
                &context,
            )
            .await
            .expect("edit old");
        assert!(edited.content.contains("historical"));

        TodoWriteTool
            .execute(
                json!({"items": [{"id": "t1", "text": "Verify feature", "state": "todo", "done_when": "tests pass"}]}),
                &context,
            )
            .await
            .expect("todos");
        let todos = TodoReadTool
            .execute(json!({}), &context)
            .await
            .expect("read todos");
        assert!(todos.content.contains("Verify feature"));
        assert!(todos.content.contains("done when: tests pass"));

        WriteHandoffTool
            .execute(
                json!({"state": "halfway", "next_actions": ["run tests"]}),
                &context,
            )
            .await
            .expect("handoff");
        let handoff =
            fs::read_to_string(directory.path().join(".openbad/handoff.md")).expect("handoff file");
        assert!(handoff.contains("halfway"));
        assert!(handoff.contains("run tests"));
    }

    #[tokio::test]
    async fn discovers_and_reads_workspace_skills() {
        let directory = tempdir().expect("tempdir");
        let skill_dir = directory.path().join(".openbad/skills/rust-review");
        fs::create_dir_all(&skill_dir).expect("skill dir");
        fs::write(
            skill_dir.join("SKILL.md"),
            "---\nname: rust-review\ndescription: Review Rust changes\n---\n# Checklist\n- Run tests\n",
        )
        .expect("skill");
        let context = context(directory.path());

        let skills: Vec<Skill> = discover_skills(directory.path())
            .into_iter()
            .filter(|skill| !skill.builtin)
            .collect();
        assert_eq!(skills.len(), 1);
        assert_eq!(skills[0].name, "rust-review");
        assert_eq!(skills[0].description, "Review Rust changes");

        let listed = ListSkillsTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("rust-review: Review Rust changes"));

        let read = ReadSkillTool
            .execute(json!({"name": "rust-review"}), &context)
            .await
            .expect("read");
        assert!(read.content.contains("# Checklist"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn skill_discovery_rejects_symlinks_outside_the_workspace() {
        use std::os::unix::fs::symlink;

        let directory = tempdir().expect("tempdir");
        let outside = tempdir().expect("outside");
        let outside_skill = outside.path().join("secret.md");
        fs::write(&outside_skill, "---\nname: secret\n---\n").expect("secret");
        let skills_dir = directory.path().join(".openbad/skills");
        fs::create_dir_all(&skills_dir).expect("skills");
        symlink(&outside_skill, skills_dir.join("secret.md")).expect("symlink");

        assert!(
            discover_skills(directory.path())
                .iter()
                .all(|skill| skill.builtin)
        );
    }

    #[test]
    fn detects_test_commands_and_failure_lines() {
        let cargo = tempdir().expect("cargo");
        fs::write(
            cargo.path().join("Cargo.toml"),
            "[workspace]\nmembers = []\n",
        )
        .expect("manifest");
        assert_eq!(
            detect_test_command(cargo.path()).expect("cargo command"),
            ("cargo test --workspace".to_owned(), "cargo")
        );

        let node = tempdir().expect("node");
        fs::write(
            node.path().join("package.json"),
            r#"{"scripts":{"test":"vitest run"}}"#,
        )
        .expect("package");
        fs::write(node.path().join("pnpm-lock.yaml"), "").expect("lock");
        assert_eq!(
            detect_test_command(node.path()).expect("node command"),
            ("pnpm test".to_owned(), "node")
        );

        let empty = tempdir().expect("empty");
        assert!(detect_test_command(empty.path()).is_err());

        let summary = test_failure_summary(
            "ok\ntest result: FAILED. 2 passed; 1 failed\nTests: 1 failed, 2 passed\n",
        )
        .expect("summary");
        assert!(summary.contains("test result: FAILED"));
        assert!(summary.contains("Tests: 1 failed"));
    }

    #[tokio::test]
    async fn every_builtin_has_a_schema() {
        let registry = ToolRegistry::with_builtin_tools();
        let definitions = registry.definitions();
        assert!(definitions.len() >= 17);
        assert!(definitions.iter().all(|tool| tool.input_schema.is_object()));
        assert!(registry.openai_tools().iter().all(Value::is_object));
    }

    #[tokio::test]
    async fn builtin_skills_are_available_without_workspace_files() {
        let directory = tempdir().expect("tempdir");
        let context = context(directory.path());
        let skills = discover_skills(directory.path());
        let names: Vec<&str> = skills.iter().map(|skill| skill.name.as_str()).collect();
        for expected in [
            "github-workflow",
            "github-releases-pages",
            "plugin-development",
            "asciidoc-rendering",
        ] {
            assert!(names.contains(&expected), "missing skill {expected}");
        }
        assert!(skills.iter().all(|skill| skill.builtin));

        let loaded = ReadSkillTool
            .execute(json!({"name": "github-workflow"}), &context)
            .await
            .expect("read builtin skill");
        assert!(loaded.content.contains("area: \"pr\""));
        assert_eq!(loaded.metadata["builtin"], true);

        let listed = ListSkillsTool
            .execute(json!({}), &context)
            .await
            .expect("list skills");
        assert!(listed.content.contains("[builtin]"));
    }

    #[tokio::test]
    async fn workspace_skills_override_builtin_skills() {
        let directory = tempdir().expect("tempdir");
        let skill_dir = directory.path().join(".openbad/skills/github-workflow");
        fs::create_dir_all(&skill_dir).expect("skill dir");
        fs::write(
            skill_dir.join("SKILL.md"),
            "---\nname: github-workflow\ndescription: local override\n---\nlocal",
        )
        .expect("skill");
        let skills = discover_skills(directory.path());
        let skill = skills
            .iter()
            .find(|skill| skill.name == "github-workflow")
            .expect("skill");
        assert!(!skill.builtin);
        assert_eq!(skill.description, "local override");
    }

    #[test]
    fn github_commands_cover_common_workflows() {
        let issue = github_command(&json!({
            "area": "issue",
            "action": "create",
            "repository": "acme/repo",
            "title": "Bug",
            "body": "Details",
            "labels": ["bug"]
        }))
        .expect("issue command");
        assert!(issue.starts_with("gh issue create --repo 'acme/repo'"));
        assert!(issue.contains("--label 'bug'"));

        let review = github_command(&json!({
            "area": "pr",
            "action": "review",
            "number": 7,
            "event": "approve",
            "confirm": true
        }))
        .expect("review command");
        assert!(review.contains("gh pr review 7"));
        assert!(review.contains("--approve"));

        let merge = github_command(&json!({
            "area": "pr",
            "action": "merge",
            "number": 7
        }));
        assert!(matches!(merge, Err(ToolError::ApprovalRequired(_))));

        let update = github_command(&json!({
            "area": "issue",
            "action": "update",
            "repository": "acme/repo",
            "number": 9,
            "add_labels": ["triage"],
            "state": "closed",
            "confirm": true
        }))
        .expect("issue update");
        assert!(update.contains("gh issue edit 9"));
        assert!(update.contains("gh issue close 9"));

        let protect = github_command(&json!({
            "area": "branch",
            "action": "protect",
            "repository": "acme/repo",
            "branch": "main",
            "body": {"required_status_checks": {"strict": true, "contexts": ["ci"]}}
        }))
        .expect("protect command");
        assert!(protect.contains("--method PUT"));
        assert!(protect.contains("required_status_checks"));

        let pages = github_command(&json!({
            "area": "pages",
            "action": "build",
            "repository": "acme/repo"
        }))
        .expect("pages command");
        assert!(pages.contains("repos/acme/repo/pages/builds"));
        assert!(pages.contains("--method POST"));

        let api = github_command(&json!({
            "area": "api",
            "endpoint": "repos/acme/repo/issues",
            "fields": {"state": "open", "per_page": 10}
        }))
        .expect("api command");
        assert!(api.contains("gh api --method GET"));
        assert!(api.contains("-F 'per_page'=10"));

        let delete = github_command(&json!({
            "area": "api",
            "method": "DELETE",
            "endpoint": "repos/acme/repo/issues/1"
        }));
        assert!(matches!(delete, Err(ToolError::ApprovalRequired(_))));
    }

    #[test]
    fn github_rejects_paths_outside_the_workspace() {
        let upload = github_command(&json!({
            "area": "release",
            "action": "upload",
            "repository": "acme/repo",
            "tag": "v1.0.0",
            "files": ["/etc/passwd"]
        }));
        assert!(matches!(upload, Err(ToolError::InvalidArgument(_))));

        let traversal = github_command(&json!({
            "area": "release",
            "action": "create",
            "repository": "acme/repo",
            "tag": "v1.0.0",
            "files": ["../escape.zip"]
        }));
        assert!(matches!(traversal, Err(ToolError::InvalidArgument(_))));
    }

    #[tokio::test]
    async fn outside_access_grants_are_scoped_and_enforced() {
        let workspace = tempdir().expect("workspace");
        let outside = tempdir().expect("outside");
        let file = outside.path().join("notes.txt");
        fs::write(&file, "outside").expect("file");
        let mut context = context(workspace.path());
        context.allow_writes = true;

        let read = ReadFileTool
            .execute(json!({"path": file.to_string_lossy()}), &context)
            .await;
        assert!(matches!(read, Err(ToolError::OutsideRoot(_))));

        context.set_outside_grants(vec![OutsideGrant {
            id: "grant-1".to_owned(),
            path: outside.path().to_path_buf(),
            write_access: false,
            reason: "test".to_owned(),
        }]);
        let read = ReadFileTool
            .execute(json!({"path": file.to_string_lossy()}), &context)
            .await
            .expect("read granted path");
        assert_eq!(read.content, "outside");

        let blocked = WriteFileTool
            .execute(
                json!({"path": outside.path().join("new.txt").to_string_lossy(), "content": "x"}),
                &context,
            )
            .await;
        assert!(matches!(blocked, Err(ToolError::OutsideRoot(_))));

        context.set_outside_grants(vec![OutsideGrant {
            id: "grant-1".to_owned(),
            path: outside.path().to_path_buf(),
            write_access: true,
            reason: "test".to_owned(),
        }]);
        WriteFileTool
            .execute(
                json!({"path": outside.path().join("new.txt").to_string_lossy(), "content": "x"}),
                &context,
            )
            .await
            .expect("write granted path");
        assert_eq!(
            fs::read_to_string(outside.path().join("new.txt")).expect("new file"),
            "x"
        );

        let other = tempdir().expect("other");
        let denied = ReadFileTool
            .execute(
                json!({"path": other.path().join("nope.txt").to_string_lossy()}),
                &context,
            )
            .await;
        assert!(matches!(denied, Err(ToolError::OutsideRoot(_))));
    }

    #[tokio::test]
    async fn filesystem_access_requests_do_not_grant_access() {
        let workspace = tempdir().expect("workspace");
        let outside = tempdir().expect("outside");
        let file = outside.path().join("notes.txt");
        fs::write(&file, "outside").expect("file");
        let mut context = context(workspace.path());
        let request = RequestFilesystemAccessTool
            .execute(
                json!({"path": file.to_string_lossy(), "write": true, "reason": "compare"}),
                &context,
            )
            .await
            .expect("request");
        assert_eq!(
            request.metadata["openbad_access_request"]["path"],
            json!(file.to_string_lossy())
        );
        assert_eq!(request.metadata["openbad_access_request"]["write"], true);

        let relative = RequestFilesystemAccessTool
            .execute(json!({"path": "elsewhere", "reason": "test"}), &context)
            .await;
        assert!(matches!(relative, Err(ToolError::InvalidArgument(_))));

        context.set_outside_grants(vec![OutsideGrant {
            id: "grant-1".to_owned(),
            path: outside.path().to_path_buf(),
            write_access: false,
            reason: "test".to_owned(),
        }]);
        let existing = RequestFilesystemAccessTool
            .execute(
                json!({"path": file.to_string_lossy(), "write": false, "reason": "compare"}),
                &context,
            )
            .await
            .expect("existing grant");
        assert!(
            existing.content.contains("already granted"),
            "{}",
            existing.content
        );
        let upgrade = RequestFilesystemAccessTool
            .execute(
                json!({"path": file.to_string_lossy(), "write": true, "reason": "compare"}),
                &context,
            )
            .await
            .expect("upgrade request");
        assert!(upgrade.metadata["openbad_access_request"].is_object());
    }

    #[tokio::test]
    async fn plugins_scaffold_test_and_call() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        context.allow_commands = true;
        let scaffold = WritePluginTool
            .execute(
                json!({
                    "name": "greeter",
                    "description": "Say hello",
                    "runtime": "shell",
                    "actions": ["run", "echo"]
                }),
                &context,
            )
            .await
            .expect("scaffold");
        assert!(scaffold.content.contains("plugin greeter scaffolded"));
        assert!(
            directory
                .path()
                .join(".openbad/plugins/greeter/plugin.yaml")
                .is_file()
        );
        assert!(
            directory
                .path()
                .join(".openbad/plugins/greeter/SKILL.md")
                .is_file()
        );

        let listed = ListPluginsTool
            .execute(json!({}), &context)
            .await
            .expect("list");
        assert!(listed.content.contains("greeter"));

        let tested = TestPluginTool
            .execute(json!({"plugin": "greeter"}), &context)
            .await
            .expect("test plugin");
        assert!(!tested.is_error, "{}", tested.content);
        assert!(tested.content.contains("passed"));

        let called = CallPluginTool
            .execute(
                json!({"plugin": "greeter", "action": "echo", "arguments": {"name": "Ada"}}),
                &context,
            )
            .await
            .expect("call plugin");
        assert!(!called.is_error, "{}", called.content);
        assert!(called.content.contains("greeter ran"));
        assert_eq!(called.metadata["plugin"], "greeter");
        assert!(called.metadata["evidence_path"].as_str().is_some());

        let missing = CallPluginTool
            .execute(json!({"plugin": "greeter", "action": "nope"}), &context)
            .await;
        assert!(matches!(missing, Err(ToolError::InvalidArgument(_))));
    }

    #[tokio::test]
    async fn bundled_asciidoc_plugin_installs() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        let installed = InstallBuiltinPluginTool
            .execute(json!({"name": "asciidoc"}), &context)
            .await
            .expect("install");
        assert!(
            installed
                .content
                .contains("installed bundled plugin asciidoc")
        );
        assert!(
            directory
                .path()
                .join(".openbad/plugins/asciidoc/main.py")
                .is_file()
        );
        let (manifest, _) = load_plugin(directory.path(), "asciidoc").expect("manifest");
        assert!(manifest.actions.contains_key("render"));
        assert!(manifest.actions.contains_key("check"));
        let skills = discover_skills(directory.path());
        assert!(
            skills
                .iter()
                .any(|skill| skill.name == "asciidoc" && !skill.builtin)
        );
    }

    #[tokio::test]
    async fn plugin_dry_run_previews_without_full_access() {
        let directory = tempdir().expect("tempdir");
        let mut context = context(directory.path());
        context.allow_writes = true;
        WritePluginTool
            .execute(
                json!({"name": "preview", "description": "preview", "runtime": "shell"}),
                &context,
            )
            .await
            .expect("scaffold");
        context.allow_writes = false;
        let preview = CallPluginTool
            .execute(
                json!({"plugin": "preview", "action": "run", "dry_run": true}),
                &context,
            )
            .await
            .expect("dry run");
        assert!(preview.content.contains("dry run: sh main.sh"));
        assert_eq!(preview.metadata["dry_run"], true);
    }
}
