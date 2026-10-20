# Phase 9: Heartbeat, Research, and Task Execution

## Purpose

This phase upgrades OpenBaD from a biologically inspired agent framework into a persistent autonomous execution system built on top of the existing substrate.

OpenBaD already has strong foundational systems:

- An MQTT-backed nervous system
- A reflex arc and finite state machine
- Active inference and surprise-driven scanning
- Endocrine regulation
- Cognitive routing and context budgeting
- Stratified memory and sleep consolidation
- A web UI and operator-facing control surface

What OpenBaD does not yet have is a complete, restart-safe autonomous task execution layer with persistent scheduling, explicit research escalation, isolated external tool use, and a formal capability registry.

This phase defines how to add those systems without replacing the architecture that already exists.

## Goals

Phase 9 must deliver the following capabilities:

1. A persistent task system backed by SQLite.
2. A heartbeat-driven scheduler that emits semantic work signals instead of embedding planning in timer loops.
3. A DAG-based task execution model with node dependencies, retries, blocking, and resumability.
4. A research escalation stack for blocked or uncertain work.
5. A trusted in-process capability registry for core OpenBaD actions.
6. An isolated MCP bridge for third-party tools with no ambient access from System 1 paths.
7. L2HR-backed reward evaluation at the task-node level.
8. Full operator visibility through the WUI, telemetry, and auditable persistence.

## Non-Goals

The first implementation of this phase must not attempt the following:

- A full HDDL parser or external planner runtime.
- Distributed task execution across multiple nodes.
- General multi-agent collaboration.
- Arbitrary unrestricted code generation for reward programs.
- Replacement of the current memory subsystem.
- Replacement of the current active inference engine.
- Replacement of the current endocrine controller.

## Existing OpenBaD Systems This Phase Must Reuse

This phase extends the current implementation and must not re-architect working systems without a direct need.

### Active Inference

Use the existing active inference infrastructure as the heartbeat-adjacent observation layer:

- `src/openbad/active_inference/engine.py`
- `src/openbad/active_inference/background_scanner.py`
- `src/openbad/active_inference/budget.py`
- `src/openbad/active_inference/insight_queue.py`
- `src/openbad/active_inference/plugin_loader.py`

### Endocrine System

Reuse the current hormone controller and extend it with task-aware triggers:

- `src/openbad/endocrine/controller.py`
- `src/openbad/endocrine/l2hr.py`
- `src/openbad/endocrine/telemetry.py`

### Cognitive System

Reuse routing, fallback, provider health, and context budgets:

- `src/openbad/cognitive/model_router.py`
- `src/openbad/cognitive/context_manager.py`
- `src/openbad/cognitive/config.py`

### Nervous System and FSM

Reuse existing topics, typed publish-subscribe patterns, and state transitions:

- `src/openbad/nervous_system/client.py`
- `src/openbad/nervous_system/topics.py`
- `src/openbad/reflex_arc/fsm.py`
- `src/openbad/daemon.py`

### Memory System

Reuse the current memory controller and sleep pipeline:

- `src/openbad/memory/controller.py`
- `src/openbad/memory/base.py`
- `src/openbad/memory/episodic.py`
- `src/openbad/memory/semantic.py`
- `src/openbad/memory/procedural.py`
- `src/openbad/memory/sleep/`

### WUI

Extend the current server and front-end instead of creating a second operator interface:

- `src/openbad/wui/server.py`
- `src/openbad/wui/bridge.py`
- `wui-svelte/src/routes/`

## Architectural Principles

The implementation for this phase must obey these constraints.

### 1. System 1 Must Remain Cheap and Narrow

System 1 includes the heartbeat path, reflex handlers, and low-cost monitoring loops. These paths must remain local-first, highly constrained, and denied external tool access.

### 2. System 2 Must Be Explicitly Scoped

High-cost reasoning, external tool execution, and research sessions must only happen in explicit task or research contexts.

### 3. No Ambient External Tool Inventory

