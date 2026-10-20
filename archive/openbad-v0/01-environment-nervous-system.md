# Phase 1: Environment, Nervous System, and Proprioception (The Subconscious)

> **Parent Document:** [overview-spec.md](overview-spec.md)

---

## Conceptual Foundation

### The Dual-Process Cognitive Core — System 1 (Instinct Layer)

The foundational error in contemporary agent design is the assumption that the LLM should act as the sole cognitive engine for all inputs and outputs. In biological systems, cognition is governed by Dual Process Theory, a psychological and neuroscientific framework that distinguishes between two highly distinct modes of thought: System 1 and System 2. System 1 is fast, automatic, associative, and operates with minimal energy expenditure, managing instincts and immediate reactions. System 2 is slow, deliberate, rule-based, and analytically rigorous, managing complex logic and planning.

To rectify this architectural bottleneck, a biologically inspired system must physically and logically decouple these processes into distinct sub-systems. System 1 in a digital agent must be implemented as a lightweight, event-driven reflex arc that operates entirely independently of the heavy reasoning model. This is optimally achieved using an event bus architecture combined with Publish/Subscribe (Pub/Sub) messaging patterns. A rule-based event bus allows the agent to process background telemetry without invoking the primary LLM. Finite-state machines (FSMs) and deterministic code-as-policy modules act as the "spinal cord," reacting instantaneously to high-priority interrupts based on rigid heuristics.

| Cognitive Layer | Biological Analogue | Digital Implementation | Operational Latency | Primary Function |
| :---- | :---- | :---- | :---- | :---- |
| **Instincts** | Spinal Cord, Brainstem | Event Bus, Hooks, Finite State Machines | Sub-millisecond | Rigid rule enforcement, immediate threat blocking, routing |
| **Reactions** | Amygdala, Basal Ganglia | Small Language Models (SLMs), Heuristics | Milliseconds | Fast categorization, semantic filtering, routine task execution |
| **Reasoning** | Prefrontal Cortex | Large Language Models (LLMs), Search Trees | Seconds to Minutes | Complex planning, ambiguity resolution, strategic adaptation |

