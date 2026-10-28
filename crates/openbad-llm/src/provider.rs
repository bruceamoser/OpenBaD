use openbad_harness::RequestEnvelope;
use serde::{Deserialize, Serialize};

/// How a provider exposes prefix caching.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheMode {
    Automatic,
    Explicit,
    None,
}

/// Capabilities a provider may expose. The harness degrades gracefully when a
/// capability is absent.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderCapabilities {
    pub chat: bool,
    pub tools: bool,
    pub thinking: bool,
    pub vision: bool,
    pub streaming: bool,
    pub json_mode: bool,
    pub strict_tools: bool,
    pub files_api: bool,
    pub search_api: bool,
    pub fetch_api: bool,
    pub fim: bool,
    pub prefix_completion: bool,
    pub system_prompt_update_in_history: bool,
    pub prefix_cache: CacheMode,
    pub max_context: u32,
    pub max_output: u32,
}

/// A model provider seam.
pub trait ModelProvider: Send + Sync {
    fn id(&self) -> &str;
    fn capabilities(&self) -> ProviderCapabilities;
}

/// Normalized token accounting. Input counts are disjoint: `input_tokens` is
/// uncached input only.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
    pub output_tokens: u64,
    pub reasoning_tokens: Option<u64>,
}

impl Usage {
    #[must_use]
    pub fn billed_input(&self) -> u64 {
        self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
    }

    #[must_use]
    pub fn cache_hit_share(&self) -> f64 {
        let billed = self.billed_input();
        if billed == 0 {
            0.0
        } else {
            self.cache_read_tokens as f64 / billed as f64
        }
    }
}

/// The exact bytes of a frozen request, reused for prefix-preserving retries.
#[derive(Debug, Clone)]
pub struct FrozenRequest {
    pub bytes: Vec<u8>,
    pub envelope: RequestEnvelope,
}

#[derive(Debug, thiserror::Error)]
pub enum ProviderError {
    #[error("provider request failed: {0}")]
    Request(String),
    #[error("provider request was invalid: {0}")]
    InvalidRequest(String),
    #[error("provider response was invalid: {0}")]
    InvalidResponse(String),
    #[error("provider does not support capability: {0}")]
    Unsupported(String),
    #[error("provider request was cancelled")]
    Cancelled,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_hit_share_uses_billed_input() {
        let usage = Usage {
            input_tokens: 10,
            cache_read_tokens: 90,
            cache_write_tokens: 0,
            output_tokens: 50,
            reasoning_tokens: None,
        };
        assert_eq!(usage.billed_input(), 100);
        assert!((usage.cache_hit_share() - 0.9).abs() < f64::EPSILON);
    }

    #[test]
    fn empty_usage_has_zero_hit_share() {
        assert_eq!(Usage::default().cache_hit_share(), 0.0);
    }
}