The heartbeat, background scanner, and reflex arc must not inherit MCP tool access implicitly.

### 4. Persistence Is Mandatory

Task state, leases, heartbeat metadata, research queue entries, and audit logs must survive process restarts.

### 5. Task Execution Must Be Observable

Every autonomous action must be attributable to a task, task node, research node, or operator command.

### 6. Budget and Resource Controls Must Be Enforced in Code

Rate limits, token budgets, task concurrency, and emergency suppression must be enforced by the runtime and not delegated to model compliance.

## Phase 9 Deliverables

This phase is complete only when all of the following exist.

1. SQLite-backed task and research state.
2. Task CRUD and task execution APIs.
3. A scheduler that emits work events based on persisted state.
4. DAG execution with node lifecycle tracking.
5. A trusted capability manifest and registry.
6. An MCP bridge with task-scoped sessions and audit logs.
7. Reward programs with task-node evaluation.
8. Research queue prioritization for blocked work.
9. WUI visibility into tasks, nodes, research, rewards, and scheduler state.
10. Tests that validate restart safety, lease contention, isolation, and task transitions.

## New Subsystems

Phase 9 introduces four new first-class subsystems:

1. The task subsystem
2. The scheduler subsystem
3. The capability subsystem
4. The MCP subsystem

These subsystems must integrate cleanly with the existing cognitive, endocrine, active inference, and memory systems.

## 1. Task Subsystem

Create a new package:

- `src/openbad/tasks/__init__.py`
- `src/openbad/tasks/models.py`
- `src/openbad/tasks/store.py`
- `src/openbad/tasks/planner.py`
- `src/openbad/tasks/executor.py`
- `src/openbad/tasks/scheduler.py`
- `src/openbad/tasks/rewards.py`
- `src/openbad/tasks/research.py`
- `src/openbad/tasks/service.py`

### 1.1 Responsibilities

The task subsystem owns:

- Task creation and persistence
- Task decomposition into nodes
- Node dependency graphs
- Task leasing and concurrency
- Node execution and retry policy
- Task event logging
- Task note compaction
- Research escalation
- Reward program binding and evaluation

### 1.2 Core Enums

Define the following enums in `models.py`.

#### TaskStatus

- `pending`
- `ready`
- `running`
- `blocked`
- `waiting`
- `completed`
- `failed`
- `cancelled`

#### TaskHorizon

- `immediate`
- `short`
- `medium`
- `long`

#### TaskKind

- `user_requested`
- `recurring`
- `heartbeat_spawned`
- `research_spawned`
- `reflex_spawned`

#### TaskNodeType

- `reason`
- `capability`
- `mcp_tool`
- `summarize`
- `verify`
- `wait`
- `research`
- `consolidate`

### 1.3 Core Dataclasses

The initial design must be strongly typed.

```python
@dataclass(slots=True)
class Task:
    task_id: str
    title: str
    description: str
    kind: TaskKind
    horizon: TaskHorizon
    priority: int
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None
    parent_task_id: str | None
    root_task_id: str
    owner: str
    lease_owner: str | None
    recurrence_rule: str | None
    requires_context: bool
    isolated_execution: bool
    notes_path: str | None
```

```python
@dataclass(slots=True)
class TaskNode:
    node_id: str
    task_id: str
    title: str
    node_type: TaskNodeType
    status: TaskStatus
    depends_on: tuple[str, ...]
    capability_requirements: tuple[str, ...]
    model_requirements: tuple[str, ...]
    reward_program_id: str | None
    expected_info_gain: float
    blockage_score: float
    retry_count: int
    max_retries: int
```

```python
@dataclass(slots=True)
class TaskRun:
    run_id: str
    task_id: str
    node_id: str | None
    started_at: datetime
    finished_at: datetime | None
    status: TaskStatus
    actor: str
    routing_provider: str | None
    routing_model: str | None
```

```python
@dataclass(slots=True)
class TaskEvent:
    event_id: str
    task_id: str
    node_id: str | None
    event_type: str
    created_at: datetime
    payload: dict[str, Any]
```

