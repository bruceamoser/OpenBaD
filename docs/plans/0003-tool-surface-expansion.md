# 0003 — Tool Surface Expansion Guide

## 1. Purpose

This is the working guide for adding capabilities to the model's tool surface.
It answers three questions in order:

1. Why can't we just add tools? (Section 2 — the tool budget.)
2. Which capabilities are missing, and in what order should they land?
   (Sections 5–11.)
3. What does each one look like when it is done? (Tasks and acceptance criteria
   per workstream.)

Most of the capabilities proposed here are **already in
[0001 — Implementation Plan](0001-implementation-plan.md)** as Phase 4 and
Phase 6 tasks. They have not shipped. This document does not replace those task
IDs; it supplies the design detail, the ordering, and — critically — the slot
accounting that 0001 does not address.

| Workstream | Existing task | Feature id | Status |
| --- | --- | --- | --- |
| Consolidation (free slots) | — | — | Not started |
| LSP code intelligence | P4-03 | F-16 | Not started |
| MCP client + proxy tool | P6-06 | F-21 | Done |
| Persistent terminal (PTY) | P4-04 | — | Done |
| Workflow engine | P6-05 | F-20 | Done |
| Session history search | — | — | Not started |

## 2. The constraint nobody documented

The tool surface is **capped at 128**. This is enforced in code but absent from
every architecture document:

- `ToolRegistry::with_builtin_tools()` (`crates/openbad-tools/src/lib.rs:992`)
  registers **exactly 128** tools — counted, not estimated.
- The guard test `every_builtin_has_a_schema`
  (`crates/openbad-tools/src/lib.rs:15281`) asserts `definitions.len() <= 128`.

**There is zero headroom.** Every capability added must be paid for by removing,
merging, or re-architecting something else. Any proposal that ignores this is
not implementable as written, which is why Section 6 (consolidation) comes before
every feature workstream.

Consider documenting the limit in
[05 — Agent Runtime and Tools](../architecture/05-agent-runtime-and-tools.md) so
future contributors do not discover it via a failing test.

## 3. Where the surface stands

Current strengths are governance, memory, and process: knowledge, canon, claims
and dead-ends, plan/gates, context residency, subagents. Current weaknesses are
*environment* capabilities — code intelligence, interactive execution, and
orchestration.

Rough grouping of the 128 built-ins:

| Group | Count | Notes |
| --- | --- | --- |
| Filesystem and file ops | ~19 | read/write/edit/delete/move, trash, preview, undo, gc |
| Search and index | 3 | `search_code`, `search_project`, `references_to` |
| Repo / reference folders | 8 | `*_repo*` family, `list_project_sources`, `classify_path` |
| Git and GitHub | 14 | 13 `git_*` plus one `github` mega-tool |
| Execution | 7 | `run_command`, `run_tests`, five `job_*` |
| Memory | 5 | remember/recall/list/update/forget |
| Knowledge | 8 | add/list/get/update/export/import/review/delete |
| Plan, todo, gates | 10 | `plan_*`, `todo_*`, recipe/gate/task runners |
| Governance | 11 | canon, glossary, ADR, invariants, assertions |
| Context residency | 7 | pin/exclude rules, `context_map`, `context_report` |
| Claims, unknowns, dead-ends | 6 | record/list pairs |
| Session, skills, plugins | 13 | checkpoint, resume, handoff, `skill_*`, `plugin_*` |
| Docs and artifacts | 6 | `present_artifact`, `yaml_query`, archive, diff, citation |
| Snapshots and golden files | 5 | `snapshot_*`, `golden_*` |
| Human interaction | 3 | `ask_question`, `list_questions`, `request_fs_access` |
| Subagents | 1 | `subagent` (spawn/list/status/wait/cancel) |
| Web, registry | 2 | `web_fetch`, `registry_lookup` |

## 4. Rules for adding a tool

Follow these or the addition will cost more than it returns.

### 4.1 Use a capability seam

Domain crates never depend on infrastructure. Add a trait in
`crates/openbad-tools/src/lib.rs`, expose it on `ToolContext` as
`Option<Arc<dyn Trait>>`, and implement it in the host
(`apps/desktop/src-tauri/src/lib.rs`).

Existing precedents — copy this shape:

