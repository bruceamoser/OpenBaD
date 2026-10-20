use serde::{Deserialize, Serialize};

use crate::chat::RawUsage;
use crate::deepseek::FLASH_MODEL;
use crate::provider::ProviderError;

pub const FIM_MAX_OUTPUT_TOKENS: u32 = 4_096;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FimRequest {
    pub model: String,
    pub prompt: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub suffix: Option<String>,
    pub max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream: Option<bool>,
}

impl FimRequest {
    #[must_use]
    pub fn new(prompt: impl Into<String>) -> Self {
        Self {
            model: FLASH_MODEL.to_owned(),
            prompt: prompt.into(),
            suffix: None,
            max_tokens: FIM_MAX_OUTPUT_TOKENS,
            stream: None,
        }
    }

    #[must_use]
    pub fn with_suffix(mut self, suffix: impl Into<String>) -> Self {
        self.suffix = Some(suffix.into());
        self
    }

    #[must_use]
    pub fn with_max_tokens(mut self, max_tokens: u32) -> Self {
        self.max_tokens = max_tokens;
        self
    }

    pub fn validate(&self) -> Result<(), ProviderError> {
        if self.model != FLASH_MODEL {
            return Err(ProviderError::InvalidRequest(
                "FIM is only supported by deepseek-flash".to_owned(),
            ));
        }
        if self.prompt.is_empty() {
            return Err(ProviderError::InvalidRequest(
                "FIM prompt must not be empty".to_owned(),
            ));
        }
        if self.max_tokens == 0 || self.max_tokens > FIM_MAX_OUTPUT_TOKENS {
            return Err(ProviderError::InvalidRequest(format!(
                "FIM max_tokens must be 1..={FIM_MAX_OUTPUT_TOKENS}"
            )));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FimChoice {
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FimResponse {
    #[serde(default)]
    pub choices: Vec<FimChoice>,
    #[serde(default)]
    pub usage: Option<RawUsage>,
}

impl FimResponse {
    #[must_use]
    pub fn normalized_usage(&self) -> crate::provider::Usage {
        self.usage.clone().unwrap_or_default().normalize()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fim_defaults_to_flash_and_the_four_k_limit() {
        let request = FimRequest::new("fn main() {");
        assert_eq!(request.model, FLASH_MODEL);
        assert_eq!(request.max_tokens, FIM_MAX_OUTPUT_TOKENS);
        assert!(request.validate().is_ok());
    }

    #[test]
    fn fim_rejects_pro_and_oversized_output() {
        let mut request = FimRequest::new("prompt");
        request.model = crate::deepseek::PRO_MODEL.to_owned();
        assert!(request.validate().is_err());

        let request = FimRequest::new("prompt").with_max_tokens(FIM_MAX_OUTPUT_TOKENS + 1);
        assert!(request.validate().is_err());
    }
}
