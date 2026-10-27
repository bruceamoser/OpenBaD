# Architecture changelog

The assistant loads this file with the project instructions. Add a short entry
whenever an architecture document or the tool/runtime surface changes, so a new
session knows what moved without re-reading every document.

## v0.1.2

Bundled desktop release containing the Linux `.deb` auto-update behavior below
on top of v0.1.1.

## v0.1.3

Bundled desktop release containing the rich file previews described below on top
of v0.1.2.

## v0.1.4

Bundled desktop release containing the preview follow-ups below on top of
v0.1.3.

## v0.1.5

Bundled desktop release containing the project export/import described below on
top of v0.1.4.

## 2026-10-27

- **Context budget and history compaction.** Before each provider request the
  agent now estimates prompt tokens against the model window and caps the
  completion budget so prompt plus output cannot exceed it. When history crosses
  the pressure threshold it replaces older turns with a bounded
  `<compacted-summary>` block, preserving the system prompt and recent whole
  turns. Assistant `reasoning_content` is still passed back verbatim while tools
  are present; older reasoning leaves the request only when its whole turn is
  compacted. A request that cannot fit after compaction returns a clear
  `ContextOverflow` error instead of a raw provider 400. The desktop chat shows a
  live `context compacted` marker; stored history and the UI transcript remain
  unchanged.
- **Project export/import.** New `openbad-transfer` crate packs a project into a
  single `.openbad-project` zip: workspace files (including `.git` and
  `.openbad`, excluding rebuildable output and `.gitignore`d paths) plus
  sessions/events/messages/turns, memories, knowledge chunks, and file
  checkpoints. Reference folders, secrets, and global MCP definitions are
  excluded by design. `openbad-store` gains `export_workspace_state` /
  `import_workspace_state` with idempotent upserts. Desktop adds
  `export_project`, `import_project` (new project), and `import_project_into`
  (merge/overwrite) plus Project-menu and project-panel UI. Extraction rejects
  unsafe archive paths and future format versions.

## 2026-10-26

- **Preview follow-ups.** `/project_file` now returns PDF bytes as a bounded
  data URL for `pdfjs-dist` canvas rendering (extracted text remains as a
  fallback), HTML previews render in a sandboxed iframe with a source toggle,
  and code files render in a line-numbered highlight.js view. Workspace and
  reference uploads stream from disk and accept up to 256 MB, so video files
  like `.mov` can be added; chat attachment staging uses the same path.

## 2026-10-25

- **Rich file previews.** `apps/desktop/src-tauri/src/preview.rs` classifies
  workspace files and returns inline text, bounded data URLs for images and
  media, tables for CSV/TSV and Excel/ODS workbooks (`calamine`), and extracted
  text for PDF (`pdf-extract`) and DOCX/PPTX (`zip` + `quick-xml`). Unknown
  binaries return a size summary instead of a UTF-8 error. Parsers run inside
  `catch_unwind` with per-type size caps. New `open_path` command opens a
  workspace/reference file with the OS default handler; the preview header and
  the fallback states expose it.

## 2026-10-24

- **Linux updates prefer the `.deb` package.** `openbad-update` now ranks
  `.deb` ahead of `.AppImage` on Linux unless the app is running from an
  AppImage, in which case the AppImage stays preferred so the running binary is
  replaced. A selected `.deb` is installed through `pkexec` (`apt-get install
  -y`, falling back to `dpkg -i`) and reported as a new `package_installed`
  outcome; if escalation is unavailable the existing save-and-open handoff
  still applies.

## v0.1.1

Bundled desktop release: global MCP server management and in-app updates
(shipped in v0.1.0) plus the preview pane, reveal-in-file-manager, chat timing,
project-scoped MCP enablement, and any-file chat attachments described below.

## 2026-10-23

- **Global MCP definitions, per-project enablement.** Global server definitions
  move to the application config file (`OPENBAD_MCP_CONFIG` overrides it); each
  workspace stores only `enabled: [names]` in `.openbad/mcp.yaml`. Legacy
  project-scoped definitions migrate on first load. The model gains
  `mcp_server_set_enabled`, and `mcp_server_add` enables the new server for the
  current project by default.
- **Preview pane.** Workspace files, knowledge documents, retrieval hits,
  memories, and assistant artifacts open in a resizable pane between the chat
  and the project panel instead of replacing the project panel.
- **Reveal in file manager.** `reveal_path` resolves a path inside the
  workspace or a reference folder and opens it in the OS file manager.
- **Chat timing.** `ChatReply.duration_ms`, per-message `duration_ms`, and
  `SessionView.total_duration_ms` back the elapsed/total header timers and the
  per-turn duration beside each response.
- **Any-file chat attachments.** Attachments are staged under
  `<workspace>/uploads/`, referenced by workspace path, and inlined only when
  they are UTF-8 text within budget.

## 2026-10-22

