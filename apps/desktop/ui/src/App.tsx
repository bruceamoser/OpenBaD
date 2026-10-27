import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import {
  Archive,
  ArchiveRestore,
  ArrowDown,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  PackageOpen,
  Copy,
  Database,
  Eye,
  FileText,
  Folder,
  FolderGit2,
  FolderOpen,
  Gauge,
  KeyRound,
  Loader2,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Search,
  Shield,
  Trash2,
  Wrench,
  X,
} from "lucide-react";

import {
  addProject as addStoredProject,
  archiveSession as archiveStoredSession,
  deleteSession as deleteStoredSession,
  loadArchivedSessions as fetchArchivedSessions,
  apiKeyStatus as fetchApiKeyStatus,
  createProject as createStoredProject,
  cancelChat,
  chat as sendChat,
  checkpointDiff as fetchCheckpointDiff,
  clearApiKey as clearStoredApiKey,
  forgetMemory as forgetStoredMemory,
  grantOutsideAccess as grantFilesystemAccess,
  loadOutsideAccessGrants as fetchAccessGrants,
  revokeOutsideAccess as revokeFilesystemAccess,
  listProjects as fetchProjects,
  loadCheckpoints as fetchCheckpoints,
  exportProject as exportProjectBundle,
  importProject as importProjectBundle,
  importProjectInto as importProjectIntoBundle,
  loadProjectAreaCounts,
  loadFoundation as fetchFoundation,
  loadMemories as fetchMemories,
  loadSession as fetchSession,
  loadSessions as fetchSessions,
  refreshWorkspaceIndex as refreshIndex,
  removeProject as removeStoredProject,
  renameProject as renameStoredProject,
  renameSession as renameStoredSession,
  restoreCheckpoint as restoreStoredCheckpoint,
  saveApiKey as persistApiKey,
  quitApp,
  pickDirectory,
  pickImportBundle,
  pickSaveFile,
  searchSessions as searchStoredSessions,
  setSessionAccessMode as persistSessionAccessMode,
  steerChat as steerRunningTurn,
  workspaceDefault,
  workspacesDirectory as fetchWorkspacesDirectory,
  workspaceIndexStatus as fetchIndexStatus,
} from "./backend";
import AppMenuBar from "./AppMenuBar";
import type { AppMenu } from "./AppMenuBar";
import ChatComposer from "./ChatComposer";
import type { ChatComposerHandle } from "./ChatComposer";
import MarkdownMessage from "./MarkdownMessage";
import McpSettings from "./McpSettings";
import ServicesSettings from "./ServicesSettings";
import UpdatePanel from "./UpdatePanel";
import { droppedToUploads, useFileDropZone } from "./dropzone";
import GitRepoList from "./GitRepoList";
import ProjectPanel from "./ProjectPanel";
import PreviewPane from "./PreviewPane";
import RevealButton from "./RevealButton";
import type { PanelTab } from "./ProjectPanel";
import type {
  AccessMode,
  AgentEvent,
  Artifact,
  ChatEntry,
  FileCheckpointSummary,
  FoundationState,
  MemoryRecord,
  OutsideAccessGrant,
  OutsideAccessRequest,
  PreviewTarget,
  ProjectAreaCounts,
  ProjectExportReport,
  ProjectImportReport,
  SessionSummary,
  SessionView,
  ToolApprovalRequest,
  UploadSource,
  Usage,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

// Artifact previews and repository source control share the preview pane.
export type { PreviewTarget };

type Preferences = {
  model: string;
  thinking: boolean;
  reasoningEffort: string;
};

type PermissionMode = AccessMode;
type SettingsTab = "model" | "access" | "secrets" | "mcp" | "project" | "services" | "about";

function countSuffix(primary?: number, total?: number): string {
  if (primary === undefined) {
    return "";
  }
  return total === undefined || total === primary ? ` (${primary})` : ` (${primary}/${total})`;
}

const defaultPreferences: Preferences = {
  model: "deepseek-flash",
  thinking: true,
  reasoningEffort: "high",
};

function loadPreferences(): Preferences {
  try {
    const raw = localStorage.getItem("openbad.preferences");
    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<Preferences>;
      return {
        model: typeof parsed.model === "string" ? parsed.model : defaultPreferences.model,
        thinking:
          typeof parsed.thinking === "boolean" ? parsed.thinking : defaultPreferences.thinking,
        reasoningEffort:
          typeof parsed.reasoningEffort === "string"
            ? parsed.reasoningEffort
            : defaultPreferences.reasoningEffort,
      };
    }
  } catch {
    // Ignore unavailable or malformed storage.
  }
  return defaultPreferences;
}

type ScrollPosition = {
  scrollTop: number;
  follow: boolean;
};

type ScrollRestore = ScrollPosition & {
  /** Scroll to the live bottom instead of the stored offset. */
  bottom: boolean;
};

type BackgroundRun = {
  id: string;
  projectId: string | null;
  projectName: string;
  workspacePath: string;
  sessionId: string | null;
  permissionMode: PermissionMode;
  entries: ChatEntry[];
  busy: boolean;
  stopRequested: boolean;
  scrollTop: number;
  follow: boolean;
  totalDurationMs: number;
  pendingToolApproval: ToolApprovalRequest | null;
  pendingAccessRequest: OutsideAccessRequest | null;
  pendingPreview: PreviewTarget | null;
  error: string | null;
};

function sessionEntries(messages: SessionView["messages"]): ChatEntry[] {
  return messages.map((item) => ({
    id: nextId(),
    role: item.role === "user" ? "user" : "assistant",
    text: item.text,
    attachments: (item.attachments ?? []).map((attachment) =>
      attachment.includes("/") || attachment.includes("\\")
        ? {
            name: attachment.split(/[\\/]/).at(-1) ?? attachment,
            path: attachment,
          }
        : { name: attachment },
    ),
    reasoning: item.reasoning ?? "",
    durationMs: item.duration_ms ?? undefined,
  }));
}

