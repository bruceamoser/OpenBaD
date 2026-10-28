# 0004 — Chat Experience and Turn Engine Rework

> **Status (2026-10-27):** Phase A implemented on `feature/832-durable-turn-substrate`. Phases B–F proposed. This plan implements, rather than invents, capabilities already listed in the feature table: F-09, F-17, F-18, F-19, F-36, F-45, plus new F-54.

**Review basis.** Three-way review of this repository, [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) at `0.2.1-rc.2` (`master`, `687f282e`), and VS Code at `main` (`7c4967fad97`). The question was narrow: *which turn and chat technology should OpenBaD adopt, and which user experience should it imitate?*

**Answer in one sentence.** Keep OpenBaD's cache-first, event-sourced engine and its Rust/Tauri implementation; adopt DSH's discipline of explicit turns, steps, attempts, inboxes, and fail-closed approvals; express the result through VS Code's interaction grammar — not its code.

---

## 1. Why this plan exists

The engine declares more than it emits, and the UI shows less than the engine records.

### 1.1 Evidence from the current code

| Observation | Evidence |
| --- | --- |
| `step/start`, `step/end`, and `assistant/attempt` are declared but never emitted. `session_turns.steps` only increments on `StepEnd`, so the projection is permanently zero. | `crates/openbad-core/src/event.rs:13`, `:19`; `crates/openbad-store/src/projections.rs:41` |
| No message is persisted until the whole run returns; a crash mid-turn loses every visible token. | `apps/desktop/src-tauri/src/lib.rs:4332`, `:4499`, `:4598` |
| Approval is a session-wide boolean. Approving flips the session to full access and queues a synthetic "Continue and retry" message as a **new turn**. `ApprovalDecision` exists but is unused. | `crates/openbad-agent/src/lib.rs:129`, `:364`; `crates/openbad-governance/src/lib.rs:30`; `apps/desktop/ui/src/App.tsx:3770` |
| All tool activity renders as one collapsed element of raw text: no per-call status, duration, arguments, or inline result. | `apps/desktop/ui/src/App.tsx:3005`; `apps/desktop/ui/src/types.ts:72` |
| File edits have no inline diff in the transcript; diffs live behind a separate command and artifacts. | `apps/desktop/src-tauri/src/lib.rs:4708` |
| Each streamed delta copies the whole entry array, so a token re-renders the transcript. | `apps/desktop/ui/src/App.tsx:218`, `:1310` |
| Only one run may exist per project at a time. | `apps/desktop/src-tauri/src/lib.rs:142` |

### 1.2 What already works and must not regress

- **Cache-first discipline is real.** `SeriesGuard`/`RequestEnvelope` hash the system prompt, tool schemas, workspace, model, thinking mode, and reasoning effort; JSON is canonicalized before hashing. This is the constraint that decides what we may copy from other harnesses. See `crates/openbad-harness/src/envelope.rs` and `crates/openbad-harness/src/prompt.rs`.
- **Append-only, single-writer SQLite with derivable projections.** Every write goes through one writer thread; projections can be rebuilt from the log.
- **Reasoning and tool calls are already persisted** in assistant payloads and rehydrated into `ChatMessage` (`chat_message_from_session` in `apps/desktop/src-tauri/src/lib.rs`). The gap is typing, not storage.
- **Steering and cancellation exist** (`SteeringInbox`, `AtomicBool` cancel) and are polled at step boundaries, inside the stream, and before each tool call.
- **Compaction exists** at a 0.8 trigger with a 0.16 verbatim tail.
- **The composer** already supports attachments, draft history, and jump-to-latest.

### 1.3 Thesis

DSH supplies the *state discipline*: turns, steps, attempts, an inbox, a fail-closed tool pipeline, and the rule "model-visible means logged." VS Code supplies the *interaction grammar*: inline approvals, reviewable edits, checkpoints, queue-versus-steer, legible progress, and accessible transcripts. OpenBaD supplies the *constraint*: the model-visible prefix is immutable and append-only, and every exception must be explicit, logged, and measured.

---

## 2. Scope

### In scope