### 1.4 Internal Execution Model

The internal representation must be DAG-native, even if future HDDL support is added later.

The planner must emit:

- Nodes
- Dependency edges
- Execution hints
- Capability requirements
- Routing hints
- Retry policy
- Reward bindings

Do not block this phase on formal HDDL parsing.

## 2. SQLite State Layer

Add a new package:

- `src/openbad/state/__init__.py`
- `src/openbad/state/db.py`
- `src/openbad/state/migrations/`

Use a local SQLite database at:

- `data/state.db`

### 2.1 Required Tables

The first migration must create at least the following tables.

- `tasks`
- `task_nodes`
- `task_edges`
- `task_runs`
- `task_events`
- `task_notes`
- `task_leases`
- `heartbeat_state`
- `research_nodes`
- `research_findings`
- `reward_programs`
- `mcp_audit`
- `scheduler_windows`

### 2.2 Operational Requirements

The store must support:

- Atomic lease acquisition
- Recovery after process restart
- Efficient polling for due or ready work
- Append-only task event logs
- Idempotent scheduler wake handling
- Event ordering by durable timestamps

### 2.3 Lease Semantics

Leases are required to prevent duplicate task execution.

Each lease record must include:

- `lease_id`
- `owner_id`
- `resource_type`
- `resource_id`
- `leased_at`
- `expires_at`

The executor must renew leases while work is ongoing. Expired leases must be reclaimable.

## 3. Scheduler Subsystem

The scheduler must not embed task planning logic in a timer loop. It should emit semantic work signals based on persisted state.

### 3.1 New Files

- `src/openbad/tasks/scheduler.py`
- `src/openbad/tasks/heartbeat.py`

### 3.2 Scheduler Responsibilities

The scheduler must:

- Wake at configured intervals
- Read `heartbeat_state` from SQLite
- Determine whether recurring work, blocked review, research review, or maintenance work is due
- Publish work events to the nervous system or invoke the task service directly
- Avoid duplicate wakeups for already leased work
- Respect cortisol, adrenaline, quiet hours, and sleep windows

### 3.3 Topic Design

OpenBaD currently uses the `agent/...` namespace in `src/openbad/nervous_system/topics.py`. Phase 9 must stay within that namespace.

Add the following topics:

- `agent/tasks/context_required`
- `agent/tasks/isolated`
- `agent/tasks/events`
- `agent/research/deep_dive`
- `agent/scheduler/wake`
- `agent/scheduler/sleep_window`
- `agent/scheduler/maintenance`

If topic templates are needed, define them in `topics.py` with the same pattern as existing topic constants.

### 3.4 Heartbeat Algorithm

The heartbeat loop must follow this algorithm.

1. Wake on interval.
2. Read persisted scheduler state.
3. Query for due recurring tasks.
4. Query for blocked tasks eligible for re-evaluation.
5. Query for research nodes awaiting work.
6. Query for maintenance or consolidation windows.
7. If no work is due, increment a silent skip counter and return.
8. If work is due, publish the appropriate event or dispatch to the task service.

### 3.5 Heartbeat State

The `heartbeat_state` table must track at least:

- `last_heartbeat_at`
- `last_triage_at`
- `last_context_required_dispatch_at`
- `last_research_review_at`
- `last_sleep_cycle_at`
- `last_maintenance_at`
- `silent_skip_count`

## 4. Capability Subsystem

The current observation plugin model is not sufficient for trusted action execution. Add a separate capability system.

### 4.1 New Files

- `src/openbad/capabilities/__init__.py`
- `src/openbad/capabilities/base.py`
- `src/openbad/capabilities/manifest.py`
- `src/openbad/capabilities/registry.py`
- `src/openbad/capabilities/executor.py`
- `src/openbad/capabilities/core_triage.py`

### 4.2 Capability Manifest

Trusted in-process capability plugins must be described by `openbad.plugin.json` files.

Example manifest:

