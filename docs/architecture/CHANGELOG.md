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

## v0.1.11

Bundled desktop release containing the context-budget and history-compaction
work described below, on top of the project export/import and other 2026-10-27
changes.

## v0.1.12

Bundled desktop release replacing the 0.1.11 per-step pruning with the harness
compaction design: model-written checkpoints, persisted compaction events, and
a `session_messages.surface` projection. This fixes long sessions that lost the
previous answer, re-ran completed tool work, and failed with a provider context
overflow. See the 2026-10-27 compaction entry for details.

## 2026-11-04

- **Supervised work pipeline (ADR-009), through admission.** A project opts in with
  `.openbad/pipeline.yaml`; a tick polls each enabled source through `gh api`,
  judges every trigger against its thread, and records the tickets, mandates, and
  read positions in the existing store. The host now exposes `pipeline_status` and
  `pipeline_tick`, so the panel can show what a project listens to and a person can
  ask for a poll. Triggers that need an executor are reported as deferred and hold
  their source's read position rather than being read past; the executor itself is
  the remaining half of #963. See section 16 of
  `05-agent-runtime-and-tools.md`.
- **Launch.** `pipeline_launch` checks a ticket out into its own worktree on its
  own branch under the project's execution root, runs a bounded prepare command
  there, and records the assignment. A broken prepare ends the ticket with the
  output in its report; a good one leaves it pending, ready for its turn.
- **Turn.** `pipeline_execute` runs that turn through a `TurnRunner` seam: the
  assignment moves `pending` → `running` → `ended`, the report's outcome decides
  the states via `effects_of`, and the project's step and token budget is checked
  with the numbers in the reason. The prompt carries the mandate, where the
  worktree and branch are, and the two things a mandate does not buy. The real
  runner is the app's own agent rooted at the worktree with `FullAccess`.
- **Watching, streaming, stage, and setup.** A per-project watcher ticks, launches,
  and runs tickets until stopped; the executor streams steps to `pipeline:progress`
  and stops at a step boundary as `waiting`. One `worker-<ticket>` session is
  resumed across assignments, slots are enforced, and a message on a live ticket
  wakes its executor. The panel lists tickets by derived stage (needs-a-person
  first) and edits `.openbad/pipeline.yaml`, refusing to save what the loader would
  refuse to run; the branch-protection check names each missing guard rather than
  accepting a promise.

## 2026-10-27

- **Playwright browser suite removed.** The `apps/desktop/ui/e2e` harness and
  `@playwright/test` dependency are gone. UI gating is now TypeScript typecheck
  plus a production Vite build; the WebDriver suite in
  `apps/desktop/native-e2e/` remains the end-to-end coverage. This removes a
  permanently drifting duplicate of the native suite.
- **Compaction follows the harness design.** Before each provider request the
  agent estimates prompt tokens against the model window, compacts when the
  prompt passes 80% of the window (or cannot fit), and caps the completion
  budget to the space that remains. Compaction summarizes complete older turns
  through the model itself, reusing the conversation's system prompt and tool
  schemas as the request prefix, and replaces them with one
  `<compacted-summary>` checkpoint. The previous and current user turns are
  always retained verbatim, so the answer the user is continuing from is never
  summarized away and tool-call/result pairs are never split. The checkpoint is
  persisted as `compaction/start`, `compaction/summary`, and `compaction/end`
  events; the new `session_messages.surface` column keeps the original messages
  for the transcript and audit while the model replays only the checkpoint plus
  the retained tail. Compaction runs at most once per turn, so a long agentic
  turn no longer rewrites tool results on every step. Completed text turns drop
  their now-unneeded `reasoning_content`; tool-call turns keep it. A request
  that still cannot fit returns a clear `ContextOverflow` error instead of a raw
  provider 400. The desktop chat shows at most one `context compacted` marker
  per turn.
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

## v0.2.0

