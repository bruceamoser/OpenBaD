import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";

type Doctor = {
  schema_version: string;
  integrity: string;
  database_bytes: number;
  event_count: number;
};

type Audit = {
  valid: boolean;
  records: number;
  first_invalid: string | null;
};

type Foundation = {
  app_version: string;
  doctor: Doctor;
  audit: Audit;
  providers: { id: string; name: string; kind: string; enabled: boolean }[];
  routes: { id: string; name: string; model: string; task_class: string }[];
};

type Usage = {
  input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
};

type ChatReply = {
  session_id: string;
  workspace_id: string;
  text: string;
  steps: number;
  usage: Usage;
  tool_trace: string[];
};

type ChatEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  tools?: string[];
  usage?: Usage;
};

type SessionSummary = {
  id: string;
  workspace_id: string | null;
  workspace_path: string | null;
  title: string | null;
  status: string;
  updated_at: number;
  last_turn: number;
};

type SessionView = {
  session_id: string;
  messages: { role: string; text: string }[];
};

type FoundationState =
  | { kind: "loading" }
  | { kind: "ready"; foundation: Foundation }
  | { kind: "error"; message: string };

export default function App() {
  const [foundation, setFoundation] = useState<FoundationState>({ kind: "loading" });
  const [workspacePath, setWorkspacePath] = useState("");
  const [message, setMessage] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [allowWrites, setAllowWrites] = useState(false);
  const [allowCommands, setAllowCommands] = useState(false);
  const [apiKeyStatus, setApiKeyStatus] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const loadFoundation = useCallback(async () => {
    try {
      const result = await invoke<Foundation>("foundation");
      setFoundation({ kind: "ready", foundation: result });
    } catch (cause) {
      setFoundation({ kind: "error", message: describeError(cause) });
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await invoke<SessionSummary[]>("sessions"));
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void invoke<string>("workspace_default")
      .then(setWorkspacePath)
      .catch(() => setWorkspacePath("."));
    void invoke<boolean>("api_key_status")
      .then(setApiKeyStatus)
      .catch(() => setApiKeyStatus(false));
    void loadFoundation();
    void loadSessions();
  }, [loadFoundation, loadSessions]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, busy]);

  const saveApiKey = useCallback(async () => {
    setError(null);
    try {
      await invoke("save_api_key", { key: apiKeyInput });
      setApiKeyInput("");
      setApiKeyStatus(true);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [apiKeyInput]);

  const clearApiKey = useCallback(async () => {
    setError(null);
    try {
      await invoke("clear_api_key");
      setApiKeyStatus(false);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, []);

  const send = useCallback(async () => {
    const text = message.trim();
    if (text.length === 0 || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    setMessage("");
    setEntries((current) => [
      ...current,
      { id: nextId(), role: "user", text },
    ]);

    try {
      const reply = await invoke<ChatReply>("chat", {
        sessionId,
        workspacePath,
        message: text,
        allowWrites,
        allowCommands,
      });
      setSessionId(reply.session_id);
      setEntries((current) => [
        ...current,
        {
          id: nextId(),
          role: "assistant",
          text: reply.text || "(no text returned)",
          tools: reply.tool_trace,
          usage: reply.usage,
        },
      ]);
      void loadFoundation();
      void loadSessions();
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  }, [allowCommands, allowWrites, busy, loadFoundation, message, sessionId, workspacePath]);

  const openSession = useCallback(async (session: SessionSummary) => {
    setError(null);
    try {
      const view = await invoke<SessionView>("session_view", { sessionId: session.id });
      setSessionId(view.session_id);
      if (session.workspace_path !== null && session.workspace_path.length > 0) {
        setWorkspacePath(session.workspace_path);
      }
      setEntries(
        view.messages.map((message) => ({
          id: nextId(),
          role: message.role === "user" ? "user" : "assistant",
          text: message.text,
        })),
      );
    } catch (cause) {
      setError(describeError(cause));
    }
  }, []);

  const newSession = useCallback(() => {
    setSessionId(null);
    setEntries([]);
    setError(null);
  }, []);

  const changeWorkspace = useCallback((value: string) => {
    setWorkspacePath(value);
    setSessionId(null);
    setEntries([]);
  }, []);

  return (
    <main className="min-h-full bg-[#0b0f14] text-slate-100">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-6 py-6">
        <header className="flex flex-wrap items-end justify-between gap-4 border-b border-white/10 pb-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.35em] text-emerald-400">
              OpenBaD Desktop
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">
              A DeepSeek-first coding harness.
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">
              Chat with a workspace. Read/search tools run freely; file edits and
              commands are gated by the toggles in the right panel.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge ok={apiKeyStatus} label={apiKeyStatus ? "API key ready" : "No API key"} />
            <Badge
              ok={foundation.kind === "ready" && foundation.foundation.audit.valid}
              label={
                foundation.kind === "ready"
                  ? `Store ${foundation.foundation.doctor.schema_version}`
                  : "Store unavailable"
              }
            />
            <button
              type="button"
              onClick={newSession}
              className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 font-medium text-slate-200 transition hover:bg-white/10"
            >
              New session
            </button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section className="flex min-h-[60vh] flex-col rounded-xl border border-white/10 bg-white/[0.02]">
            <div className="flex-1 space-y-4 overflow-y-auto p-5">
              {entries.length === 0 && (
                <div className="mx-auto mt-12 max-w-lg text-center">
                  <p className="text-sm font-medium text-slate-200">
                    Ask about the codebase, request a change, or run a check.
                  </p>
                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Try: “Read src/main.rs and summarize what it does.” or
                    “Add a unit test for the pricing table.”
                  </p>
                </div>
              )}
              {entries.map((entry) => (
                <article
                  key={entry.id}
                  className={`rounded-lg border p-4 ${
                    entry.role === "user"
                      ? "border-emerald-400/20 bg-emerald-400/5"
                      : "border-white/10 bg-black/30"
                  }`}
                >
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    {entry.role}
                  </p>
                  <pre className="mt-2 whitespace-pre-wrap font-sans text-sm leading-6 text-slate-100">
                    {entry.text}
                  </pre>
                  {entry.tools !== undefined && entry.tools.length > 0 && (
                    <details className="mt-3 rounded-md border border-white/5 bg-black/20 p-3">
                      <summary className="cursor-pointer text-xs font-medium text-slate-400">
                        {entry.tools.length} tool result
                        {entry.tools.length === 1 ? "" : "s"}
                      </summary>
                      <div className="mt-2 space-y-2">
                        {entry.tools.map((tool, index) => (
                          <pre
                            key={`${entry.id}-tool-${index}`}
                            className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 font-mono text-xs text-slate-400"
                          >
                            {tool}
                          </pre>
                        ))}
                      </div>
                    </details>
                  )}
                  {entry.usage !== undefined && (
                    <p className="mt-3 font-mono text-[11px] text-slate-500">
                      {entry.usage.input_tokens.toLocaleString()} in ·{" "}
                      {entry.usage.cache_read_tokens.toLocaleString()} cached ·{" "}
                      {entry.usage.output_tokens.toLocaleString()} out · $
                      {entry.usage.cost_usd.toFixed(4)}
                    </p>
                  )}
                </article>
              ))}
              {busy && (
                <p className="animate-pulse text-sm text-emerald-300">
                  Working through the workspace…
                </p>
              )}
              <div ref={endRef} />
            </div>

            {error !== null && (
              <div className="mx-5 mb-3 rounded-lg border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-200">
                {error}
              </div>
            )}

            <div className="border-t border-white/10 p-4">
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
                rows={3}
                placeholder="Ask OpenBaD to inspect, explain, or change the workspace…"
                className="w-full resize-none rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-emerald-400/40"
              />
              <div className="mt-2 flex items-center justify-between gap-3">
                <div className="flex flex-wrap gap-3 text-xs text-slate-500">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={allowWrites}
                      onChange={(event) => setAllowWrites(event.target.checked)}
                      className="accent-emerald-400"
                    />
                    Allow file writes
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={allowCommands}
                      onChange={(event) => setAllowCommands(event.target.checked)}
                      className="accent-emerald-400"
                    />
                    Allow commands
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={busy || message.trim().length === 0}
                  className="rounded-lg border border-emerald-400/40 bg-emerald-400/10 px-4 py-2 text-sm font-medium text-emerald-300 transition hover:bg-emerald-400/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy ? "Running…" : "Send"}
                </button>
              </div>
            </div>
          </section>

          <aside className="space-y-4">
            <Panel title="Recent sessions">
              <div className="space-y-1">
                {sessions.length === 0 && (
                  <p className="text-xs text-slate-500">No sessions yet.</p>
                )}
                {sessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => void openSession(session)}
                    className={`w-full rounded-md border px-2 py-2 text-left text-xs transition ${
                      session.id === sessionId
                        ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                        : "border-white/5 bg-black/20 text-slate-300 hover:bg-white/5"
                    }`}
                  >
                    <span className="block truncate font-medium">
                      {session.title ?? "Untitled session"}
                    </span>
                    <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
                      {session.workspace_path ?? "no workspace"} · turn {session.last_turn}
                    </span>
                  </button>
                ))}
              </div>
            </Panel>

            <Panel title="Workspace">
              <label className="text-xs text-slate-500" htmlFor="workspace">
                Folder
              </label>
              <input
                id="workspace"
                value={workspacePath}
                onChange={(event) => changeWorkspace(event.target.value)}
                className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs text-slate-200 outline-none focus:border-emerald-400/40"
              />
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Changing the folder starts a new session. The agent is confined to
                this root.
              </p>
            </Panel>

            <Panel title="DeepSeek API key">
              <div className="flex items-center justify-between gap-2">
                <Badge ok={apiKeyStatus} label={apiKeyStatus ? "Configured" : "Missing"} />
                {apiKeyStatus && (
                  <button
                    type="button"
                    onClick={() => void clearApiKey()}
                    className="text-xs text-slate-500 underline-offset-2 hover:text-amber-300 hover:underline"
                  >
                    Clear
                  </button>
                )}
              </div>
              <div className="mt-3 flex gap-2">
                <input
                  type="password"
                  value={apiKeyInput}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                  placeholder="sk-…"
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-emerald-400/40"
                />
                <button
                  type="button"
                  onClick={() => void saveApiKey()}
                  disabled={apiKeyInput.trim().length === 0}
                  className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs font-medium text-slate-200 transition hover:bg-white/10 disabled:opacity-40"
                >
                  Save
                </button>
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                With the <code>keyring</code> feature the key goes to the OS
                keychain. Without it, this build keeps it in memory; set{" "}
                <code>DEEPSEEK_API_KEY</code> for sessions.
              </p>
            </Panel>

            <Panel title="Local store">
              {foundation.kind === "loading" && (
                <p className="text-xs text-slate-500">Checking…</p>
              )}
              {foundation.kind === "error" && (
                <p className="text-xs text-amber-300">{foundation.message}</p>
              )}
              {foundation.kind === "ready" && (
                <div className="space-y-2 text-xs">
                  <Row label="Schema" value={foundation.foundation.doctor.schema_version} />
                  <Row label="Integrity" value={foundation.foundation.doctor.integrity} />
                  <Row
                    label="Database"
                    value={formatBytes(foundation.foundation.doctor.database_bytes)}
                  />
                  <Row
                    label="Events"
                    value={foundation.foundation.doctor.event_count.toLocaleString()}
                  />
                  <Row
                    label="Audit"
                    value={
                      foundation.foundation.audit.valid
                        ? `${foundation.foundation.audit.records} verified`
                        : "tamper detected"
                    }
                  />
                </div>
              )}
            </Panel>
          </aside>
        </div>
      </div>
    </main>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-slate-500">{label}</span>
      <span className="truncate font-mono text-slate-300" title={value}>
        {value}
      </span>
    </div>
  );
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-full border px-3 py-1 text-xs font-medium ${
        ok
          ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
          : "border-amber-400/40 bg-amber-400/10 text-amber-300"
      }`}
    >
      {label}
    </span>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KiB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function nextId(): string {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function describeError(error: unknown): string {
  if (typeof error === "string") {
    return error;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
