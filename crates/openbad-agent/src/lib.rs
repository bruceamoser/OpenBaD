//! The turn/step agent loop that connects DeepSeek to workspace tools.

use std::collections::BTreeMap;
use std::sync::Arc;

use futures_util::StreamExt;
use openbad_harness::{ApiFormat, ReasoningEffort, RequestEnvelope, canonical_json, hash_bytes};
use openbad_llm::DeepSeekClient;
use openbad_llm::chat::{
    ChatMessage, ChatRole, FunctionCall, StreamEvent, ToolCall, ToolCallDelta,
};
use openbad_llm::pricing::{PeakCalendar, UsageTotals, deepseek_pricing};
use openbad_llm::provider::{ProviderError, Usage};
use openbad_llm::transport::HttpTransport;
use openbad_tools::{
    CheckpointSink, MemoryStore, ProjectAreas, ToolContext, ToolRegistry, WorkspaceSearch,
    discover_skills,
};
use openbad_workspace::{WorkspaceService, load_project_manifest};
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct AgentConfig {
    pub max_steps: u32,
    pub max_output_tokens: u32,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub stream: bool,
    pub system_prompt: Option<String>,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            max_steps: 0,
            max_output_tokens: 16_384,
            allow_writes: false,
            allow_commands: false,
            stream: true,
            system_prompt: None,
        }
    }
}

/// Receives live progress from the agent. Implementations must not block.
pub trait AgentObserver: Send + Sync {
    fn on_step(&self, _step: u32) {}
    fn on_reasoning(&self, _step: u32, _text: &str) {}
    fn on_content(&self, _step: u32, _text: &str) {}
    fn on_tool_call(&self, _step: u32, _call: &ToolCall) {}
    fn on_tool_result(
        &self,
        _step: u32,
        _name: &str,
        _content: &str,
        _is_error: bool,
        _metadata: &Value,
    ) {
    }
    fn on_usage(&self, _usage: &UsageTotals) {}
    fn is_cancelled(&self) -> bool {
        false
    }
}

struct NoopObserver;

impl AgentObserver for NoopObserver {}

#[derive(Debug, Clone)]
pub struct AgentRun {
    /// Messages produced after the supplied history, including tool messages.
    pub new_messages: Vec<ChatMessage>,
    pub text: String,
    pub usage: UsageTotals,
    pub steps: u32,
}

#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    #[error("provider error: {0}")]
    Provider(#[from] ProviderError),
    #[error("agent exceeded the step budget of {0}")]
    MaxSteps(u32),
    #[error("agent returned an empty response")]
    EmptyResponse,
    #[error("invalid tool arguments for {tool}: {message}")]
    InvalidToolArguments { tool: String, message: String },
    #[error("agent turn was cancelled")]
    Cancelled,
}

pub struct Agent<T: HttpTransport> {
    client: DeepSeekClient<T>,
    tools: ToolRegistry,
    workspace: Arc<WorkspaceService>,
    search: Option<Arc<dyn WorkspaceSearch>>,
    checkpoints: Option<Arc<dyn CheckpointSink>>,
    memory: Option<Arc<dyn MemoryStore>>,
    project: Option<Arc<dyn ProjectAreas>>,
    redactions: Vec<String>,
    config: AgentConfig,
}

impl<T: HttpTransport> Agent<T> {
    #[must_use]
    pub fn new(
        client: DeepSeekClient<T>,
        tools: ToolRegistry,
        workspace: Arc<WorkspaceService>,
        config: AgentConfig,
    ) -> Self {
        Self {
            client,
            tools,
            workspace,
            search: None,
            checkpoints: None,
            memory: None,
            project: None,
            redactions: Vec::new(),
            config,
        }
    }

    #[must_use]
    pub fn with_search(mut self, search: Arc<dyn WorkspaceSearch>) -> Self {
        self.search = Some(search);
        self
    }

    #[must_use]
    pub fn with_checkpoints(mut self, checkpoints: Arc<dyn CheckpointSink>) -> Self {
        self.checkpoints = Some(checkpoints);
        self
    }

