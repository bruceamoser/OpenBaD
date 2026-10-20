use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use openbad_agent::{AgentConfig, AgentObserver, default_agent};
use openbad_core::SessionId;
use openbad_core::event::{NewEvent, SessionEventKind};
#[cfg(feature = "keyring")]
use openbad_governance::secrets::KeychainSecretStore;
#[cfg(not(feature = "keyring"))]
use openbad_governance::secrets::MemorySecretStore;
use openbad_governance::secrets::{Secret, SecretStore};
use openbad_llm::chat::{ChatMessage, ChatRole, ToolCall};
use openbad_llm::{DeepSeekClient, FLASH_MODEL, ReqwestTransport, UsageTotals};
use openbad_store::Store;
use openbad_store::types::{
    AuditReport, DoctorReport, ModelRouteRecord, NewModelRoute, NewProvider, ProviderRecord,
    SessionMessage, SessionSummary,
};
use openbad_workspace::WorkspaceService;
use serde::Serialize;
use serde_json::{Value, json};
use tauri::State;
use tauri::ipc::Channel;
use uuid::Uuid;

const API_KEY_REFERENCE: &str = "openbad/deepseek/api_key";
const USER_ID_SETTING: &str = "install_user_id";

pub struct AppState {
    store: Mutex<Store>,
    secrets: Box<dyn SecretStore>,
    cancel: Arc<AtomicBool>,
}

#[derive(Serialize)]
pub struct Health {
    pub status: &'static str,
    pub schema_version: String,
}

#[derive(Serialize)]
pub struct Foundation {
    pub app_version: &'static str,
    pub doctor: DoctorReport,
    pub audit: AuditReport,
    pub providers: Vec<ProviderRecord>,
    pub routes: Vec<ModelRouteRecord>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AgentEvent {
    Step {
        step: u32,
    },
    Reasoning {
        step: u32,
        text: String,
    },
    Content {
        step: u32,
        text: String,
    },
    ToolCall {
        step: u32,
        name: String,
        arguments: String,
    },
    ToolResult {
        step: u32,
        name: String,
        content: String,
        is_error: bool,
    },
    Usage {
        usage: UsageTotals,
    },
}

struct ChannelObserver {
    channel: Channel<AgentEvent>,
    cancel: Arc<AtomicBool>,
}

impl AgentObserver for ChannelObserver {
    fn on_step(&self, step: u32) {
        let _ = self.channel.send(AgentEvent::Step { step });
    }

    fn on_reasoning(&self, step: u32, text: &str) {
        let _ = self.channel.send(AgentEvent::Reasoning {
            step,
            text: text.to_owned(),
        });
    }

    fn on_content(&self, step: u32, text: &str) {
        let _ = self.channel.send(AgentEvent::Content {
            step,
            text: text.to_owned(),
        });
    }

    fn on_tool_call(&self, step: u32, call: &openbad_llm::chat::ToolCall) {
        let _ = self.channel.send(AgentEvent::ToolCall {
            step,
            name: call.function.name.clone(),
            arguments: call.function.arguments.clone(),
        });
    }

    fn on_tool_result(&self, step: u32, name: &str, content: &str, is_error: bool) {
        let _ = self.channel.send(AgentEvent::ToolResult {
            step,
            name: name.to_owned(),
            content: content.to_owned(),
            is_error,
        });
    }

    fn on_usage(&self, usage: &UsageTotals) {
        let _ = self.channel.send(AgentEvent::Usage { usage: *usage });
    }

    fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
}

#[derive(Serialize)]
pub struct ChatReply {
    pub session_id: String,
    pub workspace_id: String,
    pub text: String,
    pub steps: u32,
    pub usage: UsageTotals,
    pub tool_trace: Vec<String>,
}

#[derive(Serialize)]
pub struct UiMessage {
    pub role: String,
    pub text: String,
}

#[derive(Serialize)]
pub struct SessionView {
    pub session_id: String,
    pub messages: Vec<UiMessage>,
}

#[tauri::command]
fn sessions(state: State<'_, AppState>) -> Result<Vec<SessionSummary>, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    store.list_sessions(50).map_err(|error| error.to_string())
}

#[tauri::command]
fn session_view(state: State<'_, AppState>, session_id: String) -> Result<SessionView, String> {
    let session = SessionId::from(
        Uuid::parse_str(&session_id).map_err(|error| format!("invalid session id: {error}"))?,
    );
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let history = load_history(&store, session)?;
    Ok(SessionView {
        session_id,
        messages: history
            .iter()
            .filter_map(|message| match message.role {
                ChatRole::User => Some(UiMessage {
                    role: "user".to_owned(),
                    text: message.content.clone().unwrap_or_default(),
                }),
                ChatRole::Assistant => Some(UiMessage {
                    role: "assistant".to_owned(),
                    text: message
                        .content
                        .clone()
                        .filter(|text| !text.is_empty())
                        .unwrap_or_else(|| "(tool activity)".to_owned()),
                }),
                _ => None,
            })
            .collect(),
    })
}

#[tauri::command]
fn health(state: State<'_, AppState>) -> Result<Health, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    let schema_version = store
        .schema_version()
        .map_err(|error| format!("could not read schema version: {error}"))?;
    Ok(Health {
        status: "ok",
        schema_version,
    })
}

#[tauri::command]
fn foundation(state: State<'_, AppState>) -> Result<Foundation, String> {
    let store = state
        .store
        .lock()
        .map_err(|error| format!("store lock poisoned: {error}"))?;
    foundation_payload(&store)
}

#[tauri::command]
fn workspace_default() -> String {
    std::env::current_dir()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|_| ".".to_owned())
}

