import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  CircleAlert,
  FolderGit2,
  GitBranch,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { gitProjectRepos } from "./backend";
import type {
  GitRepoSummary,
  ProjectRepo,
  WorkspaceSummary,
} from "./types";

export default function GitRepoList({
  project,
  repos,
  revision,
  onOpen,
}: {
  project: WorkspaceSummary | null;
  repos: ProjectRepo[];
  revision: number;
  onOpen: (repoPath: string, repoName: string) => void;
}) {
  const workspacePath = project?.path ?? "";
  const repoKey = useMemo(
    () => repos.map((repo) => repo.path).join("\u0000"),
    [repos],
  );
  const repoMeta = useMemo(
    () => new Map(repos.map((repo) => [repo.path, repo])),
    [repos],
  );
  const [summaries, setSummaries] = useState<GitRepoSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const loadedKey = useRef<string | null>(null);

  const load = useCallback(
    async (quiet: boolean) => {
      if (workspacePath.length === 0) {
        setSummaries([]);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const paths = repoKey.length === 0 ? [] : repoKey.split("\u0000");
      const sequence = request.current + 1;
      request.current = sequence;
      if (quiet) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      try {
        const next = await gitProjectRepos(workspacePath, paths);
        if (request.current !== sequence) {
          return;
        }
        setSummaries(next);
        setError(null);
      } catch (cause) {
        if (request.current === sequence) {
          setError(describeError(cause));
        }
      } finally {
        if (request.current === sequence) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [repoKey, workspacePath],
  );

  useEffect(() => {
    setSummaries(null);
    setError(null);
  }, [workspacePath]);

  useEffect(() => {
    const key = `${workspacePath}\u0000${repoKey}`;
    const initial = loadedKey.current !== key;
    loadedKey.current = key;
    void load(!initial);
  }, [load, repoKey, revision, workspacePath]);

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") {
        void load(true);
      }
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [load]);

  const visible = useMemo(
    () =>
      (summaries ?? []).filter(
        (summary) => summary.isRepo || summary.path === workspacePath,
      ),
    [summaries, workspacePath],
  );
  const skipped = useMemo(
    () =>
      (summaries ?? []).filter(
        (summary) => !summary.isRepo && summary.path !== workspacePath,
      ).length,
    [summaries, workspacePath],
  );

  if (workspacePath.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-white/10 p-3 text-[11px] leading-5 text-slate-500">
        Open a project to inspect its source control repositories.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 px-1">
        <div className="min-w-0">
          <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-500">
            Repositories
          </p>
          <p className="mt-0.5 truncate text-[9px] text-slate-500">
            {project?.name ?? "Workspace"} · workspace and Git-backed references
          </p>
        </div>
        <button
          type="button"
          aria-label="Refresh source control repositories"
          title="Refresh source control repositories"
          onClick={() => void load(true)}
          disabled={loading}
          className="shrink-0 rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300 disabled:opacity-40"
        >
          <RefreshCw
            size={12}
            className={loading || refreshing ? "animate-spin" : ""}
          />
        </button>
      </div>

      {error !== null && (
        <div className="flex items-start gap-1.5 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-2.5 py-2 text-[10px] leading-4 text-amber-200">
          <CircleAlert size={12} className="mt-0.5 shrink-0" />
          <p className="min-w-0 flex-1 break-words">{error}</p>
        </div>
      )}

      {summaries === null && (
        <div className="flex items-center justify-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-4 text-[10px] text-slate-500">
          {error === null ? (
            <>
              <Loader2 size={13} className="animate-spin" /> Reading repositories…
            </>
          ) : (
            "Repositories unavailable."
          )}
        </div>
      )}

      {visible.map((summary) => {
        const isWorkspace = summary.path === workspacePath;
        const meta = repoMeta.get(summary.path);
        return (
          <button
            key={summary.path}
            type="button"
            onClick={() => onOpen(summary.path, summary.name)}
            className="w-full rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5 text-left transition-colors hover:border-emerald-400/25 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400/60"
          >
            <div className="flex items-start gap-2">
              <FolderGit2
                size={13}
                className="mt-0.5 shrink-0 text-emerald-300"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <p className="truncate text-[11px] text-slate-200">
                    {summary.name}
                  </p>
                  <span className="shrink-0 rounded bg-white/[0.05] px-1 py-0.5 text-[8px] uppercase tracking-wide text-slate-500">
                    {isWorkspace ? "workspace" : "reference"}
                    {meta?.read_only === true ? " · read-only" : ""}
                  </span>
                </div>
                <p className="mt-0.5 break-all font-mono text-[9px] leading-4 text-slate-500">
                  {summary.path}
                </p>
                {summary.isRepo ? (
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[9px] text-slate-500">
                    <span className="flex items-center gap-1 text-slate-400">
                      <GitBranch size={10} className="shrink-0" />
                      <span className="max-w-[140px] truncate">
                        {summary.detached
                          ? "detached"
                          : (summary.branch ?? "no branch")}
                      </span>
                    </span>
                    <span
                      className={
                        summary.dirty > 0
                          ? "font-medium text-amber-300"
                          : "text-emerald-300/80"
                      }
                    >
                      {summary.dirty > 0
                        ? `${summary.dirty} changed`
                        : "clean"}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <ArrowUp size={9} /> {summary.ahead}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <ArrowDown size={9} /> {summary.behind}
                    </span>
                    {summary.upstream !== null && (
                      <span className="max-w-[160px] truncate font-mono text-slate-500">
                        {summary.upstream}
                      </span>
                    )}
                  </div>
                ) : (
                  <p className="mt-1.5 text-[9px] text-amber-300">
                    {summary.error ??
                      "Not a Git repository · open to initialize"}
                  </p>
                )}
              </div>
              <ChevronRight
                size={12}
                className="mt-1 shrink-0 text-slate-600"
              />
            </div>
          </button>
        );
      })}

      {summaries !== null && visible.length === 0 && (
        <p className="px-1 py-2 text-[11px] leading-4 text-slate-500">
          No source control repositories are available for this project.
        </p>
      )}

      {skipped > 0 && (
        <p className="px-1 text-[9px] leading-4 text-slate-500">
          {skipped} reference{skipped === 1 ? "" : "s"} not shown: not a Git
          repository or unavailable.
        </p>
      )}

      <p className="px-1 text-[9px] leading-4 text-slate-500">
        Select a repository to open its Git screen in the preview pane.
      </p>
    </div>
  );
}

function describeError(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  if (typeof cause === "string") {
    return cause;
  }
  return "The repository list could not be loaded.";
}