1. Complete the durable turn log and make streaming crash-safe.
2. A unified inbox so queueing, steering, and notices share one mechanism.
3. A fail-closed tool pipeline with per-call approvals.
4. A transcript that renders reasoning, tool calls, edits, approvals, checkpoints, and progress as first-class rows.
5. Long-context behavior: token pressure, spill, compaction, and overflow retry.
6. Composer, session list, and notification UX for parallel sessions.
7. Accessibility and a token-based theme layer.
8. New feature **F-54 — parallel agent sessions**.

### Out of scope

- The Cordis/TypeScript plugin runtime, capability-seam ceremony, plugin records, format generations, profile/bundle/patch/HMR composition.
- Reusing VS Code's implementation (Electron, workbench lists, webviews, Monaco) or embedding the workbench.
- Inline editor chat, voice/dictation, browser/computer use, cloud sync, multi-user.
- New model providers and new tools beyond what approvals/spill require.
- A rewrite of the store; the event log stays the source of truth.

---

## 3. What we take, and what we refuse

### 3.1 From DSH: turn technology

| Idea | DSH reference | What OpenBaD builds | Deliberately not ported |
| --- | --- | --- | --- |
| Turn / step / attempt as durable units | `packages/core/agent-loop`, `docs/agent-lifecycle.md` | Emit the declared events; attempts are retries, not turns | Cordis waterfall events |
| Durable log vs live coordination events | `packages/core/session`, `docs/event-producer-consumer.md` | Two domains over the existing `SessionEventKind` | Plugin-owned projections |
| Inbox with claim/splice semantics | `packages/core/agent-loop/src/inbox.ts` | One inbox for prompts, steering, notices | Event-bus dispatch |
| Durable settlement + interrupted anchor | `packages/core/session/README.md` | Settle per step; on cancel persist delivered prefix | The compact-stream wire format |
| Fail-closed approvals | `docs/subsystems/approval.md` | `allowed-once / rejected / cancelled / unavailable` | Answerer chains |
| Tool pipeline with pre/guard/execute/post/result | `docs/tool-execution-pipeline.md` | Four explicit stages in `execute_tool_call` | `next()` chaining |
| Bounded parallel tools + exclusive barriers | `packages/core/agent-loop/src/tool-calls.ts` | `max_parallel_tool_calls` with barriers | — |
| Token meter, overflow retry, tool-result pruning | `packages/compaction/compaction-basic`, `spill` | Overflow condense-and-retry; spill oversized results | Compaction plugin API |
| Request header snapshots | `packages/core/agent-loop/README.md` | Persist the hashes `SeriesGuard` already computes | Route metadata plugin |
| Optimistic echo + atomic replacement | `packages/client/ui-chat` | Echo by client id, replaced by the authoritative event | Slot factories |
| Semantic content blocks | `packages/llm/llm/src/types.ts` | A typed *view* over existing payloads | Merge-extensible open unions |

### 3.2 From VS Code: user experience

| Move | VS Code reference | OpenBaD expression | Phase |
| --- | --- | --- | --- |
| Per-tool approval card: allow once, allow for session, skip | `chatToolConfirmationSubPart.ts` | Inline card in the tool row, keyboard-acceptable | B |
| Per-file edits with Keep/Undo and aggregate summary | `chatEditingActions.ts`, `chatChangesSummaryPart.ts` | Edit cards plus a changes rail | C |
| Checkpoints and Restore | `chatEditingActions.ts` | Checkpoint row with restore and confirm | C |
| Edit-and-resend and Retry | `chatTitleActions.ts`, `chatEditingActions.ts` | Hover actions on request and assistant rows | C |
| Scroll anchoring plus "jump to latest" | `chatListWidget.ts` | Stick-to-bottom with suppression while reading | C |
| Legible progress: rotating verbs, steps, "taking longer" | `chatThinkingContentPart.ts`, `chatProgressContentPart.ts` | One collapsible progress row per turn | C |
| Queue versus steer | `chatQueueActions.ts` | Composer stays enabled; primary action offers both | E |
| Todo list "N/M" | `chatTodoListWidget.ts` | Live checklist panel fed by todo events | D |
| Subagent as a nested, collapsible node | `chatSubagentContentPart.ts` | Child run row with status and pending approvals | D |
| Mode picker with announced intent | `chatInputPart.ts` | Segmented Ask/Plan/Agent control | E |
| Model card with metadata and pin | `modelPickerCard.ts` | Model chip opening a card | E |
| Context attachments as removable chips | `chatAttachmentsContentPart` | Chip row above the input | E |
| Usage footer with hover breakdown | `chatListRenderer.ts` | Per-turn tokens, cache hits, cost | D |
| Accessible transcript plus "focus next approval" | `chatResponseAccessibleView.ts`, `chatAccessibilityActions.ts` | Linearized transcript view and a shortcut | F |
| Session list with pin/group/archive and status | `SESSIONS_LIST.md` | Sidebar groups and state dots | E |