Bundled desktop release carrying the chat-experience and turn-engine rework on
top of 0.1.12: a durable turn log that survives a crash or cancel, a fail-closed
tool pipeline with per-call approvals and persisted workspace grants, a
transcript that renders tool calls, edits, checkpoints, retries, forks, and
compaction as first-class rows, non-truncating long-context handling, parallel
sessions with Ask/Plan/Agent modes, OS notifications with unread tracking, and
token-based theming down to light mode. Delegated subagents now run under the
parent's policy and approval gate instead of bypassing it. See
`docs/plans/0004-chat-experience-and-turn-engine.md` for the design and the
follow-up log.

## v0.2.1

Fix release on top of 0.2.0. The read-before-overwrite guard now remembers reads
for the whole session, with the file's size and modification time, instead of
only for the current turn — so a file read in one turn can be overwritten in
the next, an external edit still forces a re-read, and delegated subagents see
the parent's reads. The transcript no longer repeats every tool result under
the final message, and tool cards render before the answer so the turn ends
with the answer.

## v0.3.7

Fix release on top of 0.3.6. A tool call arrives as invalid JSON when the
provider puts a raw newline inside a string (long Typst or Python edits); the
whole call was refused at the parse error. Control characters inside strings and
trailing commas are now normalised and parsed again, at both the agent's
tool-call parse and the proxy's nested `arguments` string.

A misspelled tool name was reported as a wrong shape, sending the model after a
shape error it could not fix. The nearest registry name is suggested:
`no tool named "exec_command"; did you mean "run_command"?`

Also confirms the renamed-key repair from 0.3.6 works end to end, which the
field report could not show because the installed binary predated that release.

## v0.3.6

Fix release on top of 0.3.5. A proxy call can arrive with its tool name under a
different key — `{"command": "run_command", "arguments": {…}}` — because the
provider's serializer mangles long payloads in more than one way. That shape is
recoverable: a tool takes no argument named after another tool, and the sibling
`arguments` object is the real payload, so the call now runs the tool it names.

Execute, approval risk, parallel safety, and enforced budgets all resolve through
one function, so a repaired `write_file` is still gated as a write instead of
passing as a read. Failure messages also split by cause: an unusable name names
the domain's tools, a missing name lists the tools the arguments fit.

Workflow actions moved off the deprecated Node.js 20 runtime (checkout v7,
setup-node v7, cache v6, pnpm/action-setup v6, upload-artifact v7), and the macOS
bundle builds on `macos-26` — `macos-14` retired on 2026-11-02 and browned out a
release.

## v0.3.5

Fix release on top of 0.3.4, all of it from one session of proxy-call failures.

**Truncation is handled instead of corrupting calls.** `max_output_tokens` is
sent as `max_tokens`, and `finish_reason: "length"` was never handled: a
response cut at the budget was parsed and executed as if complete. Two mangled
proxy calls this week — a folded provider tag and a missing `tool` key, both on
~10 KB writes — have exactly that signature. A step that ends with `length` no
longer runs its tool calls; each is answered with the limit and how to split the
work, and the turn continues. A truncated text answer says where it stopped.

**No silent cuts.** `mcp_call` and subagent results keep their tails (spill
holds a path), attachment extraction states included and total bytes, an
oversize PDF or image says it was too large rather than unreadable, and error
previews went from 240 characters to 2 KiB with the dropped size named.

**Two caps became settings.** Settings → General → Limits carries the response
budget (256–65 536, default 16 384) and files per `read_files` call (1–200,
default 20).

**Ask instead of refusing.** A destructive command raises the approval dialog
with its targets and reasoning, and a model-supplied `confirm_destructive` can
no longer skip it. A proxy call that arrives with no `tool` names the omission
and the tools its arguments fit, instead of only restating the expected shape.

## v0.3.4

Fix release on top of 0.3.3. A session could become permanently unusable with
`Invalid assistant message: content or tool_calls must be set`: a step that
returned only reasoning was recorded as an assistant message with no text and no
tool call, and the provider rejects that shape on every later request, including
compaction.