| Trait | Host impl |
| --- | --- |
| `MemoryStore` | `lib.rs:74` |
| `ProjectAreas` | `lib.rs:166` |
| `SubagentRuntime` | `lib.rs:481` |
| `CheckpointSink` | `lib.rs:742` |
| `WorkspaceSearch` | `lib.rs:761` |

Mock implementations (for example `MockSubagents` at
`crates/openbad-tools/src/lib.rs:15145`) keep the tool layer testable without a
host.

### 4.2 Prefer stable proxy tools over static schemas

[04 — Cache-First Harness](../architecture/04-cache-first-harness.md) §6 already
specifies this, and it is the correct answer to the 128 cap:

> A naive harness injects every MCP tool into the schema list. Connecting a new
> server then changes the schema hash and invalidates the entire prefix.

The specified proxy set is `mcp_call`, `skill`, `tool_search`, and `workflow`,
with the real catalog injected as a **tail message** rather than the static
schema. Only `skill` exists today, and only partially (`list_skills`,
`read_skill`, `write_skill` are three static tools, not one proxy).

### 4.3 Respect the cache invariants

From the project conventions, the ones that bite when adding tools:

- **Invariant 2** — tool schemas are sorted and canonicalized.
- **Invariant 3** — the tool schema set is stable within a request series.
- **Invariant 6** — retries reuse the exact frozen request bytes.

Practical consequences:

- A tool whose *schema* changes (for example, one that exposes discovered MCP
  tools as top-level parameters) breaks invariant 3. Use a stable proxy instead.
- A tool whose *results* change is fine; results live in history, not the prefix.
- Never let server startup, discovery, or connection state leak into the stable
  prefix.

### 4.4 Mind the slot economics

A capability that needs N tools is N times more expensive than one that needs
one. Prefer one tool with an `operation` or `action` field — the `github`
mega-tool already proves the pattern is workable at this scale.

## 5. What comparable harnesses ship

Two references were reviewed. Details in Appendix A.

**VS Code** (in-tree agent tools): `readFile`, `editFile`, `findFiles`, `usages`,
`renameSymbol`, `lsp`, `problems` (diagnostics), `runInTerminal`, `runTests`,
`createAndRunTask`, `fetchWebPage`, `askQuestions`, `reviewPlan`, `readSkill`,
`runSubagent`, plus debugger tools.

**DeepSeek Harness** (`dsh`, same model family): a much broader capability map —
`tool-lsp`, `tool-workflow`, `tool-session-query`, `tool-terminal` and persistent
bash/pwsh, `sandbox-*`, `ssh`, `browser-use`, `computer-use`, `ptc-runtime`,
`spill-*`, `compaction-*`, `tool-goal`, `tool-ralph`, `tool-agent-team`, `acp`,
and subagent backends that delegate to other agents' CLIs.

The gap that hurts most in daily use is **code intelligence**: the agent can edit
code but cannot see the compiler errors it just created, except by running a
command and parsing stderr by hand.

## 6. Workstream 0 — Free the slots

**Goal:** recover roughly 11 slots so feature work has somewhere to land.
**Do this first.** It is low-risk, mechanical, and unblocks everything else.

| ID | Task | Slots | Acceptance |
| --- | --- | --- | --- |
| W0-01 | Merge `record_claim`/`list_claims`, `record_unknown`/`list_unknowns`, `record_dead_end`/`list_dead_ends` into one `ledger` tool with `kind` + `action` | +3 | All three ledgers still readable via `ledger(action=list, kind=...)`; existing `.openbad/*.json` formats unchanged |
| W0-02 | Merge `snapshot_create`/`list`/`restore` into `snapshot`, and `golden_compare`/`accept` into `golden` | +3 | Snapshot round-trip and golden diff tests pass |
| W0-03 | Merge `canon_add`/`query`/`lint`/`ban` and `glossary_add`/`lookup` into `canon` | +3 | Canon lint still scans banned phrases; superseded entries still excluded |
| W0-04 | Merge `pin_context`/`exclude_context`/`list_context_rules` into `context_rules` | +2 | `.openbad/context.yaml` round-trips; pinned/excluded rules still visible in `context_map` |
| W0-05 | Document the 128 limit in architecture doc 05 | 0 | Doc states the limit and the proxy-tool policy |

### Notes

- `list_context_rules` and `context_map` already overlap; W0-04 should not make
  that worse.
