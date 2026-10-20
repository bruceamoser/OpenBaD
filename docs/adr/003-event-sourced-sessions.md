# ADR-003 — Event-Sourced Session Log

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

A coding harness must be able to:

- Reproduce exactly what the model saw.
- Resume after a crash or restart.
- Audit tool calls, approvals, and file changes.
- Fork sessions and compare trajectories.
- Rebuild UI projections without a second source of truth.

Options:

1. Store only the current message list and overwrite it.
2. Store messages plus a separate tool log.
3. Store an append-only event log and derive all views.

## Decision

Use an **append-only session event log** as the source of truth.

- Every model-visible input is an event with `surface = 1`.
- Tool calls, results, approvals, usage, and compaction are events.
- Message history is derived, never stored as the only copy.
- Projections are derived tables updated transactionally.
- Sessions can be replayed, forked, exported, and rebuilt.

## Consequences

- No update/delete of events in normal operation.
- Append is atomic with projection updates.
- Event schemas are versioned; migrations are explicit.
- The log grows; retention/archival is required.
- Every new model-visible input requires a new event type.

## Alternatives considered

- **Current message list only:** rejected; cannot audit or replay.
- **Messages + tool log:** rejected; two sources of truth and drift.
- **JSONL only:** viable for export, but poor for queries and projections.

## References

- [Data Model and Storage](../architecture/03-data-model-and-storage.md)
- [Agent Runtime and Tools](../architecture/05-agent-runtime-and-tools.md)
- [Cache-First Harness](../architecture/04-cache-first-harness.md)