The loop no longer records such a step, replayed and rehydrated histories drop
any that are already stored, so an affected session recovers instead of staying
broken. History is repaired before the compaction boundary is computed, which
also stops a shifted boundary from indexing past the end.

## v0.3.3

Fix release on top of 0.3.2. The packaged app icon was a placeholder on Linux:
the `.deb` installed a 415×415 lockup into `hicolor/415x415/`, a directory no
desktop environment looks in, so `Icon=openbad-desktop` resolved to nothing. The
icon set is now generated from `assets/app-icon.png` — the mark alone on a
transparent 1024×1024 canvas — and installed at 32, 64, 128, 256, and 512, with
`icon.icns` for macOS and an `icon.ico` whose 16–256 sizes come from the mark
rather than a wordmark.

On a `.deb` install the icon lives in `/usr/share/icons`, so the in-app updater
does not replace it: reinstall the new `.deb` to pick it up. The AppImage updates
in place.

## v0.3.2

Fix release on top of 0.3.1. A steering message now renders where it arrived:
the live transcript used to keep one assistant entry for the whole turn, so a
message sent mid-turn could only be appended after it, at the bottom of the
answer, even though the persisted transcript already had it in the right place.
The current segment is closed on receipt and a new one opens after the card.

Releases also stopped compiling on the tag. GitHub scopes action caches to the
ref that saved them, so a build on a `v*` tag always started from an empty
target and recompiled the workspace three times before publishing. The build now
runs in the `Bundles` workflow on the version bump (where caches exist) and the
tag only publishes the artifacts: about two minutes instead of fifteen.

## v0.3.1

Fix release on top of 0.3.0. A long command payload sometimes comes back from
the provider with its own markup folded into the `tool` value of a domain proxy
(`<||DSML|| parameter name="arguments" ...>`), carrying the real arguments with
it. The proxy rejected the whole call and pasted the payload back into the
transcript, although the intended tool name and arguments were both intact.

The proxy now recovers the name from the prefix and the arguments from the first
balanced JSON object (or a JSON-encoded `arguments`, or the embedded payload when
`arguments` is empty), and resolves the approval risk against the recovered name
so a repaired call is still gated on the tool it really is. Failures name the
expected shape and preview the value instead of dumping it. `run_command` and
the exec domain now also steer toward writing a script with `write_file` and
running the file, because a heredoc nested inside a JSON string is what breaks
the serializer.

## v0.3.0

Minor release on top of 0.2.2. The chat fixes from 0.2.3's scope ship here
rather than separately, together with three new capabilities.

**Chat.** The Workspace tab is a folder tree (folders first, indented,
collapsible, search expands matches) instead of a flat file list. The settings
dialog's close control is in the standard top-right corner. `ask_question` now
blocks: the desktop host shows a question dialog and returns the answer in the
tool result, for parent and delegated runs alike, instead of queueing into an
inbox nobody opened. The exchange is still logged to `.openbad/questions.json`,
which Project → Questions now presents as a log.

**Skills.** Four engineering-process skills ship in the binary next to the four
capability skills and are indexed in every prompt: `pre-push-checks`,
`error-handling`, `test-reliability`, and `code-review`.

**Goals.** `.openbad/goal.json` holds one durable objective per workspace with
timestamps and progress notes; an open goal is re-summarised into every system
prompt, and `create_goal`/`get_goal`/`update_goal` manage it. Creating a second
objective while one is open is refused.

**Team board.** `.openbad/team.json` is a shared board of units of work with
stable ids, a state, an owner, and a result, so a parent can divide work between
subagents and see what is outstanding: `team_task_create`, `team_task_list`,
`team_task_update`, `team_task_get`.

**Plan.** Plan 0005 proposes code mode (one model-written program calling tool
bindings) and records the two decisions it needs first: the interpreter and the
approval contract for a program that blocks mid-run. Nothing implemented.