- Keep the wire-level behaviour identical where possible; these are schema
  renames, not rewrites. Every removed name should have a test asserting it is
  gone (the `every_builtin_has_a_schema` test already does this for the
  deprecated GitHub aliases).
- Do not merge tools across trust boundaries — approval-gated actions must stay
  individually identifiable in the audit log.

## 7. Workstream A — LSP code intelligence (P4-03, F-16)

**Goal:** the agent can ask a real language server where a symbol is defined,
who references it, and what the type is — instead of guessing from `grep`.

**Model-facing shape: one read-only tool, four operations.** Not four tools.

```
lsp(
  operation: "goToDefinition" | "findReferences" | "goToImplementation" | "hover",
  file_path: string,     // relative to workspace, or absolute
  line: number,          // one-based cursor line
  character: number      // one-based cursor character (UTF-16)
)
```

Design points worth taking from `dsh` verbatim:

- **Coordinates**: one-based UTF-16 at the model boundary, converted to
  zero-based positions at the seam. Models are unreliable at zero-based math.
- **`findReferences` always includes the declaration.** Prevents a whole class of
  "you missed the definition" retries.
- **Caps with omission markers**: `maxLocations` 100, `maxResultChars` 16000.
  Truncate visibly, never silently.
- **Timeout budget** ~60 s covering the open/query/close lifecycle.
- **Prompt guidance lives with the tool**, not the persona: *"Use search/read for
  ordinary navigation. Use `lsp` when textual matches are ambiguous, or before a
  change that requires precise definitions, implementations, or references."*
  Without this the model burns LSP calls on work `grep` answers faster.
- **No fallback directory.** Require an explicit workspace; LSP without a project
  root returns nonsense.

### Tasks

| ID | Task | Acceptance |
| --- | --- | --- |
| A-01 | New crate `crates/openbad-lsp`; add to workspace members | `cargo check -p openbad-lsp` succeeds |
| A-02 | `LspService` trait + `ToolContext.lsp` seam | Tool compiles with `lsp: None` and returns a clear error |
| A-03 | `lsp` tool: schema, arg parsing, coordinate conversion | Unit tests for UTF-16 one-based ↔ zero-based |
| A-04 | Host impl: stdio server manager, lifecycle, restart | Rust/TS/Python smoke tests (P4-03 criterion) |
| A-05 | Result rendering, caps, omission markers | Oversized results truncate with a marker |
| A-06 | Prompt section for tool guidance | Guideline appears in the prompt, not the persona |

### Tests

- Coordinate conversion, including astral-plane characters.
- Cap and truncation behaviour.
- Server-down and server-crash paths return typed errors, never panics.
- Smoke tests against fixture servers for Rust, TS, and Python.

### Risks

- **Latency.** Cold server start can take seconds. Do not block the turn waiting
  for full project indexing; return what is available and say so.
- **Server management is the hard part**, not the tool. Budget accordingly.
- Long-term this supersedes `find_symbols` (currently textual), which frees a slot.

## 8. Workstream B — MCP client and `mcp_call` (P6-06, F-21)

**Goal:** connect any MCP server and gain its tools without changing the request
prefix hash — the single largest capability-per-slot win available.

`crates/openbad-mcp/src/lib.rs` is currently a stub: `McpServerConfig` and
`McpTransport` only. No JSON-RPC client, no lifecycle, no bridging, and no `mcp`
tool is registered.

### Tasks

| ID | Task | Acceptance |
| --- | --- | --- |
| B-01 | JSON-RPC 2.0 client over stdio | `initialize` handshake, `tools/list`, `tools/call` |
| B-02 | HTTP transport | Same surface as stdio |
| B-03 | Server lifecycle supervision | Server or app lifetime, per [01 — System Overview](../architecture/01-system-overview.md) |
| B-04 | `allowed_tools` enforcement | Disallowed tool returns a typed error |
| B-05 | Register `mcp_call(server, tool, arguments)` | Stable schema; catalog rendered as a tail message |
| B-06 | Catalog injection as tail message | Connecting/removing a server leaves the prefix hash unchanged |
| B-07 | Untrusted-output handling | Descriptions shown in UI before trust; output fenced as untrusted |

### Acceptance

The P6-06 criterion, restated: **connecting or removing an MCP server does not
change the request prefix hash.** Assert this in a test, not by inspection —
cache invariants are release gates.

