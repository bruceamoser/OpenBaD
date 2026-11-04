# ADR-009 — Supervised work pipeline

- **Status:** Proposed
- **Date:** 2026-11-04

## Context

OpenBaD is turn-driven: a human types in the chat surface, a session runs, tools
execute, and everything that governs *whether work may happen* is per action —
approvals, outside grants, access modes.

The work that actually ships this repository is bigger than one turn and follows a
stable shape: discuss a problem, agree a plan, file an issue, branch, implement,
check, open a pull request, review it, answer the review, merge. That loop exists
today only as prose in `docs/SOP.md` and as an agent following it by hand.

The `dsh-superagent-prototype` (DeepSeek, MIT, explicitly research code) models
the same loop as a subsystem:

```
Source → Trigger → Thread → Eval → Ticket + Mandate → Assignment → Executor → Channel
```

Three properties of that model are the reason it is worth adopting rather than
inventing:

1. **Authorization belongs to the work item, not the action.** A Mandate is
   immutable and carries the policy version, scope, strength, the matched rules,
   and the sender's identity. Its holder acts inside that scope without asking
   again.
2. **Ownership of a conversation, not a mention.** A Ticket subscribes to its
   Threads, so review comments, CI results and later discussion reach its
   Executor without anyone mentioning the agent again.
3. **Stage is derived from facts.** The panel computes what needs a person from
   structured reports — never from the model's prose. The prototype states the
   rejection plainly: *"Inferring 'waiting on a person' from a report's free text
   — the classification needs structured facts."*

## Decision

Add a **per-project supervised work pipeline** to OpenBaD. A project opts in;
projects that do not are unaffected and remain purely interactive.

### Domain

Adopt the prototype's concepts and names: `Source`, `Trigger`, `Thread`,
`Eval`, `Ticket`, `Mandate`, `Assignment`, `Subscription`, `Channel`, `Report`.
Semantics we do not restate here follow
[001 Domain model](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/001-domain.md)
and [002 Authorization](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/002-authorization.md).

### Scope

A **Source instance** is one configuration: repository, label filter, allowed
list, enabled flag. Source state — its read position — is persisted so a restart
never replays events.

**Eval is rules-first.** It decides accept / attach / stop / ignore from the
label set, the allowed list, the thread's tickets and the policy version. It holds
no state. A model call is used only to judge whether a labelled issue is
coherent enough to start, and its answer is advisory.

**Admission is gated by labels.** The label set is the human's authorizing
artifact: the chat surface produces a plan and a series of labelled issues, and
the label is what a Trigger must carry to become a Ticket.

### Ownership

One Executor owns a Ticket at a time, and an Executor is one session
(`worker-<ticket>`) in its own directory under the project's execution root.

Review is **not** a second agent inside the domain. A Ticket subscribes to the
pull request it opened, so review comments, approvals and check results arrive as
Triggers and wake the same Executor, which resolves them and pushes again.

The reviewing agent therefore sits **outside** the domain, as a GitHub actor. Its
approval reaches the pipeline the same way a person's does — as a Trigger on the
Thread.

### Authority

A Ticket's Executor runs with **Full access inside its own worktree** and is not
asked to approve each action: that is what the Mandate buys, and asking anyway
would reproduce the gate the Mandate exists to remove.

Two deviations from the prototype are deliberate and stay:

- **Irreversible actions outside the project are still refused.** The prototype's
  workers run as the operator's OS user without approval. We keep the existing
  refusal for destructive commands outside the workspace or scratch space and for
  writes outside the workspace. A mandate authorizes a task, not the machine.
- **The executor never pushes to a protected branch directly.** It pushes its own
  branch and opens a pull request.

### Merge

The Executor enables auto-merge on its own pull request with **no size limit**.
The unlock is the reviewing agent's approval.

Recorded consequence: GitHub attributes a merge to whoever enabled it, so the
merge is attributed to the agent account. Because there is no line cap, the only
guard against a large change merging unreviewed is **branch protection**, which a
project opting in must configure: require review, dismiss stale approvals when
new commits arrive, require the check suite. The prototype states the same
dependency: *"These instructions govern the agent, not GitHub's merge rules."*

### Reports and stage

Every report carries a structured outcome — `ready`, `dropped`, `blocked`,
`failed`, `waiting` — plus an explicit attention field when a person is needed.
Stage is derived from those facts, in this order, first match wins:

| Stage | Condition |
| --- | --- |
| `person/delivery` | delivering an input to the Executor failed and is retrying |
| `person/answer` | the ticket has an attention |
| `person/review` | the latest report is `ready` with a pull request |
| `person/blocked`, `person/failed` | the latest report is `blocked` or `failed` |
| `working/queued` | no assignment, or a pending one |
| `idle` | the latest report is `waiting`, or the assignment ended |
| `working` | everything else |

### Concurrency and cost

A project sets a slot count. A turn that ends with live jobs or subagents keeps
its slot and stays `working`; a handoff to a person frees it immediately and
stops subagents. Each Ticket carries a token and step budget; exceeding it ends
the Ticket as `failed` with the reason, rather than running unbounded.

### Persistence

Tickets, assignments, reports, subscriptions and source read positions are rows
keyed by `workspace_id` in the existing store. Pipeline configuration lives in
`.openbad/pipeline.yaml` in the project. No new database.

## UI surface

