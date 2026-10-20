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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryEntry {
    pub id: String,
    pub content: String,
    pub tags: Vec<String>,
    pub pinned: bool,
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
}

/// Discover workspace skills from `.openbad/skills` and `.claude/skills`.
/// `.openbad` wins when both define the same name.
#[must_use]
pub fn discover_skills(root: &Path) -> Vec<Skill> {
    let mut skills = BTreeMap::new();
    let Ok(canonical_root) = root.canonicalize() else {
        return Vec::new();
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
    pub memory: Option<Arc<dyn MemoryStore>>,
    pub project: Option<Arc<dyn ProjectAreas>>,
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
            memory: None,
            project: None,
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
        registry.register(StatFileTool);
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
        registry.register(ForgetMemoryTool);
        registry.register(SearchProjectTool);
        registry.register(AddKnowledgeTool);
        registry.register(ListKnowledgeTool);
        registry.register(GetKnowledgeTool);
        registry.register(UpdateKnowledgeTool);
        registry.register(DeleteKnowledgeTool);
        registry.register(ListProjectSourcesTool);
        registry.register(ReadRepoFileTool);
        registry.register(ListRepoDirTool);
        registry.register(GlobRepoTool);
        registry.register(GrepRepoTool);
        registry.register(PresentArtifactTool);
        registry.register(ReadProjectManifestTool);
        registry.register(ClassifyPathTool);
        registry.register(RunTaskTool);
        registry.register(TodoWriteTool);
        registry.register(TodoReadTool);
        registry.register(WriteHandoffTool);
        registry.register(ListSkillsTool);
        registry.register(ReadSkillTool);
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
        let truncated = selected.len() > context.max_output_bytes;

        Ok(
            ToolResult::ok(truncate(&selected, context.max_output_bytes)).with_metadata(json!({
                "path": path,
                "lines": shown,
                "total_lines": total_lines,
                "start_line": start.min(total_lines.saturating_add(1)),
                "end_line": end,
                "truncated": truncated,
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
            "Report a workspace file's size, line count, modification time, and BLAKE3 hash without reading its contents into context.",
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
        let path = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "path")?))?;
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

struct ReadFilesTool;

#[async_trait]
impl Tool for ReadFilesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "read_files",
            "Read several workspace files in one call. Each file is capped independently; failures are reported per path.",
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
            match context.workspace.resolve(Path::new(&raw)) {
                Ok(path) => match std::fs::read_to_string(&path) {
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
            "Extract headings, YAML identifiers, or code declarations with line numbers. Use it to navigate long files before reading.",
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
            None => context.workspace.resolve(Path::new(&raw))?,
        };
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
        let path = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "path")?))?;
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
        let from = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "from")?))?;
        let to = context
            .workspace
            .resolve(Path::new(&string_arg(&arguments, "to")?))?;
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
        let destination = context.workspace.resolve(Path::new(&relative))?;
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
        let warning = scope_warning(context, &path);
        Ok(ToolResult::ok(format!(
            "wrote {} bytes to {}{}",
            content.len(),
            path.display(),
            warning
                .as_deref()
                .map(|warning| format!("\n{warning}"))
                .unwrap_or_default()
        ))
        .with_metadata(json!({
            "path": path,
            "bytes": content.len(),
            "checkpoint": checkpoint,
            "scope_warning": warning
        })))
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
        let warning = scope_warning(context, &path);
        Ok(ToolResult::ok(format!(
            "updated {}{}",
            path.display(),
            warning
                .as_deref()
                .map(|warning| format!("\n{warning}"))
                .unwrap_or_default()
        ))
        .with_metadata(json!({
            "path": path,
            "replacements": count,
            "checkpoint": checkpoint,
            "scope_warning": warning
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
                "cwd": {"type": "string", "description": "Workspace-relative directory"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
                "confirm_destructive": {"type": "boolean", "description": "Explicitly allow a destructive command such as rm -rf or git reset --hard"}
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
        let cwd = match optional_string(&arguments, "cwd") {
            Some(cwd) => context.workspace.resolve(Path::new(&cwd))?,
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

        let content = format!(
            "exit: {}\nstdout:\n{}\nstderr:\n{}",
            status.code().unwrap_or(-1),
            truncate(&stdout, context.max_output_bytes),
            truncate(&stderr, context.max_output_bytes)
        );
        Ok(ToolResult::ok(content).with_metadata(json!({
            "exit_code": status.code(),
            "success": status.success(),
            "command_class": class.label(),
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
            "Save a durable project decision or preference to workspace memory. Repeating the same content updates it.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "pinned": {"type": "boolean"}
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
        let entry = memory
            .remember(&content, tags, pinned)
            .map_err(ToolError::Failed)?;
        let tags = if entry.tags.is_empty() {
            String::new()
        } else {
            format!(" [{}]", entry.tags.join(", "))
        };
        Ok(ToolResult::ok(format!(
            "remembered{}{}: {}",
            if entry.pinned { " (pinned)" } else { "" },
            tags,
            entry.content
        ))
        .with_metadata(json!({"id": entry.id})))
    }
}

struct RecallTool;

#[async_trait]
impl Tool for RecallTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition::new(
            "recall",
            "Search durable workspace memory for prior decisions, preferences, and context.",
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
                format!(
                    "{}{}{}: {}",
                    if entry.pinned { "* " } else { "- " },
                    tags,
                    entry.id,
                    entry.content
                )
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
            "List durable project memories with ids, tags, and pinned state so they can be audited.",
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
                format!(
                    "{}{}{}: {}",
                    if entry.pinned { "* " } else { "- " },
                    tags,
                    entry.id,
                    entry.content
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(ToolResult::ok(truncate(&content, context.max_output_bytes))
            .with_metadata(json!({"memories": entries.len()})))
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
            "Search this project's knowledge library, indexed code, and durable memories. Cite the returned locator when using a hit.",
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
            "Add a curated project note or reference to the knowledge library. Use a precise title and optional tags so later retrieval can cite it.",
            RiskLevel::Write,
        )
        .with_schema(json!({
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "source": {"type": "string", "description": "Optional URL or path"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["verified", "inferred", "uncertain"], "description": "How firmly the content is established; verified means checked against primary sources or code."}
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
        let entry = project
            .add_knowledge(&title, &content, source.as_deref(), tags, &confidence)
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "Added knowledge \"{}\" ({} chars, {} chunks, confidence {}, id {}).",
            entry.title,
            content.chars().count(),
            entry.chunks,
            entry.confidence,
            entry.id
        ))
        .with_metadata(json!({
            "id": entry.id,
            "title": entry.title,
            "chunks": entry.chunks
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
            "Correct a project knowledge document's title, content, source, or tags. Omitted fields stay unchanged; content changes rebuild chunks.",
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
                "confidence": {"type": "string", "enum": ["verified", "inferred", "uncertain"]}
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
        if title.is_none()
            && content.is_none()
            && source.is_none()
            && tags.is_none()
            && confidence.is_none()
        {
            return Err(ToolError::InvalidArgument(
                "at least one of title, content, source, tags, or confidence is required"
                    .to_owned(),
            ));
        }
        let document = project
            .update_knowledge(
                &id,
                title.as_deref(),
                content.as_deref(),
                source.as_deref(),
                tags,
                confidence.as_deref(),
            )
            .map_err(ToolError::Failed)?;
        Ok(ToolResult::ok(format!(
            "Updated knowledge \"{}\" ({} chunks, confidence {}, id {}).",
            document.title, document.chunks, document.confidence, document.id
        ))
        .with_metadata(json!({
            "id": document.id,
            "chunks": document.chunks,
            "confidence": document.confidence
        })))
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
                "end_line": {"type": "integer", "minimum": 1}
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
        let truncated = selected.len() > context.max_output_bytes;
        Ok(
            ToolResult::ok(truncate(&selected, context.max_output_bytes)).with_metadata(json!({
                "repo": repo.name,
                "path": path,
                "lines": shown,
                "total_lines": total_lines,
                "start_line": start.min(total_lines.saturating_add(1)),
                "end_line": end,
                "truncated": truncated,
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
                if skill.description.is_empty() {
                    format!("- {}", skill.name)
                } else {
                    format!("- {}: {}", skill.name, skill.description)
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
        let directory = context.workspace.resolve(Path::new(&relative))?;
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
        assert!(large.content.len() > 100_000);

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
            let entry = MemoryEntry {
                id: "memory-1".to_owned(),
                content: content.to_owned(),
                tags,
                pinned,
            };
            self.entries.lock().expect("lock").push(entry.clone());
            Ok(entry)
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

        let recalled = RecallTool
            .execute(json!({"query": "immutable"}), &context)
            .await
            .expect("recall");
        assert!(
            recalled
                .content
                .contains("Keep the prompt prefix immutable")
        );
        assert!(recalled.content.contains("[cache]"));

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
            };
            self.knowledge.lock().expect("lock").push(entry.clone());
            Ok(entry)
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
                    content: "stored knowledge content".to_owned(),
                    chunks: entry.chunks,
                    confidence: entry.confidence.clone(),
                    stale: false,
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
        assert!(fetched.content.contains("stored knowledge content"));

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

        let skills = discover_skills(directory.path());
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

        assert!(discover_skills(directory.path()).is_empty());
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
}
