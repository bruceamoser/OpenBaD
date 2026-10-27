# 10 — Providers, Observability, and Performance

## 1. Provider model

Every model interaction goes through a `ModelProvider` trait. DeepSeek is the
primary implementation; other providers are optional.

```rust
#[async_trait]
pub trait ModelProvider: Send + Sync {
    fn id(&self) -> ProviderId;
    fn capabilities(&self) -> ProviderCapabilities;
    async fn stream(&self, request: FrozenRequest) -> Result<ModelStream, ProviderError>;
    async fn complete(&self, request: FrozenRequest) -> Result<ModelResponse, ProviderError>;
    fn count_tokens(&self, text: &str) -> u64;
    fn normalize_usage(&self, raw: RawUsage) -> Usage;
}
```

```rust
pub struct ProviderCapabilities {
    pub chat: bool,
    pub tools: bool,
    pub thinking: bool,
    pub vision: bool,
    pub streaming: bool,
    pub json_mode: bool,
    pub strict_tools: bool,
    pub prefix_cache: CacheMode,      // automatic, explicit, none
    pub files_api: bool,
    pub search_api: bool,
    pub fetch_api: bool,
    pub fim: bool,
    pub prefix_completion: bool,
    pub max_context: u32,
    pub max_output: u32,
}
```

## 2. DeepSeek adapter

### 2.1 Endpoints and models

| Concern | Value |
| --- | --- |
| OpenAI-compatible base | `https://api.deepseek.com` |
| Anthropic-compatible base | `https://api.deepseek.com/anthropic` |
| Beta base (FIM, prefix, strict) | `https://api.deepseek.com/beta` |
| Default model | `deepseek-flash` |
| Hard-reasoning model | `deepseek-v4-pro` |
| Context | 1M tokens |
| Max output | 384K tokens |
| Concurrency | 2,500 flash / 500 pro |

### 2.2 Thinking mode

```json
{
  "model": "deepseek-flash",
  "messages": [...],
  "thinking": { "type": "enabled" },
  "reasoning_effort": "high"
}
```

- Thinking is enabled by default with effort `high`.
- Effort values: `low`, `high`, `max`.
- Thinking mode ignores `temperature`, `presence_penalty`, and
  `frequency_penalty`.
- `top_p` behavior differs by model; the adapter does not set it in thinking
  mode unless the user explicitly overrides.
- `reasoning_content` is returned alongside `content`.
- **With `tools`:** pass back the full `reasoning_content` of every prior turn
  or the API returns 400.
- **Without `tools`:** reasoning is ignored by the API and may be kept only in
  the event log.

### 2.3 Tool calls

- Non-thinking and thinking modes both support tool calls.
- `strict: true` (beta) enforces the JSON schema of tool arguments.
- The adapter serializes tool arguments deterministically and rejects
  non-object arguments for historical messages.
- Chat Completion supports mid-conversation `system` messages; it does not
  support inserting tool calls mid-conversation. OpenBaD appends, so this is
  not a limitation. The Anthropic and Responses formats are available if a
  future feature needs insertion.

### 2.4 Files, Search, Fetch

| Feature | Behavior |
| --- | --- |
| Files | Upload images (JPEG/PNG/GIF/WebP, ≤ 64 MiB) with `purpose=user_data`; cache `file_id` by content hash; optional expiry 1h–30d |
| Search | In-house web search; returns structured results; cite URLs and snippets |
| Fetch | Fetch a URL as Markdown; cite URL and fetch time |
| Balance | Read account balance; display in Pulse; never a model tool |

Files are images only. Documents are ingested locally or fetched as Markdown.

### 2.5 FIM and prefix completion

| Feature | Constraints |
| --- | --- |
| FIM | `/beta` completions; `deepseek-flash`; non-thinking only; max 4K output |
| Chat prefix completion | `/beta` chat; last message is `assistant` with `prefix: true`; validate output before applying |

Both use separate request series and never share the main conversation prefix.

### 2.6 `user_id`

Send a stable, non-PII per-install identifier as `user_id` to isolate KV cache,
content-safety handling, and scheduling. Do not include email, username, or
workspace path.

### 2.7 Retry policy