The pipeline is a per-project subsystem, so it lives where the other
project-scoped surfaces live. No new navigation pattern: a project area button,
a panel tab, a settings section, and a main-area screen all already exist.

### 1. Sidebar — "does anything need me now?"

A project area button `Pipeline` beside Overview / Workspace / Agents, using the
existing idiom: a **count** of tickets in a `person/*` stage and a **pulse** while
any ticket is `working`. This is the only always-visible signal, and it answers
the prototype's first panel question without opening anything.

### 2. Panel — "what is the state?"

`openPanel("pipeline")` lists tickets grouped by stage, needs-a-person first:

| Group | Stages |
| --- | --- |
| Needs you | `person/answer`, `person/review`, `person/blocked`, `person/failed`, `person/delivery` |
| Working | `working`, `working/queued` |
| Idle | `idle` |
| Ended | ended, with outcome |

Each row carries the ticket's short name, its source (`issue #123`), stage, age,
the last report's one-line summary, and a pull-request link. A row opens the
ticket screen. Rows are read-only: the only writable things are domain commands —
stop, retry, re-evaluate — and each maps to one engine command, never to a direct
state edit.

### 3. Main-area screen — "review this work"

The panel is 460px and resizable; a review needs more. The ticket screen opens in
the main area, the way `GitRepoScreen` and `CodeView` already do, with sections in
the order a reviewer needs them:

- **Why this exists** — the originating trigger, the decision and its reason, and
  the mandate: scope, policy version, matched rules, who asked.
- **What it changed** — the pull request, its checks, the diff, and **what changed
  since your last look**: commits landed since this person last opened the
  ticket. Re-review is the common case, so it gets its own affordance.
- **What it said** — the report timeline with structured outcomes, the review
  thread, and each comment's resolution state.
- **What you can do** — approve, request changes, stop the ticket, retry, open on
  GitHub. Approving from here is what unlocks auto-merge; it is the one action
  that can cause a merge, so it is the only one that asks for confirmation.

### 4. Settings → Project → Pipeline — "set it up"

Configuration, not operation, so it belongs in Settings with the other
project-scoped settings:

- **Repository** — this project ↔ `owner/repo`, verified by a read before it saves.
- **Triggers** — the labels that admit work, plus receive-all toggles for issues,
  pull requests, comments and default-branch CI.
- **Allowed list** — GitHub logins permitted to originate work.
- **Reviewing agent** — the login whose approval unlocks merge.
- **Guards** — required review, dismiss stale approvals on new commits, required
  checks.
- **Limits** — tokens and steps per ticket, concurrency slots.
- **Advanced** — poll interval, execution root, blocked commands.

**Enabling auto-merge is blocked until the guards check passes.** The ADR makes
branch protection the only thing between a large unreviewed change and `main`, so
the surface must verify the protection is real — required review on, stale
approvals dismissed on new commits, check suite required — and refuse the switch
otherwise, with the specific missing setting named.

### Trust-model clarity

Executor sessions run with Full access under a mandate while interactive chats ask
per action. The ticket screen states which one applies ("this work runs under
mandate M-14 from policy v3"), so a person never has to guess which trust model
they are looking at.

## Consequences

**Positive**

- The loop that ships this repository becomes resumable, auditable and observable
  instead of living in an agent's working memory.
- Authority is attached to work, so an executor stops asking permission for every
  edit — the friction the interactive surface has been accumulating.
- Stage derived from facts answers "is this broken, or is it waiting on me?"
  without inference from prose.
- Reuses what exists: `github` tool actions (`issue`, `label`, `pr`, `workflow`,
  `run`, `search`), the event-sourced store, sessions, worktrees, approvals.

**Negative**

- Mandate-scoped authority is a genuine trust shift for executor sessions.
  Interactive sessions keep per-action approval; the two models coexist, which is
  a thing to keep straight in the UI.
- Auto-merge without a size cap can merge a large change on one approval. The
  guardrail is branch protection, configured by the project, not by us.
- Unattended work costs money unattended: the per-ticket budget is the only
  backstop.
- A new long-running component (the Source poller) that must survive restarts,
  rate limits and GitHub outages without replaying or dropping work.

## Alternatives considered

- **No proactive layer.** Rejected: the plan the human produces on the chat
  surface has no way to become work.
- **A separate reviewing agent inside the domain.** Not adopted. It would need a
  second Assignment per ticket or a second ticket on the same thread, and it
  duplicates the ownership rule the prototype already solved with subscriptions.
  The reviewer lives outside the domain as a GitHub actor instead.
- **The prototype's 200-line auto-merge cap.** Not adopted by decision, recorded
  above with its consequence.
- **Reusing the team board as tickets.** Rejected: a team task is a unit of
  delegation and carries no authorization artifact. A Ticket's whole point is the
  Mandate.

## Open questions

- Where does the reviewing agent's identity live — a separate GitHub account, a
  GitHub App, or the operator's own account approving by hand? It decides whether
  its approval can satisfy branch protection without a person.


- Label taxonomy: one label per pipeline stage, or a single `agent:run` plus
  policy rules?
- Who is "allowed"? Per-Source allowlist by GitHub login, or project-wide?
- Does the reviewing agent hold a merge-blocking authority, and does its approval
  count as a required review in branch protection?
- Per-ticket budget defaults: tokens, steps, and wall-clock.
- How does a project pause the pipeline mid-flight — drain running tickets, or
  stop new admissions only?
