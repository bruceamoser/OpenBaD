//! Replay durable session events into the model-visible message history.

use openbad_core::event::{SessionEvent, SessionEventKind};
use serde_json::Value;

use crate::chat::{ChatMessage, ChatRole, ToolCall, sanitize_tool_arguments};

/// A message derived from the durable log, with the sequence that produced it.
#[derive(Debug, Clone, PartialEq)]
pub struct DerivedMessage {
    pub seq: u64,
    pub message: ChatMessage,
    /// The message is a compaction checkpoint; a later replacement absorbs it.
    pub checkpoint: bool,
}

/// Projects durable events into the model-visible history.
///
/// Surface events append a message. A compaction checkpoint first shadows the
/// inclusive range named by `surface_op.replace`, then appends its summary.
/// Older checkpoints are always absorbed, matching the store projection.
#[must_use]
pub fn derive_messages(events: &[SessionEvent]) -> Vec<DerivedMessage> {
    let mut messages: Vec<DerivedMessage> = Vec::new();
    for event in events {
        if !event.surface {
            continue;
        }
        if let Some((start, end)) = replace_range(event) {
            messages.retain(|entry| !entry.checkpoint && (entry.seq < start || entry.seq > end));
        }
        if let Some(message) = message_from_event(event) {
            messages.push(DerivedMessage {
                seq: event.seq,
                message,
                checkpoint: event.kind == SessionEventKind::CompactionSummary,
            });
        }
    }
    // The newest checkpoint replaces a prefix of the surface, so it leads the
    // retained tail. This mirrors `Store::load_surface_messages`.
    if let Some(index) = messages.iter().rposition(|entry| entry.checkpoint) {
        let checkpoint = messages.remove(index);
        let insert_at = usize::from(
            messages
                .first()
                .is_some_and(|entry| entry.message.role == ChatRole::System),
        );
        messages.insert(insert_at, checkpoint);
    }
    messages
}

/// The inclusive sequence range shadowed by this event, when it shadows one.
#[must_use]
pub fn replace_range(event: &SessionEvent) -> Option<(u64, u64)> {
    if let Some(replace) = event
        .payload
        .get("surface_op")
        .and_then(|op| op.get("replace"))
    {
        let start = replace.get("start_seq").and_then(Value::as_u64)?;
        let end = replace.get("end_seq").and_then(Value::as_u64)?;
        return Some((start, end));
    }
    if event.kind == SessionEventKind::CompactionSummary {
        let cutoff = event.payload.get("shadowed_through_seq")?.as_u64()?;
        return Some((0, cutoff));
    }
    None
}

fn message_from_event(event: &SessionEvent) -> Option<ChatMessage> {
    let payload = &event.payload;
    match event.kind {
        SessionEventKind::UserMessage | SessionEventKind::CompactionSummary => {
            Some(ChatMessage::user(text(payload, "content")))
        }
        SessionEventKind::SystemMessage | SessionEventKind::DeveloperMessage => {
            Some(ChatMessage::system(text(payload, "content")))
        }
        SessionEventKind::AssistantMessage => {
            let mut tool_calls = payload
                .get("tool_calls")
                .filter(|value| value.is_array())
                .cloned()
                .and_then(|value| serde_json::from_value::<Vec<ToolCall>>(value).ok());
            if let Some(calls) = tool_calls.as_mut() {
                sanitize_tool_arguments(calls);
            }
            Some(ChatMessage {
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
        SessionEventKind::ToolResult => Some(ChatMessage::tool(
            text(payload, "tool_call_id"),
            text(payload, "content"),
        )),
        _ => None,
    }
}

fn text(payload: &Value, key: &str) -> String {
    payload
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

#[cfg(test)]
mod tests {
    use openbad_core::{SessionId, event::NewEvent};
    use serde_json::json;
    use uuid::Uuid;

    use super::*;

    fn event(seq: u64, draft: NewEvent) -> SessionEvent {
        SessionEvent {
            id: Uuid::nil(),
            session_id: SessionId::from(Uuid::nil()),
            seq,
            turn: draft.turn,
            step: draft.step,
            kind: draft.kind,
            surface: draft.kind.is_surface(),
            payload: draft.payload,
            created_at: 0,
        }
    }

    #[test]
    fn surface_events_project_in_order() {
        let events = vec![
            event(
                1,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_payload(json!({"content": "hello"})),
            ),
            event(
                2,
                NewEvent::new(SessionEventKind::AssistantMessage).with_payload(json!({
                    "content": "hi",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{\"path\":\"a\"}"}
                    }]
                })),
            ),
            event(
                3,
                NewEvent::new(SessionEventKind::ToolResult).with_payload(json!({
                    "content": "file body",
                    "tool_call_id": "call-1"
                })),
            ),
            event(
                4,
                NewEvent::new(SessionEventKind::TurnEnd)
                    .with_payload(json!({"reason": "complete"})),
            ),
        ];

        let derived = derive_messages(&events);
        assert_eq!(
            derived
                .iter()
                .map(|entry| entry.message.role)
                .collect::<Vec<_>>(),
            vec![ChatRole::User, ChatRole::Assistant, ChatRole::Tool]
        );
        assert_eq!(derived[2].message.tool_call_id.as_deref(), Some("call-1"));
    }

    #[test]
    fn a_checkpoint_shadows_its_range_and_older_checkpoints() {
        let events = vec![
            event(
                1,
                NewEvent::new(SessionEventKind::UserMessage)
                    .with_payload(json!({"content": "first"})),
            ),
            event(
                2,
                NewEvent::new(SessionEventKind::CompactionSummary).with_payload(json!({
                    "content": "summary one",
                    "shadowed_through_seq": 1
                })),
            ),
            event(
                3,
                NewEvent::new(SessionEventKind::AssistantMessage)
                    .with_payload(json!({"content": "second"})),
            ),
            event(
                4,
                NewEvent::new(SessionEventKind::CompactionSummary).with_payload(json!({
                    "content": "summary two",
                    "shadowed_through_seq": 3,
                    "surface_op": {"replace": {"start_seq": 0, "end_seq": 3}}
                })),
            ),
        ];

        let derived = derive_messages(&events);
        assert_eq!(derived.len(), 1, "only the newest checkpoint survives");
        assert_eq!(
            derived[0].message.content.as_deref(),
            Some("summary two"),
            "the older checkpoint is absorbed even though the range starts at zero"
        );
    }
}