| Error | Retry? | Notes |
| --- | --- | --- |
| 429 | Yes | Respect `Retry-After`; backoff |
| 5xx | Yes | Backoff; preserve request bytes |
| Timeout | Yes | Preserve request bytes |
| Stream reset | Yes | Same frozen request |
| 400 (bad request) | No | Fix request; log |
| 401/403 | No | Key/permission issue |
| Context overflow | Compact | New series |
| Content filter | No | Surface to user |

## 3. Other providers

| Provider | Role | Notes |
| --- | --- | --- |
| OpenAI-compatible | Fallback / local servers | `cached_tokens` normalization |
| Anthropic | Fallback; explicit cache breakpoints | `cache_control` |
| Local (Ollama/vLLM/llama.cpp) | Offline or private fallback | Stable prefix still helps |
| Embeddings provider | Vector search when local embeddings are disabled | DeepSeek has no embeddings endpoint today; add it as the default if it ships |

Provider switching is explicit and starts a new request series. The app never
silently falls back.

## 4. Model catalog and routing

### 4.1 Catalog

The catalog is a signed, user-editable TOML file:

```toml
[[model]]
id = "deepseek-flash"
provider = "deepseek"
context = 1_000_000
max_output = 384_000
thinking = true
vision = true
prefix_cache = "automatic"
pricing.offpeak = { hit = 0.003, miss = 0.15, output = 0.60 }
pricing.peak    = { hit = 0.006, miss = 0.30, output = 1.20 }

[[model]]
id = "deepseek-v4-pro"
provider = "deepseek"
context = 1_000_000
max_output = 384_000
thinking = true
vision = false
prefix_cache = "automatic"
pricing.offpeak = { hit = 0.022, miss = 0.66, output = 1.98 }
pricing.peak    = { hit = 0.044, miss = 1.32, output = 3.96 }
```

### 4.2 Routes

| Task class | Route | Default |
| --- | --- | --- |
| `chat` | `deepseek-flash` | Main conversation |
| `code` | `deepseek-flash` | Edits and tool use |
| `review` | `deepseek-v4-pro` | Code review and security |
| `plan` | `deepseek-v4-pro` | Architecture and plans |
| `extract` | `deepseek-flash`, thinking off | Memory extraction |
| `route` | `deepseek-flash`, thinking off | Classification |
| `title` | `deepseek-flash`, thinking off | Session titles |
| `embed` | local ONNX or secondary | Vector search |

### 4.3 Routing rules

- Same model for a session unless the user switches.
- Automatic escalation from flash to pro is explicit and visible: the app
  starts a new series and warns about the cache reset.
- Auxiliary routes never share the main prefix.
- Budget exhaustion stops the turn; it does not silently downgrade.

## 5. Observability

### 5.1 Logs

- Structured JSON via `tracing`.
- Fields: `session_id`, `turn`, `step`, `route`, `provider`, `model`,
  `tool`, `duration_ms`, `tokens`, `cache_hit`, `error_code`.
- Secrets redacted before serialization.
- Log rotation and retention (7 days default).
- A local log viewer with filters.

### 5.2 Traces

Spans:

```text
turn
├── route
├── memory_recall
├── prompt_assemble
├── provider_stream
│   └── tool_calls
├── tool_execute
│   └── policy / approval
├── evidence_gate
└── synthesize
```

OpenTelemetry export is opt-in. The default is a local span store for the
diagnostics panel.

### 5.3 Metrics

| Metric | Type | Labels |
| --- | --- | --- |
| `llm_requests_total` | counter | provider, model, status |
| `llm_tokens_total` | counter | kind (input/cache_read/output) |
| `llm_cost_usd` | counter | provider, model |
| `llm_latency_ms` | histogram | provider, model |
| `cache_hit_ratio` | gauge | session, model |
| `tool_calls_total` | counter | tool, status |
| `tool_duration_ms` | histogram | tool |
| `approval_latency_ms` | histogram | risk |
| `memory_retrieval_ms` | histogram | scope |
| `index_files_total` | counter | language |
| `index_lag_seconds` | gauge | workspace |
| `job_failures_total` | counter | kind |
| `db_size_bytes` | gauge | — |
| `wal_size_bytes` | gauge | — |
| `pulse_cpu_pct` | gauge | — |
| `pulse_mem_pct` | gauge | — |

