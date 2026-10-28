# 0005 — Code Mode: One Program Instead of Many Tool Calls

> **Status (2026-10-28):** proposal. Nothing implemented. This plan exists because
> the DeepSeek Harness review identified code-mode execution (PTC) as the one
> capability in that harness with no OpenBaD equivalent, and it is too large to
> add incrementally without a decision on the runtime and the approval contract.

**Question.** Should the model be able to write one small program that calls
OpenBaD's tools in a loop, instead of issuing one tool call per step?

**Answer in one sentence.** Yes, behind a setting, restricted at first to
read-only bindings, because the win is real — a grep-then-read-twenty-files
investigation costs twenty round trips today — but the cost is a runtime we do
not have and an approval contract that must not deadlock the turn.

---

## 1. What code mode is

The model calls a single `code` tool with a program. The host runs the program
in a sandboxed interpreter where a small set of OpenBaD tools are exposed as
bindings:

```js
const hits = grep({ pattern: "ToolContext", path: "crates" });
const files = [...new Set(hits.map(h => h.path))].slice(0, 12);
const outlines = files.map(path => outline({ path }));
return outlines.filter(o => o.symbols.length > 40);
```

The program's return value is the tool result. Intermediate data never enters
the conversation, so twelve file outlines cost one call and a few hundred
tokens instead of twelve calls and twelve results.

## 2. Why it fits this codebase

| Constraint | How code mode respects it |
| --- | --- |
| Cache-first prefix (`SeriesGuard`/`RequestEnvelope`) | One `code` schema replaces a per-task tool selection. The binding catalog is injected as a tail message, exactly like the domain facades, so adding a binding never changes the hashed prefix |
| Domain facades already proxy the surface | `code` bindings resolve through the same registry and `tool_domain` map; the facade pattern is the precedent |
| Approval is fail-closed | Every binding call re-enters the normal gate. A program cannot reach around the policy |
| Everything model-visible is logged | Each binding call emits the same tool events as a direct call, plus one `code` event for the program text and return value |
| Cancellation exists | The turn's `AtomicBool` is polled inside the interpreter between statements and inside every binding |

## 3. The decisions that must be made first

### 3.1 Runtime

| Option | Cost | Risk |
| --- | --- | --- |
| `rquickjs` (QuickJS bindings) | small C dependency, fast, mature, async support | a C toolchain dependency in the build matrix |
| `boa_engine` (pure Rust JS) | no C dependency, slower, larger binary | performance ceiling on large loops |
| `mlua` (Lua) | smallest surface, easy sandbox | the model writes Lua, which it does worse than JS |
| Shell out to `node`/`python` with an IPC bridge | no embedding | a sandbox escape by default; the bridge is a new attack surface |

**Recommendation: `rquickjs`.** The build already requires a C toolchain on all
three platforms, and a JS surface matches what the model writes most reliably.

### 3.2 The approval contract

A program that calls `write_file` in *Ask before writes* blocks mid-execution.
The interpreter must wait on a decision the UI is rendering, without stalling
the async runtime:

- the VM runs on a dedicated blocking thread;
- a binding that needs approval sends a request through the existing approvals
  channel and waits on a bounded condition (the same 50 ms poll the question
  gate uses, honouring the turn's cancel flag);
- a refusal returns an error object into the program, so it can adapt instead of
  dying;
- a timeout or cancel kills the interpreter and records which bindings ran.

### 3.3 What may be bound

Start read-only: `read_file`, `read_files`, `grep`, `glob`, `outline`,
`stat_file`, `git_diff`, `git_status`, `lsp`, `session` (search only). Writes
land only after the approval contract is proven, and never in the first release.

## 4. Phases

| Phase | Scope | Exit criteria |
| --- | --- | --- |
| P1 | Threat model, runtime spike, binding whitelist | A program can call `grep` and return a filtered array; caps enforced |
| P2 | `code` tool, read-only bindings, transcript card, per-call events | Same investigation as a direct run costs fewer calls and is fully legible |
| P3 | Caps and failure: time, memory, output, kill, partial-effect record | A hostile program (infinite loop, allocation bomb, output flood) is stopped and reported |
| P4 | Write bindings behind the approval gate | A refused write returns an error into the program; the turn continues |
| P5 | Measurements and prompt guidance | Round trips, wall clock, and tokens measured on a fixed task; a rule in the prompt says when to prefer `code` |

## 5. Measurement

Use the same task shape as plan 0002: a repo-wide question that needs more than
eight file reads. Record, for a direct run and a code-mode run:

- number of model turns and tool calls;
- wall-clock time to the answer;
- prompt and completion tokens;
- cache-hit ratio (must not regress: the `code` schema is static, the catalog is
  a tail message).

Ship only if code mode wins on tokens and wall clock without losing cache hit.

## 6. Open questions

1. Should a program be able to spawn subagents (`subagent` binding)? Deferred:
   a program that spawns workers is a scheduler, not a script.
2. Do subagents get `code`? Yes if shipped, under the same policy; depth stays 1.
3. Setting or experiment? A setting (`code.enabled`, default off) first; an
   experiment only once the metric is stable.
4. Do we keep the program text in the transcript after the turn? Keep the text
   and the returned value; drop intermediate binding payloads beyond their
   previews.
5. Does `code` count against the domain-facade budget? It is one schema in the
   `exec` domain, not a new domain.

## 7. Definition of done

- `code` ships behind `code.enabled`; read-only bindings only.
- Caps: 30 s, 64 MB, 256 KB output, cancelled by the turn's flag.
- Every binding call appears in the transcript with arguments and a preview.
- A hostile program cannot escape the workspace or the clock.
- Measurements recorded in this plan, with the ship/no-ship decision.

## 8. Status log

| Date | Change |
| --- | --- |
| 2026-10-28 | Created from the DeepSeek Harness review. No implementation; runtime and approval contract are the blocking decisions. |