### 3.3 Refused on purpose

1. **Cordis and the three-role capability seam.** A Rust trait plus a default implementation is enough.
2. **The DSH scroll microsystem.** Six hooks, `scrollend`, subpixel thresholds, and per-group following are a browser-specific liability. Start with coarse stick-to-bottom plus anchor restore.
3. **Plugin records, `ignorable` markers, and format generations.** One schema version and forward-only migrations.
4. **Merge-extensible unions and brand-heavy ID proliferation.** Closed enums and newtypes only where the UI or store needs them.
5. **Lossless-JSON validation plus deep-freeze on every append.** Keep validate-before-accept; share with `Arc` instead of cloning.
6. **VS Code's chat implementation.** We take the grammar, not the workbench.

---

## 4. Target architecture

### 4.1 Two event domains

| Domain | Examples | Storage | Consumer |
| --- | --- | --- | --- |
| **Durable** | `turn/start`, `step/start`, `user/message`, `assistant/message`, `assistant/attempt`, `tool/call`, `tool/result`, `approval/requested`, `approval/decided`, `compaction/*`, `turn/end` | SQLite log, append-only | Replay, resume, projections |
| **Live** | `agent/status`, `inbox/spliced`, `assistant/stream`, `tool/status`, `usage/tick` | Tauri channel only | Current UI |

Rules:

- **Model-visible means logged.** Anything that can enter a request exists in the durable log.
- Live events may be dropped without corrupting history. The UI must be reconstructible from the log.
- Every durable message event carries a surface operation: `append` or `replace { start_seq, end_seq }`.

### 4.2 Turn, step, attempt

```text
Idle
 └─ turn/start
     ├─ step/start
     │   ├─ attempt/start (live)
     │   ├─ assistant/message   (success, durable)
     │   └─ assistant/attempt   (failure, retry, cancel; never model-visible)
     │   ├─ tool/call ─ approval? ─ tool/result
     │   └─ step/end
     └─ turn/end { cause: completed | cancelled | error | budget | interrupted }
```

Invariants:

1. A turn with rejected or empty input closes with zero steps.
2. A failed attempt never fabricates an assistant message.
3. An undispatched tool call becomes a synthetic `tool/call` plus an aborted result; it is never silently dropped.
4. Retry reuses the assembled request — no re-assembly, no re-admission.
5. A cancelled stream finalizes the delivered prefix as an `interrupted: true` assistant message so the next request matches what the user saw.
6. Budgets are explicit: steps, tokens, cost, and — subject to open question 4 — wall-clock time.

### 4.3 One inbox

```rust
enum InboxEntry {
    Prompt { id: MessageId, text: String },
    Steering { id: MessageId, text: String },
    Notice { id: MessageId, kind: NoticeKind },
}
```

- `claim_next()` marks an entry consumed; cancellation clears pending entries with a recorded outcome.
- **Queue** inserts a `Prompt` for the next turn. **Steer** inserts `Steering` consumed before the next request in the current turn.
- `MessageId` is unique across pending entries; insertion is idempotent.

This replaces the current steering-only inbox and is the single mechanism behind the composer's queue/steer choice.

### 4.4 Tool pipeline and approvals

```text
pre_execute  → guards, risk, sandbox policy
approval     → allowed_once | allowed_session | allowed_workspace | rejected | cancelled | unavailable
execute      → bounded pool; exclusive tools act as ordering barriers
post_execute → truncate or spill, collect artifacts
tool/result  → the single authoritative outcome
```

