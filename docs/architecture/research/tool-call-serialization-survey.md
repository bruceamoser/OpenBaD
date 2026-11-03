# Tool-call serialization: an industry survey

**Status:** reference for the tool-surface redesign. Every claim carries a source; claims
that could not be verified are flagged. No design decisions are made here.

## Why this exists

Measured over ~7,000 tool results in this project's own session store, 2.1% were
errors, and **39% of those errors were malformed proxy envelopes or JSON
escaping failures** — invalid escapes, control characters, omitted or renamed
inner keys. Three successive repair patches did not reduce the share. This
document records how the rest of the field handles the same problem so the next
change copies proven practice instead of inventing one.

## 1. Argument representation on the wire

| Provider | Field | Type | Parsed by |
| --- | --- | --- | --- |
| OpenAI Chat Completions | `tool_calls[].function.arguments` | **string** | the client |
| OpenAI Responses | `function_call.arguments` | **string** | the client |
| Anthropic Messages | `tool_use.input` | **object** | the API |
| Google Gemini | `functionCall.args` | **object** | the API |
| DeepSeek | OpenAI-compatible `arguments` | **string** | the client |

OpenAI states the consequence plainly: *"the model does not always generate
valid JSON… Validate the arguments in your code before calling your function."*
([api reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create))