### 5.4 Diagnostics

`doctor` reports:

- App version, OS, SQLite version, extensions.
- Schema version and integrity check.
- Provider reachability, key validity, balance.
- Cache hit rate over the last 24h.
- Workspace index status.
- Running jobs and stuck processes.
- Disk/WAL size.
- Recent errors with correlation IDs.

## 6. Performance

### 6.1 Budgets

| Area | Budget |
| --- | --- |
| Cold start to interactive | < 2 s |
| Session switch | < 150 ms |
| Streaming delta to paint | < 50 ms |
| Local DB query p95 | < 10 ms |
| Prompt assembly p95 | < 5 ms |
| Token meter p95 | < 10 ms |
| Memory retrieval p95 | < 50 ms |
| Tool dispatch overhead | < 5 ms |
| Index throughput | 10k files < 60 s |
| Idle CPU | < 2% |
| Idle RAM | < 300 MB (excluding models) |

### 6.2 Techniques

- SQLite WAL, prepared statements, covering indexes.
- Batched writes and reads; no N+1 queries.
- Virtualized UI lists; batched streaming deltas.
- Parallel read-only tools; serialized writes.
- Incremental indexing with file hashes and debounce.
- Local embeddings in a bounded worker pool.
- Lazy loading of editor, diff, and graph views.
- Request body constructed once and reused for retries.
- Cache-first prompt assembly to reduce provider latency and cost.

### 6.3 Profiling

- Rust: `cargo flamegraph`, `tracing` spans, `pprof` optional.
- SQLite: `EXPLAIN QUERY PLAN`, slow-query log (> 50 ms).
- UI: React Profiler and browser devtools.
- Provider: first-token, total latency, cache hit, retry count.

## 7. Benchmarks

### 7.1 Suites

| Suite | What it measures |
| --- | --- |
| `cache_prefix` | Prefix stability and cache-hit share across turns |
| `cache_cost` | Cost vs. a naive harness |
| `prompt_assembly` | Latency and determinism |
| `tool_pipeline` | Dispatch overhead and validation cost |
| `workspace_index` | Throughput and incremental update |
| `memory_retrieval` | Recall@k, MRR, latency |
| `knowledge_retrieval` | Recall@k, citation accuracy |
| `ui_startup` | Cold start and session switch |
| `db_queries` | p95 for hot queries |

### 7.2 Method

- Fixed fixtures in `tests/fixtures/`.
- Mock provider for deterministic unit/CI runs.
- Real DeepSeek runs in an opt-in nightly job with a capped budget.
- Compare against a "naive harness" that injects timestamps, reorders tools,
  and rewrites history.
- Record tokens, cost, latency, and cache hit per fixture.
- Fail CI on regressions beyond thresholds.

See [Cache-First Benchmark Plan](../plans/0002-cache-first-benchmark-plan.md).

## 8. Cost controls

- Per-session and monthly budgets.
- Balance API check with low-balance alerts.
- Off-peak scheduling for background work.
- `deepseek-flash` by default; pro only for hard tasks.
- Auxiliary routes with thinking disabled.
- Tool output spill and compaction.
- A "cost preview" before expensive actions (large reviews, whole-repo tasks).
- A hard stop at 100% budget unless the user overrides.

## 9. Failure and degradation

| Failure | Degradation |
| --- | --- |
| DeepSeek unreachable | Local browsing/editing/history; banner; retry |
| Rate limited | Backoff; suggest flash or off-peak |
| Balance exhausted | Block model calls; local work continues |
| Vision unavailable (pro) | Route image requests to flash |
| Embeddings unavailable | FTS-only retrieval |
| Search/Fetch unavailable | Local knowledge only; web tools disabled |
| Cache miss storm | Pulse alert; diagnostics; prompt diff tool |

## 10. Release gates

- [ ] All provider contract tests pass.
- [ ] Cache benchmark meets targets.
- [ ] Cost benchmark meets targets.
- [ ] Latency budgets meet targets.
- [ ] No unpriced models in default routes.
- [ ] Balance and budget flows tested.
- [ ] Diagnostics redact secrets.
- [ ] Off-peak scheduling verified.
