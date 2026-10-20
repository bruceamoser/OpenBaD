import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowUpRight,
  Ban,
  Bot,
  CircleCheck,
  Loader2,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import { cancelProjectAgent, loadProjectAgents } from "./backend";
import type { Artifact, ProjectAgent } from "./types";

type AgentsPanelProps = {
  workspacePath: string;
  revision: number;
  onArtifact: (artifact: Artifact | null) => void;
};

function relativeTime(timestamp: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function duration(agent: ProjectAgent): string {
  const end = agent.finished_at ?? agent.updated_at;
  const seconds = Math.max(0, Math.round((end - agent.created_at) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

const statusStyles: Record<string, string> = {
  running: "bg-sky-400/10 text-sky-200",
  completed: "bg-emerald-400/10 text-emerald-200",
  failed: "bg-rose-400/10 text-rose-200",
  cancelled: "bg-amber-400/10 text-amber-200",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 size={12} className="animate-spin" />;
  if (status === "completed") return <CircleCheck size={12} />;
  if (status === "failed") return <TriangleAlert size={12} />;
  if (status === "cancelled") return <Ban size={12} />;
  return <Bot size={12} />;
}

export default function AgentsPanel({
  workspacePath,
  revision,
  onArtifact,
}: AgentsPanelProps) {
  const [agents, setAgents] = useState<ProjectAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (workspacePath.length === 0) {
      setAgents([]);
      return;
    }
    setLoading(true);
    try {
      const records = await loadProjectAgents(workspacePath);
      setAgents(records);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [workspacePath]);

  useEffect(() => {
    void refresh();
  }, [refresh, revision]);

  const running = useMemo(
    () => agents.some((agent) => agent.status === "running"),
    [agents],
  );

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [refresh, running]);

  const cancel = async (id: string) => {
    try {
      await cancelProjectAgent(id);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
        <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">
          {agents.length} agent{agents.length === 1 ? "" : "s"}
          {running ? " · live" : ""}
        </p>
        <button
          type="button"
          onClick={() => void refresh()}
          aria-label="Refresh subagents"
          className="rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
        </button>
      </div>
      {error !== null && (
        <p className="border-b border-rose-400/20 bg-rose-400/5 px-3 py-2 text-[11px] text-rose-200">
          {error}
        </p>
      )}
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2.5">
        {agents.length === 0 && !loading && (
          <div className="rounded-lg border border-dashed border-white/10 p-3 text-[11px] leading-5 text-slate-500">
            <p className="mb-1 font-medium text-slate-300">No subagents yet</p>
            Ask the assistant to delegate a task. It can call the{" "}
            <code className="rounded bg-white/5 px-1 text-[10px] text-slate-300">
              subagent
            </code>{" "}
            tool with <code className="text-slate-300">action: "spawn"</code>, then
            collect results with <code className="text-slate-300">action: "status"</code>.
            Workers share this project&apos;s workspace and access mode.
          </div>
        )}
        {agents.map((agent) => (
          <article
            key={agent.id}
            className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-2.5"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-[12px] font-medium text-slate-200">
                  {agent.title}
                </p>
                <p className="mt-0.5 truncate text-[10px] text-slate-500">
                  {agent.model} · {agent.steps} steps · {duration(agent)} ·{" "}
                  {relativeTime(agent.created_at)}
                </p>
              </div>
              <span
                className={`flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${
                  statusStyles[agent.status] ?? "bg-white/5 text-slate-400"
                }`}
              >
                <StatusIcon status={agent.status} />
                {agent.status}
              </span>
            </div>
            <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-[11px] leading-4 text-slate-400">
              {agent.task}
            </p>
            {agent.result.trim().length > 0 && (
              <button
                type="button"
                onClick={() =>
                  onArtifact({
                    kind: "markdown",
                    title: agent.title,
                    content: agent.result,
                  })
                }
                className="mt-2 flex items-center gap-1 rounded-md bg-white/[0.04] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.08]"
              >
                <ArrowUpRight size={11} /> Open result
              </button>
            )}
            {agent.error !== null && (
              <p className="mt-2 whitespace-pre-wrap rounded bg-rose-400/5 p-1.5 text-[10px] text-rose-200">
                {agent.error}
              </p>
            )}
            <details className="mt-2 text-[10px] text-slate-500">
              <summary className="cursor-pointer select-none hover:text-slate-300">
                Activity log
              </summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-black/30 p-1.5 text-[9px] leading-4 text-slate-400">
                {agent.log.trim() || "No activity recorded."}
              </pre>
            </details>
            {agent.status === "running" && (
              <button
                type="button"
                onClick={() => void cancel(agent.id)}
                className="mt-2 flex items-center gap-1 rounded-md border border-rose-400/20 px-2 py-1 text-[10px] text-rose-200 hover:bg-rose-400/10"
              >
                <Ban size={11} /> Cancel
              </button>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
