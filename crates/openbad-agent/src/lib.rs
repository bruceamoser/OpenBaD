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
use openbad_tools::{CheckpointSink, MemoryStore, ToolContext, ToolRegistry, WorkspaceSearch};
use openbad_workspace::WorkspaceService;
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
            max_steps: 64,
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
    fn on_tool_result(&self, _step: u32, _name: &str, _content: &str, _is_error: bool) {}
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
        let system_prompt = self.system_prompt();
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
            context
        });

        for step in 1..=self.config.max_steps {
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
                            observer.on_tool_result(step, &call.function.name, &content, true);
                            content
                        }
                        Ok(result) => {
                            let content = redact_secrets(&result.content, &self.redactions);
                            observer.on_tool_result(step, &call.function.name, &content, false);
                            content
                        }
                        Err(error) => {
                            let content =
                                redact_secrets(&format!("error: {error}"), &self.redactions);
                            observer.on_tool_result(step, &call.function.name, &content, true);
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
                        observer.on_tool_result(step, &call.function.name, &content, true);
                        content
                    }
                };
                let content = redact_secrets(&content, &self.redactions);
                if std::env::var_os("OPENBAD_DEBUG_AGENT").is_some() {
                    eprintln!(
                        "[agent]   result {}: {}",
                        call.function.name,
                        content.chars().take(500).collect::<String>()
                    );
                }
                messages.push(ChatMessage::tool(call.id, content));
            }

            if step == self.config.max_steps {
                return Err(AgentError::MaxSteps(self.config.max_steps));
            }
        }

        Err(AgentError::MaxSteps(self.config.max_steps))
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

    fn system_prompt(&self) -> String {
        if let Some(prompt) = &self.config.system_prompt {
            return prompt.clone();
        }
        format!(
            "You are OpenBaD, a precise coding agent working in {}.\n\
Use the provided tools to inspect and change files. Read a file before editing it.\n\
Prefer small, surgical changes and run the relevant tests when useful.\n\
Use recall and remember for durable project decisions when they matter.\n\
Report what changed and any remaining risk. Never claim a file changed unless a tool succeeded.",
            self.workspace.root().display()
        )
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
    fn redacts_configured_secrets_from_tool_output() {
        let content = "key=sk-live-12345678 and also sk-live-12345678";
        assert_eq!(
            redact_secrets(content, &["sk-live-12345678".to_owned()]),
            "key=[redacted] and also [redacted]"
        );
        assert_eq!(redact_secrets(content, &["short".to_owned()]), content);
    }
}