- **Fail-closed.** No answerer, a thrown answerer, or a malformed answer is `unavailable`, which denies.
- **Per-call, in-turn.** Approval resolves the pending call inside the same turn. There is no session-wide elevation and no synthetic "Continue and retry" message.
- **Cache-neutral.** An approval decision is metadata, not a model-visible message. Only the `tool/result` is appended, so the prefix is untouched.
- **Cancellation-safe.** Waiting for a decision races the run's cancellation token; cancel resolves as `cancelled`.
- **Policies, not a boolean.** Replace `allow_commands` with presets — read-only, ask before writes, ask before commands, full access — persisted per workspace.

### 4.5 Content model

The payload already carries `content`, `reasoning_content`, and `tool_calls`, and history mapping already reads them. Therefore:

- Introduce a typed `MessageBlock` view (`Text`, `Reasoning`, `ToolCall`, `ToolResult`, `File`, `Image`) derived in Rust and mirrored in TypeScript.
- Add a semantic `MessageSource` (`instructions`, `catalog`, `snapshot`, `notice`, `relay`, `recall`) where it is missing. Source describes *why* a message exists and is used for context accounting, never for styling.
- **No schema migration.** The wire encoder flattens blocks back to the existing shape, and its output must stay byte-identical for equivalent content. This protects the cache prefix.

### 4.6 Durable streaming

- Settle the complete assistant message at `step/end`, in one transaction. Worst-case loss on a crash is one step.
- On cancel, write the delivered prefix as `interrupted: true`.
- On resume after a crash mid-turn, append turn closers and synthetic aborted results for undispatched calls — as ordinary events, not repair code in storage.
- Persist progress at phase boundaries, never per token.

### 4.7 Cache-first invariants (unchanged)

1. Appending extends the reusable prefix.
2. `replace` invalidates the cache from the first shadowed message; it must carry a reason and be counted.
3. Compaction, explicit rewind, and explicit edit-and-resend are the only legitimate invalidators.
4. Retries and approvals append nothing model-visible.
5. Every request remains a pure function of the log; persisted request-header hashes make cache misses diagnosable.

### 4.8 Client state and rendering

The current UI keeps flat `ChatEntry` objects and rebuilds the transcript array on every delta.

```ts
type ChatStore = {
    entries: Map<EntryId, ChatEntry>;
    order: EntryId[];
    subscribe(id: EntryId, listener: () => void): () => void;
    apply(event: AgentEvent): void;   // patches one entry, not the array
};
```

- **Per-entry subscriptions.** A token touches only the streaming entry; React 19 `useSyncExternalStore` avoids a new state library.
- **Frame-batched deltas.** Reasoning and content deltas flush once per animation frame; durable events flush immediately.
- **Optimistic echo.** Submissions appear instantly keyed by client id and are replaced atomically by the authoritative event, preserving component identity.
- **Folding, not virtualization, first.** Completed process rows collapse; add windowing only if measurements demand it.

---

## 5. Target experience

### 5.1 Transcript anatomy

One turn renders as an ordered set of rows:

| Row | Content | Actions |
| --- | --- | --- |
| Request | Text, attachment chips | Copy, edit and resend, retry |
| Progress | Status verb, elapsed, step count; collapsed | Expand to steps |
| Thinking | Collapsed reasoning, streamed | Expand, copy |
| Tool card | Name, target, status, duration; expandable arguments and result | Approve, skip, retry failed, open artifact |
| Approval card | Risk, command or path preview, policy scope | Allow once, allow for session, skip; keyboard hints |
| Edit card | File path, `+N −M`, diff preview | Keep, undo, open diff |
| Changes summary | `N files changed, +X −Y` | View all changes |
| Checkpoint | Turn boundary | Restore (with confirm) |
| Error | Message, retryability | Retry, open logs |
| Answer | Markdown, code blocks, citations | Copy, retry, feedback |
| Follow-ups | Suggested next requests | Send as new prompt |

Status vocabulary is fixed: `Thinking`, `Working`, `Waiting for approval`, `Stopping`, `Stopped`, `Failed`, `Done in N steps`.

