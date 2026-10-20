# 0002 — Cache-First Benchmark Plan

## 1. Purpose

Prove that OpenBaD's prompt/runtime design actually produces high DeepSeek
prefix-cache hit rates, lower cost, and lower latency than a cache-naive
harness. This plan defines fixtures, metrics, the comparison baseline, and CI
gates.

## 2. Hypotheses

| # | Hypothesis | Measurement |
| --- | --- | --- |
| H1 | Append-only prompt assembly preserves the prefix across turns | Prefix hash equality |
| H2 | Cache-first assembly achieves ≥ 85% cache-hit share after turn 1 | `cache_read / billed input` |
| H3 | Cache-first assembly achieves ≥ 92% hit share after turn 5 | Same |
| H4 | Cache-first reduces input cost by ≥ 60% vs. naive | Cost ledger |
| H5 | Cache-first reduces cost per completed task by ≥ 40% | Cost per fixture |
| H6 | Prefix-preserving retries do not reset the cache | Byte equality + hit share |
| H7 | Compaction summarizer reuses the warm prefix | Prefix equality |
| H8 | Dynamic MCP/skill changes do not reset the prefix | Schema hash equality |
| H9 | DeepSeek Search/Fetch citations do not bloat the stable prefix | Token meter |
| H10 | Thinking-mode passback does not break cache reuse | Hit share with tools |

## 3. Baselines

### 3.1 Naive harness

A deliberately cache-unfriendly but otherwise equivalent harness:

- Injects `Current time: ...` at the top of the system prompt.
- Reorders tool schemas by hash map iteration.
- Rewrites the system prompt each turn with a "helpful assistant" preamble.
- Rebuilds tool descriptions with random IDs.
- Compacts every 4 turns.
- Re-sends full file contents instead of stable spans.
- Switches between flash and pro for "optimization".

This is the counterfactual we must beat.

### 3.2 OpenBaD

- Stable sections, canonical tool schemas.
- Append-only history.
- Volatile context at the tail.
- Prefix-preserving retries.
- Rare, boundary-aligned compaction.
- Spill at creation.
- Single model per session.

## 4. Fixtures

Fixtures are deterministic conversations with a fixed workspace snapshot and a
scripted mock-provider mode for CI.

| Fixture | Turns | Shape | What it stresses |
| --- | --- | --- | --- |
| `repo_qa` | 8 | Q&A over a medium repo | Retrieval tail, citations |
| `scoped_edit` | 12 | Read → edit → test → fix | Tool results, diffs, checkpoints |
| `debug_loop` | 10 | Failing test → diagnosis → patch | Repeated tool calls, reasoning passback |
| `review` | 6 | Diff review with pro model | Large diff, thinking, output cost |
| `knowledge` | 8 | RAG over docs | Knowledge tail, citations |
| `memory` | 10 | Cross-session recall | Memory tail, ACT-R |
| `long_session` | 60 | Long coding session | Compaction, cache resets |
| `mcp_dynamic` | 8 | Add/remove MCP server mid-session | Schema stability |
| `skill_load` | 6 | Load a skill body | Stable index + tail body |
| `vision` | 4 | Screenshot analysis via Files | File cache, image tokens |
| `search_fetch` | 6 | Web research | Search/Fetch citations |
| `fim` | 20 | Inline completions | FIM latency/cost |
| `prefix_completion` | 6 | Constrained edits | Prefix completion validity |

### 4.1 Fixture layout

```text
tests/fixtures/cache/
├── repo_qa/
│   ├── workspace/            # file snapshot
│   ├── conversation.json     # user turns + expected tool calls
│   ├── provider/             # mock responses (SSE)
│   └── expected.json         # metrics and golden request hashes
├── scoped_edit/
...
```

### 4.2 Mock provider

- Replays recorded SSE streams with deterministic usage.
- Supports thinking, tool calls, errors, rate limits, and context overflow.
- Can emulate cache hits/misses for a given prefix hash.
- Records the exact request bytes for assertions.

### 4.3 Real DeepSeek runs

- Opt-in nightly job.
- `DEEPSEEK_API_KEY` from CI secrets.
- Capped budget (for example, $2 per run).
- Uses `deepseek-flash` for most fixtures; `deepseek-v4-pro` only for `review`.
- Records real `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`.
- Fails if the hit share is below target for two consecutive runs.

## 5. Metrics

| Metric | Definition |
| --- | --- |
| `billed_input_tokens` | `input + cache_read + cache_write` |
| `cache_hit_share` | `cache_read / billed_input_tokens` |
| `cost_usd` | Catalog price × tokens, peak/off-peak aware |
| `cost_per_task` | Total cost / completed fixture |
| `first_token_ms` | Request start → first content delta |
| `total_latency_ms` | Request start → finish |
| `tool_overhead_ms` | Tool dispatch + validation, excluding execution |
| `prefix_stability` | Share of turns whose prefix matches the prior turn |
| `series_resets` | Count and reasons |
| `compaction_count` | Count and cache reset cost |
| `spill_count` | Count and bytes |
| `schema_hash_changes` | Count of tool schema changes |