> **See also:** System 2 reasoning is implemented in [Phase 3 — Cognitive Engine](03-cognitive-engine-immune-system.md#task-33-route-system-2-reasoning-the-prefrontal-cortex). The SLM-based reactive layer bridges Phase 1 instincts and Phase 3 reasoning.

### Proprioception — Awareness of Digital Limbs

**Proprioception** is the biological awareness of the position and movement of the body's limbs in physical space. For an AI agent, its tools, scripts, and connected MCP servers function as its limbs. A major flaw in existing architectures is that agents blindly attempt to execute commands without verifying if the requested tool is in the correct state to receive the input, leading to erratic behavior and workflow failures.

An agentic proprioception module continuously monitors the readiness, permission scopes, and execution status of every attached tool. It employs real-time state monitors and semantic verifiers to confirm that a requested action is logically feasible within the current system topology before execution. If the reasoning brain attempts to write data to a read-only database, the proprioceptive loop triggers an immediate reflex arc to block the action, notifying the brain of the physical constraint and bypassing a costly, post-failure LLM hallucination loop.

### Interoception & Homeostasis — Internal System Awareness

**Interoception and Homeostasis** involve monitoring the internal physiological condition of the organism to maintain balance. In an AI agent, this translates to the continuous, low-latency monitoring of the underlying hardware and infrastructure. A dedicated daemon must track CPU utilization, GPU VRAM allocation, disk I/O latency, network bandwidth, and the remaining financial token budget. This data feeds directly into the agent's digital endocrine system to trigger cortisol responses.

> **See also:** The endocrine hooks that consume interoceptive telemetry are defined in [Phase 5 — Active Inference & Digital Endocrine Regulation](05-active-inference-endocrine.md#task-52-map-endocrine-hooks-to-system-directives).

---

## Technical Implementation

### Task 1.1: Establish the Central "Nervous System" (Message Broker)

Deploy a robust, protocol-flexible event bus to decouple all agent capabilities into isolated, message-driven modules.

**Objective:** Replace direct API-chaining between components with asynchronous Pub/Sub event routing. Every internal telemetry signal, tool call, sensory input, and inter-module communication passes through this broker.

#### Sub-tasks

- [ ] **1.1.1 — Select and deploy the MQTT broker**
  - Evaluate **NanoMQ** (MIT License) vs. **Mosquitto** (EPL/EDL License) based on:
    - Throughput benchmarks under sustained message load (target: >50k msgs/sec on commodity hardware)
    - Support for MQTT v5 features (shared subscriptions, message expiry, request-response patterns)
    - Plugin/extension ecosystem for authentication and access control
  - Avoid brokers with restrictive BSL licenses.
  - Deploy as a `systemd` service with automatic restart and watchdog integration.

- [ ] **1.1.2 — Define the topic namespace and message schema**
  - Establish a hierarchical topic taxonomy:
    ```
    agent/telemetry/cpu
    agent/telemetry/memory
    agent/telemetry/disk
    agent/telemetry/tokens
    agent/reflex/{reflex-id}/trigger
    agent/reflex/{reflex-id}/result
    agent/sensory/vision/{source-id}
    agent/sensory/audio/{source-id}
    agent/cognitive/escalation
    agent/cognitive/result
    agent/immune/alert
    agent/immune/quarantine
    agent/endocrine/{hormone}
    agent/memory/stm/write
    agent/memory/ltm/consolidate
    agent/sleep/{phase}
    ```
  - Define message payloads using **Protocol Buffers** (protobuf) or **MessagePack** for compact, schema-enforced serialization (avoid plain JSON for high-frequency telemetry).
  - Create a `schemas/` directory to version all message schemas.

- [ ] **1.1.3 — Implement client libraries and connection pooling**
  - Build a thin Python wrapper (`nervous_system/client.py`) around the MQTT client (e.g., `paho-mqtt` or `gmqtt`) providing:
    - Singleton connection management
    - Topic subscription helpers with typed message deserialization
    - Dead-letter topic routing for undeliverable messages
  - Unit test message publish/subscribe round-trips with latency assertions.

- [ ] **1.1.4 — Configure QoS levels and message retention**
  - **QoS 0** (fire-and-forget) for high-frequency telemetry (CPU, memory ticks).
  - **QoS 1** (at-least-once) for reflex triggers, cognitive escalations, and immune alerts.
  - **QoS 2** (exactly-once) for memory consolidation commands and endocrine state transitions.
  - Enable retained messages for current system state topics so late-joining subscribers receive the latest snapshot.

#### Suggested File Structure

```
src/
  nervous_system/
    __init__.py
    broker_config.py       # Broker connection, topic constants
    client.py              # Pub/Sub wrapper with typed messages
    schemas/
      telemetry.proto
      reflex.proto
      cognitive.proto
      immune.proto
      endocrine.proto
      memory.proto
```

#### Acceptance Criteria
- All inter-module communication routes through the broker; no direct function calls between subsystems.
- Message round-trip latency < 5ms on localhost.
- Broker survives crash/restart without message loss for QoS 1+ topics (persistent sessions enabled).

---

### Task 1.2: Implement Interoception via eBPF (Homeostasis)

Utilize kernel-level observability to give the agent continuous awareness of its own resource consumption, feeding into the endocrine regulation system.

**Objective:** Build a low-overhead telemetry daemon that monitors hardware and budget constraints at the per-tool-call granularity, publishing alerts to the nervous system when thresholds are breached.

#### Sub-tasks

- [ ] **1.2.1 — Deploy the eBPF-based resource monitor**
  - Integrate **AgentCgroup** (or build a custom eBPF probe set using **bcc** / **libbpf**, both Apache 2.0) to attach to kernel tracepoints for:
    - CPU scheduler events (per-cgroup CPU time)
    - Memory allocation / OOM pressure signals
    - Block I/O latency histograms
    - Network socket statistics (bytes in/out per process)
  - Ensure the agent process runs within a dedicated cgroup v2 hierarchy for isolated measurement.

- [ ] **1.2.2 — Implement the token budget tracker**
  - Maintain a rolling ledger of API token consumption per model tier (SLM vs. LLM).
  - Track:
    - Daily / hourly token spend vs. configured budget ceiling
    - Per-task token cost (correlated with task IDs from the reflex/cognitive modules)
    - Cost-per-action moving average
  - Persist budget state to disk to survive restarts.

- [ ] **1.2.3 — Define threshold policies and publish cortisol events**
  - Configurable YAML-based threshold definitions:
    ```yaml
    thresholds:
      cpu_percent: { warning: 75, critical: 90 }
      memory_percent: { warning: 80, critical: 95 }
      disk_io_latency_ms: { warning: 50, critical: 200 }
      token_budget_remaining_pct: { warning: 20, critical: 5 }
      gpu_vram_percent: { warning: 85, critical: 95 }
      thermal_celsius: { warning: 80, critical: 95 }
    ```
  - On threshold breach, publish structured events to `agent/endocrine/cortisol` with severity, metric name, current value, and recommended action.

- [ ] **1.2.4 — Build the interoception dashboard (optional, low-priority)**
  - Expose a lightweight HTTP endpoint (e.g., via `aiohttp`) serving a JSON telemetry snapshot.
  - Useful for debugging and operator visibility during development.

#### Suggested File Structure

```
src/
  interoception/
    __init__.py
    ebpf_probes.py          # eBPF attachment and data collection
    token_budget.py          # API spend tracking and ledger
    threshold_policies.yaml  # Configurable alert thresholds
    monitor.py               # Main daemon loop: collect → evaluate → publish
```

#### Acceptance Criteria
- Telemetry publishes to `agent/telemetry/*` topics at configurable intervals (default: 1s for CPU/mem, 5s for disk/network).
- Cortisol events fire within 500ms of a threshold breach.
- eBPF probes add < 1% CPU overhead to the monitored cgroup.

> **See also:** Cortisol events are consumed by the endocrine regulation system in [Phase 5](05-active-inference-endocrine.md#task-52-map-endocrine-hooks-to-system-directives).

---

### Task 1.3: Configure the System 1 Reflex Arc (Instincts)

Build a rule-based engine utilizing Finite State Machines (FSMs) tied directly to the event bus, providing sub-millisecond deterministic responses to system events without LLM involvement.

**Objective:** Implement the "spinal cord" — a set of deterministic, code-as-policy handlers that subscribe to event bus topics and execute rigid, predefined responses. These bypass the LLM entirely.

#### Sub-tasks

- [ ] **1.3.1 — Implement the FSM engine**
  - Use the **transitions** library (MIT License) or a custom lightweight FSM to model agent operational states:
    ```
    States: IDLE, ACTIVE, THROTTLED, SLEEP, EMERGENCY
    ```
  - Transitions triggered by event bus messages:
    - `agent/endocrine/cortisol [critical]` → transition to `THROTTLED`
    - `agent/endocrine/adrenaline` → transition to `EMERGENCY`
    - `agent/endocrine/endorphin` → transition to `SLEEP` (triggers consolidation)
    - `agent/immune/alert [critical]` → transition to `EMERGENCY`
  - On each state transition, publish the new state to `agent/reflex/state` for downstream consumers.

- [ ] **1.3.2 — Build Code-as-Policy reflex handlers**
  - Implement deterministic handlers (pure Python functions, no LLM calls) for:
    - **Thermal throttle reflex:** On cortisol critical → immediately suspend all background curiosity tasks, switch cognitive routing to SLM-only mode.
    - **Budget exhaustion reflex:** On token budget critical → block all new LLM calls, queue pending tasks, emit user notification.
    - **Security lockdown reflex:** On immune critical alert → isolate the flagged data source, revoke its topic publish permissions, log the incident.
    - **Proprioceptive block reflex:** On tool state mismatch (e.g., target is read-only) → cancel the pending action, publish error context back to the cognitive module.
  - Each handler is a self-contained module that subscribes to specific topics and publishes results.

- [ ] **1.3.3 — Implement the proprioception registry**
  - Maintain a live registry of all connected tools, MCP servers, and external services:
    ```python
    {
        "tool_id": "filesystem_write",
        "status": "ready",          # ready | busy | error | unavailable
        "permissions": ["read", "write"],
        "last_heartbeat": "2026-04-09T12:00:00Z",
        "current_operation": null
    }
    ```
  - Tools publish periodic heartbeats to `agent/proprioception/{tool_id}/heartbeat`.
  - Before any tool invocation, the reflex arc consults the registry to verify the tool is in a valid state. If not, the invocation is blocked and the cognitive module is notified.

- [ ] **1.3.4 — Implement the escalation gateway**
  - When a reflex handler encounters an event outside its heuristic boundaries (ambiguous, novel, or multi-step), it publishes to `agent/cognitive/escalation` with:
    - The original event payload
    - The reason for escalation
    - Suggested priority level (low / medium / high / critical)
  - This is the sole interface between System 1 and System 2.

#### Suggested File Structure

```
src/
  reflex_arc/
    __init__.py
    fsm.py                  # Core state machine definition and transitions
    handlers/
      __init__.py
      thermal.py            # Thermal throttle reflex
      budget.py             # Token budget exhaustion reflex
      security.py           # Security lockdown reflex
      proprioceptive.py     # Tool state verification reflex
    proprioception/
      __init__.py
      registry.py           # Live tool/MCP server state registry
      heartbeat.py          # Heartbeat publisher/subscriber
    escalation.py           # System 1 → System 2 escalation gateway
```

#### Acceptance Criteria
- Reflex handlers execute in < 1ms from event receipt to response publication.
- The FSM state machine processes transitions atomically with no race conditions under concurrent events.
- Proprioception registry detects a tool going offline within 2 heartbeat intervals (configurable, default: 10s).
- Escalation gateway correctly routes ambiguous events to the cognitive module without data loss.

> **See also:** The System 2 cognitive module that receives escalations is defined in [Phase 3](03-cognitive-engine-immune-system.md#task-33-route-system-2-reasoning-the-prefrontal-cortex).

---

## Phase 1 Deliverables Summary

| Deliverable | Component | Key Technology |
| :--- | :--- | :--- |
| Central event bus (Pub/Sub) | Nervous System | NanoMQ or Mosquitto (MQTT v5) |
| eBPF telemetry daemon | Interoception | bcc / libbpf, AgentCgroup |
| Token budget tracker | Interoception | Custom Python + persistent ledger |
| FSM state machine | Reflex Arc | `transitions` library |
| Code-as-Policy handlers | Reflex Arc | Pure Python deterministic modules |
| Tool/MCP state registry | Proprioception | Heartbeat protocol over MQTT |
| Escalation gateway | System 1 → System 2 bridge | Topic-based routing |

---

## Dependencies

- **Upstream:** Linux host with cgroup v2, kernel ≥ 5.8 (eBPF support), Python ≥ 3.11
- **Downstream:** Phase 3 (Cognitive Engine consumes escalations), Phase 5 (Endocrine system consumes cortisol events)
