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
};

export type ChatEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  lastContentStep?: number;
  lastReasoningStep?: number;
  status?: string;
  tools?: string[];
  steering?: boolean;
  usage?: Usage;
};

export type AgentEvent =
  | { type: "step"; step: number }
  | { type: "reasoning"; step: number; text: string }
  | { type: "content"; step: number; text: string }
  | { type: "tool_call"; step: number; name: string; arguments: string }
  | {
      type: "tool_result";
      step: number;
      name: string;
      content: string;
      is_error: boolean;
      metadata: unknown;
    }
  | { type: "usage"; usage: Usage };

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

export type AccessMode = "read" | "edit" | "full";

export type SessionSummary = {
  id: string;
  workspace_id: string | null;
  workspace_path: string | null;
  title: string | null;
  status: string;
  updated_at: number;
  last_turn: number;
  access_mode: AccessMode;
};

export type SessionView = {
  session_id: string;
  messages: { role: string; text: string; reasoning: string | null }[];
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

export type ProjectFileView = {
  path: string;
  kind: "text" | "image";
  content: string | null;
  data_url: string | null;
};

export type Artifact = {
  kind: "web" | "image" | "markdown" | "diff" | "file";
  title: string;
  uri?: string;
  content?: string;
  language?: string;
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
