export type Doctor = {
  schema_version: string;
  integrity: string;
  database_bytes: number;
  event_count: number;
};

export type Audit = {
  valid: boolean;
  records: number;
  first_invalid: string | null;
};

export type Foundation = {
  app_version: string;
  doctor: Doctor;
  audit: Audit;
  providers: { id: string; name: string; kind: string; enabled: boolean }[];
  routes: { id: string; name: string; model: string; task_class: string }[];
};

export type Usage = {
  input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
};

export type ChatReply = {
  session_id: string;
  workspace_id: string;
  text: string;
  steps: number;
  usage: Usage;
  tool_trace: string[];
  duration_ms: number;
  attachments: UploadedFile[];
};

export type ProjectExportReport = {
  path: string;
  project_name: string;
  files: number;
  bytes: number;
  sessions: number;
  memories: number;
  knowledge_documents: number;
  checkpoints: number;
  warnings: string[];
};

export type ProjectImportReport = {
  path: string;
  project_name: string;
  files: number;
  sessions: number;
  memories: number;
  knowledge_documents: number;
  checkpoints: number;
  warnings: string[];
};

export type ChatAttachment = {
  name: string;
  path?: string;
  bytes?: number;
  previewUrl?: string;
};

export type ToolCallView = {
  id: string;
  name: string;
  arguments: string;
  status: "queued" | "running" | "settled" | "failed" | "interrupted";
  durationMs?: number;
  result?: string;
  isError?: boolean;
};

export type TodoItemView = {
  id: string;
  text: string;
  state: string;
};

export type ChatEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  startedAt?: number;
  durationMs?: number;
  reasoning?: string;
  lastContentStep?: number;
  lastReasoningStep?: number;
  status?: string;
  tools?: string[];
  toolCalls?: ToolCallView[];
  todos?: TodoItemView[];
  checkpointCount?: number;
  steering?: boolean;
  attachments?: ChatAttachment[];
  usage?: Usage;
  preview?: PreviewTarget;
};

export type AgentEvent =
  | { type: "step"; step: number }
  | { type: "reasoning"; step: number; text: string }
  | { type: "content"; step: number; text: string }
  | { type: "tool_call"; step: number; call_id?: string; name: string; arguments: string }
  | {
      type: "tool_result";
      step: number;
      call_id?: string;
      name: string;
      content: string;
      is_error: boolean;
      metadata: unknown;
    }
  | { type: "usage"; usage: Usage }
  | { type: "compaction"; before_bytes: number; after_bytes: number }
  | {
      type: "attempt_failed";
      step: number;
      attempt: number;
      message: string;
      retry_in_ms: number | null;
    }
  | {
      type: "tool_status";
      step: number;
      call_id?: string;
      name: string;
      status: string;
      duration_ms: number | null;
    }
  | {
      type: "approval_request";
      request_id: string;
      tool: string;
      risk: string;
      detail: string;
    }
  | {
      type: "approval_decision";
      request_id: string;
      allowed: boolean;
      decision: string;
    };

export type PendingApproval = {
  requestId: string;
  tool: string;
  risk: string;
  detail: string;
};

export type WorkspaceIndexSummary = {
  files: number;
  symbols: number;
  bytes: number;
  indexed_at: number | null;
};

export type IndexReport = {
  seen: number;
  added: number;
  updated: number;
  removed: number;
  elapsed_ms: number;
  summary: WorkspaceIndexSummary;
};

export type MemoryRecord = {
  id: string;
  content: string;
  tags: string[];
  pinned: boolean;
  created_at: number;
  updated_at: number;
  access_count: number;
  origin_turn: number | null;
  origin_task: string | null;
  author: string;
  scope: string;
  supersedes: string | null;
  expires_at: number | null;
};

export type FileCheckpointSummary = {
  id: string;
  path: string;
  existed: boolean;
  bytes: number;
  created_at: number;
};

export type WorkspaceSummary = {
  id: string;
  name: string;
  path: string;
  created_at: number;
  updated_at: number;
  session_count: number;
};

export type ChatMode = "ask" | "plan" | "agent";

export type AccessMode =
  | "read"
  | "edit"
  | "ask-writes"
  | "ask-commands"
  | "full";

