import { useCallback, useEffect, useState } from "react";
import {
  Check,
  CircleAlert,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import {
  addMcpServer,
  loadMcpServers,
  removeMcpServer,
  setMcpServerEnabled,
  testMcpServer,
  updateMcpServer,
} from "./backend";
import type { McpServer, McpServerInput, McpTool } from "./types";

type FormState = {
  name: string;
  transport: "stdio" | "http";
  command: string;
  args: string;
  env: string;
  cwd: string;
  url: string;
  allowedTools: string;
  timeout: string;
  enabled: boolean;
};

const emptyForm: FormState = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  cwd: "",
  url: "",
  allowedTools: "",
  timeout: "",
  enabled: true,
};

function formFor(server: McpServer): FormState {
  return {
    name: server.name,
    transport: server.transport === "http" ? "http" : "stdio",
    command: server.command ?? "",
    args: server.args.join("\n"),
    env: Object.entries(server.env)
      .map(([key, value]) => `${key}=${value}`)
      .join("\n"),
    cwd: server.cwd ?? "",
    url: server.url ?? "",
    allowedTools: server.allowed_tools.join(", "),
    timeout: server.timeout_seconds === null ? "" : String(server.timeout_seconds),
    enabled: server.enabled,
  };
}

function inputFor(form: FormState): McpServerInput {
  const env: Record<string, string> = {};
  for (const line of form.env.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.length === 0) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator <= 0) {
      throw new Error(`environment line '${trimmed}' must be KEY=VALUE`);
    }
    env[trimmed.slice(0, separator).trim()] = trimmed.slice(separator + 1);
  }
  const timeout = form.timeout.trim();
  if (timeout.length > 0) {
    const seconds = Number(timeout);
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 600) {
      throw new Error("timeout must be a whole number between 1 and 600");
    }
  }
  return {
    name: form.name.trim(),
    transport: form.transport,
    command: form.command.trim().length > 0 ? form.command : null,
    args: form.args
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0),
    env,
    cwd: form.cwd.trim().length > 0 ? form.cwd : null,
    url: form.url.trim().length > 0 ? form.url : null,
    allowed_tools: form.allowedTools
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0),
    enabled: form.enabled,
    timeout_seconds: timeout.length > 0 ? Number(timeout) : null,
  };
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function serverDetail(server: McpServer): string {
  if (server.transport === "http") {
    return server.url ?? "no URL";
  }
  return [server.command ?? "", ...server.args].join(" ").trim() || "no command";
}

const fieldClass =
  "w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[11px] text-slate-200 outline-none focus:border-emerald-400/40";
const labelClass =
  "mb-1 block text-[10px] uppercase tracking-wider text-slate-500";
const buttonClass =
  "rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-slate-200 hover:bg-white/10 disabled:opacity-40";

