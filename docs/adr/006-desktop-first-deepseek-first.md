# ADR-006 — Desktop-First, DeepSeek-First

- **Status:** Accepted
- **Date:** 2026-10-19
- **Supersedes:** the local-first assumption in early design drafts

## Context

Early drafts framed OpenBaD as "local-first": all data stays on the machine and
network calls are minimized. The product goal is a powerful coding harness that
leverages DeepSeek as much as possible. A strict local-first stance would:

- Push embeddings and auxiliary models to local runtimes.
- Avoid DeepSeek Search/Fetch and Files.
- Treat every network call as a privacy exception.
- Limit the harness to what a local model can do.

That conflicts with the goal.

## Decision

Adopt **desktop-first, DeepSeek-first**:

- The app is a desktop app with a Rust core and Tauri UI.
- DeepSeek is the default and preferred provider for generation, reasoning,
  planning, review, extraction, classification, summarization, search, and
  fetch.
- SQLite remains the local working store for latency, durability, and offline
  browsing/editing, but it is not a privacy boundary.
- Prompts and selected context are sent to DeepSeek. Users are informed.
- Optional providers fill capability gaps (for example, embeddings, since
  DeepSeek has no embeddings endpoint).
- Optional encrypted cloud backup/sync is a later feature, not a requirement.

## Consequences

- Data-flow documentation and threat modeling must assume provider exposure.
- Context minimization and secret redaction become more important.
- Offline mode is degraded: local browsing, editing, and history work; model
  features require connectivity.
- The product can use DeepSeek's 1M context, thinking mode, Files, Search,
  Fetch, FIM, prefix completion, and off-peak pricing.
- Marketing and docs must not claim "local-first" or "data never leaves your
  machine."

## Alternatives considered

- **Strict local-first:** rejected; conflicts with the DeepSeek-first goal and
  would slow the product.
- **Cloud-only:** rejected; a desktop harness still benefits from a local
  working store and offline editing.
- **Provider-neutral:** rejected; it would dilute DeepSeek-specific advantages.

## References

- [Vision and Feature Set](../architecture/00-vision-and-feature-set.md)
- [Security and Threat Model](../architecture/09-security-and-threat-model.md)
- [ADR-007](007-deepseek-first-provider-strategy.md)