Rules:

- Tool approval is never a modal. Modals are reserved for destructive, system-level actions.
- Folding must never hide the final answer.
- Every interactive row is reachable by keyboard and announced by a screen reader.
- Hover toolbars also appear on focus.

### 5.2 Composer

- Mode control: **Ask | Plan | Agent**; the placeholder and announced intent change with the mode.
- Model chip opens a card with context window, thinking effort, cost, default/pin.
- Context chips: files, symbols, images, workspace; removable, with a count and token estimate.
- While a turn runs, the primary action becomes **Stop**, and Send offers **Queue** or **Steer**. Queued count is visible.
- Attachments, paste, drop, and draft history keep their current behavior.

### 5.3 Sessions and notifications

- Session rows show title, workspace, state (`running`, `needs approval`, `unread`, `error`, `idle`), relative time, and cost.
- Pin, group, archive, search, and a parallel-run badge.
- Subagent runs appear as child sessions and can be opened.
- In-app toast for completion while unfocused, approval needed, and errors. OS notification only when the window is unfocused, at most once per event. Transient problems stay inline and do not toast.

### 5.4 Accessibility

- `aria-live="polite"` announces phase changes (sent, thinking, tool running, waiting for approval, done), throttled; `assertive` for approvals and failures. Never announce tokens.
- Dialogs trap focus, close on Escape, and restore focus.
- **Focus next approval** shortcut, plus a **read transcript** view that linearizes requests, answers, tool outcomes, edits, and errors into plain text.
- A keyboard-map help dialog; visible focus rings; `prefers-reduced-motion` disables shimmer and rotation.

### 5.5 Theming

- Replace the hardcoded dark palette with tokens: `--obd-color-*`, `--obd-space-*`, `--obd-radius-*`, `--obd-font-*`, `--obd-motion-*`.
- Dark, light, and system themes; comfortable and compact density.
- State colors are semantic (`running`, `success`, `warning`, `danger`, `needs-input`), not hue-based.
- Tailwind utilities consume tokens; components contain no literal colors.

---

## 6. Phases

Assumes one senior engineer; adjust without changing order. Each phase ends in something usable.

### Phase A — Durable turn substrate

**Goal:** the log describes what actually happened, and a crash cannot erase a visible answer.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-01 | Emit `step/start`, `step/end`, `assistant/attempt`, and request-header snapshots | F-01, F-02 | Replaying a session yields the same step count the UI showed; `session_turns.steps` is non-zero |
| P9-02 | Settle each step; write `interrupted` anchors on cancel; append turn closers on resume | F-01, F-09 | Kill the process mid-turn; on restart the delivered text is present, marked stopped, and the next request contains exactly it |
| P9-03 | Implement `derive_messages` with surface ops and a replay-equivalence test | F-01, F-05 | A request built from memory and from replay is byte-identical |
| P9-04 | Unified inbox: prompts, steering, notices; claim/splice/discard | F-08 | Three queued prompts run in order; a steer lands in the current turn; cancel records pending outcomes |
| P9-05 | Allow concurrent runs per project, one registry entry per session | F-10, F-54 | Two sessions stream simultaneously; cancel affects only its own run |

**Exit:** no visible token can be lost by a crash or cancel.

### Phase B — Tool pipeline and approvals

**Goal:** risky actions are decided per call, in place, without elevating the session.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-06 | Four-stage tool pipeline with live status and duration | F-09, F-36 | Each call emits queued → running → settled with a duration |
| P9-07 | Approval broker with `allowed_once/session/workspace/rejected/cancelled/unavailable` | F-36 | Allow once does not change session policy; deny returns a clean tool error; no deadlock |
| P9-08 | Replace `allow_commands` with workspace policy presets | F-36, F-35 | Presets survive restart; no code path flips the session to full access implicitly |
| P9-09 | Bounded parallel pool with exclusive barriers | F-08 | Ten safe reads run concurrently; an exclusive call runs alone and in order |
| P9-10 | Cancellation-safe approvals and crash recovery | F-36 | Killing the app during an approval resumes with a cancelled decision, not a hung turn |

