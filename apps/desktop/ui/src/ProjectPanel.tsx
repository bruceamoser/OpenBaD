import { useCallback, useEffect, useMemo, useState } from "react";
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
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";

import ArtifactViewer from "./ArtifactViewer";
import { KnowledgePanel, RetrievalPanel } from "./KnowledgePanels";
import {
  addProjectRepo,
  answerProjectQuestion,
  listProjectFiles,
  loadProjectManifest,
  loadProjectQuestions,
  loadProjectOverview,
  loadProjectRepos,
  loadProjectTodos,
  pickDirectory,
  removeProjectRepo,
} from "./backend";
import type {
  Artifact,
  FileCheckpointSummary,
  FoundationState,
  MemoryRecord,
  ProjectManifest,
  ProjectOverview,
  ProjectQuestion,
  ProjectRepo,
  ProjectTodo,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

export type PanelTab =
  | "overview"
  | "workspace"
  | "repos"
  | "knowledge"
  | "retrieval"
  | "memory"
  | "todos"
  | "questions"
  | "checkpoints"
  | "diagnostics"
  | "artifact";

const panelTabs: { id: PanelTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "workspace", label: "Workspace" },
  { id: "repos", label: "References" },
  { id: "knowledge", label: "Knowledge" },
  { id: "retrieval", label: "Retrieval" },
  { id: "memory", label: "Memory" },
  { id: "todos", label: "Todos" },
  { id: "questions", label: "Questions" },
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
  artifact: Artifact | null;
  onArtifact: (artifact: Artifact | null) => void;
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
    artifact,
    onArtifact,
    revision,
    onWorkspaceChanged,
  } = props;
  const workspacePath = project?.path ?? "";
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [fileQuery, setFileQuery] = useState("");
  const [repos, setRepos] = useState<ProjectRepo[]>([]);
  const [manifest, setManifest] = useState<ProjectManifest | null>(null);
  const [todos, setTodos] = useState<ProjectTodo[]>([]);
  const [questions, setQuestions] = useState<ProjectQuestion[]>([]);
  const [dataError, setDataError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (workspacePath.length === 0) {
      return;
    }
    try {
      const [
        nextOverview,
        nextRepos,
        nextFiles,
        nextManifest,
        nextTodos,
        nextQuestions,
      ] = await Promise.all([
        loadProjectOverview(workspacePath),
        loadProjectRepos(workspacePath),
        listProjectFiles(workspacePath),
        loadProjectManifest(workspacePath),
        loadProjectTodos(workspacePath),
        loadProjectQuestions(workspacePath),
      ]);
      setOverview(nextOverview);
      setRepos(nextRepos);
      setFiles(nextFiles);
      setManifest(nextManifest);
      setTodos(nextTodos);
      setQuestions(nextQuestions);
      setDataError(null);
    } catch (cause) {
      setDataError(describeError(cause));
    }
  }, [workspacePath]);

  useEffect(() => {
    setOverview(null);
    setRepos([]);
    setFiles([]);
    setManifest(null);
    setTodos([]);
    setQuestions([]);
    setFileQuery("");
    setDataError(null);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (
      tab === "overview" ||
      tab === "repos" ||
      tab === "knowledge" ||
      tab === "workspace" ||
      tab === "todos" ||
      tab === "questions"
    ) {
      void refresh();
    }
  }, [refresh, revision, tab]);

  const visibleFiles = useMemo(() => {
    const needle = fileQuery.trim().toLowerCase();
    return needle.length === 0
      ? files
      : files.filter((path) => path.toLowerCase().includes(needle));
  }, [fileQuery, files]);

  const openFile = (path: string) => {
    onArtifact({ kind: "file", title: path.split("/").at(-1) ?? path, uri: path });
    onTab("artifact");
  };

  const activeTab = panelTabs.find((item) => item.id === tab) ?? panelTabs[0];
  const title =
    tab === "artifact" && artifact !== null
      ? artifact.title
      : tab === "checkpoints"
        ? "Checkpoints"
        : tab === "diagnostics"
          ? "Diagnostics"
          : activeTab.label;

  return (
    <aside
      aria-label="Project workspace"
      className="flex min-h-0 w-full shrink-0 flex-col border-l border-white/[0.08] bg-[#0c1015]"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-white/[0.06] px-3.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-[12px] font-medium text-slate-200">{title}</p>
            <span
              aria-label="Project areas refresh automatically"
              title="Refreshes automatically"
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400/60"
            />
          </div>
          <p className="truncate text-[10px] text-slate-600">
            {project?.name ?? "No project"}
            {overview !== null &&
              ` · ${overview.repos} references · ${overview.knowledge_documents} docs`}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close project panel"
          onClick={onClose}
          className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
        >
          <X size={15} />
        </button>
      </div>

      <div className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-white/[0.06] px-2 py-1.5">
        {panelTabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onTab(item.id)}
            className={`shrink-0 rounded-md px-2 py-1 text-[10px] ${
              tab === item.id
                ? "bg-white/[0.08] text-slate-200"
                : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
            }`}
          >
            {item.label}
          </button>
        ))}
        {tab === "artifact" && (
          <span className="shrink-0 rounded-md bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-200">
            Artifact
          </span>
        )}
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
                <p className="mt-2 text-[10px] leading-4 text-slate-600">
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
          <div className="space-y-2">
            <SearchField
              value={fileQuery}
              onChange={setFileQuery}
              placeholder="Filter workspace files"
              label="Filter files"
            />
            <div className="flex items-center justify-between px-1">
              <p className="text-[10px] text-slate-600">
                {visibleFiles.length} file{visibleFiles.length === 1 ? "" : "s"} · click
                to open
              </p>
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
            {visibleFiles.map((path) => (
              <button
                key={path}
                type="button"
                onClick={() => openFile(path)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[10px] text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
              >
                <FileCode2 size={12} className="shrink-0 text-slate-600" />
                <span className="truncate">{path}</span>
              </button>
            ))}
            {visibleFiles.length === 0 && (
              <p className="px-1 py-3 text-[11px] text-slate-600">
                No files yet. Add or create a file in this workspace.
              </p>
            )}
            {(indexError !== null || indexSummary === null) && (
              <p className="px-1 text-[10px] text-slate-600">
                {indexError ?? "The workspace index is empty."}
              </p>
            )}
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
            onTab={onTab}
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
          />
        )}

        {tab === "todos" && <TodoPanel todos={todos} />}

        {tab === "questions" && (
          <QuestionsPanel
            questions={questions}
            onAnswer={async (id, answer) => {
              await answerProjectQuestion(workspacePath, id, answer);
              onWorkspaceChanged();
              void refresh();
            }}
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

        {tab === "artifact" && (
          <ArtifactViewer
            artifact={artifact}
            workspacePath={workspacePath}
            revision={revision}
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

  const remove = async (repo: ProjectRepo) => {
    try {
      await removeProjectRepo(workspacePath, repo.id);
      onRepos(repos.filter((item) => item.id !== repo.id));
    } catch (cause) {
      onError(describeError(cause));
    }
  };

  return (
    <div className="space-y-2">
      {repos.map((repo) => (
        <div
          key={repo.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5"
        >
          <div className="flex items-start gap-2">
            <FolderGit2 size={13} className="mt-0.5 shrink-0 text-emerald-300" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] text-slate-200">{repo.name}</p>
              <p className="mt-0.5 break-all font-mono text-[9px] leading-4 text-slate-600">
                {repo.path}
              </p>
            </div>
            <button
              type="button"
              aria-label={`Remove ${repo.name}`}
              onClick={() => void remove(repo)}
              className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-amber-300"
            >
              <Trash2 size={11} />
            </button>
          </div>
          <p className="mt-1.5 text-[9px] uppercase tracking-wide text-slate-700">
            {repo.read_only ? "read-only reference" : "editable"}
          </p>
        </div>
      ))}
      {repos.length === 0 && (
        <p className="px-1 py-2 text-[11px] leading-4 text-slate-600">
          Reference folders are read-only sources the assistant can inspect
          alongside the project workspace.
        </p>
      )}
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
}: {
  memories: MemoryRecord[];
  memoryError: string | null;
  onForgetMemory: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      {memories.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-600">No saved memories yet.</p>
      )}
      {memories.map((memory) => (
        <div
          key={memory.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3"
        >
          <p className="text-[11px] leading-5 text-slate-300">
            {memory.pinned && <span className="mr-1 text-emerald-300">★</span>}
            {memory.content}
          </p>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="truncate font-mono text-[9px] text-slate-600">
              {memory.scope} · {memory.author}
              {memory.origin_turn !== null ? ` · turn ${memory.origin_turn}` : ""} ·{" "}
              {memory.tags.length > 0 ? memory.tags.join(", ") : "untagged"}
            </span>
            <button
              type="button"
              onClick={() => onForgetMemory(memory.id)}
              className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-amber-300"
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

function QuestionsPanel({
  questions,
  onAnswer,
}: {
  questions: ProjectQuestion[];
  onAnswer: (id: string, answer: string) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (id: string, answer: string) => {
    if (answer.trim().length === 0) {
      return;
    }
    setBusy(id);
    try {
      await onAnswer(id, answer.trim());
      setDrafts((current) => ({ ...current, [id]: "" }));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  };

  if (questions.length === 0) {
    return (
      <p className="px-1 py-2 text-[11px] text-slate-600">
        No questions yet. The assistant can queue a decision with ask_question.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {questions.map((question) => (
        <div
          key={question.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <p className="text-[11px] leading-5 text-slate-200">{question.question}</p>
            <span
              className={
                question.answer === null
                  ? "shrink-0 rounded bg-amber-400/15 px-1 py-0.5 text-[9px] text-amber-300"
                  : "shrink-0 rounded bg-emerald-400/15 px-1 py-0.5 text-[9px] text-emerald-300"
              }
            >
              {question.answer === null ? "pending" : "answered"}
            </span>
          </div>
          {question.context !== null && (
            <p className="mt-1 text-[10px] text-slate-500">{question.context}</p>
          )}
          {question.answer !== null ? (
            <p className="mt-2 text-[11px] text-emerald-300">{question.answer}</p>
          ) : (
            <div className="mt-2 space-y-1.5">
              {question.options.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {question.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      disabled={busy === question.id}
                      onClick={() => void submit(question.id, option)}
                      className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-200 hover:bg-white/[0.08] disabled:opacity-40"
                    >
                      {option}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex gap-1.5">
                <input
                  value={drafts[question.id] ?? ""}
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      [question.id]: event.target.value,
                    }))
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      void submit(question.id, drafts[question.id] ?? "");
                    }
                  }}
                  aria-label={`Answer ${question.question}`}
                  placeholder="Type an answer…"
                  className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
                />
                <button
                  type="button"
                  disabled={busy === question.id}
                  onClick={() => void submit(question.id, drafts[question.id] ?? "")}
                  className="rounded-md bg-emerald-400 px-2 py-1.5 text-[10px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-40"
                >
                  Answer
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
      {error !== null && <p className="text-[10px] text-amber-300">{error}</p>}
    </div>
  );
}

function TodoPanel({ todos }: { todos: ProjectTodo[] }) {
  if (todos.length === 0) {
    return (
      <div className="space-y-2 px-1 py-3">
        <p className="text-[11px] text-slate-500">No project todos yet.</p>
        <p className="text-[10px] leading-4 text-slate-600">
          The assistant writes durable work state with{" "}
          <code className="text-slate-400">todo_write</code>; it persists in{" "}
          <code className="text-slate-400">.openbad/todos.json</code>.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {todos.map((todo) => (
        <div
          key={todo.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5"
        >
          <div className="flex items-start gap-2">
            <span
              className={`mt-0.5 rounded px-1 py-0.5 text-[8px] uppercase tracking-wide ${
                todo.state === "done"
                  ? "bg-emerald-400/10 text-emerald-300"
                  : todo.state === "doing"
                    ? "bg-sky-400/10 text-sky-300"
                    : todo.state === "blocked"
                      ? "bg-amber-400/10 text-amber-300"
                      : todo.state === "dropped"
                        ? "bg-slate-400/10 text-slate-500"
                        : "bg-white/[0.06] text-slate-400"
              }`}
            >
              {todo.state}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[11px] leading-5 text-slate-300">{todo.text}</p>
              {todo.done_when !== null && (
                <p className="mt-0.5 text-[9px] leading-4 text-slate-600">
                  Done when: {todo.done_when}
                </p>
              )}
              {todo.blocked_by.length > 0 && (
                <p className="mt-0.5 text-[9px] leading-4 text-amber-300/70">
                  Blocked by: {todo.blocked_by.join(", ")}
                </p>
              )}
            </div>
          </div>
        </div>
      ))}
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
        <p className="px-1 py-2 text-[11px] text-slate-600">
          Start a session to see file checkpoints.
        </p>
      )}
      {sessionId !== null && checkpoints.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-600">No checkpoints yet.</p>
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
            <span className="text-[9px] text-slate-600">
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
          <span className="ml-auto text-[10px] text-slate-600">
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
      <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-wide text-slate-600">
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
      <Search size={11} className="shrink-0 text-slate-600" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className="min-w-0 flex-1 bg-transparent text-[10px] text-slate-300 outline-none placeholder:text-slate-600"
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