## 6. Method

### 6.1 Replay

For each fixture:

1. Load the workspace snapshot.
2. Start a session with the fixed model route.
3. For each turn:
   - Assemble and freeze the request.
   - Record request bytes and hashes.
   - Feed the mock or real provider.
   - Apply the scripted tool calls (or real tools in the workspace sandbox).
   - Record usage and cost.
4. Assert expected tool behavior and evidence.
5. Emit a metrics JSON.

### 6.2 Prefix comparison

```rust
fn assert_prefix(previous: &[u8], current: &[u8]) {
    assert!(
        current.starts_with(previous),
        "request prefix changed at byte {}",
        first_difference(previous, current)
    );
}
```

The test must allow legitimate series resets (model change, compaction,
workspace refresh) and assert the reset reason.

### 6.3 Cost comparison

Run the same conversation through:

- The naive harness (recorded request shape).
- The OpenBaD harness.

Compare:

- Billed input tokens.
- Cache read tokens.
- Output tokens (should be similar; if not, investigate prompt quality).
- Cost per task.
- Latency.

### 6.4 Statistical rigor

- 5 repetitions per fixture for real API runs; report median and p95.
- Warm and cold cache runs separately.
- Pin the DeepSeek model version in the report.
- Record the date/time and peak/off-peak window.
- Record the account's `user_id` isolation behavior.

## 7. Targets

| Metric | Target | Gate |
| --- | --- | --- |
| Cache hit share after turn 1 | ≥ 85% | Nightly + release |
| Cache hit share after turn 5 | ≥ 92% | Nightly + release |
| Input cost vs. naive | −60% | Release |
| Cost per task vs. naive | −40% | Release |
| Prefix stability (non-reset turns) | 100% | CI |
| Retry byte equality | 100% | CI |
| Summarizer prefix reuse | 100% | CI |
| Tool schema changes without explicit reset | 0 | CI |
| First token p95 (warm) | < 1.5 s | Release |
| Prompt assembly p95 | < 5 ms | CI |
| Token meter p95 | < 10 ms | CI |

## 8. CI integration

### 8.1 PR checks (mock only, < 2 min)

- Build the fixture harness.
- Run `cache_prefix`, `cache_cost` (mock), `prompt_assembly`, and
  `token_meter`.
- Compare against golden metrics with a tolerance of ±2%.
- Fail on prefix instability, schema drift, or retry byte differences.

### 8.2 Nightly (real DeepSeek, < 20 min, capped budget)

- Run all fixtures against `deepseek-flash`.
- Run `review` against `deepseek-v4-pro`.
- Publish a report artifact with metrics and request hashes.
- Open an issue automatically if a target is missed twice.

### 8.3 Release

- Run the full suite.
- Attach the report to the release.
- Block release if any gate fails.

## 9. Reporting

A report contains:

```json
{
  "date": "2026-10-19",
  "commit": "abc123",
  "model_version": "DeepSeek-V4.1-Flash-1017",
  "pricing_window": "offpeak",
  "fixtures": {
    "scoped_edit": {
      "turns": 12,
      "billed_input_tokens": 184000,
      "cache_read_tokens": 168000,
      "cache_hit_share": 0.913,
      "cost_usd": 0.031,
      "cost_per_task_usd": 0.031,
      "first_token_ms_p95": 820,
      "series_resets": 1,
      "reset_reasons": ["compaction"],
      "prefix_stability": 1.0
    }
  },
  "totals": {
    "cost_usd": 0.42,
    "naive_cost_usd": 1.31,
    "savings_pct": 0.68
  }
}
```

The UI diagnostics panel can display the latest local run. The release report is
stored in `docs/reports/cache/`.

## 10. Failure triage

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Prefix instability | Volatile content in a stable section | Section diff tool |
| Hit share drops after turn N | Tool schema change / MCP connect | Schema hash log |
| Hit share drops after compaction | Expected reset; verify only once | Reset reason |
| Retry misses cache | Request rebuilt | Frozen request byte test |
| Cost higher than naive | More output tokens or tool loops | Per-turn token breakdown |
| First-token regression | Larger prefix or provider latency | Prefix size + provider metrics |
| Summarizer cost high | Different model/route | Summarizer envelope |

## 11. Open questions

- How does DeepSeek cache behavior change with 1M-token prefixes and fixed
  interval units?
- Does `user_id` isolation affect hit rate when switching workspaces?
- Does the Anthropic-compatible API have different cache behavior than the
  OpenAI-compatible API?
- Do Search/Fetch server-side tool calls enter the cacheable prefix?
- How does thinking-mode reasoning passback affect cache size and hit rate?
- What is the optimal compaction threshold at 1M context?

These are resolved by the nightly suite, not by assumptions.

## 12. Definition of done

- Fixtures cover all major flows.
- CI gates are enforced.
- Nightly report is published and triaged.
- Release report is attached.
- The naive comparison is reproducible.
- Any regression has an owner and a fix issue.