#[tauri::command]
fn save_api_key(state: State<'_, AppState>, key: String) -> Result<(), String> {
    if key.trim().is_empty() {
        return Err("API key must not be empty".to_owned());
    }
    state
        .secrets
        .set(API_KEY_REFERENCE, &Secret::new(key.trim()))
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn cancel_chat(state: State<'_, AppState>) -> Result<(), String> {
    state.cancel.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
fn clear_api_key(state: State<'_, AppState>) -> Result<(), String> {
    state
        .secrets
        .delete(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn api_key_status(state: State<'_, AppState>) -> Result<bool, String> {
    let stored = state
        .secrets
        .get(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())?
        .is_some();
    Ok(stored || std::env::var_os("DEEPSEEK_API_KEY").is_some())
}

#[tauri::command]
async fn chat(
    state: State<'_, AppState>,
    session_id: Option<String>,
    workspace_path: String,
    message: String,
    allow_writes: bool,
    allow_commands: bool,
    on_event: Channel<AgentEvent>,
) -> Result<ChatReply, String> {
    let message = message.trim().to_owned();
    if message.is_empty() {
        return Err("message must not be empty".to_owned());
    }
    let workspace_path = PathBuf::from(workspace_path);
    if !workspace_path.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            workspace_path.display()
        ));
    }

    let api_key = resolve_api_key(&state)?;
    state.cancel.store(false, Ordering::SeqCst);

    let (session, workspace_id, history, turn) = {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;

        let (session, workspace_id) = match session_id {
            Some(value) => {
                let session = SessionId::from(
                    Uuid::parse_str(&value)
                        .map_err(|error| format!("invalid session id: {error}"))?,
                );
                let workspace = store
                    .session_workspace_id(session)
                    .map_err(|error| error.to_string())?
                    .map(|workspace| workspace.to_string())
                    .unwrap_or_default();
                (session, workspace)
            }
            None => {
                let name = workspace_path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("workspace");
                let workspace = store
                    .ensure_workspace(name, &workspace_path.to_string_lossy())
                    .map_err(|error| error.to_string())?;
                let title = truncate_title(&message);
                let session = store
                    .create_session(Some(workspace), Some(&title))
                    .map_err(|error| error.to_string())?;
                (session, workspace.to_string())
            }
        };

        let turn = store
            .load_turns(session)
            .map_err(|error| error.to_string())?
            .len() as u32
            + 1;
        let mut history = load_history(&store, session)?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnStart).with_turn(turn),
            )
            .map_err(|error| error.to_string())?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_turn(turn)
                    .with_step(1)
                    .with_payload(json!({"content": message})),
            )
            .map_err(|error| error.to_string())?;
        history.push(ChatMessage::user(message.clone()));

        let user_id = ensure_user_id(&store)?;
        (session, workspace_id, (history, user_id), turn)
    };

    let (history, user_id) = history;
    let base_url = std::env::var("OPENBAD_DEEPSEEK_BASE_URL")
        .unwrap_or_else(|_| "https://api.deepseek.com".to_owned());
    let client = DeepSeekClient::new(ReqwestTransport::new(), api_key)
        .with_model(FLASH_MODEL)
        .with_base_url(base_url)
        .with_user_id(user_id);
    let agent = default_agent(
        client,
        Arc::new(WorkspaceService::new(workspace_path)),
        AgentConfig {
            allow_writes,
            allow_commands,
            ..AgentConfig::default()
        },
    );

    let observer = ChannelObserver {
        channel: on_event,
        cancel: state.cancel.clone(),
    };
    let run = match agent.run_with_observer(history, &observer).await {
        Ok(run) => run,
        Err(error) => {
            if let Ok(store) = state.store.lock() {
                let _ = store.append_event(
                    session,
                    NewEvent::new(SessionEventKind::Error)
                        .with_turn(turn)
                        .with_payload(json!({"message": error.to_string()})),
                );
                let _ = store.append_event(
                    session,
                    NewEvent::new(SessionEventKind::TurnEnd)
                        .with_turn(turn)
                        .with_payload(json!({"reason": "error"})),
                );
            }
            return Err(error.to_string());
        }
    };

    let mut tool_trace = Vec::new();
    {
        let store = state
            .store
            .lock()
            .map_err(|error| format!("store lock poisoned: {error}"))?;

        for message in &run.new_messages {
            match message.role {
                ChatRole::Assistant => {
                    let payload = json!({
                        "content": message.content,
                        "reasoning_content": message.reasoning_content,
                        "tool_calls": message.tool_calls,
                    });
                    store
                        .append_event(
                            session,
                            NewEvent::new(SessionEventKind::AssistantMessage)
                                .with_turn(turn)
                                .with_step(run.steps)
                                .with_payload(payload),
                        )
                        .map_err(|error| error.to_string())?;
                }
                ChatRole::Tool => {
                    let content = message.content.clone().unwrap_or_default();
                    if !content.is_empty() {
                        tool_trace.push(truncate_for_ui(&content));
                    }
                    store
                        .append_event(
                            session,
                            NewEvent::new(SessionEventKind::ToolResult)
                                .with_turn(turn)
                                .with_step(run.steps)
                                .with_payload(json!({
                                    "content": content,
                                    "tool_call_id": message.tool_call_id,
                                })),
                        )
                        .map_err(|error| error.to_string())?;
                }
                _ => {}
            }
        }

        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::UsageRecorded)
                    .with_turn(turn)
                    .with_payload(json!({
                        "input_tokens": run.usage.input_tokens,
                        "cache_read_tokens": run.usage.cache_read_tokens,
                        "output_tokens": run.usage.output_tokens,
                        "cost_usd": run.usage.cost_usd,
                    })),
            )
            .map_err(|error| error.to_string())?;
        store
            .append_event(
                session,
                NewEvent::new(SessionEventKind::TurnEnd)
                    .with_turn(turn)
                    .with_payload(json!({"reason": "complete"})),
            )
            .map_err(|error| error.to_string())?;
    }

    Ok(ChatReply {
        session_id: session.to_string(),
        workspace_id,
        text: run.text,
        steps: run.steps,
        usage: run.usage,
        tool_trace,
    })
}

