import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  BookOpen,
  Database,
  FileCode2,
  FolderGit2,
  FolderPlus,
  Gauge,
  KeyRound,
  Layers,
  Loader2,
  PackageOpen,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import AgentsPanel from "./AgentsPanel";
import { KnowledgePanel, RetrievalPanel } from "./KnowledgePanels";
import RevealButton from "./RevealButton";
import { droppedToUploads, fileName, useFileDropZone } from "./dropzone";
import {
  addProjectRepo,
  importReferenceFiles,
  importWorkspaceFiles,
  listProjectFiles,
  loadProjectManifest,
  loadProjectOverview,
  loadProjectRepos,
  pickDirectory,
  pickFiles,
  removeProjectRepo,
  setProjectRepoReadOnly,
} from "./backend";
import type {
  Artifact,
  FileCheckpointSummary,
  FoundationState,
  MemoryRecord,
  ProjectManifest,
  ProjectOverview,
  ProjectRepo,
  UploadReport,
  UploadSource,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

export type PanelTab =
  | "overview"
  | "workspace"
  | "agents"
  | "repos"
  | "knowledge"
  | "retrieval"
  | "memory"
  | "checkpoints"
  | "diagnostics";

const panelTabs: { id: PanelTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "workspace", label: "Workspace" },
  { id: "agents", label: "Agents" },
  { id: "repos", label: "References" },
  { id: "knowledge", label: "Knowledge" },
  { id: "retrieval", label: "Retrieval" },
  { id: "memory", label: "Memory" },
  { id: "checkpoints", label: "Edits" },
  { id: "diagnostics", label: "Diag" },
];

export type ProjectPanelProps = {
  tab: PanelTab;
  onTab: (tab: PanelTab) => void;
  onClose: () => void;
  project: WorkspaceSummary | null;
  indexSummary: WorkspaceIndexSummary | null;
  indexing: boolean;
  indexError: string | null;
  onRefreshIndex: () => void;
  memories: MemoryRecord[];
  memoryError: string | null;
  onForgetMemory: (id: string) => void;
  checkpoints: FileCheckpointSummary[];
  restoringCheckpoint: string | null;
  checkpointDiffId: string | null;
  checkpointDiffText: string;
  checkpointNotice: string | null;
  checkpointError: string | null;
  onShowDiff: (id: string) => void;
  onRestore: (id: string) => void;
  foundation: FoundationState;
  apiKeyStatus: boolean;
  sessionId: string | null;
  onOpenSettings: (
    tab: "model" | "access" | "secrets" | "project" | "about",
  ) => void;
  onArtifact: (artifact: Artifact | null) => void;
  onImportBundle: () => void;
  revision: number;
  onWorkspaceChanged: () => void;
};

