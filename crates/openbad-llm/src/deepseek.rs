use std::collections::VecDeque;

use futures_util::StreamExt;
use futures_util::stream::{self, BoxStream};
use openbad_harness::{ApiFormat, ReasoningEffort};
use serde_json::Value;

use crate::chat::{
    ChatMessage, ChatRequest, ChatResponse, DeepSeekStreamDecoder, ModelResponse, StreamEvent,
    Thinking,
};
use crate::fim::{FimRequest, FimResponse};
use crate::provider::{
    CacheMode, FrozenRequest, ModelProvider, ProviderCapabilities, ProviderError,
};
use crate::retry::{RequestAttempts, RetryPolicy, backoff};
use crate::transport::{HttpRequest, HttpTransport};

pub const FLASH_MODEL: &str = "deepseek-flash";
pub const PRO_MODEL: &str = "deepseek-v4-pro";
pub const OPENAI_BASE_URL: &str = "https://api.deepseek.com";
pub const ANTHROPIC_BASE_URL: &str = "https://api.deepseek.com/anthropic";
pub const BETA_BASE_URL: &str = "https://api.deepseek.com/beta";

/// DeepSeek provider metadata.
#[derive(Debug, Clone, Copy)]
pub struct DeepSeekProvider {
    model: &'static str,
    api_format: ApiFormat,
}

impl DeepSeekProvider {
    #[must_use]
    pub fn flash() -> Self {
        Self {
            model: FLASH_MODEL,
            api_format: ApiFormat::OpenAi,
        }
    }

    #[must_use]
    pub fn pro() -> Self {
        Self {
            model: PRO_MODEL,
            api_format: ApiFormat::OpenAi,
        }
    }

    #[must_use]
    pub fn with_api_format(mut self, api_format: ApiFormat) -> Self {
        self.api_format = api_format;
        self
    }

    #[must_use]
    pub fn model(&self) -> &'static str {
        self.model
    }

    #[must_use]
    pub fn api_format(&self) -> ApiFormat {
        self.api_format
    }

    #[must_use]
    pub fn base_url(&self) -> &'static str {
        match self.api_format {
            ApiFormat::OpenAi | ApiFormat::Responses => OPENAI_BASE_URL,
            ApiFormat::Anthropic => ANTHROPIC_BASE_URL,
        }
    }
}

impl ModelProvider for DeepSeekProvider {
    fn id(&self) -> &str {
        "deepseek"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            chat: true,
            tools: true,
            thinking: true,
            vision: self.model == FLASH_MODEL,
            streaming: true,
            json_mode: true,
            strict_tools: true,
            files_api: true,
            search_api: true,
            fetch_api: true,
            fim: true,
            prefix_completion: true,
            system_prompt_update_in_history: true,
            prefix_cache: CacheMode::Automatic,
            max_context: 1_000_000,
            max_output: 384_000,
        }
    }
}

/// A cache-aware DeepSeek chat client.
pub struct DeepSeekClient<T: HttpTransport> {
    transport: T,
    api_key: String,
    base_url: String,
    model: String,
    user_id: Option<String>,
    thinking: bool,
    reasoning_effort: Option<ReasoningEffort>,
    retry: RetryPolicy,
}

impl<T: HttpTransport> DeepSeekClient<T> {
    #[must_use]
    pub fn new(transport: T, api_key: impl Into<String>) -> Self {
        Self {
            transport,
            api_key: api_key.into(),
            base_url: OPENAI_BASE_URL.to_owned(),
            model: FLASH_MODEL.to_owned(),
            user_id: None,
            thinking: true,
            reasoning_effort: None,
            retry: RetryPolicy::default(),
        }
    }