    #[must_use]
    pub fn with_memory(mut self, memory: Arc<dyn MemoryStore>) -> Self {
        self.memory = Some(memory);
        self
    }

    #[must_use]
    pub fn with_project(mut self, project: Arc<dyn ProjectAreas>) -> Self {
        self.project = Some(project);
        self
    }

    /// Strings that must never reach the model, observer, or persisted trace.
    #[must_use]
    pub fn with_redactions(mut self, redactions: Vec<String>) -> Self {
        self.redactions = redactions;
        self
    }

    pub async fn run(&self, history: Vec<ChatMessage>) -> Result<AgentRun, AgentError> {
        self.run_with_observer(history, &NoopObserver).await
    }

    pub async fn run_with_observer(
        &self,
        history: Vec<ChatMessage>,
        observer: &dyn AgentObserver,
    ) -> Result<AgentRun, AgentError> {
        let system_prompt = format!("{}{}", self.system_prompt(), self.memory_context());
        let mut messages = history;
        if !matches!(
            messages.first().map(|message| &message.role),
            Some(ChatRole::System)
        ) {
            messages.insert(0, ChatMessage::system(system_prompt.clone()));
        }
        let start_len = messages.len();

        let tool_schemas = self.tools.openai_tools();
        let tools_hash = hash_bytes(canonical_json(&Value::Array(tool_schemas.clone())).as_bytes());
        let system_hash = hash_bytes(system_prompt.as_bytes());
        let series_id = Uuid::now_v7();
        let mut usage_totals = UsageTotals::default();
        let peak_pricing = PeakCalendar::new().is_peak_now();

        let context = Arc::new({
            let mut context = ToolContext::new(self.workspace.clone());
            context.allow_writes = self.config.allow_writes;
            context.allow_commands = self.config.allow_commands;
            context.search = self.search.clone();
            context.checkpoints = self.checkpoints.clone();
            context.memory = self.memory.clone();
            context.project = self.project.clone();
            context
        });

        let step_budget = if self.config.max_steps == 0 {
            u32::MAX
        } else {
            self.config.max_steps
        };
        for step in 1..=step_budget {
            context.set_turn(step);
            if observer.is_cancelled() {
                return Err(AgentError::Cancelled);
            }
            observer.on_step(step);
            let request = self.client.build_request(
                messages.clone(),
                Some(tool_schemas.clone()),
                Some(self.config.max_output_tokens),
                self.config.stream,
            );
            let envelope = RequestEnvelope {
                series_id,
                provider: "deepseek".to_owned(),
                model: self.client.model().to_owned(),
                api_format: ApiFormat::OpenAi,
                thinking_enabled: self.client.thinking_enabled(),
                reasoning_effort: self.client.reasoning_effort(),
                system_hash: system_hash.clone(),
                tools_hash: tools_hash.clone(),
                workspace_hash: Some(hash_bytes(
                    self.workspace.root().to_string_lossy().as_bytes(),
                )),
                max_output_tokens: self.config.max_output_tokens,
            };
            let frozen = request.freeze(envelope)?;

            let (assistant, finish_reason, response_usage) = if self.config.stream {
                self.stream_step(frozen, observer, step).await?
            } else {
                let response = self.client.complete(frozen).await?;
                emit_full_message(observer, step, &response.message);
                (response.message, response.finish_reason, response.usage)
            };

            if let Some(pricing) = deepseek_pricing(self.client.model()) {
                usage_totals.record(
                    &response_usage,
                    &pricing.cost(&response_usage, peak_pricing),
                );
            } else {
                usage_totals.record(&response_usage, &Default::default());
            }
            observer.on_usage(&usage_totals);

            let tool_calls = assistant.tool_calls.clone().unwrap_or_default();
            let text = assistant.content.clone().unwrap_or_default();
            if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
                eprintln!(
                    "[agent] step {step} finish={finish_reason:?} content={text:?} tool_calls={}",
                    tool_calls.len()
                );
                for call in &tool_calls {
                    eprintln!(
                        "[agent]   call {} {}",
                        call.function.name, call.function.arguments
                    );
                }
            }
            messages.push(assistant);

            if tool_calls.is_empty() {
                return Ok(AgentRun {
                    new_messages: messages[start_len..].to_vec(),
                    text,
                    usage: usage_totals,
                    steps: step,
                });
            }

            for call in tool_calls {
                if observer.is_cancelled() {
                    return Err(AgentError::Cancelled);
                }
                observer.on_tool_call(step, &call);
                let started = std::time::Instant::now();
                let content = match serde_json::from_str::<Value>(&call.function.arguments) {
                    Ok(arguments) => match self
                        .tools
                        .execute(&call.function.name, arguments, &context)
                        .await
                    {
                        Ok(result) if result.is_error => {
                            let content = redact_secrets(
                                &format!("error: {}", result.content),
                                &self.redactions,
                            );
                            observer.on_tool_result(
                                step,
                                &call.function.name,
                                &content,
                                true,
                                &result.metadata,
                            );
                            content
                        }
                        Ok(result) => {
                            let content = redact_secrets(&result.content, &self.redactions);
                            observer.on_tool_result(
                                step,
                                &call.function.name,
                                &content,
                                false,
                                &result.metadata,
                            );
                            content
                        }
                        Err(error) => {
                            let content =
                                redact_secrets(&format!("error: {error}"), &self.redactions);
                            observer.on_tool_result(
                                step,
                                &call.function.name,
                                &content,
                                true,
                                &Value::Null,
                            );
                            content
                        }
                    },
                    Err(error) => {
                        let content = redact_secrets(
                            &format!(
                                "error: invalid arguments for {}: {error}",
                                call.function.name
                            ),
                            &self.redactions,
                        );
                        observer.on_tool_result(
                            step,
                            &call.function.name,
                            &content,
                            true,
                            &Value::Null,
                        );
                        content
                    }
                };
                let content = redact_secrets(&content, &self.redactions);
                append_tool_audit(
                    self.workspace.root(),
                    step,
                    &call.function.name,
                    &redact_secrets(&call.function.arguments, &self.redactions),
                    &content,
                    started.elapsed().as_millis() as u64,
                );
                if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
                    eprintln!(
                        "[agent]   result {}: {}",
                        call.function.name,
                        content.chars().take(500).collect::<String>()
                    );
                }
                messages.push(ChatMessage::tool(call.id, content));
            }

