# 00 — Vision and Feature Set

## 1. Product statement

**OpenBaD Desktop is a DeepSeek-first AI coding harness that treats context as
a cache, not a scratchpad.**

It runs as a desktop app, works inside a chosen repository, and combines four
things that are usually separate products:

1. A **coding agent** that reads, edits, runs, tests, and reviews code.
2. A **cognitive assistant** that remembers decisions, preferences, and project
   knowledge over time.
3. A **knowledge and RAG system** with citations down to file and line.
4. A **cache-first model runtime** that minimizes token cost and latency by
   preserving DeepSeek's prefix cache and using DeepSeek-native capabilities
   wherever they fit.

The desktop app is built with Rust and Tauri v2, uses DeepSeek (`deepseek-flash`
and `deepseek-v4-pro`) as its primary provider, and keeps a fast local working
store in SQLite (FTS5 + `sqlite-vec`). The product assumes DeepSeek
connectivity; local storage is an implementation choice for latency and
durability, not a local-first ideology.

## 2. Why this exists

### What v0 got right

OpenBaD v0 had a strong thesis: an agent should have fast instincts, deliberate
reasoning, memory that decays and consolidates, system awareness, and
governance. Those ideas are still useful.

### What v0 got wrong

The implementation paid a large complexity tax for biological fidelity:

- MQTT, protobuf, systemd, eBPF, and a multi-service topology for a
  single-user assistant.
- Python/LangGraph runtime overhead and a fragmented web/TUI surface.
- Simulation of drives and emotions that did not change user-visible behavior.
- No coherent story for token cost, cache reuse, or coding workflows.

### What Clarity got right

Clarity contributed evidence-grounded answers, a knowledge library, cognitive
memory (ACT-R + Hebbian), hybrid RAG, typed connectors, workflows, and
persona-aware responses.

### What Clarity got wrong for this product

Clarity is a multi-tenant enterprise platform:

- Azure hosting, Entra ID, PostgreSQL, Azure AI Search, ADLS, .NET data APIs,
  and queue-based workers.
- A Python/LangGraph harness and a React enterprise console.
- Operational complexity that makes no sense for a local desktop coding tool.

### The refined thesis

Keep the **useful cognitive behaviors**, drop the **distributed-system
machinery**, and make **cache-efficient context management** a first-class
architectural concern. Build the harness in Rust so that persistence, process
control, streaming, and UI latency are all under one runtime.

## 3. Design principles

| # | Principle | Consequence |
| --- | --- | --- |
| P1 | **Prefix immutability** | Never rewrite, reorder, or mutate model-visible history. Append only. Volatile context goes last. |
| P2 | **Model-visible means logged** | Every input the model sees is reconstructable from the session event log. |
| P3 | **Evidence before synthesis** | Non-trivial answers are grounded in validated tool output, file spans, tests, diffs, or citations. |
| P4 | **Bounded autonomy** | Hard step, token, time, and cost budgets. Every irreversible action is approval-gated. |
| P5 | **DeepSeek-first** | DeepSeek is the default for generation, reasoning, extraction, classification, summarization, search, and fetch. Other providers are fallbacks or fill capability gaps. |
| P6 | **Capability seams** | Every major capability is a trait with a swappable provider and a tested consumer. |
| P7 | **One source of truth** | SQLite is the system of record; projections are derived, never authoritative. |
| P8 | **Cache-aware by construction** | Prompt sections, tool schemas, and request envelopes are content-addressed and versioned. |
| P9 | **Explainable retrieval** | Memory and RAG return scoring breakdowns, not opaque similarity numbers. |
| P10 | **Transparent autonomy** | The UI always shows what the agent is doing, what it will cost, and what it has changed. |
| P11 | **Keyboard-first, accessible** | Every action has a command and a shortcut; no mouse-only workflows. |
| P12 | **Refine, don't simulate** | Biological metaphors survive only when they produce a concrete product behavior. |

## 4. Feature set

Priority: **P0** = MVP, **P1** = v1.0, **P2** = later. Phase refers to
[`../plans/0001-implementation-plan.md`](../plans/0001-implementation-plan.md).

