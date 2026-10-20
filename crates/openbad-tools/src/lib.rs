//! Tool registry, schemas, and built-in coding tools.

use std::collections::BTreeMap;
use std::collections::HashSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

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

pub trait CheckpointSink: Send + Sync {
    fn save(&self, path: &str, existed: bool, content: Option<&[u8]>) -> Result<String, String>;
}

pub struct ToolContext {
    pub workspace: Arc<WorkspaceService>,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub max_output_bytes: usize,
    pub search: Option<Arc<dyn WorkspaceSearch>>,
    pub checkpoints: Option<Arc<dyn CheckpointSink>>,
    read_paths: Mutex<HashSet<PathBuf>>,
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
            read_paths: Mutex::new(HashSet::new()),
        }
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
        registry.register(ListDirTool);
        registry.register(GrepTool);
        registry.register(SearchCodeTool);
        registry.register(GlobTool);
        registry.register(FindSymbolsTool);
        registry.register(WriteFileTool);
        registry.register(EditFileTool);
        registry.register(RunCommandTool);
        registry.register(GitStatusTool);
        registry.register(GitDiffTool);
        registry.register(RepoMapTool);
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

fn optional_bool(arguments: &Value, name: &str) -> Option<bool> {
    arguments.get(name).and_then(Value::as_bool)
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
    let mut temp = tempfile::NamedTempFile::new_in(directory)?;
    std::io::Write::write_all(&mut temp, content.as_bytes())?;
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
            "Read a UTF-8 text file from the workspace. Returns the requested line range.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path"},
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
        let path = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "path")?))?;
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

        Ok(
            ToolResult::ok(truncate(&selected, context.max_output_bytes)).with_metadata(json!({
                "path": path,
                "lines": selected.lines().count(),
            })),
        )
    }
}

struct ListDirTool;

#[async_trait]
impl Tool for ListDirTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "list_dir",
            "List files and directories in the workspace, respecting .gitignore.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative directory"},
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
        let root = context.workspace.resolve(Path::new(&relative))?;
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
            "Search workspace text files for a substring. Respects .gitignore and returns path:line matches.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "description": "Workspace-relative directory"},
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
        let root = context.workspace.resolve(Path::new(&relative))?;
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
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
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
                .map_err(ToolError::Failed)?;
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
            "Find workspace files whose path matches a glob such as **/*.rs or src/**/*.ts. Respects .gitignore.",
            RiskLevel::Read,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Workspace-relative directory"},
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
        let root = context.workspace.resolve(Path::new(&relative))?;
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
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
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
                "path": {"type": "string", "description": "Workspace-relative directory"},
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
        let root = context.workspace.resolve(Path::new(&relative))?;
        let max_results = optional_usize(&arguments, "max_results")
            .unwrap_or(100)
            .clamp(1, 500);
        if let Some(search) = &context.search {
            let hits = search
                .find_symbols(&query, kind_filter.as_deref(), max_results)
                .map_err(ToolError::Failed)?;
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
            if !is_code_file(path) {
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
            "Write a complete UTF-8 file. Existing files must be read first.",
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
        let path = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "path")?))?;
        if path.exists() && !context.was_read(&path) {
            return Err(ToolError::Failed(format!(
                "read the file before overwriting it: {}",
                path.display()
            )));
        }
        let content = string_arg(&arguments, "content")?;
        let checkpoint = checkpoint_before_write(context, &path)?;
        atomic_write(&path, &content)?;
        Ok(ToolResult::ok(format!(
            "wrote {} bytes to {}",
            content.len(),
            path.display()
        ))
        .with_metadata(json!({"path": path, "bytes": content.len(), "checkpoint": checkpoint})))
    }
}

struct EditFileTool;

#[async_trait]
impl Tool for EditFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "edit_file",
            "Replace an exact string in a workspace file. The file must be read first.",
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
        let path = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "path")?))?;
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
        let checkpoint = checkpoint_before_write(context, &path)?;
        atomic_write(&path, &updated)?;
        Ok(
            ToolResult::ok(format!("updated {}", path.display())).with_metadata(json!({
                "path": path,
                "replacements": count,
                "checkpoint": checkpoint
            })),
        )
    }
}

struct RunCommandTool;

#[async_trait]
impl Tool for RunCommandTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "run_command",
            "Run a shell command in the workspace. Requires command approval.",
            RiskLevel::Execute,
        )
        .with_timeout(30)
        .with_schema(json!({
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "description": "Workspace-relative directory"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}
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
            return Err(ToolError::ApprovalRequired("run_command".to_owned()));
        }
        let command = string_arg(&arguments, "command")?;
        let cwd = match optional_string(&arguments, "cwd") {
            Some(cwd) => context.workspace.resolve(Path::new(&cwd))?,
            None => context.workspace.root().to_path_buf(),
        };
        let timeout = optional_usize(&arguments, "timeout_seconds")
            .unwrap_or(30)
            .clamp(1, 120) as u64;

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
        let mut child = process.spawn()?;

        let status = match child.wait_timeout(Duration::from_secs(timeout))? {
            Some(status) => status,
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ToolError::Timeout(timeout));
            }
        };
        let mut stdout = String::new();
        let mut stderr = String::new();
        if let Some(mut stream) = child.stdout.take() {
            let _ = stream.read_to_string(&mut stdout);
        }
        if let Some(mut stream) = child.stderr.take() {
            let _ = stream.read_to_string(&mut stderr);
        }

        let content = format!(
            "exit: {}\nstdout:\n{}\nstderr:\n{}",
            status.code().unwrap_or(-1),
            truncate(&stdout, context.max_output_bytes),
            truncate(&stderr, context.max_output_bytes)
        );
        Ok(ToolResult::ok(content).with_metadata(json!({
            "exit_code": status.code(),
            "success": status.success(),
        })))
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
            let path = context.workspace.resolve(Path::new(&path))?;
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

    #[tokio::test]
    async fn every_builtin_has_a_schema() {
        let registry = ToolRegistry::with_builtin_tools();
        let definitions = registry.definitions();
        assert!(definitions.len() >= 12);
        assert!(definitions.iter().all(|tool| tool.input_schema.is_object()));
        assert!(registry.openai_tools().iter().all(Value::is_object));
    }
}