```json
{
  "id": "openbad-core-triage",
  "name": "OpenBaD Core Triage",
  "version": "1.0.0",
  "tier": "trusted",
  "kind": "tool",
  "module": "openbad.capabilities.core_triage",
  "capabilities": [
    "create_task",
    "queue_research",
    "pause_task",
    "resume_task",
    "mark_task_blocked"
  ],
  "permissions": [
    "db.insert",
    "db.update",
    "mqtt.publish"
  ]
}
```

### 4.3 Manifest Rules

The loader must enforce these rules.

1. Only approved local directories may be scanned.
2. Only package-local Python modules may be imported as trusted.
3. Import must be side-effect free.
4. Invalid manifests must fail closed.
5. Permissions must be validated against `config/permissions.yaml`.

### 4.4 Capability Interface

```python
class Capability(Protocol):
    capability_id: str

    async def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> CapabilityResult:
        ...
```

Required types:

- `CapabilityRequest`
- `CapabilityContext`
- `CapabilityResult`
- `CapabilityDescriptor`
- `CapabilityError`

`CapabilityContext` must include at least:

- `task_id`
- `run_id`
- `actor`
- `permission_scope`
- `budget_snapshot`
- `endocrine_snapshot`
- `memory_controller`
- `mqtt_client`
- `cancellation_token`

### 4.5 Initial Trusted Capabilities

The first implementation must provide the following trusted capabilities.

- `create_task`
- `queue_research`
- `pause_task`
- `resume_task`
- `cancel_task`
- `append_task_note`
- `publish_event`
- `request_escalation`

These capabilities are enough to let System 1 trigger or shape work without granting third-party tool use.

## 5. MCP Subsystem

Third-party tools must be isolated from heartbeat and reflex paths.

### 5.1 New Files

- `src/openbad/mcp/__init__.py`
- `src/openbad/mcp/bridge.py`
- `src/openbad/mcp/session.py`
- `src/openbad/mcp/policy.py`

### 5.2 Scope Rules

These rules are mandatory.

- The heartbeat path has no MCP access.
- The background scanner has no MCP access.
- Reflex handlers have no MCP access.
- Task executor may create MCP sessions for nodes that explicitly require them.
- Research executor may create MCP sessions only if policy permits.

### 5.3 MCPPolicy

```python
@dataclass(frozen=True)
class MCPPolicy:
    allowed_servers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_calls: int
    max_duration_seconds: int
    allow_network_egress: bool
```

### 5.4 Session Requirements

Each MCP session must be:

- Created explicitly by the task or research executor
- Bound to one task run or research run
- Audited per tool call
- Torn down at the end of the run
- Denied access to undeclared tools

### 5.5 Audit Requirements

Every MCP invocation must create an audit record containing:

- `audit_id`
- `task_id`
- `run_id`
- `tool_name`
- `server_name`
- `started_at`
- `finished_at`
- `status`
- `input_summary`
- `output_summary`
- `error_summary`

## 6. Planner

The planner converts a task request into a DAG of executable nodes.

### 6.1 New File

- `src/openbad/tasks/planner.py`

### 6.2 First Iteration Strategy

The first version should be deterministic and template-driven with optional LLM refinement.

The planner must:

- Parse task intent
- Determine horizon
- Estimate node sequence
- Attach dependencies
- Mark context-required vs isolated nodes
- Estimate capability and model requirements
- Attach reward program templates
- Set retries and blocking thresholds

### 6.3 Planner Output

The planner must emit a structure that includes:

- Task metadata
- Nodes
- Edges
- Execution hints
- Default reward templates
- Initial research eligibility

## 7. Executor

The executor runs ready nodes under leases and updates state durably.

### 7.1 New File

- `src/openbad/tasks/executor.py`

### 7.2 Executor Responsibilities

The executor must:

- Lease a ready node
- Create a task run record
- Build a bounded execution context
- Route reasoning through `ModelRouter` when necessary
- Invoke trusted capabilities or MCP sessions only if declared
- Write structured notes and event records
- Evaluate reward programs
- Update endocrine hooks when appropriate
- Transition node and task states

