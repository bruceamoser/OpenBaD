# 08 — Desktop Application

## 1. Shell

OpenBaD Desktop is a **Tauri v2** application:

- **Rust process** — owns the harness, store, tools, workspace, memory,
  knowledge, autonomic system, and governance.
- **Webview** — React 19 + TypeScript + Vite + Tailwind CSS 4.
- **IPC** — typed Tauri commands and a single typed event stream.
- **OS integration** — tray icon, native notifications, global shortcut,
  file dialogs, keychain, updater.

The UI is a thin client over the Rust core. It never opens SQLite, never reads
the filesystem directly, and never holds provider API keys in memory longer
than a command needs.

## 2. Information architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Menu bar: OpenBaD · File · Edit · View · Project · Help             │
├─────────────────────────────────────────────────────────────────────┤
│ Project header: project · path · session · model · access mode      │
├──────────┬───────────────────────────────────────┬──────────────────┤
│ Sidebar  │ Chat / Agent                          │ Project panel    │
│          │                                       │                  │
│ Projects │ Messages                              │ Overview         │
│ Overview │ Composer                              │ Workspace        │
│ Workspace│                                       │ References       │
│ References│                                      │ Knowledge        │
│ Knowledge│                                       │ Retrieval        │
│ Retrieval│                                       │ Memory / Edits   │
│ Memory   │                                       │ Artifact         │
│ Sessions │                                       │ Diagnostics      │
└──────────┴───────────────────────────────────────┴──────────────────┘
```

The project panel is resizable and only present when requested. On narrow
windows the full sidebar collapses while the panel is open so the conversation
keeps a usable width.

### Navigation

- **Application menu bar** — File, Edit, View, Project, and Help. It owns
  project-wide actions (new session, settings, project areas, refresh index,
  shortcuts), keeping the permanent header down to breadcrumbs and status.
- **Command palette** (`Ctrl/Cmd+K`) — every action, file, session, and
  setting.
- **Quick ask** (`Ctrl/Cmd+Shift+Space`) — floating prompt from any app.
- **Session sidebar** — grouped by workspace, pinned, recent, archived.
- **Workspace switcher** — recent folders, open folder, trust state.
- **Project panel tabs** — overview, workspace, agents, references, knowledge,
  retrieval, memory, checkpoints, diagnostics, and the active artifact.
  Knowledge is for long-form documents; Memory is for short indexed snippets.
  Agents lists background workers spawned by the assistant, refreshes while a
  worker is running, and opens completed results as Markdown artifacts. The
  sidebar shows a live count for each project area; running subagents add a
  pulse indicator, and counts refresh without opening the panel.
- **Split artifacts** — assistant-presented web pages, images, Markdown, diffs,
  and workspace files open beside the conversation.
- **Bottom panel** — terminal, problems, job output (planned).

## 3. Views

### 3.1 Chat / Agent

- Streaming GitHub-flavored Markdown: headings, lists, task lists, tables,
  blockquotes, links, images, footnotes, strikethrough, and inline/display math
  through KaTeX.
- Syntax-highlighted code blocks with a copy action, plus copy controls at the
  top and bottom of every assistant response. User messages also have a copy
  action. Copy actions use the raw Markdown text.
- Project knowledge can be downloaded as Markdown or opened in a print/PDF view
  with light-document styling; the native print dialog provides Save as PDF.
- Mermaid diagrams rendered from fenced `mermaid` blocks, with the source
  available in a disclosure and render errors shown inline.
- Tool cards: name, status, duration, risk, result summary, expandable output.
- Reasoning: collapsible "Thinking" section, hidden by default.
- Citations: file path + line range; clicking opens the editor at the range.
- Approvals: inline cards with exact command/diff and allow/deny actions.
- Turn footer: input/output/cache tokens, cost, cache hit %, model, elapsed.
- Cancel button always visible during a turn.
- Document attachments: drop files onto the composer or use the paperclip
  button. Attachments appear as removable chips above the textarea, travel with
  the next turn as `<attached_document>` blocks, and are listed on the user
  message. Documents can be sent without any prompt text.
- Input stays isolated from the transcript: the composer owns its draft and
  attachment state, and message cards are memoized per entry. Typing therefore
  never re-parses rendered Markdown, and streamed deltas only re-render the
  message being written.
- While a turn is running, the composer offers **Steer** (append to the live
  turn before its next model request) and **Queue** (send automatically when
  the current turn finishes). Enter steers; Shift+Enter inserts a newline.
  Queued messages are listed above the composer and can be removed. Steering
  messages are marked in the transcript.
- Access mode: Read only, Can edit, or Full access. The choice is stored in
  the session metadata, restored when the session is reopened, and applied to
  every tool call in the turn. New sessions start read-only.
- Outside-workspace access: when the assistant calls `request_fs_access`, a
  dialog shows the exact path, requested read/write scope, and reason. Approving
  it stores a project-scoped grant; Settings → Access lists and revokes grants.
  Write grants still require Can edit or Full access for the session.
- "Continue", "Retry", "Fork", "Compact" actions.

### 3.2 Editor and inline completion

- CodeMirror 6 or Monaco, chosen at implementation time.
- File tree, tabs, breadcrumbs, symbol outline.
- **FIM inline completion** (`deepseek-flash`, non-thinking, 4K):
  ghost text, accept with Tab, reject with Esc, never auto-applied.
- **Chat prefix completion** for constrained edits triggered from the editor.
- Diagnostics from LSP shown inline and in the Problems panel.
- Format/lint/test reflex results shown as a reviewable diff.

### 3.3 Diff and review

- Unified and side-by-side modes.
- Hunk-level accept/reject.
- Checkpoint timeline: every edit turn has a checkpoint; rewind restores only
  tool-changed files.
- "Review this diff" action spawns a review route with `deepseek-v4-pro`.
- Guardrails highlight destructive changes and secrets.

### 3.4 Plan and todos

- Structured plan: goal, assumptions, steps, files, risks, validation.
- Approve plan to begin execution.
- Todo list with status, owner, and evidence links.
- Goal-loop progress with budget and round count.

### 3.5 Memory

Current build:

- Project-scoped memories in the split panel.
- Pinned markers, tags, and forget actions.
- Recent memories are appended to the agent prompt as tail context.

Planned: search filters, activation sparkline, association graph, score
explanation, edit/export, and consolidation reports.

### 3.6 Knowledge and retrieval

Current build:

- Project-scoped knowledge documents with title, kind, source, tags, chunk
  count, and size.
- Add a Markdown/text note, import a UTF-8 file, open a document as a Markdown
  artifact, or delete it.
- Retrieval panel queries knowledge, indexed workspace code, and memory through
  one fused result list with citable locators.
- Clicking a knowledge hit opens the document; clicking a code hit opens the
  file.

Planned: collections, PDF/DOCX/HTML ingestion, ingestion queue, chunk viewer,
versions, embeddings, and relevance explanations.

### 3.7 Pulse

- CPU, memory, disk, GPU/VRAM, battery.
- DB/WAL size, index lag, job health.
- Context pressure, cache hit %, token spend, account balance.
- Alerts with one-click actions ("Checkpoint WAL", "Pause indexing").
- DeepSeek status: reachable, latency, rate-limit headroom, peak/off-peak.

### 3.8 Settings

| Section | Contents |
| --- | --- |
| Provider | DeepSeek key (keychain), base URL, API format, `user_id` |
| Models | Routes, model catalog, context/output limits, pricing |
| Thinking | Default mode and effort per route |
| Budget | Session/monthly limits, alerts, off-peak preference |
| Cache | Cache meter, series resets, warm-up (off by default) |
| Workspace | Trust, indexing, ignores, formatters, reflexes |
| Governance | Approvals, sandbox, secret scanning, audit |
| Skills | Installed skills, enable/disable, sources |
| MCP | Servers, tools, resources, health |
| Data | Backup, export, retention, cloud sync (later) |
| Appearance | Theme, density, font, motion, accessibility |

## 4. Key interactions

### 4.1 Sending a request

1. User types in the composer; `@` mentions files/symbols, `/` runs commands.
2. The composer shows the target model, thinking effort, and estimated cost.
3. On send, the turn starts; the composer becomes a read-only status strip.
4. Tool cards appear as calls start; approvals surface inline.
5. The turn footer shows usage and cache hit.
6. The user can cancel, retry, fork, or compact.

### 4.2 Approving an action

- The approval card shows the exact command or diff.
- Destructive tokens are highlighted.
- Options: allow once, allow for session, always for workspace, deny.
- "Deny" returns a tool error to the model, which must adapt.
- The decision is written to the audit log.

### 4.3 Switching models

- The model menu shows context, pricing, thinking support, and cache state.
- Switching warns that the prefix cache will reset.
- "Switch and fork" creates a child session with the new model.
- The cache meter shows "cold" after a switch.

### 4.4 Viewing cost

- The header shows session spend and cache hit.
- Clicking opens a breakdown: input, cache read, output, reasoning, by model.
- The Pulse panel shows daily spend and balance.
- The budget bar warns at 80% and blocks at 100% unless overridden.

## 5. Design system

| Token | Choice |
| --- | --- |
| Typography | System UI + monospace for code |
| Density | Comfortable and compact modes |
| Color | Neutral base, one accent, semantic risk colors |
| Motion | Subtle; respects reduced-motion |
| Icons | Consistent set (Lucide or similar) |
| Theming | Light, dark, high contrast |
| Focus | Visible focus rings; keyboard-first |

Rules:

- Never use color alone to convey risk or status.
- Every icon button has a tooltip and accessible label.
- Streaming text must not cause layout shift.
- Long lists are virtualized (sessions, files, tool logs, memory).
- Markdown rendering sanitizes HTML and blocks remote images by default.

## 6. State management

- **Server state** (sessions, events, files, memory) lives in Rust; the UI
  caches projections.
- **UI state** (layout, selection, drafts) lives in React stores (Zustand or
  similar).
- **Event stream** is the single source of live updates; no polling for
  transcript data.
- **Optimistic updates** are limited to composer actions and toggles; anything
  durable waits for the Rust ack.
- **Invalidation** is event-driven, not time-based.

## 7. Performance

| Concern | Approach |
| --- | --- |
| Transcript rendering | Virtualized list; memoized tool cards |
| Streaming | Batch deltas at 30–60 Hz |
| File tree | Lazy loading; watcher-driven updates |
| Search | Debounced; server-side FTS/vector |
| Large diffs | Virtualized diff; hunk-level rendering |
| Memory graph | Canvas/WebGL; progressive layout |
| Startup | Load shell first; hydrate sessions asynchronously |
| Bundle | Code-split by view; no editor in the initial bundle |

Targets:

- Cold start to interactive: < 2 s.
- Session switch: < 150 ms.
- Streaming delta to paint: < 50 ms.
- Typing latency in editor: < 16 ms.

## 8. Accessibility

- Full keyboard navigation with a documented shortcut map.
- Screen-reader announcements for streaming status, tool completion, and
  approvals.
- Focus management for modals and approval cards.
- High-contrast theme and reduced-motion support.
- Minimum 4.5:1 contrast for text.
- No drag-only interactions.

## 9. Tauri commands and events

### Command groups

| Group | Examples |
| --- | --- |
| Session | `start_turn`, `cancel_turn`, `fork_session`, `compact`, `export` |
| Workspace | `workspaces_directory`, `create_project`, `open_workspace`, `trust_workspace`, `index_status`, `search` |
| Files | `read_file`, `write_file`, `apply_patch`, `checkpoint_rewind` |
| Git | `git_status`, `git_diff`, `git_commit`, `git_push` |
| Memory | `search_memory`, `remember`, `forget`, `consolidate` |
| Knowledge | `knowledge_documents`, `add_knowledge_note`, `import_knowledge_file`, `delete_knowledge_document` |
| MCP | `mcp_servers`, `add_mcp_server`, `update_mcp_server`, `remove_mcp_server`, `set_mcp_server_enabled`, `test_mcp_server` |
| Updates | `check_for_updates`, `install_update` |
| Project areas | `project_overview`, `project_repos`, `add_project_repo`, `remove_project_repo`, `project_files`, `project_file`, `search_project` |
| Models | `list_routes`, `set_route`, `test_provider`, `refresh_balance` |
| Governance | `pending_approvals`, `decide_approval`, `audit_verify` |
| Autonomic | `jobs_list`, `job_cancel`, `schedules_list`, `pulse_snapshot` |

### Event envelope

```json
{
  "type": "tool_finished",
  "data": {
    "session_id": "...",
    "call_id": "...",
    "name": "apply_patch",
    "ok": true,
    "duration_ms": 412
  }
}
```

Events are batched and ordered. The UI can request a replay from a sequence
number after a disconnect.

## 10. Packaging and updates

- **Linux:** AppImage and `.deb` (WebKitGTK dependency documented).
- **macOS:** `.dmg`, universal binary (aarch64 + x86_64).
- **Windows:** `.msi`/`.exe` via WiX or NSIS.
- **Signing:** platform code signing.
- **Update source:** `bruceamoser/OpenBaD` GitHub releases by default. Override
  with `OPENBAD_UPDATE_REPO`; point at a compatible API with
  `OPENBAD_UPDATE_API`.
- **Check:** Settings → About (or Help → Check for Updates…) calls
  `check_for_updates`. `openbad-update` reads the latest release, compares
  semantic versions, and selects an asset for the current OS and architecture
  (AppImage, then `.deb`/`.rpm`; `.msi`/`-setup.exe`; `.dmg`).
- **Install:** `install_update` re-checks the release, downloads the asset to
  the user's downloads directory, and verifies `SHA256SUMS` when the release
  publishes one. A running AppImage is replaced atomically and the app
  relaunches. Other installers are saved and opened with the platform handler
  so the user completes the install. Updates can only be started from the UI;
  no agent tool can install one.
- **Release contract:** tag releases with a semantic version (for example
  `v0.2.0`), attach the platform bundles, and upload a `SHA256SUMS` asset that
  lists each bundle. Downloads are refused unless they use HTTPS (loopback is
  allowed for development).
- **Rollout:** releases are published through GitHub; staged rollout and
  startup rollback remain future work.
- **Size:** target < 30 MB shell; embedding model downloaded on demand.
- **Telemetry:** off by default; opt-in, redacted, local viewer.

## 11. Error surfaces

| Error | UI |
| --- | --- |
| DeepSeek unreachable | Banner with retry and status link; local work continues |
| Invalid key | Settings dialog with "Test key" |
| Rate limited | Countdown, retry, suggestion to use flash/off-peak |
| Budget exceeded | Blocking modal with override |
| Context overflow | "Compact and retry" action |
| Tool failure | Tool card error with details and retry |
| Approval denied | Tool card shows denial; model adapts |
| DB issue | Diagnostics panel with backup/restore |
| Crash | Restart prompt with session recovery |

## 12. Testing

| Layer | Tool | What |
| --- | --- | --- |
| Components | Vitest + Testing Library | Rendering, interactions, accessibility |
| Stores | Vitest | State transitions, event application |
| E2E | Playwright + Tauri driver | Launch, chat with mock provider, approve, diff |
| Visual | Playwright snapshots | Key views and themes |
| Performance | Playwright traces + Rust benches | Startup, streaming, large lists |

Mock provider fixtures cover DeepSeek streaming, tool calls, thinking output,
cache usage, errors, and rate limits.

### Native automation

The desktop shell is covered by a WebDriver suite in
`apps/desktop/native-e2e/`. It launches the real Tauri binary through
`tauri-driver` and `WebKitWebDriver`, uses an isolated SQLite database, and
serves a mock DeepSeek SSE endpoint. The suite verifies project counts, the
Agents area, a real chat round-trip, and both copy controls.

CI installs `webkitgtk-webdriver` and `tauri-driver`, builds
`target/debug/openbad-desktop`, and runs the suite under `xvfb-run`. Run it
locally with:

```bash
cd apps/desktop && ./ui/node_modules/.bin/tauri build --no-bundle --debug
cd native-e2e && npm test
```