**Earlier than 0.3.0 but after 0.2.2.** The read-before-overwrite guard now
remembers reads per session rather than per turn, `write_file` refreshes that
record after a successful write, and the transcript no longer repeats every tool
result below the message or renders cards after the text.

## v0.2.2

Fix release on top of 0.2.1. The domain proxies (`exec_run`, `files_run`, …)
now carry an enum of their domain's tools in the schema, name those tools in
the description, and repeat both in argument errors — so a model that skips
`<domain>_list` gets a correctable message instead of `missing string argument:
tool` or `tool … is not in the exec domain`.

## v0.3.8

**A `cwd` inside the workspace is just a path.** `run_command` treated every
absolute path as external without first asking whether it was inside the
workspace, so a session's own root, passed as a `cwd`, was refused with "path is
outside the workspace" and the model retried in a loop. An absolute `cwd` now
resolves like a relative one: accepted inside the workspace, accepted outside
once the human granted write access, with `allow_external_cwd` kept for declared
recipes. Refusals name every remedy — request write access, use a relative cwd,
or pass the flag — and `ToolError::OutsideRoot` gains the same guidance for file
tools. The phrase "outside the workspace" is unchanged, because the agent system
prompt keys off it.

**Diagnostics you can read.** `scripts/diagnostics.py` opens `openbad.db`
read-only, joins each session's workspace root, redacts credentials, and prints
either a summary or, with `--full`, the exchange with tool calls and reasoning;
`--errors` filters to failures and `--log` tails the new app log. The desktop
now writes `openbad.log` beside the database — startup version, pid, and
platform, plus any panic with its message and location — capped at 4 MiB with
one rotation and following `OPENBAD_DB_PATH` for isolated runs.

## v0.3.9

**Five days of logs, then gone.** Nothing pruned `.openbad/`: one workspace had
2195 command logs, another 1326, and `spill/` and `audit.jsonl` only grew. Age is
now the rule — command logs and spill files live five days — with file-count and
byte ceilings kept only as a backstop so a runaway day cannot fill a disk. The
audit trail rotates at 8 MiB keeping one archive, and `openbad.log` rotates at
4 MiB through the same helper. Pruning is best effort, never prunes directories
or symlinks, and keeps a file whose timestamp is in the future rather than
deleting it over a clock disagreement. `.openbad/undo` and the session database
are recovery and product data and stay outside the policy.

## v0.3.10

**Log retention is yours to set.** The five-day window from v0.3.9 was hardcoded;
it is now **Settings → General → Limits → Log retention (days)**, default 5,
clamped 1–365, and it travels `ChatInput` → `AgentConfig` →
`ToolContext::log_retention_days` to the prune sites. The file-count and byte
ceilings stay fixed as last-resort guards for a runaway day, and because they
trim newest-first within the chosen window they can never shorten it behind your
back. The audit trail and `openbad.log` keep size-based rotation.

## v0.3.11

**Session search works again.** Searching history failed with a SQLite
"Invalid column type Null" whenever a match landed on an event with no
`search_text` — 4867 of 6840 assistant messages, and every step and usage event.
The index is external-content, so `snippet()` re-reads that column; the triggers
had been indexing a JSON fallback the column never held, so those rows matched on
keys like `cache_read_tokens` and then returned a NULL preview that failed the
whole call. Migration 0018 rebuilds the index from `search_text` alone and the
snippet is read as optional. Measured on a live database, searching "cache" goes
from 4352 matches — 4193 of them JSON keys, and 79 of 82 assistant matches
failing the call — to 80 matches, all conversation text. A term that exists only
inside a payload is no longer searchable. `unsupported session action` also names
the valid actions now.

## v0.3.12

