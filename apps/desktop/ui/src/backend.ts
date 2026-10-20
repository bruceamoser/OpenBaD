import { Channel, invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";

import type {
  AccessMode,
  AgentEvent,
  ChatReply,
  Foundation,
  FileCheckpointSummary,
  IndexReport,
  MemoryRecord,
  OutsideAccessGrant,
  SessionSummary,
  SessionView,
  KnowledgeDocument,
  KnowledgeDocumentSummary,
  ProjectFileView,
  ProjectHit,
  ProjectManifest,
  ProjectOverview,
  ProjectPlan,
  ProjectQuestion,
  ProjectRepo,
  ProjectTodo,
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
    origin_turn: 4,
    origin_task: "phase-1",
    author: "agent",
    scope: "project",
    supersedes: null,
    expires_at: null,
  },
];
let mockAccessGrants: OutsideAccessGrant[] = [];
let mockRepos: ProjectRepo[] = [
  {
    id: "mock-repo-1",
    workspace_id: "mock-workspace",
    name: "vscode",
    path: "/mnt/disk4/repos/vscode",
    read_only: true,
    created_at: Date.now() - 7_200_000,
  },
];
let mockKnowledge: KnowledgeDocumentSummary[] = [
  {
    id: "mock-knowledge-1",
    workspace_id: "mock-workspace",
    title: "Product thesis",
    kind: "note",
    source: null,
    tags: ["desktop", "focus"],
    characters: 184,
    chunks: 1,
    confidence: "verified",
    source_hash: null,
    supersedes: null,
    review_status: "accepted",
    created_at: Date.now() - 7_200_000,
    updated_at: Date.now() - 7_200_000,
  },
];
const mockKnowledgeContent = new Map<string, string>([
  [
    "mock-knowledge-1",
    "OpenBaD is a desktop coding harness. Projects own one workspace plus reference folders, a knowledge library, retrieval corpus, and durable memories. Keep the chat focused and open visual artifacts beside it.",
  ],
]);

let mockFiles = [
  "apps/desktop/ui/src/App.tsx",
  "apps/desktop/ui/src/backend.ts",
  "crates/openbad-agent/src/lib.rs",
  "crates/openbad-store/src/store.rs",
  "docs/architecture/00-vision-and-feature-set.md",
];
let mockPlan: ProjectPlan = {
  goal: "Ship the project intelligence tranche",
  status: "draft",
  steps: [
    {
      id: "1",
      text: "Add plan state and approval",
      status: "done",
      verify_with: "cargo test --workspace",
      evidence: "tests passed",
    },
    {
      id: "2",
      text: "Render the Plan panel",
      status: "doing",
      verify_with: null,
      evidence: null,
    },
    {
      id: "3",
      text: "Verify approval flow",
      status: "todo",
      verify_with: "pnpm test",
      evidence: null,
    },
  ],
  created_at: Date.now() - 7_200_000,
  updated_at: Date.now() - 3_600_000,
  approved_at: null,
};

let mockQuestions: ProjectQuestion[] = [
  {
    id: "mock-question-1",
    question: "Should project knowledge be exported to Markdown?",
    options: ["Yes, git-backed", "No, database only"],
    multi: false,
    context: "Knowledge review workflow",
    created_at: Date.now() - 3_600_000,
    answer: null,
    answered_at: null,
  },
];