**Exit:** no mutation happens without an explicit decision or an explicit standing policy.

### Phase C — Transcript rendering

**Goal:** the transcript becomes the product surface: legible, reviewable, reversible.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-11 | Chat store with per-entry subscriptions and frame-batched deltas | F-09 | A 2,000-token stream re-renders only the streaming row; no array copies per token |
| P9-12 | Progress and thinking rows with verbs, steps, elapsed, and "taking longer" | F-09 | Every turn has a legible state at all times |
| P9-13 | Tool cards with arguments, result, status, duration, and retry | F-09 | A failed call is diagnosable without opening logs |
| P9-14 | Edit cards with Keep/Undo, diff preview, and changes summary | F-17 | Every file edit is reviewable in the transcript |
| P9-15 | Checkpoints with restore | F-17 | Restore reverts workspace and surface together, after confirmation |
| P9-16 | Retry, regenerate, and edit-and-resend | F-09 | Retry appends an attempt inside the same turn; edit-and-resend forks a branch at the prior request and warns before any explicit truncation |
| P9-17 | Scroll anchoring, jump-to-latest, reading mode | F-09, F-45 | Reading older content during a stream is never interrupted |

**Exit:** a user can review, correct, and undo everything the agent did without leaving the chat.

### Phase D — Long context and delegation

**Goal:** long sessions and delegated work stay comprehensible and affordable.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-18 | Token meter, context gauge, per-turn usage footer, and a compaction row in the transcript | F-04, F-05 | Pressure is visible before compaction; each turn reports tokens, cache hits, and cost; compaction is explained in place |
| P9-19 | Spill oversized tool results with a retrieval reference | F-05 | A 2 MB result does not enter the context body; it remains retrievable |
| P9-20 | Overflow condense-and-retry | F-05 | A context-overflow failure recovers once without user action |
| P9-21 | Todo and plan panels fed by tool events | F-18 | Todos show `N/M` and update live |
| P9-22 | Subagent cards and child-session navigation | F-19 | Pending child approvals are visible from the parent |

**Exit:** a 500k-token session survives, stays legible, and reports its cost.

### Phase E — Composer, sessions, notifications

**Goal:** the daily-driver loop: many sessions, fast iteration, no surprises.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-23 | Mode picker with prompt and tool changes per mode | F-06, F-18 | Ask cannot mutate; Plan writes only plans; Agent has the full surface |
| P9-24 | Model card, context chips, and estimates | F-42, F-50 | Model, thinking effort, and context are visible before send |
| P9-25 | Queue/steer UI and queued-count indicator | F-08 | The choice is explicit and reflected in turn behavior |
| P9-26 | Session sidebar states, pin/group/archive, unread | F-10, F-54 | Parallel work is navigable without opening each session |
| P9-27 | Notification policy: inline, toast, OS | F-45 | One notification per event; none while focused; none for transient noise |

**Exit:** three sessions can run in parallel and be driven entirely from the keyboard.

### Phase F — Accessibility and theming

**Goal:** the experience is usable by keyboard and screen reader, and themeable without code changes.

| ID | Task | Features | Acceptance |
| --- | --- | --- | --- |
| P9-28 | Phase-level live-region announcements | F-45 | NVDA and VoiceOver announce send → approve → done; tokens are never announced |
| P9-29 | Focus management, traps, and focus-next-approval | F-45 | No focus escape from dialogs; approvals reachable in one shortcut |
| P9-30 | Linearized accessible transcript view | F-45 | Every row type is readable as plain text, including errors and edits |
| P9-31 | Token-based themes: dark, light, system, density, reduced motion | F-40, F-45 | No literal colors remain in components; light theme meets AA contrast |
| P9-32 | Keyboard map and help dialog | F-45 | Every action in this plan is reachable without a pointer |

**Exit:** a keyboard-only and a screen-reader-only session both complete end to end.

---

## 7. Milestones