**Startup no longer dies on a GLib log.** Seven core dumps in seven days were
SIGABRT. GLib aborts on any level in its always-fatal mask and **skips every
installed handler** while doing so, so the fix is to narrow that mask to errors
before GTK initialises and then route warnings and criticals to `openbad.log` —
verified with a throwaway C program, not assumed. `g_error` stays fatal on
purpose. `--version` and `--help` also exit before anything touches the display:
they used to reach `gtk_init_check`, which calls `g_error` when the xsettings
schema has no `antialiasing` key — the case in any terminal inheriting the VS
Code snap's schema paths. Four of the seven dumps came from a version probe that
never needed a display. The six CIFS-share entries behind the original critical
were removed from the recent-files list, with a backup.

## v0.3.13

**The envelope is gone from the advertised surface.** ADR-008's first slice: the
model now sees `discover_tools` plus the active set — six hot tools by default —
flat, one level of arguments, capped at 30 definitions. `<domain>_run` is no
longer advertised but stays callable for the workflow engine, older transcripts,
and tests. `discover_tools` returns each match with its full argument schema and
reports the names in `metadata.activate`; the agent records them and the desktop
carries the wider set into the next turn, per session.

Why: 39% of 150 measured tool errors were proxy-envelope or JSON serialization
failures, and three repair patches never moved that share. No provider or
surveyed harness nests JSON inside JSON, and Anthropic documents that tool
selection degrades past 30–50 advertised tools — we sat at 36 while paying the
error share. Evidence, sources, and unverified flags live in
`docs/architecture/research/tool-call-serialization-survey.md`.

Known limitation: the advertised list is fixed for a turn because it is part of
the cached prompt prefix, so an activated tool becomes selectable on the next
turn. Moving schema construction into the step loop is the follow-up.

## v0.3.14

**ADR-008 finished.** The advertised tool set is rebuilt every step, so a tool
found by `tool_search` is callable on the step that follows instead of the next
turn. An unreadable argument payload is still reported in band, but two in a row
now end the turn with an explanation rather than looping — repair cannot fix
DeepSeek's value-level corruption, so re-asking is the whole remedy. Scratch
directories (`/tmp`, `/var/tmp`, `/dev/shm`, `/run/user`, the platform temp root)
need no outside-access grant; a live session writing a script to `/tmp` was
refused, and scratch space is ephemeral rather than a trust boundary
(`ToolContext::scratch_access` turns it off for locked-down deployments).

DeepSeek **strict mode** is available behind an off-by-default setting: schemas
are rewritten so every property is required and no extras are allowed, with
optional arguments made nullable, and requests move to the beta endpoint that
serves it. It is unmeasured — one session with it on is the way to know.

`discover_tools` and the MCP-only `tool_search` are now one `tool_search`
covering both catalogues; an unconfigured MCP reads as an empty list instead of
failing.

## v0.3.15

**Recoverable is now a property, not a guess.** Tool errors carry whether the
model can act on them: a refusal, a bad argument, a timeout, or a read-before-write
gate is recoverable and renders as a collapsed neutral "Retry"; I/O and database
faults stay red failures. Before this every tool error was a red auto-expanded
block, so routine self-correction looked like the application breaking.

A path refusal now names the workspace root and, when an entry with the same file
name exists inside the workspace, points at it — the model cannot infer the root
from a path. The read-before-write gate became `ToolError::Stale` and says what to
do: read the file, then retry.

`edit_file` and every other tool also accept the argument names models are
trained on — `file_path`, `old_string`, `new_string`, `search`, `replace` — rewritten
once at dispatch from each tool's own schema, so `lsp`'s genuine `file_path` is
left alone.

## v0.3.16

**The chat column uses the width it has.** Transcript and composer were pinned to
768px each, so a 1500px pane was half empty and three-column tables in a reply
were squeezed into about 400px. They now share one `.chat-column` that grows with
the pane and stops at 1280px — wide enough to fill the space, narrow enough to
stay readable. Sharing the rule also fixed a 7px misalignment: only the transcript
reserves a scrollbar gutter, so the composer sat right of the text above it.

## v0.3.17

