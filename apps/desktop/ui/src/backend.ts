import { Channel, invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open, save } from "@tauri-apps/plugin-dialog";

import type {
  AccessMode,
  AgentEvent,
  ChatReply,
  ClipboardImage,
  Foundation,
  FileCheckpointSummary,
  GitCommandResult,
  GitCommit,
  GitRepoStatus,
  GitRepoSummary,
  ServiceStatus,
  IndexReport,
  InstallOutcome,
  McpServer,
  McpServerChange,
  McpServerInput,
  McpTool,
  MemoryRecord,
  OutsideAccessGrant,
  SessionSummary,
  SessionView,
  KnowledgeDocument,
  KnowledgeDocumentSummary,
  KnowledgeImportReport,
  ProjectAgent,
  ProjectAreaCounts,
  ProjectFileView,
  ProjectHit,
  ProjectManifest,
  ProjectOverview,
  ProjectPlan,
  ProjectExportReport,
  ProjectImportReport,
  ProjectQuestion,
  ProjectRepo,
  ProjectTodo,
  ReferenceUpload,
  UpdateStatus,
  UploadReport,
  UploadSource,
  Usage,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let mockKeyConfigured = false;
const mockCancelledRuns = new Set<string>();
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

const mockAgents: ProjectAgent[] = [];

let mockMcpServers: McpServer[] = [
  {
    name: "docs",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@example/mcp-docs"],
    env: {},
    cwd: null,
    url: null,
    allowed_tools: [],
    enabled: false,
    timeout_seconds: null,
  },
];

function mockMcpTools(server: McpServer): McpTool[] {
  if (server.transport === "http") {
    return [
      {
        server: server.name,
        name: "fetch",
        description: "Fetch a document by URL",
      },
    ];
  }
  return [
    {
      server: server.name,
      name: "search_docs",
      description: "Search the documentation index",
    },
    {
      server: server.name,
      name: "read_doc",
      description: "Read one documentation page",
    },
  ];
}

let mockFiles = [
  "apps/desktop/ui/src/App.tsx",
  "apps/desktop/ui/src/backend.ts",
  "assets/logo.png",
  "crates/openbad-agent/src/lib.rs",
  "crates/openbad-store/src/store.rs",
  "data/releases.csv",
  "docs/architecture/00-vision-and-feature-set.md",
  "docs/index.html",
  "docs/report.pdf",
];

const MOCK_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==";
const MOCK_PDF =
  "data:application/pdf;base64,JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAzMDAgMjAwXSAvQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIgPj4gPj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA0NSA+PgpzdHJlYW0KQlQgL0YxIDE4IFRmIDIwIDEwMCBUZCAoT3BlbkJhRCByZXBvcnQpIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKNSAwIG9iago8PCAvVHlwZSAvRm9udCAvU3VidHlwZSAvVHlwZTEgL0Jhc2VGb250IC9IZWx2ZXRpY2EgPj4KZW5kb2JqCnhyZWYKMCA2CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAwOSAwMDAwMCBuIAowMDAwMDAwMDU4IDAwMDAwIG4gCjAwMDAwMDAxMTUgMDAwMDAgbiAKMDAwMDAwMDI0MSAwMDAwMCBuIAowMDAwMDAwMzM2IDAwMDAwIG4gCnRyYWlsZXIKPDwgL1NpemUgNiAvUm9vdCAxIDAgUiA+PgpzdGFydHhyZWYKNDA2CiUlRU9GCg==";
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
    web: "pnpm --dir apps/desktop/ui build",
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
const mockMessages = new Map<
  string,
  {
    role: string;
    text: string;
    reasoning: string | null;
    attachments?: string[];
    duration_ms?: number | null;
  }[]
>();

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
    schema_version: "0012_subagents",
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

export async function pickSaveFile(
  title: string,
  defaultPath: string,
): Promise<string | null> {
  if (isTauri) {
    return save({
      title,
      defaultPath,
      filters: [{ name: "OpenBaD project", extensions: ["openbad-project"] }],
    });
  }
  return `${mockWorkspacesDirectory}/${defaultPath.split("/").at(-1) ?? "project.openbad-project"}`;
}

export async function pickImportBundle(): Promise<string | null> {
  if (isTauri) {
    const selected = await open({
      directory: false,
      multiple: false,
      title: "Import OpenBaD project",
      filters: [{ name: "OpenBaD project", extensions: ["openbad-project"] }],
    });
    return typeof selected === "string" ? selected : null;
  }
  return `${mockWorkspacesDirectory}/OpenBaD.openbad-project`;
}

export async function exportProject(
  workspacePath: string,
  destination: string,
): Promise<ProjectExportReport> {
  if (isTauri) {
    return invoke<ProjectExportReport>("export_project", { workspacePath, destination });
  }
  return {
    path: destination,
    project_name: "OpenBaD",
    files: 128,
    bytes: 18_450_000,
    sessions: 4,
    memories: 7,
    knowledge_documents: 3,
    checkpoints: 12,
    warnings: [
      "Reference folders are not included; re-add them on the new machine.",
      "The model API key is not included; enter it in Settings after importing.",
    ],
  };
}

export async function importProject(
  bundlePath: string,
  destinationParent: string,
): Promise<ProjectImportReport> {
  if (isTauri) {
    return invoke<ProjectImportReport>("import_project", {
      bundlePath,
      destinationParent,
    });
  }
  const path = `${destinationParent}/OpenBaD`;
  if (!mockProjects.some((project) => project.path === path)) {
    mockProjects = [
      {
        id: `mock-project-${mockProjects.length + 1}`,
        name: "OpenBaD",
        path,
        created_at: Date.now(),
        updated_at: Date.now(),
        session_count: 4,
      },
      ...mockProjects,
    ];
  }
  return {
    path,
    project_name: "OpenBaD",
    files: 128,
    sessions: 4,
    memories: 7,
    knowledge_documents: 3,
    checkpoints: 12,
    warnings: [
      "Reference folders are not part of the bundle; re-add them for this machine.",
      "The model API key is not part of the bundle; enter it in Settings.",
    ],
  };
}

export async function importProjectInto(
  workspacePath: string,
  bundlePath: string,
): Promise<ProjectImportReport> {
  if (isTauri) {
    return invoke<ProjectImportReport>("import_project_into", {
      workspacePath,
      bundlePath,
    });
  }
  return {
    path: workspacePath,
    project_name: "OpenBaD",
    files: 128,
    sessions: 2,
    memories: 7,
    knowledge_documents: 3,
    checkpoints: 5,
    warnings: [
      "Reference folders are not part of the bundle; re-add them for this machine.",
      "The model API key is not part of the bundle; enter it in Settings.",
    ],
  };
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

export async function pickFiles(title: string): Promise<string[]> {
  if (isTauri) {
    const selected = await open({
      directory: false,
      multiple: true,
      title,
    });
    if (selected === null) {
      return [];
    }
    return Array.isArray(selected) ? selected : [selected];
  }
  return [`${mockWorkspacesDirectory}/upload-${mockUploads++}.md`];
}

let mockUploads = 1;

export async function importWorkspaceFiles(
  workspacePath: string,
  files: UploadSource[],
): Promise<UploadReport> {
  if (isTauri) {
    return invoke<UploadReport>("import_workspace_files", { workspacePath, files });
  }
  const report: UploadReport = { files: [], skipped: [] };
  for (const file of files) {
    const name = uploadName(file);
    if (mockFiles.includes(name)) {
      report.skipped.push(`${name} already exists in the workspace`);
      continue;
    }
    mockFiles = [...mockFiles, name].sort();
    mockIndex = { ...mockIndex, files: mockIndex.files + 1 };
    report.files.push({
      name,
      path: `${workspacePath}/${name}`,
      bytes: file.content?.length ?? 1024,
    });
  }
  return report;
}

export async function importReferenceFiles(
  workspacePath: string,
  files: UploadSource[],
): Promise<ReferenceUpload> {
  if (isTauri) {
    return invoke<ReferenceUpload>("import_reference_files", { workspacePath, files });
  }
  const uploads = files.map((file) => uploadName(file));
  let repo = mockRepos.find((item) => item.name === "Uploaded documents");
  if (repo === undefined) {
    repo = {
      id: `mock-repo-${mockRepos.length + 1}`,
      workspace_id: "mock-workspace",
      name: "Uploaded documents",
      path: `${mockWorkspacesDirectory}/../references/reference-library`,
      read_only: true,
      created_at: Date.now(),
    };
    mockRepos = [...mockRepos, repo];
  }
  return {
    repo,
    report: {
      files: uploads.map((name) => ({
        name,
        path: `${repo.path}/${name}`,
        bytes: 1024,
      })),
      skipped: [],
    },
  };
}

export async function importKnowledgeFiles(
  workspacePath: string,
  files: UploadSource[],
  tags: string[],
): Promise<KnowledgeImportReport> {
  if (isTauri) {
    return invoke<KnowledgeImportReport>("import_knowledge_files", {
      workspacePath,
      files,
      tags,
    });
  }
  const documents = files.map((file, index) => {
    const summary: KnowledgeDocumentSummary = {
      id: `mock-knowledge-${mockKnowledge.length + index + 1}`,
      workspace_id: "mock-workspace",
      title: uploadName(file).replace(/\.[^.]+$/, ""),
      kind: "file",
      source: file.path ?? null,
      tags,
      characters: file.content?.length ?? 640,
      chunks: 1,
      confidence: "verified",
      source_hash: null,
      supersedes: null,
      review_status: "proposed",
      created_at: Date.now(),
      updated_at: Date.now(),
    };
    return summary;
  });
  mockKnowledge = [...documents, ...mockKnowledge];
  for (const document of documents) {
    mockKnowledgeContent.set(document.id, `Imported ${document.title}.`);
  }
  return { documents, skipped: [] };
}

function uploadName(file: UploadSource): string {
  const parts = (file.path ?? file.name).split(/[\\/]/);
  return parts.filter(Boolean).at(-1) ?? file.name;
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
  return isTauri
    ? invoke<SessionSummary[]>("sessions")
    : mockSessions.filter((session) => session.status !== "archived");
}

export async function loadArchivedSessions(): Promise<SessionSummary[]> {
  return isTauri
    ? invoke<SessionSummary[]>("archived_sessions")
    : mockSessions.filter((session) => session.status === "archived");
}

export async function archiveSession(
  sessionId: string,
  archived: boolean,
): Promise<void> {
  if (isTauri) {
    await invoke("archive_session", { sessionId, archived });
    return;
  }
  const session = mockSessions.find((item) => item.id === sessionId);
  if (session) {
    session.status = archived ? "archived" : "active";
  }
}

export async function deleteSession(sessionId: string): Promise<void> {
  if (isTauri) {
    await invoke("delete_session", { sessionId });
    return;
  }
  const index = mockSessions.findIndex((item) => item.id === sessionId);
  if (index >= 0) {
    mockSessions.splice(index, 1);
  }
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
  const messages = mockMessages.get(sessionId) ?? [];
  return {
    session_id: sessionId,
    messages,
    total_duration_ms: messages.reduce(
      (total, message) => total + (message.duration_ms ?? 0),
      0,
    ),
  };
}

export async function steerChat(runId: string, message: string): Promise<void> {
  if (isTauri) {
    await invoke("steer_chat", { runId, message });
    return;
  }
  if (message.trim().length === 0) {
    throw new Error("steering message must not be empty");
  }
}

export async function cancelChat(runId: string): Promise<void> {
  if (isTauri) {
    await invoke("cancel_chat", { runId });
    return;
  }
  mockCancelledRuns.add(runId);
}

export async function readClipboardImage(): Promise<ClipboardImage | null> {
  if (!isTauri) {
    return null;
  }
  return invoke<ClipboardImage | null>("read_clipboard_image");
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

export async function loadProjectAreaCounts(
  workspacePath: string,
): Promise<ProjectAreaCounts> {
  if (isTauri) {
    return invoke<ProjectAreaCounts>("project_area_counts", { workspacePath });
  }
  return {
    workspace_files: mockIndex.files,
    agents: mockAgents.length,
    running_agents: mockAgents.filter((agent) => agent.status === "running").length,
    references: mockRepos.length,
    knowledge: mockKnowledge.length,
    memories: mockMemories.length,
    todos_open: mockTodos.filter((todo) => todo.state !== "done" && todo.state !== "dropped")
      .length,
    todos_total: mockTodos.length,
    questions_open: mockQuestions.filter((question) => question.answer === null).length,
    questions_total: mockQuestions.length,
    plan_steps: mockPlan?.steps.length ?? 0,
    plan_open_steps: mockPlan?.steps.filter((step) => step.status !== "done").length ?? 0,
  };
}

export async function loadProjectAgents(
  workspacePath: string,
): Promise<ProjectAgent[]> {
  return isTauri
    ? invoke<ProjectAgent[]>("project_agents", { workspacePath })
    : [...mockAgents];
}

export async function cancelProjectAgent(id: string): Promise<ProjectAgent> {
  if (!isTauri) {
    throw new Error("subagents are unavailable outside the desktop app");
  }
  return invoke<ProjectAgent>("cancel_agent", { id });
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

export async function setProjectRepoReadOnly(
  repoId: string,
  readOnly: boolean,
): Promise<void> {
  if (isTauri) {
    await invoke("set_project_repo_read_only", { repoId, readOnly });
    return;
  }
  mockRepos = mockRepos.map((repo) =>
    repo.id === repoId ? { ...repo, read_only: readOnly } : repo,
  );
}

export async function servicesStatus(): Promise<ServiceStatus[]> {
  if (!isTauri) {
    return [];
  }
  return invoke<ServiceStatus[]>("services_status");
}

export async function openServiceUrl(url: string): Promise<void> {
  if (!isTauri) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  return invoke("open_service_url", { url });
}

export async function gitRepoStatus(
  workspacePath: string,
  repoPath: string,
): Promise<GitRepoStatus> {
  if (isTauri) {
    return invoke<GitRepoStatus>("git_repo_status", { workspacePath, repoPath });
  }
  return mockGitStatus(repoPath);
}

export async function gitProjectRepos(
  workspacePath: string,
  repoPaths: string[],
): Promise<GitRepoSummary[]> {
  if (isTauri) {
    return invoke<GitRepoSummary[]>("git_project_repos", {
      workspacePath,
      repoPaths,
    });
  }
  const seen = new Set<string>();
  return [workspacePath, ...repoPaths]
    .filter((path) => {
      if (path.length === 0 || seen.has(path)) {
        return false;
      }
      seen.add(path);
      return true;
    })
    .map((path) => {
      const status = mockGitStatus(path);
      return {
        path,
        name: status.name,
        isRepo: status.isRepo,
        branch: status.branch,
        detached: status.detached,
        upstream: status.upstream,
        ahead: status.ahead,
        behind: status.behind,
        dirty: status.files.length,
        error: status.error,
      };
    });
}

function mockGitStatus(repoPath: string): GitRepoStatus {
  return {
    path: repoPath,
    name: repoPath.split("/").filter(Boolean).at(-1) ?? "repository",
    isRepo: true,
    branch: "main",
    detached: false,
    upstream: "origin/main",
    ahead: 0,
    behind: 0,
    hasCommits: true,
    files: [],
    branches: [],
    remotes: [{ name: "origin", url: "https://github.com/example/repo.git" }],
    lastCommit: null,
    operation: null,
    error: null,
  };
}

export async function gitInit(
  workspacePath: string,
  repoPath: string,
): Promise<GitCommandResult> {
  return runGitCommand("git_init", { workspacePath, repoPath });
}

export async function gitCheckout(
  workspacePath: string,
  repoPath: string,
  branch: string,
  create = false,
): Promise<GitCommandResult> {
  return runGitCommand("git_checkout", { workspacePath, repoPath, branch, create });
}

export async function gitFetch(
  workspacePath: string,
  repoPath: string,
  remote?: string,
): Promise<GitCommandResult> {
  return runGitCommand("git_fetch", { workspacePath, repoPath, remote });
}

export async function gitPull(
  workspacePath: string,
  repoPath: string,
  mode: "ff-only" | "merge" | "rebase" = "ff-only",
): Promise<GitCommandResult> {
  return runGitCommand("git_pull", { workspacePath, repoPath, mode });
}

export async function gitPush(
  workspacePath: string,
  repoPath: string,
  input: { remote?: string; branch?: string; setUpstream?: boolean } = {},
): Promise<GitCommandResult> {
  return runGitCommand("git_push", { workspacePath, repoPath, ...input });
}

export async function gitStage(
  workspacePath: string,
  repoPath: string,
  paths: string[],
): Promise<GitCommandResult> {
  return runGitCommand("git_stage", { workspacePath, repoPath, paths });
}

export async function gitUnstage(
  workspacePath: string,
  repoPath: string,
  paths: string[],
): Promise<GitCommandResult> {
  return runGitCommand("git_unstage", { workspacePath, repoPath, paths });
}

export async function gitCommit(
  workspacePath: string,
  repoPath: string,
  message: string,
  stageAll = false,
): Promise<GitCommandResult> {
  return runGitCommand("git_commit", { workspacePath, repoPath, message, stageAll });
}

export async function gitDiff(
  workspacePath: string,
  repoPath: string,
  path?: string,
  staged = false,
): Promise<string> {
  if (!isTauri) {
    return "";
  }
  return invoke<string>("git_diff", { workspacePath, repoPath, path, staged });
}

export async function gitLog(
  workspacePath: string,
  repoPath: string,
  limit = 20,
): Promise<GitCommit[]> {
  if (!isTauri) {
    return [];
  }
  return invoke<GitCommit[]>("git_log", { workspacePath, repoPath, limit });
}

async function runGitCommand(
  command: string,
  args: Record<string, unknown>,
): Promise<GitCommandResult> {
  if (!isTauri) {
    return {
      ok: false,
      message: "Git actions are unavailable outside the desktop app.",
      status: null,
    };
  }
  return invoke<GitCommandResult>(command, args);
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
  const base = {
    path,
    content: null,
    data_url: null,
    mime: null,
    size: 0,
    sheets: [],
    truncated: false,
  };
  if (path.toLowerCase().endsWith(".png")) {
    return { ...base, kind: "image", data_url: MOCK_PNG, mime: "image/png", size: 68 };
  }
  if (path.toLowerCase().endsWith(".pdf")) {
    return {
      ...base,
      kind: "pdf",
      content: "OpenBaD report",
      data_url: MOCK_PDF,
      mime: "application/pdf",
      size: 470,
    };
  }
  if (path.toLowerCase().endsWith(".html") || path.toLowerCase().endsWith(".htm")) {
    return {
      ...base,
      kind: "text",
      content:
        "<!doctype html><html><head><style>body{font-family:sans-serif;padding:24px;background:#fff;color:#123}</style></head><body><h1>Rendered HTML</h1><p>This file is rendered, not shown as source.</p></body></html>",
      mime: "text/html",
      size: 200,
    };
  }
  if (path.toLowerCase().endsWith(".csv")) {
    return {
      ...base,
      kind: "sheet",
      sheets: [
        {
          name: "CSV",
          rows: [
            ["crate", "tests"],
            ["openbad-store", "42"],
            ["openbad-tools", "58"],
          ],
          truncated: false,
        },
      ],
    };
  }
  return {
    ...base,
    kind: "text",
    content: `// ${path}\n// Mock preview for the browser harness.\n`,
    size: 64,
  };
}

export async function openPath(
  workspacePath: string,
  path: string,
): Promise<string> {
  if (isTauri) {
    return invoke<string>("open_path", { workspacePath, path });
  }
  return path;
}

export async function revealPath(
  workspacePath: string,
  path: string,
): Promise<string> {
  if (isTauri) {
    return invoke<string>("reveal_path", { workspacePath, path });
  }
  return path;
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

export async function loadMcpServers(
  workspacePath: string,
): Promise<McpServer[]> {
  if (isTauri) {
    return invoke<McpServer[]>("mcp_servers", { workspacePath });
  }
  await delay(40);
  return mockMcpServers.map((server) => ({ ...server }));
}

export async function addMcpServer(
  workspacePath: string,
  server: McpServerInput,
): Promise<McpServerChange> {
  if (isTauri) {
    return invoke<McpServerChange>("add_mcp_server", { workspacePath, server });
  }
  await delay(120);
  const name = server.name.trim();
  if (name.length === 0) {
    throw new Error("server name must not be empty");
  }
  if (mockMcpServers.some((item) => item.name === name)) {
    throw new Error(`MCP server already exists: ${name}`);
  }
  const transport =
    server.transport ?? (server.command !== undefined && server.command !== null ? "stdio" : "http");
  if (transport === "stdio" && (server.command ?? "").trim().length === 0) {
    throw new Error("stdio server requires a command");
  }
  if (transport === "http" && (server.url ?? "").trim().length === 0) {
    throw new Error("http server requires a url");
  }
  const created: McpServer = {
    name,
    transport,
    command: server.command ?? null,
    args: server.args ?? [],
    env: server.env ?? {},
    cwd: server.cwd ?? null,
    url: server.url ?? null,
    allowed_tools: server.allowed_tools ?? [],
    enabled: server.enabled ?? true,
    timeout_seconds: server.timeout_seconds ?? null,
  };
  mockMcpServers = [...mockMcpServers, created];
  return { server: { ...created }, tools: mockMcpTools(created), warning: null };
}

export async function updateMcpServer(
  workspacePath: string,
  name: string,
  server: McpServerInput,
): Promise<McpServerChange> {
  if (isTauri) {
    return invoke<McpServerChange>("update_mcp_server", {
      workspacePath,
      name,
      server,
    });
  }
  await delay(120);
  const index = mockMcpServers.findIndex((item) => item.name === name);
  if (index < 0) {
    throw new Error(`unknown MCP server: ${name}`);
  }
  const current = mockMcpServers[index];
  const updated: McpServer = {
    ...current,
    transport: server.transport ?? current.transport,
    command: server.command !== undefined ? server.command : current.command,
    args: server.args ?? current.args,
    env: server.env ?? current.env,
    cwd: server.cwd !== undefined ? server.cwd : current.cwd,
    url: server.url !== undefined ? server.url : current.url,
    allowed_tools: server.allowed_tools ?? current.allowed_tools,
    enabled: server.enabled ?? current.enabled,
    timeout_seconds:
      server.timeout_seconds !== undefined
        ? server.timeout_seconds
        : current.timeout_seconds,
  };
  if (updated.transport === "stdio" && (updated.command ?? "").trim().length === 0) {
    throw new Error("stdio server requires a command");
  }
  if (updated.transport === "http" && (updated.url ?? "").trim().length === 0) {
    throw new Error("http server requires a url");
  }
  mockMcpServers = mockMcpServers.map((item, itemIndex) =>
    itemIndex === index ? updated : item,
  );
  return { server: { ...updated }, tools: mockMcpTools(updated), warning: null };
}

export async function removeMcpServer(
  workspacePath: string,
  name: string,
): Promise<void> {
  if (isTauri) {
    await invoke("remove_mcp_server", { workspacePath, name });
    return;
  }
  await delay(80);
  const before = mockMcpServers.length;
  mockMcpServers = mockMcpServers.filter((server) => server.name !== name);
  if (mockMcpServers.length === before) {
    throw new Error(`unknown MCP server: ${name}`);
  }
}

export async function setMcpServerEnabled(
  workspacePath: string,
  name: string,
  enabled: boolean,
): Promise<McpServer> {
  if (isTauri) {
    return invoke<McpServer>("set_mcp_server_enabled", {
      workspacePath,
      name,
      enabled,
    });
  }
  await delay(80);
  const server = mockMcpServers.find((item) => item.name === name);
  if (server === undefined) {
    throw new Error(`unknown MCP server: ${name}`);
  }
  const updated = { ...server, enabled };
  mockMcpServers = mockMcpServers.map((item) =>
    item.name === name ? updated : item,
  );
  return updated;
}

export async function testMcpServer(
  workspacePath: string,
  name: string,
): Promise<McpTool[]> {
  if (isTauri) {
    return invoke<McpTool[]>("test_mcp_server", { workspacePath, name });
  }
  await delay(160);
  const server = mockMcpServers.find((item) => item.name === name);
  if (server === undefined) {
    throw new Error(`unknown MCP server: ${name}`);
  }
  return mockMcpTools(server);
}

export async function checkForUpdates(): Promise<UpdateStatus> {
  if (isTauri) {
    return invoke<UpdateStatus>("check_for_updates");
  }
  await delay(180);
  return { kind: "up_to_date", current: "0.1.0", latest: "0.1.0" };
}

export async function installUpdate(version: string): Promise<InstallOutcome> {
  if (isTauri) {
    return invoke<InstallOutcome>("install_update", { version });
  }
  throw new Error("updates can only be installed from the desktop app");
}

export type ChatInput = {
  /** UI-generated id that scopes cancellation and steering to one turn. */
  runId: string;
  sessionId: string | null;
  workspacePath: string;
  message: string;
  allowWrites: boolean;
  allowCommands: boolean;
  policy: AccessMode;
  model: string;
  thinking: boolean;
  reasoningEffort: string;
  attachments: UploadSource[];
};

export async function respondToApproval(
  requestId: string,
  decision: "allow_once" | "allow_session" | "allow_workspace" | "deny",
): Promise<void> {
  if (!isTauri) {
    return;
  }
  await invoke("respond_to_approval", { requestId, decision });
}

export async function chat(
  input: ChatInput,
  onEvent: (event: AgentEvent) => void,
): Promise<ChatReply> {
  if (isTauri) {
    const channel = new Channel<AgentEvent>();
    channel.onmessage = onEvent;
    return invoke<ChatReply>("chat", {
      request: {
        runId: input.runId,
        sessionId: input.sessionId,
        workspacePath: input.workspacePath,
        message: input.message,
        allowWrites: input.allowWrites,
        allowCommands: input.allowCommands,
        policy: input.policy,
        model: input.model,
        thinking: input.thinking,
        reasoningEffort: input.reasoningEffort,
        attachments: input.attachments,
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
  const startedAt = Date.now();
  mockCancelledRuns.delete(input.runId);
  if (/subagent|spawn agent/i.test(input.message)) {
    const now = Date.now();
    mockAgents.push({
      id: `mock-agent-${mockAgents.length + 1}`,
      workspace_id: "mock-workspace",
      parent_session_id: input.sessionId,
      title: "Repository inventory",
      task: "Inspect the repository and report the main packages.",
      model: "deepseek-flash",
      status: "running",
      log: "[step 1] -> list_dir({\"path\":\".\"})\n",
      result: "",
      error: null,
      steps: 1,
      created_at: now,
      updated_at: now,
      finished_at: null,
    });
  }
  if (/slow/i.test(input.message)) {
    await tick(1_400, input.runId);
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
  const attached = input.attachments ?? [];
  const reply = attached.length === 0
    ? text
    : `${text} Attached documents: ${attached.map((file) => file.name).join(", ")}.`;

  onEvent({ type: "step", step: 1 });
  await tick(120, input.runId);
  for (const chunk of [
    "The user wants a workspace summary. ",
    "I should read Cargo.toml first. ",
    "Then I can group the crates by role.",
  ]) {
    onEvent({ type: "reasoning", step: 1, text: chunk });
    await tick(90, input.runId);
  }
  onEvent({
    type: "tool_call",
    step: 1,
    name: "read_file",
    arguments: '{"path":"Cargo.toml"}',
  });
  await tick(140, input.runId);
  onEvent({
    type: "tool_result",
    step: 1,
    name: "read_file",
    content: "[workspace] members = [...12 crates...]",
    is_error: false,
    metadata: {},
  });
  onEvent({ type: "step", step: 2 });
  for (const chunk of reply.match(/.{1,36}/g) ?? [reply]) {
    onEvent({ type: "content", step: 2, text: chunk });
    await tick(45, input.runId);
  }
  if (/preview|artifact|web page/i.test(input.message)) {
    onEvent({
      type: "tool_call",
      step: 2,
      name: "present_artifact",
      arguments: '{"kind":"web","title":"OpenBaD architecture","uri":"https://example.com"}',
    });
    await tick(80, input.runId);
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
    await tick(80, input.runId);
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
      created_at: startedAt,
      updated_at: Date.now(),
      last_turn: 1,
      access_mode: "read",
    });
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

  const durationMs = Date.now() - startedAt;
  const history = mockMessages.get(sessionId) ?? [];
  history.push(
    {
      role: "user",
      text: input.message,
      reasoning: null,
      attachments: attached.map((file) => file.name),
    },
    {
      role: "assistant",
      text: reply,
      reasoning: "The user wants a workspace summary.",
      duration_ms: durationMs,
    },
  );
  mockMessages.set(sessionId, history);
  return {
    session_id: sessionId,
    workspace_id: "mock-workspace",
    text,
    steps: 2,
    usage: mockUsage,
    tool_trace: ["[workspace] members = [...12 crates...]"],
    duration_ms: durationMs,
    attachments: attached.map((file) => ({
      name: file.name,
      path: `uploads/${file.name}`,
      bytes: file.content?.length ?? 0,
    })),
  };
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function tick(milliseconds: number, runId: string): Promise<void> {
  await delay(milliseconds);
  if (mockCancelledRuns.has(runId)) {
    throw new Error("agent turn was cancelled");
  }
}