| Milestone | Phases | Outcome | Estimate |
| --- | --- | --- | --- |
| **M9.1 — Durable** | A | A crash or cancel cannot erase visible work | 2 weeks |
| **M9.2 — Safe** | B | Risky tools are decided per call, in place | 2 weeks |
| **M9.3 — Trustworthy** | C | Reviewable, reversible transcript | 3 weeks |
| **M9.4 — Sustainable** | D | Long sessions and delegation remain legible | 2 weeks |
| **M9.5 — Daily driver** | E | Parallel sessions and a fast composer | 2 weeks |
| **M9.6 — Inclusive** | F | Accessible and themeable | 1.5 weeks |

Dependency: B may start in parallel with A once P9-01 lands. C depends on A and B for real states to render. F is continuous but is gated at the end.

---

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Approval deadlock or hung turns | Race decisions against the run's cancellation token; deny on timeout; close unresolved approvals on resume |
| Cache-hit regression from the new event work | Keep the envelope hash test; add a benchmark gate on cache-read ratio; report invalidators with reasons |
| Write amplification from step-level settlement | Settle per step in one transaction, never per token; measure WAL growth |
| UI refactor destabilizes the current chat | Land the store (P9-11) behind a flag; keep the old renderer until parity |
| Scope creep toward an editor | Monaco, inline chat, and webviews are explicit non-goals |
| Concurrency races with multiple runs | One registry entry per session, per-session cancellation, a global resource semaphore, and stress tests |
| Accessibility regressions | Per-phase keyboard and screen-reader checklist; no phase exits without it |

---

## 9. Validation

- **Replay equivalence:** `derive_messages(log)` equals the in-memory history for every fixture.
- **Crash tests:** kill the process during a stream, during a tool call, and during an approval; assert recovery and no data loss beyond one step.
- **Approval tests:** allow once does not persist; deny is a clean result; cancel wins the race; unresolved approvals close on resume.
- **Cache regression:** run the existing cache-first benchmark; a drop greater than two percentage points in cache-read ratio fails the phase.
- **Performance:** streaming at 60 fps with a 5,000-entry transcript; no full-array copies per delta; measure frame time.
- **Accessibility:** keyboard-only script for send → approve → edit → undo → restore; NVDA/VoiceOver pass; contrast AA in both themes.
- **Determinism:** no live event may be required to reconstruct the transcript.

---

## 10. Open questions

1. ~~Should **Retry** append an attempt inside the same turn, or open a new turn?~~ **Resolved 2026-10-27:** same turn, as an attempt. Budgets and checkpoints stay coherent; a failed attempt never becomes a model-visible message.
2. ~~Should **edit-and-resend** truncate the surface or fork a branch?~~ **Resolved 2026-10-27:** fork a branch. The branch point is preserved; truncation remains an explicit, separate action.
3. Do we need transcript windowing at all, or is folding sufficient below ten thousand rows? Decide after P9-11 measurements.
4. Is a wall-clock turn budget required, or are steps, tokens, and cost enough?
5. How much of the light theme is a first-class design versus a token inversion? Decide before P9-31.
6. Should compaction be user-triggerable in addition to automatic? DSH supports `/compact`; recommendation: yes, once P9-18 lands.

---

## 11. Definition of done

1. No visible token is lost by crash, cancel, or restart.
2. No risky tool runs without an explicit decision or an explicit standing policy.
3. Every file edit is reviewable, keepable, and undoable in the transcript.
4. Every wait is legible: what is running, where, for how long, and why it is waiting.
5. The user can always interrupt, steer, queue, or retry.
6. The transcript is complete and correct for a screen reader.
7. Streaming does not jank while the user reads.
8. No new cache invalidation exists outside compaction, rewind, and explicit edit-and-resend.

---

## 12. Status

| Date | Change |
| --- | --- |
| 2026-10-27 | Proposed after a three-way review of OpenBaD, DeepSeek Harness, and VS Code chat. |
| 2026-10-27 | Phase A (#832) landed: step lifecycle events, atomic step settlement, interrupted anchors, crash repair, replay equivalence, unified inbox, and per-session concurrency. Prompt-queue consumption is wired with the composer work in P9-25 because the UI must own the queued entry. |
| 2026-10-27 | Phase B (#833) landed: four-stage tool pipeline with live status, a fail-closed approval gate with per-call decisions and session/workspace scopes, policy presets persisted per session, and a bounded parallel tool pool. |