### Risks

- MCP tool output is **untrusted** and is a documented prompt-injection vector
  ([07 — Autonomic System and Governance](../architecture/07-autonomic-system-and-governance.md),
  [09 — Security and Threat Model](../architecture/09-security-and-threat-model.md)).
  Treat tool descriptions as untrusted metadata.
- Third-party servers may hang. Every call needs a timeout and a kill path.
- Do not register one tool per MCP tool. That is the documented anti-pattern.

## 9. Workstream C — Persistent terminal (P4-04)

**Goal:** drive REPLs, dev servers, and interactive prompts. Today there is
one-shot `run_command` plus background jobs — no PTY, no stdin, no
`portable-pty` dependency anywhere in the workspace.

| ID | Task | Acceptance |
| --- | --- | --- |
| C-01 | `pty` capability seam + host impl | Process group spawned and killed cleanly |
| C-02 | `terminal` tool: `create`/`send`/`read`/`close` | Round-trip test against a shell |
| C-03 | Process-group kill on close and on turn cancel | No orphaned children (existing PTY/job kill tests) |
| C-04 | Output spilling for long sessions | Output capped with a notice, not silently dropped |

Consider one `terminal` tool with an `action` field rather than four tools.

**Risks:** orphan processes are the classic failure. Jobs already kill process
groups — reuse that path rather than inventing a second one.

## 10. Workstream D — Workflow engine (P6-05, F-20)

**Goal:** let one tool call express a loop or fan-out that would otherwise be
dozens of round-trips. This is the main token-cost lever.

`dsh` implements this as a **tiny stable schema over a swappable engine**:

- The tool runs a scripting-language orchestration script and returns its final
  value.
- Parsing, execution, caps, and cancellation live behind a `ctx.workflowEngine`
  seam, so the engine can be hardened or replaced without changing what the model
  sees.
- `run_in_background: true` registers the run as a job, returns the id
  immediately, streams progress through the job output ring, and delivers the
  value with the completion notice.
- `maxResultChars` ~50000 with a truncation notice.

That background path maps directly onto the existing `job_start`/`job_status`/
`job_logs` surface and the `jobs` table. Most of this is **composition of seams
we already have**; the genuinely new part is a sandboxed interpreter
(`rquickjs` or `boa` in Rust).

| ID | Task | Acceptance |
| --- | --- | --- |
| D-01 | `WorkflowEngine` seam + minimal script runtime | Script returns a value; errors are typed |
| D-02 | `workflow` tool with stable schema | Schema unchanged across workflow edits |
| D-03 | Background mode over the jobs seam | Progress visible in `job_logs`; value on completion |
| D-04 | Access-mode enforcement at execution time | Read-only session cannot run a write workflow |
| D-05 | Deterministic run + stop-on-approval-denial | P6-05 and Phase 6 acceptance criteria |

**Risks:** a script interpreter is a sandbox boundary. It must inherit and
enforce the session access mode, and it must not become a way to bypass
approval-gated actions.

## 11. Workstream E — Session history search

**Goal:** answer "what did we decide last week?" Today `search_project` covers
knowledge, code, and memory — but not the session event log. `session_checkpoint`
and `session_resume` only handle labelled checkpoints.

The event log is already the source of truth
([03 — Data Model and Storage](../architecture/03-data-model-and-storage.md)), so
this is an index away.

```
search_sessions(query: string, limit?: number)
  -> [{ session_id, seq, kind, snippet, when }]

read_session(session_id: string, from_seq?: number, to_seq?: number)
  -> events
```

| ID | Task | Acceptance |
| --- | --- | --- |
| E-01 | FTS index over session events (migration + backfill) | Search returns hits with `session#seq` locators |
| E-02 | Workspace authorization boundary | A session cannot read another workspace's history |
| E-03 | `search_sessions` + `read_session` tools | Two slots, or one `session` tool with `action` |

**Note the security dimension.** `dsh` gives this its own authorization module
(`workspace-access.ts`); cross-workspace history reads should be treated as a
security boundary, not a convenience.

## 12. Slot ledger

Starting point: **128 / 128 used**.