export default function ProjectPanel(props: ProjectPanelProps) {
  const {
    tab,
    onTab,
    onClose,
    project,
    indexSummary,
    indexing,
    indexError,
    onRefreshIndex,
    memories,
    memoryError,
    onForgetMemory,
    checkpoints,
    restoringCheckpoint,
    checkpointDiffId,
    checkpointDiffText,
    checkpointNotice,
    checkpointError,
    onShowDiff,
    onRestore,
    foundation,
    apiKeyStatus,
    sessionId,
    onOpenSettings,
    onArtifact,
    onImportBundle,
    revision,
    onWorkspaceChanged,
  } = props;
  const workspacePath = project?.path ?? "";
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [fileQuery, setFileQuery] = useState("");
  const [repos, setRepos] = useState<ProjectRepo[]>([]);
  const [manifest, setManifest] = useState<ProjectManifest | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  const [workspaceNotice, setWorkspaceNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const activeTabRef = useRef<HTMLButtonElement | null>(null);
  const didMountRef = useRef(false);

  const refresh = useCallback(async () => {
    if (workspacePath.length === 0) {
      return;
    }
    try {
      const [nextOverview, nextRepos, nextFiles, nextManifest] =
        await Promise.all([
          loadProjectOverview(workspacePath),
          loadProjectRepos(workspacePath),
          listProjectFiles(workspacePath),
          loadProjectManifest(workspacePath),
        ]);
      setOverview(nextOverview);
      setRepos(nextRepos);
      setFiles(nextFiles);
      setManifest(nextManifest);
      setDataError(null);
    } catch (cause) {
      setDataError(describeError(cause));
    }
  }, [workspacePath]);

  const uploadWorkspaceFiles = useCallback(
    async (sources: UploadSource[]) => {
      if (workspacePath.length === 0 || sources.length === 0) {
        return;
      }
      setUploading(true);
      try {
        const report = await importWorkspaceFiles(workspacePath, sources);
        setFiles(await listProjectFiles(workspacePath));
        setWorkspaceNotice(describeUpload(report));
        setDataError(report.skipped.length === 0 ? null : report.skipped.join(" · "));
        onRefreshIndex();
        onWorkspaceChanged();
      } catch (cause) {
        setDataError(describeError(cause));
      } finally {
        setUploading(false);
      }
    },
    [workspacePath, onRefreshIndex, onWorkspaceChanged],
  );

  const chooseWorkspaceFiles = useCallback(async () => {
    try {
      const chosen = await pickFiles("Upload documents to the workspace");
      await uploadWorkspaceFiles(
        chosen.map((path) => ({ name: fileName(path), path })),
      );
    } catch (cause) {
      setDataError(describeError(cause));
    }
  }, [uploadWorkspaceFiles]);

  const workspaceDrop = useFileDropZone(
    async (payload) => uploadWorkspaceFiles(await droppedToUploads(payload)),
    { disabled: workspacePath.length === 0 },
  );

  useEffect(() => {
    setOverview(null);
    setRepos([]);
    setFiles([]);
    setManifest(null);
    setFileQuery("");
    setDataError(null);
    setWorkspaceNotice(null);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true;
      return;
    }
    if (
      tab === "overview" ||
      tab === "repos" ||
      tab === "knowledge" ||
      tab === "workspace"
    ) {
      void refresh();
    }
  }, [refresh, revision, tab]);

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState !== "visible") {
        return;
      }
      void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refresh]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      activeTabRef.current?.scrollIntoView({
        block: "nearest",
        inline: "nearest",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [tab]);

  const visibleFiles = useMemo(() => {
    const needle = fileQuery.trim().toLowerCase();
    return needle.length === 0
      ? files
      : files.filter((path) => path.toLowerCase().includes(needle));
  }, [fileQuery, files]);

  const openFile = (path: string) => {
    onArtifact({ kind: "file", title: path.split("/").at(-1) ?? path, uri: path });
  };

  const activeTab = panelTabs.find((item) => item.id === tab) ?? panelTabs[0];
  const title =
    tab === "checkpoints"
      ? "Checkpoints"
      : tab === "diagnostics"
        ? "Diagnostics"
        : activeTab.label;

  return (
    <aside
      aria-label="Project workspace"
      className="flex min-h-0 w-full shrink-0 flex-col border-l border-white/[0.08] bg-[#1b232e]"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-white/[0.06] px-3.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-[12px] font-medium text-slate-200">{title}</p>
            <span
              aria-label="Refreshes when the window regains focus and after actions"
              title="Refreshes on focus and after actions"
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-500/50"
            />
          </div>
          <p className="truncate text-[10px] text-slate-500">
            {project?.name ?? "No project"}
            {overview !== null &&
              ` · ${overview.repos} references · ${overview.knowledge_documents} docs`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label="Import project bundle"
            title="Merge an OpenBaD project bundle into this project"
            onClick={onImportBundle}
            className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-emerald-300"
          >
            <PackageOpen size={14} />
          </button>
          <button
            type="button"
            aria-label="Close project panel"
            onClick={onClose}
            className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
          >
            <X size={15} />
          </button>
        </div>
      </div>

      <div className="panel-tab-strip">
        <div className="panel-tab-row" role="tablist" aria-label="Project areas">
          {panelTabs.map((item) => (
            <button
              key={item.id}
              ref={tab === item.id ? activeTabRef : undefined}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              onClick={() => onTab(item.id)}
              className={`flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[10px] ${
                tab === item.id
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {dataError !== null && (
        <p className="shrink-0 border-b border-white/[0.06] px-3 py-2 text-[10px] leading-4 text-amber-300">
          {dataError}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "overview" && (
          <div className="space-y-3">
            <div className="rounded-xl border border-white/[0.07] bg-gradient-to-br from-emerald-400/[0.07] to-transparent p-3">
              <p className="text-[11px] font-medium text-slate-200">
                {project?.name ?? "Project"}
              </p>
              <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-500">
                {project?.path}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Stat
                label="References"
                value={overview?.repos ?? repos.length}
                icon={<FolderGit2 size={12} />}
              />
              <Stat
                label="Knowledge"
                value={overview?.knowledge_documents ?? 0}
                icon={<BookOpen size={12} />}
              />
              <Stat
                label="Memories"
                value={overview?.memories ?? memories.length}
                icon={<Layers size={12} />}
              />
              <Stat
                label="Indexed files"
                value={indexSummary?.files ?? overview?.index.files ?? 0}
                icon={<Database size={12} />}
              />
            </div>
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2 text-[11px] text-slate-300">
                <BookOpen size={13} className="text-sky-300" /> Project manifest
              </div>
              {manifest === null ? (
                <p className="mt-2 text-[10px] leading-4 text-slate-500">
                  Add <code className="text-slate-400">.openbad/project.yaml</code> to
                  declare named tasks, verification recipes, validators, scope, and
                  invariants.
                </p>
              ) : (
                <div className="mt-2 space-y-1 text-[10px] leading-4 text-slate-500">
                  <p className="text-[11px] text-slate-300">
                    {manifest.name ?? "Unnamed project"}
                  </p>
                  {manifest.description !== null &&
                    manifest.description !== undefined && <p>{manifest.description}</p>}
                  <p>
                    {Object.keys(manifest.commands ?? {}).length} tasks ·{" "}
                    {Object.keys(manifest.recipes ?? {}).length} recipes ·{" "}
                    {Object.keys(manifest.gates ?? {}).length} gates ·{" "}
                    {(manifest.validators ?? []).length} validators ·{" "}
                    {(manifest.invariants ?? []).length} invariants
                    {manifest.prove_it ? " · prove-it" : ""}
                  </p>
                </div>
              )}
            </div>
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2 text-[11px] text-slate-300">
                <Gauge size={13} className="text-emerald-300" /> Project context
              </div>
              <p className="mt-2 text-[11px] leading-5 text-slate-500">
                Chat owns the left side. Workspace, reference folders, knowledge,
                retrieval, and memory stay scoped to this project and open here
                without competing with the conversation.
              </p>
              <button
                type="button"
                onClick={onRefreshIndex}
                disabled={indexing}
                className="mt-3 flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[10px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40"
              >
                {indexing ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <RefreshCw size={11} />
                )}
                {indexing ? "Indexing…" : "Refresh code index"}
              </button>
            </div>
          </div>
        )}

        {tab === "workspace" && (
          <div
            {...workspaceDrop.zoneProps}
            className={`relative min-h-full space-y-2 rounded-lg transition-shadow ${
              workspaceDrop.active ? "ring-2 ring-emerald-400/40" : ""
            }`}
          >
            {workspaceDrop.active && (
              <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-emerald-400/10 text-[11px] font-medium text-emerald-100">
                Drop documents to add them to the workspace
              </div>
            )}
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => void chooseWorkspaceFiles()}
                disabled={uploading || workspacePath.length === 0}
                className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-dashed border-white/[0.12] bg-white/[0.02] px-2.5 py-2 text-[10px] text-slate-300 hover:border-emerald-400/30 hover:bg-emerald-400/[0.05] hover:text-emerald-200 disabled:opacity-40"
              >
                {uploading ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Upload size={12} />
                )}
                Upload documents…
              </button>
              <button
                type="button"
                onClick={onRefreshIndex}
                disabled={indexing}
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] text-slate-500 hover:bg-white/5 hover:text-slate-300 disabled:opacity-40"
              >
                {indexing ? (
                  <Loader2 size={10} className="animate-spin" />
                ) : (
                  <RefreshCw size={10} />
                )}
                Refresh
              </button>
            </div>
            {workspaceNotice !== null && (
              <p className="px-1 text-[10px] text-emerald-300">{workspaceNotice}</p>
            )}
            <SearchField
              value={fileQuery}
              onChange={setFileQuery}
              placeholder="Filter workspace files"
              label="Filter files"
            />
            <p className="px-1 text-[10px] text-slate-500">
              {visibleFiles.length} file{visibleFiles.length === 1 ? "" : "s"} · click
              to open · drop documents to upload
            </p>
            {visibleFiles.map((path) => (
              <div
                key={path}
                className="flex items-center gap-1 rounded-md pr-1 hover:bg-white/[0.04]"
              >
                <button
                  type="button"
                  onClick={() => openFile(path)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left font-mono text-[10px] text-slate-400 hover:text-slate-200"
                >
                  <FileCode2 size={12} className="shrink-0 text-slate-500" />
                  <span className="truncate">{path}</span>
                </button>
                <RevealButton workspacePath={workspacePath} path={path} />
              </div>
            ))}
            {visibleFiles.length === 0 && (
              <p className="px-1 py-3 text-[11px] text-slate-500">
                No files yet. Add or create a file in this workspace.
              </p>
            )}
            {(indexError !== null || indexSummary === null) && (
              <p className="px-1 text-[10px] text-slate-500">
                {indexError ?? "The workspace index is empty."}
              </p>
            )}
          </div>
        )}

        {tab === "agents" && (
          <div className="flex min-h-[320px] flex-1 flex-col">
            <AgentsPanel
              workspacePath={workspacePath}
              revision={revision}
              onArtifact={onArtifact}
            />
          </div>
        )}

        {tab === "repos" && (
          <ReferencesPanel
            workspacePath={workspacePath}
            repos={repos}
            onRepos={setRepos}
            onRefresh={() => {
              void refresh();
              onWorkspaceChanged();
            }}
            onError={setDataError}
          />
        )}

        {tab === "knowledge" && (
          <KnowledgePanel
            workspacePath={workspacePath}
            onOpenArtifact={onArtifact}
            revision={revision}
            onChanged={onWorkspaceChanged}
          />
        )}

        {tab === "retrieval" && (
          <RetrievalPanel
            workspacePath={workspacePath}
            onOpenArtifact={onArtifact}
            onTab={onTab}
          />
        )}

        {tab === "memory" && (
          <MemoryPanel
            memories={memories}
            memoryError={memoryError}
            onForgetMemory={onForgetMemory}
            onOpen={(memory) =>
              onArtifact({
                kind: "memory",
                title:
                  memory.content.length > 48
                    ? `${memory.content.slice(0, 48)}…`
                    : memory.content,
                content: memory.content,
              })
            }
          />
        )}

        {tab === "checkpoints" && (
          <CheckpointsPanel
            sessionId={sessionId}
            checkpoints={checkpoints}
            restoringCheckpoint={restoringCheckpoint}
            checkpointDiffId={checkpointDiffId}
            checkpointDiffText={checkpointDiffText}
            checkpointNotice={checkpointNotice}
            checkpointError={checkpointError}
            onShowDiff={onShowDiff}
            onRestore={onRestore}
          />
        )}

        {tab === "diagnostics" && (
          <DiagnosticsPanel
            foundation={foundation}
            apiKeyStatus={apiKeyStatus}
            onOpenSettings={onOpenSettings}
          />
        )}
      </div>
    </aside>
  );
}


function ReferencesPanel({
  workspacePath,
  repos,
  onRepos,
  onRefresh,
  onError,
}: {
  workspacePath: string;
  repos: ProjectRepo[];
  onRepos: (repos: ProjectRepo[]) => void;
  onRefresh: () => void;
  onError: (message: string | null) => void;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [permissionBusy, setPermissionBusy] = useState<string | null>(null);

  const add = async () => {
    try {
      const selected = await pickDirectory("Add reference folder");
      if (selected === null) {
        return;
      }
      const repo = await addProjectRepo(workspacePath, selected);
      onRepos([...repos, repo]);
      setNotice(`Added reference ${repo.name}`);
      onError(null);
      onRefresh();
    } catch (cause) {
      onError(describeError(cause));
    }
  };

  const upload = async (sources: UploadSource[]) => {
    if (sources.length === 0) {
      return;
    }
    setUploading(true);
    try {
      const { repo, report } = await importReferenceFiles(workspacePath, sources);
      onRepos(
        repos.some((item) => item.id === repo.id)
          ? repos.map((item) => (item.id === repo.id ? repo : item))
          : [...repos, repo],
      );
      setNotice(`${describeUpload(report)} · ${repo.name}`);
      onError(report.skipped.length === 0 ? null : report.skipped.join(" · "));
      onRefresh();
    } catch (cause) {
      onError(describeError(cause));
    } finally {
      setUploading(false);
    }
  };

  const choose = async () => {
    try {
      const chosen = await pickFiles("Upload documents to references");
      await upload(chosen.map((path) => ({ name: fileName(path), path })));
    } catch (cause) {
      onError(describeError(cause));
    }
  };

  const drop = useFileDropZone(async (payload) => upload(await droppedToUploads(payload)));

  const remove = async (repo: ProjectRepo) => {
    try {
      await removeProjectRepo(workspacePath, repo.id);
      onRepos(repos.filter((item) => item.id !== repo.id));
    } catch (cause) {
      onError(describeError(cause));
    }
  };

  const toggleEditable = async (repo: ProjectRepo) => {
    setPermissionBusy(repo.id);
    onError(null);
    try {
      const readOnly = !repo.read_only;
      await setProjectRepoReadOnly(repo.id, readOnly);
      onRepos(
        repos.map((item) =>
          item.id === repo.id ? { ...item, read_only: readOnly } : item,
        ),
      );
      setNotice(
        readOnly
          ? `${repo.name} is read-only again.`
          : `${repo.name} is editable by the assistant.`,
      );
    } catch (cause) {
      onError(describeError(cause));
    } finally {
      setPermissionBusy(null);
    }
  };

  return (
    <div
      {...drop.zoneProps}
      className={`relative min-h-full space-y-2 rounded-lg transition-shadow ${
        drop.active ? "ring-2 ring-emerald-400/40" : ""
      }`}
    >
      {drop.active && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-emerald-400/10 text-[11px] font-medium text-emerald-100">
          Drop documents to add them to references
        </div>
      )}
      {repos.map((repo) => (
        <div
          key={repo.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5"
        >
          <div className="flex items-start gap-2">
            <FolderGit2 size={13} className="mt-0.5 shrink-0 text-emerald-300" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] text-slate-200">{repo.name}</p>
              <p className="mt-0.5 break-all font-mono text-[9px] leading-4 text-slate-500">
                {repo.path}
              </p>
            </div>
            <RevealButton
              workspacePath={workspacePath}
              path={repo.path}
              label="Show reference folder"
              className="p-1 hover:bg-white/5"
            />
            <button
              type="button"
              aria-label={`Remove ${repo.name}`}
              onClick={() => void remove(repo)}
              className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-amber-300"
            >
              <Trash2 size={11} />
            </button>
          </div>
          <button
            type="button"
            aria-pressed={repo.read_only}
            disabled={permissionBusy === repo.id}
            onClick={() => void toggleEditable(repo)}
            title={
              repo.read_only
                ? "Allow the assistant to modify files in this reference"
                : "Make this reference read-only again"
            }
            className={`mt-1.5 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wide transition-colors ${
              repo.read_only
                ? "border-white/10 text-slate-500 hover:border-emerald-400/30 hover:text-emerald-200"
                : "border-emerald-400/25 bg-emerald-400/[0.06] text-emerald-200"
            } disabled:opacity-40`}
          >
            {permissionBusy === repo.id && <Loader2 size={9} className="animate-spin" />}
            {repo.read_only ? "read-only · make editable" : "editable · make read-only"}
          </button>
        </div>
      ))}
      {repos.length === 0 && (
        <p className="px-1 py-2 text-[11px] leading-4 text-slate-500">
          Reference folders are project sources the assistant can inspect. Make
          one editable when you want the assistant to change its files.
        </p>
      )}
      <button
        type="button"
        onClick={() => void choose()}
        disabled={uploading}
        className="flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-white/[0.12] bg-white/[0.02] px-2.5 py-2 text-[10px] text-slate-300 hover:border-emerald-400/30 hover:bg-emerald-400/[0.05] hover:text-emerald-200 disabled:opacity-40"
      >
        {uploading ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <Upload size={12} />
        )}
        Upload documents…
      </button>
      <button
        type="button"
        onClick={() => void add()}
        className="flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-white/[0.12] bg-white/[0.02] px-2.5 py-2 text-[10px] text-slate-300 hover:border-emerald-400/30 hover:bg-emerald-400/[0.05] hover:text-emerald-200"
      >
        <FolderPlus size={12} /> Add reference folder…
      </button>
      {notice !== null && <p className="text-[10px] text-emerald-300">{notice}</p>}
    </div>
  );
}

function MemoryPanel({
  memories,
  memoryError,
  onForgetMemory,
  onOpen,
}: {
  memories: MemoryRecord[];
  memoryError: string | null;
  onForgetMemory: (id: string) => void;
  onOpen: (memory: MemoryRecord) => void;
}) {
  return (
    <div className="space-y-2">
      <p className="px-1 pb-1 text-[10px] leading-4 text-slate-500">
        Memory snippets: short durable preferences, gotchas, constraints, and decisions.
        They are full-text indexed and injected when relevant. Use Knowledge for
        documents.
      </p>
      {memories.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-500">No saved memories yet.</p>
      )}
      {memories.map((memory) => (
        <div
          key={memory.id}
          role="button"
          tabIndex={0}
          aria-label="Preview memory"
          onClick={() => onOpen(memory)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onOpen(memory);
            }
          }}
          className="cursor-pointer rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 hover:border-emerald-400/20 hover:bg-white/[0.04]"
        >
          <p className="text-[11px] leading-5 text-slate-300">
            {memory.pinned && <span className="mr-1 text-emerald-300">★</span>}
            {memory.content}
          </p>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[9px] text-slate-500">
              {memory.scope} · {memory.author}
              {memory.origin_turn !== null ? ` · turn ${memory.origin_turn}` : ""} ·{" "}
              {memory.tags.length > 0 ? memory.tags.join(", ") : "untagged"}
            </span>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onForgetMemory(memory.id);
              }}
              className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-amber-300"
              aria-label="Forget memory"
            >
              <Trash2 size={11} />
            </button>
          </div>
        </div>
      ))}
      {memoryError !== null && <p className="text-[10px] text-amber-300">{memoryError}</p>}
    </div>
  );
}

function CheckpointsPanel({
  sessionId,
  checkpoints,
  restoringCheckpoint,
  checkpointDiffId,
  checkpointDiffText,
  checkpointNotice,
  checkpointError,
  onShowDiff,
  onRestore,
}: {
  sessionId: string | null;
  checkpoints: FileCheckpointSummary[];
  restoringCheckpoint: string | null;
  checkpointDiffId: string | null;
  checkpointDiffText: string;
  checkpointNotice: string | null;
  checkpointError: string | null;
  onShowDiff: (id: string) => void;
  onRestore: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      {sessionId === null && (
        <p className="px-1 py-2 text-[11px] text-slate-500">
          Start a session to see file checkpoints.
        </p>
      )}
      {sessionId !== null && checkpoints.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-500">No checkpoints yet.</p>
      )}
      {checkpoints.map((checkpoint) => (
        <div
          key={checkpoint.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3"
        >
          <p className="truncate font-mono text-[10px] text-slate-400">
            {checkpoint.path}
          </p>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="text-[9px] text-slate-500">
              {formatBytes(checkpoint.bytes)} · {formatClock(checkpoint.created_at)}
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => onShowDiff(checkpoint.id)}
                className="rounded border border-white/[0.08] px-2 py-1 text-[10px] text-slate-400 hover:bg-white/5"
              >
                {checkpointDiffId === checkpoint.id ? "Hide" : "Diff"}
              </button>
              <button
                type="button"
                onClick={() => onRestore(checkpoint.id)}
                disabled={restoringCheckpoint !== null}
                className="rounded border border-white/[0.08] px-2 py-1 text-[10px] text-slate-400 hover:bg-white/5 disabled:opacity-40"
              >
                {restoringCheckpoint === checkpoint.id ? "Restoring…" : "Restore"}
              </button>
            </div>
          </div>
          {checkpointDiffId === checkpoint.id && checkpointDiffText.length > 0 && (
            <pre className="mt-2 max-h-56 overflow-auto rounded bg-black/30 p-2 font-mono text-[9px] leading-4 text-slate-500">
              {checkpointDiffText}
            </pre>
          )}
        </div>
      ))}
      {checkpointNotice !== null && (
        <p className="text-[10px] text-emerald-300">{checkpointNotice}</p>
      )}
      {checkpointError !== null && (
        <p className="text-[10px] text-amber-300">{checkpointError}</p>
      )}
    </div>
  );
}

function DiagnosticsPanel({
  foundation,
  apiKeyStatus,
  onOpenSettings,
}: {
  foundation: FoundationState;
  apiKeyStatus: boolean;
  onOpenSettings: (
    tab: "model" | "access" | "secrets" | "project" | "about",
  ) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
        <div className="flex items-center gap-2 text-[11px] text-slate-300">
          <KeyRound
            size={13}
            className={apiKeyStatus ? "text-emerald-300" : "text-amber-300"}
          />
          DeepSeek key
          <span className="ml-auto text-[10px] text-slate-500">
            {apiKeyStatus ? "ready" : "missing"}
          </span>
        </div>
        <button
          type="button"
          onClick={() => onOpenSettings("secrets")}
          className="mt-2 text-[10px] text-emerald-300 hover:underline"
        >
          Manage key
        </button>
      </div>
      {foundation.kind === "ready" && (
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 font-mono text-[9px] leading-5 text-slate-400">
          <p>schema {foundation.foundation.doctor.schema_version}</p>
          <p>integrity {foundation.foundation.doctor.integrity}</p>
          <p>database {formatBytes(foundation.foundation.doctor.database_bytes)}</p>
          <p>events {foundation.foundation.doctor.event_count.toLocaleString()}</p>
          <p>
            audit {foundation.foundation.audit.valid ? "verified" : "failed"} ·{" "}
            {foundation.foundation.audit.records} records
          </p>
        </div>
      )}
      {foundation.kind === "error" && (
        <p className="text-[10px] text-amber-300">{foundation.message}</p>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5">
      <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-wide text-slate-500">
        {icon}
        {label}
      </div>
      <p className="mt-1 text-[16px] font-medium text-slate-200">
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function SearchField({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <label className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-1.5">
      <Search size={11} className="shrink-0 text-slate-500" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className="min-w-0 flex-1 bg-transparent text-[10px] text-slate-300 outline-none placeholder:text-slate-500"
      />
    </label>
  );
}

function describeError(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  if (typeof cause === "string") {
    return cause;
  }
  return "The project area request failed.";
}

function describeUpload(report: UploadReport): string {
  const uploaded = report.files.length;
  const skipped = report.skipped.length;
  const partial = skipped === 0 ? "" : `, ${skipped} skipped`;
  return `Uploaded ${uploaded} file${uploaded === 1 ? "" : "s"}${partial}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) {
    return `${bytes} B`;
  }
  if (bytes < 1_048_576) {
    return `${(bytes / 1_024).toFixed(1)} KB`;
  }
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function formatClock(value: number): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