**The supervised work pipeline (ADR-009) runs end to end.** A project opts in with
`.openbad/pipeline.yaml`; a watcher polls its sources, judges each trigger against
its thread, and admits what the rules allow as a ticket with a mandate. A ticket is
checked out into its own worktree on `worker/<ticket>`, prepared inside it, and run
by the app's own agent in a `worker-<ticket>` session it keeps across assignments —
with full access inside that worktree and nothing beyond it, since the workspace
the tools can see is the worktree.

What a person gets: a Pipeline panel listing tickets needs-a-person first, with the
stage derived from rows rather than inferred from prose; check now, watch, stop, and
prepare & run per row; a setup surface that edits the real file and refuses to save
what the loader would refuse to run; and a branch-protection check that names each
missing guard instead of accepting a promise. A finished turn asks `gh` for the pull
request its branch opened and subscribes the thread, so a review comment wakes the
same executor.

Not in this release: auto-merge (the guard check exists, the merge action does not),
the main-area ticket screen with the diff, attention questions, and per-project
provider settings for the runner.

**The agent can configure the pipeline.** A `pipeline` tool in the plan domain reads
the project's configuration, validates a change with the loader's own parser, writes
it only when asked to, and reports what branch protection actually says. Its
description carries the document's shape and the four rules that change behaviour —
labels admit, `allowed` authorizes, a disabled source keeps its read position, and a
failing prepare fails the ticket — so the model does not have to guess a schema it
cannot see. Guard rules moved into `openbad-pipeline::guards`, so the panel and the
agent get the same answer.

## v0.3.18

**The agent can set the pipeline up itself.** 0.3.17 shipped the pipeline but left
its configuration as something a person had to write: the document's shape lived in
code the model cannot read, and the only prose was in an architecture note it is
never given. Ask it to configure a pipeline in 0.3.17 and it guessed keys until the
loader complained.

The `pipeline` tool closes that gap. It lives in the plan domain, so the system
prompt's own domain guidance reaches it, and it carries the knowledge in its
description: the document's shape, and the four rules that change behaviour —
labels admit (an empty list admits nothing), `allowed` authorizes a person while
`known_bots` may only propose, a disabled source keeps its read position, and a
failing prepare fails the ticket with its output. `show` reads and checks, `write`
validates through the loader's own parser before saving (with `dry_run` to check
without saving), and `guards` reports what branch protection actually says, so
auto-merge is never promised on a guess.

The guard rules moved into `openbad-pipeline::guards`, which means the panel that
refuses the switch and the agent that checks it compute the same answer.

## v0.3.19

**The pipeline configuration is readable again.** The setup panel's textarea used
the canvas surface — the white page used for previews — with light body text, so in
the dark theme the configuration was light grey on white: present, selectable, and
impossible to read. It now uses the same inset field as the rest of the app's forms,
and the report detail got the same treatment. No behaviour changed.

**Watching survives a restart, and a configured project polls on its own.** Watch
used to live only in the running process, so a project that had been watched went
quiet the moment the app closed — nothing polled, no ticket was ever admitted, and
the panel looked empty even though the configuration named sources with open,
labelled work waiting behind them. Watching is now a setting on the project
(`pipeline.watch.<workspace_id>`), restored for every project when the app starts.
A recorded choice always wins; with none recorded the configuration decides, since
a project that names an enabled source has already opted in.

The watcher polls at the fastest cadence its enabled sources ask for — clamped to
5–3600 seconds — instead of a fixed interval. `pipeline_status` now reports the
intent (`watching`) and the fact (`running`) separately, so a project whose watcher
could not start says so rather than looking healthy, and the empty state names the
step that is missing. Tickets appearing in the panel is a consequence of a tick:
until a project polls once, a correctly configured pipeline is empty by design.

