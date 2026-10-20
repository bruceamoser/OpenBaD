# OpenBaD Desktop — Architecture Documentation

> A DeepSeek-first, cache-first AI coding harness for the desktop.

## Document index

| # | Document | What it covers |
| --- | --- | --- |
| 00 | [Vision and Feature Set](00-vision-and-feature-set.md) | Product definition, principles, feature list, scope |
| 01 | [System Overview](01-system-overview.md) | Component map, runtime topology, turn lifecycle |
| 02 | [Rust Workspace and Crates](02-rust-workspace-and-crates.md) | Crate layout, dependency rules, key libraries |
| 03 | [Data Model and Storage](03-data-model-and-storage.md) | SQLite schema, event log, FTS5, `sqlite-vec`, migrations |
| 04 | [Cache-First Harness](04-cache-first-harness.md) | Prefix invariants, prompt assembly, compaction, DeepSeek caching |
| 05 | [Agent Runtime and Tools](05-agent-runtime-and-tools.md) | Turn/step loop, routing, tools, subagents, plan/todo/goal, MCP |
| 06 | [Memory and Knowledge](06-memory-and-knowledge.md) | Engrams, ACT-R, Hebbian learning, library, ingestion, RAG |
| 07 | [Autonomic System and Governance](07-autonomic-system-and-governance.md) | Heartbeat, pulse, reflexes, immune guardrails, approvals, audit |
| 08 | [Desktop Application](08-desktop-application.md) | Tauri shell, React UI, views, interactions, packaging |
| 09 | [Security and Threat Model](09-security-and-threat-model.md) | Trust boundaries, prompt injection, sandboxing, secrets |
| 10 | [Providers, Observability, and Performance](10-providers-observability-performance.md) | Model adapters, cost accounting, telemetry, benchmarks |

Implementation planning lives in [`../plans/`](../plans/). Decisions live in
[`../adr/`](../adr/).

## Reading order

- **New contributors:** 00 → 01 → 02 → 03 → 04 → 05.
- **Harness/LLM work:** 04 → 05 → 10.
- **Memory/RAG work:** 03 → 06.
- **Desktop UI work:** 01 → 08.
- **Security-sensitive work:** 09 → 07.

## Conventions

- "MUST", "SHOULD", and "MAY" follow [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).
- Diagrams use [Mermaid](https://mermaid.js.org/).
- Rust examples use edition 2024 syntax.
- SQL examples target SQLite 3.45+ with FTS5 and `sqlite-vec`.
- Feature IDs (`F-xx`) are stable and are referenced by the implementation plan.

## The one invariant

> **The model-visible prefix is immutable and append-only. Volatile context
> goes at the tail, never in the prefix.**

Every component that can influence a model request is designed around this
rule. See [Cache-First Harness](04-cache-first-harness.md).