- **MCP server management.** `openbad-mcp` now supports `enabled`, `env`,
  `cwd`, and `timeout_seconds`, validates configurations, persists changes to
  `.openbad/mcp.yaml` atomically, restarts only affected server processes, and
  reloads when another manager writes the file. The desktop caches one manager
  per workspace and exposes `mcp_servers`, `add_mcp_server`,
  `update_mcp_server`, `remove_mcp_server`, `set_mcp_server_enabled`, and
  `test_mcp_server`, plus an MCP settings tab.
- **MCP tool domain.** `openbad-tools` adds `mcp_servers_list`,
  `mcp_server_add`, `mcp_server_update`, `mcp_server_remove`, and
  `mcp_server_test`, and moves `mcp_call`/`tool_search` into a dedicated `mcp`
  domain. Environment values are never exposed to the model. The registry now
  has 125 internal tools in 18 domains (36 facade schemas).
- **MCP catalog at the tail.** The MCP catalog is now appended only to each
  provider request, after the cached history prefix, instead of being inserted
  before history. Adding or removing a server changes the volatile tail without
  rewriting the cached prefix.
- **In-app updates.** New `openbad-update` crate checks GitHub releases for the
  current OS and architecture, selects AppImage/deb/rpm/msi/setup.exe/dmg
  assets, verifies `SHA256SUMS` when published, replaces a running AppImage
  atomically, and hands other installers to the platform. Settings → About and
  Help → Check for Updates surface the flow; no agent tool can install an
  update.

## 2026-10-21

- **Tool approval prompt.** A risk-gate denial now carries
  `openbad_approval` metadata. The desktop shows the tool, risk, and reason,
  and approving switches the session to Full access, persists it, and sends a
  continuation so the task resumes.
- **Session archive and delete.** Sessions can now be archived, restored, or
  soft-deleted from the sidebar. The active list excludes archived sessions,
  archived sessions have a separate expandable section, and deleting the open
  session clears the chat.
- **Risk gate.** The agent now resolves the effective risk of direct and
  `<domain>_run` calls and refuses approval-required tools before execution when
  the session lacks command access, returning an audited error instead of
  running the tool.
- **Deterministic tool-result compaction.** Before a request exceeds 600 KB,
  old tool results outside the recent 8-message window are replaced with a
  compact marker. User/assistant text is preserved and the cache prefix is
  deliberately reset.
- **Parallel read tools.** Consecutive read-only tool calls now execute
  concurrently, including `<domain>_run` calls whose inner tool is read-only.
  Write and execute calls remain serial, and tool-message order and audit
  records are preserved.
- **Tool-output spilling.** Results above the 64 KiB model-facing cap are saved
  under `.openbad/spill/` with a `spilled_path`, and the new `read_spill` tool
  pages through them. The internal extraction cap remains 256 KiB.
- **Domain-based tool surface.** Built-in tools are now grouped into 17
  domains. The model sees `<domain>_list` and `<domain>_run` for each domain,
  reducing the provider-facing schema set from 119 to 34 while keeping all 119
  internal tools available. Adding tools to an existing domain no longer
  changes the tool-schema hash.
- **Multi-step chat formatting.** Visible assistant text is accumulated across
  steps and joined with a blank line, and the streaming UI inserts the same
  boundary when a new step starts writing. This fixes run-on paragraphs when a
  step ends without a trailing newline.

## 2026-10-20

- **GitHub surface.** `docs/architecture/05-agent-runtime-and-tools.md` now
  documents the `github` tool: status, repositories, branches and protection,
  issues, labels, milestones, pull requests and reviews, Actions, releases,
  Pages, search, and `gh api`. Destructive actions require `confirm=true`.
- **Built-in skills.** `github-workflow`, `github-releases-pages`,
  `plugin-development`, and `asciidoc-rendering` ship in the binary and are
  indexed in every prompt. Workspace skills override them by name.
- **Plugin runtime.** `plugin_list`, `plugin_describe`, `plugin_call`,
  `plugin_test`, `write_plugin`, and `plugin_install_builtin` are documented in
  section 11.1 of `05-agent-runtime-and-tools.md`. Plugins live in
  `.openbad/plugins/<name>/`, exchange JSON over stdin/stdout, and record runs
  in `.openbad/plugin-runs/`.
- **AsciiDoc is optional.** The bundled AsciiDoc plugin is installed with
  `plugin_install_builtin`; `asciidoctor` is not a core dependency.
- **Project sidecars.** `docs/architecture/11-project-areas-and-split-workspace.md`
  now lists `.openbad/plugins/` and plugin run evidence alongside the existing
  canon, claims, plan, questions, context, and permissions files.
- **Session access mode.** Read only / Can edit / Full access is stored per
  session in `metadata_json`, restored when reopening the session, and new
  sessions start read-only. See `08-desktop-application.md`.
- **Rich chat output.** Assistant messages render GitHub-flavored Markdown,
  tables, task lists, footnotes, KaTeX math, highlighted code with copy, and
  Mermaid diagrams. See `08-desktop-application.md`.
