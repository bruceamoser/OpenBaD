# ADR-007 — DeepSeek-First Provider Strategy

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

DeepSeek's current API offers a combination that fits a coding harness:

- `deepseek-flash` and `deepseek-v4-pro` with 1M context and 384K output.
- Thinking mode with `low`/`high`/`max` effort.
- Automatic prefix caching at ~2–3% of the miss price.
- Files API for images.
- Search and Fetch APIs.
- FIM and chat prefix completion (beta).
- Strict tool schemas (beta).
- Off-peak pricing at half rate.
- `user_id` isolation.
- OpenAI-compatible and Anthropic-compatible endpoints.

A provider-neutral design would leave most of this unused.

## Decision

Make DeepSeek the **default and preferred provider**:

1. `deepseek-flash` is the default for chat, code, tools, extraction, routing,
   titles, and summaries.
2. `deepseek-v4-pro` is used for hard reasoning, planning, review, and security.
3. Thinking mode is enabled by default (`high`) for main work; disabled or
   `low` for auxiliary work.
4. Files, Search, Fetch, FIM, prefix completion, strict tools, Balance, and
   `user_id` are first-class capabilities.
5. Background work is scheduled off-peak when possible.
6. Other providers are optional fallbacks or capability gaps:
   - Embeddings (DeepSeek has no embeddings endpoint).
   - Offline/private deployments.
   - User preference.
7. Model/provider switching is explicit, visible, and starts a new request
   series.

## Consequences

- The product is network-dependent on DeepSeek.
- Pricing and model names live in a signed, user-editable catalog, not code.
- Cache-first design is not optional; it is the core cost strategy.
- The harness must handle DeepSeek-specific rules:
  - `reasoning_content` passback with tools.
  - Thinking mode parameter constraints.
  - FIM non-thinking/4K limits.
  - Files API image-only limits.
  - Peak/off-peak pricing windows.
- Provider abstraction remains, but DeepSeek is not an afterthought.

## Alternatives considered

- **Provider-neutral:** rejected; leaves DeepSeek capabilities unused.
- **OpenAI-first:** rejected; worse fit for cost and context.
- **Local-model-first:** rejected; conflicts with the product goal.

## References

- [Providers, Observability, and Performance](../architecture/10-providers-observability-performance.md)
- [Cache-First Harness](../architecture/04-cache-first-harness.md)
- [DeepSeek API — Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek API — Context Caching](https://api-docs.deepseek.com/guides/kv_cache)
