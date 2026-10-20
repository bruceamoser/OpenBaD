import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Database,
  Folder,
  FolderOpen,
  KeyRound,
  Loader2,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
  Pencil,
  Plus,
  Search,
  Send,
  Settings,
  Shield,
  Square,
  Trash2,
  Wrench,
  X,
} from "lucide-react";

import {
  addProject as addStoredProject,
  apiKeyStatus as fetchApiKeyStatus,
  cancelChat,
  chat as sendChat,
  checkpointDiff as fetchCheckpointDiff,
  clearApiKey as clearStoredApiKey,
  forgetMemory as forgetStoredMemory,
  listProjects as fetchProjects,
  loadCheckpoints as fetchCheckpoints,
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
  searchSessions as searchStoredSessions,
  workspaceDefault,
  workspaceIndexStatus as fetchIndexStatus,
} from "./backend";
import type {
  ChatEntry,
  FileCheckpointSummary,
  FoundationState,
  MemoryRecord,
  SessionSummary,
  WorkspaceIndexSummary,
  WorkspaceSummary,
} from "./types";

type Preferences = {
  model: string;
  thinking: boolean;
  reasoningEffort: string;
};

type PermissionMode = "read" | "edit" | "full";
type ContextTab = "index" | "memory" | "checkpoints" | "diagnostics";
type SettingsTab = "model" | "access" | "secrets" | "project" | "about";

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