            if step == step_budget {
                return Err(AgentError::MaxSteps(step_budget));
            }
        }

        Err(AgentError::MaxSteps(step_budget))
    }

    async fn stream_step(
        &self,
        frozen: openbad_llm::provider::FrozenRequest,
        observer: &dyn AgentObserver,
        step: u32,
    ) -> Result<(ChatMessage, Option<String>, Usage), AgentError> {
        let mut stream = self.client.stream(frozen).await?;
        let mut content = String::new();
        let mut reasoning = String::new();
        let mut calls = BTreeMap::new();
        let mut usage = Usage::default();
        let mut finish_reason = None;

        while let Some(event) = stream.next().await {
            if observer.is_cancelled() {
                return Err(AgentError::Cancelled);
            }
            match event? {
                StreamEvent::Delta {
                    content: content_delta,
                    reasoning_content: reasoning_delta,
                    tool_calls,
                } => {
                    if let Some(delta) = reasoning_delta {
                        observer.on_reasoning(step, &delta);
                        reasoning.push_str(&delta);
                    }
                    if let Some(delta) = content_delta {
                        observer.on_content(step, &delta);
                        content.push_str(&delta);
                    }
                    for delta in tool_calls {
                        merge_tool_delta(&mut calls, delta);
                    }
                }
                StreamEvent::Usage(value) => usage = value,
                StreamEvent::Finished {
                    finish_reason: reason,
                } => {
                    if reason.is_some() {
                        finish_reason = reason;
                    }
                }
            }
        }

        let tool_calls = if calls.is_empty() {
            None
        } else {
            Some(calls.into_values().collect())
        };
        Ok((
            ChatMessage {
                role: ChatRole::Assistant,
                content: if content.is_empty() {
                    None
                } else {
                    Some(content)
                },
                reasoning_content: if reasoning.is_empty() {
                    None
                } else {
                    Some(reasoning)
                },
                tool_calls,
                tool_call_id: None,
                prefix: None,
            },
            finish_reason,
            usage,
        ))
    }

    fn memory_context(&self) -> String {
        let Some(memory) = &self.memory else {
            return String::new();
        };
        let Ok(entries) = memory.recall(None, 6) else {
            return String::new();
        };
        if entries.is_empty() {
            return String::new();
        }
        let pinned = entries.iter().filter(|entry| entry.pinned).count();
        let mut context = format!(
            "\n\nWorkspace memory loaded {} entr{} ({} pinned; verify before relying on it):\n",
            entries.len(),
            if entries.len() == 1 { "y" } else { "ies" },
            pinned
        );
        for entry in entries {
            let content: String = entry.content.chars().take(1_000).collect();
            let origin = entry
                .origin_task
                .as_deref()
                .map(|task| format!(" ({task})"))
                .or_else(|| entry.origin_turn.map(|turn| format!(" (turn {turn})")))
                .unwrap_or_default();
            context.push_str(&format!(
                "- {}[{}] {}{}\n",
                if entry.pinned { "[pinned] " } else { "" },
                entry.scope,
                content,
                origin
            ));
        }
        context
    }

    fn system_prompt(&self) -> String {
        if let Some(prompt) = &self.config.system_prompt {
            return prompt.clone();
        }
        let mut prompt = format!(
            "You are OpenBaD, a precise coding agent working in {}.\n\
Use the provided tools to inspect and change files. Read a file before editing it.\n\
Prefer small, surgical changes and run the relevant tests when useful.\n\
Use recall and remember for durable project decisions when they matter; use session scope for transient context and supersedes when replacing a contradictory memory.\n\
Project knowledge is curated separately from code: call search_project before\n\
answering questions that may rely on earlier decisions or imported references.\n\
Use list_project_sources to orient yourself. For reference folders, use\n\
list_repo_dir, glob_repo, and grep_repo before reading files; use stat_repo_file\n\
for bounded reads. Reads report total_lines and continuation metadata.\n\
Use list_knowledge/get_knowledge to audit knowledge, update_knowledge or\n\
delete_knowledge to correct it, and list_memories/forget_memory to audit memory.\n\
Use git_log/git_blame/repo_health for repository archaeology and state, git_commit_context before drafting a commit, git_worktree for isolated branches, and github_issue/github_pr/github_ci when the repo is GitHub-backed. Use canon_query/canon_lint to resolve competing versions of project truth, glossary_lookup for terminology, adr_write for durable decisions, yaml_query for structured documents, and check_invariants before claiming project invariants hold. Use ask_question when a decision blocks correctness instead of guessing; the human answers in Project → Questions and the answer appears in the next prompt. Use record_claim with executed/read/inferred/assumed provenance and record_unknown for unresolved gaps; context_report estimates what the turn is holding. Use preview_write before risky large writes, snapshot_create before experiments, snapshot_restore only with confirmation, and assert_project for structural release checks. Use typed knowledge kinds and review_knowledge for curation; pass supersedes when replacing a note, and export_knowledge/import_knowledge for git review. Use web_fetch for external docs and specs, cite the URL and fetch date, and record a dead end when a page does not answer the question. Use context_map to see what this turn has loaded, pin_context/exclude_context for residency rules, and record_dead_end when evidence rules out a path or hypothesis so it is not re-explored. Use present_artifact when showing a web page, image, diff, or note is clearer\n\
than prose. Cite knowledge locators. Report what changed and any remaining risk. Never claim a file changed unless a tool succeeded.",
            self.workspace.root().display()
        );
        if let Ok(policy) = openbad_workspace::load_context_policy(self.workspace.root()) {
            let summary = policy.summary();
            if !summary.is_empty() {
                prompt.push_str("\nContext rules from .openbad/context.yaml:\n");
                prompt.push_str(&summary);
            }
        }
        if let Ok(questions) = openbad_workspace::load_questions(self.workspace.root()) {
            let summary = openbad_workspace::question_summary(&questions, 5);
            if !summary.is_empty() {
                prompt.push_str("\nHuman questions from .openbad/questions.json:\n");
                prompt.push_str(&summary);
            }
        }
        if let (Ok(claims), Ok(unknowns)) = (
            openbad_workspace::load_claims(self.workspace.root()),
            openbad_workspace::load_unknowns(self.workspace.root()),
        ) {
            let summary = openbad_workspace::learning_summary(&claims, &unknowns, 10);
            if !summary.is_empty() {
                prompt.push_str("\nClaims and known unknowns:\n");
                prompt.push_str(&summary);
            }
        }
        if let Ok(dead_ends) = openbad_workspace::load_dead_ends(self.workspace.root()) {
            let summary = openbad_workspace::dead_end_summary(&dead_ends, 20);
            if !summary.is_empty() {
                prompt.push('\n');
                prompt.push_str(&summary);
            }
        }
        if let Some(instructions) = load_project_instructions(self.workspace.root()) {
            prompt.push_str("\n\nProject conventions (follow unless they conflict with safety or explicit user instructions):\n");
            prompt.push_str(&instructions);
        }
        if let Ok(Some(manifest)) = load_project_manifest(self.workspace.root()) {
            prompt.push_str("\n\nProject manifest (.openbad/project.yaml):\n");
            prompt.push_str(&manifest.summary());
            if manifest.prove_it {
                prompt.push_str("Prove-it mode is active: every factual claim must cite an executed check or be labelled UNVERIFIED. Run gates/recipes before declaring completion.\n");
            }
            prompt.push_str("\nClassify paths before editing when scope matters; historical and generated paths need care. Use run_task for named manifest tasks, todo_write/todo_read for durable work state, and write_handoff before ending a long session.\n");
            prompt.push_str("Prefer run_gate/run_recipe for declared verification and note that write_file/edit_file run matching manifest validators. Use stat_file/stat_repo_file before guessing line counts, read_files for small related files, and outline to navigate long files. Prefer delete_file (soft delete) and move_file over shell commands. Destructive shell commands require confirm_destructive after checking the blast radius. If .openbad/permissions.yaml denies a path or command class, treat the denial as a project rule and ask the human to change the policy rather than working around it.\n");
        }
        let skills = discover_skills(self.workspace.root());
        if !skills.is_empty() {
            prompt.push_str("\n\nWorkspace skills (call read_skill to load one):\n");
            for skill in skills {
                if skill.description.is_empty() {
                    prompt.push_str(&format!("- {}\n", skill.name));
                } else {
                    prompt.push_str(&format!("- {}: {}\n", skill.name, skill.description));
                }
            }
        }
        prompt
    }
}