const mockManifest: ProjectManifest = {
  name: "OpenBaD",
  description: "Desktop coding harness with project-scoped context.",
  scope: {
    authoritative: ["crates/**", "apps/**", "docs/**"],
    generated: ["target/**", "apps/desktop/ui/dist/**"],
  },
  commands: {
    verify: "cargo test --workspace",
    web: "pnpm --dir apps/desktop/ui test:e2e",
  },
  recipes: {
    verify: {
      command: "cargo test --workspace",
      description: "Unit and integration tests",
    },
    lint: "cargo clippy --workspace --all-targets -- -D warnings",
  },
  validators: [{ pattern: "**/*.yaml", command: "cargo run -- check-yaml" }],
  gates: {
    release: { description: "Release checks", checks: ["verify", "lint"] },
  },
  prove_it: true,
  invariants: [
    {
      id: "INV-1",
      statement: "The model-visible prompt prefix stays immutable.",
    },
  ],
};
let mockTodos: ProjectTodo[] = [
  {
    id: "t1",
    text: "Verify the project panel refresh behavior",
    state: "doing",
    blocked_by: [],
    done_when: "A file added by the assistant appears without switching tabs",
    updated_at: Date.now(),
  },
  {
    id: "t2",
    text: "Add manifest-aware scope warnings",
    state: "done",
    blocked_by: [],
    done_when: "Historical writes carry a warning",
    updated_at: Date.now(),
  },
];
let mockIndex: WorkspaceIndexSummary = {
  files: 42,
  symbols: 216,
  bytes: 1_284_096,
  indexed_at: Date.now() - 60_000,
};
const mockSessions: SessionSummary[] = [];
const mockWorkspacesDirectory = "/home/you/OpenBaD/workspaces";
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
    schema_version: "0011_memory_fts",
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

export async function workspacesDirectory(): Promise<string> {
  return isTauri
    ? invoke<string>("workspaces_directory")
    : mockWorkspacesDirectory;
}

export async function createProject(name: string): Promise<WorkspaceSummary> {
  if (isTauri) {
    return invoke<WorkspaceSummary>("create_project", { name });
  }
  const slug =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "project";
  const project: WorkspaceSummary = {
    id: `mock-project-${mockProjects.length + 1}`,
    name: name.trim(),
    path: `${mockWorkspacesDirectory}/${slug}`,
    created_at: Date.now(),
    updated_at: Date.now(),
    session_count: 0,
  };
  mockProjects = [project, ...mockProjects];
  return project;
}