export default function App() {
  const [preferences] = useState(loadPreferences);
  const [foundation, setFoundation] = useState<FoundationState>({ kind: "loading" });
  const [projects, setProjects] = useState<WorkspaceSummary[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [workspacePath, setWorkspacePath] = useState("");
  const [message, setMessage] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
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
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextTab, setContextTab] = useState<ContextTab>("index");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("model");
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectPath, setNewProjectPath] = useState("");
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [sessionTitleDraft, setSessionTitleDraft] = useState("");
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const followRef = useRef(true);

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [activeProjectId, projects],
  );
  const activeSession = useMemo(
    () => sessions.find((session) => session.id === sessionId) ?? null,
    [sessionId, sessions],
  );
  const currentTitle = activeSession?.title ?? (sessionId === null ? "New session" : "Session");
  const allowWrites = permissionMode !== "read";
  const allowCommands = permissionMode === "full";

  const loadFoundation = useCallback(async () => {
    try {
      setFoundation({ kind: "ready", foundation: await fetchFoundation() });
    } catch (cause) {
      setFoundation({ kind: "error", message: describeError(cause) });
    }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await fetchProjects());
    } catch (cause) {
      setError(describeError(cause));
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await fetchSessions());
    } catch {
      setSessions([]);
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
    })();
    return () => {
      active = false;
    };
  }, [loadFoundation, loadSessions]);

  useEffect(() => {
    const query = sessionQuery.trim();
    if (query.length === 0) {
      void loadSessions();
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

  useEffect(() => {
    const element = scrollRef.current;
    if (element !== null && followRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [entries, busy]);

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

  const handleChatScroll = useCallback(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    followRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 96;
  }, []);

  const selectProject = useCallback((project: WorkspaceSummary) => {
    setActiveProjectId(project.id);
    setWorkspacePath(project.path);
    setProjectNameDraft(project.name);
    rememberProjectPath(project.path);
    setSessionId(null);
    setEntries([]);
    setError(null);
    followRef.current = true;
  }, []);

  const newSession = useCallback(() => {
    setSessionId(null);
    setEntries([]);
    setError(null);
    followRef.current = true;
    composerRef.current?.focus();
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
    setStopRequested(true);
    try {
      await cancelChat();
    } catch (cause) {
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
        void refreshWorkspaceIndex();
      } catch (cause) {
        setCheckpointError(describeError(cause));
      } finally {
        setRestoringCheckpoint(null);
      }
    },
    [checkpointDiffId, refreshWorkspaceIndex],
  );

  const send = useCallback(async () => {
    const text = message.trim();
    if (text.length === 0 || busy || workspacePath.length === 0) {
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
      void loadProjects();
      void loadMemories();
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
    loadMemories,
    loadProjects,
    loadSessions,
    message,
    model,
    reasoningEffort,
    sessionId,
    thinking,
    workspacePath,
  ]);

  const openSession = useCallback(
    async (session: SessionSummary) => {
      setError(null);
      try {
        const view = await fetchSession(session.id);
        setSessionId(view.session_id);
        if (session.workspace_id !== null) {
          const project = projects.find((item) => item.id === session.workspace_id);
          if (project !== undefined) {
            setActiveProjectId(project.id);
            setWorkspacePath(project.path);
            rememberProjectPath(project.path);
          } else if (session.workspace_path !== null) {
            setWorkspacePath(session.workspace_path);
          }
        }
        followRef.current = true;
        setEntries(
          view.messages.map((item) => ({
            id: nextId(),
            role: item.role === "user" ? "user" : "assistant",
            text: item.text,
            reasoning: item.reasoning ?? "",
          })),
        );
      } catch (cause) {
        setError(describeError(cause));
      }
    },
    [projects],
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
      }
      setSettingsOpen(false);
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [activeProject, projects, selectProject]);

  const createProject = useCallback(async () => {
    const path = newProjectPath.trim();
    if (path.length === 0) {
      return;
    }
    try {
      const project = await addStoredProject(path, newProjectName.trim() || undefined);
      await loadProjects();
      setProjects((items) =>
        items.some((item) => item.id === project.id) ? items : [project, ...items],
      );
      selectProject(project);
      setProjectDialogOpen(false);
      setNewProjectName("");
      setNewProjectPath("");
    } catch (cause) {
      setError(describeError(cause));
    }
  }, [loadProjects, newProjectName, newProjectPath, selectProject]);

  const openSettings = useCallback((tab: SettingsTab) => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  }, []);

  const openContext = useCallback((tab: ContextTab) => {
    setContextTab(tab);
    setContextOpen(true);
  }, []);

  const projectSessions = useMemo(
    () =>
      sessions.filter(
        (session) => activeProjectId !== null && session.workspace_id === activeProjectId,
      ),
    [activeProjectId, sessions],
  );
  const visibleSessions = sessionQuery.trim().length > 0 ? sessions : projectSessions;

  return (
    <main className="flex h-screen overflow-hidden bg-[#0a0d11] text-slate-100">
      {sidebarOpen && (
        <aside className="flex w-[276px] shrink-0 flex-col border-r border-white/[0.07] bg-[#0c1015]">
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
              <Search size={13} className="shrink-0 text-slate-600" />
              <input
                type="search"
                value={sessionQuery}
                onChange={(event) => setSessionQuery(event.target.value)}
                placeholder="Search sessions"
                aria-label="Search sessions"
                className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-300 outline-none placeholder:text-slate-600"
              />
            </label>
          </div>

          <div className="flex-1 overflow-y-auto px-2 pb-3">
            <div className="mb-1 flex items-center justify-between px-2 py-1">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">
                Projects
              </p>
              <button
                type="button"
                aria-label="Add project"
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
                      <ChevronRight size={13} className="shrink-0 text-slate-600" />
                    )}
                    {isActive ? (
                      <FolderOpen size={14} className="shrink-0 text-emerald-300" />
                    ) : (
                      <Folder size={14} className="shrink-0 text-slate-500" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    <span className="font-mono text-[10px] text-slate-600">
                      {project.session_count}
                    </span>
                  </button>

                  {isActive && (
                    <div className="ml-4 mt-1 space-y-0.5 border-l border-white/[0.06] pl-2">
                      {visibleSessions.length === 0 && (
                        <p className="px-2 py-1.5 text-[11px] text-slate-600">
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
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="border-t border-white/[0.06] p-2">
            <button
              type="button"
              onClick={() => openSettings("model")}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-[12px] text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
            >
              <Settings size={14} /> Settings
            </button>
            <div className="flex items-center gap-2 px-2 pt-1 text-[10px] text-slate-600">
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

      <div className="flex min-w-0 flex-1 flex-col">
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
                  <span className="text-slate-700">/</span>
                  <span className="truncate font-mono text-[10px] text-slate-600">
                    {activeProject.path}
                  </span>
                </>
              )}
            </div>
            <h1 className="truncate text-[13px] font-medium text-slate-200">{currentTitle}</h1>
          </div>
          <button
            type="button"
            onClick={() => openContext("index")}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] ${
              contextOpen
                ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
                : "border-white/[0.08] bg-white/[0.03] text-slate-400 hover:bg-white/[0.06]"
            }`}
          >
            <PanelRight size={14} /> Context
          </button>
          <button
            type="button"
            aria-label="Settings"
            onClick={() => openSettings("model")}
            className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
          >
            <Settings size={16} />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col">
          <div
            ref={scrollRef}
            onScroll={handleChatScroll}
            data-testid="chat-scroll"
            className="min-h-0 flex-1 overflow-y-auto"
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
                        <Plus size={14} /> Add project
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
                          onClick={() => {
                            setMessage(suggestion);
                            composerRef.current?.focus();
                          }}
                          className="rounded-full border border-white/[0.07] bg-white/[0.02] px-3 py-1.5 text-[11px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {entries.map((entry) => (
                <MessageCard key={entry.id} entry={entry} />
              ))}

              {busy && (
                <div className="flex items-center gap-2 text-[12px] text-emerald-300">
                  <Loader2 size={14} className="animate-spin" />
                  Working through the workspace…
                </div>
              )}
            </div>
          </div>

          <div className="shrink-0 border-t border-white/[0.06] bg-[#0b0e12]/95 px-4 py-3">
            <div className="mx-auto max-w-3xl">
              {error !== null && (
                <div className="mb-2 flex items-start gap-2 rounded-md border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-200">
                  <CircleAlert size={13} className="mt-0.5 shrink-0" />
                  <span className="flex-1">{error}</span>
                  <button type="button" onClick={() => setError(null)}>
                    <X size={13} />
                  </button>
                </div>
              )}
              <div className="rounded-xl border border-white/[0.09] bg-[#10151b] shadow-lg shadow-black/20">
                <textarea
                  ref={composerRef}
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  rows={2}
                  placeholder={
                    activeProject === null
                      ? "Add a project to start…"
                      : "Ask OpenBaD to inspect, explain, or change the workspace…"
                  }
                  className="block w-full resize-none bg-transparent px-3.5 pt-3 text-[13px] leading-5 text-slate-100 outline-none placeholder:text-slate-600"
                />
                <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
                  <label className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-slate-500 hover:bg-white/[0.04]">
                    <Shield size={12} />
                    <select
                      value={permissionMode}
                      onChange={(event) =>
                        setPermissionMode(event.target.value as PermissionMode)
                      }
                      aria-label="Access mode"
                      className="bg-transparent text-[11px] text-slate-400 outline-none"
                    >
                      <option value="read">Read only</option>
                      <option value="edit">Can edit</option>
                      <option value="full">Full access</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={() => openSettings("model")}
                    className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
                  >
                    <Bot size={12} />
                    <span className="max-w-[150px] truncate">{model}</span>
                    {thinking && <span className="text-emerald-400/70">thinking</span>}
                  </button>
                  <span className="ml-auto text-[10px] text-slate-700">
                    Enter to send · Shift+Enter for a newline
                  </span>
                  {busy ? (
                    <button
                      type="button"
                      onClick={() => void stop()}
                      disabled={stopRequested}
                      className="ml-1 flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/10 px-2.5 py-1.5 text-[11px] text-amber-200 disabled:opacity-40"
                    >
                      <Square size={12} /> {stopRequested ? "Stopping…" : "Stop"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void send()}
                      disabled={message.trim().length === 0 || workspacePath.length === 0}
                      className="ml-1 flex items-center gap-1 rounded-md bg-emerald-400 px-2.5 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-30"
                    >
                      <Send size={12} /> Send
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {contextOpen && (
        <ContextDrawer
          tab={contextTab}
          onTab={setContextTab}
          onClose={() => setContextOpen(false)}
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
        />
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
          onPermissionMode={setPermissionMode}
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
          onAddProject={() => {
            setProjectDialogOpen(true);
            setNewProjectPath(workspacePath);
          }}
          foundation={foundation}
        />
      )}

      {projectDialogOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Add project"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
        >
          <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#0f141a] p-5 shadow-2xl">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-[14px] font-medium">Add project</h2>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  A project is a directory the agent is confined to.
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
            <label className="mt-4 block text-[10px] uppercase tracking-wider text-slate-600">
              Directory
            </label>
            <input
              autoFocus
              value={newProjectPath}
              onChange={(event) => setNewProjectPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void createProject();
                }
              }}
              aria-label="Project directory"
              placeholder="/home/you/projects/app"
              className="mt-1 w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 font-mono text-[11px] text-slate-200 outline-none focus:border-emerald-400/40"
            />
            <label className="mt-3 block text-[10px] uppercase tracking-wider text-slate-600">
              Name <span className="normal-case tracking-normal text-slate-700">(optional)</span>
            </label>
            <input
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              aria-label="Project name"
              placeholder="Project name"
              className="mt-1 w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[11px] text-slate-200 outline-none focus:border-emerald-400/40"
            />
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
                disabled={newProjectPath.trim().length === 0}
                className="rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-30"
              >
                Add project
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
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
        <MessageSquare size={12} className="shrink-0 text-slate-600" />
        <span className="min-w-0 flex-1 truncate">{session.title ?? "Untitled session"}</span>
        {showProject && (
          <span className="max-w-[80px] truncate rounded bg-white/[0.05] px-1 py-0.5 text-[9px] text-slate-500">
            {session.workspace_path?.split("/").filter(Boolean).at(-1) ?? "project"}
          </span>
        )}
        <span className="shrink-0 font-mono text-[9px] text-slate-700">
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
        className="hidden shrink-0 rounded p-0.5 text-slate-600 hover:text-slate-300 group-hover:block"
      >
        <Pencil size={11} />
      </button>
    </div>
  );
}

function MessageCard({ entry }: { entry: ChatEntry }) {
  if (entry.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl border border-emerald-400/15 bg-emerald-400/[0.06] px-4 py-3 text-[13px] leading-6 text-slate-100">
          {entry.text}
        </div>
      </div>
    );
  }
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-white/[0.08] bg-white/[0.03] text-slate-400">
        <Bot size={13} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          <span className="font-medium text-slate-400">OpenBaD</span>
          {entry.status !== undefined && <span>· {entry.status}</span>}
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
          <div className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-slate-200">
            {entry.text}
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
        {entry.usage !== undefined && (
          <p className="mt-2 font-mono text-[10px] text-slate-700">
            {entry.usage.input_tokens.toLocaleString()} in ·{" "}
            {entry.usage.cache_read_tokens.toLocaleString()} cached (
            {cacheHitShare(entry.usage).toFixed(0)}%) · {entry.usage.output_tokens.toLocaleString()}{" "}
            out · ${entry.usage.cost_usd.toFixed(4)}
          </p>
        )}
      </div>
    </div>
  );
}

function ContextDrawer({
  tab,
  onTab,
  onClose,
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
}: {
  tab: ContextTab;
  onTab: (tab: ContextTab) => void;
  onClose: () => void;
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
  onOpenSettings: (tab: SettingsTab) => void;
}) {
  const tabs: { id: ContextTab; label: string }[] = [
    { id: "index", label: "Index" },
    { id: "memory", label: "Memory" },
    { id: "checkpoints", label: "Checkpoints" },
    { id: "diagnostics", label: "Diagnostics" },
  ];
  return (
    <aside className="fixed inset-y-0 right-0 z-30 flex w-[380px] flex-col border-l border-white/[0.08] bg-[#0c1015] shadow-2xl shadow-black/40">
      <div className="flex h-14 items-center justify-between border-b border-white/[0.06] px-4">
        <div>
          <p className="text-[13px] font-medium">Context</p>
          <p className="text-[10px] text-slate-600">Index, memory, edits, diagnostics</p>
        </div>
        <button
          type="button"
          aria-label="Close context"
          onClick={onClose}
          className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex gap-1 border-b border-white/[0.06] px-2 py-2">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onTab(item.id)}
            className={`rounded-md px-2 py-1 text-[11px] ${
              tab === item.id
                ? "bg-white/[0.08] text-slate-200"
                : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "index" && (
          <div className="space-y-3">
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2 text-[11px] text-slate-400">
                <Database size={13} className="text-emerald-300" /> Workspace index
              </div>
              <p className="mt-2 font-mono text-[11px] text-slate-300">
                {indexSummary === null
                  ? indexing
                    ? "Indexing…"
                    : "Not indexed"
                  : `${indexSummary.files.toLocaleString()} files · ${indexSummary.symbols.toLocaleString()} symbols · ${formatBytes(indexSummary.bytes)}`}
              </p>
              <button
                type="button"
                onClick={onRefreshIndex}
                disabled={indexing}
                className="mt-3 flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40"
              >
                {indexing ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                {indexing ? "Indexing…" : "Refresh index"}
              </button>
              {indexError !== null && <p className="mt-2 text-[11px] text-amber-300">{indexError}</p>}
            </div>
            <p className="px-1 text-[11px] leading-5 text-slate-600">
              Indexed search powers <code className="text-slate-500">search_code</code> and{" "}
              <code className="text-slate-500">find_symbols</code>. The index refreshes after each
              turn.
            </p>
          </div>
        )}

        {tab === "memory" && (
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
                  <span className="truncate font-mono text-[10px] text-slate-600">
                    {memory.tags.length > 0 ? memory.tags.join(", ") : "untagged"}
                  </span>
                  <button
                    type="button"
                    onClick={() => onForgetMemory(memory.id)}
                    className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-amber-300"
                    aria-label="Forget memory"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
            {memoryError !== null && <p className="text-[11px] text-amber-300">{memoryError}</p>}
          </div>
        )}

        {tab === "checkpoints" && (
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
                <p className="truncate font-mono text-[10px] text-slate-400">{checkpoint.path}</p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="text-[10px] text-slate-600">
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
                  <pre className="mt-2 max-h-56 overflow-auto rounded bg-black/30 p-2 font-mono text-[10px] leading-4 text-slate-500">
                    {checkpointDiffText}
                  </pre>
                )}
              </div>
            ))}
            {checkpointNotice !== null && (
              <p className="text-[11px] text-emerald-300">{checkpointNotice}</p>
            )}
            {checkpointError !== null && (
              <p className="text-[11px] text-amber-300">{checkpointError}</p>
            )}
          </div>
        )}

        {tab === "diagnostics" && (
          <div className="space-y-3">
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
              <div className="flex items-center gap-2 text-[11px] text-slate-300">
                <KeyRound size={13} className={apiKeyStatus ? "text-emerald-300" : "text-amber-300"} />
                DeepSeek key
                <span className="ml-auto text-[10px] text-slate-600">
                  {apiKeyStatus ? "ready" : "missing"}
                </span>
              </div>
              <button
                type="button"
                onClick={() => onOpenSettings("secrets")}
                className="mt-2 text-[11px] text-emerald-300 hover:underline"
              >
                Manage key
              </button>
            </div>
            {foundation.kind === "ready" && (
              <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 font-mono text-[10px] leading-5 text-slate-400">
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
              <p className="text-[11px] text-amber-300">{foundation.message}</p>
            )}
          </div>
        )}
      </div>
    </aside>
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
    { id: "project", label: "Project" },
    { id: "about", label: "About" },
  ];
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
    >
      <div className="flex h-[min(640px,80vh)] w-full max-w-3xl overflow-hidden rounded-xl border border-white/10 bg-[#0f141a] shadow-2xl">
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
              <p className="px-1 text-[11px] leading-5 text-slate-600">
                The access mode is intentionally not persisted across launches. The composer
                always starts in read-only mode.
              </p>
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
                <Plus size={13} /> Add another project
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
            </div>
          )}
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
        <p className="mt-0.5 text-[11px] leading-5 text-slate-600">{description}</p>
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