### 7.3 Context Compaction

The executor must not keep raw tool output in active prompt state beyond its immediate utility.

For medium and long horizon tasks:

1. Raw output may be used during the current node.
2. After node completion, replace raw output in working state with:
   - A summary
   - Extracted facts
   - Follow-up implications
   - Artifact references
3. Persist detailed artifacts to disk when configured.

This must be implemented as a task-aware extension of the existing context budget logic in `src/openbad/cognitive/context_manager.py`.

### 7.4 Failure Handling

On node failure, the executor must:

- Increment retry count
- Capture structured failure summary
- Update blockage score
- Either retry, block, or escalate to research based on policy

## 8. Research Subsystem

Research is a specialized escalation path for uncertainty and blockage.

### 8.1 New File

- `src/openbad/tasks/research.py`

### 8.2 Research Node Model

```python
@dataclass(slots=True)
class ResearchNode:
    research_id: str
    source_task_id: str
    source_node_id: str
    trigger_reason: str
    blockage_score: float
    expected_info_gain: float
    urgency_score: float
    priority_score: float
    status: TaskStatus
    findings_summary: str | None
    artifact_path: str | None
```

### 8.3 Priority Formula

Research queue priority should be computed as:

$$
priority = w_b \cdot blockage + w_i \cdot expected\_info\_gain + w_u \cdot urgency - w_c \cdot cortisol
$$

Default weights:

- $w_b = 0.4$
- $w_i = 0.3$
- $w_u = 0.2$
- $w_c = 0.1$

### 8.4 Research Lifecycle

1. A node becomes blocked or enters uncertain completion.
2. The executor computes blockage and expected information gain.
3. If thresholds are exceeded, a research node is queued.
4. The research scheduler acquires the highest-priority item.
5. The research run may use MCP if policy allows.
6. Findings are summarized and persisted.
7. Findings are written into episodic or semantic memory.
8. The source task is re-evaluated.

### 8.5 Memory Integration

Research findings must integrate with the existing memory system.

- Episodic memory stores trace-like records.
- Semantic memory stores reusable findings.
- Procedural memory is updated only when a repeatable workflow is verified.

## 9. Reward Programs and L2HR

OpenBaD already contains a natural-language-to-hormone mapper in `src/openbad/endocrine/l2hr.py`. Phase 9 must extend this into true task-node reward evaluation.

### 9.1 New File

- `src/openbad/tasks/rewards.py`

### 9.2 Reward Program Interface

```python
class RewardProgram(Protocol):
    def evaluate(self, trace: ExecutionTrace) -> RewardResult:
        ...
```

Required types:

- `ExecutionTrace`
- `RewardResult`

`ExecutionTrace` must include:

- `task_id`
- `node_id`
- `duration_ms`
- `retries`
- `api_calls`
- `mcp_calls`
- `tokens_used`
- `budget_remaining`
- `blocked`
- `completed`
- `verification_passed`
- `operator_interrupt`
- `endocrine_snapshot`

`RewardResult` must include:

- `scalar_reward`
- `hormone_adjustment`
- `reasons`

### 9.3 Example Reward Program

```python
def evaluate(trace: ExecutionTrace) -> RewardResult:
    reward = 0
    reasons: list[str] = []

    if trace.completed:
        reward += 10
        reasons.append("completed")

    if trace.verification_passed:
        reward += 5
        reasons.append("verification_passed")

    if trace.api_calls > 5:
        reward -= 100
        reasons.append("api_limit_exceeded")

    if trace.blocked:
        reward -= 15
        reasons.append("blocked")

    hormone_adjustment = {
        "dopamine": 0.1 if reward > 0 else 0.0,
        "cortisol": 0.1 if reward < 0 else 0.0,
    }

    return RewardResult(
        scalar_reward=reward,
        hormone_adjustment=hormone_adjustment,
        reasons=reasons,
    )
```