fn load_project_instructions(root: &std::path::Path) -> Option<String> {
    const FILES: &[&str] = &[
        "AGENTS.md",
        "CLAUDE.md",
        ".github/copilot-instructions.md",
        ".openbad/instructions.md",
        ".cursorrules",
    ];
    const MAX_TOTAL_CHARS: usize = 32 * 1024;
    let mut sections = Vec::new();
    let mut total = 0;
    for relative in FILES {
        let path = root.join(relative);
        if !path.is_file() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&path) else {
            continue;
        };
        let remaining = MAX_TOTAL_CHARS.saturating_sub(total);
        if remaining == 0 {
            break;
        }
        let content: String = content.chars().take(remaining).collect();
        total += content.len();
        sections.push(format!("From {relative}:\n{}", content.trim_end()));
    }
    if sections.is_empty() {
        None
    } else {
        Some(sections.join("\n\n"))
    }
}

fn append_tool_audit(
    root: &std::path::Path,
    turn: u32,
    tool: &str,
    arguments: &str,
    content: &str,
    duration_ms: u64,
) {
    let directory = root.join(".openbad");
    if std::fs::create_dir_all(&directory).is_err() {
        return;
    }
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as i64);
    let record = serde_json::json!({
        "ts": timestamp,
        "turn": turn,
        "tool": tool,
        "args_hash": hash_bytes(arguments.as_bytes()),
        "args_preview": arguments.chars().take(500).collect::<String>(),
        "result_hash": hash_bytes(content.as_bytes()),
        "result_preview": content.chars().take(500).collect::<String>(),
        "is_error": content.starts_with("error:"),
        "duration_ms": duration_ms,
    });
    let Ok(line) = serde_json::to_string(&record) else {
        return;
    };
    use std::io::Write;
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("audit.jsonl"))
    {
        let _ = writeln!(file, "{line}");
    }
}

