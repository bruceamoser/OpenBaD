# ADR-002 — SQLite as the System of Record

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

OpenBaD needs a durable store for sessions, events, memories, knowledge chunks,
usage, approvals, and audit records. The product is a desktop app and is
DeepSeek-first, not local-first, so the store is a working store rather than a
privacy boundary.

Options:

1. SQLite with FTS5 and `sqlite-vec`.
2. PostgreSQL with `pgvector`.
3. DuckDB + LanceDB.
4. A remote database (for example, Postgres in the cloud).

## Decision

Use **SQLite** (bundled) with:

- WAL mode, foreign keys, and a single writer actor.
- FTS5 for BM25 search (code and documents).
- `sqlite-vec` for vector search.
- Embedded SQL migrations with a small runner.
- Optional SQLCipher for at-rest encryption.

Reasons:

- Zero operations and embedded in the desktop app.
- ACID with a single writer; excellent read performance in WAL mode.
- FTS5 and `sqlite-vec` cover the retrieval needs without another service.
- Easy backup, export, and portability.
- A local store keeps UI latency low even though model calls are remote.

## Consequences

- Vector dimensions are fixed per database generation; model changes require a
  re-embed and alias swap.
- FTS5 tokenization must be chosen per content type (porter for docs, trigram
  for code).
- Very large repositories need index limits and sampling.
- Cloud sync is a separate, optional feature, not a replacement for SQLite.

## Alternatives considered

- **PostgreSQL:** rejected for desktop install complexity and operations.
- **DuckDB/LanceDB:** rejected for transactional semantics and mixed workload.
- **Remote DB:** rejected for latency, offline resilience, and key management.

## References

- [Data Model and Storage](../architecture/03-data-model-and-storage.md)
- [Memory and Knowledge](../architecture/06-memory-and-knowledge.md)