fn resolve_api_key(state: &AppState) -> Result<String, String> {
    if let Some(secret) = state
        .secrets
        .get(API_KEY_REFERENCE)
        .map_err(|error| error.to_string())?
    {
        return Ok(secret.expose().to_owned());
    }
    std::env::var("DEEPSEEK_API_KEY").map_err(|_| {
        "No DeepSeek API key configured. Save one in Settings or set DEEPSEEK_API_KEY.".to_owned()
    })
}

fn ensure_user_id(store: &Store) -> Result<String, String> {
    if let Some(value) = store
        .get_setting(USER_ID_SETTING)
        .map_err(|error| error.to_string())?
    {
        if let Some(value) = value.as_str() {
            return Ok(value.to_owned());
        }
    }
    let value = Uuid::now_v7().to_string();
    store
        .set_setting(USER_ID_SETTING, json!(value))
        .map_err(|error| error.to_string())?;
    Ok(value)
}

fn load_history(store: &Store, session: SessionId) -> Result<Vec<ChatMessage>, String> {
    let messages = store
        .load_messages(session)
        .map_err(|error| error.to_string())?;
    messages
        .iter()
        .map(chat_message_from_session)
        .collect::<Result<Vec<_>, _>>()
}

fn chat_message_from_session(message: &SessionMessage) -> Result<ChatMessage, String> {
    let payload = &message.content;
    match message.role.as_str() {
        "user" => Ok(ChatMessage::user(
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        "system" | "developer" => Ok(ChatMessage::system(
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        "assistant" => {
            let tool_calls = payload
                .get("tool_calls")
                .filter(|value| value.is_array())
                .cloned()
                .map(serde_json::from_value::<Vec<ToolCall>>)
                .transpose()
                .map_err(|error| format!("invalid tool calls in history: {error}"))?;
            Ok(ChatMessage {
                role: ChatRole::Assistant,
                content: payload
                    .get("content")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                reasoning_content: payload
                    .get("reasoning_content")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                tool_calls,
                tool_call_id: None,
                prefix: None,
            })
        }
        "tool" => Ok(ChatMessage::tool(
            payload
                .get("tool_call_id")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            payload
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )),
        other => Err(format!("unsupported stored message role: {other}")),
    }
}

fn truncate_title(message: &str) -> String {
    let mut title: String = message.chars().take(80).collect();
    if message.chars().count() > 80 {
        title.push('…');
    }
    title
}

fn truncate_for_ui(content: &str) -> String {
    let mut preview: String = content.chars().take(400).collect();
    if content.chars().count() > 400 {
        preview.push('…');
    }
    preview
}

fn foundation_payload(store: &Store) -> Result<Foundation, String> {
    Ok(Foundation {
        app_version: env!("CARGO_PKG_VERSION"),
        doctor: store
            .doctor()
            .map_err(|error| format!("doctor failed: {error}"))?,
        audit: store
            .audit_verify()
            .map_err(|error| format!("audit verification failed: {error}"))?,
        providers: store
            .list_providers()
            .map_err(|error| format!("could not list providers: {error}"))?,
        routes: store
            .list_model_routes()
            .map_err(|error| format!("could not list model routes: {error}"))?,
    })
}

fn secret_store() -> Box<dyn SecretStore> {
    #[cfg(feature = "keyring")]
    {
        Box::new(KeychainSecretStore::new("OpenBaD Desktop"))
    }
    #[cfg(not(feature = "keyring"))]
    {
        Box::new(MemorySecretStore::new())
    }
}

fn ensure_defaults(store: &Store) -> Result<(), String> {
    if !store
        .list_providers()
        .map_err(|error| format!("could not list providers: {error}"))?
        .is_empty()
    {
        return Ok(());
    }

    let provider = store
        .create_provider(
            NewProvider::new("deepseek", "DeepSeek")
                .with_base_url("https://api.deepseek.com")
                .with_keychain_ref(API_KEY_REFERENCE),
        )
        .map_err(|error| format!("could not create the DeepSeek provider: {error}"))?;

    store
        .create_model_route(NewModelRoute::new(
            "chat-default",
            "chat",
            provider.id,
            "deepseek-flash",
        ))
        .map_err(|error| format!("could not create the default chat route: {error}"))?;
    store
        .create_model_route(NewModelRoute::new(
            "reasoning-default",
            "reasoning",
            provider.id,
            "deepseek-v4-pro",
        ))
        .map_err(|error| format!("could not create the default reasoning route: {error}"))?;

    Ok(())
}

pub fn run() {
    let store = Store::open_default().expect("failed to open the OpenBaD store");
    ensure_defaults(&store).expect("failed to seed default providers");

    tauri::Builder::default()
        .manage(AppState {
            store: Mutex::new(store),
            secrets: secret_store(),
            cancel: Arc::new(AtomicBool::new(false)),
        })
        .invoke_handler(tauri::generate_handler![
            health,
            foundation,
            workspace_default,
            save_api_key,
            clear_api_key,
            cancel_chat,
            api_key_status,
            sessions,
            session_view,
            chat
        ])
        .run(tauri::generate_context!())
        .expect("error while running OpenBaD Desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn foundation_reports_seeded_defaults() {
        let store = Store::open_in_memory().expect("store");
        ensure_defaults(&store).expect("defaults");

        let payload = foundation_payload(&store).expect("foundation");
        assert_eq!(payload.doctor.schema_version, "0002_phase1_schema");
        assert_eq!(payload.doctor.integrity, "ok");
        assert!(payload.audit.valid);
        assert_eq!(payload.providers.len(), 1);
        assert_eq!(payload.providers[0].kind, "deepseek");
        assert_eq!(payload.routes.len(), 2);
        assert!(
            payload
                .routes
                .iter()
                .any(|route| route.model == "deepseek-flash")
        );
        assert!(
            payload
                .routes
                .iter()
                .any(|route| route.model == "deepseek-v4-pro")
        );
    }

    #[test]
    fn defaults_are_idempotent() {
        let store = Store::open_in_memory().expect("store");
        ensure_defaults(&store).expect("first");
        ensure_defaults(&store).expect("second");
        assert_eq!(store.list_providers().expect("providers").len(), 1);
        assert_eq!(store.list_model_routes().expect("routes").len(), 2);
    }

    #[test]
    fn stored_history_round_trips_assistant_and_tool_messages() {
        let message = ChatMessage {
            role: ChatRole::Assistant,
            content: None,
            reasoning_content: Some("reasoning".to_owned()),
            tool_calls: Some(vec![ToolCall::function(
                "call-1",
                "read_file",
                json!({"path": "README.md"}),
            )]),
            tool_call_id: None,
            prefix: None,
        };
        let session_message = SessionMessage {
            session_id: SessionId::new(),
            seq: 1,
            turn: Some(1),
            step: Some(1),
            role: "assistant".to_owned(),
            content: json!({
                "content": message.content,
                "reasoning_content": message.reasoning_content,
                "tool_calls": message.tool_calls,
            }),
            source: None,
            created_at: 0,
        };

        let restored = chat_message_from_session(&session_message).expect("restore");
        assert_eq!(restored.role, ChatRole::Assistant);
        assert_eq!(restored.reasoning_content.as_deref(), Some("reasoning"));
        assert_eq!(
            restored.tool_calls.expect("tool calls")[0].function.name,
            "read_file"
        );
    }
}
