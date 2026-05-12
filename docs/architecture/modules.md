# Module Reference

Every subdirectory under `src/openbad/` and its purpose.

## Autonomy (`autonomy/`)

Autonomous scheduling, event dispatch, hormone regulation, and FSM state
management.

| File | Purpose |
|------|---------|
| `scheduler_worker.py` | Lease-aware task/research/doctor dispatcher with quiet-hours support |
| `endocrine_runtime.py` | Persistent hormone state (dopamine, adrenaline, cortisol, endorphin) with doctor notes |
| `session_policy.py` | User session catalogue and budget policy persistence |
| `tool_agent.py` | LangChain/CrewAI agent orchestration for background jobs |

## Nervous System (`nervous_system/`)

MQTT-based inter-module communication fabric.

| File | Purpose |
|------|---------|
| `client.py` | `NervousSystemClient` — publish/subscribe wrapper over paho-mqtt |
| `topics.py` | 100+ topic constants (telemetry, reflex, endocrine, immune, cognitive, sensory) |
| `qos.py` | QoS level constants |
| `schemas/` | Protobuf message definitions for all topic payloads |

## Memory (`memory/`)

Hierarchical memory with multiple tiers.

| File | Purpose |
|------|---------|
| `stm.py` | Short-term memory — recent conversation turns (≤ 50) |
| `episodic.py` | Event-based memory stored as JSON |
| `semantic.py` | Vector-backed semantic search using embeddings |
| `procedural.py` | Skill/procedure memory |
| `cognitive.py` | Reasoning insight storage |
| `controller.py` | Unified memory orchestration across tiers |
| `sleep/` | Consolidation scheduling, recall, and pruning during sleep cycles |

## State & Database (`state/`)

SQLite-backed persistent state.

| File | Purpose |
|------|---------|
| `db.py` | Database initialisation, connection management, migration runner |
| `event_log.py` | Event audit trail |
| `migrations/` | Numbered SQL migration files (0001–0008+) |

## Tasks & Research (`tasks/`)

Task scheduling, research planning, and reward evaluation.

| File | Purpose |
|------|---------|
| `store.py` | Task CRUD |
| `service.py` | High-level task service API |
| `scheduler.py` | Poll-based dispatch with lease acquisition |
| `executor.py` | Task execution with error handling |
| `heartbeat.py` | Heartbeat cycle state tracking |
| `research_service.py` | Research queue singleton |
| `research_queue.py` | Priority queue for research nodes |
| `research_planner.py` | Decompose queries into sub-queries |
| `research_findings.py` | Finding consolidation |
| `research_escalation.py` | Escalation to doctor when stuck |
| `reward_evaluator.py` | Endocrine feedback from outcomes |
| `artifacts.py` | Output artifact persistence |

## Skills (`skills/`)

Native MCP server with embedded tools.

| File | Purpose |
|------|---------|
| `server.py` | FastMCP-based skill server — registers all tools |
| `library_tool.py` | `browse_library`, `search_library`, `read_book`, `draft_book`, `link_books` |
| `memory_tool.py` | `read_memory`, `write_memory`, `prune_memory`, `query_semantic` |
| `web_search.py` | `web_search`, `web_fetch` |
| `fs_tool.py` | `read_file`, `write_file`, `find_files` |
| `cli_tool.py` | `exec_command` |
| `ask_user.py` | `ask_user` — request human input |
| `doctor_tool.py` | `call_doctor` |
| `access_control.py` | Permission-gated tool wrappers |

## Cognitive Engine (`cognitive/`)

LLM/SLM reasoning with multi-provider support.

| File | Purpose |
|------|---------|
| `config.py` | Provider configuration loading |
| `context_manager.py` | Context window management with token budgeting |
| `model_router.py` | Route requests to the assigned provider/model per system (chat, reasoning, reactions, sleep) |
| `orchestrator.py` | Chain-of-thought orchestration |
| `providers/` | Adapters: `anthropic.py`, `ollama.py`, `github_copilot.py`, `litellm_adapter.py` |
| `reasoning/` | System 2 deep reasoning module |

## Immune System (`immune_system/`)

Threat detection, quarantine, and adaptive memory.

| File | Purpose |
|------|---------|
| `rules_engine.py` | Pattern-based threat matching |
| `anomaly_detector.py` | Statistical anomaly detection |
| `quarantine.py` | Isolation of suspect messages/tools |
| `threat_signatures.py` | Known threat patterns |
| `interceptor.py` | Chat pipeline integration point |
| `monitor.py` | Real-time threat monitoring |
| `model_classifier.py` | ML-based threat classification |

## Reflex Arc (`reflex_arc/`)

Fast, low-latency reactive decision-making.

| File | Purpose |
|------|---------|
| `fsm.py` | Finite state machine: IDLE → ACTIVE → RESEARCHING / EXECUTING_TASK / DIAGNOSING → THROTTLED / SLEEP / EMERGENCY |
| `chat_activator.py` | Activate agent on chat input |
| `escalation.py` | Escalate to System 2 reasoning |
| `handlers/` | Per-state FSM handlers |

## Active Inference (`active_inference/`)