export type SessionSummary = {
  id: string;
  workspace_id: string | null;
  workspace_path: string | null;
  title: string | null;
  status: string;
  created_at: number;
  updated_at: number;
  last_turn: number;
  access_mode: AccessMode;
};

export type SessionView = {
  session_id: string;
  messages: {
    role: string;
    text: string;
    attachments?: string[];
    reasoning: string | null;
    duration_ms?: number | null;
    interrupted?: boolean;
    tool_calls?: {
      call_id: string;
      name: string;
      arguments: string;
      status: string;
      result: string;
      is_error: boolean;
      duration_ms?: number | null;
    }[];
  }[];
  total_duration_ms: number;
};

export type OutsideAccessGrant = {
  id: string;
  workspace_id: string;
  path: string;
  write_access: boolean;
  reason: string;
  created_at: number;
};

export type OutsideAccessRequest = {
  path: string;
  write: boolean;
  reason: string;
};

export type ToolApprovalRequest = {
  tool: string;
  risk: string;
  reason: string;
};

export type FoundationState =
  | { kind: "loading" }
  | { kind: "ready"; foundation: Foundation }
  | { kind: "error"; message: string };


export type ProjectRepo = {
  id: string;
  workspace_id: string;
  name: string;
  path: string;
  read_only: boolean;
  created_at: number;
};

export type KnowledgeDocumentSummary = {
  id: string;
  workspace_id: string;
  title: string;
  kind: string;
  source: string | null;
  tags: string[];
  characters: number;
  chunks: number;
  confidence: string;
  source_hash: string | null;
  supersedes: string | null;
  review_status: string;
  created_at: number;
  updated_at: number;
};

export type KnowledgeDocument = {
  summary: KnowledgeDocumentSummary;
  content: string;
};

export type ProjectHit = {
  source: "knowledge" | "code" | "memory" | string;
  title: string;
  snippet: string;
  locator: string;
  score: number;
};

export type ProjectOverview = {
  repos: number;
  knowledge_documents: number;
  knowledge_characters: number;
  memories: number;
  index: WorkspaceIndexSummary;
};

export type SheetPreview = {
  name: string;
  rows: string[][];
  truncated: boolean;
};

export type ProjectFileView = {
  path: string;
  kind: "text" | "image" | "pdf" | "sheet" | "doc" | "audio" | "video" | "binary";
  content: string | null;
  data_url: string | null;
  mime: string | null;
  size: number;
  sheets: SheetPreview[];
  truncated: boolean;
};

export type Artifact = {
  kind: "web" | "image" | "markdown" | "diff" | "file" | "memory";
  title: string;
  uri?: string;
  content?: string;
  language?: string;
};

export type PreviewTarget =
  | { kind: "artifact"; artifact: Artifact }
  | {
      kind: "git";
      workspacePath: string;
      repoPath: string;
      repoName: string;
    };


export type ProjectCommand = {
  command: string;
  description?: string | null;
  cwd?: string | null;
  timeout_seconds?: number | null;
};

export type ProjectManifest = {
  name?: string | null;
  description?: string | null;
  scope?: {
    authoritative?: string[];
    historical?: string[];
    generated?: string[];
    vendored?: string[];
    scratch?: string[];
  };
  commands?: Record<string, string | ProjectCommand>;
  recipes?: Record<string, string | ProjectCommand>;
  validators?: {
    pattern: string;
    command: string;
    description?: string | null;
  }[];
  gates?: Record<string, { description?: string | null; checks: string[] }>;
  prove_it?: boolean;
  invariants?: {
    id: string;
    statement: string;
    check?: string | null;
    severity?: string | null;
    rationale?: string | null;
  }[];
};

export type ProjectPlanStep = {
  id: string;
  text: string;
  status: string;
  verify_with: string | null;
  evidence: string | null;
};

export type ProjectPlan = {
  goal: string;
  status: string;
  steps: ProjectPlanStep[];
  created_at: number;
  updated_at: number;
  approved_at: number | null;
};

export type ProjectQuestion = {
  id: string;
  question: string;
  options: string[];
  multi: boolean;
  context: string | null;
  created_at: number;
  answer: string | null;
  answered_at: number | null;
};

export type ProjectTodo = {
  id: string;
  text: string;
  state: "todo" | "doing" | "blocked" | "done" | "dropped";
  blocked_by: string[];
  done_when: string | null;
  updated_at: number;
};

