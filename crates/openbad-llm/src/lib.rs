//! Model provider seams and provider implementations.

pub mod chat;
pub mod deepseek;
pub mod fim;
pub mod pricing;
pub mod provider;
pub mod replay;
pub mod retry;
pub mod transport;

pub use chat::{
    ChatMessage, ChatRequest, ChatResponse, DeepSeekStreamDecoder, ModelResponse, StreamEvent,
    drop_empty_assistant_messages,
};
pub use deepseek::{DeepSeekClient, DeepSeekProvider, FLASH_MODEL, PRO_MODEL};
pub use fim::{FIM_MAX_OUTPUT_TOKENS, FimChoice, FimRequest, FimResponse};
pub use pricing::{DEEPSEEK_FLASH, DEEPSEEK_V4_PRO, ModelPricing, PeakCalendar, UsageTotals};
pub use provider::{
    CacheMode, FrozenRequest, ModelProvider, ProviderCapabilities, ProviderError, Usage,
};
pub use replay::{DerivedMessage, derive_messages};
pub use retry::{RequestAttempts, RetryPolicy, is_retryable};
pub use transport::{HttpRequest, HttpResponse, HttpTransport, ReqwestTransport};
