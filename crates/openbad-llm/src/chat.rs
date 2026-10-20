use std::collections::VecDeque;

use openbad_harness::{ReasoningEffort, RequestEnvelope};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::provider::{FrozenRequest, ProviderError, Usage};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRole {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FunctionCall {
    pub name: String,
    pub arguments: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub function: FunctionCall,
}

impl ToolCall {
    #[must_use]
    pub fn function(id: impl Into<String>, name: impl Into<String>, arguments: Value) -> Self {
        Self {
            id: id.into(),
            kind: "function".to_owned(),
            function: FunctionCall {
                name: name.into(),
                arguments: serde_json::to_string(&arguments).unwrap_or_else(|_| "{}".to_owned()),
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: ChatRole,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning_content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prefix: Option<bool>,
}

impl ChatMessage {
    #[must_use]
    pub fn system(content: impl Into<String>) -> Self {
        Self::text(ChatRole::System, content)
    }

    #[must_use]
    pub fn user(content: impl Into<String>) -> Self {
        Self::text(ChatRole::User, content)
    }

    #[must_use]
    pub fn assistant(content: impl Into<String>) -> Self {
        Self::text(ChatRole::Assistant, content)
    }

    #[must_use]
    pub fn tool(tool_call_id: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: ChatRole::Tool,
            content: Some(content.into()),
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: Some(tool_call_id.into()),
            prefix: None,
        }
    }

    #[must_use]
    pub fn assistant_prefix(content: impl Into<String>) -> Self {
        Self {
            role: ChatRole::Assistant,
            content: Some(content.into()),
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            prefix: Some(true),
        }
    }

    #[must_use]
    pub fn assistant_tool_calls(
        content: Option<String>,
        reasoning_content: Option<String>,
        tool_calls: Vec<ToolCall>,
    ) -> Self {
        Self {
            role: ChatRole::Assistant,
            content,
            reasoning_content,
            tool_calls: Some(tool_calls),
            tool_call_id: None,
            prefix: None,
        }
    }

    fn text(role: ChatRole, content: impl Into<String>) -> Self {
        Self {
            role,
            content: Some(content.into()),
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            prefix: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Thinking {
    #[serde(rename = "type")]
    pub kind: String,
}

impl Thinking {
    #[must_use]
    pub fn enabled() -> Self {
        Self {
            kind: "enabled".to_owned(),
        }
    }

    #[must_use]
    pub fn disabled() -> Self {
        Self {
            kind: "disabled".to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StreamOptions {
    pub include_usage: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream_options: Option<StreamOptions>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking: Option<Thinking>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<ReasoningEffort>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_choice: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_id: Option<String>,
}

impl ChatRequest {
    #[must_use]
    pub fn new(model: impl Into<String>, messages: Vec<ChatMessage>) -> Self {
        Self {
            model: model.into(),
            messages,
            stream: None,
            stream_options: None,
            thinking: None,
            reasoning_effort: None,
            tools: None,
            tool_choice: None,
            max_tokens: None,
            user_id: None,
        }
    }

    #[must_use]
    pub fn streaming(mut self) -> Self {
        self.stream = Some(true);
        self.stream_options = Some(StreamOptions {
            include_usage: true,
        });
        self
    }

    #[must_use]
    pub fn with_thinking(mut self, thinking: Thinking, effort: ReasoningEffort) -> Self {
        self.thinking = Some(thinking);
        self.reasoning_effort = Some(effort);
        self
    }

    #[must_use]
    pub fn with_tools(mut self, tools: Vec<Value>) -> Self {
        self.tools = Some(tools);
        self
    }

    #[must_use]
    pub fn with_max_tokens(mut self, max_tokens: u32) -> Self {
        self.max_tokens = Some(max_tokens);
        self
    }

    #[must_use]
    pub fn with_user_id(mut self, user_id: impl Into<String>) -> Self {
        self.user_id = Some(user_id.into());
        self
    }

    /// Validate request invariants before freezing the bytes.
    pub fn validate(&self) -> Result<(), ProviderError> {
        if self.messages.is_empty() {
            return Err(ProviderError::InvalidRequest(
                "chat request needs at least one message".to_owned(),
            ));
        }
        let message_count = self.messages.len();
        for (index, message) in self.messages.iter().enumerate() {
            if message.prefix == Some(true)
                && (index + 1 != message_count || message.role != ChatRole::Assistant)
            {
                return Err(ProviderError::InvalidRequest(
                    "prefix completion requires the last message to be an assistant message"
                        .to_owned(),
                ));
            }
            if let Some(tool_calls) = &message.tool_calls {
                for call in tool_calls {
                    let parsed: Value = serde_json::from_str(&call.function.arguments)
                        .map_err(|error| ProviderError::InvalidRequest(error.to_string()))?;
                    if !parsed.is_object() {
                        return Err(ProviderError::InvalidRequest(format!(
                            "tool call {} arguments must be a JSON object",
                            call.id
                        )));
                    }
                }
            }
        }
        Ok(())
    }

    /// Freeze the exact request bytes. Retries reuse this value verbatim.
    pub fn freeze(&self, envelope: RequestEnvelope) -> Result<FrozenRequest, ProviderError> {
        self.validate()?;
        if self.model != envelope.model {
            return Err(ProviderError::InvalidRequest(format!(
                "request model {} does not match envelope model {}",
                self.model, envelope.model
            )));
        }
        let bytes = serde_json::to_vec(self)
            .map_err(|error| ProviderError::InvalidRequest(error.to_string()))?;
        Ok(FrozenRequest { bytes, envelope })
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PromptTokensDetails {
    #[serde(default)]
    pub cached_tokens: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompletionTokensDetails {
    #[serde(default)]
    pub reasoning_tokens: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RawUsage {
    #[serde(default)]
    pub prompt_tokens: u64,
    #[serde(default)]
    pub completion_tokens: u64,
    #[serde(default)]
    pub total_tokens: u64,
    #[serde(default)]
    pub prompt_cache_hit_tokens: Option<u64>,
    #[serde(default)]
    pub prompt_cache_miss_tokens: Option<u64>,
    #[serde(default)]
    pub prompt_tokens_details: Option<PromptTokensDetails>,
    #[serde(default)]
    pub completion_tokens_details: Option<CompletionTokensDetails>,
}

impl RawUsage {
    #[must_use]
    pub fn normalize(&self) -> Usage {
        let hit = self
            .prompt_cache_hit_tokens
            .or_else(|| self.prompt_tokens_details.as_ref().map(|d| d.cached_tokens))
            .unwrap_or(0);
        let miss = self
            .prompt_cache_miss_tokens
            .unwrap_or_else(|| self.prompt_tokens.saturating_sub(hit));
        let reasoning = self
            .completion_tokens_details
            .as_ref()
            .map(|details| details.reasoning_tokens)
            .filter(|tokens| *tokens > 0);

        Usage {
            input_tokens: miss,
            cache_read_tokens: hit,
            cache_write_tokens: 0,
            output_tokens: self.completion_tokens,
            reasoning_tokens: reasoning,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatChoice {
    #[serde(default)]
    pub index: u32,
    pub message: ChatMessage,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatResponse {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub choices: Vec<ChatChoice>,
    #[serde(default)]
    pub usage: Option<RawUsage>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ModelResponse {
    pub id: Option<String>,
    pub model: String,
    pub message: ChatMessage,
    pub finish_reason: Option<String>,
    pub usage: Usage,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FunctionDelta {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub arguments: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolCallDelta {
    #[serde(default)]
    pub index: u32,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub function: Option<FunctionDelta>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StreamDelta {
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub reasoning_content: Option<String>,
    #[serde(default)]
    pub tool_calls: Option<Vec<ToolCallDelta>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StreamChoice {
    #[serde(default)]
    pub index: u32,
    pub delta: StreamDelta,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StreamChunk {
    #[serde(default)]
    pub choices: Vec<StreamChoice>,
    #[serde(default)]
    pub usage: Option<RawUsage>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StreamEvent {
    Delta {
        content: Option<String>,
        reasoning_content: Option<String>,
        tool_calls: Vec<ToolCallDelta>,
    },
    Usage(Usage),
    Finished {
        finish_reason: Option<String>,
    },
}

#[derive(Debug, Default)]
pub struct SseDecoder {
    buffer: Vec<u8>,
    data: Vec<String>,
    event: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SseFrame {
    pub event: Option<String>,
    pub data: String,
}

impl SseDecoder {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<SseFrame>, ProviderError> {
        self.buffer.extend_from_slice(chunk);
        let mut frames = Vec::new();

        while let Some(position) = self.buffer.iter().position(|byte| *byte == b'\n') {
            let line_bytes: Vec<u8> = self.buffer.drain(..=position).collect();
            let line = String::from_utf8_lossy(&line_bytes[..line_bytes.len() - 1]);
            let line = line.trim_end_matches('\r');
            self.consume_line(line, &mut frames);
        }

        Ok(frames)
    }

    pub fn finish(&mut self) -> Result<Vec<SseFrame>, ProviderError> {
        let mut frames = Vec::new();
        if !self.buffer.is_empty() {
            let line = String::from_utf8_lossy(&self.buffer).into_owned();
            self.buffer.clear();
            self.consume_line(&line, &mut frames);
        }
        self.dispatch(&mut frames);
        Ok(frames)
    }

    fn consume_line(&mut self, line: &str, frames: &mut Vec<SseFrame>) {
        if line.is_empty() {
            self.dispatch(frames);
            return;
        }
        if line.starts_with(':') {
            return;
        }
        if let Some(data) = line.strip_prefix("data:") {
            self.data.push(data.trim_start().to_owned());
        } else if let Some(event) = line.strip_prefix("event:") {
            self.event = Some(event.trim().to_owned());
        }
    }

    fn dispatch(&mut self, frames: &mut Vec<SseFrame>) {
        if self.data.is_empty() {
            self.event = None;
            return;
        }
        frames.push(SseFrame {
            event: self.event.take(),
            data: self.data.join("\n"),
        });
        self.data.clear();
    }
}

#[derive(Debug, Default)]
pub struct DeepSeekStreamDecoder {
    sse: SseDecoder,
    finished: bool,
}

impl DeepSeekStreamDecoder {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn is_finished(&self) -> bool {
        self.finished
    }

    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<StreamEvent>, ProviderError> {
        let frames = self.sse.push(chunk)?;
        self.parse_frames(frames)
    }

    pub fn finish(&mut self) -> Result<Vec<StreamEvent>, ProviderError> {
        let frames = self.sse.finish()?;
        self.parse_frames(frames)
    }

    fn parse_frames(&mut self, frames: Vec<SseFrame>) -> Result<Vec<StreamEvent>, ProviderError> {
        let mut events = VecDeque::new();
        for frame in frames {
            if frame.data == "[DONE]" {
                self.finished = true;
                events.push_back(StreamEvent::Finished {
                    finish_reason: None,
                });
                continue;
            }

            let chunk: StreamChunk = serde_json::from_str(&frame.data)
                .map_err(|error| ProviderError::InvalidResponse(error.to_string()))?;
            if let Some(raw) = chunk.usage {
                events.push_back(StreamEvent::Usage(raw.normalize()));
            }
            for choice in chunk.choices {
                let tool_calls = choice.delta.tool_calls.unwrap_or_default();
                if choice.delta.content.is_some()
                    || choice.delta.reasoning_content.is_some()
                    || !tool_calls.is_empty()
                {
                    events.push_back(StreamEvent::Delta {
                        content: choice.delta.content,
                        reasoning_content: choice.delta.reasoning_content,
                        tool_calls,
                    });
                }
                if choice.finish_reason.is_some() {
                    events.push_back(StreamEvent::Finished {
                        finish_reason: choice.finish_reason,
                    });
                }
            }
        }
        Ok(events.into())
    }
}

#[cfg(test)]
mod tests {
    use openbad_harness::{ApiFormat, hash_bytes};
    use serde_json::json;
    use uuid::Uuid;

    use super::*;

    fn envelope(model: &str) -> RequestEnvelope {
        RequestEnvelope {
            series_id: Uuid::now_v7(),
            provider: "deepseek".to_owned(),
            model: model.to_owned(),
            api_format: ApiFormat::OpenAi,
            thinking_enabled: true,
            reasoning_effort: Some(ReasoningEffort::High),
            system_hash: hash_bytes(b"system"),
            tools_hash: hash_bytes(b"tools"),
            workspace_hash: None,
            max_output_tokens: 32_000,
        }
    }

    #[test]
    fn deepseek_usage_is_disjoint() {
        let raw = RawUsage {
            prompt_tokens: 100,
            completion_tokens: 50,
            total_tokens: 150,
            prompt_cache_hit_tokens: Some(90),
            prompt_cache_miss_tokens: Some(10),
            ..RawUsage::default()
        };
        let usage = raw.normalize();
        assert_eq!(usage.input_tokens, 10);
        assert_eq!(usage.cache_read_tokens, 90);
        assert_eq!(usage.billed_input(), 100);
    }

    #[test]
    fn openai_style_cached_tokens_are_normalized() {
        let raw = RawUsage {
            prompt_tokens: 100,
            completion_tokens: 20,
            prompt_tokens_details: Some(PromptTokensDetails { cached_tokens: 80 }),
            completion_tokens_details: Some(CompletionTokensDetails {
                reasoning_tokens: 5,
            }),
            ..RawUsage::default()
        };
        let usage = raw.normalize();
        assert_eq!(usage.input_tokens, 20);
        assert_eq!(usage.cache_read_tokens, 80);
        assert_eq!(usage.reasoning_tokens, Some(5));
    }

    #[test]
    fn frozen_request_bytes_are_deterministic() {
        let request = ChatRequest::new(
            crate::deepseek::FLASH_MODEL,
            vec![ChatMessage::user("hello")],
        )
        .with_thinking(Thinking::enabled(), ReasoningEffort::High)
        .with_max_tokens(1024);
        let first = request
            .freeze(envelope(crate::deepseek::FLASH_MODEL))
            .expect("freeze");
        let second = request
            .freeze(envelope(crate::deepseek::FLASH_MODEL))
            .expect("freeze");
        assert_eq!(first.bytes, second.bytes);
    }

    #[test]
    fn reasoning_content_is_serialized_verbatim() {
        let message = ChatMessage::assistant_tool_calls(
            None,
            Some("reasoning text".to_owned()),
            vec![ToolCall::function(
                "call-1",
                "read_file",
                json!({"path": "src/lib.rs"}),
            )],
        );
        let encoded = serde_json::to_value(&message).expect("json");
        assert_eq!(encoded["reasoning_content"], "reasoning text");
        assert_eq!(encoded["tool_calls"][0]["function"]["name"], "read_file");
        let decoded: ChatMessage = serde_json::from_value(encoded).expect("decode");
        assert_eq!(decoded.reasoning_content.as_deref(), Some("reasoning text"));
    }

    #[test]
    fn prefix_completion_is_last_and_assistant_only() {
        let request = ChatRequest::new(
            crate::deepseek::FLASH_MODEL,
            vec![
                ChatMessage::user("write a function"),
                ChatMessage::assistant_prefix("fn main"),
            ],
        );
        assert!(request.validate().is_ok());

        let request = ChatRequest::new(
            crate::deepseek::FLASH_MODEL,
            vec![
                ChatMessage::assistant_prefix("fn main"),
                ChatMessage::user("wrong"),
            ],
        );
        assert!(request.validate().is_err());
    }

    #[test]
    fn non_object_tool_arguments_are_rejected() {
        let message = ChatMessage::assistant_tool_calls(
            None,
            Some("reasoning".to_owned()),
            vec![ToolCall {
                id: "call-1".to_owned(),
                kind: "function".to_owned(),
                function: FunctionCall {
                    name: "read_file".to_owned(),
                    arguments: "[]".to_owned(),
                },
            }],
        );
        let request = ChatRequest::new(crate::deepseek::FLASH_MODEL, vec![message]);
        assert!(request.validate().is_err());
    }

    #[test]
    fn sse_decoder_handles_split_chunks() {
        let mut decoder = DeepSeekStreamDecoder::new();
        let first = decoder
            .push(b"data: {\"choices\":[{\"delta\":{\"content\":\"hel")
            .expect("first");
        assert!(first.is_empty());
        let second = decoder
            .push(b"lo\"},\"finish_reason\":null}]}\n\ndata: [DONE]\n\n")
            .expect("second");
        assert_eq!(
            second,
            vec![
                StreamEvent::Delta {
                    content: Some("hello".to_owned()),
                    reasoning_content: None,
                    tool_calls: Vec::new(),
                },
                StreamEvent::Finished {
                    finish_reason: None
                },
            ]
        );
    }

    #[test]
    fn sse_decoder_reads_reasoning_tool_calls_and_usage() {
        let mut decoder = DeepSeekStreamDecoder::new();
        let events = decoder
            .push(
                b"data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"think\",\"tool_calls\":[{\"index\":0,\"id\":\"call-1\",\"function\":{\"name\":\"read_file\",\"arguments\":\"{\\\"path\\\":\\\"a\\\"}\"}}]},\"finish_reason\":null}]}\n\ndata: {\"choices\":[],\"usage\":{\"prompt_tokens\":100,\"prompt_cache_hit_tokens\":90,\"prompt_cache_miss_tokens\":10,\"completion_tokens\":5}}\n\n",
            )
            .expect("events");
        assert!(matches!(
            &events[0],
            StreamEvent::Delta { reasoning_content, tool_calls, .. }
                if reasoning_content.as_deref() == Some("think") && tool_calls.len() == 1
        ));
        assert!(matches!(
            &events[1],
            StreamEvent::Usage(usage)
                if usage.cache_read_tokens == 90 && usage.input_tokens == 10
        ));
    }
}
