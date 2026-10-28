# Implementation Plans

| Plan | Purpose |
| --- | --- |
| [0001 — Implementation Plan](0001-implementation-plan.md) | Phased delivery plan from archive reset to v1.0 |
| [0002 — Cache-First Benchmark Plan](0002-cache-first-benchmark-plan.md) | How we prove cache hit, cost, and latency targets |
| [0003 — Tool Surface Expansion Guide](0003-tool-surface-expansion.md) | The 128-tool budget, slot consolidation, and designs for LSP, MCP, PTY, workflows, and session search |
| [0004 — Chat Experience and Turn Engine Rework](0004-chat-experience-and-turn-engine.md) | DSH turn technology plus VS Code UX grammar: durable turns, per-call approvals, reviewable transcripts, long context, parallel sessions, and accessibility |
| [0005 — Code Mode](0005-code-mode-tool-execution.md) | Proposal: one model-written program calling tool bindings instead of one call per step. Runtime and approval-contract decisions first, read-only bindings only |

Plans are living documents. When a phase is completed, record the outcome and
link the PRs and decisions. When scope changes, update the feature table in
[00 — Vision and Feature Set](../architecture/00-vision-and-feature-set.md).