### A. Harness core

| ID | Feature | Source | Priority | Phase |
| --- | --- | --- | --- | --- |
| F-01 | Append-only session event log as the source of truth | DSH | P0 | 1 |
| F-02 | Cache-first prompt assembly with stable sections and hashes | DSH | P0 | 2 |
| F-03 | DeepSeek cache-hit accounting, cost ledger, and cache meter | DSH + DeepSeek | P0 | 2 |
| F-04 | Token meter, per-node context pricing, and pressure gauge | DSH | P0 | 2 |
| F-05 | Prefix-preserving retry, compaction, and summarization | DSH | P0 | 2 |
| F-06 | Dual-process routing: reflex path vs. deliberate agent loop | OpenBaD | P1 | 3 |
| F-07 | Evidence contract and deterministic evidence gate | Clarity | P0 | 3 |
| F-08 | Bounded agent loop with cancellation, HITL, and budgets | DSH + OpenBaD | P0 | 3 |
| F-09 | Streaming responses, tool cards, and incremental transcripts | DSH | P0 | 3 |
| F-10 | Session fork, resume, search, titles, and references | DSH | P1 | 4 |

### B. Coding harness

| ID | Feature | Source | Priority | Phase |
| --- | --- | --- | --- | --- |
| F-11 | File read/write/edit/apply-patch with read-before-write safety | DSH | P0 | 3 |
| F-12 | Grep, glob, repo map, and symbol search | DSH | P0 | 3 |
| F-13 | Shell/PTY execution and background jobs | DSH | P0 | 4 |
| F-14 | Git status/diff/log, commit and push approval | DSH + new | P1 | 4 |
| F-15 | Test runner, diagnostics, and failure triage | New | P0 | 4 |
| F-16 | LSP navigation: definition, references, implementation, hover | DSH | P1 | 4 |
| F-17 | Diff review, hunk accept/reject, checkpoint and rewind | DSH + Reasonix | P1 | 4 |
| F-18 | Plan mode, todo list, and bounded goal loop | DSH | P1 | 5 |
| F-19 | Subagents with depth, budget, and tool-scope limits | DSH | P1 | 5 |
| F-20 | Skills (markdown procedures) and deterministic workflows | DSH + Clarity | P1 | 5 |
| F-21 | MCP client for tools, resources, and prompts | DSH | P1 | 5 |
| F-22 | Web search and fetch with citation capture | DSH + Clarity | P1 | 5 |
| F-23 | Browser and computer use | DSH | P2 | 7 |
| F-24 | Schedules, reminders, and recurring maintenance | DSH + OpenBaD | P2 | 6 |

### C. Memory and knowledge

| ID | Feature | Source | Priority | Phase |
| --- | --- | --- | --- | --- |
| F-25 | Engram memory with ACT-R decay and Hebbian associations | Clarity + OpenBaD | P1 | 5 |
| F-26 | Hybrid retrieval: FTS5 + vectors + associations + recency | Clarity + OpenBaD | P1 | 5 |
| F-27 | Memory extraction, consolidation ("sleep"), and archival | Clarity + OpenBaD | P1 | 6 |
| F-28 | Knowledge library with ingestion, versions, chunks, and citations | Clarity | P1 | 5 |
| F-29 | Workspace indexing and code-aware RAG | New | P0 | 4 |
| F-30 | Operator profile and preferences (lightweight persona) | Clarity | P1 | 5 |
| F-31 | Memory scoping, privacy controls, export, and forget | New | P1 | 5 |

### D. Autonomic system and governance