export default function McpSettings({ workspacePath }: { workspacePath: string }) {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [tools, setTools] = useState<Record<string, McpTool[]>>({});
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (workspacePath.length === 0) {
      setServers([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setServers(await loadMcpServers(workspacePath));
      setError(null);
    } catch (loadError) {
      setError(messageOf(loadError));
    } finally {
      setLoading(false);
    }
  }, [workspacePath]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = (patch: Partial<FormState>) =>
    setForm((current) => ({ ...current, ...patch }));

  const submit = async () => {
    setError(null);
    setNotice(null);
    let input: McpServerInput;
    try {
      input = inputFor(form);
    } catch (validationError) {
      setError(messageOf(validationError));
      return;
    }
    if (input.name.length === 0) {
      setError("server name must not be empty");
      return;
    }
    setBusy("save");
    try {
      const change =
        editing === null
          ? await addMcpServer(workspacePath, input)
          : editing !== undefined
            ? await updateMcpServer(workspacePath, editing, input)
            : null;
      if (change === null) {
        return;
      }
      setTools((current) => ({ ...current, [change.server.name]: change.tools }));
      setNotice(
        change.warning === null
          ? `${editing === null ? "Added" : "Saved"} ${change.server.name}.`
          : `${editing === null ? "Added" : "Saved"} ${change.server.name}, but the connection failed: ${change.warning}`,
      );
      setEditing(undefined);
      setForm(emptyForm);
      await load();
    } catch (saveError) {
      setError(messageOf(saveError));
    } finally {
      setBusy(null);
    }
  };

  const runTest = async (server: McpServer) => {
    setError(null);
    setNotice(null);
    setBusy(`test:${server.name}`);
    try {
      const result = await testMcpServer(workspacePath, server.name);
      setTools((current) => ({ ...current, [server.name]: result }));
      setNotice(`${server.name} is reachable.`);
    } catch (testError) {
      setError(messageOf(testError));
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (server: McpServer) => {
    setError(null);
    setNotice(null);
    setBusy(`toggle:${server.name}`);
    try {
      await setMcpServerEnabled(workspacePath, server.name, !server.enabled);
      await load();
    } catch (toggleError) {
      setError(messageOf(toggleError));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (server: McpServer) => {
    if (confirmDelete !== server.name) {
      setConfirmDelete(server.name);
      return;
    }
    setConfirmDelete(null);
    setError(null);
    setNotice(null);
    setBusy(`remove:${server.name}`);
    try {
      await removeMcpServer(workspacePath, server.name);
      setTools((current) => {
        const next = { ...current };
        delete next[server.name];
        return next;
      });
      setNotice(`Removed ${server.name}.`);
      await load();
    } catch (removeError) {
      setError(messageOf(removeError));
    } finally {
      setBusy(null);
    }
  };

  const startEdit = (server: McpServer) => {
    setEditing(server.name);
    setForm(formFor(server));
    setError(null);
    setNotice(null);
  };

  return (
    <div className="max-w-2xl space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium text-slate-200">MCP servers</p>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Servers are defined once globally. Each project chooses which ones
            it enables in <code>.openbad/mcp.yaml</code>. The model can add,
            change, and toggle servers through the mcp domain.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            aria-label="Reload MCP servers"
            onClick={() => void load()}
            disabled={loading}
            className="rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:bg-white/10 hover:text-slate-200 disabled:opacity-40"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : undefined} />
          </button>
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setForm(emptyForm);
              setError(null);
              setNotice(null);
            }}
            className={buttonClass}
          >
            <span className="flex items-center gap-1.5">
              <Plus size={13} /> Add server
            </span>
          </button>
        </div>
      </div>

      {workspacePath.length === 0 && (
        <p role="status" className="text-[11px] text-amber-300">
          Open a project to manage which MCP servers it enables.
        </p>
      )}

      {error !== null && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-[11px] text-rose-200"
        >
          <CircleAlert size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}
      {notice !== null && error === null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-emerald-400/20 bg-emerald-400/[0.06] px-3 py-2 text-[11px] text-emerald-200"
        >
          <Check size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{notice}</span>
        </div>
      )}

      {editing !== undefined && (
        <form
          aria-label={editing === null ? "Add MCP server" : "Edit MCP server"}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
          className="space-y-3 rounded-lg border border-white/10 bg-white/[0.02] p-3"
        >
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="mcp-name">
                Name
              </label>
              <input
                id="mcp-name"
                aria-label="Server name"
                value={form.name}
                disabled={editing !== null}
                placeholder="docs"
                onChange={(event) => update({ name: event.target.value })}
                className={fieldClass}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="mcp-transport">
                Transport
              </label>
              <select
                id="mcp-transport"
                aria-label="Transport"
                value={form.transport}
                onChange={(event) =>
                  update({
                    transport: event.target.value === "http" ? "http" : "stdio",
                  })
                }
                className={fieldClass}
              >
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </div>
          </div>

          {form.transport === "stdio" ? (
            <>
              <div>
                <label className={labelClass} htmlFor="mcp-command">
                  Command
                </label>
                <input
                  id="mcp-command"
                  aria-label="Command"
                  value={form.command}
                  placeholder="npx"
                  onChange={(event) => update({ command: event.target.value })}
                  className={fieldClass}
                />
              </div>
              <div>
                <label className={labelClass} htmlFor="mcp-args">
                  Arguments (one per line)
                </label>
                <textarea
                  id="mcp-args"
                  aria-label="Arguments"
                  value={form.args}
                  rows={3}
                  placeholder={"-y\n@example/mcp-docs"}
                  onChange={(event) => update({ args: event.target.value })}
                  className={`${fieldClass} font-mono`}
                />
              </div>
              <div>
                <label className={labelClass} htmlFor="mcp-cwd">
                  Working directory (optional)
                </label>
                <input
                  id="mcp-cwd"
                  aria-label="Working directory"
                  value={form.cwd}
                  onChange={(event) => update({ cwd: event.target.value })}
                  className={fieldClass}
                />
              </div>
            </>
          ) : (
            <div>
              <label className={labelClass} htmlFor="mcp-url">
                URL
              </label>
              <input
                id="mcp-url"
                aria-label="URL"
                value={form.url}
                placeholder="https://mcp.example.com/rpc"
                onChange={(event) => update({ url: event.target.value })}
                className={fieldClass}
              />
            </div>
          )}

          <div>
            <label className={labelClass} htmlFor="mcp-env">
              Environment (KEY=VALUE per line)
            </label>
            <textarea
              id="mcp-env"
              aria-label="Environment"
              value={form.env}
              rows={2}
              placeholder="DOCS_TOKEN=..."
              onChange={(event) => update({ env: event.target.value })}
              className={`${fieldClass} font-mono`}
            />
            <p className="mt-1 text-[10px] text-slate-500">
              Values stay on this machine and are never shown to the model.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="mcp-tools">
                Allowed tools (comma separated)
              </label>
              <input
                id="mcp-tools"
                aria-label="Allowed tools"
                value={form.allowedTools}
                placeholder="empty allows every tool"
                onChange={(event) => update({ allowedTools: event.target.value })}
                className={fieldClass}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="mcp-timeout">
                Timeout seconds (optional)
              </label>
              <input
                id="mcp-timeout"
                aria-label="Timeout seconds"
                type="number"
                min={1}
                max={600}
                value={form.timeout}
                onChange={(event) => update({ timeout: event.target.value })}
                className={fieldClass}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-[11px] text-slate-400">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => update({ enabled: event.target.checked })}
            />
            Enable for this project
          </label>

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy === "save"}
              className="rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-40"
            >
              <span className="flex items-center gap-1.5">
                {busy === "save" && <Loader2 size={12} className="animate-spin" />}
                Save server
              </span>
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(undefined);
                setForm(emptyForm);
                setError(null);
              }}
              className={buttonClass}
            >
              <span className="flex items-center gap-1.5">
                <X size={13} /> Cancel
              </span>
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {loading && servers.length === 0 && (
          <p className="flex items-center gap-2 text-[11px] text-slate-500">
            <Loader2 size={12} className="animate-spin" /> Loading MCP servers…
          </p>
        )}
        {!loading && servers.length === 0 && workspacePath.length > 0 && (
          <p className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-[11px] text-slate-500">
            No MCP servers are defined yet. Add one here or ask the model to add
            it with the mcp domain; definitions are shared by every project.
          </p>
        )}
        {servers.map((server) => (
          <div
            key={server.name}
            className="rounded-lg border border-white/10 bg-white/[0.02] p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-[12px] text-slate-200">
                  <span className="truncate">{server.name}</span>
                  <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-slate-400">
                    {server.transport}
                  </span>
                  <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-slate-500">
                    global
                  </span>
                </p>
                <p className="mt-0.5 break-all font-mono text-[10px] text-slate-500">
                  {serverDetail(server)}
                </p>
                {server.allowed_tools.length > 0 && (
                  <p className="mt-0.5 text-[10px] text-slate-500">
                    Allowed: {server.allowed_tools.join(", ")}
                  </p>
                )}
              </div>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${
                  server.enabled
                    ? "bg-emerald-400/10 text-emerald-300"
                    : "bg-white/[0.06] text-slate-500"
                }`}
              >
                {server.enabled ? "Enabled here" : "Not enabled"}
              </span>
            </div>

            {tools[server.name] !== undefined && (
              <div
                role="status"
                className="mt-2 rounded-md border border-white/10 bg-black/20 px-2.5 py-2"
              >
                {tools[server.name].length === 0 ? (
                  <p className="text-[10px] text-slate-500">
                    Reachable, but no allowed tools.
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {tools[server.name].map((tool) => (
                      <li
                        key={`${tool.server}/${tool.name}`}
                        className="font-mono text-[10px] text-slate-400"
                      >
                        {tool.server}/{tool.name}
                        {tool.description.length > 0 && (
                          <span className="ml-2 font-sans text-slate-500">
                            {tool.description}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div className="mt-2 flex flex-wrap gap-1.5">
              <button
                type="button"
                aria-label={`Test ${server.name}`}
                onClick={() => void runTest(server)}
                disabled={busy !== null}
                className={buttonClass}
              >
                <span className="flex items-center gap-1">
                  {busy === `test:${server.name}` && (
                    <Loader2 size={11} className="animate-spin" />
                  )}
                  Test
                </span>
              </button>
              <button
                type="button"
                aria-label={`Edit ${server.name}`}
                onClick={() => startEdit(server)}
                disabled={busy !== null}
                className={buttonClass}
              >
                <span className="flex items-center gap-1">
                  <Pencil size={11} /> Edit
                </span>
              </button>
              <button
                type="button"
                aria-label={`${server.enabled ? "Disable" : "Enable"} ${server.name} for this project`}
                title="Enablement applies to the current project only"
                onClick={() => void toggle(server)}
                disabled={busy !== null}
                className={buttonClass}
              >
                {server.enabled ? "Disable here" : "Enable here"}
              </button>
              <button
                type="button"
                aria-label={`Delete ${server.name}`}
                onClick={() => void remove(server)}
                disabled={busy !== null}
                className={`rounded-md border px-3 py-1.5 text-[11px] ${
                  confirmDelete === server.name
                    ? "border-rose-400/40 bg-rose-400/10 text-rose-200"
                    : "border-white/10 bg-white/5 text-amber-300 hover:bg-white/10"
                }`}
              >
                <span className="flex items-center gap-1">
                  <Trash2 size={11} />
                  {confirmDelete === server.name ? "Confirm delete" : "Delete"}
                </span>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