| Change | Delta | Running total |
| --- | --- | --- |
| Consolidation (W0-01…W0-04) | +11 | 117 |
| LSP (`lsp`) | −1 | 118 |
| MCP (`mcp_call`, `tool_search`) | −2 | 120 |
| Terminal (`terminal`) | −1 | 121 |
| Workflow (`workflow`) | −1 | 122 |
| Session search (`search_sessions`, `read_session`) | −2 | 124 |
| Reserve for unplanned work | — | **4 free** |

Keep at least four slots free as headroom for hotfixes.

## 13. Sequencing

1. **W0 consolidation** — frees slots, no new capability. Ship alone so it is
   easy to revert.
2. **A — LSP** — highest daily-use value and self-contained. Closes the
   compile-error loop.
3. **B — MCP** — highest breadth per unit of work, but the largest build.
4. **C — terminal** — unblocks REPLs and dev servers.
5. **D — workflow** — the cost lever; depends on terminal and subagents being solid.
6. **E — session search** — needs a new index and an auth boundary; lowest urgency.

Each workstream is one issue, one branch, one PR, per the repository SOP.

## 14. Definition of done

- The built-in count is at or below 128 with at least four slots free, and the
  cap is documented in architecture doc 05.
- A connecting-and-removing-MCP-servers test proves the request prefix hash is
  unchanged.
- The agent can fix a compile error without parsing `run_command` stderr by hand.
- A long-running process can be started, written to, read, and killed with no
  orphans.
- Every new capability seam has a mock implementation and unit tests.
- `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`,
  and the workspace test suite pass.

## 15. Open questions

1. **Raise the cap instead of consolidating?** The limit is enforced only by a
   test. If 128 reflects a real provider or billing constraint, consolidation is
   mandatory; if it is a self-imposed guardrail, raising it may be cheaper than
   merging eleven tools. Decide before W0.
2. **Which language servers ship by default?** P4-03 implies Rust/TS/Python.
   Shipping zero and letting users configure them is a legitimate v1 choice.
3. **Script language for workflows** — JS for ecosystem familiarity, or a smaller
   purpose-built DSL that is easier to sandbox?
4. **MCP trust model** — per-server opt-in, or allowlist individual tools?
   `McpServerConfig.allowed_tools` already exists and suggests per-tool.
5. **Nested subagents** — `P6-01` implies depth limits but today's `subagent`
   cannot nest. Does workflow fan-out need nesting, or flat fan-out only?

## Appendix A — Reference designs reviewed

Both on disk at the time of writing; re-verify before implementing.

**VS Code** (`/mnt/disk4/repos/vscode`): tool ids found in
`src/vs/workbench/contrib/chat` include `readFile`, `editFile`, `findFiles`,
`usages`, `renameSymbol`, `lsp`, `problems`, `runInTerminal`, `runTests`,
`createAndRunTask`, `fetchWebPage`, `askQuestions`, `reviewPlan`, `readSkill`,
`runSubagent`, `resolveDebugEventDetails`. Notable: semantic rename and
diagnostics are first-class, and there are debugger tools.

**DeepSeek Harness** (`/mnt/disk4/repos/deepseek-harness`): a pnpm monorepo whose
`packages/` directory functions as a capability map. Most relevant sources:

| Path | What it does |
| --- | --- |
| `packages/lsp/tool-lsp/src/index.ts` | The `lsp` tool: 4 operations, caps, coordinate conversion |
| `packages/workflow/tool-workflow/src/index.ts` | `workflow` tool over a swappable engine seam |
| `packages/experimental/tool-session-query/` | Session history search with a workspace access boundary |
| `packages/shell/tool-bash-persistent`, `packages/terminal/tool-terminal` | Persistent shell/PTY |
| `packages/subagent/subagent-claude-code`, `subagent-codex` | Subagents that delegate to other agents' CLIs |
| `packages/compaction/*`, `packages/spill/*` | Compaction strategies and tool-output spilling |

## Appendix B — Tooling defect found during this review

After filesystem access to `/mnt/disk4/repos` was approved, `list_dir` and
`stat_file` on that path still returned `path is outside the workspace`, while
`run_command` with `allow_external_cwd: true` succeeded on the same path.

The access grant is honoured by one code path and not the other. The filesystem
tools should consult the same `outside_access_grants` check that `run_command`
uses. This is a usability bug, not a security hole — access was correctly denied —
but it makes approved paths unusable with the file tools.