    #[must_use]
    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = model.into();
        self
    }

    #[must_use]
    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into();
        self
    }

    #[must_use]
    pub fn with_user_id(mut self, user_id: impl Into<String>) -> Self {
        self.user_id = Some(user_id.into());
        self
    }

    #[must_use]
    pub fn with_thinking(mut self, enabled: bool) -> Self {
        self.thinking = enabled;
        self
    }

    #[must_use]
    pub fn with_reasoning_effort(mut self, effort: ReasoningEffort) -> Self {
        self.reasoning_effort = Some(effort);
        self
    }

    #[must_use]
    pub fn with_retry_policy(mut self, retry: RetryPolicy) -> Self {
        self.retry = retry;
        self
    }

    #[must_use]
    pub fn transport(&self) -> &T {
        &self.transport
    }

    #[must_use]
    pub fn model(&self) -> &str {
        &self.model
    }

    #[must_use]
    pub fn thinking_enabled(&self) -> bool {
        self.thinking
    }

    #[must_use]
    pub fn reasoning_effort(&self) -> Option<ReasoningEffort> {
        self.reasoning_effort
    }

    #[must_use]
    pub fn build_request(
        &self,
        messages: Vec<ChatMessage>,
        tools: Option<Vec<Value>>,
        max_tokens: Option<u32>,
        streaming: bool,
    ) -> ChatRequest {
        let mut request = ChatRequest::new(&self.model, messages);
        request = if self.thinking {
            request.with_thinking(
                Thinking::enabled(),
                self.reasoning_effort.unwrap_or(ReasoningEffort::High),
            )
        } else {
            request.thinking = Some(Thinking::disabled());
            request
        };
        if let Some(tools) = tools {
            request = request.with_tools(tools);
        }
        if let Some(max_tokens) = max_tokens {
            request = request.with_max_tokens(max_tokens);
        }
        if let Some(user_id) = &self.user_id {
            request = request.with_user_id(user_id.clone());
        }
        if streaming {
            request = request.streaming();
        }
        request
    }

    pub async fn complete(&self, frozen: FrozenRequest) -> Result<ModelResponse, ProviderError> {
        self.complete_with(frozen, None).await
    }

    /// Complete while reporting each failed attempt to `attempts`.
    pub async fn complete_with(
        &self,
        frozen: FrozenRequest,
        attempts: Option<&dyn RequestAttempts>,
    ) -> Result<ModelResponse, ProviderError> {
        let response = self.post_with_retry(&frozen, attempts).await?;
        let parsed: ChatResponse = serde_json::from_slice(&response.body)
            .map_err(|error| ProviderError::InvalidResponse(error.to_string()))?;
        let choice =
            parsed.choices.into_iter().next().ok_or_else(|| {
                ProviderError::InvalidResponse("response had no choices".to_owned())
            })?;

        Ok(ModelResponse {
            id: parsed.id,
            model: parsed
                .model
                .unwrap_or_else(|| frozen.envelope.model.clone()),
            message: choice.message,
            finish_reason: choice.finish_reason,
            usage: parsed.usage.unwrap_or_default().normalize(),
        })
    }

    pub async fn complete_fim(&self, request: FimRequest) -> Result<FimResponse, ProviderError> {
        request.validate()?;
        let body = serde_json::to_vec(&request)
            .map_err(|error| ProviderError::InvalidRequest(error.to_string()))?;
        let response = self
            .post_with_retry_request(
                HttpRequest {
                    url: format!("{}/beta/completions", self.base_url.trim_end_matches('/')),
                    headers: self.headers(),
                    body,
                },
                None,
            )
            .await?;
        serde_json::from_slice(&response.body)
            .map_err(|error| ProviderError::InvalidResponse(error.to_string()))
    }

    pub async fn stream(
        &self,
        frozen: FrozenRequest,
    ) -> Result<BoxStream<'static, Result<StreamEvent, ProviderError>>, ProviderError> {
        self.stream_with(frozen, None).await
    }

    /// Stream while reporting each failed attempt to `attempts`.
    pub async fn stream_with(
        &self,
        frozen: FrozenRequest,
        attempts: Option<&dyn RequestAttempts>,
    ) -> Result<BoxStream<'static, Result<StreamEvent, ProviderError>>, ProviderError> {
        let bytes = self.stream_with_retry(&frozen, attempts).await?;
        let decoder = DeepSeekStreamDecoder::new();
        let pending: VecDeque<StreamEvent> = VecDeque::new();

        let stream = stream::unfold(
            (bytes, decoder, pending),
            |(mut bytes, mut decoder, mut pending)| async move {
                loop {
                    if let Some(event) = pending.pop_front() {
                        return Some((Ok(event), (bytes, decoder, pending)));
                    }

                    match bytes.next().await {
                        Some(Ok(chunk)) => match decoder.push(&chunk) {
                            Ok(events) => pending.extend(events),
                            Err(error) => return Some((Err(error), (bytes, decoder, pending))),
                        },
                        Some(Err(error)) => return Some((Err(error), (bytes, decoder, pending))),
                        None => {
                            match decoder.finish() {
                                Ok(events) => pending.extend(events),
                                Err(error) => return Some((Err(error), (bytes, decoder, pending))),
                            }
                            if let Some(event) = pending.pop_front() {
                                return Some((Ok(event), (bytes, decoder, pending)));
                            }
                            return None;
                        }
                    }
                }
            },
        );

        Ok(Box::pin(stream))
    }

    async fn post_with_retry(
        &self,
        frozen: &FrozenRequest,
        attempts: Option<&dyn RequestAttempts>,
    ) -> Result<crate::transport::HttpResponse, ProviderError> {
        self.post_with_retry_request(self.http_request(frozen), attempts)
            .await
    }

    async fn post_with_retry_request(
        &self,
        request: HttpRequest,
        listener: Option<&dyn RequestAttempts>,
    ) -> Result<crate::transport::HttpResponse, ProviderError> {
        let mut attempts = 0;
        loop {
            let body = request.body.clone();
            match self
                .transport
                .post(HttpRequest {
                    url: request.url.clone(),
                    headers: request.headers.clone(),
                    body,
                })
                .await
            {
                Ok(response) if (200..300).contains(&response.status) => return Ok(response),
                Ok(response) => {
                    let status = response.status;
                    if let Some(delay) = self.retry.next_delay(attempts, Some(status), None) {
                        if let Some(listener) = listener {
                            listener.retrying(attempts + 1, Some(status), delay);
                        }
                        backoff(delay, listener).await?;
                        attempts += 1;
                        continue;
                    }
                    return Err(ProviderError::Request(format!(
                        "HTTP {status}: {}",
                        String::from_utf8_lossy(&response.body)
                    )));
                }
                Err(error) => {
                    let status = provider_error_status(&error);
                    if let Some(delay) = self.retry.next_delay(attempts, status, None) {
                        if let Some(listener) = listener {
                            listener.retrying(attempts + 1, status, delay);
                        }
                        backoff(delay, listener).await?;
                        attempts += 1;
                        continue;
                    }
                    return Err(error);
                }
            }
        }
    }

    async fn stream_with_retry(
        &self,
        frozen: &FrozenRequest,
        listener: Option<&dyn RequestAttempts>,
    ) -> Result<crate::transport::ByteStream, ProviderError> {
        let mut attempts = 0;
        loop {
            match self.transport.post_stream(self.http_request(frozen)).await {
                Ok(bytes) => return Ok(bytes),
                Err(error) => {
                    let status = provider_error_status(&error);
                    if let Some(delay) = self.retry.next_delay(attempts, status, None) {
                        if let Some(listener) = listener {
                            listener.retrying(attempts + 1, status, delay);
                        }
                        backoff(delay, listener).await?;
                        attempts += 1;
                        continue;
                    }
                    return Err(error);
                }
            }
        }
    }

    fn http_request(&self, frozen: &FrozenRequest) -> HttpRequest {
        HttpRequest {
            url: format!("{}/chat/completions", self.base_url.trim_end_matches('/')),
            headers: self.headers(),
            body: frozen.bytes.clone(),
        }
    }

    fn headers(&self) -> Vec<(String, String)> {
        vec![
            (
                "Authorization".to_owned(),
                format!("Bearer {}", self.api_key),
            ),
            ("Content-Type".to_owned(), "application/json".to_owned()),
        ]
    }
}

