# OpenBaD Desktop

**A DeepSeek-first, cache-first AI coding harness.**

OpenBaD Desktop is a desktop coding agent that understands your repository,
runs tools, edits code, executes tests, and remembers what matters — while
spending as few tokens as possible. It is built in Rust with a Tauri v2 shell
and a React/TypeScript interface, uses DeepSeek as its primary model provider,
and keeps a fast local working store in SQLite (FTS5 + `sqlite-vec`).

The design refines two earlier systems:

- **OpenBaD v0** — a biologically inspired cognitive architecture. Its useful
  ideas survive as dual-process routing, autonomic maintenance, cognitive
  memory, system interoception, and immune-style guardrails.
- **Clarity AI Assistant** — an enterprise cognitive assistant. Its useful
  ideas survive as evidence-grounded answers, the knowledge library, hybrid
  RAG, cognitive memory, and a typed connector model.

The v0 implementation and concept documents are preserved in
[`archive/openbad-v0/`](archive/openbad-v0/).

---

## The core bet: leverage DeepSeek's cache

DeepSeek's current models (`deepseek-flash` and `deepseek-v4-pro`) offer a 1M
token context, up to 384K output tokens, thinking mode, and automatic prefix
caching. A cache hit costs a small fraction of a cache miss — for
`deepseek-flash`, **$0.003 vs. $0.15 per 1M input tokens** off-peak. A cache
hit requires an **exact prefix match**. Most agent harnesses accidentally
destroy the prefix by injecting timestamps, reordering tools, rewriting
history, or compacting mid-prefix.

OpenBaD is designed around one invariant:

> **The model-visible prefix is immutable and append-only. Volatile context
> goes at the tail, never in the prefix.**

This yields:

- Stable system prompts and tool schemas, hashed and versioned.
- A request series that appends instead of rewrites.
- Prefix-preserving retries and compaction.
- Live cache-hit, token, and cost accounting.
- Cache-aware model routing that refuses to silently switch models mid-session.
- DeepSeek-native use of Files, Search, Fetch, FIM completion, prefix
  completion, and off-peak scheduling.

See [Cache-First Harness](docs/architecture/04-cache-first-harness.md) and
[DeepSeek-First Provider Strategy](docs/adr/007-deepseek-first-provider-strategy.md).

---

## What it does

### Coding harness

- Reads, searches, edits, and patches files with reviewable diffs.
- Runs commands in a PTY, executes tests, and surfaces diagnostics.
- Integrates Git, LSP navigation, workspace indexing, and checkpoints/rewind.
- Plans before acting, tracks todos, and runs bounded autonomous goal loops.
- Delegates to isolated subagents for exploration, review, and test triage.
- Loads skills, deterministic workflows, and MCP tools.
- Uses a thin native connector layer for Git/GitHub, Search/Fetch, and the
  filesystem, with MCP as the default third-party extension path.

### Assistant with memory and knowledge

- **Projects are containers**, not just folders: each owns a workspace, linked
  repositories, knowledge library, retrieval corpus, and durable memories.
- Project-scoped FTS5 retrieval fuses knowledge chunks, indexed code, and
  memory with citable locators.
- Knowledge documents are chunked, deduplicated by content hash, and available
  to the assistant through `search_project` and `add_knowledge`.
- Linked repositories are readable by the agent but confined to their roots.
- **Split workspace**: files, knowledge, retrieval hits, web pages, images, and
  diffs open beside the conversation instead of replacing it.
- Cognitive memory with ACT-R decay and Hebbian associations remains the
  long-term design; the current build has durable pinned/recent memory.
- Evidence gate: answers are grounded in file spans, command output, test
  results, LSP diagnostics, Git diffs, or cited web sources.

### DeepSeek-native

- **Context caching** with prefix accounting and cache-hit pricing.
- **1M context** and **384K output** used deliberately, not accidentally.
- **Thinking mode** with per-task reasoning effort.
- **Files API** for images instead of re-uploading or inlining blobs.
- **Search and Fetch APIs** with citations.
- **FIM completion** for inline code completion and **chat prefix completion**
  for constrained edits.
- **Off-peak scheduling** for maintenance, consolidation, and batch-like work.

### Autonomic system

- A heartbeat scheduler for indexing, consolidation, backups, and reminders.
- Interoception ("Pulse") for CPU, RAM, disk, GPU, context pressure, token
  spend, latency, and error rate.
- Reflex hooks for format/lint/test after edits and risky-command guards.
- Approval policies, audit logging, secret redaction, and prompt-injection
  defenses.

