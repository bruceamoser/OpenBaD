use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Wire format used for a request series.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApiFormat {
    OpenAi,
    Anthropic,
    Responses,
}

/// DeepSeek reasoning effort for thinking mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasoningEffort {
    Low,
    High,
    Max,
}

/// The cache identity of one request series.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RequestEnvelope {
    pub series_id: Uuid,
    pub provider: String,
    pub model: String,
    pub api_format: ApiFormat,
    pub thinking_enabled: bool,
    pub reasoning_effort: Option<ReasoningEffort>,
    pub system_hash: String,
    pub tools_hash: String,
    pub workspace_hash: Option<String>,
    pub max_output_tokens: u32,
}

impl RequestEnvelope {
    /// Returns true when the cache-breaking fields match.
    #[must_use]
    pub fn is_same_series(&self, other: &Self) -> bool {
        self.series_id == other.series_id
            && self.provider == other.provider
            && self.model == other.model
            && self.api_format == other.api_format
            && self.thinking_enabled == other.thinking_enabled
            && self.reasoning_effort == other.reasoning_effort
            && self.system_hash == other.system_hash
            && self.tools_hash == other.tools_hash
            && self.workspace_hash == other.workspace_hash
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SeriesChangeReason {
    FirstRequest,
    SeriesId,
    Provider,
    Model,
    ApiFormat,
    Thinking,
    ReasoningEffort,
    SystemPrompt,
    ToolSchemas,
    Workspace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SeriesChange {
    Same,
    New { reason: SeriesChangeReason },
}

/// Tracks the cache identity of a conversation and reports when a request must
/// start a new provider-side prefix.
#[derive(Debug, Default)]
pub struct SeriesGuard {
    current: Option<RequestEnvelope>,
}

impl SeriesGuard {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn current(&self) -> Option<&RequestEnvelope> {
        self.current.as_ref()
    }

    pub fn reset(&mut self) {
        self.current = None;
    }

    pub fn observe(&mut self, next: RequestEnvelope) -> SeriesChange {
        let Some(current) = &self.current else {
            self.current = Some(next);
            return SeriesChange::New {
                reason: SeriesChangeReason::FirstRequest,
            };
        };

        let reason = if current.series_id != next.series_id {
            Some(SeriesChangeReason::SeriesId)
        } else if current.provider != next.provider {
            Some(SeriesChangeReason::Provider)
        } else if current.model != next.model {
            Some(SeriesChangeReason::Model)
        } else if current.api_format != next.api_format {
            Some(SeriesChangeReason::ApiFormat)
        } else if current.thinking_enabled != next.thinking_enabled
            || current.reasoning_effort != next.reasoning_effort
        {
            Some(SeriesChangeReason::Thinking)
        } else if current.system_hash != next.system_hash {
            Some(SeriesChangeReason::SystemPrompt)
        } else if current.tools_hash != next.tools_hash {
            Some(SeriesChangeReason::ToolSchemas)
        } else if current.workspace_hash != next.workspace_hash {
            Some(SeriesChangeReason::Workspace)
        } else {
            None
        };

        if let Some(reason) = reason {
            self.current = Some(next);
            SeriesChange::New { reason }
        } else {
            if let Some(current) = &mut self.current {
                current.max_output_tokens = next.max_output_tokens;
            }
            SeriesChange::Same
        }
    }
}

/// BLAKE3 hex digest, used for section and tool-schema hashes.
#[must_use]
pub fn hash_bytes(bytes: &[u8]) -> String {
    blake3::hash(bytes).to_hex().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn envelope(series_id: Uuid) -> RequestEnvelope {
        RequestEnvelope {
            series_id,
            provider: "deepseek".to_owned(),
            model: "deepseek-flash".to_owned(),
            api_format: ApiFormat::OpenAi,
            thinking_enabled: true,
            reasoning_effort: Some(ReasoningEffort::High),
            system_hash: hash_bytes(b"system"),
            tools_hash: hash_bytes(b"tools"),
            workspace_hash: Some(hash_bytes(b"workspace")),
            max_output_tokens: 32_000,
        }
    }

    #[test]
    fn same_series_ignores_output_budget() {
        let series = Uuid::now_v7();
        let mut left = envelope(series);
        let right = envelope(series);
        left.max_output_tokens = 64_000;
        assert!(left.is_same_series(&right));
    }

    #[test]
    fn changing_the_model_breaks_the_series() {
        let series = Uuid::now_v7();
        let left = envelope(series);
        let mut right = envelope(series);
        right.model = "deepseek-v4-pro".to_owned();
        assert!(!left.is_same_series(&right));
    }

    #[test]
    fn series_guard_reports_the_first_breaking_field() {
        let series = Uuid::now_v7();
        let mut guard = SeriesGuard::new();
        assert_eq!(
            guard.observe(envelope(series)),
            SeriesChange::New {
                reason: SeriesChangeReason::FirstRequest
            }
        );
        assert_eq!(guard.observe(envelope(series)), SeriesChange::Same);

        let mut thinking = envelope(series);
        thinking.reasoning_effort = Some(ReasoningEffort::Max);
        assert_eq!(
            guard.observe(thinking),
            SeriesChange::New {
                reason: SeriesChangeReason::Thinking
            }
        );

        let mut tools = envelope(series);
        tools.reasoning_effort = Some(ReasoningEffort::Max);
        tools.tools_hash = hash_bytes(b"new-tools");
        assert_eq!(
            guard.observe(tools),
            SeriesChange::New {
                reason: SeriesChangeReason::ToolSchemas
            }
        );

        let mut model = envelope(series);
        model.reasoning_effort = Some(ReasoningEffort::Max);
        model.tools_hash = hash_bytes(b"new-tools");
        model.model = "deepseek-v4-pro".to_owned();
        assert_eq!(
            guard.observe(model),
            SeriesChange::New {
                reason: SeriesChangeReason::Model
            }
        );
    }

    #[test]
    fn series_guard_ignores_output_budget_changes() {
        let series = Uuid::now_v7();
        let mut guard = SeriesGuard::new();
        let first = envelope(series);
        let mut second = envelope(series);
        second.max_output_tokens = 64_000;
        guard.observe(first);
        assert_eq!(guard.observe(second), SeriesChange::Same);
        assert_eq!(guard.current().expect("current").max_output_tokens, 64_000);
    }
}
