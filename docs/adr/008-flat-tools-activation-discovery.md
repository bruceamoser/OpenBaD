# ADR-008 — Flat tools with activation-based discovery

- **Status:** Proposed
- **Date:** 2026-11-03

## Context

The registry holds 136 internal tools and advertises them through 18 domain
facades, two tools per domain: `<domain>_list` (discovery) and `<domain>_run`
(execution through `{ "tool": ..., "arguments": { ... } }`). That keeps the
model-facing surface at 36 schemas under a 128-schema cap and keeps the
tool-schema hash stable when a tool is added to an existing domain.

**Measured cost.** Across 6,993 tool results in this project's own session store,
150 were errors (2.1%). Ranked by normalised class:

| Class | Count | Share |
| --- | --- | --- |
| Proxy envelope / JSON serialization (missing or renamed `tool`, invalid escape, control characters, omitted inner argument) | 59 | **39%** |
| Read-before-write guard | 20 | 13% |
| Destructive approval demanded | 9 | 6% |
| Timeouts | 8 | 5% |
| Path outside workspace | 4 | 3% |
| Unknown tool name | 3 | 2% |

Three successive fix attempts targeted the first class as a parsing problem
(#882 markup repair, #904 missing-`tool` diagnostics, #914 renamed-key recovery)
plus repair-on-parse (#917). **The share did not fall.** For comparison, the
published baseline for single-level arguments is 0.03% parse failures across
122,636 calls, all of them from `deepseek-flash`.

**External evidence** (full sources in
[research/tool-call-serialization-survey.md](../architecture/research/tool-call-serialization-survey.md)):

- No provider and no surveyed harness nests a JSON object inside a JSON string.
  OpenAI and DeepSeek parse one `arguments` string client-side; Anthropic and
  Gemini take an object parsed by the API.
- Anthropic: *"Claude's ability to pick the right tool degrades once you exceed
  30–50 available tools."* Tool search "typically reduces [definitions] by over
  85 percent, loading only the 3–5 tools Claude needs for a given request."
- DeepSeek Harness bounds its surface by **selection**: 31 flat tool packages,
  per-agent plugin composition, and a tool set resolved per model turn
  (`tools.resolve`), surfaced in its UI as tools added and removed.
- Repair cannot fix value-level corruption: 0/14 corrupt DeepSeek strings were
  recovered by `JSON.parse`, `json_repair`, or strip-then-repair.
- Retry budgets converge at 2–3 (Claude Code retries twice, Aider
  `max_reflections = 3`).

The facade's benefit (36 schemas instead of 136) and its cost (39% of errors)
are both real. The question is whether wrapping execution is the right way to
buy the first, and the survey says no surveyed harness does it.

## Decision

Adopt **flat tools with activation-based discovery**.

1. **One level of arguments.** Every model-callable tool has a flat schema.
   `<domain>_run` stops wrapping execution; the facade remains only as a
   discovery surface.
2. **Discovery activates, rather than wraps.** `<domain>_list` (and a search
   path over the full catalogue) returns tool definitions *and* adds those names
   to the session's active set, so the following turn advertises them with their
   real flat schemas.
3. **The advertised set is resolved per turn** from a stable base set plus
   activated tools, capped at 30 advertised tools — the top of the published
   accuracy range, deliberately not above it.
4. **Presets define the base set**, mirroring the DeepSeek Harness composition
   model: an agent profile states which domains start active.
5. **Malformed arguments are passed through and re-asked, not silently
   repaired.** A payload that is not a JSON object becomes an in-band tool result
   naming the problem; the turn is re-asked at most twice with that error. The
   existing syntax-level repairs (control characters inside strings, trailing
   commas, provider markup folded into a name) stay, because they demonstrably
   work on syntax. Nothing attempts to recover value-level damage.
6. **DeepSeek `strict` mode lands separately**, behind a setting that is off by
   default, after the schema shape settles. It requires every property to be
   `required` and `additionalProperties: false`, so writing strict-compatible
   schemas before the surface is final would mean writing them twice.
7. **Approval risk resolves from the called tool name directly.** Today the risk
   of a facade call is taken from the recovered inner name; with flat tools the
   name is the call, which removes an entire class of "a repaired call must not
   be measured as a cheaper tool" reasoning.

## Consequences

**Positive**

- The dominant error class disappears by construction: there is no inner key to
  omit or rename, and one fewer escaping layer over code payloads.
- The advertised surface sits inside the published accuracy budget instead of at
  its edge.
- Prompts shrink when the active set is small: definitions are loaded on demand.
- Approval risk, budgets, and parallelism all key off one name.
- Matches the shape of every surveyed harness, so future provider features
  (strict mode, custom tools, tool search) apply directly instead of through a
  translation layer.

**Negative, and the cost is real**

- **Activation costs a round trip** when the model needs a tool outside its base
  set. Mitigation: base presets that cover common work, and one discovery call
  that activates a whole domain rather than a single tool.
- **Activation changes the advertised tool list, which invalidates the cached
  prompt prefix** — a direct tension with ADR-004 (cache-first assembly). This
  must be measured, not assumed: activation should happen in as few turns as
  possible, ordering must be deterministic so an unchanged set is byte-identical,
  and the cache-read ratio is a success measure below.
- The registry, prompt assembly, approval-risk resolution, parallelism checks,
  and a large number of tests currently assume the envelope. This is a wide
  change.
- Model behaviour changes: a model that learned the facade shape from prior
  sessions must be steered by the tool descriptions and the base preset.

## Success measures

Measured from the session store, before and after, over comparable traffic:

| Measure | Target |
| --- | --- |
| Envelope/serialization share of tool errors | → 0 by construction (class removed) |
| Overall tool-error rate | below 1% (from 2.1%) |
| Reads/edited ratio, tool calls per turn | not worse than baseline |
| Cache-read token ratio | not materially worse; report the delta, do not assume |
| Prompt tokens for tool definitions | lower on turns with a small active set |

## Alternatives considered

- **Keep the envelope and invest in repair.** Rejected on evidence: three
  patches, no movement in the 39% share, and repair cannot fix value-level
  corruption (0/14).
- **Flatten all 136 tools.** Rejected: it exceeds the 128-schema cap and puts
  the advertised set far past the 30–50 accuracy range. Selection is required
  regardless of shape.
- **Freeform/custom tools for edits** (Codex `apply_patch` with a Lark grammar).
  Deferred rather than rejected: the precedent is strong and it eliminates
  escaping for code entirely, but it depends on provider support that DeepSeek
  does not document. Revisit after the flat surface lands.
- **Do nothing.** Rejected: a measured 2.1% failure rate with a known cause.

## Open questions

- Should discovery return full schemas, or names plus one-line descriptions with
  a second call for the schema? (Affects activation cost and prompt size.)
- What happens when activation would exceed the 30-tool cap — evict least-recently
  activated, or refuse and explain?
- Does activation belong to the session or the turn, and how does it interact with
  subagents that inherit a parent's set?

## Supersedes

The facade execution contract in
[05-agent-runtime-and-tools.md](../architecture/05-agent-runtime-and-tools.md)
("Domain facades"), which describes `<domain>_run` as the execution path.
Discovery via `<domain>_list` survives.