**`gh` is resolved rather than assumed.** A desktop app inherits the session's
`PATH`, which is not always the login shell's: a per-user install under
`~/.local/bin` is missing whenever the session manager, not a shell, started the
app. That makes a bare `Command::new("gh")` a silent failure — a spawn error the
caller discards, indistinguishable from "there is nothing there". The lookup now
checks `PATH` first, then the directories installers use (`~/.local/bin`,
`/usr/local/bin`, Homebrew on both Linux and macOS, `~/.nix-profile/bin`,
`~/.asdf/shims`), and `OPENBAD_GH` names the binary outright. The app on this
machine does have `~/.local/bin` on its `PATH`, so nothing was broken by it here;
the lookup removes a way for it to break silently somewhere else.

## v0.3.20

**A delivered ticket is no longer undone by its own next round.** Two defects made
the first real run unreadable: every ticket showed `failed`, although all eight had
committed work and reported `ready` minutes earlier.

The first is that `prepare` ran again on a worktree that already existed. `uv venv`
refuses to overwrite the virtualenv the previous round created, so the second
assignment died in seconds with `A virtual environment already exists at: .venv` —
and because that report is the newest, the ticket's stage became `person/failed`
over a delivery that had succeeded. Prepare now runs once per worktree, recorded as
`pipeline.prepared.<branch>`; a worktree that was already prepared keeps what the
first prepare made, and a ticket keeps one worktree for its whole life.

The second is a self-resume loop. The executor signs in as the person it works for,
so `known_bots` cannot filter its comments and rule 7 delivered its own report
comment back to it as new input. Ticket #7 therefore ran three full rounds — 33, 53
and 30 minutes — and would have continued indefinitely; only the prepare failure
stopped it. Admission now drops a message written before the ticket's last finished
turn, which is output the pipeline already produced or input it already received. A
reply written after that turn ends still resumes the ticket, and a message arriving
mid-turn still wakes the live executor.

## v0.3.21

**The pipeline acts as the account it names, without switching anyone's login.**
`gh auth switch` mutates one global setting, so a person signed in as their work
account for other projects and their personal account here would have each side
flip the other — and whichever ran last decided who a commit or a comment was
attributed to. `github.user` in `.openbad/pipeline.yaml` now pins an account:
polling, guard checks, pull-request detection, and the executor's own `gh` and
`git` all read that account's saved credential and carry it per process, in an
isolated token-free config directory. Nothing global changes, so the other
workflow is untouched in both directions.

The token is never serialized, is redacted from tool output and reports, and
errors name the account rather than the secret. A project that names no account
behaves as before, and `pipeline_status` reports which account a project acts as
so "why did this comment come from that user" has a visible answer.

## v0.3.22

**The panel and the log say what the pipeline is doing.** Eight tickets read
`ended` with no pull request beside them, although eight pull requests were open:
the row took its link from the newest report, and the newest report was a failed
attempt that never reached the branch. A pull request is a fact about the ticket,
not about whichever attempt ran last, so the row now falls back to the last one the
ticket recorded.

The watcher also says what it is doing at the two moments that were previously
silent. Starting one writes the account it acts as and its polling cadence —
"is it running, and as whom?" is the first question a quiet pipeline raises — and a
tick that admitted, deferred, or errored is written down, where before only
failures were.

## v0.3.23

**The pipeline finishes the loop instead of handing it back.** Every ticket used
to stop at `person/review`: an agent did the work, opened a pull request, and then
waited for a human to review and merge — which is the half of ADR-009 that had not
been built, and the reason "supervised" looked like "manual".

`execution.review: agent` adds a second pass after a `ready` report: a fresh agent
with none of the executor's history reads the branch diff, runs the tests, and must
answer `APPROVE:` or `REJECT:` on its first line. A reviewer that moves the branch
is rejected, because a reviewer that changes what it reviews has not reviewed it,
and anything that is not a plain approval — silence, prose, "LGTM" — merges
nothing. `execution.merge: auto` then squash-merges an approved pull request, after
re-checking with GitHub that it is still mergeable with no failing checks.

Both are off by default: a project that wants a person at the merge is unchanged,
and on a protected branch GitHub still refuses a self-approval, which is reported
rather than bypassed.