export type ProjectAgent = {
  id: string;
  workspace_id: string;
  parent_session_id: string | null;
  title: string;
  task: string;
  model: string;
  status: "running" | "completed" | "failed" | "cancelled" | string;
  log: string;
  result: string;
  error: string | null;
  steps: number;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
};

export type ProjectAreaCounts = {
  workspace_files: number;
  agents: number;
  running_agents: number;
  references: number;
  knowledge: number;
  memories: number;
  todos_open: number;
  todos_total: number;
  questions_open: number;
  questions_total: number;
  plan_steps: number;
  plan_open_steps: number;
};

export type UploadSource = {
  name: string;
  path?: string;
  content?: string;
  /** Base64 bytes for clipboard images and other path-less files. */
  contentBase64?: string;
  mime?: string;
  /** Local-only object URL used to preview images in the composer. */
  previewUrl?: string;
};

export type ClipboardImage = {
  dataBase64: string;
  mime: string;
  bytes: number;
};

export type ServiceStatus = {
  id: string;
  name: string;
  description: string;
  kind: string;
  url: string | null;
  envVar: string | null;
  status: string;
  detail: string;
  installHint: string;
};

export type GitFileChange = {
  path: string;
  oldPath: string | null;
  indexStatus: string;
  worktreeStatus: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
};

export type GitBranch = {
  name: string;
  current: boolean;
  remote: boolean;
  upstream: string | null;
};

export type GitRemote = {
  name: string;
  url: string;
};

export type GitCommit = {
  hash: string;
  shortHash: string;
  author: string;
  email: string;
  date: string;
  subject: string;
  refs: string;
};

export type GitRepoStatus = {
  path: string;
  name: string;
  isRepo: boolean;
  branch: string | null;
  detached: boolean;
  upstream: string | null;
  ahead: number;
  behind: number;
  hasCommits: boolean;
  files: GitFileChange[];
  branches: GitBranch[];
  remotes: GitRemote[];
  lastCommit: GitCommit | null;
  operation: string | null;
  error: string | null;
};

export type GitRepoSummary = {
  path: string;
  name: string;
  isRepo: boolean;
  branch: string | null;
  detached: boolean;
  upstream: string | null;
  ahead: number;
  behind: number;
  dirty: number;
  error: string | null;
};

export type GitCommandResult = {
  ok: boolean;
  message: string;
  status: GitRepoStatus | null;
};

export type UploadedFile = {
  name: string;
  path: string;
  bytes: number;
};

export type UploadReport = {
  files: UploadedFile[];
  skipped: string[];
};

export type ReferenceUpload = {
  repo: ProjectRepo;
  report: UploadReport;
};

export type KnowledgeImportReport = {
  documents: KnowledgeDocumentSummary[];
  skipped: string[];
};

export type McpServer = {
  name: string;
  transport: "stdio" | "http" | string;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  cwd: string | null;
  url: string | null;
  allowed_tools: string[];
  enabled: boolean;
  timeout_seconds: number | null;
};

export type McpServerInput = {
  name: string;
  transport?: string;
  command?: string | null;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string | null;
  url?: string | null;
  allowed_tools?: string[];
  enabled?: boolean;
  timeout_seconds?: number | null;
};

export type McpTool = {
  server: string;
  name: string;
  description: string;
};

export type McpServerChange = {
  server: McpServer;
  tools: McpTool[];
  warning: string | null;
};

export type ReleaseAsset = {
  name: string;
  url: string;
  size: number | null;
};

export type ReleaseInfo = {
  tag: string;
  version: string;
  name: string;
  notes: string;
  html_url: string;
  published_at: string | null;
  assets: ReleaseAsset[];
  checksums: ReleaseAsset | null;
};

export type UpdateStatus =
  | { kind: "no_release"; current: string }
  | { kind: "up_to_date"; current: string; latest: string }
  | {
      kind: "available";
      current: string;
      latest: string;
      release: ReleaseInfo;
      asset: ReleaseAsset;
      installer: string;
    };

export type InstallOutcome =
  | { kind: "app_image_replaced"; path: string }
  | { kind: "package_installed"; path: string; installer: string }
  | { kind: "installer_launched"; path: string; installer: string }
  | { kind: "installer_saved"; path: string; installer: string };
