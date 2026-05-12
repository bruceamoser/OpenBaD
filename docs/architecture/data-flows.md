# Data Flows

Key workflows showing how data moves through the system.

## Heartbeat Cycle

The main event loop — runs every 60 seconds via systemd timer.

```
openbad-heartbeat.timer (systemd)
        │
        ▼
openbad-heartbeat.service
        │
        ▼
cli.py heartbeat dispatch
        │
        ├─ 1. Triage ─────── Check pending tasks, research, doctor requests
        │
        ├─ 2. Dispatch ────── Acquire leases, route to workers
        │   ├─ task_worker ──── Execute due tasks
        │   ├─ research_worker ── Run pending research nodes
        │   └─ doctor ────────── Process doctor requests
        │
        ├─ 3. Consolidation ─ Memory sleep/recall if due
        │
        └─ 4. Maintenance ─── Autonomic scanning, library persistence
                               Publish telemetry to MQTT
```

**Telemetry published:** `agent/telemetry/*` topics with CPU, memory,
disk, token usage.

## Chat Pipeline

User message → AI response via the WUI.

```
Browser (SvelteKit)
    │  POST /api/chat/stream
    ▼
chat_pipeline.stream_chat()
    │
    ├─ 1. Immune scan ───── rules_engine checks threat level
    │
    ├─ 2. _needs_tools() ── Regex heuristic: simple vs agentic?
    │       │
    │       ├─ Simple ──────── _direct_stream()
    │       │                  ChatOpenAI with enable_thinking=False
    │       │                  No tools, fast path
    │       │
    │       └─ Complex ─────── _agentic_stream()
    │                          LangGraph supervisor agent
    │                          Routes to sub-agents:
    │                            library_agent, web_agent,
    │                            task_agent, memory_agent, etc.
    │
    ├─ 3. Context assembly ── STM + episodic + semantic memory
    │                         Identity/personality injection
    │                         Token budget compression
    │
    ├─ 4. Stream ──────────── SSE chunks to frontend
    │
    └─ 5. Post-completion ── Write turn to STM + episodic
                              Track tokens and latency
```

## Research Pipeline

Research query → consolidated findings.

```
Trigger (user request or autonomic scan)
    │
    ▼
research_planner.decompose()
    │  Break query into sub-queries
    ▼
research_queue.enqueue()
    │  Priority-ordered nodes
    ▼
scheduler_worker (heartbeat dispatch)
    │
    ├─ Acquire lease on next research node
    │
    ├─ Execute: web_search, web_fetch, read_file
    │
    ├─ If stuck → research_escalation → doctor
    │
    ├─ Consolidate findings
    │
    └─ _save_research_to_library()
       │  Persist as Library book
       │  under Research > Autonomous Research > Findings
       ▼
    reward_evaluator → endocrine feedback
```

## Task Execution

```
Task created (user or agent)
    │
    ▼
task_store: status=PENDING, due_at=...
    │
    ▼
scheduler_worker (heartbeat)
    │  Acquire lease on due task
    ▼
task_executor.execute()
    │
    ├─ Run tool_agent with task-role tools
    │  (read_file, write_file, exec_command, web_search, ...)
    │
    ├─ Publish progress events
    │
    ├─ On success: mark DONE, publish result
    │
    └─ On failure: retry with backoff or escalate
```

## Doctor System

Autonomous oversight that monitors system health and intervenes.

```
Sources:
    ├─ Failed research (escalation signal)
    ├─ Resource exhaustion (cortisol spike)
    ├─ Model errors (immune quarantine)
    └─ Endocrine activation threshold

        │
        ▼
scheduler_worker.process_doctor_call()
    │
    ├─ Evaluate system state
    │
    ├─ Decisions:
    │   ├─ Gate subsystems (disable chat, throttle research)
    │   ├─ Adjust endocrine levels
    │   ├─ Request re-runs or alternatives
    │   └─ Update threat signatures
    │
    └─ Write doctor notes → endocrine_doctor_notes table
       Publish to MQTT
```

**Cooldown:** 10 minutes per source to prevent feedback loops.

## Immune Response

```
Scan points:
    ├─ Inbound chat messages → rules_engine
    ├─ Tool outputs → anomaly_detector
    └─ Model responses → model_classifier

        │ threat detected
        ▼
Publish to agent/immune/threat
        │
        ▼
quarantine.isolate()
    │  Remove suspect content
    │  Log for human review
    │
    └─ Update threat_signatures (adaptive)
```

## Endocrine Modulation

Hormones modulate system behaviour across all subsystems.

```
Adjustment sources:
    ├─ Telemetry (CPU/memory spikes → cortisol)
    ├─ Reward evaluator (success → dopamine)
    ├─ User feedback (complaints → endorphin drop)
    └─ Doctor notes (manual adjustments)

        │
        ▼
endocrine_runtime.adjust()
    │
    ├─ Update persistent state
    │
    ├─ Publish to agent/endocrine/state
    │
    └─ Threshold effects:
        ├─ cortisol > 0.8 → throttle (quiet hours)
        ├─ adrenaline > threshold → emergency mode
        └─ dopamine < 0.3 → speed up exploration
```

## FSM State Transitions

```
IDLE ──── chat input ────→ ACTIVE
  │                          │
  │                    ┌─────┼──────────────┐
  │                    │     │              │
  │                    ▼     ▼              ▼
  │             RESEARCHING  EXECUTING   DIAGNOSING
  │                    │     TASK          │
  │                    │     │              │
  │                    └─────┼──────────────┘
  │                          │
  │                          ▼
  │                      THROTTLED
  │                          │
  └──────────────────────────┘

  Any state ──── critical ────→ EMERGENCY
  Any state ──── sleep due ───→ SLEEP
```

## Routine Execution

```
routines table (recurrence_rule, next_run_at, body_md)
    │
    ▼
routine_daemon.poll_and_run() (every 30s)
    │
    ├─ Query: enabled=1 AND next_run_at <= now
    │
    ├─ Advance next_run_at (prevent re-fire)
    │
    ├─ Execute body_md via run_tool_agent
    │  (full task-role tool access)
    │
    └─ Record run in routine_runs
       Publish MQTT event on completion
```