### 9.4 Safety Constraints

Generated reward programs must not be arbitrary unrestricted code.

Allowed first implementations:

- Restricted Python subset
- Deterministic rule templates
- Validated declarative rules compiled to Python

## 10. Endocrine Coupling

This phase must connect task execution and research pressure to the endocrine system.

### 10.1 Cortisol Inputs

Increase cortisol when any of the following occur:

- Token budget exhaustion
- Thermal threshold breach
- Provider failure storm
- MCP rate-limit exhaustion
- Repeated node retries
- Excess blocked tasks

Effects:

- Suppress research branching
- Lower task concurrency
- Prefer cheaper routes in `ModelRouter`
- Defer maintenance and non-urgent work

### 10.2 Adrenaline Inputs

Increase adrenaline when any of the following occur:

- Critical immune alert
- Explicit urgent user request
- Near-term deadline breach risk
- Cascading critical task failure

Effects:

- Suspend background research
- Widen context allowance for critical tasks
- Allow temporary soft-cap override
- Enter emergency scheduling mode

### 10.3 Dopamine Inputs

Increase dopamine when:

- A task completes with verification
- A high-value research node resolves useful uncertainty
- A repeatable workflow is verified for procedural storage

### 10.4 Endorphin Inputs

Increase endorphin when:

- Stress resolves after adrenaline or cortisol spikes
- Maintenance completes cleanly
- Consolidation or sleep windows complete successfully

## 11. Configuration

Add a new file:

- `config/tasks.yaml`

Initial schema:

```yaml
tasks:
  enabled: true
  db_path: data/state.db
  heartbeat_interval_seconds: 180
  recurring_scan_interval_seconds: 300
  blocked_review_interval_seconds: 600
  research_review_interval_seconds: 900
  max_concurrent_runs: 2
  default_node_max_retries: 2
  blocked_threshold: 0.65
  research_threshold: 0.70
  quiet_hours_start: "23:00"
  quiet_hours_end: "06:00"
  maintenance_window_start: "02:00"
  maintenance_window_duration_minutes: 90
  compaction:
    medium_horizon_drop_raw_tool_output: true
    long_horizon_drop_raw_tool_output: true
    store_artifacts_on_disk: true
  mcp:
    enabled: true
    default_max_calls: 5
    default_max_duration_seconds: 300
```

Optionally extend `config/endocrine.yaml` with task-related increments:

- `task_retry_cortisol_increment`
- `research_success_dopamine_increment`
- `deadline_adrenaline_increment`

## 12. WUI and API Surface

The task system must be operator-visible from the start.

### 12.1 Back-End Routes

Add endpoints to `src/openbad/wui/server.py`.

- `GET /api/tasks`
- `POST /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/pause`
- `POST /api/tasks/{task_id}/resume`
- `POST /api/tasks/{task_id}/cancel`
- `GET /api/tasks/{task_id}/events`
- `GET /api/research`
- `GET /api/research/{research_id}`
- `GET /api/capabilities`
- `GET /api/mcp/audit`
- `GET /api/scheduler/state`

### 12.2 Front-End Views

Add WUI views for:

- Task list
- Task detail and node graph
- Research queue
- Capability inventory
- Scheduler state
- Reward evaluation traces
- MCP audit records

The UI does not need to be visually complete in the first pass, but it must expose enough state for debugging and operator trust.

## 13. Topic and Schema Extensions

If cross-process transport is required for task and research events, add new protobuf schemas under `src/openbad/nervous_system/schemas/`.

Potential files:

- `task.proto`
- `research.proto`
- `scheduler.proto`
- `capability.proto`

Potential messages:

- `TaskCreated`
- `TaskUpdated`
- `TaskNodeUpdated`
- `TaskRunStarted`
- `TaskRunFinished`
- `ResearchQueued`
- `ResearchResolved`
- `SchedulerWake`
- `CapabilityExecuted`
- `MCPAuditRecord`

