import { useCallback, useEffect, useRef, useState } from "react";

import {
  apiKeyStatus as fetchApiKeyStatus,
  cancelChat,
  chat as sendChat,
  clearApiKey as clearStoredApiKey,
  loadFoundation as fetchFoundation,
  loadSession as fetchSession,
  loadSessions as fetchSessions,
  saveApiKey as persistApiKey,
  workspaceDefault,
} from "./backend";
import type { ChatEntry, FoundationState, SessionSummary } from "./types";

export default function App() {
  const [foundation, setFoundation] = useState<FoundationState>({ kind: "loading" });
  const [workspacePath, setWorkspacePath] = useState("");
  const [message, setMessage] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [allowWrites, setAllowWrites] = useState(false);
  const [allowCommands, setAllowCommands] = useState(false);
  const [maxSteps, setMaxSteps] = useState(64);
  const [model, setModel] = useState("deepseek-flash");
  const [thinking, setThinking] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState("high");
  const [apiKeyStatus, setApiKeyStatus] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);

  const loadFoundation = useCallback(async () => {
    try {
      const result = await fetchFoundation();
      setFoundation({ kind: "ready", foundation: result });
    } catch (cause) {
      setFoundation({ kind: "error", message: describeError(cause) });
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await fetchSessions());
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void workspaceDefault()
      .then(setWorkspacePath)
      .catch(() => setWorkspacePath("."));
    void fetchApiKeyStatus()
      .then(setApiKeyStatus)
      .catch(() => setApiKeyStatus(false));
    void loadFoundation();
    void loadSessions();
  }, [loadFoundation, loadSessions]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element !== null && followRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [entries, busy]);

  const handleChatScroll = useCallback(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    followRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 96;
  }, []);

  const saveApiKey = useCallback(async () => {
    setError(null);
    try {
      await persistApiKey(apiKeyInput);
      setApiKeyInput("");
      setApiKeyStatus(true);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [apiKeyInput]);

  const stop = useCallback(async () => {
    setStopRequested(true);
    try {
      await cancelChat();
    } catch (cause) {
      setError(describeError(cause));
    }
  }, []);

  const clearApiKey = useCallback(async () => {
    setError(null);
    try {
      await clearStoredApiKey();
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
    setStopRequested(false);
    setError(null);
    setMessage("");
    followRef.current = true;
    const assistantId = nextId();
    setEntries((current) => [
      ...current,
      { id: nextId(), role: "user", text },
      {
        id: assistantId,
        role: "assistant",
        text: "",
        reasoning: "",
        status: "Starting…",
        tools: [],
      },
    ]);

    try {
      const reply = await sendChat(
        {
          sessionId,
          workspacePath,
          message: text,
          allowWrites,
          allowCommands,
          maxSteps,
          model,
          thinking,
          reasoningEffort,
        },
        (event) => {
          setEntries((current) =>
            current.map((entry) => {
              if (entry.id !== assistantId) {
                return entry;
              }
              switch (event.type) {
                case "step":
                  return { ...entry, status: `Step ${event.step}` };
                case "reasoning":
                  return {
                    ...entry,
                    reasoning: `${entry.reasoning ?? ""}${event.text}`,
                  };
                case "content":
                  return {
                    ...entry,
                    text: `${entry.text}${event.text}`,
                    status: "Writing…",
                  };
                case "tool_call":
                  return {
                    ...entry,
                    status: `Calling ${event.name}…`,
                    tools: [
                      ...(entry.tools ?? []),
                      `→ ${event.name}(${event.arguments})`,
                    ],
                  };
                case "tool_result":
                  return {
                    ...entry,
                    status: `${event.name} finished`,
                    tools: [
                      ...(entry.tools ?? []),
                      `${event.is_error ? "✗" : "✓"} ${event.name}: ${event.content}`,
                    ],
                  };
                case "usage":
                  return { ...entry, usage: event.usage };
              }
            }),
          );
        },
      );
      setSessionId(reply.session_id);
      setEntries((current) =>
        current.map((entry) =>
          entry.id === assistantId
            ? {
                ...entry,
                text: reply.text || entry.text || "(no text returned)",
                tools:
                  entry.tools !== undefined && entry.tools.length > 0
                    ? entry.tools
                    : reply.tool_trace,
                usage: reply.usage,
                status: `Done · ${reply.steps} step${reply.steps === 1 ? "" : "s"}`,
              }
            : entry,
        ),
      );
      void loadFoundation();
      void loadSessions();
    } catch (cause) {
      const message = describeError(cause);
      setError(message);
      setEntries((current) =>
        current.map((entry) =>
          entry.id === assistantId
            ? {
                ...entry,
                status: "Failed",
                text: entry.text || "(request failed)",
              }
            : entry,
        ),
      );
    } finally {
      setBusy(false);
      setStopRequested(false);
    }
  }, [
    allowCommands,
    allowWrites,
    busy,
    loadFoundation,
    maxSteps,
    message,
    model,
    reasoningEffort,
    sessionId,
    thinking,
    workspacePath,
  ]);

  const openSession = useCallback(async (session: SessionSummary) => {
    setError(null);
    try {
      const view = await fetchSession(session.id);
      setSessionId(view.session_id);
      followRef.current = true;
      if (session.workspace_path !== null && session.workspace_path.length > 0) {
        setWorkspacePath(session.workspace_path);
      }
      setEntries(
        view.messages.map((message) => ({
          id: nextId(),
          role: message.role === "user" ? "user" : "assistant",
          text: message.text,
          reasoning: message.reasoning ?? "",
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
    followRef.current = true;
  }, []);

  const changeWorkspace = useCallback((value: string) => {
    setWorkspacePath(value);
    setSessionId(null);
    setEntries([]);
  }, []);

  return (
    <main className="min-h-full bg-[#0b0f14] text-slate-100">
      <div className="mx-auto flex h-screen max-w-7xl flex-col gap-6 overflow-hidden px-6 py-6">
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
          <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]">
            <div
              ref={scrollRef}
              onScroll={handleChatScroll}
              className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5"
            >
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
                  {entry.status !== undefined && (
                    <p className="mt-1 font-mono text-[10px] text-slate-500">
                      {entry.status}
                    </p>
                  )}
                  {entry.reasoning !== undefined && entry.reasoning.length > 0 && (
                    <details open className="mt-3 rounded-md border border-white/5 bg-black/20 p-3">
                      <summary className="cursor-pointer text-xs font-medium text-slate-400">
                        Thinking
                      </summary>
                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-slate-400">
                        {entry.reasoning}
                      </pre>
                    </details>
                  )}
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
                      {entry.usage.cache_read_tokens.toLocaleString()} cached (
                      {cacheHitShare(entry.usage).toFixed(0)}%) ·{" "}
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
                  <label className="flex items-center gap-2">
                    Max steps
                    <input
                      type="number"
                      min={1}
                      max={256}
                      value={maxSteps}
                      onChange={(event) =>
                        setMaxSteps(Math.max(1, Math.min(256, Number(event.target.value) || 1)))
                      }
                      className="w-16 rounded border border-white/10 bg-black/30 px-2 py-1 text-xs text-slate-200 outline-none focus:border-emerald-400/40"
                    />
                  </label>
                </div>
                <div className="flex items-center gap-2">
                  {busy && (
                    <button
                      type="button"
                      onClick={() => void stop()}
                      disabled={stopRequested}
                      className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm font-medium text-amber-300 transition hover:bg-amber-400/20 disabled:opacity-40"
                    >
                      {stopRequested ? "Stopping…" : "Stop"}
                    </button>
                  )}
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
            </div>
          </section>

          <aside className="min-h-0 space-y-4 overflow-y-auto">
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

            <Panel title="Model">
              <label className="text-xs text-slate-500" htmlFor="model">
                Route
              </label>
              <select
                id="model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-xs text-slate-200 outline-none focus:border-emerald-400/40"
              >
                <option value="deepseek-flash">deepseek-flash · fast</option>
                <option value="deepseek-v4-pro">deepseek-v4-pro · reasoning</option>
              </select>
              <div className="mt-3 flex items-center justify-between gap-3 text-xs text-slate-400">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={thinking}
                    onChange={(event) => setThinking(event.target.checked)}
                    className="accent-emerald-400"
                  />
                  Thinking
                </label>
                <select
                  value={reasoningEffort}
                  onChange={(event) => setReasoningEffort(event.target.value)}
                  disabled={!thinking}
                  className="rounded border border-white/10 bg-black/30 px-2 py-1 text-xs text-slate-200 outline-none focus:border-emerald-400/40 disabled:opacity-40"
                >
                  <option value="low">low effort</option>
                  <option value="high">high effort</option>
                  <option value="max">max effort</option>
                </select>
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Changing model, thinking, or effort starts a new cache series.
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

function cacheHitShare(usage: { input_tokens: number; cache_read_tokens: number }): number {
  const billed = usage.input_tokens + usage.cache_read_tokens;
  return billed === 0 ? 0 : (usage.cache_read_tokens / billed) * 100;
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
