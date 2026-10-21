# Architecture changelog

The assistant loads this file with the project instructions. Add a short entry
whenever an architecture document or the tool/runtime surface changes, so a new
session knows what moved without re-reading every document.

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
  closes its sessions. The built-in surface is 118 of 128 slots.
- **Deterministic workflows.** The `workflow` tool runs JSON scripts of tool
  steps through the registry with sequential steps, `for_each` fan-out,
  `${{steps.id...}}` references, result templates, a 64-step cap, and
  background status. Nested calls inherit the session access mode. The
  built-in surface is 119 of 128 slots.
