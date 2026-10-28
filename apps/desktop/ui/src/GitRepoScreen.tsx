import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  CircleAlert,
  CircleCheck,
  FileCode2,
  FolderGit2,
  GitBranch,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import {
  gitCheckout,
  gitCommit,
  gitDiff,
  gitFetch,
  gitInit,
  gitPull,
  gitPush,
  gitRepoStatus,
  gitStage,
  gitUnstage,
} from "./backend";
import type {
  Artifact,
  GitCommandResult,
  GitFileChange,
  GitRepoStatus,
} from "./types";

export type GitRepoScreenProps = {
  workspacePath: string;
  repoPath: string;
  repoName: string;
  onArtifact: (artifact: Artifact | null) => void;
  onWorkspaceChanged: () => void;
};

export default function GitRepoScreen({
  workspacePath,
  repoPath,
  repoName,
  onArtifact,
  onWorkspaceChanged,
}: GitRepoScreenProps) {
  const [status, setStatus] = useState<GitRepoStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diffPath, setDiffPath] = useState<string | null>(null);
  const [branchChoice, setBranchChoice] = useState("");
  const [newBranch, setNewBranch] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [stageAll, setStageAll] = useState(false);
  const request = useRef(0);

  useEffect(() => {
    setStatus(null);
    setNotice(null);
    setError(null);
    setDiffPath(null);
    setBranchChoice("");
    setNewBranch("");
    setCommitMessage("");
    setStageAll(false);
  }, [workspacePath, repoPath]);

  const refresh = useCallback(
    async (quiet = false) => {
      if (workspacePath.length === 0 || repoPath.length === 0) {
        setStatus(null);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      if (quiet) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      const sequence = request.current + 1;
      request.current = sequence;
      try {
        const next = await gitRepoStatus(workspacePath, repoPath);
        if (request.current !== sequence) {
          return;
        }
        setStatus(next);
        setError(next.error);
      } catch (cause) {
        if (request.current === sequence) {
          setStatus(null);
          setError(errorMessage(cause));
        }
      } finally {
        if (request.current === sequence) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [workspacePath, repoPath],
  );

  useEffect(() => {
    void refresh(false);
  }, [refresh]);

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") {
        void refresh(true);
      }
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refresh]);

  const applyResult = useCallback((result: GitCommandResult): boolean => {
    if (result.status !== null) {
      setStatus(result.status);
    }
    if (result.ok) {
      setNotice(result.message.trim() || "Done.");
      setError(null);
      return true;
    }
    setNotice(null);
    setError(result.message.trim() || "The git command failed.");
    return false;
  }, []);

  const run = useCallback(
    async (
      key: string,
      action: () => Promise<GitCommandResult>,
    ): Promise<boolean> => {
      request.current += 1;
      setLoading(false);
      setRefreshing(false);
      setBusy(key);
      setNotice(null);
      setError(null);
      try {
        const applied = applyResult(await action());
        if (applied) {
          onWorkspaceChanged();
        }
        return applied;
      } catch (cause) {
        setError(errorMessage(cause));
        return false;
      } finally {
        setBusy(null);
      }
    },
    [applyResult, onWorkspaceChanged],
  );

  const initializeRepo = async () => {
    await run("init", () => gitInit(workspacePath, repoPath));
  };

  const currentBranch = status?.branch ?? "";
  const localBranches = useMemo(
    () => status?.branches.filter((branch) => !branch.remote) ?? [],
    [status],
  );
  const checkoutTarget =
    branchChoice.length > 0
      ? branchChoice
      : localBranches.some((branch) => branch.name === currentBranch)
        ? currentBranch
        : "";

  const checkoutBranch = async () => {
    if (checkoutTarget.length === 0 || checkoutTarget === currentBranch) {
      return;
    }
    if (
      await run("checkout", () =>
        gitCheckout(workspacePath, repoPath, checkoutTarget),
      )
    ) {
      setBranchChoice("");
    }
  };

  const createBranch = async () => {
    const name = newBranch.trim();
    if (name.length === 0) {
      return;
    }
    if (
      await run("create-branch", () =>
        gitCheckout(workspacePath, repoPath, name, true),
      )
    ) {
      setNewBranch("");
      setBranchChoice("");
    }
  };

  const fetchRemote = async () => {
    await run("fetch", () => gitFetch(workspacePath, repoPath));
  };

  const pullRemote = async () => {
    await run("pull", () => gitPull(workspacePath, repoPath, "ff-only"));
  };

  const pushBranch = async () => {
    if (status === null || status.branch === null) {
      return;
    }
    const remote =
      status.remotes.find((item) => item.name === "origin")?.name ??
      status.remotes[0]?.name;
    const missingUpstream = status.upstream === null;
    const input = missingUpstream
      ? { remote, branch: status.branch, setUpstream: true }
      : { setUpstream: false };
    await run("push", () => gitPush(workspacePath, repoPath, input));
  };

  const stagePaths = async (paths: string[]) => {
    if (paths.length === 0) {
      return;
    }
    await run("stage", () => gitStage(workspacePath, repoPath, paths));
  };

  const unstagePaths = async (paths: string[]) => {
    if (paths.length === 0) {
      return;
    }
    await run("unstage", () => gitUnstage(workspacePath, repoPath, paths));
  };

  const commitChanges = async () => {
    const message = commitMessage.trim();
    if (message.length === 0) {
      return;
    }
    if (
      await run("commit", () =>
        gitCommit(workspacePath, repoPath, message, stageAll),
      )
    ) {
      setCommitMessage("");
      setStageAll(false);
    }
  };

  const openDiff = async (file: GitFileChange, staged: boolean) => {
    const key = `${staged ? "staged" : "worktree"}:${file.path}`;
    setDiffPath(key);
    setError(null);
    try {
      const diff = await gitDiff(workspacePath, repoPath, file.path, staged);
      const content =
        diff.trim().length > 0
          ? diff
          : file.untracked
            ? `Untracked file: ${file.path}\n\nStage this file to include it in the next commit.`
            : `No ${staged ? "staged " : ""}changes for ${file.path}.`;
      onArtifact({
        kind: "diff",
        title: `${file.path}${staged ? " (staged)" : ""}`,
        content,
      });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setDiffPath(null);
    }
  };

  const files = status?.files ?? [];
  const stagedFiles = files.filter((file) => file.staged);
  const changedFiles = files.filter((file) => file.unstaged || file.untracked);
  const isBusy = busy !== null || loading;
  const hasUpstream = status !== null && status.upstream !== null;
  const hasRemotes = status !== null && status.remotes.length > 0;
  const operation = status?.operation ?? null;
  const commitBlocked = operation !== null && operation !== "merge";
  const canCommit =
    commitMessage.trim().length > 0 && (stagedFiles.length > 0 || stageAll);

  if (workspacePath.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-obd-line p-3 text-[11px] leading-5 text-obd-subtle">
        Open a project to inspect repositories and source control.
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="rounded-lg border border-obd-line bg-obd-raised p-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <FolderGit2 size={12} className="shrink-0 text-obd-accent" />
              <p className="truncate text-[11px] font-medium text-obd-text">
                {repoName}
              </p>
            </div>
            <p className="mt-0.5 break-all font-mono text-[9px] leading-4 text-obd-subtle">
              {repoPath}
            </p>
          </div>
          <button
            type="button"
            aria-label="Refresh repository status"
            title="Refresh repository status"
            onClick={() => void refresh(true)}
            disabled={loading || busy !== null}
            className="shrink-0 rounded-md p-1 text-obd-subtle hover:bg-obd-raised hover:text-obd-body disabled:opacity-40"
          >
            <RefreshCw
              size={13}
              className={loading || refreshing ? "animate-spin" : ""}
            />
          </button>
        </div>
      </div>

      {error !== null && (
        <div className="flex items-start gap-1.5 rounded-lg border border-obd-warning/20 bg-obd-warning/[0.06] px-2.5 py-2 text-[10px] leading-4 text-obd-warning">
          <CircleAlert size={12} className="mt-0.5 shrink-0" />
          <p className="min-w-0 flex-1 break-words">{error}</p>
        </div>
      )}

      {notice !== null && (
        <div className="flex items-start gap-1.5 rounded-lg border border-obd-accent/20 bg-obd-accent/[0.06] px-2.5 py-2 text-[10px] leading-4 text-obd-accent">
          <CircleCheck size={12} className="mt-0.5 shrink-0" />
          <pre className="max-h-24 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-words font-mono text-[9px] leading-4">
            {notice}
          </pre>
        </div>
      )}

      {status === null && (
        <div className="rounded-lg border border-obd-line bg-obd-raised px-3 py-4 text-center">
          {loading || refreshing || error === null ? (
            <div className="flex items-center justify-center gap-2 text-[10px] text-obd-subtle">
              <Loader2 size={13} className="animate-spin" /> Reading repository…
            </div>
          ) : (
            <>
              <CircleAlert size={18} className="mx-auto text-obd-warning" />
              <p className="mt-2 text-[11px] font-medium text-obd-text">
                Repository status unavailable
              </p>
              <p className="mt-1 text-[10px] leading-4 text-obd-subtle">
                Check that the folder exists and that Git is installed and on
                PATH.
              </p>
              <button
                type="button"
                onClick={() => void refresh(false)}
                disabled={loading}
                className="mt-2 rounded-md border border-obd-line bg-obd-raised px-2.5 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
              >
                Retry
              </button>
            </>
          )}
        </div>
      )}

      {status !== null && !status.isRepo && (
        <div className="rounded-lg border border-dashed border-obd-line bg-obd-raised px-3 py-4 text-center">
          <FolderGit2 size={20} className="mx-auto text-obd-subtle" />
          <p className="mt-2 text-[11px] font-medium text-obd-text">
            Not a Git repository
          </p>
          <p className="mt-1 break-all font-mono text-[9px] leading-4 text-obd-subtle">
            {status.path}
          </p>
          <p className="mt-2 text-[10px] leading-4 text-obd-subtle">
            Initialize Git to track changes, stage files, and commit from
            OpenBaD.
          </p>
          {status.error !== null && (
            <p className="mt-1 text-[10px] text-obd-warning">{status.error}</p>
          )}
          <button
            type="button"
            onClick={() => void initializeRepo()}
            disabled={isBusy}
            className="mt-2.5 inline-flex items-center gap-1.5 rounded-md bg-obd-accent px-2.5 py-1.5 text-[10px] font-medium text-obd-on-accent hover:bg-obd-accent disabled:opacity-40"
          >
            {busy === "init" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <GitBranch size={12} />
            )}
            {busy === "init" ? "Initializing…" : "Initialize repository"}
          </button>
        </div>
      )}
      {status !== null && status.isRepo && (
        <div className="space-y-2.5">
          <div className="rounded-xl border border-obd-line bg-obd-raised p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <GitBranch size={13} className="shrink-0 text-obd-accent" />
                  <p className="truncate text-[12px] font-medium text-obd-text">
                    {currentBranch.length > 0 ? currentBranch : "No branch"}
                  </p>
                  {status.detached && (
                    <span className="shrink-0 rounded bg-obd-warning/15 px-1 py-0.5 text-[8px] uppercase tracking-wide text-obd-warning">
                      detached
                    </span>
                  )}
                  {operation !== null && (
                    <span className="shrink-0 rounded bg-obd-warning/15 px-1 py-0.5 text-[8px] uppercase tracking-wide text-obd-warning">
                      {operation}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate font-mono text-[9px] text-obd-subtle">
                  {hasUpstream
                    ? `tracking ${status.upstream}`
                    : "no upstream configured"}
                </p>
              </div>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[9px]">
              <span
                title={`${status.ahead} commits ahead of upstream`}
                className="flex items-center gap-0.5 rounded bg-obd-raised px-1.5 py-0.5 text-obd-muted"
              >
                <ArrowUp size={10} /> {status.ahead} ahead
              </span>
              <span
                title={`${status.behind} commits behind upstream`}
                className="flex items-center gap-0.5 rounded bg-obd-raised px-1.5 py-0.5 text-obd-muted"
              >
                <ArrowDown size={10} /> {status.behind} behind
              </span>
              {!status.hasCommits && (
                <span className="rounded bg-obd-raised px-1.5 py-0.5 text-obd-subtle">
                  no commits yet
                </span>
              )}
              <span className="truncate text-obd-subtle">
                {status.remotes.length > 0
                  ? status.remotes.map((remote) => remote.name).join(", ")
                  : "no remotes"}
              </span>
            </div>

            <div className="mt-2 grid grid-cols-3 gap-1.5">
              <button
                type="button"
                onClick={() => void fetchRemote()}
                disabled={isBusy || !hasRemotes}
                title={
                  hasRemotes ? "Fetch from remotes" : "No remotes configured"
                }
                className="flex items-center justify-center gap-1 rounded-md border border-obd-line bg-obd-raised px-1.5 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
              >
                <RefreshCw
                  size={11}
                  className={busy === "fetch" ? "animate-spin" : ""}
                />
                {busy === "fetch" ? "Fetching…" : "Fetch"}
              </button>
              <button
                type="button"
                onClick={() => void pullRemote()}
                disabled={
                  isBusy || !hasUpstream || !hasRemotes || operation !== null
                }
                title={
                  !hasUpstream
                    ? "Configure an upstream before pulling"
                    : operation !== null
                      ? "Finish the in-progress operation first"
                      : "Pull with fast-forward only"
                }
                className="flex items-center justify-center gap-1 rounded-md border border-obd-line bg-obd-raised px-1.5 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
              >
                <ArrowDown size={11} />
                {busy === "pull" ? "Pulling…" : "Pull"}
              </button>
              <button
                type="button"
                onClick={() => void pushBranch()}
                disabled={
                  isBusy ||
                  !hasRemotes ||
                  status.branch === null ||
                  status.detached ||
                  operation !== null
                }
                title={
                  !hasRemotes
                    ? "No remotes configured"
                    : status.detached
                      ? "Check out a branch before pushing"
                      : operation !== null
                        ? "Finish the in-progress operation first"
                        : hasUpstream
                          ? "Push to upstream"
                          : "Push and set upstream"
                }
                className="flex items-center justify-center gap-1 rounded-md border border-obd-line bg-obd-raised px-1.5 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
              >
                <ArrowUp size={11} />
                {busy === "push" ? "Pushing…" : "Push"}
              </button>
            </div>

            <div className="mt-2.5 border-t border-obd-line pt-2.5">
              <label
                htmlFor="git-checkout-branch"
                className="text-[9px] uppercase tracking-wide text-obd-subtle"
              >
                Checkout local branch
              </label>
              <div className="mt-1 flex items-center gap-1.5">
                <select
                  id="git-checkout-branch"
                  value={checkoutTarget}
                  onChange={(event) => setBranchChoice(event.target.value)}
                  disabled={
                    isBusy || operation !== null || localBranches.length === 0
                  }
                  className="min-w-0 flex-1 rounded-md border border-obd-line bg-obd-inset px-2 py-1.5 text-[10px] text-obd-body outline-none focus:border-obd-accent/40 disabled:opacity-40"
                >
                  <option value="" className="bg-obd-surface">
                    Select a branch…
                  </option>
                  {localBranches.map((branch) => (
                    <option
                      key={branch.name}
                      value={branch.name}
                      className="bg-obd-surface"
                    >
                      {branch.name}
                      {branch.current ? " (current)" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => void checkoutBranch()}
                  disabled={
                    isBusy ||
                    checkoutTarget.length === 0 ||
                    checkoutTarget === status.branch ||
                    operation !== null
                  }
                  className="shrink-0 rounded-md border border-obd-line bg-obd-raised px-2 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
                >
                  Checkout
                </button>
              </div>
            </div>

            <div className="mt-2">
              <label
                htmlFor="git-new-branch"
                className="text-[9px] uppercase tracking-wide text-obd-subtle"
              >
                Create branch
              </label>
              <div className="mt-1 flex items-center gap-1.5">
                <input
                  id="git-new-branch"
                  value={newBranch}
                  onChange={(event) => setNewBranch(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void createBranch();
                    }
                  }}
                  placeholder="feature/my-branch"
                  disabled={isBusy || operation !== null}
                  className="min-w-0 flex-1 rounded-md border border-obd-line bg-obd-inset px-2 py-1.5 font-mono text-[10px] text-obd-body outline-none placeholder:text-obd-faint focus:border-obd-accent/40 disabled:opacity-40"
                />
                <button
                  type="button"
                  onClick={() => void createBranch()}
                  disabled={
                    isBusy ||
                    newBranch.trim().length === 0 ||
                    operation !== null
                  }
                  className="flex shrink-0 items-center gap-1 rounded-md border border-obd-line bg-obd-raised px-2 py-1.5 text-[10px] text-obd-body hover:bg-obd-raised-strong disabled:opacity-40"
                >
                  {busy === "create-branch" ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <Plus size={11} />
                  )}
                  Create
                </button>
              </div>
            </div>
          </div>

          {operation !== null && (
            <div className="flex items-start gap-2 rounded-lg border border-obd-warning/20 bg-obd-warning/[0.06] px-2.5 py-2 text-[10px] leading-4 text-obd-warning">
              <TriangleAlert size={13} className="mt-0.5 shrink-0" />
              <div>
                <p className="font-medium">
                  {operationLabel(operation)} in progress
                </p>
                <p className="mt-0.5 text-obd-warning/80">
                  {operationHelp(operation)}
                </p>
              </div>
            </div>
          )}

          {operation === null && status.ahead > 0 && status.behind > 0 && (
            <div className="flex items-start gap-2 rounded-lg border border-obd-warning/20 bg-obd-warning/[0.06] px-2.5 py-2 text-[10px] leading-4 text-obd-warning">
              <TriangleAlert size={13} className="mt-0.5 shrink-0" />
              <div>
                <p className="font-medium">Branches have diverged</p>
                <p className="mt-0.5 text-obd-warning/80">
                  Local and upstream branches have diverged. A fast-forward
                  pull will fail; merge or rebase from a terminal, then push.
                </p>
              </div>
            </div>
          )}
          <ChangeSection
            title="Staged changes"
            files={stagedFiles}
            stagedView
            emptyLabel="No staged changes."
            busy={isBusy}
            diffPath={diffPath}
            onOpenDiff={(file, staged) => void openDiff(file, staged)}
            onToggle={(file) => void unstagePaths([file.path])}
            onToggleAll={() =>
              void unstagePaths(stagedFiles.map((file) => file.path))
            }
          />
          <ChangeSection
            title="Changes"
            files={changedFiles}
            stagedView={false}
            emptyLabel="No unstaged or untracked changes."
            busy={isBusy}
            diffPath={diffPath}
            onOpenDiff={(file, staged) => void openDiff(file, staged)}
            onToggle={(file) => void stagePaths([file.path])}
            onToggleAll={() =>
              void stagePaths(changedFiles.map((file) => file.path))
            }
          />

          <section className="rounded-lg border border-obd-line bg-obd-raised p-3">
            <label
              htmlFor="git-commit-message"
              className="flex items-center gap-1.5 text-[11px] font-medium text-obd-body"
            >
              <Check size={12} className="text-obd-accent" /> Commit message
            </label>
            <textarea
              id="git-commit-message"
              value={commitMessage}
              onChange={(event) => setCommitMessage(event.target.value)}
              rows={3}
              placeholder="Describe your changes"
              disabled={isBusy || commitBlocked}
              className="mt-1.5 w-full resize-y rounded-md border border-obd-line bg-obd-inset px-2 py-1.5 text-[10px] leading-5 text-obd-body outline-none placeholder:text-obd-faint focus:border-obd-accent/40 disabled:opacity-40"
            />
            <label className="mt-2 flex items-center gap-1.5 text-[10px] text-obd-muted">
              <input
                type="checkbox"
                checked={stageAll}
                onChange={(event) => setStageAll(event.target.checked)}
                disabled={isBusy || commitBlocked}
                className="h-3 w-3 accent-obd-accent"
              />
              Stage all changes before committing
            </label>
            <button
              type="button"
              onClick={() => void commitChanges()}
              disabled={!canCommit || isBusy || commitBlocked}
              className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md bg-obd-accent px-2.5 py-2 text-[10px] font-medium text-obd-on-accent hover:bg-obd-accent disabled:opacity-40"
            >
              {busy === "commit" ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Check size={12} />
              )}
              {busy === "commit" ? "Committing…" : "Commit"}
            </button>
            {stagedFiles.length === 0 && !stageAll && (
              <p className="mt-2 text-[9px] leading-4 text-obd-subtle">
                No staged changes. Stage files above or enable “Stage all
                changes”.
              </p>
            )}
            {commitBlocked && (
              <p className="mt-2 text-[9px] leading-4 text-obd-warning">
                Finish or abort the in-progress {operationLabel(operation ?? "")}{" "}
                from a terminal before committing.
              </p>
            )}
          </section>

          {status.lastCommit !== null && (
            <section className="rounded-lg border border-obd-line bg-obd-raised p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[9px] uppercase tracking-wide text-obd-subtle">
                  Last commit
                </p>
                <span className="shrink-0 font-mono text-[9px] text-obd-subtle">
                  {status.lastCommit.shortHash}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-5 text-obd-body">
                {status.lastCommit.subject}
              </p>
              <p className="mt-1 text-[9px] text-obd-subtle">
                {status.lastCommit.author} ·{" "}
                {formatCommitDate(status.lastCommit.date)}
              </p>
              {status.lastCommit.refs.trim().length > 0 && (
                <p className="mt-1 truncate font-mono text-[9px] text-obd-accent/80">
                  {status.lastCommit.refs}
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function ChangeSection({
  title,
  files,
  stagedView,
  emptyLabel,
  busy,
  diffPath,
  onOpenDiff,
  onToggle,
  onToggleAll,
}: {
  title: string;
  files: GitFileChange[];
  stagedView: boolean;
  emptyLabel: string;
  busy: boolean;
  diffPath: string | null;
  onOpenDiff: (file: GitFileChange, staged: boolean) => void;
  onToggle: (file: GitFileChange) => void;
  onToggleAll: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-obd-line bg-obd-raised">
      <div className="flex items-center justify-between gap-2 border-b border-obd-line px-2.5 py-1.5">
        <p className="text-[10px] uppercase tracking-[0.12em] text-obd-subtle">
          {title}
          <span className="ml-1.5 rounded bg-obd-raised-strong px-1 py-0.5 text-[9px] tracking-normal text-obd-muted">
            {files.length}
          </span>
        </p>
        <button
          type="button"
          onClick={onToggleAll}
          disabled={busy || files.length === 0}
          className="rounded px-1.5 py-0.5 text-[9px] text-obd-subtle hover:bg-obd-raised hover:text-obd-body disabled:opacity-40"
        >
          {stagedView ? "Unstage all" : "Stage all"}
        </button>
      </div>
      {files.length === 0 ? (
        <p className="px-2.5 py-2 text-[10px] text-obd-subtle">{emptyLabel}</p>
      ) : (
        <ul className="divide-y divide-obd-line">
          {files.map((file) => {
            const key = `${stagedView ? "staged" : "worktree"}:${file.path}`;
            return (
              <li key={key} className="flex items-center gap-1 pr-1">
                <button
                  type="button"
                  onClick={() => onOpenDiff(file, stagedView)}
                  title={
                    file.oldPath !== null
                      ? `${file.oldPath} → ${file.path}`
                      : file.path
                  }
                  aria-label={`Show diff for ${file.path}`}
                  className="flex min-w-0 flex-1 items-center gap-1.5 px-2.5 py-1.5 text-left font-mono text-[10px] text-obd-muted hover:text-obd-text"
                >
                  {diffPath === key ? (
                    <Loader2
                      size={11}
                      className="shrink-0 animate-spin text-obd-accent"
                    />
                  ) : (
                    <FileCode2 size={11} className="shrink-0 text-obd-subtle" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{file.path}</span>
                  <span
                    className={`shrink-0 text-[9px] font-semibold ${changeTone(
                      file,
                      stagedView,
                    )}`}
                  >
                    {changeLetter(file, stagedView)}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onToggle(file)}
                  disabled={busy}
                  aria-label={`${stagedView ? "Unstage" : "Stage"} ${file.path}`}
                  title={stagedView ? "Unstage file" : "Stage file"}
                  className="rounded p-1 text-obd-subtle hover:bg-obd-raised hover:text-obd-text disabled:opacity-40"
                >
                  {stagedView ? <Minus size={11} /> : <Plus size={11} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function changeLetter(file: GitFileChange, staged: boolean): string {
  if (!staged && file.untracked) {
    return "U";
  }
  const status = (staged ? file.indexStatus : file.worktreeStatus).trim();
  return status.length > 0 ? status : "M";
}

function changeTone(file: GitFileChange, staged: boolean): string {
  if (!staged && file.untracked) {
    return "text-obd-accent";
  }
  const status = (staged ? file.indexStatus : file.worktreeStatus).trim();
  if (status === "D") {
    return "text-obd-danger";
  }
  if (status === "A" || status === "?") {
    return "text-obd-accent";
  }
  return staged ? "text-obd-info" : "text-obd-warning";
}

function errorMessage(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  if (typeof cause === "string") {
    return cause;
  }
  return "The git request failed.";
}

function formatCommitDate(value: string): string {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return value;
  }
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function operationLabel(operation: string): string {
  return operation.charAt(0).toUpperCase() + operation.slice(1);
}

function operationHelp(operation: string): string {
  switch (operation) {
    case "merge":
      return "Resolve conflicts, stage the results, then commit to finish the merge.";
    case "rebase":
      return "Resolve conflicts, then continue or abort the rebase from a terminal.";
    case "cherry-pick":
      return "Resolve conflicts, then continue or abort the cherry-pick from a terminal.";
    case "revert":
      return "Resolve conflicts, then continue or abort the revert from a terminal.";
    default:
      return "Finish or abort the in-progress operation before switching branches or pulling.";
  }
}
