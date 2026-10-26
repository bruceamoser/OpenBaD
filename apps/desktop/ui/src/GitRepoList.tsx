import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  FolderGit2,
  GitBranch,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { gitProjectRepos, loadProjectRepos } from "./backend";
import type { GitRepoSummary, ProjectRepo } from "./types";

/**
 * Source Control entry in the left project tree.
 *
 * Lists the project workspace plus every Git-backed reference, and opens the
 * selected repository in the preview pane. The right project panel no longer
 * mounts the Git screen, so this is the only Source Control entry point.
 */
export default function GitRepoList({
  workspacePath,
  projectName,
  revision,
  onOpen,
}: {
  workspacePath: string;
  projectName: string;
  revision: number;
  onOpen: (repoPath: string, repoName: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [summaries, setSummaries] = useState<GitRepoSummary[] | null>(null);
  const [repoMeta, setRepoMeta] = useState<ReadonlyMap<string, ProjectRepo>>(
    () => new Map(),
  );
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const loadedFor = useRef<string | null>(null);

  const load = useCallback(
    async (quiet: boolean) => {
      if (workspacePath.length === 0) {
        setSummaries([]);
        setRepoMeta(new Map());
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const sequence = request.current + 1;
      request.current = sequence;
      if (quiet) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      try {
        const references = await loadProjectRepos(workspacePath);
        if (request.current !== sequence) {
          return;
        }
        setRepoMeta(new Map(references.map((repo) => [repo.path, repo])));
        const next = await gitProjectRepos(
          workspacePath,
          references.map((repo) => repo.path),
        );
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
    [workspacePath],
  );

  useEffect(() => {
    request.current += 1;
    setSummaries(null);
    setRepoMeta(new Map());
    setError(null);
    setLoading(false);
    setRefreshing(false);
    loadedFor.current = null;
  }, [workspacePath]);

  useEffect(() => {
    if (collapsed || workspacePath.length === 0) {
      return;
    }
    const quiet = loadedFor.current === workspacePath;
    loadedFor.current = workspacePath;
    void load(quiet);
  }, [collapsed, load, revision, workspacePath]);

  useEffect(() => {
    const onFocus = () => {
      if (collapsed || workspacePath.length === 0) {
        return;
      }
      if (document.visibilityState !== "visible") {
        return;
      }
      void load(loadedFor.current === workspacePath);
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [collapsed, load, workspacePath]);

  const visible = useMemo(
    () =>
      (summaries ?? []).filter(
        (summary) => summary.isRepo || summary.path === workspacePath,
      ),
    [summaries, workspacePath],
  );
  const skipped = (summaries ?? []).length - visible.length;
  const busy = loading || refreshing;

  return (
    <div className="mt-0.5">
      <div className="flex items-center gap-0.5">
        <button
          type="button"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[11px] text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          <GitBranch size={12} />
          <span className="truncate">Source Control</span>
          {summaries !== null && visible.length > 0 && (
            <span className="ml-auto font-mono text-[9px] text-slate-500">
              {visible.length}
            </span>
          )}
        </button>
        {!collapsed && (
          <button
            type="button"
            aria-label="Refresh source control repositories"
            title="Refresh source control repositories"
            onClick={() => void load(true)}
            disabled={loading}
            className="shrink-0 rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300 disabled:opacity-40"
          >
            <RefreshCw size={11} className={busy ? "animate-spin" : ""} />
          </button>
        )}
      </div>

      {!collapsed && (
        <div id="source-control-repos" className="mt-0.5 space-y-0.5">
          {error !== null && (
            <div className="mx-1 flex items-start gap-1.5 rounded-md border border-amber-400/20 bg-amber-400/[0.06] px-2 py-1.5 text-[10px] leading-4 text-amber-200">
              <CircleAlert size={11} className="mt-0.5 shrink-0" />
              <p className="min-w-0 flex-1 break-words">{error}</p>
            </div>
          )}

          {summaries === null && error === null && (
            <div className="flex items-center gap-1.5 px-2 py-1 text-[10px] text-slate-500">
              <Loader2 size={11} className="animate-spin" /> Reading repositories…
            </div>
          )}

          {visible.map((summary) => {
            const isWorkspace = summary.path === workspacePath;
            const meta = repoMeta.get(summary.path);
            return (
              <button
                key={summary.path}
                type="button"
                title={summary.path}
                onClick={() => onOpen(summary.path, summary.name)}
                className="flex w-full items-start gap-1.5 rounded-md px-2 py-1 text-left hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400/60"
              >
                <FolderGit2
                  size={11}
                  className="mt-0.5 shrink-0 text-emerald-300/80"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1">
                    <span className="truncate text-[11px] text-slate-300">
                      {summary.name || (isWorkspace ? projectName : "repository")}
                    </span>
                    <span className="shrink-0 rounded bg-white/[0.05] px-1 text-[8px] uppercase tracking-wide text-slate-500">
                      {isWorkspace ? "workspace" : "reference"}
                    </span>
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[9px]">
                    <span className="flex items-center gap-1 text-slate-500">
                      <GitBranch size={9} className="shrink-0" />
                      <span className="max-w-[110px] truncate">
                        {summary.detached
                          ? "detached"
                          : (summary.branch ?? "no branch")}
                      </span>
                    </span>
                    {summary.error !== null ? (
                      <span className="text-amber-300" title={summary.error}>
                        {summary.isRepo ? "status unavailable" : "not a repository"}
                      </span>
                    ) : summary.isRepo ? (
                      <span
                        className={
                          summary.dirty > 0
                            ? "font-medium text-amber-300"
                            : "text-emerald-300/80"
                        }
                      >
                        {summary.dirty > 0
                          ? `${summary.dirty} dirty`
                          : "clean"}
                      </span>
                    ) : (
                      <span className="text-amber-300">not a repository</span>
                    )}
                    {meta?.read_only === true && (
                      <span className="text-slate-600">read-only</span>
                    )}
                  </span>
                </span>
              </button>
            );
          })}

          {summaries !== null && visible.length === 0 && error === null && (
            <p className="px-2 py-1 text-[10px] leading-4 text-slate-500">
              No Git repositories yet.
            </p>
          )}

          {skipped > 0 && (
            <p className="px-2 py-0.5 text-[9px] leading-4 text-slate-500">
              {skipped} reference{skipped === 1 ? "" : "s"} hidden: not a Git
              repository.
            </p>
          )}
        </div>
      )}
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
  return "The source control list could not be loaded.";
}
