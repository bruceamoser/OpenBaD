# OpenBaD Documentation

| Section | Contents |
| --- | --- |
| [Architecture](architecture/README.md) | Vision, system design, data model, harness, memory, security |
| [Plans](plans/README.md) | Implementation phases and cache benchmarks |
| [ADRs](adr/README.md) | Accepted architecture decisions |
| [Archive](https://github.com/bruceamoser/OpenBaD/tree/main/archive/openbad-v0) | The original OpenBaD v0 concept and Python implementation |

## Start here

1. [Vision and Feature Set](architecture/00-vision-and-feature-set.md)
2. [System Overview](architecture/01-system-overview.md)
3. [Cache-First Harness](architecture/04-cache-first-harness.md)
4. [Implementation Plan](plans/0001-implementation-plan.md)

## The one invariant

> **The model-visible prefix is immutable and append-only. Volatile context
> goes at the tail, never in the prefix.**

## Status

Phases 1–3 are working on Linux: store, audit, keychain, DeepSeek client, usage
and cost accounting, coding tools, agent loop, and a Tauri chat UI. Per-call
approvals, token streaming, the evidence gate, and Phases 4–8 remain. The v0
Python implementation is archived and unsupported.