Prediction-driven exploration for novelty-seeking behaviour.

| File | Purpose |
|------|---------|
| `background_scanner.py` | Periodic observation with endocrine modulation |
| `engine.py` | Active inference loop |
| `exploration_actions.py` | Action generation from model uncertainty |
| `world_model.py` | Predictive model of environment |
| `surprise.py` | Surprise / divergence metric |
| `insight_queue.py` | Proactive insight generation |
| `budget.py` | Exploration budget management |

## Endocrine System (`endocrine/`)

Hormonal regulation of system behaviour.

| File | Purpose |
|------|---------|
| `controller.py` | Apply hormone adjustments from sources |
| `config.py` | Baseline hormone configuration |
| `hooks/` | Integration points for endocrine events |
| `telemetry.py` | Publish hormone state to MQTT |
| `l2hr.py` | Liaison-to-human readiness signals |

## Interoception (`interoception/`)

Internal state monitoring (hardware telemetry).

| File | Purpose |
|------|---------|
| `monitor.py` | Unified telemetry aggregator |
| `cgroup.py` | Linux cgroup v2 resource limits |
| `ebpf_probes.py` | eBPF kernel probes |
| `disk_network.py` | Disk I/O and network telemetry |
| `dashboard.py` | Real-time health dashboard |
| `token_budget.py` | Token consumption tracking |

## Proprioception (`proprioception/`)

Belt inventory and tool readiness.

| File | Purpose |
|------|---------|
| `registry.py` | Tool role registry with permission gating |
| `heartbeat_state.py` | Tool health state persistence |
| `readiness.py` | Readiness gate — wait until belt is equipped |

## Sensory (`sensory/`)

Audio and vision perception.

| File | Purpose |
|------|---------|
| `audio/` | Audio capture, ASR (Whisper, Vosk), TTS |
| `vision/` | Camera capture and image analysis |
| `config.py` | Sensory config loading |
| `dispatcher.py` | Route sensor events to handlers |
| `health.py` | Sensor health monitoring |

## Peripherals (`peripherals/`)

External interface bridges.

| File | Purpose |
|------|---------|
| `chat_router.py` | Route messages to sessions/users |
| `telegram_bridge.py` | Telegram bot integration |
| `webhook.py` | HTTP webhook ingress |
| `corsair/` | External interface module |

## Frameworks (`frameworks/`)

LangChain / CrewAI / LangGraph integration.

| File | Purpose |
|------|---------|
| `langchain_tools.py` | `_ROLE_TOOLS` — per-role tool allow-lists; wraps skills as LangChain `StructuredTool` |
| `langchain_model.py` | LangChain model builders |
| `supervisor.py` | Multi-agent supervisor graph |
| `agents/sub_agents.py` | Sub-agent definitions (library, web, task, research, …) |
| `crews/` | Multi-agent crew workflows |
| `workflows/` | LangGraph workflow definitions |
| `langgraph_checkpointer.py` | Crew state persistence |

## Library (`library/`)

Hierarchical knowledge store with vector search.

| File | Purpose |
|------|---------|
| `store.py` | CRUD for libraries → shelves → sections → books; vector chunk search |
| `embedder.py` | Text chunking (500 tokens, 50 overlap) |

## Routines (`routines/`)

Scheduled recurring actions.

| File | Purpose |
|------|---------|
| `store.py` | Routine CRUD and run history |
| `daemon.py` | Background poller — fires due routines via tool agent |

## Identity (`identity/`)

User and assistant profiling.

| File | Purpose |
|------|---------|
| `assistant_profile.py` | Personality traits |
| `user_profile.py` | User preferences and learning |
| `personality_modulator.py` | Tone/style injection into responses |
| `permissions.py` | Fine-grained capability permissions |
| `onboarding.py` | Initial setup interview |
| `evolution.py` | Adaptive learning from interactions |

## Plugins (`plugins/`)

User-extensible capability system.

| File | Purpose |
|------|---------|
| `registry.py` | Capability registry with permission enforcement |
| `manifest.py` | Capability manifest parsing |
| `observations/` | External signal plugins |
| `mcp_guard.py`, `mcp_audit.py`, `mcp_policy.py` | MCP server governance |

## Web UI (`wui/`)

HTTP API + WebSocket bridge + SPA serving.

| File | Purpose |
|------|---------|
| `server.py` | Main aiohttp application — route registration, startup hooks |
| `chat_pipeline.py` | Chat processing: immune scan → context assembly → streaming |
| `bridge.py` | MQTT ↔ WebSocket bridge |
| `*_api.py` | Route handlers: `task_api`, `research_api`, `memory_api`, `library_api`, `routine_api`, `services_api`, `transducer_api` |
| `build/` | Compiled SvelteKit SPA (served as static files) |

## CLI & Daemon (root level)

| File | Purpose |
|------|---------|
| `cli.py` | Command-line interface — heartbeat dispatch, service control |
| `daemon.py` | System startup and event loop bootstrap |
| `updater.py` | Version updates |
| `usage_recorder.py` | Usage tracking adapter |
| `tui/` | Terminal UI for local debugging |
