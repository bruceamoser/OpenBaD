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
  status?: string;
  tools?: string[];
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
};

export type FileCheckpointSummary = {
  id: string;
  path: string;
  existed: boolean;
  bytes: number;
  created_at: number;
};

export type SessionSummary = {
  id: string;
  workspace_id: string | null;
  workspace_path: string | null;
  title: string | null;
  status: string;
  updated_at: number;
  last_turn: number;
};

export type SessionView = {
  session_id: string;
  messages: { role: string; text: string; reasoning: string | null }[];
};

export type FoundationState =
  | { kind: "loading" }
  | { kind: "ready"; foundation: Foundation }
  | { kind: "error"; message: string };
