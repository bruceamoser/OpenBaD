import { Channel, invoke } from "@tauri-apps/api/core";

import type {
  AgentEvent,
  ChatReply,
  Foundation,
  FileCheckpointSummary,
  IndexReport,
  SessionSummary,
  SessionView,
  Usage,
  WorkspaceIndexSummary,
} from "./types";

const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let mockKeyConfigured = false;
let mockCancelled = false;
let mockSessionCounter = 1;
let mockCheckpoints: FileCheckpointSummary[] = [];
let mockIndex: WorkspaceIndexSummary = {
  files: 42,
  symbols: 216,
  bytes: 1_284_096,
  indexed_at: Date.now() - 60_000,
};
const mockSessions: SessionSummary[] = [];
const mockMessages = new Map<string, { role: string; text: string; reasoning: string | null }[]>();

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
    schema_version: "0004_file_checkpoints",
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

export async function loadCheckpoints(
  sessionId: string,
): Promise<FileCheckpointSummary[]> {
  if (isTauri) {
    return invoke<FileCheckpointSummary[]>("checkpoints", { sessionId });
  }
  return mockCheckpoints.filter(() => sessionId.length > 0);
}

export async function checkpointDiff(checkpointId: string): Promise<string> {
  if (isTauri) {
    return invoke<string>("checkpoint_diff", { checkpointId });
  }
  const checkpoint = mockCheckpoints.find((item) => item.id === checkpointId);
  return checkpoint
    ? `--- a/${checkpoint.path}\n+++ b/${checkpoint.path}\n@@ -1 +1 @@\n-old content\n+new content\n`
    : "";
}

export async function restoreCheckpoint(checkpointId: string): Promise<string> {
  if (isTauri) {
    return invoke<string>("restore_checkpoint", { checkpointId });
  }
  const checkpoint = mockCheckpoints.find((item) => item.id === checkpointId);
  mockCheckpoints = mockCheckpoints.filter((item) => item.id !== checkpointId);
  return checkpoint?.path ?? "unknown";
}

export async function searchSessions(query: string): Promise<SessionSummary[]> {
  if (isTauri) {
    return invoke<SessionSummary[]>("search_sessions", { query });
  }
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) {
    return [...mockSessions];
  }
  return mockSessions.filter((session) => {
    if ((session.title ?? "").toLowerCase().includes(needle)) {
      return true;
    }
    return (mockMessages.get(session.id) ?? []).some((message) =>
      message.text.toLowerCase().includes(needle),
    );
  });
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
    return;
  }
  mockCancelled = true;
}

export async function workspaceIndexStatus(
  workspacePath: string,
): Promise<WorkspaceIndexSummary> {
  if (isTauri) {
    return invoke<WorkspaceIndexSummary>("workspace_index_status", { workspacePath });
  }
  return { ...mockIndex };
}

export async function refreshWorkspaceIndex(
  workspacePath: string,
): Promise<IndexReport> {
  if (isTauri) {
    return invoke<IndexReport>("refresh_workspace_index", { workspacePath });
  }
  mockIndex = {
    ...mockIndex,
    indexed_at: Date.now(),
  };
  return {
    seen: mockIndex.files,
    added: 0,
    updated: 2,
    removed: 0,
    elapsed_ms: 18,
    summary: { ...mockIndex },
  };
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
  model: string;
  thinking: boolean;
  reasoningEffort: string;
};

export async function chat(
  input: ChatInput,
  onEvent: (event: AgentEvent) => void,
): Promise<ChatReply> {
  if (isTauri) {
    const channel = new Channel<AgentEvent>();
    channel.onmessage = onEvent;
    return invoke<ChatReply>("chat", {
      request: {
        sessionId: input.sessionId,
        workspacePath: input.workspacePath,
        message: input.message,
        allowWrites: input.allowWrites,
        allowCommands: input.allowCommands,
        maxSteps: input.maxSteps,
        model: input.model,
        thinking: input.thinking,
        reasoningEffort: input.reasoningEffort,
      },
      onEvent: channel,
    });
  }
  return mockChat(input, onEvent);
}

async function mockChat(
  input: ChatInput,
  onEvent: (event: AgentEvent) => void,
): Promise<ChatReply> {
  mockCancelled = false;
  const sessionId = input.sessionId ?? `mock-session-${mockSessionCounter++}`;
  const text =
    "I read `Cargo.toml`. It is a virtual workspace with twelve `openbad-*` crates plus the Tauri desktop app. The core crates are store, harness, llm, tools, agent, workspace, governance, memory, knowledge, autonomic, and mcp.";

  onEvent({ type: "step", step: 1 });
  await tick(120);
  for (const chunk of [
    "The user wants a workspace summary. ",
    "I should read Cargo.toml first. ",
    "Then I can group the crates by role.",
  ]) {
    onEvent({ type: "reasoning", step: 1, text: chunk });
    await tick(90);
  }
  onEvent({
    type: "tool_call",
    step: 1,
    name: "read_file",
    arguments: '{"path":"Cargo.toml"}',
  });
  await tick(140);
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
    await tick(45);
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
      { role: "user", text: input.message, reasoning: null },
      { role: "assistant", text, reasoning: "The user wants a workspace summary." },
    ]);
  }

  if (mockCheckpoints.length === 0) {
    mockCheckpoints.push({
      id: `mock-checkpoint-${Date.now()}`,
      path: "/mnt/disk4/repos/OpenBaD/src/main.rs",
      existed: true,
      bytes: 128,
      created_at: Date.now(),
    });
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

async function tick(milliseconds: number): Promise<void> {
  await delay(milliseconds);
  if (mockCancelled) {
    throw new Error("agent turn was cancelled");
  }
}
