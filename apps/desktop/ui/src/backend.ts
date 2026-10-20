import { Channel, invoke } from "@tauri-apps/api/core";

import type {
  AgentEvent,
  ChatReply,
  Foundation,
  FileCheckpointSummary,
  IndexReport,
  MemoryRecord,
  SessionSummary,
  SessionView,
  Usage,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let mockKeyConfigured = false;
let mockCancelled = false;
let mockSessionCounter = 1;
let mockCheckpoints: FileCheckpointSummary[] = [];
let mockMemories: MemoryRecord[] = [
  {
    id: "mock-memory-1",
    content: "Keep the model-visible prompt prefix immutable.",
    tags: ["cache", "decision"],
    pinned: true,
    created_at: Date.now() - 86_400_000,
    updated_at: Date.now() - 3_600_000,
    access_count: 2,
  },
];
let mockIndex: WorkspaceIndexSummary = {
  files: 42,
  symbols: 216,
  bytes: 1_284_096,
  indexed_at: Date.now() - 60_000,
};
const mockSessions: SessionSummary[] = [];
let mockProjects: WorkspaceSummary[] = [
  {
    id: "mock-workspace",
    name: "OpenBaD",
    path: "/mnt/disk4/repos/OpenBaD",
    created_at: Date.now() - 86_400_000,
    updated_at: Date.now(),
    session_count: 0,
  },
];
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
    schema_version: "0005_project_memory",
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

export async function listProjects(): Promise<WorkspaceSummary[]> {
  return isTauri ? invoke<WorkspaceSummary[]>("list_projects") : [...mockProjects];
}

export async function addProject(
  path: string,
  name?: string,
): Promise<WorkspaceSummary> {
  if (isTauri) {
    return invoke<WorkspaceSummary>("add_project", { path, name });
  }
  const existing = mockProjects.find((project) => project.path === path);
  if (existing) {
    return existing;
  }
  const project: WorkspaceSummary = {
    id: `mock-project-${mockProjects.length + 1}`,
    name: name?.trim() || path.split("/").filter(Boolean).at(-1) || "Project",
    path,
    created_at: Date.now(),
    updated_at: Date.now(),
    session_count: 0,
  };
  mockProjects = [project, ...mockProjects];
  return project;
}

export async function renameProject(projectId: string, name: string): Promise<void> {
  if (isTauri) {
    await invoke("rename_project", { projectId, name });
    return;
  }
  mockProjects = mockProjects.map((project) =>
    project.id === projectId ? { ...project, name } : project,
  );
}

export async function removeProject(projectId: string): Promise<void> {
  if (isTauri) {
    await invoke("remove_project", { projectId });
    return;
  }
  mockProjects = mockProjects.filter((project) => project.id !== projectId);
  mockSessions.splice(0, mockSessions.length, ...mockSessions.filter((s) => s.workspace_id !== projectId));
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  if (isTauri) {
    await invoke("rename_session", { sessionId, title });
    return;
  }
  const session = mockSessions.find((item) => item.id === sessionId);
  if (session) {
    session.title = title;
  }
}

export async function loadSessions(): Promise<SessionSummary[]> {
  return isTauri ? invoke<SessionSummary[]>("sessions") : [...mockSessions];
}

export async function loadMemories(workspacePath: string): Promise<MemoryRecord[]> {
  if (isTauri) {
    return invoke<MemoryRecord[]>("memories", { workspacePath });
  }
  return [...mockMemories];
}

export async function forgetMemory(memoryId: string): Promise<void> {
  if (isTauri) {
    await invoke("forget_memory", { memoryId });
    return;
  }
  mockMemories = mockMemories.filter((memory) => memory.id !== memoryId);
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
    const project = mockProjects.find((item) => item.id === "mock-workspace");
    if (project) {
      project.session_count += 1;
    }
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