Anthropic parses server-side, so a harness never unescapes twice
([messages API](https://platform.claude.com/docs/en/api/messages),
[tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)).

⚠️ DeepSeek's `arguments` type is **inferred** from documented OpenAI-SDK
compatibility, not stated in a field table
([tool calls guide](https://api-docs.deepseek.com/guides/tool_calls)). A
third-party harness had to tolerate the field arriving already-parsed as an
object ([OmniAgent #62](https://github.com/YeQing17-2026/OmniAgent/pull/62)).

**None of these is single-level-with-a-wrapper.** Both forms are one level deep.

## 2. Generation-time guarantees (constrained decoding)

| Provider | Mechanism | Status |
| --- | --- | --- |
| OpenAI | JSON Schema → CFG, token masking per step | GA |
| Anthropic | `strict: true` → grammar-constrained sampling | Stable |
| DeepSeek | `strict: true` on the function, `/beta` base URL | **Beta** |
| vLLM | xgrammar / guidance | GA |
| SGLang | XGrammar (default), Outlines, llguidance | GA |
| llama.cpp | GBNF grammars | GA |
| Gemini | `response_schema` — **for response text, not verified for tool args** | — |

OpenAI's own description is the clearest: the schema becomes a CFG, invalid
tokens are masked to probability 0
([structured outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)).

DeepSeek's `strict` mode requires the `/beta` base URL, **every property
`required`**, and `additionalProperties: false`; supported types include `anyOf`
([strict mode](https://api-docs.deepseek.com/guides/tool_calls#strict-mode-beta)).

⚠️ **No rigorous cross-backend benchmark of schema adherence vs latency was
found.** Treat any percentage claim as unmeasured on our traffic.

## 3. Surface size: how large tool libraries are handled

This is the part that matters most, because this project exposes 36 facade
schemas over 136 internal tools under a 128-schema cap.

Anthropic publishes both the problem and the fix:

> "Loading every tool definition up front causes two problems as a tool library
> grows: **Context bloat** … a typical multiserver setup … can consume ~55k
> tokens in definitions … and **Tool selection accuracy**: Claude's ability to
> pick the right tool **degrades once you exceed 30–50 available tools**."
> ([tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool))

Their answer is **tool search**: definitions are not loaded up front; the model
searches the catalogue and loads only what it needs — *"typically reduces this
by over 85 percent, loading only the 3–5 tools Claude needs for a given
request."* The same page notes a client-side equivalent is supported.

Other harnesses bound the surface by **selection**, never by wrapping:

| Harness | Tool shape | How the set is bounded |
| --- | --- | --- |
| DeepSeek Harness (local) | flat (`{filePath, offset, limit}`) | per-agent **plugin composition** presets, plus a **tool set resolved per model turn** (`tools.resolve`), surfaced in the UI as tools added/removed |
| Claude Code | flat; MCP tools namespaced | small fixed set; `old_string`/`new_string` edits |
| OpenAI Codex CLI | flat JSON tools + **freeform `apply_patch`** constrained by a Lark grammar | small set; *"do not wrap the patch in JSON"* |
| Aider | plain-text SEARCH/REPLACE blocks | tiny surface; per-model edit formats |
| Cline / Roo Code | native flat tool calls | small set, per-mode allowlists |

Sources: [codex apply_patch spec](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/apply_patch_spec.rs),
[codex grammar](https://github.com/openai/codex/blob/main/codex-rs/core/assets/tools/apply_patch.lark),
[aider edit formats](https://aider.chat/docs/more/edit-formats.html),
[Claude Code tools](https://code.claude.com/docs/en/tools).

Aider's rationale for abandoning JSON for code payloads is our bug, named:

> "stuffing *source code* into JSON is complicated and error prone … Due to
> escaping issues GPT's code is often syntactically incorrect when it is
> unpacked from JSON, or the JSON decode just fails entirely."
> ([unified diffs](https://aider.chat/2023/12/21/unified-diffs.html#choose-a-familiar-editing-format))

## 4. Failure handling when arguments are unusable

| Project | Behaviour |
| --- | --- |
| LangChain JS | `JSON.parse`; failures go to an `invalid_tool_call` channel, not a throw ([source](https://github.com/langchain-ai/langchainjs/blob/main/libs/langchain-core/src/messages/tool.ts)) |
| Vercel AI SDK | `repairToolCall` hook; failed calls are otherwise **sent back to the model next step** ([docs](https://ai-sdk.dev/docs/ai-sdk-core/tools-and-tool-calling)) |
| MCP TS SDK | schema validation failure → **protocol error** `InvalidParams` ([source](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/packages/server/src/server/mcp.ts)) |
| MCP spec | errors originating *in the tool* `SHOULD` be `isError` results, *"Otherwise, the LLM would not be able to see that an error occurred and self-correct"* ([spec](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#error-handling)) |
| Roo Code | `partial-json` streaming parse + per-field coercion, fail-fast |
| Claude Code | retries an unparseable/absent tool call **twice**, then reports failure; refuses to re-run a request that already executed tools |
| Aider | bounded reflection loop, `max_reflections = 3` |
| DeepSeek Harness (local) | passes the raw non-JSON argument string through to the tool; the loop does not crash |

⚠️ **Repair cannot fix value-level corruption.** A machine-wide scan of 122,636
tool calls found 38 parse errors (0.03%), all from `deepseek-flash`; on 14 raw
corrupt strings, `JSON.parse`, `parseJsonWithRepair`, and a strip-then-repair
chain each recovered **0/14** — the damage was value-level, not syntax
([oh-my-pi #15997](https://github.com/can1357/oh-my-pi/issues/15997)). The same
issue documents DeepSeek's DSML markup leaking into `arguments`; elsewhere,
tool calls arriving as plain text instead of `tool_calls` were measured at 11%
(2/19) ([DeepSeek-V3 #1244](https://github.com/deepseek-ai/DeepSeek-V3/issues/1244)).

## 5. Error taxonomy that converged

- **Two tiers.** Protocol-level errors (unknown tool, invalid params, transport) versus
  in-band tool results carrying `isError`. MCP is explicit about why the second tier
  exists. JSON-RPC supplies the code vocabulary (`-32602` invalid params);
  [AIP-193](https://google.aip.dev/193) supplies the richer `reason` + `metadata` model.
- **Instructive messages.** Anthropic: *"Write instructive error messages… include
  what went wrong and what Claude should try next"*, and notes Claude *"will retry
  2–3 times with corrections before apologizing"*
  ([handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)).
- **Typed classes in code.** Codex encodes retryability in the type system
  ([error.rs](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/error.rs)).

## 6. Converged practices

1. Single-level, flat arguments. No provider and no surveyed harness nests a JSON
   object inside a JSON string.
2. Validate on arrival; return the error in-band as a tool result.
3. Prefer generation-time constraint where the provider offers it.
4. Bound retries (2–3, or a small reflection budget).
5. Lenient parsing on ingestion is normal, *in addition to* constrained generation.
6. Large catalogues are handled by **selection and discovery** (tool search,
   presets, per-turn sets, namespacing) — not by wrapping execution in a proxy.

## 7. Contested or unverified

- Object-on-the-wire (Anthropic, Gemini) vs string-on-the-wire (OpenAI, DeepSeek) is
  not settled; each camp has shipping rationale.
- Strict-mode schema subsets differ per vendor, so strict-mode code is not portable.
- Protocol error vs in-band error for *malformed arguments* is inconsistent across
  MCP clients; the spec does not adjudicate.
- Idempotency under retry has no documented general solution; Claude Code avoids the
  question by not replaying requests that already ran tools.
- DeepSeek `strict` is beta and inherits documented format drift.