| ID | Feature | Source | Priority | Phase |
| --- | --- | --- | --- | --- |
| F-32 | Heartbeat scheduler for indexing, maintenance, and reminders | OpenBaD | P1 | 6 |
| F-33 | Interoception ("Pulse"): system, context, and cost health | OpenBaD | P1 | 6 |
| F-34 | Reflex hooks: format/lint/test after edits, command guards | OpenBaD | P2 | 6 |
| F-35 | Immune guardrails: injection defense, secret scanning, redaction | OpenBaD + new | P0 | 3 |
| F-36 | Approval system with risk levels and workspace presets | DSH | P0 | 3 |
| F-37 | Tamper-evident audit log | Clarity + new | P0 | 3 |
| F-38 | Sandboxed execution with workspace confinement | DSH + new | P1 | 6 |
| F-39 | Affective/endocrine modes (focus, careful, explore, budget) | OpenBaD | P2 | 7 |

### E. Platform

| ID | Feature | Source | Priority | Phase |
| --- | --- | --- | --- | --- |
| F-40 | Tauri v2 desktop shell and React/TypeScript UI | New | P0 | 3 |
| F-41 | SQLite storage, migrations, backup, and export | New | P0 | 1 |
| F-42 | Provider abstraction: DeepSeek-first, optional compatible/local | Clarity + DSH | P0 | 2 |
| F-43 | Observability, traces, metrics, and benchmark suite | DSH + new | P1 | 2 |
| F-44 | Signed installers and auto-update | New | P2 | 8 |
| F-45 | Keyboard-first UX and accessibility | New | P1 | 3 |
| F-46 | Capability seams and plugin-ready architecture | DSH | P1 | 1 |
| F-47 | DeepSeek Files API for image reuse | DeepSeek | P1 | 2 |
| F-48 | DeepSeek Search and Fetch with citations | DeepSeek | P1 | 3 |
| F-49 | FIM and chat prefix completion for inline/constrained edits | DeepSeek | P1 | 4 |
| F-50 | Thinking-mode budgets and reasoning-effort routing | DeepSeek | P0 | 2 |
| F-51 | Off-peak scheduling for maintenance and auxiliary work | DeepSeek | P1 | 6 |
| F-52 | Optional encrypted cloud backup/sync | New | P2 | 8 |
| F-53 | Connector layer: MCP-first, with thin native Git/GitHub/web connectors | Clarity + DSH | P1 | 5 |
| F-54 | Parallel agent sessions with independent run state and per-session cancellation | DSH + VS Code | P1 | 9 |

## 5. What we keep from each system

### From Clarity

- **Knowledge library** — collections, documents, versions, chunks, citations.
- **Cognitive memory** — engrams, ACT-R temporal decay, Hebbian associations,
  hybrid retrieval, explainable scores.
- **RAG** — ingestion, chunking, embeddings, hybrid search, grounded answers.
- **Connectors** — typed data/action interfaces, but simplified and MCP-first.
  Native connectors are reserved for capabilities where MCP is a poor fit
  (Git/GitHub, DeepSeek Search/Fetch, filesystem). Third-party integrations use
  MCP rather than a bespoke connector framework.
- **Evidence contract and gate** — extended to code, tests, Git, and LSP.
- **Persona** — reduced to an operator profile and preferences, with optional
  OCEAN traits.

### From OpenBaD

- **Dual-process routing** — cheap reflex path for simple work, deliberate loop
  for multi-step tasks.
- **Autonomic heartbeat** — scheduled maintenance and background work.
- **Memory consolidation** — idle-time dedupe, summarize, promote, decay.
- **Interoception** — local system and runtime health in the UI.
- **Immune guardrails** — untrusted-content tagging, injection defense, secret
  scanning, command risk classification.
- **Reflex hooks** — deterministic post-edit and pre-command actions.
- **Affective modes** — optional, explicit, and transparent control presets.

### From DeepSeek Harness (DSH)

- **Append-only session log** as the model-visible source of truth.
- **System prompt and tool schema assembly** with stable ordering and hashes.
- **Prefix-cache discipline** across retries, compaction, pruning, and routing.
- **Token meter** with per-node pricing and context pressure.
- **Tool pipeline** with typed schemas, output contracts, spill, and approval.
- **Subagents**, **plan mode**, **todo**, **goal loop**, **skills**, **MCP**,
  **LSP**, **jobs**, **schedules**, and **session search**.
- **Capability seams** — every capability has a definition, provider, consumer.

### From the DeepSeek API itself

