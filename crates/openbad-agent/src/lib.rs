//! The turn/step agent loop that connects DeepSeek to workspace tools.

use std::sync::Arc;

use openbad_harness::{ApiFormat, ReasoningEffort, RequestEnvelope, canonical_json, hash_bytes};
use openbad_llm::DeepSeekClient;
use openbad_llm::chat::ChatMessage;
use openbad_llm::pricing::{UsageTotals, deepseek_pricing};
use openbad_llm::provider::ProviderError;
use openbad_llm::transport::HttpTransport;
use openbad_tools::{ToolContext, ToolRegistry};
use openbad_workspace::WorkspaceService;
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct AgentConfig {
    pub max_steps: u32,
    pub max_output_tokens: u32,
    pub allow_writes: bool,
    pub allow_commands: bool,
    pub system_prompt: Option<String>,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            max_steps: 8,
            max_output_tokens: 16_384,
            allow_writes: false,
            allow_commands: false,
            system_prompt: None,
        }
    }
}

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
}

pub struct Agent<T: HttpTransport> {
    client: DeepSeekClient<T>,
    tools: ToolRegistry,
    workspace: Arc<WorkspaceService>,
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
            config,
        }
    }

    pub async fn run(&self, history: Vec<ChatMessage>) -> Result<AgentRun, AgentError> {
        let system_prompt = self.system_prompt();
        let mut messages = history;
        if !matches!(
            messages.first().map(|message| &message.role),
            Some(openbad_llm::chat::ChatRole::System)
        ) {
            messages.insert(0, ChatMessage::system(system_prompt.clone()));
        }
        let start_len = messages.len();

        let tool_schemas = self.tools.openai_tools();
        let tools_hash = hash_bytes(canonical_json(&Value::Array(tool_schemas.clone())).as_bytes());
        let system_hash = hash_bytes(system_prompt.as_bytes());
        let series_id = Uuid::now_v7();
        let mut usage = UsageTotals::default();

        let context = Arc::new({
            let mut context = ToolContext::new(self.workspace.clone());
            context.allow_writes = self.config.allow_writes;
            context.allow_commands = self.config.allow_commands;
            context
        });

        for step in 1..=self.config.max_steps {
            let request = self.client.build_request(
                messages.clone(),
                Some(tool_schemas.clone()),
                Some(self.config.max_output_tokens),
                false,
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
            let response = self.client.complete(frozen).await?;

            if let Some(pricing) =
                deepseek_pricing(&response.model).or_else(|| deepseek_pricing(self.client.model()))
            {
                usage.record(&response.usage, &pricing.cost(&response.usage, false));
            } else {
                usage.record(&response.usage, &Default::default());
            }

            let assistant = response.message;
            let tool_calls = assistant.tool_calls.clone().unwrap_or_default();
            let text = assistant.content.clone().unwrap_or_default();
            messages.push(assistant);

            if tool_calls.is_empty() {
                return Ok(AgentRun {
                    new_messages: messages[start_len..].to_vec(),
                    text,
                    usage,
                    steps: step,
                });
            }

            for call in tool_calls {
                let arguments: Value =
                    serde_json::from_str(&call.function.arguments).map_err(|error| {
                        AgentError::InvalidToolArguments {
                            tool: call.function.name.clone(),
                            message: error.to_string(),
                        }
                    })?;
                let content = match self
                    .tools
                    .execute(&call.function.name, arguments, &context)
                    .await
                {
                    Ok(result) if result.is_error => format!("error: {}", result.content),
                    Ok(result) => result.content,
                    Err(error) => format!("error: {error}"),
                };
                messages.push(ChatMessage::tool(call.id, content));
            }

            if step == self.config.max_steps {
                return Err(AgentError::MaxSteps(self.config.max_steps));
            }
        }

        Err(AgentError::MaxSteps(self.config.max_steps))
    }

    fn system_prompt(&self) -> String {
        if let Some(prompt) = &self.config.system_prompt {
            return prompt.clone();
        }
        format!(
            "You are OpenBaD, a precise coding agent working in {}.\n\
Use the provided tools to inspect and change files. Read a file before editing it.\n\
Prefer small, surgical changes and run the relevant tests when useful.\n\
Report what changed and any remaining risk. Never claim a file changed unless a tool succeeded.",
            self.workspace.root().display()
        )
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
            AgentConfig::default(),
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
                ..AgentConfig::default()
            },
        );

        let error = agent
            .run(vec![ChatMessage::user("loop")])
            .await
            .expect_err("max steps");
        assert!(matches!(error, AgentError::MaxSteps(2)));
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
            AgentConfig::default(),
        );
        let run = agent
            .run(vec![ChatMessage::user("what is in hello.txt?")])
            .await
            .expect("run");
        assert_eq!(run.text, "It says hello over http.");
        assert_eq!(run.steps, 2);
        server.join().expect("server");
    }
}