## v0.3.24

**A comment on an ended ticket resumes it, which it never actually did.** Eval
returns `Attach` for a message on an ended thread — "resumes the ended ticket under
its existing mandate" — but the host's wake only looked for a *live* ticket, so the
delivery found nothing and the trigger was reported as **deferred**. Worse, a
deferred trigger holds the source's read position on itself, so every later tick
re-found the same comment and deferred it again: the pipeline stopped dead and
looked idle while it was in fact stuck.

An `Attach` for an ended ticket now reopens it and creates the pending assignment
the scheduler runs. A stopped ticket still defers, because only a person restarts a
stop.

## v0.3.25

**A pull request link opens, a ticket can be merged from the panel, and a stale
branch is brought up to date by the pipeline rather than by a person.** The link
was a plain anchor with `target=_blank`, which a webview cannot navigate to, so it
did nothing — a dead control that looked like a broken app. It now goes through the
OS browser, http(s) only. Beside it, **Merge** asks GitHub to merge the ticket's
pull request with the same gates as the automatic path.

Rebasing joins the executor's instructions: before it reports, it fetches the base,
rebases, resolves its own conflicts, re-runs the tests and pushes — a branch that
cannot merge is an unfinished ticket. The merge side does the mechanical part
without a model: a branch that is merely behind is passed to `gh pr update-branch`
and re-checked, while a conflicting one is reported back to the executor, whose job
the rebase is.

## v0.3.26

**The ticket row says whether the work landed.** Eight rows all read `prepare
exited with exit status: 2` while five of their pull requests were merged: the row
showed the newest report and nothing consulted GitHub, so the answer to "did my
work land?" was not on the screen at all.

Each row now carries GitHub's own view of its pull request — `PR #12 · merged`,
`· open`, `· conflicts`, `· behind` — from one `gh pr list` per project, cached for
fifteen seconds. A merged row says so instead of repeating a stale failure, and the
Merge button disappears once there is nothing to merge.

The stale failure itself stops recurring: a worktree the ticket has already worked
in counts as prepared, because its prepare ran before the flag existed and
re-running a non-idempotent setup command failed the ticket on work already done.

## v0.3.27

**A conflict is now worked, not reported.** When the merge found the branch
conflicting it stopped and said so, which put a mechanical problem in front of a
person. It now runs one more turn that tells the executor the branch conflicts,
has it rebase, resolve, re-run the tests and push, and then reviews and merges the
result — bounded to one extra turn so a branch that cannot be reconciled cannot
spin. A reviewer still has to approve the rebased work; resolving conflicts is not
a way around review.

**Resolved tickets leave the board.** Merged work stayed in the `ended` group, so
`ended` meant both "you have work here" and "this is done" — which is why a screen
of merged work read as stuck. Tickets whose pull request merged drop off the list,
and the header carries the count (`2 tickets · 6 merged`).

## v0.3.28

**The pipeline picks up work it left unfinished.** Everything that closes the loop
— the review, the merge, the rebase — ran inside a turn, and a ticket whose turn
had ended never got another one unless somebody commented. So a pull request that
conflicted just sat there: the pipeline was idle, the board said `conflicts`, and
nothing connected the two.

The watcher now looks for that case on every tick: a ticket whose report is `ready`
and whose pull request is still open gets a fresh assignment, the executor rebases
and resolves, the reviewer reads the result, and the merge runs. Bounded twice over
— one attempt per ticket every ten minutes, five attempts in all — so an
unreconcilable branch stops asking instead of burning turns, and says so.

## v0.3.29

**The pickup asked the wrong question, so it never fired.** v0.3.28 looked for a
ticket whose *newest* report was `ready`; for the two tickets that needed it the
newest report was a failed prepare from a retry that never ran, so they were
skipped on every tick and the log said nothing. The pull request is the fact: a
ticket with an open pull request has unfinished work whatever the last report says.