export async function pickDirectory(title: string): Promise<string | null> {
  if (isTauri) {
    const selected = await open({
      directory: true,
      multiple: false,
      title,
    });
    return typeof selected === "string" ? selected : null;
  }
  return "/mnt/disk4/repos/reference-library";
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

export async function loadOutsideAccessGrants(
  workspacePath: string,
): Promise<OutsideAccessGrant[]> {
  if (isTauri) {
    return invoke<OutsideAccessGrant[]>("outside_access_grants", { workspacePath });
  }
  return mockAccessGrants.filter(
    (grant) => grant.workspace_id === "mock-workspace",
  );
}

export async function grantOutsideAccess(
  workspacePath: string,
  path: string,
  writeAccess: boolean,
  reason: string,
): Promise<OutsideAccessGrant> {
  if (isTauri) {
    return invoke<OutsideAccessGrant>("grant_outside_access", {
      workspacePath,
      path,
      writeAccess,
      reason,
    });
  }
  const existing = mockAccessGrants.find((grant) => grant.path === path);
  if (existing) {
    existing.write_access = writeAccess;
    existing.reason = reason;
    return { ...existing };
  }
  const grant: OutsideAccessGrant = {
    id: `mock-access-${Date.now()}`,
    workspace_id: "mock-workspace",
    path,
    write_access: writeAccess,
    reason,
    created_at: Date.now(),
  };
  mockAccessGrants = [...mockAccessGrants, grant];
  return { ...grant };
}

export async function revokeOutsideAccess(
  workspacePath: string,
  grantId: string,
): Promise<void> {
  if (isTauri) {
    await invoke("revoke_outside_access", { workspacePath, grantId });
    return;
  }
  mockAccessGrants = mockAccessGrants.filter((grant) => grant.id !== grantId);
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

export async function setSessionAccessMode(
  sessionId: string,
  accessMode: AccessMode,
): Promise<void> {
  if (isTauri) {
    await invoke("set_session_access_mode", { sessionId, accessMode });
    return;
  }
  const session = mockSessions.find((item) => item.id === sessionId);
  if (session) {
    session.access_mode = accessMode;
  }
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

export async function steerChat(message: string): Promise<void> {
  if (isTauri) {
    await invoke("steer_chat", { message });
    return;
  }
  if (message.trim().length === 0) {
    throw new Error("steering message must not be empty");
  }
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

export async function loadProjectManifest(
  workspacePath: string,
): Promise<ProjectManifest | null> {
  return isTauri
    ? invoke<ProjectManifest | null>("project_manifest", { workspacePath })
    : mockManifest;
}

export async function loadProjectPlan(
  workspacePath: string,
): Promise<ProjectPlan | null> {
  return isTauri
    ? invoke<ProjectPlan | null>("project_plan", { workspacePath })
    : mockPlan;
}

export async function approveProjectPlan(
  workspacePath: string,
): Promise<ProjectPlan> {
  if (isTauri) {
    return invoke<ProjectPlan>("approve_project_plan", { workspacePath });
  }
  mockPlan = {
    ...mockPlan,
    status: "approved",
    approved_at: Date.now(),
  };
  return mockPlan;
}

export async function loadProjectQuestions(
  workspacePath: string,
): Promise<ProjectQuestion[]> {
  return isTauri
    ? invoke<ProjectQuestion[]>("project_questions", { workspacePath })
    : [...mockQuestions];
}

export async function answerProjectQuestion(
  workspacePath: string,
  questionId: string,
  answer: string,
): Promise<ProjectQuestion> {
  if (isTauri) {
    return invoke<ProjectQuestion>("answer_project_question", {
      workspacePath,
      questionId,
      answer,
    });
  }
  const question = mockQuestions.find((question) => question.id === questionId);
  if (question === undefined) {
    throw new Error(`unknown question: ${questionId}`);
  }
  const answered = {
    ...question,
    answer,
    answered_at: Date.now(),
  };
  mockQuestions = mockQuestions.map((item) =>
    item.id === questionId ? answered : item,
  );
  return answered;
}

export async function loadProjectTodos(
  workspacePath: string,
): Promise<ProjectTodo[]> {
  return isTauri
    ? invoke<ProjectTodo[]>("project_todos", { workspacePath })
    : [...mockTodos];
}

export async function loadProjectOverview(
  workspacePath: string,
): Promise<ProjectOverview> {
  if (isTauri) {
    return invoke<ProjectOverview>("project_overview", { workspacePath });
  }
  return {
    repos: mockRepos.length,
    knowledge_documents: mockKnowledge.length,
    knowledge_characters: mockKnowledge.reduce((sum, doc) => sum + doc.characters, 0),
    memories: mockMemories.length,
    index: { ...mockIndex },
  };
}

export async function loadProjectRepos(workspacePath: string): Promise<ProjectRepo[]> {
  return isTauri
    ? invoke<ProjectRepo[]>("project_repos", { workspacePath })
    : [...mockRepos];
}

export async function addProjectRepo(
  workspacePath: string,
  path: string,
  name?: string,
): Promise<ProjectRepo> {
  if (isTauri) {
    return invoke<ProjectRepo>("add_project_repo", { workspacePath, path, name });
  }
  const repo: ProjectRepo = {
    id: `mock-repo-${mockRepos.length + 1}`,
    workspace_id: "mock-workspace",
    name: name?.trim() || path.split("/").filter(Boolean).at(-1) || "repository",
    path,
    read_only: true,
    created_at: Date.now(),
  };
  mockRepos = [...mockRepos, repo];
  return repo;
}

export async function removeProjectRepo(
  workspacePath: string,
  repoId: string,
): Promise<void> {
  if (isTauri) {
    await invoke("remove_project_repo", { workspacePath, repoId });
    return;
  }
  mockRepos = mockRepos.filter((repo) => repo.id !== repoId);
}

export async function loadKnowledgeDocuments(
  workspacePath: string,
): Promise<KnowledgeDocumentSummary[]> {
  return isTauri
    ? invoke<KnowledgeDocumentSummary[]>("knowledge_documents", { workspacePath })
    : [...mockKnowledge];
}

export async function loadKnowledgeStaleDocuments(
  workspacePath: string,
): Promise<string[]> {
  return isTauri
    ? invoke<string[]>("knowledge_stale_documents", { workspacePath })
    : [];
}

export async function loadKnowledgeDocument(
  workspacePath: string,
  documentId: string,
): Promise<KnowledgeDocument> {
  if (isTauri) {
    return invoke<KnowledgeDocument>("knowledge_document", {
      workspacePath,
      documentId,
    });
  }
  const summary = mockKnowledge.find((document) => document.id === documentId);
  if (!summary) {
    throw new Error(`knowledge document not found: ${documentId}`);
  }
  return { summary, content: mockKnowledgeContent.get(documentId) ?? "" };
}

function knowledgeSlug(title: string): string {
  return (
    title
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "knowledge"
  );
}

async function saveTextExport(
  fileName: string,
  content: string,
): Promise<string | null> {
  if (isTauri) {
    const { save } = await import("@tauri-apps/plugin-dialog");
    const path = await save({
      defaultPath: fileName,
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    if (path === null) {
      return null;
    }
    return invoke<string>("write_export_file", { path, content });
  }
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
  return fileName;
}

export async function downloadKnowledgeDocument(
  workspacePath: string,
  documentId: string,
): Promise<string | null> {
  const document = await loadKnowledgeDocument(workspacePath, documentId);
  return saveTextExport(
    `${knowledgeSlug(document.summary.title)}.md`,
    document.content,
  );
}

export async function addKnowledgeNote(
  workspacePath: string,
  title: string,
  content: string,
  tags: string[],
  confidence = "inferred",
  kind = "note",
): Promise<KnowledgeDocumentSummary> {
  if (isTauri) {
    return invoke<KnowledgeDocumentSummary>("add_knowledge_note", {
      workspacePath,
      title,
      content,
      tags,
      confidence,
      kind,
    });
  }
  const summary: KnowledgeDocumentSummary = {
    id: `mock-knowledge-${mockKnowledge.length + 1}`,
    workspace_id: "mock-workspace",
    title: title.trim() || "Project note",
    kind,
    source: null,
    tags,
    characters: content.length,
    chunks: Math.max(1, Math.ceil(content.length / 1400)),
    confidence,
    source_hash: null,
    supersedes: null,
    review_status: "proposed",
    created_at: Date.now(),
    updated_at: Date.now(),
  };
  mockKnowledge = [summary, ...mockKnowledge];
  mockKnowledgeContent.set(summary.id, content);
  return summary;
}

export async function importKnowledgeFile(
  workspacePath: string,
  path: string,
  tags: string[],
): Promise<KnowledgeDocumentSummary> {
  if (isTauri) {
    return invoke<KnowledgeDocumentSummary>("import_knowledge_file", {
      workspacePath,
      path,
      tags,
    });
  }
  const name = path.split("/").filter(Boolean).at(-1) ?? "Imported document";
  return addKnowledgeNote(
    workspacePath,
    name,
    `Imported reference from ${path}.`,
    tags,
  );
}

export async function deleteKnowledgeDocument(
  workspacePath: string,
  documentId: string,
): Promise<void> {
  if (isTauri) {
    await invoke("delete_knowledge_document", { workspacePath, documentId });
    return;
  }
  mockKnowledge = mockKnowledge.filter((document) => document.id !== documentId);
  mockKnowledgeContent.delete(documentId);
}

export async function searchProject(
  workspacePath: string,
  query: string,
  limit = 12,
): Promise<ProjectHit[]> {
  if (isTauri) {
    return invoke<ProjectHit[]>("search_project", { workspacePath, query, limit });
  }
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) {
    return [];
  }
  const hits: ProjectHit[] = [];
  for (const document of mockKnowledge) {
    const content = mockKnowledgeContent.get(document.id) ?? "";
    if (content.toLowerCase().includes(needle) || document.title.toLowerCase().includes(needle)) {
      hits.push({
        source: "knowledge",
        title: document.title,
        snippet: content.slice(0, 220),
        locator: `${document.id}#0`,
        score: 1,
      });
    }
  }
  for (const memory of mockMemories) {
    if (memory.content.toLowerCase().includes(needle)) {
      hits.push({
        source: "memory",
        title: "Durable memory",
        snippet: memory.content,
        locator: memory.id,
        score: 0.9,
      });
    }
  }
  hits.push({
    source: "code",
    title: "apps/desktop/ui/src/App.tsx",
    snippet: `Indexed code matched ${query}.`,
    locator: "apps/desktop/ui/src/App.tsx",
    score: 0.8,
  });
  return hits.slice(0, limit);
}

export async function listProjectFiles(workspacePath: string): Promise<string[]> {
  if (isTauri) {
    return invoke<string[]>("project_files", { workspacePath });
  }
  return [...mockFiles];
}

export async function previewProjectFile(
  workspacePath: string,
  path: string,
): Promise<ProjectFileView> {
  if (isTauri) {
    return invoke<ProjectFileView>("project_file", { workspacePath, path });
  }
  return {
    path,
    kind: "text",
    content: `// ${path}\n// Mock preview for the browser harness.\n`,
    data_url: null,
  };
}

export async function quitApp(): Promise<void> {
  if (isTauri) {
    await getCurrentWindow().close();
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
  if (/slow/i.test(input.message)) {
    await tick(1_400);
  }
  const sessionId = input.sessionId ?? `mock-session-${mockSessionCounter++}`;
  const text = /markdown|mermaid/i.test(input.message)
    ? [
        "# Markdown features",
        "",
        "Some **bold**, *italic*, ~~strikethrough~~, `inline code`, and a [link](https://example.com).",
        "",
        "| Feature | Supported |",
        "| --- | --- |",
        "| Tables | yes |",
        "| Task lists | yes |",
        "",
        "- [x] headings and lists",
        "- [ ] follow-up work",
        "",
        "> Blockquotes work too.",
        "",
        "```rust",
        "fn main() {",
        "    println!(\"hello\");",
        "}",
        "```",
        "",
        "Inline math $E = mc^2$ and display math:",
        "",
        "$$",
        "x^2 + y^2 = z^2",
        "$$",
        "",
        "```mermaid",
        "graph TD",
        "  A[Start] --> B{Tests pass?}",
        "  B -->|yes| C[Ship]",
        "  B -->|no| D[Fix]",
        "```",
        "",
        "Footnote reference[^1].",
        "",
        "[^1]: Footnotes render at the end.",
      ].join("\n")
    : "I read `Cargo.toml`. It is a virtual workspace with twelve `openbad-*` crates plus the Tauri desktop app. The core crates are store, harness, llm, tools, agent, workspace, governance, memory, knowledge, autonomic, and mcp.";

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
    metadata: {},
  });
  onEvent({ type: "step", step: 2 });
  for (const chunk of text.match(/.{1,36}/g) ?? [text]) {
    onEvent({ type: "content", step: 2, text: chunk });
    await tick(45);
  }
  if (/preview|artifact|web page/i.test(input.message)) {
    onEvent({
      type: "tool_call",
      step: 2,
      name: "present_artifact",
      arguments: '{"kind":"web","title":"OpenBaD architecture","uri":"https://example.com"}',
    });
    await tick(80);
    onEvent({
      type: "tool_result",
      step: 2,
      name: "present_artifact",
      content: 'Presented web artifact "OpenBaD architecture" beside the chat.',
      is_error: false,
      metadata: {
        openbad_artifact: {
          kind: "web",
          title: "OpenBaD architecture",
          uri: "https://example.com",
        },
      },
    });
  }
  if (/outside|external.*(?:access|folder)/i.test(input.message)) {
    onEvent({
      type: "tool_call",
      step: 2,
      name: "request_fs_access",
      arguments:
        '{"path":"/home/you/other-project","write":false,"reason":"Compare the implementation"}',
    });
    await tick(80);
    onEvent({
      type: "tool_result",
      step: 2,
      name: "request_fs_access",
      content:
        "Filesystem access requested for /home/you/other-project (read only). Stop this turn and ask the human to approve it in OpenBaD; retry only after approval.",
      is_error: false,
      metadata: {
        openbad_access_request: {
          path: "/home/you/other-project",
          write: false,
          reason: "Compare the implementation",
        },
      },
    });
  }
  if (/add\s+(?:a\s+)?file/i.test(input.message)) {
    const path = "src/generated.rs";
    if (!mockFiles.includes(path)) {
      mockFiles = [...mockFiles, path].sort();
      mockIndex = { ...mockIndex, files: mockIndex.files + 1 };
    }
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
      access_mode: "read",
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