fn redact_secrets(content: &str, secrets: &[String]) -> String {
    let mut redacted = content.to_owned();
    for secret in secrets {
        if secret.len() >= 8 {
            redacted = redacted.replace(secret, "[redacted]");
        }
    }
    redacted
}

fn emit_full_message(observer: &dyn AgentObserver, step: u32, message: &ChatMessage) {
    if let Some(reasoning) = &message.reasoning_content {
        observer.on_reasoning(step, reasoning);
    }
    if let Some(content) = &message.content {
        observer.on_content(step, content);
    }
}

fn merge_tool_delta(calls: &mut BTreeMap<u32, ToolCall>, delta: ToolCallDelta) {
    let entry = calls.entry(delta.index).or_insert_with(|| ToolCall {
        id: String::new(),
        kind: "function".to_owned(),
        function: FunctionCall {
            name: String::new(),
            arguments: String::new(),
        },
    });
    if let Some(id) = delta.id {
        entry.id = id;
    }
    if let Some(function) = delta.function {
        if let Some(name) = function.name {
            entry.function.name.push_str(&name);
        }
        if let Some(arguments) = function.arguments {
            entry.function.arguments.push_str(&arguments);
        }
    }
}

/// Convenience constructor for the common DeepSeek + built-in tools setup.
#[must_use]
pub fn default_agent<T: HttpTransport>(
    client: DeepSeekClient<T>,
    workspace: Arc<WorkspaceService>,
    config: AgentConfig,
) -> Agent<T> {
    Agent::new(
        client,
        ToolRegistry::with_builtin_tools(),
        workspace,
        config,
    )
}

