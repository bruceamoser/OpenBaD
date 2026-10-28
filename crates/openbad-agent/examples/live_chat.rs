//! Run one live agent turn against DeepSeek.
//!
//! ```bash
//! cargo run -p openbad-agent --example live_chat -- "Summarize Cargo.toml"
//! ```

use std::sync::Arc;

use openbad_agent::{AgentConfig, AgentObserver, default_agent};
use openbad_governance::secrets::{KeychainSecretStore, SecretStore};
use openbad_llm::chat::ChatMessage;
use openbad_llm::pricing::UsageTotals;
use openbad_llm::{DeepSeekClient, FLASH_MODEL, ReqwestTransport};
use openbad_workspace::WorkspaceService;

const API_KEY_REFERENCE: &str = "openbad/deepseek/api_key";

struct ConsoleObserver;

impl AgentObserver for ConsoleObserver {
    fn on_step(&self, step: u32) {
        println!("\n[step {step}]");
    }

    fn on_reasoning(&self, _step: u32, text: &str) {
        print!("{text}");
    }

    fn on_content(&self, _step: u32, text: &str) {
        print!("{text}");
    }

    fn on_tool_call(&self, _step: u32, call: &openbad_llm::chat::ToolCall) {
        println!(
            "\n[tool] {} {}",
            call.function.name, call.function.arguments
        );
    }

    fn on_tool_result(
        &self,
        _step: u32,
        call: &openbad_llm::chat::ToolCall,
        content: &str,
        is_error: bool,
        _metadata: &serde_json::Value,
    ) {
        let name = call.function.name.as_str();
        println!(
            "\n[tool result] {name} (error: {is_error}): {}",
            content.chars().take(500).collect::<String>()
        );
    }

    fn on_usage(&self, usage: &UsageTotals) {
        println!(
            "\n[usage] input={} cache_read={} output={} cost=${:.4}",
            usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.cost_usd
        );
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let prompt = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
    let prompt = if prompt.is_empty() {
        "Read Cargo.toml and reply with the number of workspace members. Do not change files."
            .to_owned()
    } else {
        prompt
    };

    let api_key = match KeychainSecretStore::new("OpenBaD Desktop").get(API_KEY_REFERENCE)? {
        Some(secret) => secret.expose().to_owned(),
        None => std::env::var("DEEPSEEK_API_KEY")
            .map_err(|_| "no keychain key and no DEEPSEEK_API_KEY")?,
    };
    let workspace = Arc::new(WorkspaceService::new(std::env::current_dir()?));
    let client = DeepSeekClient::new(ReqwestTransport::new(), api_key)
        .with_model(FLASH_MODEL)
        .with_thinking(true);
    let agent = default_agent(
        client,
        workspace,
        AgentConfig {
            max_steps: 8,
            ..AgentConfig::default()
        },
    );
    let run = agent
        .run_with_observer(vec![ChatMessage::user(prompt)], &ConsoleObserver)
        .await?;
    println!("\n\n[final]\n{}", run.text);
    Ok(())
}