function applyAgentEvent(entry: ChatEntry, event: AgentEvent): ChatEntry {
  switch (event.type) {
    case "step":
      return { ...entry, status: `Step ${event.step}` };
    case "reasoning":
      return {
        ...entry,
        reasoning: appendStepText(
          entry.reasoning ?? "",
          event.text,
          entry.lastReasoningStep,
          event.step,
        ),
        lastReasoningStep: event.step,
      };
    case "content":
      return {
        ...entry,
        text: appendStepText(entry.text, event.text, entry.lastContentStep, event.step),
        lastContentStep: event.step,
        status: "Writing…",
      };
    case "tool_call":
      return {
        ...entry,
        status: `Calling ${event.name}…`,
        tools: [...(entry.tools ?? []), `→ ${event.name}(${event.arguments})`],
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
    case "compaction":
      return {
        ...entry,
        status: "Context compacted",
        tools: [
          ...(entry.tools ?? []),
          `⟳ context compacted (${formatBytes(event.before_bytes)} → ${formatBytes(event.after_bytes)})`,
        ],
      };
  }
}

export default function App() {
  const [preferences] = useState(loadPreferences);
  const [foundation, setFoundation] = useState<FoundationState>({ kind: "loading" });
  const [projects, setProjects] = useState<WorkspaceSummary[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [workspacePath, setWorkspacePath] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [archivedSessions, setArchivedSessions] = useState<SessionSummary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [sessionQuery, setSessionQuery] = useState("");
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("read");
  const [model, setModel] = useState(preferences.model);
  const [thinking, setThinking] = useState(preferences.thinking);
  const [reasoningEffort, setReasoningEffort] = useState(preferences.reasoningEffort);
  const [apiKeyStatus, setApiKeyStatus] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [indexSummary, setIndexSummary] = useState<WorkspaceIndexSummary | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [checkpointList, setCheckpointList] = useState<FileCheckpointSummary[]>([]);
  const [restoringCheckpoint, setRestoringCheckpoint] = useState<string | null>(null);
  const [checkpointDiffId, setCheckpointDiffId] = useState<string | null>(null);
  const [checkpointDiffText, setCheckpointDiffText] = useState("");
  const [checkpointNotice, setCheckpointNotice] = useState<string | null>(null);
  const [checkpointError, setCheckpointError] = useState<string | null>(null);
  const [memoryList, setMemoryList] = useState<MemoryRecord[]>([]);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelTab, setPanelTab] = useState<PanelTab>("overview");
  const [panelWidth, setPanelWidth] = useState(460);
  const [previewWidth, setPreviewWidth] = useState(420);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const previewOpen = preview !== null;
  const [totalDurationMs, setTotalDurationMs] = useState(0);
  const [transfer, setTransfer] = useState<TransferState | null>(null);
  const [mergeBundle, setMergeBundle] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [projectRevision, setProjectRevision] = useState(0);
  const [areaCounts, setAreaCounts] = useState<ProjectAreaCounts | null>(null);
  const [accessRequest, setAccessRequest] = useState<OutsideAccessRequest | null>(null);
  const [toolApproval, setToolApproval] = useState<ToolApprovalRequest | null>(null);
  const [accessNotice, setAccessNotice] = useState<string | null>(null);
  const [accessGrants, setAccessGrants] = useState<OutsideAccessGrant[]>([]);
  const [queuedMessages, setQueuedMessages] = useState<string[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("model");
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [workspacesDir, setWorkspacesDir] = useState("");
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [sessionTitleDraft, setSessionTitleDraft] = useState("");
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<ChatComposerHandle | null>(null);
  const sessionSearchRef = useRef<HTMLInputElement | null>(null);
  const followRef = useRef(true);
  const scrollPositionsRef = useRef<Map<string, ScrollPosition>>(new Map());
  const pendingScrollRestoreRef = useRef<ScrollRestore | null>(null);
  const autoCollapsedSidebar = useRef(false);
  const sendRef = useRef<(text: string) => void>(() => undefined);
  const steerRef = useRef<(text: string) => void>(() => undefined);
  const runsRef = useRef<Map<string, BackgroundRun>>(new Map());
  const visibleRunRef = useRef<string | null>(null);
  const workspacePathRef = useRef(workspacePath);
  workspacePathRef.current = workspacePath;

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [activeProjectId, projects],
  );
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === sessionId) ?? null,
    [sessionId, sessions],
  );
  const currentTitle = activeSession?.title ?? (sessionId === null ? "New session" : "Session");
  const sessionUsage = useMemo<Usage>(
    () =>
      entries.reduce<Usage>(
        (total, entry) => {
          if (entry.usage === undefined) {
            return total;
          }
          return {
            input_tokens: total.input_tokens + entry.usage.input_tokens,
            cache_read_tokens: total.cache_read_tokens + entry.usage.cache_read_tokens,
            cache_write_tokens: total.cache_write_tokens + entry.usage.cache_write_tokens,
            output_tokens: total.output_tokens + entry.usage.output_tokens,
            reasoning_tokens: total.reasoning_tokens + entry.usage.reasoning_tokens,
            cost_usd: total.cost_usd + entry.usage.cost_usd,
          };
        },
        {
          input_tokens: 0,
          cache_read_tokens: 0,
          cache_write_tokens: 0,
          output_tokens: 0,
          reasoning_tokens: 0,
          cost_usd: 0,
        },
      ),
    [entries],
  );
  const allowWrites = permissionMode !== "read";
  const allowCommands = permissionMode === "full";

  const loadFoundation = useCallback(async () => {
    try {
      setFoundation({ kind: "ready", foundation: await fetchFoundation() });
    } catch (cause) {
      setFoundation({ kind: "error", message: describeError(cause) });
    }
  }, []);

  useEffect(() => {
    void fetchWorkspacesDirectory()
      .then(setWorkspacesDir)
      .catch(() => setWorkspacesDir(""));
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const next = await fetchProjects();
      setProjects(next);
      return next;
    } catch (cause) {
      setError(describeError(cause));
      return [];
    }
  }, []);

  const loadAccessGrants = useCallback(async () => {
    if (activeProject === null) {
      setAccessGrants([]);
      return;
    }
    try {
      setAccessGrants(await fetchAccessGrants(activeProject.path));
    } catch {
      setAccessGrants([]);
    }
  }, [activeProject]);

  useEffect(() => {
    void loadAccessGrants();
  }, [loadAccessGrants]);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await fetchSessions());
    } catch {
      setSessions([]);
    }
  }, []);

  const loadArchivedSessions = useCallback(async () => {
    try {
      setArchivedSessions(await fetchArchivedSessions());
    } catch {
      setArchivedSessions([]);
    }
  }, []);

  const refreshWorkspaceIndex = useCallback(async () => {
    if (workspacePath.length === 0) {
      return;
    }
    setIndexing(true);
    setIndexError(null);
    try {
      const report = await refreshIndex(workspacePath);
      setIndexSummary(report.summary);
      setProjectRevision((value) => value + 1);
    } catch (cause) {
      setIndexError(describeError(cause));
    } finally {
      setIndexing(false);
    }
  }, [workspacePath]);

  const loadMemories = useCallback(async () => {
    if (workspacePath.length === 0) {
      return;
    }
    try {
      setMemoryList(await fetchMemories(workspacePath));
      setMemoryError(null);
    } catch (cause) {
      setMemoryError(describeError(cause));
    }
  }, [workspacePath]);

  useEffect(() => {
    let active = true;
    void (async () => {
      const defaultPath = await workspaceDefault().catch(() => "");
      const rememberedPath = rememberedProjectPath();
      const existing = await fetchProjects().catch(() => []);
      let project: WorkspaceSummary | null | undefined =
        existing.find((item) => item.path === rememberedPath) ??
        (defaultPath.length > 0
          ? existing.find((item) => item.path === defaultPath)
          : undefined) ??
        existing[0];
      if (project === undefined && defaultPath.length > 0) {
        project = await addStoredProject(defaultPath).catch(() => null);
      }
      if (!active) {
        return;
      }
      if (project === null || project === undefined) {
        setProjects(existing);
        setProjectDialogOpen(true);
        return;
      }
      const selected = project;
      setProjects(existing.some((item) => item.id === selected.id) ? existing : [selected, ...existing]);
      setActiveProjectId(selected.id);
      setWorkspacePath(selected.path);
      setProjectNameDraft(selected.name);
      rememberProjectPath(selected.path);
      void fetchApiKeyStatus()
        .then(setApiKeyStatus)
        .catch(() => setApiKeyStatus(false));
      void loadFoundation();
      void loadSessions();
      void loadArchivedSessions();
    })();
    return () => {
      active = false;
    };
  }, [loadFoundation, loadSessions]);

  useEffect(() => {
    const query = sessionQuery.trim();
    if (query.length === 0) {
      void loadSessions();
      void loadArchivedSessions();
      return;
    }
    const timeout = window.setTimeout(() => {
      void searchStoredSessions(query)
        .then(setSessions)
        .catch(() => setSessions([]));
    }, 200);
    return () => window.clearTimeout(timeout);
  }, [loadSessions, sessionQuery]);

  useEffect(() => {
    if (workspacePath.length === 0) {
      return;
    }
    let active = true;
    setIndexSummary(null);
    setIndexError(null);
    void fetchIndexStatus(workspacePath)
      .then((summary) => {
        if (!active) {
          return;
        }
        setIndexSummary(summary);
        if (summary.files === 0) {
          void refreshWorkspaceIndex();
        }
      })
      .catch((cause) => {
        if (active) {
          setIndexError(describeError(cause));
        }
      });
    return () => {
      active = false;
    };
  }, [refreshWorkspaceIndex, workspacePath]);

  useEffect(() => {
    if (workspacePath.length === 0) {
      setAreaCounts(null);
      return;
    }
    let active = true;
    const load = () => {
      if (document.visibilityState === "hidden") {
        return;
      }
      void loadProjectAreaCounts(workspacePath)
        .then((counts) => {
          if (active) {
            setAreaCounts((previous) =>
              previous !== null && JSON.stringify(previous) === JSON.stringify(counts)
                ? previous
                : counts,
            );
          }
        })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 5_000);
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", load);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", load);
    };
  }, [projectRevision, workspacePath]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  useEffect(() => {
    if (sessionId === null) {
      setCheckpointList([]);
      return;
    }
    let active = true;
    void fetchCheckpoints(sessionId)
      .then((items) => {
        if (active) {
          setCheckpointList(items);
        }
      })
      .catch((cause) => {
        if (active) {
          setCheckpointError(describeError(cause));
        }
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  // Follow new content only while the reader is already at the bottom. Busy
  // transitions deliberately do not scroll: they would yank the viewport away
  // from someone reading history while a turn streams in the background.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element === null || !followRef.current) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [entries]);

  useLayoutEffect(() => {
    const pending = pendingScrollRestoreRef.current;
    const element = scrollRef.current;
    if (pending === null || element === null) {
      return;
    }
    pendingScrollRestoreRef.current = null;
    element.scrollTop =
      pending.bottom || pending.follow ? element.scrollHeight : pending.scrollTop;
    const atBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    followRef.current = atBottom;
    setShowJumpToLatest(!atBottom);
  }, [activeProjectId, entries, sessionId]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "openbad.preferences",
        JSON.stringify({ model, thinking, reasoningEffort }),
      );
    } catch {
      // Storage can be unavailable in hardened webviews; preferences are optional.
    }
  }, [model, reasoningEffort, thinking]);

  useEffect(() => {
    setProjectNameDraft(activeProject?.name ?? "");
  }, [activeProject?.id, activeProject?.name]);

  const saveScrollPosition = useCallback(
    (element: HTMLDivElement) => {
      // Derive follow from the live box rather than the last scroll event so a
      // programmatic scroll immediately before a project switch is still saved
      // accurately.
      const follow =
        element.scrollHeight - element.scrollTop - element.clientHeight < 96;
      followRef.current = follow;
      const position: ScrollPosition = {
        scrollTop: element.scrollTop,
        follow,
      };
      const runKey = visibleRunRef.current;
      const run = runKey === null ? undefined : runsRef.current.get(runKey);
      if (run !== undefined) {
        run.scrollTop = position.scrollTop;
        run.follow = position.follow;
      }
      if (sessionId !== null) {
        scrollPositionsRef.current.set(`session:${sessionId}`, position);
      } else if (workspacePath.length > 0) {
        scrollPositionsRef.current.set(`project:${workspacePath}`, position);
      }
    },
    [sessionId, workspacePath],
  );

  const captureCurrentScroll = useCallback(() => {
    const element = scrollRef.current;
    if (element !== null) {
      saveScrollPosition(element);
    }
  }, [saveScrollPosition]);

  const handleChatScroll = useCallback(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    const atBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    followRef.current = atBottom;
    setShowJumpToLatest(!atBottom);
    saveScrollPosition(element);
  }, [saveScrollPosition]);

  const jumpToLatest = useCallback(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    element.scrollTop = element.scrollHeight;
    followRef.current = true;
    setShowJumpToLatest(false);
    saveScrollPosition(element);
  }, [saveScrollPosition]);

  const handleChatKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const element = scrollRef.current;
      if (element === null) {
        return;
      }
      const page = Math.max(80, element.clientHeight * 0.9);
      switch (event.key) {
        case "PageDown":
          element.scrollTop = Math.min(
            element.scrollHeight,
            element.scrollTop + page,
          );
          break;
        case "PageUp":
          element.scrollTop = Math.max(0, element.scrollTop - page);
          break;
        case "Home":
          element.scrollTop = 0;
          break;
        case "End":
          element.scrollTop = element.scrollHeight;
          break;
        default:
          return;
      }
      event.preventDefault();
      const atBottom =
        element.scrollHeight - element.scrollTop - element.clientHeight < 96;
      followRef.current = atBottom;
      setShowJumpToLatest(!atBottom);
      saveScrollPosition(element);
    },
    [saveScrollPosition],
  );

  const selectProject = useCallback(
    (project: WorkspaceSummary) => {
      captureCurrentScroll();
      const run = runsRef.current.get(project.path);
      const stored =
        run === undefined
          ? scrollPositionsRef.current.get(`project:${project.path}`)
          : { scrollTop: run.scrollTop, follow: run.follow };
      pendingScrollRestoreRef.current = {
        scrollTop: stored?.scrollTop ?? 0,
        follow: stored?.follow ?? true,
        bottom: stored === undefined || stored.follow,
      };
      followRef.current = pendingScrollRestoreRef.current.follow;
      visibleRunRef.current = run === undefined ? null : project.path;
      setActiveProjectId(project.id);
      setWorkspacePath(project.path);
      setProjectNameDraft(project.name);
      rememberProjectPath(project.path);
      setSessionId(run?.sessionId ?? null);
      setEntries(run === undefined ? [] : [...run.entries]);
      setPermissionMode(run?.permissionMode ?? "read");
      setAccessRequest(run?.pendingAccessRequest ?? null);
      setToolApproval(run?.pendingToolApproval ?? null);
      setPreview(run?.pendingPreview ?? null);
      setBusy(run?.busy ?? false);
      setStopRequested(run?.stopRequested ?? false);
      setQueuedMessages([]);
      setError(run?.error ?? null);
      setTotalDurationMs(run?.totalDurationMs ?? 0);
      setShowJumpToLatest(false);
      if (run !== undefined && !run.busy && run.sessionId !== null) {
        // A completed run only caches its own turn. Hydrate the full session
        // transcript so switching back does not hide earlier turns.
        void fetchSession(run.sessionId)
          .then((view) => {
            const current = runsRef.current.get(project.path);
            if (current !== run) {
              return;
            }
            run.entries = sessionEntries(view.messages);
            if (visibleRunRef.current === project.path) {
              setEntries([...run.entries]);
            }
          })
          .catch(() => undefined);
      }
    },
    [captureCurrentScroll],
  );

  const openPreview = useCallback((target: PreviewTarget) => {
    setPreview(target);
  }, []);

  const openArtifact = useCallback((artifact: Artifact | null) => {
    setPreview(artifact === null ? null : { kind: "artifact", artifact });
  }, []);

  const openGitRepo = useCallback(
    (repoPath: string, repoName: string) => {
      if (workspacePath.length === 0) {
        return;
      }
      setPreview({ kind: "git", workspacePath, repoPath, repoName });
    },
    [workspacePath],
  );

  const markWorkspaceChanged = useCallback(() => {
    setProjectRevision((current) => current + 1);
  }, []);

  const exportProjectFlow = useCallback(async () => {
    if (activeProject === null || workspacePath.length === 0) {
      return;
    }
    try {
      const destination = await pickSaveFile(
        "Export project",
        `${activeProject.name}.openbad-project`,
      );
      if (destination === null) {
        return;
      }
      setTransfer({
        phase: "running",
        label: `Packing ${activeProject.name}…`,
      });
      const report = await exportProjectBundle(workspacePath, destination);
      setTransfer({ phase: "done", title: "Export complete", report });
    } catch (cause) {
      setTransfer({
        phase: "error",
        title: "Export failed",
        message: describeError(cause),
      });
    }
  }, [activeProject, workspacePath]);

  const importProjectFlow = useCallback(async () => {
    try {
      const bundle = await pickImportBundle();
      if (bundle === null) {
        return;
      }
      const parent = await pickDirectory("Choose where to create the imported project");
      if (parent === null) {
        return;
      }
      setTransfer({ phase: "running", label: "Importing project…" });
      const report = await importProjectBundle(bundle, parent);
      const next = await loadProjects();
      const imported = next.find((project) => project.path === report.path);
      if (imported !== undefined) {
        selectProject(imported);
      }
      setTransfer({ phase: "done", title: "Import complete", report });
      void loadSessions();
      void loadArchivedSessions();
      void loadFoundation();
    } catch (cause) {
      setTransfer({
        phase: "error",
        title: "Import failed",
        message: describeError(cause),
      });
    }
  }, [loadArchivedSessions, loadFoundation, loadProjects, loadSessions, selectProject]);

  const startMergeImport = useCallback(async () => {
    if (activeProject === null) {
      return;
    }
    try {
      const bundle = await pickImportBundle();
      if (bundle === null) {
        return;
      }
      setMergeBundle(bundle);
    } catch (cause) {
      setTransfer({
        phase: "error",
        title: "Import failed",
        message: describeError(cause),
      });
    }
  }, [activeProject]);

  const confirmMergeImport = useCallback(async () => {
    if (mergeBundle === null || activeProject === null || workspacePath.length === 0) {
      return;
    }
    const bundle = mergeBundle;
    setMergeBundle(null);
    try {
      setTransfer({ phase: "running", label: `Merging into ${activeProject.name}…` });
      const report = await importProjectIntoBundle(workspacePath, bundle);
      setTransfer({ phase: "done", title: "Merge complete", report });
      setProjectRevision((value) => value + 1);
      void loadSessions();
      void loadArchivedSessions();
      void loadFoundation();
    } catch (cause) {
      setTransfer({
        phase: "error",
        title: "Import failed",
        message: describeError(cause),
      });
    }
  }, [
    activeProject,
    loadArchivedSessions,
    loadFoundation,
    loadSessions,
    mergeBundle,
    workspacePath,
  ]);

  const newSession = useCallback(() => {
    captureCurrentScroll();
    visibleRunRef.current = null;
    pendingScrollRestoreRef.current = { scrollTop: 0, follow: true, bottom: true };
    followRef.current = true;
    setSessionId(null);
    setEntries([]);
    setTotalDurationMs(0);
    setPreview(null);
    setPermissionMode("read");
    setAccessRequest(null);
    setToolApproval(null);
    setBusy(false);
    setStopRequested(false);
    setQueuedMessages([]);
    setError(null);
    setShowJumpToLatest(false);
    composerRef.current?.focus();
  }, [captureCurrentScroll]);

  const changePermissionMode = useCallback(
    (mode: PermissionMode) => {
      setPermissionMode(mode);
      const runKey = visibleRunRef.current;
      const run = runKey === null ? undefined : runsRef.current.get(runKey);
      if (run !== undefined) {
        run.permissionMode = mode;
      }
      if (sessionId === null) {
        return;
      }
      setSessions((items) =>
        items.map((item) =>
          item.id === sessionId ? { ...item, access_mode: mode } : item,
        ),
      );
      void persistSessionAccessMode(sessionId, mode).catch((cause) => {
        setError(describeError(cause));
      });
    },
    [sessionId],
  );

  const grantAccess = useCallback(async () => {
    if (accessRequest === null || workspacePath.length === 0) {
      return;
    }
    try {
      const grant = await grantFilesystemAccess(
        workspacePath,
        accessRequest.path,
        accessRequest.write,
        accessRequest.reason,
      );
      setAccessRequest(null);
      setAccessGrants((items) => [
        ...items.filter((item) => item.path !== grant.path),
        grant,
      ]);
      setAccessNotice(`Access granted to ${grant.path}. Resuming the task.`);
      const continuation = `Access granted for ${grant.path}. Continue with the task.`;
      if (busy) {
        steerRef.current(continuation);
      } else {
        sendRef.current(continuation);
      }
      composerRef.current?.focus();
      window.setTimeout(() => setAccessNotice(null), 8_000);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [accessRequest, busy, workspacePath]);

  const approveToolAccess = useCallback(async () => {
    if (toolApproval === null) {
      return;
    }
    changePermissionMode("full");
    const run = visibleRunRef.current === null
      ? undefined
      : runsRef.current.get(visibleRunRef.current);
    if (run !== undefined) {
      run.permissionMode = "full";
      run.pendingToolApproval = null;
    }
    setToolApproval(null);
    setAccessNotice(`Tool access approved. This session is now in Full access.`);
    const continuation = `Approved ${toolApproval.tool}. Continue and retry the action.`;
    if (busy) {
      // The running turn captured its permission mode when it started. Queue the
      // retry so it runs as a new turn with Full access once this one finishes.
      setQueuedMessages((current) => [...current, continuation]);
    } else {
      sendRef.current(continuation);
    }
    composerRef.current?.focus();
    window.setTimeout(() => setAccessNotice(null), 8_000);
  }, [busy, changePermissionMode, toolApproval]);

  const revokeAccess = useCallback(
    async (grant: OutsideAccessGrant) => {
      if (activeProject === null) {
        return;
      }
      try {
        await revokeFilesystemAccess(activeProject.path, grant.id);
        setAccessGrants((items) => items.filter((item) => item.id !== grant.id));
      } catch (cause) {
        setError(describeError(cause));
      }
    },
    [activeProject],
  );

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

  const clearApiKey = useCallback(async () => {
    setError(null);
    try {
      await clearStoredApiKey();
      setApiKeyStatus(false);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, []);

  const stop = useCallback(async () => {
    const runKey = visibleRunRef.current;
    const run = runKey === null ? undefined : runsRef.current.get(runKey);
    if (run === undefined || !run.busy) {
      return;
    }
    run.stopRequested = true;
    setStopRequested(true);
    try {
      // Only the run shown in this project is cancelled. A turn streaming in
      // another project keeps its own token and continues.
      await cancelChat(run.id);
    } catch (cause) {
      run.stopRequested = false;
      setStopRequested(false);
      setError(describeError(cause));
    }
  }, []);

  const showCheckpointDiff = useCallback(
    async (checkpointId: string) => {
      if (checkpointDiffId === checkpointId) {
        setCheckpointDiffId(null);
        return;
      }
      setCheckpointDiffId(checkpointId);
      setCheckpointDiffText("");
      setCheckpointError(null);
      try {
        setCheckpointDiffText(await fetchCheckpointDiff(checkpointId));
      } catch (cause) {
        setCheckpointError(describeError(cause));
      }
    },
    [checkpointDiffId],
  );

  const forgetMemory = useCallback(async (memoryId: string) => {
    setMemoryError(null);
    try {
      await forgetStoredMemory(memoryId);
      setMemoryList((items) => items.filter((item) => item.id !== memoryId));
    } catch (cause) {
      setMemoryError(describeError(cause));
    }
  }, []);

  const restoreCheckpoint = useCallback(
    async (checkpointId: string) => {
      setRestoringCheckpoint(checkpointId);
      setCheckpointError(null);
      setCheckpointNotice(null);
      try {
        const path = await restoreStoredCheckpoint(checkpointId);
        setCheckpointList((items) => items.filter((item) => item.id !== checkpointId));
        if (checkpointDiffId === checkpointId) {
          setCheckpointDiffId(null);
        }
        setCheckpointNotice(`Restored ${path}`);
        setProjectRevision((value) => value + 1);
        void refreshWorkspaceIndex();
      } catch (cause) {
        setCheckpointError(describeError(cause));
      } finally {
        setRestoringCheckpoint(null);
      }
    },
    [checkpointDiffId, refreshWorkspaceIndex],
  );

  useEffect(() => {
    const interval = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, []);

  const openPanel = useCallback((tab: PanelTab) => {
    setPanelTab(tab);
    setPanelOpen(true);
  }, []);

  useEffect(() => {
    if (panelOpen && window.innerWidth < 1180 && sidebarOpen) {
      autoCollapsedSidebar.current = true;
      setSidebarOpen(false);
    } else if (!panelOpen && autoCollapsedSidebar.current) {
      autoCollapsedSidebar.current = false;
      setSidebarOpen(true);
    }
  }, [panelOpen, sidebarOpen]);

  const startPanelResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = panelWidth;
      const reserved = 380 + (previewOpen ? previewWidth + 4 : 0);
      const move = (pointer: PointerEvent) => {
        const available = Math.max(320, window.innerWidth - reserved);
        setPanelWidth(
          Math.min(available, Math.max(320, startWidth - (pointer.clientX - startX))),
        );
      };
      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
    },
    [panelWidth, previewOpen, previewWidth],
  );

  const startPreviewResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = previewWidth;
      const reserved = 720 + (panelOpen ? panelWidth + 4 : 0);
      const move = (pointer: PointerEvent) => {
        const available = Math.max(300, window.innerWidth - reserved);
        setPreviewWidth(
          Math.min(available, Math.max(300, startWidth - (pointer.clientX - startX))),
        );
      };
      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
    },
    [previewWidth, panelOpen, panelWidth],
  );

  const send = useCallback(async (override: string, pending: UploadSource[] = []) => {
    const text = override.trim();
    if ((text.length === 0 && pending.length === 0) || workspacePath.length === 0) {
      return;
    }
    // Only the same project is serialized. Another project may keep a turn
    // streaming while this one starts a new one.
    const activeRun = runsRef.current.get(workspacePath);
    if (activeRun?.busy === true) {
      setError(
        "This project already has a running turn. Switch to that session to follow or stop it.",
      );
      return;
    }
    setBusy(true);
    setStopRequested(false);
    setError(null);
    const assistantId = nextId();
    const userId = nextId();
    const userEntry: ChatEntry = {
      id: userId,
      role: "user",
      text,
      attachments: pending.map((attachment) => ({ name: attachment.name })),
    };
    const assistantEntry: ChatEntry = {
      id: assistantId,
      role: "assistant",
      text: "",
      reasoning: "",
      status: "Starting…",
      tools: [],
      startedAt: Date.now(),
    };
    const runKey = workspacePath;
    const runId = nextId();
    const existingRun = runsRef.current.get(runKey);
    // Preserve the turns already visible in this session. The previous run
    // cache only holds the latest turn; the live `entries` state is the full
    // transcript the user can currently see.
    const baseEntries =
      existingRun !== undefined && existingRun.busy
        ? existingRun.entries
        : entries;
    const run: BackgroundRun = {
      id: runId,
      projectId: activeProjectId,
      projectName: activeProject?.name ?? "another project",
      workspacePath,
      sessionId,
      permissionMode,
      entries: [...baseEntries, userEntry, assistantEntry],
      busy: true,
      stopRequested: false,
      scrollTop: scrollRef.current?.scrollTop ?? 0,
      follow: followRef.current,
      totalDurationMs,
      pendingToolApproval: null,
      pendingAccessRequest: null,
      pendingPreview: null,
      error: null,
    };
    runsRef.current.set(runKey, run);
    visibleRunRef.current = runKey;
    setEntries([...run.entries]);

    try {
      const reply = await sendChat(
        {
          runId,
          sessionId,
          workspacePath,
          message: text,
          allowWrites,
          allowCommands,
          model,
          thinking,
          reasoningEffort,
          attachments: pending,
        },
        (event) => {
          const current = runsRef.current.get(runKey);
          if (current === undefined) {
            return;
          }
          let eventPreview: PreviewTarget | null = null;
          if (event.type === "tool_result") {
            const nextPreview = previewTargetFromMetadata(
              event.metadata,
              workspacePath,
            );
            eventPreview = nextPreview;
            const nextAccessRequest = accessRequestFromMetadata(event.metadata);
            const nextToolApproval = toolApprovalFromMetadata(event.metadata);
            if (nextPreview !== null) {
              current.pendingPreview = nextPreview;
              if (visibleRunRef.current === runKey) {
                openPreview(nextPreview);
              }
            }
            if (nextAccessRequest !== null) {
              current.pendingAccessRequest = nextAccessRequest;
              if (visibleRunRef.current === runKey) {
                setAccessRequest(nextAccessRequest);
              }
            }
            if (nextToolApproval !== null) {
              current.pendingToolApproval = nextToolApproval;
              if (visibleRunRef.current === runKey) {
                setToolApproval(nextToolApproval);
              }
            }
          }
          const entryPreview = eventPreview;
          current.entries = current.entries.map((entry) => {
            const updated =
              entry.id === assistantId ? applyAgentEvent(entry, event) : entry;
            return entry.id === assistantId && entryPreview !== null
              ? { ...updated, preview: entryPreview }
              : updated;
          });
          if (visibleRunRef.current === runKey) {
            setEntries([...current.entries]);
          }
        },
      );
      run.sessionId = reply.session_id;
      if (sessionId === null) {
        try {
          // Use the run's latest mode: the user may have approved tool access
          // or changed permissions while the first turn was streaming.
          await persistSessionAccessMode(reply.session_id, run.permissionMode);
        } catch (cause) {
          setError(describeError(cause));
        }
      }
      run.entries = run.entries.map((entry) =>
        entry.id === userId
          ? {
              ...entry,
              attachments: reply.attachments.map((file) => ({
                name: file.name,
                path: file.path,
                bytes: file.bytes,
              })),
            }
          : entry,
      );
      run.entries = run.entries.map((entry) =>
        entry.id === assistantId
          ? {
              ...entry,
              text: reply.text || entry.text || "(no text returned)",
              tools:
                entry.tools !== undefined && entry.tools.length > 0
                  ? entry.tools
                  : reply.tool_trace,
              usage: reply.usage,
              durationMs: reply.duration_ms,
              status: `Done · ${reply.steps} step${reply.steps === 1 ? "" : "s"}`,
            }
          : entry,
      );
      run.totalDurationMs += reply.duration_ms;
      if (visibleRunRef.current === runKey) {
        setSessionId(reply.session_id);
        setEntries([...run.entries]);
        setTotalDurationMs(run.totalDurationMs);
      }
      void loadFoundation();
      void loadSessions();
      void loadArchivedSessions();
      void loadProjects();
      void loadMemories();
      setProjectRevision((value) => value + 1);
      window.setTimeout(() => setProjectRevision((value) => value + 1), 1_500);
    } catch (cause) {
      const message = describeError(cause);
      run.error = message;
      run.entries = run.entries.map((entry) =>
        entry.id === assistantId
          ? {
              ...entry,
              status: "Failed",
              text: entry.text || "(request failed)",
            }
          : entry,
      );
      if (visibleRunRef.current === runKey) {
        setError(message);
        setEntries([...run.entries]);
      }
    } finally {
      run.busy = false;
      run.stopRequested = false;
      if (visibleRunRef.current === runKey) {
        setBusy(false);
        setStopRequested(false);
      }
    }
  }, [
    activeProject,
    activeProjectId,
    allowCommands,
    allowWrites,
    entries,
    loadFoundation,
    loadMemories,
    loadProjects,
    loadSessions,
    model,
    openPreview,
    permissionMode,
    reasoningEffort,
    sessionId,
    thinking,
    totalDurationMs,
    workspacePath,
  ]);

  const steer = useCallback(async (override: string) => {
    const text = override.trim();
    if (text.length === 0) {
      return;
    }
    const run = runsRef.current.get(workspacePath);
    if (run === undefined || !run.busy) {
      void send(text);
      return;
    }
    if (visibleRunRef.current !== workspacePath) {
      setError(
        "This project is already running in another session. Open that session to steer it.",
      );
      return;
    }
    try {
      await steerRunningTurn(run.id, text);
      const steeringEntry: ChatEntry = {
        id: nextId(),
        role: "user",
        text,
        steering: true,
      };
      run.entries = [...run.entries, steeringEntry];
      if (visibleRunRef.current === workspacePath) {
        setEntries([...run.entries]);
      }
    } catch {
      setQueuedMessages((current) => [...current, text]);
    }
  }, [send, workspacePath]);
  sendRef.current = (text) => {
    void send(text);
  };
  steerRef.current = (text) => {
    void steer(text);
  };

  const queue = useCallback((text: string) => {
    const trimmed = text.trim();
    if (trimmed.length === 0) {
      return;
    }
    setQueuedMessages((current) => [...current, trimmed]);
  }, []);

  useEffect(() => {
    if (busy || queuedMessages.length === 0 || workspacePath.length === 0) {
      return;
    }
    const [next, ...rest] = queuedMessages;
    setQueuedMessages(rest);
    void send(next);
  }, [busy, queuedMessages, send, workspacePath]);

  const removeQueued = useCallback((index: number) => {
    setQueuedMessages((current) =>
      current.filter((_, itemIndex) => itemIndex !== index),
    );
  }, []);

  const openSession = useCallback(
    async (session: SessionSummary) => {
      captureCurrentScroll();
      setError(null);
      try {
        const view = await fetchSession(session.id);
        const project =
          session.workspace_id === null
            ? undefined
            : projects.find((item) => item.id === session.workspace_id);
        const targetPath = project?.path ?? session.workspace_path ?? workspacePath;
        const run =
          targetPath.length === 0 ? undefined : runsRef.current.get(targetPath);
        const loadedEntries = sessionEntries(view.messages);
        if (
          run !== undefined &&
          !run.busy &&
          (run.sessionId === view.session_id || run.sessionId === null)
        ) {
          run.entries = loadedEntries;
        }
        const showRun =
          run !== undefined &&
          (run.busy || run.sessionId === null) &&
          (run.sessionId === view.session_id || run.sessionId === null);
        const stored = showRun
          ? { scrollTop: run.scrollTop, follow: run.follow }
          : scrollPositionsRef.current.get(`session:${view.session_id}`);
        pendingScrollRestoreRef.current = {
          scrollTop: stored?.scrollTop ?? 0,
          follow: stored?.follow ?? true,
          bottom: stored === undefined || stored.follow,
        };
        followRef.current = pendingScrollRestoreRef.current.follow;
        if (project !== undefined) {
          setActiveProjectId(project.id);
          setWorkspacePath(project.path);
          rememberProjectPath(project.path);
        } else if (session.workspace_path !== null) {
          setWorkspacePath(session.workspace_path);
        }
        visibleRunRef.current = showRun ? targetPath : null;
        setSessionId(view.session_id);
        setPermissionMode(showRun ? run.permissionMode : session.access_mode);
        setAccessRequest(showRun ? run.pendingAccessRequest : null);
        setToolApproval(showRun ? run.pendingToolApproval : null);
        setPreview(showRun ? run.pendingPreview : null);
        setBusy(showRun ? run.busy : false);
        setStopRequested(showRun ? run.stopRequested : false);
        setQueuedMessages([]);
        setError(showRun ? run.error : null);
        setTotalDurationMs(
          showRun ? run.totalDurationMs : view.total_duration_ms,
        );
        setEntries(showRun ? [...run.entries] : loadedEntries);
      } catch (cause) {
        setError(describeError(cause));
      }
    },
    [captureCurrentScroll, projects, workspacePath],
  );

  const beginRenameSession = useCallback((session: SessionSummary) => {
    setEditingSessionId(session.id);
    setSessionTitleDraft(session.title ?? "Untitled session");
  }, []);

  const commitRenameSession = useCallback(async () => {
    if (editingSessionId === null) {
      return;
    }
    const title = sessionTitleDraft.trim();
    const session = editingSessionId;
    setEditingSessionId(null);
    if (title.length === 0) {
      return;
    }
    try {
      await renameStoredSession(session, title);
      setSessions((items) =>
        items.map((item) => (item.id === session ? { ...item, title } : item)),
      );
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [editingSessionId, sessionTitleDraft]);

  const archiveSession = useCallback(
    async (session: SessionSummary, archived: boolean) => {
      try {
        await archiveStoredSession(session.id, archived);
        if (archived && session.id === sessionId) {
          newSession();
        }
        await Promise.all([loadSessions(), loadArchivedSessions()]);
        setSessions((items) =>
          archived ? items.filter((item) => item.id !== session.id) : items,
        );
      } catch (cause) {
        setError(describeError(cause));
      }
    },
    [loadArchivedSessions, loadSessions, newSession, sessionId],
  );

  const deleteSession = useCallback(
    async (session: SessionSummary) => {
      if (!window.confirm(`Delete session "${session.title ?? "Untitled session"}"?`)) {
        return;
      }
      try {
        await deleteStoredSession(session.id);
        if (session.id === sessionId) {
          newSession();
        }
        await Promise.all([loadSessions(), loadArchivedSessions()]);
      } catch (cause) {
        setError(describeError(cause));
      }
    },
    [loadArchivedSessions, loadSessions, newSession, sessionId],
  );

  const saveProjectName = useCallback(async () => {
    if (activeProject === null) {
      return;
    }
    const name = projectNameDraft.trim();
    if (name.length === 0 || name === activeProject.name) {
      setProjectNameDraft(activeProject.name);
      return;
    }
    try {
      await renameStoredProject(activeProject.id, name);
      setProjects((items) =>
        items.map((item) => (item.id === activeProject.id ? { ...item, name } : item)),
      );
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [activeProject, projectNameDraft]);

  const removeProject = useCallback(async () => {
    if (activeProject === null) {
      return;
    }
    if (!window.confirm(`Remove project "${activeProject.name}" from OpenBaD?`)) {
      return;
    }
    try {
      await removeStoredProject(activeProject.id);
      const remaining = projects.filter((item) => item.id !== activeProject.id);
      setProjects(remaining);
      if (remaining.length > 0) {
        selectProject(remaining[0]);
      } else {
        setActiveProjectId(null);
        setWorkspacePath("");
        setSessionId(null);
        setEntries([]);
        setQueuedMessages([]);
        visibleRunRef.current = null;
        pendingScrollRestoreRef.current = null;
        followRef.current = true;
        setBusy(false);
        setStopRequested(false);
        setShowJumpToLatest(false);
        setTotalDurationMs(0);
      }
      setSettingsOpen(false);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [activeProject, projects, selectProject]);

  const createProject = useCallback(async () => {
    const name = newProjectName.trim();
    if (name.length === 0) {
      return;
    }
    try {
      const project = await createStoredProject(name);
      await loadProjects();
      setProjects((items) =>
        items.some((item) => item.id === project.id) ? items : [project, ...items],
      );
      selectProject(project);
      setProjectDialogOpen(false);
      setNewProjectName("");
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [loadProjects, newProjectName, selectProject]);

  const openSettings = useCallback(
    (tab: SettingsTab) => {
      setSettingsTab(tab);
      setSettingsOpen(true);
      void loadAccessGrants();
    },
    [loadAccessGrants],
  );

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const command = event.metaKey || event.ctrlKey;
      if (command && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      } else if (command && event.key.toLowerCase() === "n") {
        event.preventDefault();
        newSession();
      } else if (command && event.key === ",") {
        event.preventDefault();
        openSettings("model");
      } else if (command && event.key.toLowerCase() === "b") {
        event.preventDefault();
        setSidebarOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [newSession, openSettings]);

  const projectSessions = useMemo(
    () =>
      sessions.filter(
        (session) => activeProjectId !== null && session.workspace_id === activeProjectId,
      ),
    [activeProjectId, sessions],
  );
  const projectArchivedSessions = useMemo(
    () =>
      archivedSessions.filter(
        (session) => activeProjectId !== null && session.workspace_id === activeProjectId,
      ),
    [activeProjectId, archivedSessions],
  );
  const visibleSessions = sessionQuery.trim().length > 0 ? sessions : projectSessions;

  const menus: AppMenu[] = [
    {
      label: "File",
      items: [
        { label: "New Session", shortcut: "Ctrl N", onSelect: newSession },
        {
          label: "New Project…",
          onSelect: () => setProjectDialogOpen(true),
        },
        { label: "separator", separator: true },
        {
          label: "Knowledge Library",
          disabled: activeProject === null,
          onSelect: () => openPanel("knowledge"),
        },
        {
          label: "Settings…",
          shortcut: "Ctrl ,",
          onSelect: () => openSettings("model"),
        },
        { label: "separator", separator: true },
        { label: "Quit OpenBaD", onSelect: () => void quitApp() },
      ],
    },
    {
      label: "Edit",
      items: [
        {
          label: "Command Palette…",
          shortcut: "Ctrl K",
          onSelect: () => setCommandOpen(true),
        },
        {
          label: "Find Sessions",
          onSelect: () => {
            setSidebarOpen(true);
            window.setTimeout(() => sessionSearchRef.current?.focus(), 0);
          },
        },
        {
          label: "Rename Current Session…",
          disabled: activeSession === null,
          onSelect: () => {
            if (activeSession !== null) {
              setSidebarOpen(true);
              beginRenameSession(activeSession);
            }
          },
        },
      ],
    },
    {
      label: "View",
      items: [
        {
          label: "Toggle Sidebar",
          shortcut: "Ctrl B",
          checked: sidebarOpen,
          onSelect: () => setSidebarOpen((open) => !open),
        },
        {
          label: "Toggle Project Panel",
          checked: panelOpen,
          onSelect: () => {
            if (panelOpen) {
              setPanelOpen(false);
            } else {
              openPanel(panelTab);
            }
          },
        },
        { label: "separator", separator: true },
        {
          label: "Overview",
          checked: panelOpen && panelTab === "overview",
          disabled: activeProject === null,
          onSelect: () => openPanel("overview"),
        },
        {
          label: "Workspace",
          checked: panelOpen && panelTab === "workspace",
          disabled: activeProject === null,
          onSelect: () => openPanel("workspace"),
        },
        {
          label: "Agents",
          checked: panelOpen && panelTab === "agents",
          disabled: activeProject === null,
          onSelect: () => openPanel("agents"),
        },
        {
          label: "References",
          checked: panelOpen && panelTab === "repos",
          disabled: activeProject === null,
          onSelect: () => openPanel("repos"),
        },
        {
          label: "Knowledge",
          checked: panelOpen && panelTab === "knowledge",
          disabled: activeProject === null,
          onSelect: () => openPanel("knowledge"),
        },
        {
          label: "Retrieval",
          checked: panelOpen && panelTab === "retrieval",
          disabled: activeProject === null,
          onSelect: () => openPanel("retrieval"),
        },
        {
          label: "Memory",
          checked: panelOpen && panelTab === "memory",
          disabled: activeProject === null,
          onSelect: () => openPanel("memory"),
        },
        {
          label: "Checkpoints",
          checked: panelOpen && panelTab === "checkpoints",
          onSelect: () => openPanel("checkpoints"),
        },
        {
          label: "Diagnostics",
          checked: panelOpen && panelTab === "diagnostics",
          onSelect: () => openPanel("diagnostics"),
        },
      ],
    },
    {
      label: "Project",
      items: [
        {
          label: "Project Settings…",
          disabled: activeProject === null,
          onSelect: () => openSettings("project"),
        },
        {
          label: "Refresh Workspace Index",
          disabled: activeProject === null || indexing,
          onSelect: () => void refreshWorkspaceIndex(),
        },
        { label: "separator", separator: true },
        {
          label: "Add Reference…",
          disabled: activeProject === null,
          onSelect: () => openPanel("repos"),
        },
        {
          label: "Add Knowledge…",
          disabled: activeProject === null,
          onSelect: () => openPanel("knowledge"),
        },
        { label: "separator", separator: true },
        {
          label: "Export Project…",
          disabled: activeProject === null,
          onSelect: () => void exportProjectFlow(),
        },
        {
          label: "Import Project…",
          onSelect: () => void importProjectFlow(),
        },
      ],
    },
    {
      label: "Help",
      items: [
        {
          label: "Check for Updates…",
          onSelect: () => openSettings("about"),
        },
        {
          label: "Keyboard Shortcuts",
          onSelect: () => setShortcutsOpen(true),
        },
        {
          label: "About OpenBaD",
          onSelect: () => openSettings("about"),
        },
      ],
    },
  ];

  const composerSend = useCallback(
    (text: string, files: UploadSource[]) => {
      void send(text, files);
    },
    [send],
  );
  const composerSteer = useCallback(
    (text: string) => {
      void steer(text);
    },
    [steer],
  );
  const composerStop = useCallback(() => {
    void stop();
  }, [stop]);
  const composerOpenModel = useCallback(() => openSettings("model"), [openSettings]);
  const dismissAccessNotice = useCallback(() => setAccessNotice(null), []);
  const dismissError = useCallback(() => setError(null), []);
  const composerError = useCallback((message: string) => setError(message), []);
  const chatDrop = useFileDropZone(
    async (payload) => {
      composerRef.current?.addUploads(await droppedToUploads(payload));
    },
    { disabled: workspacePath.length === 0 },
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#151c25] text-slate-100">
      <AppMenuBar menus={menus} />
      <main className="flex min-h-0 flex-1 overflow-hidden bg-[#151c25] text-slate-100">
      {sidebarOpen && (
        <aside className="flex w-[276px] shrink-0 flex-col border-r border-white/[0.07] bg-[#1b232e]">
          <div className="flex h-14 items-center gap-2 border-b border-white/[0.06] px-3">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-400/15 text-[11px] font-bold text-emerald-300">
              OB
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-semibold tracking-tight">OpenBaD</p>
              <p className="truncate text-[10px] text-slate-500">coding harness</p>
            </div>
            <button
              type="button"
              aria-label="Collapse sidebar"
              onClick={() => setSidebarOpen(false)}
              className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
            >
              <PanelLeftClose size={15} />
            </button>
          </div>

          <div className="p-3">
            <button
              type="button"
              onClick={newSession}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-[12px] font-medium text-emerald-200 hover:bg-emerald-400/15"
            >
              <Plus size={14} /> New session
            </button>
            <label className="mt-3 flex items-center gap-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-1.5">
              <Search size={13} className="shrink-0 text-slate-500" />
              <input
                ref={sessionSearchRef}
                type="search"
                value={sessionQuery}
                onChange={(event) => setSessionQuery(event.target.value)}
                placeholder="Search sessions"
                aria-label="Search sessions"
                className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-300 outline-none placeholder:text-slate-500"
              />
            </label>
          </div>

          <div className="flex-1 overflow-y-auto px-2 pb-3">
            <div className="mb-1 flex items-center justify-between px-2 py-1">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                Projects
              </p>
              <button
                type="button"
                aria-label="New project"
                onClick={() => setProjectDialogOpen(true)}
                className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300"
              >
                <Plus size={13} />
              </button>
            </div>

            {projects.map((project) => {
              const isActive = project.id === activeProjectId;
              return (
                <div key={project.id} className="mb-1">
                  <button
                    type="button"
                    onClick={() => selectProject(project)}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] ${
                      isActive
                        ? "bg-white/[0.06] text-slate-100"
                        : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200"
                    }`}
                  >
                    {isActive ? (
                      <ChevronDown size={13} className="shrink-0 text-slate-500" />
                    ) : (
                      <ChevronRight size={13} className="shrink-0 text-slate-500" />
                    )}
                    {isActive ? (
                      <FolderOpen size={14} className="shrink-0 text-emerald-300" />
                    ) : (
                      <Folder size={14} className="shrink-0 text-slate-500" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {project.session_count}
                    </span>
                  </button>

                  {isActive && (
                    <div className="ml-2 mt-1">
                      <div className="mb-1 space-y-0.5">
                        <ProjectAreaButton
                          icon={<Gauge size={12} />}
                          label="Overview"
                          active={panelOpen && panelTab === "overview"}
                          onClick={() => openPanel("overview")}
                        />
                        <ProjectAreaButton
                          icon={<Folder size={12} />}
                          label={`Workspace${countSuffix(areaCounts?.workspace_files)}`}
                          active={panelOpen && panelTab === "workspace"}
                          onClick={() => openPanel("workspace")}
                        />
                        <ProjectAreaButton
                          icon={<Bot size={12} />}
                          label={`Agents${countSuffix(areaCounts?.agents)}`}
                          active={panelOpen && panelTab === "agents"}
                          pulse={(areaCounts?.running_agents ?? 0) > 0}
                          onClick={() => openPanel("agents")}
                        />
                        <ProjectAreaButton
                          icon={<FolderGit2 size={12} />}
                          label={`References${countSuffix(areaCounts?.references)}`}
                          active={panelOpen && panelTab === "repos"}
                          onClick={() => openPanel("repos")}
                        />
                        <ProjectAreaButton
                          icon={<BookOpen size={12} />}
                          label={`Knowledge${countSuffix(areaCounts?.knowledge)}`}
                          active={panelOpen && panelTab === "knowledge"}
                          onClick={() => openPanel("knowledge")}
                        />
                        <ProjectAreaButton
                          icon={<Search size={12} />}
                          label={`Retrieval${countSuffix(
                            areaCounts === null
                              ? undefined
                              : areaCounts.workspace_files + areaCounts.knowledge,
                          )}`}
                          active={panelOpen && panelTab === "retrieval"}
                          onClick={() => openPanel("retrieval")}
                        />
                        <ProjectAreaButton
                          icon={<Database size={12} />}
                          label={`Memory${countSuffix(areaCounts?.memories)}`}
                          active={panelOpen && panelTab === "memory"}
                          onClick={() => openPanel("memory")}
                        />
                        <GitRepoList
                          workspacePath={project.path}
                          projectName={project.name}
                          revision={projectRevision}
                          onOpen={openGitRepo}
                        />
                      </div>
                      <p className="px-2 pb-1 pt-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                        Sessions
                      </p>
                    </div>
                  )}

                  {isActive && (
                    <div className="ml-4 space-y-0.5 border-l border-white/[0.06] pl-2">
                      {visibleSessions.length === 0 && (
                        <p className="px-2 py-1.5 text-[11px] text-slate-500">
                          {sessionQuery.trim().length > 0 ? "No matches" : "No sessions"}
                        </p>
                      )}
                      {visibleSessions.map((session) => (
                        <SessionRow
                          key={session.id}
                          session={session}
                          active={session.id === sessionId}
                          editing={editingSessionId === session.id}
                          draft={sessionTitleDraft}
                          showProject={sessionQuery.trim().length > 0}
                          onOpen={() => void openSession(session)}
                          onStartRename={() => beginRenameSession(session)}
                          onDraft={setSessionTitleDraft}
                          onCommit={() => void commitRenameSession()}
                          onCancel={() => setEditingSessionId(null)}
                          onArchive={() => void archiveSession(session, true)}
                          onDelete={() => void deleteSession(session)}
                          archived={false}
                        />
                      ))}
                      {projectArchivedSessions.length > 0 && (
                        <button
                          type="button"
                          onClick={() => setShowArchived((value) => !value)}
                          className="mt-1 flex w-full items-center gap-1 rounded px-2 py-1 text-[10px] text-slate-500 hover:text-slate-400"
                        >
                          {showArchived ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                          Archived ({projectArchivedSessions.length})
                        </button>
                      )}
                      {showArchived &&
                        projectArchivedSessions.map((session) => (
                          <SessionRow
                            key={session.id}
                            session={session}
                            active={false}
                            editing={false}
                            draft=""
                            showProject={false}
                            onOpen={() => void openSession(session)}
                            onStartRename={() => undefined}
                            onDraft={() => undefined}
                            onCommit={() => undefined}
                            onCancel={() => undefined}
                            onArchive={() => void archiveSession(session, false)}
                            onDelete={() => void deleteSession(session)}
                            archived
                          />
                        ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="border-t border-white/[0.06] p-2">
            <div className="flex items-center gap-2 px-2 py-1 text-[10px] text-slate-500">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  apiKeyStatus ? "bg-emerald-400" : "bg-amber-400"
                }`}
              />
              {apiKeyStatus ? "DeepSeek connected" : "API key needed"}
              <span className="ml-auto font-mono">
                {foundation.kind === "ready" ? foundation.foundation.doctor.schema_version : "—"}
              </span>
            </div>
          </div>
        </aside>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-white/[0.06] px-4">
          {!sidebarOpen && (
            <button
              type="button"
              aria-label="Open sidebar"
              onClick={() => setSidebarOpen(true)}
              className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
            >
              <PanelLeftOpen size={16} />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <Folder size={12} />
              <span className="truncate">{activeProject?.name ?? "No project"}</span>
              {activeProject !== null && (
                <>
                  <span className="text-slate-500">/</span>
                  <span className="truncate font-mono text-[10px] text-slate-500">
                    {activeProject.path}
                  </span>
                  <RevealButton
                    workspacePath={workspacePath}
                    path={activeProject.path}
                    label="Show project folder"
                  />
                </>
              )}
            </div>
            <h1 className="truncate text-[13px] font-medium text-slate-200">{currentTitle}</h1>
            {activeSession !== null && (
              <p
                className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-slate-500"
                title="Elapsed time since this chat started, and the total time spent running turns"
              >
                <Clock3 size={10} className="shrink-0" />
                <span data-testid="chat-elapsed">
                  {formatDuration(Math.max(0, nowMs - activeSession.created_at))}
                </span>
                <span className="text-slate-500">elapsed</span>
                <span className="text-slate-500">·</span>
                <span data-testid="chat-total">
                  {formatDuration(totalDurationMs + (busy ? runningMs(entries, nowMs) : 0))}
                </span>
                <span className="text-slate-500">total</span>
              </p>
            )}
          </div>
          {sessionUsage.input_tokens + sessionUsage.output_tokens > 0 && (
            <div
              className="shrink-0 text-right text-[10px] leading-4 text-slate-500"
              title="Cumulative tokens and cost for this session"
            >
              <p className="font-mono">
                {formatCompactTokens(sessionUsage.input_tokens + sessionUsage.cache_read_tokens)} in
                · {formatCompactTokens(sessionUsage.output_tokens)} out
              </p>
              <p className="font-mono text-slate-500">
                ${sessionUsage.cost_usd.toFixed(4)} · {cacheHitShare(sessionUsage).toFixed(0)}%
                cached
              </p>
            </div>
          )}
        </header>

        <div
          {...chatDrop.zoneProps}
          className={`relative flex min-h-0 flex-1 flex-col ${
            chatDrop.active ? "ring-2 ring-inset ring-emerald-400/30" : ""
          }`}
        >
          {chatDrop.active && (
            <div className="pointer-events-none absolute inset-0 z-20 flex items-start justify-center bg-emerald-400/[0.04] pt-6 text-[12px] font-medium text-emerald-200">
              Drop files to attach them to the chat
            </div>
          )}
          <div className="relative min-h-0 flex-1">
            <div
              ref={scrollRef}
              onScroll={handleChatScroll}
              onKeyDown={handleChatKeyDown}
              tabIndex={0}
              role="region"
              aria-label="Chat transcript"
              data-testid="chat-scroll"
              className="chat-scroll h-full overflow-y-auto outline-none"
            >
            <div className="mx-auto w-full max-w-3xl space-y-6 px-5 py-6">
              {entries.length === 0 && (
                <div className="pt-16">
                  <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10 text-emerald-300">
                    <Bot size={22} />
                  </div>
                  <h2 className="mt-4 text-center text-lg font-medium tracking-tight text-slate-200">
                    {activeProject === null ? "Open a project to begin" : "What are we building?"}
                  </h2>
                  <p className="mx-auto mt-1 max-w-md text-center text-[12px] leading-5 text-slate-500">
                    {activeProject === null
                      ? "Choose a project directory so the agent has a workspace and a place to keep session context."
                      : "Ask about the codebase, request a change, or run a check. Read and search tools run freely; edits and commands follow the access mode."}
                  </p>
                  {activeProject === null ? (
                    <div className="mt-5 flex justify-center">
                      <button
                        type="button"
                        onClick={() => setProjectDialogOpen(true)}
                        className="flex items-center gap-1.5 rounded-md bg-emerald-400 px-3 py-2 text-[12px] font-medium text-emerald-950 hover:bg-emerald-300"
                      >
                        <Plus size={14} /> New project
                      </button>
                    </div>
                  ) : (
                    <div className="mx-auto mt-5 flex max-w-xl flex-wrap justify-center gap-2">
                      {[
                        "Summarize this repository and its main packages.",
                        "Find the riskiest code in this project.",
                        "Run the tests and triage any failures.",
                      ].map((suggestion) => (
                        <button
                          key={suggestion}
                          type="button"
                          onClick={() => composerRef.current?.setDraft(suggestion)}
                          className="rounded-full border border-white/[0.07] bg-white/[0.02] px-3 py-1.5 text-[11px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {entries.map((entry) => {
                const running =
                  busy &&
                  entry.role === "assistant" &&
                  entry.id ===
                    [...entries].reverse().find((item) => item.role === "assistant")?.id;
                return (
                  <MessageCard
                    key={entry.id}
                    entry={entry}
                    nowMs={running ? nowMs : undefined}
                    workspacePath={workspacePath}
                    onOpenPreview={openPreview}
                    running={running}
                  />
                );
              })}

              {busy && (
                <div className="flex items-center gap-2 text-[12px] text-emerald-300">
                  <Loader2 size={14} className="animate-spin" />
                  Working through the workspace…
                </div>
              )}
            </div>
            </div>
            {showJumpToLatest && (
              <button
                type="button"
                aria-label="Jump to latest"
                onClick={jumpToLatest}
                className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-white/[0.1] bg-[#151921]/95 px-3 py-1.5 text-[11px] font-medium text-slate-200 shadow-lg backdrop-blur hover:bg-[#1b202a]"
              >
                <ArrowDown size={12} /> Jump to latest
              </button>
            )}
          </div>

          <ChatComposer
            ref={composerRef}
            workspacePath={workspacePath}
            hasProject={activeProject !== null}
            busy={busy}
            stopRequested={stopRequested}
            permissionMode={permissionMode}
            model={model}
            thinking={thinking}
            accessNotice={accessNotice}
            error={error}
            queuedMessages={queuedMessages}
            onPermissionMode={changePermissionMode}
            onOpenSettings={composerOpenModel}
            onDismissAccessNotice={dismissAccessNotice}
            onDismissError={dismissError}
            onRemoveQueued={removeQueued}
            onSend={composerSend}
            onSteer={composerSteer}
            onQueue={queue}
            onStop={composerStop}
            onError={composerError}
          />
        </div>
      </div>

      {preview !== null && (
        <PreviewPane
          target={preview}
          workspacePath={workspacePath}
          revision={projectRevision}
          width={previewWidth}
          reserved={720 + (panelOpen ? panelWidth + 4 : 0)}
          onResizeStart={startPreviewResize}
          onClose={() => setPreview(null)}
          onArtifact={openArtifact}
          onWorkspaceChanged={markWorkspaceChanged}
        />
      )}

      {panelOpen && (
        <>
          <div
            role="separator"
            aria-label="Resize project panel"
            aria-orientation="vertical"
            onPointerDown={startPanelResize}
            className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-emerald-400/30"
          />
          <div
            className="flex min-h-0 shrink-0 overflow-hidden"
            style={{
              width: `clamp(320px, ${panelWidth}px, calc(100vw - ${
                380 + (previewOpen ? previewWidth + 4 : 0)
              }px))`,
            }}
          >
            <ProjectPanel
              tab={panelTab}
              onTab={setPanelTab}
              onClose={() => setPanelOpen(false)}
              project={activeProject}
              indexSummary={indexSummary}
              indexing={indexing}
              indexError={indexError}
              onRefreshIndex={() => void refreshWorkspaceIndex()}
              memories={memoryList}
              memoryError={memoryError}
              onForgetMemory={(id) => void forgetMemory(id)}
              checkpoints={checkpointList}
              restoringCheckpoint={restoringCheckpoint}
              checkpointDiffId={checkpointDiffId}
              checkpointDiffText={checkpointDiffText}
              checkpointNotice={checkpointNotice}
              checkpointError={checkpointError}
              onShowDiff={(id) => void showCheckpointDiff(id)}
              onRestore={(id) => void restoreCheckpoint(id)}
              foundation={foundation}
              apiKeyStatus={apiKeyStatus}
              sessionId={sessionId}
              onOpenSettings={openSettings}
              onArtifact={openArtifact}
              onImportBundle={() => void startMergeImport()}
              revision={projectRevision}
              onWorkspaceChanged={markWorkspaceChanged}
            />
          </div>
        </>
      )}

      {accessRequest !== null && (
        <AccessRequestDialog
          request={accessRequest}
          onGrant={() => void grantAccess()}
          onDeny={() => setAccessRequest(null)}
        />
      )}

      {toolApproval !== null && (
        <ToolApprovalDialog
          request={toolApproval}
          onApprove={() => void approveToolAccess()}
          onDeny={() => setToolApproval(null)}
        />
      )}

      {shortcutsOpen && (
        <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />
      )}

      {settingsOpen && (
        <SettingsDialog
          tab={settingsTab}
          onTab={setSettingsTab}
          onClose={() => setSettingsOpen(false)}
          model={model}
          onModel={setModel}
          thinking={thinking}
          onThinking={setThinking}
          reasoningEffort={reasoningEffort}
          onReasoningEffort={setReasoningEffort}
          permissionMode={permissionMode}
          onPermissionMode={changePermissionMode}
          accessGrants={accessGrants}
          onRevokeAccess={(grant) => void revokeAccess(grant)}
          apiKeyStatus={apiKeyStatus}
          apiKeyInput={apiKeyInput}
          onApiKeyInput={setApiKeyInput}
          onSaveApiKey={() => void saveApiKey()}
          onClearApiKey={() => void clearApiKey()}
          activeProject={activeProject}
          projectNameDraft={projectNameDraft}
          onProjectNameDraft={setProjectNameDraft}
          onSaveProjectName={() => void saveProjectName()}
          onRemoveProject={() => void removeProject()}
          onAddProject={() => setProjectDialogOpen(true)}
          foundation={foundation}
        />
      )}

      {commandOpen && (
        <CommandPalette
          onClose={() => setCommandOpen(false)}
          onNewSession={() => {
            setCommandOpen(false);
            newSession();
          }}
          onAddProject={() => {
            setCommandOpen(false);
            setProjectDialogOpen(true);
          }}
          onSettings={(tab) => {
            setCommandOpen(false);
            openSettings(tab);
          }}
          onContext={(tab) => {
            setCommandOpen(false);
            openPanel(tab);
          }}
          onToggleSidebar={() => {
            setCommandOpen(false);
            setSidebarOpen((open) => !open);
          }}
          onExportProject={() => {
            setCommandOpen(false);
            void exportProjectFlow();
          }}
          onImportProject={() => {
            setCommandOpen(false);
            void importProjectFlow();
          }}
        />
      )}

      {transfer !== null && (
        <TransferDialog state={transfer} onClose={() => setTransfer(null)} />
      )}

      {mergeBundle !== null && activeProject !== null && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Merge project bundle"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
        >
          <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl">
            <h2 className="text-[14px] font-medium text-slate-100">
              Merge this bundle into {activeProject.name}?
            </h2>
            <p className="mt-2 text-[11px] leading-5 text-slate-400">
              Files that exist in both places are overwritten, files that only
              exist locally are kept, and imported chats are appended as new
              sessions.
            </p>
            <p className="mt-2 break-all font-mono text-[9px] text-slate-500">
              {mergeBundle}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setMergeBundle(null)}
                className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-slate-200 hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmMergeImport()}
                className="rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300"
              >
                Merge bundle
              </button>
            </div>
          </div>
        </div>
      )}

      {projectDialogOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="New project"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
        >
          <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-medium">New project</h2>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  A project owns one workspace. Name it and OpenBaD creates the
                  folder for you.
                </p>
              </div>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setProjectDialogOpen(false)}
                className="rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300"
              >
                <X size={15} />
              </button>
            </div>
            <label className="mt-4 block text-[10px] uppercase tracking-wider text-slate-500">
              Project name
            </label>
            <input
              autoFocus
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void createProject();
                }
              }}
              aria-label="Project name"
              placeholder="My project"
              className="mt-1 w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[12px] text-slate-200 outline-none focus:border-emerald-400/40"
            />
            <div className="mt-3 rounded-md border border-white/[0.06] bg-black/20 px-2.5 py-2">
              <p className="text-[9px] uppercase tracking-wider text-slate-500">
                Workspace location
              </p>
              <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-500">
                {workspacesDir.length > 0
                  ? `${workspacesDir}/${projectSlugPreview(newProjectName)}`
                  : "Resolving the default workspaces directory…"}
              </p>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setProjectDialogOpen(false)}
                className="rounded-md px-3 py-1.5 text-[11px] text-slate-400 hover:bg-white/5"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void createProject()}
                disabled={newProjectName.trim().length === 0}
                className="rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-30"
              >
                Create project
              </button>
            </div>
          </div>
        </div>
      )}
      </main>
    </div>
  );
}

function ProjectAreaButton({
  icon,
  label,
  active,
  pulse = false,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  active: boolean;
  pulse?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] ${
        active
          ? "bg-emerald-400/10 text-emerald-200"
          : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
      }`}
    >
      {icon}
      <span className="truncate">{label}</span>
      {pulse && (
        <span
          aria-label="Background work running"
          className="ml-auto h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-emerald-400"
        />
      )}
    </button>
  );
}

function SessionRow({
  session,
  active,
  editing,
  draft,
  showProject,
  onOpen,
  onStartRename,
  onDraft,
  onCommit,
  onCancel,
  onArchive,
  onDelete,
  archived,
}: {
  session: SessionSummary;
  active: boolean;
  editing: boolean;
  draft: string;
  showProject: boolean;
  onOpen: () => void;
  onStartRename: () => void;
  onDraft: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  onArchive: () => void;
  onDelete: () => void;
  archived: boolean;
}) {
  if (editing) {
    return (
      <div className="flex items-center gap-1 rounded-md bg-white/[0.06] px-1.5 py-1">
        <input
          autoFocus
          value={draft}
          aria-label="Session title"
          onChange={(event) => onDraft(event.target.value)}
          onBlur={onCommit}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onCommit();
            } else if (event.key === "Escape") {
              onCancel();
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-200 outline-none"
        />
        <button type="button" aria-label="Save name" onClick={onCommit}>
          <Check size={12} className="text-emerald-300" />
        </button>
      </div>
    );
  }
  return (
    <div
      className={`group flex items-center gap-1.5 rounded-md px-1.5 py-1.5 text-[11px] ${
        active ? "bg-emerald-400/10 text-emerald-200" : "text-slate-400 hover:bg-white/[0.04]"
      }`}
    >
      <button type="button" onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
        <MessageSquare size={12} className="shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1 truncate">{session.title ?? "Untitled session"}</span>
        {showProject && (
          <span className="max-w-[80px] truncate rounded bg-white/[0.05] px-1 py-0.5 text-[9px] text-slate-500">
            {session.workspace_path?.split("/").filter(Boolean).at(-1) ?? "project"}
          </span>
        )}
        <span className="shrink-0 font-mono text-[9px] text-slate-500">
          {formatRelativeTime(session.updated_at)}
        </span>
      </button>
      <button
        type="button"
        aria-label="Rename session"
        onClick={(event) => {
          event.stopPropagation();
          onStartRename();
        }}
        className="hidden shrink-0 rounded p-0.5 text-slate-500 hover:text-slate-300 group-hover:block"
      >
        <Pencil size={11} />
      </button>
      <button
        type="button"
        aria-label={archived ? "Restore session" : "Archive session"}
        title={archived ? "Restore session" : "Archive session"}
        onClick={(event) => {
          event.stopPropagation();
          onArchive();
        }}
        className="hidden shrink-0 rounded p-0.5 text-slate-500 hover:text-slate-300 group-hover:block"
      >
        {archived ? <ArchiveRestore size={11} /> : <Archive size={11} />}
      </button>
      <button
        type="button"
        aria-label="Delete session"
        title="Delete session"
        onClick={(event) => {
          event.stopPropagation();
          onDelete();
        }}
        className="hidden shrink-0 rounded p-0.5 text-slate-500 hover:text-rose-300 group-hover:block"
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}

function CopyMessageButton({
  text,
  label,
  showLabel = false,
}: {
  text: string;
  label: string;
  showLabel?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      onClick={() => {
        if (!navigator.clipboard) {
          return;
        }
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1_500);
          })
          .catch(() => undefined);
      }}
      className={
        showLabel
          ? "inline-flex items-center gap-1 rounded border border-white/[0.06] px-1.5 py-1 text-[10px] text-slate-500 hover:border-white/[0.12] hover:text-slate-300"
          : "ml-1 rounded p-0.5 text-slate-500 hover:text-slate-300 focus-visible:text-slate-300"
      }
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {showLabel && <span>{copied ? "Copied" : label}</span>}
    </button>
  );
}

function appendStepText(
  current: string,
  incoming: string,
  previousStep: number | undefined,
  step: number,
): string {
  if (current.length === 0) {
    return incoming;
  }
  if (previousStep === undefined || previousStep === step) {
    return `${current}${incoming}`;
  }
  const separator = current.endsWith("\n\n")
    ? ""
    : current.endsWith("\n")
      ? "\n"
      : "\n\n";
  return `${current}${separator}${incoming}`;
}

type TransferState =
  | { phase: "running"; label: string }
  | {
      phase: "done";
      title: string;
      report: ProjectExportReport | ProjectImportReport;
    }
  | { phase: "error"; title: string; message: string };

function TransferDialog({
  state,
  onClose,
}: {
  state: TransferState;
  onClose: () => void;
}) {
  const done = state.phase === "done";
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={done ? state.title : "Project transfer"}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl">
        <div className="flex items-start gap-2">
          {state.phase === "running" && (
            <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-emerald-300" />
          )}
          {state.phase === "done" && (
            <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-300" />
          )}
          {state.phase === "error" && (
            <CircleAlert size={16} className="mt-0.5 shrink-0 text-rose-300" />
          )}
          <div className="min-w-0">
            <h2 className="text-[14px] font-medium text-slate-100">
              {state.phase === "running" ? "Project transfer" : state.title}
            </h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">
              {state.phase === "running" && state.label}
              {state.phase === "error" && state.message}
              {state.phase === "done" && (
                <>
                  {state.report.files} files · {state.report.sessions} chats ·{" "}
                  {state.report.knowledge_documents} knowledge docs ·{" "}
                  {state.report.memories} memories ·{" "}
                  {state.report.checkpoints} checkpoints
                </>
              )}
            </p>
            {done && state.report.warnings.length > 0 && (
              <ul className="mt-2 space-y-1 text-[10px] leading-4 text-amber-300/90">
                {state.report.warnings.map((warning) => (
                  <li key={warning}>• {warning}</li>
                ))}
              </ul>
            )}
            {done && (
              <p className="mt-2 break-all font-mono text-[9px] text-slate-500">
                {state.report.path}
              </p>
            )}
          </div>
        </div>
        {state.phase !== "running" && (
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-slate-200 hover:bg-white/10"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const MessageCard = memo(function MessageCard({
  entry,
  nowMs,
  running = false,
  workspacePath,
  onOpenPreview,
}: {
  entry: ChatEntry;
  nowMs?: number;
  running?: boolean;
  workspacePath?: string;
  onOpenPreview?: (target: PreviewTarget) => void;
}) {
  const preview = entry.preview;
  if (entry.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl border border-emerald-400/15 bg-emerald-400/[0.06] px-4 py-3 text-[13px] leading-6 text-slate-100">
          {entry.steering && (
            <p className="mb-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-300/80">
              Steering
            </p>
          )}
          {entry.attachments !== undefined && entry.attachments.length > 0 && (
            <div className="mb-1 flex flex-wrap justify-end gap-1">
              {entry.attachments.map((attachment, index) => (
                <span
                  key={`${attachment.name}-${index}`}
                  className="flex items-center gap-1 rounded border border-emerald-400/20 bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-100"
                >
                  <button
                    type="button"
                    title={
                      attachment.path === undefined
                        ? attachment.name
                        : `Preview ${attachment.path}`
                    }
                    disabled={attachment.path === undefined || onOpenPreview === undefined}
                    onClick={() =>
                      attachment.path !== undefined &&
                      onOpenPreview?.({
                        kind: "artifact",
                        artifact: {
                          kind: "file",
                          title: attachment.name,
                          uri: attachment.path,
                        },
                      })
                    }
                    className="flex min-w-0 items-center gap-1 disabled:cursor-default"
                  >
                    <FileText size={10} className="shrink-0" />
                    <span className="max-w-[180px] truncate">{attachment.name}</span>
                  </button>
                  {attachment.path !== undefined &&
                    workspacePath !== undefined &&
                    workspacePath.length > 0 && (
                      <RevealButton
                        workspacePath={workspacePath}
                        path={attachment.path}
                        label="Show file in file manager"
                      />
                    )}
                </span>
              ))}
            </div>
          )}
          {entry.text.length > 0 && <span className="whitespace-pre-wrap">{entry.text}</span>}
          {entry.text.length > 0 && (
            <div className="mt-2 flex justify-end">
              <CopyMessageButton text={entry.text} label="Copy message" showLabel />
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="group/message flex gap-3">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-white/[0.08] bg-white/[0.03] text-slate-400">
        <Bot size={13} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          <span className="font-medium text-slate-400">OpenBaD</span>
          {entry.status !== undefined && <span>· {entry.status}</span>}
          {running && entry.startedAt !== undefined && nowMs !== undefined && (
            <span className="font-mono text-slate-500" data-testid="turn-running">
              · {formatDuration(Math.max(0, nowMs - entry.startedAt))}
            </span>
          )}
          {!running && entry.durationMs !== undefined && (
            <span className="font-mono text-slate-500" data-testid="turn-duration">
              · {formatDuration(entry.durationMs)}
            </span>
          )}
          {entry.text.length > 0 && (
            <CopyMessageButton text={entry.text} label="Copy response" />
          )}
        </div>
        {entry.reasoning !== undefined && entry.reasoning.length > 0 && (
          <details className="mt-2 rounded-md border border-white/[0.05] bg-black/20 px-3 py-2">
            <summary className="cursor-pointer text-[11px] text-slate-500">Reasoning</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-4 text-slate-500">
              {entry.reasoning}
            </pre>
          </details>
        )}
        {entry.text.length > 0 && (
          <div className="mt-2 text-[13px] leading-6 text-slate-200">
            <MarkdownMessage text={entry.text} />
          </div>
        )}
        {entry.tools !== undefined && entry.tools.length > 0 && (
          <details className="mt-3 rounded-md border border-white/[0.05] bg-black/20 px-3 py-2">
            <summary className="flex cursor-pointer items-center gap-1 text-[11px] text-slate-500">
              <Wrench size={11} /> {entry.tools.length} tool event
              {entry.tools.length === 1 ? "" : "s"}
            </summary>
            <div className="mt-2 space-y-1.5">
              {entry.tools.map((tool, index) => (
                <pre
                  key={`${entry.id}-tool-${index}`}
                  className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 font-mono text-[10px] leading-4 text-slate-500"
                >
                  {tool}
                </pre>
              ))}
            </div>
          </details>
        )}
        {preview !== undefined && onOpenPreview !== undefined && (
          <button
            type="button"
            onClick={() => onOpenPreview(preview)}
            className="mt-2 inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-slate-400 transition-colors hover:border-emerald-400/25 hover:text-emerald-200"
          >
            <Eye size={11} />
            {preview.kind === "git" ? "Open source control" : "Reopen preview"}
          </button>
        )}
        {entry.usage !== undefined && (
          <p className="mt-2 font-mono text-[10px] text-slate-500">
            {entry.usage.input_tokens.toLocaleString()} in ·{" "}
            {entry.usage.cache_read_tokens.toLocaleString()} cached (
            {cacheHitShare(entry.usage).toFixed(0)}%) · {entry.usage.output_tokens.toLocaleString()}{" "}
            out · ${entry.usage.cost_usd.toFixed(4)}
          </p>
        )}
        {entry.text.length > 0 && (
          <div className="mt-2 flex justify-end">
            <CopyMessageButton text={entry.text} label="Copy response" showLabel />
          </div>
        )}
      </div>
    </div>
  );
});

function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const shortcuts = [
    ["Command menu", "Ctrl K"],
    ["New session", "Ctrl N"],
    ["Toggle sidebar", "Ctrl B"],
    ["Settings", "Ctrl ,"],
    ["Send message", "Enter"],
    ["New line", "Shift Enter"],
    ["Close menu or dialog", "Esc"],
  ];
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-[14px] font-medium">Keyboard shortcuts</h2>
            <p className="mt-0.5 text-[11px] text-slate-500">
              Menus carry project actions; the canvas stays yours.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close keyboard shortcuts"
            onClick={onClose}
            className="rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300"
          >
            <X size={15} />
          </button>
        </div>
        <div className="mt-4 space-y-1">
          {shortcuts.map(([label, keys]) => (
            <div
              key={label}
              className="flex items-center justify-between rounded-md px-2 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.03]"
            >
              <span>{label}</span>
              <span className="rounded border border-white/[0.08] bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-slate-500">
                {keys}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SettingsDialog({
  tab,
  onTab,
  onClose,
  model,
  onModel,
  thinking,
  onThinking,
  reasoningEffort,
  onReasoningEffort,
  permissionMode,
  onPermissionMode,
  accessGrants,
  onRevokeAccess,
  apiKeyStatus,
  apiKeyInput,
  onApiKeyInput,
  onSaveApiKey,
  onClearApiKey,
  activeProject,
  projectNameDraft,
  onProjectNameDraft,
  onSaveProjectName,
  onRemoveProject,
  onAddProject,
  foundation,
}: {
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  onClose: () => void;
  model: string;
  onModel: (value: string) => void;
  thinking: boolean;
  onThinking: (value: boolean) => void;
  reasoningEffort: string;
  onReasoningEffort: (value: string) => void;
  permissionMode: PermissionMode;
  onPermissionMode: (value: PermissionMode) => void;
  accessGrants: OutsideAccessGrant[];
  onRevokeAccess: (grant: OutsideAccessGrant) => void;
  apiKeyStatus: boolean;
  apiKeyInput: string;
  onApiKeyInput: (value: string) => void;
  onSaveApiKey: () => void;
  onClearApiKey: () => void;
  activeProject: WorkspaceSummary | null;
  projectNameDraft: string;
  onProjectNameDraft: (value: string) => void;
  onSaveProjectName: () => void;
  onRemoveProject: () => void;
  onAddProject: () => void;
  foundation: FoundationState;
}) {
  const tabs: { id: SettingsTab; label: string }[] = [
    { id: "model", label: "Model" },
    { id: "access", label: "Access" },
    { id: "secrets", label: "Secrets" },
    { id: "mcp", label: "MCP" },
    { id: "project", label: "Project" },
    { id: "services", label: "Services" },
    { id: "about", label: "About" },
  ];
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
    >
      <div className="flex h-[min(640px,80vh)] w-full max-w-3xl overflow-hidden rounded-xl border border-white/10 bg-[#1f2833] shadow-2xl">
        <div className="w-[180px] shrink-0 border-r border-white/[0.06] bg-black/10 p-3">
          <div className="flex items-center justify-between px-1">
            <p className="text-[12px] font-medium text-slate-300">Settings</p>
            <button
              type="button"
              aria-label="Close settings"
              onClick={onClose}
              className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-slate-300"
            >
              <X size={14} />
            </button>
          </div>
          <div className="mt-4 space-y-0.5">
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onTab(item.id)}
                className={`w-full rounded-md px-2 py-1.5 text-left text-[11px] ${
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

        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          {tab === "model" && (
            <div className="max-w-md space-y-4">
              <Section title="Model route" description="Changes start a new cache series.">
                <select
                  value={model}
                  aria-label="Model route"
                  onChange={(event) => onModel(event.target.value)}
                  className="w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[12px] text-slate-200 outline-none focus:border-emerald-400/40"
                >
                  <option value="deepseek-flash">deepseek-flash · fast</option>
                  <option value="deepseek-v4-pro">deepseek-v4-pro · reasoning</option>
                </select>
              </Section>
              <Section title="Thinking" description="Reasoning effort for this route.">
                <label className="flex items-center justify-between text-[12px] text-slate-300">
                  <span>Enable thinking</span>
                  <input
                    type="checkbox"
                    checked={thinking}
                    onChange={(event) => onThinking(event.target.checked)}
                    className="accent-emerald-400"
                  />
                </label>
                <select
                  value={reasoningEffort}
                  aria-label="Reasoning effort"
                  onChange={(event) => onReasoningEffort(event.target.value)}
                  disabled={!thinking}
                  className="mt-3 w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[12px] text-slate-200 outline-none focus:border-emerald-400/40 disabled:opacity-40"
                >
                  <option value="low">low effort</option>
                  <option value="high">high effort</option>
                  <option value="max">max effort</option>
                </select>
              </Section>
            </div>
          )}

          {tab === "access" && (
            <div className="max-w-lg space-y-3">
              {(
                [
                  ["read", "Read only", "Inspect files, search, and read Git state. No mutations."],
                  ["edit", "Can edit", "Write and patch files. Commands remain blocked."],
                  ["full", "Full access", "Edit files and run commands. Use for tests and builds."],
                ] as [PermissionMode, string, string][]
              ).map(([id, label, description]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => onPermissionMode(id)}
                  className={`w-full rounded-lg border p-3 text-left ${
                    permissionMode === id
                      ? "border-emerald-400/30 bg-emerald-400/[0.06]"
                      : "border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.04]"
                  }`}
                >
                  <div className="flex items-center gap-2 text-[12px] text-slate-200">
                    <Shield size={14} className={permissionMode === id ? "text-emerald-300" : "text-slate-500"} />
                    {label}
                    {permissionMode === id && <Check size={13} className="ml-auto text-emerald-300" />}
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-slate-500">{description}</p>
                </button>
              ))}
              <p className="px-1 text-[11px] leading-5 text-slate-500">
                Access mode is remembered per session, so returning to a session restores
                the mode it last used. New sessions start read-only.
              </p>
              <div className="pt-2">
                <p className="px-1 pb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                  Outside workspace
                </p>
                {accessGrants.length === 0 ? (
                  <p className="px-1 text-[11px] leading-5 text-slate-500">
                    No external filesystem grants. The assistant can ask for one when a
                    task needs a path outside this project.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {accessGrants.map((grant) => (
                      <div
                        key={grant.id}
                        className="flex items-start gap-2 rounded-md border border-white/[0.07] bg-white/[0.02] p-2.5"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="break-all font-mono text-[10px] text-slate-300">
                            {grant.path}
                          </p>
                          <p className="mt-0.5 text-[9px] uppercase tracking-wide text-slate-500">
                            {grant.write_access ? "read and write" : "read only"}
                          </p>
                          {grant.reason.length > 0 && (
                            <p className="mt-1 text-[10px] leading-4 text-slate-500">
                              {grant.reason}
                            </p>
                          )}
                        </div>
                        <button
                          type="button"
                          aria-label={`Revoke access to ${grant.path}`}
                          onClick={() => onRevokeAccess(grant)}
                          className="rounded p-1 text-slate-500 hover:bg-white/5 hover:text-amber-300"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === "secrets" && (
            <div className="max-w-md space-y-4">
              <Section title="DeepSeek API key" description={apiKeyStatus ? "A key is stored in the OS keychain." : "No key is stored."}>
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={apiKeyInput}
                    onChange={(event) => onApiKeyInput(event.target.value)}
                    placeholder="sk-…"
                    className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2.5 py-2 font-mono text-[11px] text-slate-200 outline-none focus:border-emerald-400/40"
                  />
                  <button
                    type="button"
                    onClick={onSaveApiKey}
                    disabled={apiKeyInput.trim().length === 0}
                    className="rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-30"
                  >
                    Save
                  </button>
                </div>
                {apiKeyStatus && (
                  <button
                    type="button"
                    onClick={onClearApiKey}
                    className="mt-3 text-[11px] text-amber-300 hover:underline"
                  >
                    Remove stored key
                  </button>
                )}
              </Section>
            </div>
          )}

          {tab === "services" && <ServicesSettings />}
          {tab === "mcp" && (
            <McpSettings workspacePath={activeProject?.path ?? ""} />
          )}

          {tab === "project" && (
            <div className="max-w-md space-y-4">
              <Section title="Active project" description={activeProject?.path ?? "No project selected."}>
                <div className="flex gap-2">
                  <input
                    value={projectNameDraft}
                    aria-label="Project display name"
                    onChange={(event) => onProjectNameDraft(event.target.value)}
                    className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[12px] text-slate-200 outline-none focus:border-emerald-400/40"
                  />
                  <button
                    type="button"
                    onClick={onSaveProjectName}
                    className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-slate-200 hover:bg-white/10"
                  >
                    Rename
                  </button>
                </div>
              </Section>
              <button
                type="button"
                onClick={onAddProject}
                className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] text-slate-300 hover:bg-white/[0.06]"
              >
                <Plus size={13} /> Create another project
              </button>
              <button
                type="button"
                onClick={onRemoveProject}
                disabled={activeProject === null}
                className="flex items-center gap-1.5 rounded-md border border-amber-400/20 bg-amber-400/[0.04] px-3 py-2 text-[11px] text-amber-300 hover:bg-amber-400/10 disabled:opacity-30"
              >
                <Trash2 size={13} /> Remove project
              </button>
            </div>
          )}

          {tab === "about" && (
            <div className="max-w-md space-y-3">
              <Section title="OpenBaD Desktop" description="A DeepSeek-first coding harness.">
                <div className="space-y-1 font-mono text-[11px] text-slate-400">
                  <p>version {foundation.kind === "ready" ? foundation.foundation.app_version : "—"}</p>
                  <p>schema {foundation.kind === "ready" ? foundation.foundation.doctor.schema_version : "—"}</p>
                  <p>integrity {foundation.kind === "ready" ? foundation.foundation.doctor.integrity : "—"}</p>
                  <p>events {foundation.kind === "ready" ? foundation.foundation.doctor.event_count.toLocaleString() : "—"}</p>
                  <p>
                    audit{" "}
                    {foundation.kind === "ready"
                      ? `${foundation.foundation.audit.valid ? "verified" : "failed"} · ${foundation.foundation.audit.records} records`
                      : "—"}
                  </p>
                </div>
              </Section>
              <Section
                title="Updates"
                description="Check GitHub releases and install the newest build from the app."
              >
                <UpdatePanel
                  currentVersion={
                    foundation.kind === "ready"
                      ? foundation.foundation.app_version
                      : null
                  }
                />
              </Section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CommandPalette({
  onClose,
  onNewSession,
  onAddProject,
  onSettings,
  onContext,
  onToggleSidebar,
  onExportProject,
  onImportProject,
}: {
  onClose: () => void;
  onNewSession: () => void;
  onAddProject: () => void;
  onSettings: (tab: SettingsTab) => void;
  onContext: (tab: PanelTab) => void;
  onToggleSidebar: () => void;
  onExportProject: () => void;
  onImportProject: () => void;
}) {
  const [query, setQuery] = useState("");
  const actions = [
    { label: "New session", hint: "Ctrl N", run: onNewSession },
    { label: "New project", hint: "", run: onAddProject },
    { label: "Toggle sidebar", hint: "", run: onToggleSidebar },
    { label: "Export project", hint: "", run: onExportProject },
    { label: "Import project", hint: "", run: onImportProject },
    { label: "Settings: Model", hint: "", run: () => onSettings("model") },
    { label: "Settings: Access", hint: "", run: () => onSettings("access") },
    { label: "Settings: Secrets", hint: "", run: () => onSettings("secrets") },
    { label: "Settings: MCP servers", hint: "", run: () => onSettings("mcp") },
    { label: "Settings: Project", hint: "", run: () => onSettings("project") },
    { label: "Check for updates", hint: "", run: () => onSettings("about") },
    { label: "Project: Overview", hint: "", run: () => onContext("overview") },
    { label: "Project: Workspace", hint: "", run: () => onContext("workspace") },
    { label: "Project: Agents", hint: "", run: () => onContext("agents") },
    { label: "Project: References", hint: "", run: () => onContext("repos") },
    { label: "Project: Knowledge", hint: "", run: () => onContext("knowledge") },
    { label: "Project: Retrieval", hint: "", run: () => onContext("retrieval") },
    { label: "Project: Memory", hint: "", run: () => onContext("memory") },
    { label: "Project: Checkpoints", hint: "", run: () => onContext("checkpoints") },
    { label: "Project: Diagnostics", hint: "", run: () => onContext("diagnostics") },
  ];
  const needle = query.trim().toLowerCase();
  const filtered =
    needle.length === 0
      ? actions
      : actions.filter((action) => action.label.toLowerCase().includes(needle));

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command menu"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-[15vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-xl border border-white/10 bg-[#1f2833] shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2.5">
          <Search size={14} className="text-slate-500" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                onClose();
              }
            }}
            aria-label="Command search"
            placeholder="Type a command…"
            className="min-w-0 flex-1 bg-transparent text-[12px] text-slate-200 outline-none placeholder:text-slate-500"
          />
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 font-mono text-[9px] text-slate-500">
            esc
          </span>
        </div>
        <div className="max-h-80 overflow-y-auto p-1.5">
          {filtered.length === 0 && (
            <p className="px-2 py-3 text-[11px] text-slate-500">No matching commands.</p>
          )}
          {filtered.map((action) => (
            <button
              key={action.label}
              type="button"
              onClick={action.run}
              className="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-[12px] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100"
            >
              <span>{action.label}</span>
              {action.hint !== "" && (
                <span className="font-mono text-[9px] text-slate-500">{action.hint}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
      <h3 className="text-[12px] font-medium text-slate-200">{title}</h3>
      {description !== undefined && (
        <p className="mt-0.5 text-[11px] leading-5 text-slate-500">{description}</p>
      )}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function rememberedProjectPath(): string | null {
  try {
    return localStorage.getItem("openbad.activeProjectPath");
  } catch {
    return null;
  }
}

function rememberProjectPath(path: string): void {
  try {
    localStorage.setItem("openbad.activeProjectPath", path);
  } catch {
    // Storage is optional in hardened webviews.
  }
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

function formatClock(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRelativeTime(timestamp: number): string {
  const delta = Date.now() - timestamp;
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) {
    return "now";
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function formatCompactTokens(tokens: number): string {
  if (tokens < 1_000) {
    return `${tokens}`;
  }
  return `${(tokens / 1_000).toFixed(tokens < 10_000 ? 1 : 0)}k`;
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


function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`;
}

function runningMs(entries: ChatEntry[], nowMs: number): number {
  const last = [...entries].reverse().find((entry) => entry.role === "assistant");
  if (last === undefined || last.startedAt === undefined || last.durationMs !== undefined) {
    return 0;
  }
  return Math.max(0, nowMs - last.startedAt);
}

function artifactRecord(metadata: unknown): Record<string, unknown> | null {
  if (typeof metadata !== "object" || metadata === null) {
    return null;
  }
  const value = (metadata as Record<string, unknown>).openbad_artifact;
  if (typeof value !== "object" || value === null) {
    return null;
  }
  return value as Record<string, unknown>;
}

function artifactFromRecord(record: Record<string, unknown>): Artifact | null {
  const kind = record.kind;
  const title = record.title;
  if (
    typeof kind !== "string" ||
    !["web", "image", "markdown", "diff", "file"].includes(kind) ||
    typeof title !== "string"
  ) {
    return null;
  }
  return {
    kind: kind as Artifact["kind"],
    title,
    uri: typeof record.uri === "string" ? record.uri : undefined,
    content: typeof record.content === "string" ? record.content : undefined,
    language: typeof record.language === "string" ? record.language : undefined,
  };
}

function previewTargetFromMetadata(
  metadata: unknown,
  workspacePath: string,
): PreviewTarget | null {
  const record = artifactRecord(metadata);
  if (record === null) {
    return null;
  }
  if (record.kind === "git") {
    const title =
      typeof record.title === "string" && record.title.trim().length > 0
        ? record.title.trim()
        : "Repository";
    const uri = typeof record.uri === "string" ? record.uri.trim() : "";
    return {
      kind: "git",
      workspacePath,
      repoPath:
        uri.length === 0
          ? workspacePath
          : resolvePreviewRepoPath(workspacePath, uri),
      repoName: title,
    };
  }
  const artifact = artifactFromRecord(record);
  return artifact === null ? null : { kind: "artifact", artifact };
}

function resolvePreviewRepoPath(workspacePath: string, repoPath: string): string {
  if (
    repoPath.startsWith("/") ||
    repoPath.startsWith("\\\\") ||
    /^[A-Za-z]:[\\/]/.test(repoPath)
  ) {
    return repoPath;
  }
  const base = workspacePath.replace(/[\\/]+$/, "");
  return base.length === 0
    ? repoPath
    : `${base}/${repoPath.replace(/^[\\/]+/, "")}`;
}


function accessRequestFromMetadata(metadata: unknown): OutsideAccessRequest | null {
  if (typeof metadata !== "object" || metadata === null) {
    return null;
  }
  const value = (metadata as Record<string, unknown>).openbad_access_request;
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.path !== "string" || typeof record.reason !== "string") {
    return null;
  }
  return {
    path: record.path,
    write: record.write === true,
    reason: record.reason,
  };
}

function toolApprovalFromMetadata(metadata: unknown): ToolApprovalRequest | null {
  if (typeof metadata !== "object" || metadata === null) {
    return null;
  }
  const value = (metadata as Record<string, unknown>).openbad_approval;
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.tool !== "string" ||
    typeof record.risk !== "string" ||
    typeof record.reason !== "string"
  ) {
    return null;
  }
  return { tool: record.tool, risk: record.risk, reason: record.reason };
}

function ToolApprovalDialog({
  request,
  onApprove,
  onDeny,
}: {
  request: ToolApprovalRequest;
  onApprove: () => void;
  onDeny: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Tool approval request"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-amber-400/20 bg-amber-400/10 text-amber-300">
            <Shield size={16} />
          </div>
          <div>
            <h2 className="text-[14px] font-medium">Approve this tool?</h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              {request.reason}. Approval applies to this session.
            </p>
          </div>
        </div>
        <div className="mt-4 flex items-center gap-2 rounded-md border border-white/[0.07] bg-black/30 px-2.5 py-2">
          <span className="font-mono text-[11px] text-slate-300">{request.tool}</span>
          <span className="ml-auto rounded border border-amber-400/20 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-amber-300">
            {request.risk}
          </span>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onDeny}
            className="rounded-md border border-white/10 px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/5"
          >
            Deny
          </button>
          <button
            type="button"
            onClick={onApprove}
            className="rounded-md border border-emerald-400/30 bg-emerald-400/15 px-3 py-1.5 text-[11px] font-medium text-emerald-200 hover:bg-emerald-400/20"
          >
            Approve for session
          </button>
        </div>
      </div>
    </div>
  );
}

function AccessRequestDialog({
  request,
  onGrant,
  onDeny,
}: {
  request: OutsideAccessRequest;
  onGrant: () => void;
  onDeny: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Filesystem access request"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#1f2833] p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-amber-400/20 bg-amber-400/10 text-amber-300">
            <Shield size={16} />
          </div>
          <div>
            <h2 className="text-[14px] font-medium">Allow access outside the workspace?</h2>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              OpenBaD asked for {request.write ? "read and write" : "read-only"} access.
              The grant applies to this project until you revoke it.
              {request.write &&
                " Write access also requires the session access mode to be Can edit or Full access."}
            </p>
          </div>
        </div>
        <p className="mt-4 break-all rounded-md border border-white/[0.07] bg-black/30 px-2.5 py-2 font-mono text-[10px] text-slate-300">
          {request.path}
        </p>
        <p className="mt-2 text-[11px] leading-5 text-slate-500">{request.reason}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onDeny}
            className="rounded-md border border-white/10 px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/5"
          >
            Deny
          </button>
          <button
            type="button"
            onClick={onGrant}
            className="rounded-md border border-emerald-400/30 bg-emerald-400/15 px-3 py-1.5 text-[11px] font-medium text-emerald-200 hover:bg-emerald-400/20"
          >
            Grant access
          </button>
        </div>
      </div>
    </div>
  );
}

function projectSlugPreview(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return slug.length > 0 ? slug : "project";
}