/// The reasoning effort used by the default agent when thinking is enabled.
#[must_use]
pub fn default_reasoning_effort() -> ReasoningEffort {
    ReasoningEffort::High
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::fs;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Mutex;

    use async_trait::async_trait;
    use openbad_llm::transport::{ByteStream, HttpRequest, HttpResponse};
    use serde_json::json;
    use tempfile::tempdir;

    use super::*;

    struct MockTransport {
        responses: Mutex<VecDeque<HttpResponse>>,
        requests: Mutex<Vec<HttpRequest>>,
    }

    #[async_trait]
    impl HttpTransport for MockTransport {
        async fn post(&self, request: HttpRequest) -> Result<HttpResponse, ProviderError> {
            self.requests.lock().expect("requests").push(request);
            self.responses
                .lock()
                .expect("responses")
                .pop_front()
                .ok_or_else(|| ProviderError::Request("no mock response".to_owned()))
        }

        async fn post_stream(&self, _request: HttpRequest) -> Result<ByteStream, ProviderError> {
            Err(ProviderError::Unsupported("streaming".to_owned()))
        }
    }

    fn response(body: &str) -> HttpResponse {
        HttpResponse {
            status: 200,
            body: body.as_bytes().to_vec(),
        }
    }

    #[tokio::test]
    async fn agent_executes_a_tool_then_answers() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("hello.txt"), "hello from disk").expect("file");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::from([
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"hello.txt\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}"#,
                ),
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":"The file says hello from disk."},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":8}}"#,
                ),
            ])),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                stream: false,
                ..AgentConfig::default()
            },
        );

        let run = agent
            .run(vec![ChatMessage::user("what is in hello.txt?")])
            .await
            .expect("run");
        assert_eq!(run.steps, 2);
        assert_eq!(run.text, "The file says hello from disk.");
        assert!(run.new_messages.iter().any(|message| {
            message.role == openbad_llm::chat::ChatRole::Tool
                && message.content.as_deref() == Some("hello from disk")
        }));
        assert!(run.usage.output_tokens > 0);
        let audit =
            fs::read_to_string(directory.path().join(".openbad/audit.jsonl")).expect("audit log");
        assert!(audit.contains(r#""tool":"read_file""#));
        assert!(audit.contains(r#""turn":1"#));
        assert!(audit.contains(r#""is_error":false"#));
    }

    #[tokio::test]
    async fn agent_stops_at_the_step_budget() {
        let directory = tempdir().expect("tempdir");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::from([
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"list_dir","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}"#,
                ),
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-2","type":"function","function":{"name":"list_dir","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}"#,
                ),
            ])),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                max_steps: 2,
                stream: false,
                ..AgentConfig::default()
            },
        );

        let error = agent
            .run(vec![ChatMessage::user("loop")])
            .await
            .expect_err("max steps");
        assert!(matches!(error, AgentError::MaxSteps(2)));
    }

    struct ArtifactObserver {
        metadata: Mutex<Value>,
    }

    impl AgentObserver for ArtifactObserver {
        fn on_tool_result(
            &self,
            _step: u32,
            _name: &str,
            _content: &str,
            _is_error: bool,
            metadata: &Value,
        ) {
            *self.metadata.lock().expect("metadata") = metadata.clone();
        }
    }

    #[tokio::test]
    async fn tool_metadata_reaches_the_observer() {
        let directory = tempdir().expect("tempdir");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::from([
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"present_artifact","arguments":"{\"kind\":\"web\",\"title\":\"Docs\",\"uri\":\"https://example.com\"}"}}]},"finish_reason":"tool_calls"}]}"#,
                ),
                response(
                    r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":"Opened the docs."},"finish_reason":"stop"}]}"#,
                ),
            ])),
            requests: Mutex::new(Vec::new()),
        };
        let agent = default_agent(
            DeepSeekClient::new(transport, "sk-test"),
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                stream: false,
                ..AgentConfig::default()
            },
        );
        let observer = ArtifactObserver {
            metadata: Mutex::new(Value::Null),
        };
        agent
            .run_with_observer(vec![ChatMessage::user("show docs")], &observer)
            .await
            .expect("run");
        let metadata = observer.metadata.lock().expect("metadata");
        assert_eq!(metadata["openbad_artifact"]["kind"], json!("web"));
        assert_eq!(
            metadata["openbad_artifact"]["uri"],
            json!("https://example.com")
        );
    }

    struct CancelObserver;

    impl AgentObserver for CancelObserver {
        fn is_cancelled(&self) -> bool {
            true
        }
    }

    #[tokio::test]
    async fn cancelled_turns_stop_before_the_next_model_call() {
        let directory = tempdir().expect("tempdir");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::new()),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                stream: false,
                ..AgentConfig::default()
            },
        );
        let error = agent
            .run_with_observer(vec![ChatMessage::user("stop")], &CancelObserver)
            .await
            .expect_err("cancelled");
        assert!(matches!(error, AgentError::Cancelled));
    }

    #[tokio::test]
    async fn agent_runs_end_to_end_over_http() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("hello.txt"), "hello over http").expect("file");

        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("address");
        let server = std::thread::spawn(move || {
            let responses = [
                r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"hello.txt\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}"#,
                r#"{"model":"deepseek-flash","choices":[{"message":{"role":"assistant","content":"It says hello over http."},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":8}}"#,
            ];
            for body in responses {
                let (mut stream, _) = listener.accept().expect("accept");
                let mut buffer = [0_u8; 8192];
                let _ = stream.read(&mut buffer);
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                stream.write_all(response.as_bytes()).expect("write");
            }
        });

        let client = DeepSeekClient::new(openbad_llm::ReqwestTransport::new(), "sk-test")
            .with_base_url(format!("http://{address}"));
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                stream: false,
                ..AgentConfig::default()
            },
        );
        let run = agent
            .run(vec![ChatMessage::user("what is in hello.txt?")])
            .await
            .expect("run");
        assert_eq!(run.text, "It says hello over http.");
        assert_eq!(run.steps, 2);
        server.join().expect("server");
    }

    #[test]
    fn system_prompt_includes_project_instructions() {
        let directory = tempdir().expect("tempdir");
        fs::write(
            directory.path().join("AGENTS.md"),
            "Use the repository test command.",
        )
        .expect("instructions");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::new()),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig::default(),
        );
        let prompt = agent.system_prompt();
        assert!(prompt.contains("From AGENTS.md"));
        assert!(prompt.contains("Use the repository test command."));
    }

    #[test]
    fn system_prompt_lists_workspace_skills() {
        let directory = tempdir().expect("tempdir");
        let skill_dir = directory.path().join(".openbad/skills/review");
        fs::create_dir_all(&skill_dir).expect("skill dir");
        fs::write(
            skill_dir.join("SKILL.md"),
            "---\nname: review\ndescription: Review changes\n---\n",
        )
        .expect("skill");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::new()),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig::default(),
        );
        let prompt = agent.system_prompt();
        assert!(prompt.contains("review: Review changes"));
    }

    #[test]
    fn loads_root_project_instructions() {
        let directory = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            directory.path().join("AGENTS.md"),
            "# Conventions\nRun cargo test.\n",
        )
        .expect("instructions");
        let instructions = load_project_instructions(directory.path()).expect("loaded");
        assert!(instructions.contains("From AGENTS.md"));
        assert!(instructions.contains("Run cargo test."));

        let empty = tempfile::tempdir().expect("empty");
        assert!(load_project_instructions(empty.path()).is_none());
    }

    struct StaticMemory;

    impl openbad_tools::MemoryStore for StaticMemory {
        fn remember(
            &self,
            content: &str,
            tags: Vec<String>,
            pinned: bool,
        ) -> Result<openbad_tools::MemoryEntry, String> {
            Ok(openbad_tools::MemoryEntry {
                id: "memory".to_owned(),
                content: content.to_owned(),
                tags,
                pinned,
                scope: "project".to_owned(),
                author: "agent".to_owned(),
                origin_turn: None,
                origin_task: None,
                supersedes: None,
                expires_at: None,
            })
        }

        fn recall(
            &self,
            _query: Option<&str>,
            _limit: usize,
        ) -> Result<Vec<openbad_tools::MemoryEntry>, String> {
            Ok(vec![openbad_tools::MemoryEntry {
                id: "memory".to_owned(),
                content: "Use the cache-first prompt order.".to_owned(),
                tags: vec!["decision".to_owned()],
                pinned: true,
                scope: "project".to_owned(),
                author: "agent".to_owned(),
                origin_turn: Some(2),
                origin_task: None,
                supersedes: None,
                expires_at: None,
            }])
        }
    }

    #[tokio::test]
    async fn includes_recent_memory_in_the_request() {
        let directory = tempdir().expect("tempdir");
        let transport = MockTransport {
            responses: Mutex::new(VecDeque::from([HttpResponse {
                status: 200,
                body: br#"{"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}"#.to_vec(),
            }])),
            requests: Mutex::new(Vec::new()),
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let agent = default_agent(
            client,
            Arc::new(WorkspaceService::new(directory.path())),
            AgentConfig {
                stream: false,
                ..AgentConfig::default()
            },
        )
        .with_memory(Arc::new(StaticMemory));
        agent
            .run(vec![ChatMessage::user("hello")])
            .await
            .expect("run");

        let transport = agent.client.transport();
        let requests = transport.requests.lock().expect("requests");
        let body = String::from_utf8_lossy(&requests[0].body);
        assert!(body.contains("Workspace memory loaded 1 entry (1 pinned"));
        assert!(body.contains("cache-first prompt order"));
        assert!(body.contains("[pinned] [project]"));
        assert!(body.contains("turn 2"));
    }

    #[test]
    fn redacts_configured_secrets_from_tool_output() {
        let content = "key=sk-live-12345678 and also sk-live-12345678";
        assert_eq!(
            redact_secrets(content, &["sk-live-12345678".to_owned()]),
            "key=[redacted] and also [redacted]"
        );
        assert_eq!(redact_secrets(content, &["short".to_owned()]), content);
    }
}
