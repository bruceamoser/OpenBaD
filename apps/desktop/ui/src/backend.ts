import { Channel, invoke } from "@tauri-apps/api/core";

import type {
  AgentEvent,
  ChatReply,
  Foundation,
  SessionSummary,
  SessionView,
  Usage,
} from "./types";

const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let mockKeyConfigured = false;
let mockSessionCounter = 1;
const mockSessions: SessionSummary[] = [];
const mockMessages = new Map<string, { role: string; text: string }[]>();

const mockUsage: Usage = {
  input_tokens: 428,
  cache_read_tokens: 1_920,
  cache_write_tokens: 0,
  output_tokens: 86,
  reasoning_tokens: 42,
  cost_usd: 0.00072,
};

const mockFoundation: Foundation = {
  app_version: "0.1.0-mock",
  doctor: {
    schema_version: "0002_phase1_schema",
    integrity: "ok",
    database_bytes: 131_072,
    event_count: 42,
  },
  audit: { valid: true, records: 7, first_invalid: null },
  providers: [{ id: "mock-deepseek", name: "DeepSeek", kind: "deepseek", enabled: true }],
  routes: [
    {
      id: "mock-chat",
      name: "chat-default",
      model: "deepseek-flash",
      task_class: "chat",
    },
  ],
};

export async function workspaceDefault(): Promise<string> {
  return isTauri ? invoke<string>("workspace_default") : "/mnt/disk4/repos/OpenBaD";
}

export async function loadFoundation(): Promise<Foundation> {
  return isTauri ? invoke<Foundation>("foundation") : mockFoundation;
}

export async function loadSessions(): Promise<SessionSummary[]> {
  return isTauri ? invoke<SessionSummary[]>("sessions") : [...mockSessions];
}

export async function loadSession(sessionId: string): Promise<SessionView> {
  if (isTauri) {
    return invoke<SessionView>("session_view", { sessionId });
  }
  return {
    session_id: sessionId,
    messages: mockMessages.get(sessionId) ?? [],
  };
}

export async function cancelChat(): Promise<void> {
  if (isTauri) {
    await invoke("cancel_chat");
  }
}

export async function apiKeyStatus(): Promise<boolean> {
  return isTauri ? invoke<boolean>("api_key_status") : mockKeyConfigured;
}

export async function saveApiKey(key: string): Promise<void> {
  if (isTauri) {
    await invoke("save_api_key", { key });
    return;
  }
  mockKeyConfigured = key.trim().length > 0;
}

export async function clearApiKey(): Promise<void> {
  if (isTauri) {
    await invoke("clear_api_key");
    return;
  }
  mockKeyConfigured = false;
}

export type ChatInput = {
  sessionId: string | null;
  workspacePath: string;
  message: string;
  allowWrites: boolean;
  allowCommands: boolean;
  maxSteps: number;
};

export async function chat(
  input: ChatInput,
  onEvent: (event: AgentEvent) => void,
): Promise<ChatReply> {
  if (isTauri) {
    const channel = new Channel<AgentEvent>();
    channel.onmessage = onEvent;
    return invoke<ChatReply>("chat", {
      sessionId: input.sessionId,
      workspacePath: input.workspacePath,
      message: input.message,
      allowWrites: input.allowWrites,
      allowCommands: input.allowCommands,
      maxSteps: input.maxSteps,
      onEvent: channel,
    });
  }
  return mockChat(input, onEvent);
}

async function mockChat(
  input: ChatInput,
  onEvent: (event: AgentEvent) => void,
): Promise<ChatReply> {
  const sessionId = input.sessionId ?? `mock-session-${mockSessionCounter++}`;
  const text =
    "I read `Cargo.toml`. It is a virtual workspace with twelve `openbad-*` crates plus the Tauri desktop app. The core crates are store, harness, llm, tools, agent, workspace, governance, memory, knowledge, autonomic, and mcp.";

  onEvent({ type: "step", step: 1 });
  await delay(120);
  for (const chunk of [
    "The user wants a workspace summary. ",
    "I should read Cargo.toml first. ",
    "Then I can group the crates by role.",
  ]) {
    onEvent({ type: "reasoning", step: 1, text: chunk });
    await delay(90);
  }
  onEvent({
    type: "tool_call",
    step: 1,
    name: "read_file",
    arguments: '{"path":"Cargo.toml"}',
  });
  await delay(140);
  onEvent({
    type: "tool_result",
    step: 1,
    name: "read_file",
    content: "[workspace] members = [...12 crates...]",
    is_error: false,
  });
  onEvent({ type: "step", step: 2 });
  for (const chunk of text.match(/.{1,36}/g) ?? [text]) {
    onEvent({ type: "content", step: 2, text: chunk });
    await delay(45);
  }
  onEvent({ type: "usage", usage: mockUsage });

  if (input.sessionId === null) {
    mockSessions.unshift({
      id: sessionId,
      workspace_id: "mock-workspace",
      workspace_path: input.workspacePath,
      title: input.message.slice(0, 80),
      status: "active",
      updated_at: Date.now(),
      last_turn: 1,
    });
    mockMessages.set(sessionId, [
      { role: "user", text: input.message },
      { role: "assistant", text },
    ]);
  }

  return {
    session_id: sessionId,
    workspace_id: "mock-workspace",
    text,
    steps: 2,
    usage: mockUsage,
    tool_trace: ["[workspace] members = [...12 crates...]"],
  };
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
