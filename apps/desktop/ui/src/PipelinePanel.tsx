import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  CircleSlash,
  Loader2,
  Play,
  RefreshCw,
  Square,
  TriangleAlert,
} from "lucide-react";

import {
  checkPipelineGuards,
  loadPipelineConfig,
  loadPipelineStatus,
  loadPipelineTickets,
  pipelineExecute,
  pipelineLaunch,
  pipelineStop,
  pipelineTick,
  pipelineUnwatch,
  pipelineWatch,
  savePipelineConfig,
} from "./backend";
import type {
  PipelineGuards,
  PipelineStatus,
  PipelineTicketRow,
  PipelineTickReport,
} from "./types";

type PipelinePanelProps = {
  workspacePath: string;
  revision: number;
};

const groups: { title: string; stages: PipelineTicketRow["stage"][] }[] = [
  {
    title: "Needs you",
    stages: [
      "person/delivery",
      "person/answer",
      "person/review",
      "person/blocked",
      "person/failed",
    ],
  },
  { title: "Working", stages: ["working", "working/queued"] },
  { title: "Idle", stages: ["idle"] },
  { title: "Ended", stages: ["ended"] },
];

const stageStyles: Record<string, string> = {
  "person/review": "bg-obd-info/10 text-obd-info",
  "person/answer": "bg-obd-warning/10 text-obd-warning",
  "person/blocked": "bg-obd-warning/10 text-obd-warning",
  "person/failed": "bg-obd-danger/10 text-obd-danger",
  "person/delivery": "bg-obd-danger/10 text-obd-danger",
  working: "bg-obd-info/10 text-obd-info",
  "working/queued": "bg-obd-raised text-obd-subtle",
  idle: "bg-obd-raised text-obd-subtle",
  ended: "bg-obd-raised text-obd-faint",
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

function threadLabel(thread: string): string {
  const hash = thread.lastIndexOf("#");
  return hash === -1 ? thread : `issue #${thread.slice(hash + 1)}`;
}

export default function PipelinePanel({
  workspacePath,
  revision,
}: PipelinePanelProps) {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [tickets, setTickets] = useState<PipelineTicketRow[]>([]);
  const [watching, setWatching] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tick, setTick] = useState<PipelineTickReport | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [configText, setConfigText] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [guards, setGuards] = useState<PipelineGuards | null>(null);
  const stopped = useRef(false);

  const refresh = useCallback(async () => {
    if (!workspacePath) {
      setStatus(null);
      setTickets([]);
      return;
    }
    try {
      const [nextStatus, nextTickets] = await Promise.all([
        loadPipelineStatus(workspacePath),
        loadPipelineTickets(workspacePath),
      ]);
      if (stopped.current) return;
      setStatus(nextStatus);
      setTickets(nextTickets);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [workspacePath]);

  useEffect(() => {
    stopped.current = false;
    void refresh();
    return () => {
      stopped.current = true;
    };
  }, [refresh, revision]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!stopped.current) void refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const needsPerson = useMemo(
    () => tickets.filter((ticket) => ticket.needs_a_person).length,
    [tickets],
  );

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  };

  const checkNow = () =>
    run("tick", async () => {
      const report = await pipelineTick(workspacePath);
      setTick(report);
      setNotice(
        report.skipped ??
          `${report.admitted.length} judged, ${report.deferred} deferred`,
      );
    });

  const toggleWatch = () =>
    run("watch", async () => {
      if (watching) {
        await pipelineUnwatch(workspacePath);
        setWatching(false);
      } else {
        await pipelineWatch(workspacePath);
        setWatching(true);
      }
    });

  const prepareAndRun = (ticket: PipelineTicketRow) =>
    run(ticket.id, async () => {
      try {
        await pipelineLaunch(workspacePath, ticket.id);
      } catch (cause) {
        // A ticket that is already prepared has an assignment; running it is the
        // next step, so a refused launch is not fatal here.
        const message = cause instanceof Error ? cause.message : String(cause);
        if (!message.includes("already has")) throw cause;
      }
      const report = await pipelineExecute(workspacePath, ticket.id);
      setNotice(`${report.outcome}: ${report.last_words.split("\n")[0]}`);
    });

  const openSetup = () =>
    run("setup", async () => {
      const file = await loadPipelineConfig(workspacePath);
      setConfigText(
        file.text ||
          `sources:\n  - id: main\n    repository: owner/repo\n    labels: ["agent:run"]\n    allowed: ["your-login"]\nexecution:\n  prepare: pnpm install\n`,
      );
      setConfigPath(file.path);
      setSetupOpen(true);
    });

  const saveConfig = () =>
    run("save", async () => {
      await savePipelineConfig(workspacePath, configText);
      setNotice("Pipeline configuration saved");
      await refresh();
    });

  const checkGuards = () =>
    run("guards", async () => {
      const repository = status?.sources[0]?.repository;
      if (!repository) throw new Error("Add a source with a repository first");
      setGuards(await checkPipelineGuards(repository));
    });

  const halt = (ticket: PipelineTicketRow) =>
    run(`stop:${ticket.id}`, async () => {
      const stoppedSomething = await pipelineStop(ticket.id);
      setNotice(stoppedSomething ? "Stopping after this step" : "Nothing running");
    });

  if (!workspacePath) {
    return (
      <p className="p-3 text-[11px] text-obd-subtle">Open a project first.</p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-obd-line px-3 py-2">
        <p className="text-[10px] uppercase tracking-[0.16em] text-obd-subtle">
          {tickets.length} ticket{tickets.length === 1 ? "" : "s"}
          {needsPerson > 0 ? ` · ${needsPerson} need you` : ""}
          {watching ? " · watching" : ""}
        </p>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void checkNow()}
            disabled={busy !== null}
            className="rounded-md px-1.5 py-1 text-[10px] text-obd-subtle hover:bg-obd-raised hover:text-obd-body disabled:opacity-50"
          >
            {busy === "tick" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              "Check now"
            )}
          </button>
          <button
            type="button"
            onClick={() => void toggleWatch()}
            disabled={busy !== null || !status?.configured}
            className="rounded-md px-1.5 py-1 text-[10px] text-obd-subtle hover:bg-obd-raised hover:text-obd-body disabled:opacity-50"
          >
            {watching ? "Stop watching" : "Watch"}
          </button>
          <button
            type="button"
            onClick={() => void openSetup()}
            disabled={busy !== null}
            className="rounded-md px-1.5 py-1 text-[10px] text-obd-subtle hover:bg-obd-raised hover:text-obd-body disabled:opacity-50"
          >
            Setup
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            aria-label="Refresh pipeline"
            className="rounded-md p-1 text-obd-subtle hover:bg-obd-raised hover:text-obd-body"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {error !== null && (
        <p className="border-b border-obd-danger/20 bg-obd-danger/5 px-3 py-2 text-[11px] text-obd-danger">
          {error}
        </p>
      )}
      {notice !== null && error === null && (
        <p className="border-b border-obd-line px-3 py-2 text-[11px] text-obd-subtle">
          {notice}
        </p>
      )}

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-2.5">
        {status !== null && !status.configured && (
          <div className="rounded-lg border border-dashed border-obd-line p-3 text-[11px] leading-5 text-obd-subtle">
            <p className="mb-1 font-medium text-obd-body">
              This project has no pipeline
            </p>
            Add <code className="text-obd-body">.openbad/pipeline.yaml</code> to
            listen to a repository. A project without one behaves exactly as it
            did before:
            <pre className="mt-2 overflow-x-auto rounded bg-obd-raised p-2 text-[10px] text-obd-body">{`sources:
  - id: main
    repository: owner/repo
    labels: ["agent:run"]
    allowed: ["your-login"]
execution:
  prepare: pnpm install`}</pre>
          </div>
        )}

        {status !== null && status.configured && (
          <div className="flex flex-wrap gap-1">
            {status.sources.map((source) => (
              <span
                key={source.id}
                className="rounded-full bg-obd-raised px-2 py-0.5 text-[10px] text-obd-subtle"
                title={`${source.repository} · every ${source.poll_interval_secs}s`}
              >
                {source.id}
                {source.enabled ? "" : " (off)"} · {source.repository}
              </span>
            ))}
          </div>
        )}

        {tick !== null && (
          <div className="rounded-lg border border-obd-line bg-obd-raised p-2.5 text-[10px] leading-5 text-obd-subtle">
            {tick.sources.map((source) => (
              <p key={source.source_id}>
                <span className="text-obd-body">{source.source_id}</span>:{" "}
                {source.triggers} trigger{source.triggers === 1 ? "" : "s"}
                {source.dropped_own_bot > 0
                  ? `, ${source.dropped_own_bot} own`
                  : ""}
                {source.dropped_duplicate > 0
                  ? `, ${source.dropped_duplicate} seen`
                  : ""}
                {source.error !== null ? ` · ${source.error}` : ""}
              </p>
            ))}
          </div>
        )}

        {tickets.length === 0 && status?.configured && (
          <div className="rounded-lg border border-dashed border-obd-line p-3 text-[11px] leading-5 text-obd-subtle">
            <p className="mb-1 font-medium text-obd-body">No tickets yet</p>
            A labelled issue or a comment that admits work becomes a ticket the
            next time the sources are polled.
          </div>
        )}

        {setupOpen && (
          <section className="rounded-lg border border-obd-line bg-obd-raised p-2.5">
            <div className="mb-1.5 flex items-center justify-between">
              <p className="text-[10px] uppercase tracking-[0.16em] text-obd-subtle">
                .openbad/pipeline.yaml
              </p>
              <button
                type="button"
                onClick={() => setSetupOpen(false)}
                className="text-[10px] text-obd-subtle hover:text-obd-body"
              >
                Close
              </button>
            </div>
            {configPath !== "" && (
              <p className="mb-1.5 truncate text-[10px] text-obd-faint" title={configPath}>
                {configPath}
              </p>
            )}
            <textarea
              value={configText}
              onChange={(event) => setConfigText(event.target.value)}
              spellCheck={false}
              rows={12}
              className="w-full rounded-md border border-obd-line bg-obd-canvas p-2 font-mono text-[10px] leading-5 text-obd-body outline-none focus:border-obd-accent"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => void saveConfig()}
                disabled={busy !== null}
                className="rounded-md border border-obd-line px-2 py-0.5 text-[10px] text-obd-body hover:bg-obd-inset disabled:opacity-50"
              >
                {busy === "save" ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={() => void checkGuards()}
                disabled={busy !== null || !status?.sources.length}
                className="rounded-md border border-obd-line px-2 py-0.5 text-[10px] text-obd-subtle hover:bg-obd-inset hover:text-obd-body disabled:opacity-50"
              >
                {busy === "guards" ? "Checking…" : "Check branch protection"}
              </button>
            </div>
            {guards !== null && (
              <div className="mt-2 text-[10px] leading-5">
                {guards.missing.length === 0 ? (
                  <p className="text-obd-info">
                    {guards.branch} is protected as ADR-009 requires.
                  </p>
                ) : (
                  <>
                    <p className="text-obd-warning">
                      Auto-merge stays off until {guards.branch} has:
                    </p>
                    <ul className="mt-1 list-disc pl-4 text-obd-subtle">
                      {guards.missing.map((setting) => (
                        <li key={setting}>{setting}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </section>
        )}

        {groups.map((group) => {
          const rows = tickets.filter((ticket) =>
            group.stages.includes(ticket.stage),
          );
          if (rows.length === 0) return null;
          return (
            <section key={group.title}>
              <p className="mb-1.5 text-[10px] uppercase tracking-[0.16em] text-obd-subtle">
                {group.title} · {rows.length}
              </p>
              <div className="space-y-2">
                {rows.map((ticket) => (
                  <article
                    key={ticket.id}
                    className="rounded-lg border border-obd-line bg-obd-raised p-2.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-[12px] font-medium text-obd-text">
                          {threadLabel(ticket.thread)}
                        </p>
                        <p className="mt-0.5 truncate text-[10px] text-obd-subtle">
                          {ticket.source_id} ·{" "}
                          {relativeTime(ticket.updated_at)}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${
                          stageStyles[ticket.stage] ??
                          "bg-obd-raised text-obd-subtle"
                        }`}
                      >
                        {ticket.stage}
                      </span>
                    </div>

                    {ticket.summary !== null && (
                      <p className="mt-1.5 line-clamp-2 text-[11px] leading-5 text-obd-muted">
                        {ticket.summary}
                      </p>
                    )}

                    <div className="mt-2 flex items-center gap-2">
                      {ticket.pull_request !== null && (
                        <a
                          href={ticket.pull_request}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-[10px] text-obd-info hover:underline"
                        >
                          Pull request <ArrowUpRight size={11} />
                        </a>
                      )}
                      <div className="ml-auto flex items-center gap-1">
                        {ticket.stage === "working" ? (
                          <button
                            type="button"
                            onClick={() => void halt(ticket)}
                            disabled={busy !== null}
                            className="inline-flex items-center gap-1 rounded-md border border-obd-line px-1.5 py-0.5 text-[10px] text-obd-subtle hover:text-obd-body disabled:opacity-50"
                          >
                            <Square size={10} /> Stop
                          </button>
                        ) : ticket.stage === "ended" ? (
                          <span className="inline-flex items-center gap-1 text-[10px] text-obd-faint">
                            <CircleSlash size={10} /> {ticket.state}
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => void prepareAndRun(ticket)}
                            disabled={busy !== null}
                            className="inline-flex items-center gap-1 rounded-md border border-obd-line px-1.5 py-0.5 text-[10px] text-obd-body hover:bg-obd-inset disabled:opacity-50"
                          >
                            {busy === ticket.id ? (
                              <Loader2 size={10} className="animate-spin" />
                            ) : (
                              <Play size={10} />
                            )}
                            {ticket.stage === "working/queued"
                              ? "Prepare & run"
                              : "Run"}
                          </button>
                        )}
                      </div>
                    </div>

                    {ticket.stage === "person/failed" && (
                      <p className="mt-1.5 inline-flex items-center gap-1 text-[10px] text-obd-danger">
                        <TriangleAlert size={10} /> A person decides what happens
                        next
                      </p>
                    )}
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
