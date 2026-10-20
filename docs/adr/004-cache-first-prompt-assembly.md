# ADR-004 — Cache-First Prompt Assembly

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

DeepSeek's prefix cache is automatic but exact-prefix-only. Cache-hit input
costs about 2–3% of cache-miss input. Most harnesses destroy the prefix by:

- Injecting timestamps or runtime state into the system prompt.
- Reordering tool schemas.
- Rewriting history or editing prior messages.
- Injecting all MCP/skill tools dynamically.
- Compacting frequently or mid-prefix.
- Rebuilding requests on retry.

## Decision

Make prefix stability a first-class architectural invariant:

1. **Immutable stable prefix** — static sections, then workspace sections.
2. **Append-only history** — never rewrite or reorder.
3. **Volatile tail** — time, git status, diagnostics, pressure.
4. **Canonical tool schemas** — sorted, hashed, stable per series.
5. **Stable proxy tools** — `mcp_call`, `skill`, `tool_search`, `workflow`.
6. **Request envelopes** — record hashes; enforce series boundaries.
7. **Frozen requests** — retries reuse exact bytes.
8. **Rare, boundary-aligned compaction** — summarizer replays the warm prefix.
9. **Spill at creation** — large tool output is never rewritten later.
10. **Live cache accounting** — hit/miss tokens, cost, reset reasons.

## Consequences

- Some dynamic behavior moves from the system prompt to tool calls or tail
  messages.
- Model/tool/thinking changes are explicit and visible to the user.
- Compaction has a measurable cost and is deliberately infrequent.
- Tests must assert prefix equality and request-byte stability.
- The design also improves reproducibility for non-cached providers.

## Alternatives considered

- **Rebuild the prompt each turn:** simplest, but destroys caching.
- **Semantic caching:** not what DeepSeek offers; prefix caching is exact.
- **Compact aggressively:** saves context but repeatedly cold-starts the cache.
- **Provider-specific prompt hacks:** rejected; the harness stays canonical.

## References

- [Cache-First Harness](../architecture/04-cache-first-harness.md)
- [Cache-First Benchmark Plan](../plans/0002-cache-first-benchmark-plan.md)
- [ADR-007](007-deepseek-first-provider-strategy.md)