---

## Technology

| Layer | Choice |
| --- | --- |
| Desktop shell | Tauri v2 |
| Core runtime | Rust (edition 2024, Tokio) |
| UI | React 19 + TypeScript + Vite + Tailwind CSS 4 |
| Database | SQLite (WAL) + FTS5 + `sqlite-vec` |
| Model providers | DeepSeek-first (`deepseek-flash`, `deepseek-v4-pro`), plus optional OpenAI-compatible and local models |
| Embeddings | DeepSeek does not expose an embeddings endpoint; local ONNX (`fastembed`) by default, provider embeddings optional |
| Packaging | Signed Tauri installers for Linux, macOS, and Windows |

---

## Status

Phases 1–3 are working on Linux. The Rust workspace, single-writer SQLite store,
event projections, audit hash chain, provider/model routes, backup/doctor checks,
keychain secret storage, DeepSeek chat/streaming client, usage and cost
accounting, built-in coding tools, agent loop, and Tauri chat UI all build and
pass tests. Streamed reasoning, cancellation, and persisted model preferences
are working. The Phase 4 workspace index persists file hashes, symbol
definitions, and FTS5 text in SQLite, refreshes after each turn, and powers
`search_code`/`find_symbols`. File edits are checkpointed with sidebar diff and
restore. Phase 5 memory provides `remember`/`recall` plus automatic recent-memory
context, and Phase 6 workspace skills load on demand. Project conventions from
`AGENTS.md`/`CLAUDE.md` are included in the system prompt. Migration
`0006_project_areas` adds project-scoped reference folders, knowledge documents
and chunks, retrieval, and overview counts. New projects ask only for a name and
receive a managed workspace under the default workspaces directory. The desktop
UI now has a project/session sidebar, inline session rename, a Codex-style top
menu and project area navigation, a compact composer with access modes, a live
resizable split workspace for artifacts, and no fixed step cap. The P0 tool
surface includes reference list/glob/grep, bounded reads with totals, knowledge
CRUD, and memory audit/forget.
Per-call approvals, the evidence gate, LSP/PTY, whole-turn rewind,
embedding-backed retrieval, richer document ingestion, subagents, workflows, and
packaging remain.

- [Vision and feature set](docs/architecture/00-vision-and-feature-set.md)
- [System overview](docs/architecture/01-system-overview.md)
- [Cache-first harness](docs/architecture/04-cache-first-harness.md)
- [Implementation plan](docs/plans/0001-implementation-plan.md)
- [Architecture decision records](docs/adr/)

---

## Headless live check

Run one real DeepSeek turn with the workspace tools and streaming observer:

```bash
cargo run -p openbad-agent --example live_chat -- "Read Cargo.toml and summarize it"
```

It reads the key from the OpenBaD Desktop keychain entry, or falls back to
`DEEPSEEK_API_KEY`.

## Repository layout

```text
.
├── apps/
│   └── desktop/            # Tauri shell + React UI
├── crates/                 # Rust workspace
│   ├── openbad-core/       # Domain types, events, config, errors
│   ├── openbad-store/      # SQLite, migrations, projections, search
│   ├── openbad-harness/    # Prompt assembly, routing, cache planning
│   ├── openbad-llm/        # Provider adapters, cache accounting
│   ├── openbad-agent/      # Turn/step loop and tool dispatch
│   ├── openbad-tools/      # Tool registry and built-in tools
│   ├── openbad-workspace/  # Files, Git, shell, LSP, indexing
│   ├── openbad-memory/     # Engrams, ACT-R, consolidation
│   ├── openbad-knowledge/  # Library, ingestion, RAG
│   ├── openbad-autonomic/  # Heartbeat, jobs, schedules, pulse
│   ├── openbad-governance/ # Policy, approvals, audit, secrets
│   ├── openbad-mcp/        # MCP client and server management
│   └── openbad-update/     # Release checks and in-app updates
├── docs/                   # Architecture, plans, ADRs
├── tests/                  # Cross-crate integration tests
└── archive/                # Archived v0
```

---

## Development

```bash
# Rust workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace

# Desktop app
cd apps/desktop
pnpm --dir ui install
pnpm --dir ui build
./ui/node_modules/.bin/tauri dev
```

`pnpm --dir ui build` produces `apps/desktop/ui/dist`, which the Tauri shell
embeds. Provider keys use the `SecretStore` abstraction; the platform keychain
backend is available behind the `keyring` feature and is never written to the
repository or the SQLite database.

---

## License

MIT. See [LICENSE](LICENSE).