Do not add protobuf messages unnecessarily if SQLite plus HTTP is sufficient for the first implementation.

## 14. Integration Sequence

Implement Phase 9 in this order.

### Step 1. Persistence and Basic Task CRUD

Build SQLite state, migrations, task models, and task creation APIs.

Acceptance criteria:

- Tasks persist across restart.
- Tasks can be created and queried.
- Task events are stored durably.

### Step 2. Scheduler and Lease Model

Build the heartbeat scheduler and lease acquisition.

Acceptance criteria:

- Due tasks are dispatched after restart.
- Duplicate dispatch is prevented by leases.
- Quiet hours and maintenance windows are respected.

### Step 3. DAG Execution

Add the planner and executor with node transitions.

Acceptance criteria:

- Dependent nodes run in order.
- Downstream nodes remain blocked if upstream nodes fail.
- Retry and blocking behavior are deterministic.

### Step 4. Trusted Capabilities

Add the capability manifest, registry, and core triage capability pack.

Acceptance criteria:

- Manifests validate correctly.
- Capability inventory is exposed to operators.
- System 1 only sees the restricted capability set.

### Step 5. MCP Isolation

Add the MCP bridge and task-scoped sessions.

Acceptance criteria:

- Heartbeat and reflex paths have no MCP access.
- MCP sessions are task-scoped and audited.
- Tool access is denied unless declared by policy.

### Step 6. Research Queue and Reward Programs

Add blocked-task research escalation and reward evaluation.

Acceptance criteria:

- Blocked nodes can enqueue research work.
- Reward traces are stored and inspectable.
- Research findings can feed back into task execution.

### Step 7. Endocrine and WUI Coupling

Connect task signals into endocrine behavior and expose everything through the WUI.

Acceptance criteria:

- Cortisol suppresses exploratory work under stress.
- Adrenaline prioritizes urgent work.
- Operator can inspect tasks, research, reward traces, and scheduler state.

## 15. Testing Requirements

Add at least the following tests.

- `tests/test_task_store.py`
- `tests/test_task_scheduler.py`
- `tests/test_task_executor.py`
- `tests/test_task_planner.py`
- `tests/test_capability_registry.py`
- `tests/test_mcp_bridge.py`
- `tests/test_reward_programs.py`
- `tests/test_research_queue.py`
- `tests/test_task_api.py`

Minimum behavioral coverage:

- SQLite migration correctness
- Lease contention and expiry
- Duplicate heartbeat suppression
- DAG ordering and dependency blocking
- Retry and escalation thresholds
- Compaction of raw tool outputs
- Manifest validation and permission enforcement
- MCP isolation and audit logging
- Reward evaluation correctness
- Endocrine adjustments from task traces

## 16. Coding Agent Guidance

An agent implementing this phase should follow these rules.

1. Reuse OpenBaD's current nervous system, cognitive router, endocrine controller, memory controller, and WUI.
2. Do not redesign working modules to fit a cleaner abstract architecture.
3. Add persistence and orchestration first, then add richer autonomy.
4. Keep System 1 narrow and non-ambient.
5. Treat MCP as a scoped execution privilege, not a globally visible tool inventory.
6. Prefer deterministic first implementations over speculative generality.
7. Make every autonomous step replayable and inspectable.

## 17. Final Definition of Done

Phase 9 is done when OpenBaD can:

1. Persist tasks and task DAGs in SQLite.
2. Resume scheduled and in-progress work after restart.
3. Execute task nodes under leases without duplicate processing.
4. Escalate blocked work into a research queue.
5. Use trusted core capabilities without exposing external tools to heartbeat paths.
6. Use MCP tools only in explicitly scoped task or research sessions.
7. Evaluate task-node reward programs and translate outcomes into endocrine adjustments.
8. Expose task, research, scheduler, capability, and audit state through the WUI.
9. Pass the Phase 9 test suite.

At that point, OpenBaD will have crossed from biologically inspired reactive architecture into persistent, inspectable, and resource-governed autonomous execution.