fn provider_error_status(error: &ProviderError) -> Option<u16> {
    let ProviderError::Request(message) = error else {
        return None;
    };
    message
        .strip_prefix("HTTP ")?
        .split(':')
        .next()?
        .trim()
        .parse()
        .ok()
}

/// DeepSeek reports cache hits and misses as disjoint prompt counts.
#[must_use]
pub fn normalize_usage(
    cache_miss_tokens: u64,
    cache_hit_tokens: u64,
    output_tokens: u64,
) -> crate::provider::Usage {
    crate::provider::Usage {
        input_tokens: cache_miss_tokens,
        cache_read_tokens: cache_hit_tokens,
        cache_write_tokens: 0,
        output_tokens,
        reasoning_tokens: None,
    }
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::sync::Mutex;

    use crate::transport::ByteStream;
    use async_trait::async_trait;
    use bytes::Bytes;
    use openbad_harness::{ApiFormat, RequestEnvelope, hash_bytes};
    use serde_json::json;
    use uuid::Uuid;

    use super::*;
    use crate::chat::{ChatMessage, ChatRequest};

    #[derive(Default)]
    struct MockTransport {
        response: Option<crate::transport::HttpResponse>,
        chunks: Vec<Vec<u8>>,
        requests: Mutex<Vec<HttpRequest>>,
    }

    #[async_trait]
    impl HttpTransport for MockTransport {
        async fn post(
            &self,
            request: HttpRequest,
        ) -> Result<crate::transport::HttpResponse, ProviderError> {
            self.requests.lock().expect("requests").push(request);
            self.response
                .clone()
                .ok_or_else(|| ProviderError::Request("no mock response".to_owned()))
        }

        async fn post_stream(&self, request: HttpRequest) -> Result<ByteStream, ProviderError> {
            self.requests.lock().expect("requests").push(request);
            let chunks = self.chunks.clone();
            Ok(Box::pin(stream::iter(
                chunks.into_iter().map(|chunk| Ok(Bytes::from(chunk))),
            )))
        }
    }

    #[derive(Default)]
    struct SequenceTransport {
        responses: Mutex<VecDeque<crate::transport::HttpResponse>>,
        requests: Mutex<Vec<HttpRequest>>,
    }

    #[async_trait]
    impl HttpTransport for SequenceTransport {
        async fn post(
            &self,
            request: HttpRequest,
        ) -> Result<crate::transport::HttpResponse, ProviderError> {
            self.requests.lock().expect("requests").push(request);
            self.responses
                .lock()
                .expect("responses")
                .pop_front()
                .ok_or_else(|| ProviderError::Request("no mock response".to_owned()))
        }

        async fn post_stream(&self, _request: HttpRequest) -> Result<ByteStream, ProviderError> {
            Err(ProviderError::Request("not implemented".to_owned()))
        }
    }

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

    fn request() -> ChatRequest {
        ChatRequest::new(FLASH_MODEL, vec![ChatMessage::user("hello")])
            .with_thinking(Thinking::enabled(), ReasoningEffort::High)
    }

    #[tokio::test]
    async fn complete_parses_message_and_usage() {
        let transport = MockTransport {
            response: Some(crate::transport::HttpResponse {
                status: 200,
                body: br#"{
                    "id": "resp-1",
                    "model": "deepseek-flash",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "hi",
                            "reasoning_content": "thinking"
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": 100,
                        "prompt_cache_hit_tokens": 90,
                        "prompt_cache_miss_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 105
                    }
                }"#
                .to_vec(),
            }),
            ..MockTransport::default()
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let frozen = request().freeze(envelope(FLASH_MODEL)).expect("freeze");
        let response = client.complete(frozen).await.expect("complete");

        assert_eq!(response.message.content.as_deref(), Some("hi"));
        assert_eq!(
            response.message.reasoning_content.as_deref(),
            Some("thinking")
        );
        assert_eq!(response.usage.cache_read_tokens, 90);
        assert_eq!(response.usage.input_tokens, 10);
    }

    #[tokio::test]
    async fn complete_retries_rate_limits_and_server_errors() {
        let transport = SequenceTransport {
            responses: Mutex::new(VecDeque::from([
                crate::transport::HttpResponse {
                    status: 429,
                    body: b"slow down".to_vec(),
                },
                crate::transport::HttpResponse {
                    status: 503,
                    body: b"unavailable".to_vec(),
                },
                crate::transport::HttpResponse {
                    status: 200,
                    body: br#"{"choices":[{"message":{"role":"assistant","content":"ok"}}]}"#
                        .to_vec(),
                },
            ])),
            requests: Mutex::new(Vec::new()),
        };
        let client =
            DeepSeekClient::new(transport, "sk-test").with_retry_policy(RetryPolicy::new(3, 1, 1));
        let frozen = request().freeze(envelope(FLASH_MODEL)).expect("freeze");
        let response = client.complete(frozen).await.expect("complete");

        assert_eq!(response.message.content.as_deref(), Some("ok"));
        assert_eq!(client.transport.requests.lock().expect("requests").len(), 3);
    }

    #[tokio::test]
    async fn complete_does_not_retry_client_errors() {
        let transport = SequenceTransport {
            responses: Mutex::new(VecDeque::from([crate::transport::HttpResponse {
                status: 401,
                body: b"bad key".to_vec(),
            }])),
            requests: Mutex::new(Vec::new()),
        };
        let client =
            DeepSeekClient::new(transport, "sk-test").with_retry_policy(RetryPolicy::new(5, 1, 1));
        let frozen = request().freeze(envelope(FLASH_MODEL)).expect("freeze");
        let error = client.complete(frozen).await.expect_err("auth error");

        assert!(error.to_string().contains("401"));
        assert_eq!(client.transport.requests.lock().expect("requests").len(), 1);
    }

    #[tokio::test]
    async fn complete_sends_auth_header_and_frozen_bytes() {
        let transport = MockTransport {
            response: Some(crate::transport::HttpResponse {
                status: 200,
                body: br#"{"choices":[{"message":{"role":"assistant","content":"ok"}}]}"#.to_vec(),
            }),
            ..MockTransport::default()
        };
        let client =
            DeepSeekClient::new(transport, "sk-test").with_base_url("https://example.test/");
        let frozen = request().freeze(envelope(FLASH_MODEL)).expect("freeze");
        let body = frozen.bytes.clone();
        client.complete(frozen).await.expect("complete");

        let requests = &client.transport.requests;
        let requests = requests.lock().expect("requests");
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].url, "https://example.test/chat/completions");
        assert!(
            requests[0]
                .headers
                .iter()
                .any(|(name, value)| name == "Authorization" && value == "Bearer sk-test")
        );
        assert_eq!(requests[0].body, body);
    }

    #[tokio::test]
    async fn stream_decodes_reasoning_and_usage() {
        let transport = MockTransport {
            chunks: vec![
                b"data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"think\"},\"finish_reason\":null}]}\n\n".to_vec(),
                b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"},\"finish_reason\":\"stop\"}]}\n\ndata: {\"choices\":[],\"usage\":{\"prompt_tokens\":10,\"prompt_cache_hit_tokens\":9,\"prompt_cache_miss_tokens\":1,\"completion_tokens\":2}}\n\ndata: [DONE]\n\n".to_vec(),
            ],
            ..MockTransport::default()
        };
        let client = DeepSeekClient::new(transport, "sk-test");
        let frozen = request()
            .streaming()
            .freeze(envelope(FLASH_MODEL))
            .expect("freeze");
        let events: Vec<_> = client.stream(frozen).await.expect("stream").collect().await;

        assert!(events.iter().all(Result::is_ok));
        assert!(matches!(
            &events[0],
            Ok(StreamEvent::Delta { reasoning_content, .. })
                if reasoning_content.as_deref() == Some("think")
        ));
        assert!(matches!(
            &events[1],
            Ok(StreamEvent::Delta { content, .. }) if content.as_deref() == Some("hi")
        ));
        assert!(events.iter().any(|event| matches!(
            event,
            Ok(StreamEvent::Usage(usage)) if usage.cache_read_tokens == 9
        )));
    }

    #[test]
    fn flash_supports_vision_and_pro_does_not() {
        assert!(DeepSeekProvider::flash().capabilities().vision);
        assert!(!DeepSeekProvider::pro().capabilities().vision);
    }

    #[test]
    fn deepseek_reports_one_million_context() {
        let capabilities = DeepSeekProvider::flash().capabilities();
        assert_eq!(capabilities.max_context, 1_000_000);
        assert_eq!(capabilities.max_output, 384_000);
        assert_eq!(capabilities.prefix_cache, CacheMode::Automatic);
    }

    #[test]
    fn usage_normalization_keeps_cache_hits_disjoint() {
        let usage = normalize_usage(10, 90, 50);
        assert_eq!(usage.billed_input(), 100);
        assert!((usage.cache_hit_share() - 0.9).abs() < f64::EPSILON);
    }

    #[test]
    fn request_includes_tools_and_user_id() {
        let client =
            DeepSeekClient::new(MockTransport::default(), "sk-test").with_user_id("install-123");
        let request = client.build_request(
            vec![ChatMessage::user("hi")],
            Some(vec![
                json!({"type": "function", "function": {"name": "read_file"}}),
            ]),
            Some(2048),
            true,
        );
        let value = serde_json::to_value(&request).expect("json");
        assert_eq!(value["user_id"], "install-123");
        assert_eq!(value["stream"], true);
        assert_eq!(value["tools"][0]["function"]["name"], "read_file");
        assert_eq!(value["max_tokens"], 2048);
    }
}