- **Outside-workspace access.** `request_fs_access` asks the human to approve a
  specific absolute path. Grants live in the project database
  (`outside_access_grants`), are read/write scoped, and are managed in Settings
  → Access. See sections 7.1 and 11.1 of `05-agent-runtime-and-tools.md`.
- **Copy response.** Assistant messages have a header copy action that copies
  the raw Markdown response. See `08-desktop-application.md`.
- **Knowledge export.** Knowledge rows have Markdown download and print/PDF
  actions. Printable output uses a light document theme and hides the app
  chrome; the native print dialog provides Save as PDF.
- **Knowledge vs memory.** Prompt and tool descriptions now state that
  Knowledge is the long-form document library and Memory is short atomic
  snippets. Both are full-text indexed: `knowledge_fts` and `memory_fts`.
  `search_project` fuses both with code.
- **Steering and queuing.** A running turn accepts steering messages through
  `steer_chat`; they are appended before the next provider request. The desktop
  composer also queues messages for the next turn. See the turn rules in
  `05-agent-runtime-and-tools.md` and the composer notes in
  `08-desktop-application.md`.

## 2026-10-21

- **Subagents.** The `subagent` tool spawns, lists, inspects, and cancels
  directed background workers. Records live in the `subagents` table; the
  project panel has a new Agents area with live status, activity, result
  artifacts, and cancellation. Workers inherit the session access mode and
  cannot spawn further workers. See section 9 of
  `05-agent-runtime-and-tools.md` and the project areas document.
- **Tool budget.** Deprecated `github_issue`, `github_pr`, and `github_ci`
  aliases were removed so the sorted built-in surface stays at 128 tools, and
  the GitHub workflow skill now uses the real `git_branch`/`git_checkout`
  arguments.
- **Live project counts and copy controls.** The project sidebar now shows a
  live count beside Workspace, Agents, References, Knowledge, Retrieval,
  Memory, Todos, Questions, and Plan; running subagents add a pulse. Assistant
  responses can be copied from the header or footer, and user messages have a
  copy action. See `08-desktop-application.md`.
- **Subagent waiting.** `subagent` gained `action: "wait"` with an optional
  `timeout_seconds` (default 120, max 600), so the main agent collects a worker's
  result instead of duplicating its work. The prompt now prefers `wait` over a
  single `status` poll.
- **Native Tauri automation.** `apps/desktop/native-e2e/` drives the real
  desktop binary with `tauri-driver` and `WebKitWebDriver` against an isolated
  database and a mock DeepSeek SSE server. CI runs it under `xvfb-run` and
  covers project counts, the Agents area, chat, and top/bottom/user copy
  controls. See `08-desktop-application.md`.
- **Finished-agent cleanup.** Terminal subagent records are pruned after 60
  seconds (`OPENBAD_SUBAGENT_RETENTION_SECONDS` override), and the Agents panel
  keeps polling after completion so removed workers disappear without a click.
- **Tool surface consolidation.** `record_claim`/`list_claims`,
  `record_unknown`/`list_unknowns`, and `record_dead_end`/`list_dead_ends`
  merged into `ledger`; `snapshot_*` into `snapshot`; `golden_*` into `golden`;
  `canon_*` and `glossary_*` into `canon`; and the context-rule tools into
  `context_rules`. The built-in surface is now 113 of the 128-slot cap.
- **LSP code intelligence.** New `openbad-lsp` crate, `LspService` seam, and
  `lsp` tool for definition, references, implementation, and hover queries over
  a configured stdio language server. Configure servers in `.openbad/lsp.yaml`.
  The built-in surface is now 114 of 128 slots.
- **Session history search.** `session_fts` indexes user, assistant, and tool
  event text. The `session` tool searches this workspace's history and reads a
  sequence range; cross-workspace reads are rejected. Schema is now
  `0013_session_fts`, and the built-in surface is 115 of 128 slots.
- **MCP client and proxy tools.** `openbad-mcp` now speaks line-delimited
  JSON-RPC over stdio and JSON-RPC over HTTP, loads `.openbad/mcp.yaml`, and
  enforces per-server `allowed_tools`. `tool_search` searches the catalog and
  `mcp_call` invokes a tool; the catalog is appended as an untrusted tail
  message so server changes do not alter the cached prefix. The built-in
  surface is 117 of 128 slots.
- **Persistent terminal.** New `openbad-terminal` crate wraps `portable-pty`
  with create/send/read/close sessions, a bounded output ring, and process-group
  cleanup. The `terminal` tool requires Full access, and cancelling a turn
  closes terminal sessions. The built-in surface is 118 of 128 slots.
- **Deterministic workflows.** The `workflow` tool runs JSON scripts of tool
  steps through the registry with sequential steps, `for_each` fan-out,
  `${{steps.id...}}` references, result templates, a 64-step cap, and
  background status. Nested calls inherit the session access mode. The
  built-in surface is 119 of 128 slots.