- **1M-token context** and **up to 384K output tokens**.
- **Thinking mode** with per-task reasoning effort.
- **Automatic prefix caching** with hit/miss accounting.
- **Files API** for images.
- **Search and Fetch APIs** with in-house web access.
- **FIM completion** and **chat prefix completion** (beta).
- **Off-peak pricing** at half the peak rate.
- **`user_id` isolation** for cache, safety, and scheduling.
- **Anthropic-compatible API** as an alternate wire format.

## 6. What we deliberately drop

### From Clarity

- Multi-tenancy, Entra ID, Azure hosting, Azure AI Search, ADLS, .NET data APIs.
- Queue-based background workers and a cloud deployment topology.
- The Python/LangGraph harness and enterprise admin console.
- OCEAN as a full psychological simulation; it becomes optional profile data.

### From OpenBaD

- MQTT, protobuf, systemd units, eBPF probes, and the multi-service topology.
- The Python implementation and Svelte/TUI surfaces.
- Biological drives that do not change behavior.
- System 1/System 2 as a literal brain simulation; it becomes a routing policy.

### From DSH

- The Cordis/TypeScript plugin runtime. We borrow the **seam pattern**, not the
  framework.
- The Electron desktop shell. We use Tauri v2.
- Node-specific package composition.

## 7. Non-goals

- A cloud-hosted multi-user assistant (optional backup/sync is later).
- A general-purpose chat app that happens to edit files.
- A fully autonomous agent with unbounded spend or no approvals.
- A benchmark-maximizing agent that sacrifices reviewability.
- A model-training or fine-tuning platform.
- A provider-neutral product that treats DeepSeek as an afterthought.

## 8. Primary use cases

1. **Repository Q&A** — "How does auth work in this service?" with cited file
   spans and no full-repo dump into context.
2. **Scoped change** — "Add retry to the HTTP client and tests" with plan,
   diff review, test run, and checkpoint.
3. **Debug** — "Why does this test fail?" with tool-driven reproduction,
   evidence gate, and a minimal patch.
4. **Review** — "Review my working tree for correctness and security."
5. **Project memory** — "What did we decide about the database, and why?"
6. **Knowledge work** — ingest ADRs, specs, and runbooks; ask grounded
   questions with citations.
7. **Repeated workflow** — run a deterministic release/checklist playbook with
   approval gates.
8. **Inline completion** — FIM-based code completion in the editor.
9. **Constrained edit** — chat prefix completion for a forced format or code
   block.

## 9. Success metrics

| Metric | Target |
| --- | --- |
| Cache-hit share after the first turn (multi-turn coding session) | ≥ 85% |
| Cache-hit share after turn 5 | ≥ 92% |
| Input-token cost vs. a cache-naive harness | −60% or better |
| Cost per completed coding task vs. a naive harness | −40% or better |
| First token latency (warm connection) | p95 < 1.5 s |
| Local DB query latency | p95 < 10 ms |
| Workspace index throughput | 10k files in < 60 s |
| Memory retrieval latency | p95 < 50 ms |
| Tool call success rate (non-user-error) | ≥ 98% |
| Crash-free sessions | ≥ 99.5% |
| Approval prompt latency | p95 < 100 ms |
| Installer size (desktop shell, excluding models) | < 30 MB |

## 10. Glossary

| Term | Meaning |
| --- | --- |
| **Engram** | A unit of cognitive memory with activation and associations. |
| **Prefix** | The exact leading token sequence a provider caches. |
| **Request series** | A contiguous set of requests that share a prefix and model route. |
| **Cache hit** | Input tokens served from the provider's prefix cache. |
| **Compaction** | Replacing old history with a summary while preserving recent turns. |
| **Spill** | Writing oversized tool output to a file and giving the model a locator. |
| **Capability seam** | A trait definition + provider + consumer triple. |
| **Evidence gate** | Deterministic validation that filters ungrounded or stale evidence. |
| **Pulse** | The interoception panel showing system and runtime health. |
| **Sleep** | Idle-time memory consolidation and maintenance. |
