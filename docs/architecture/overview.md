# System Overview

OpenBaD (**Open Biologically-analogous Daemon**) is an autonomous agent
framework that maps biological subsystems onto software components.  It
runs as a set of systemd services on Linux, communicates internally via
MQTT, persists state in SQLite, and exposes a web UI for monitoring and
interaction.

## Design Philosophy

| Biological Analogy | Software Component |
|--------------------|--------------------|
| Nervous system | MQTT message bus (`nervous_system/`) |
| Brain / cognition | LLM providers + reasoning engine (`cognitive/`) |
| Endocrine system | Hormone-modulated behaviour (`endocrine/`, `autonomy/endocrine_runtime.py`) |
| Immune system | Threat detection and quarantine (`immune_system/`) |
| Reflex arc | Fast reactive FSM (`reflex_arc/`) |
| Memory hierarchies | STM, episodic, semantic, procedural (`memory/`) |
| Sleep cycle | Memory consolidation and pruning (`memory/sleep/`) |
| Proprioception | Tool readiness and belt inventory (`proprioception/`) |
| Interoception | Hardware telemetry — CPU, memory, disk, network (`interoception/`) |
| Senses | Audio capture / ASR, vision (`sensory/`) |
| Active inference | Novelty-seeking exploration (`active_inference/`) |
| Identity | Personality and user profiling (`identity/`) |

## Runtime Components

```
┌─────────────────────────────────────────────────────────────┐
│                      systemd services                       │
│  openbad.service          Main daemon (MQTT, FSM, autonomy) │
│  openbad-wui.service      Web UI (aiohttp, port 9200)       │
│  openbad-broker.service   Mosquitto MQTT broker (:1883)     │
│  openbad-heartbeat.timer  Heartbeat trigger (every 60s)     │
│  openbad-heartbeat.service Heartbeat dispatcher             │
│  openbad-corsair.service  Peripheral interface              │
│  openbad-searxng.service  Local metasearch (optional)       │
└─────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────┐
│              External services             │
│  llama-server (:8085)  — LLM inference     │
│  nomic-embed  (:8081)  — Text embeddings   │
│  Mosquitto    (:1883)  — MQTT broker       │
└────────────────────────────────────────────┘
```

## Technology Stack

- **Language:** Python ≥ 3.11
- **HTTP server:** aiohttp
- **Agent frameworks:** LangChain, CrewAI, LangGraph
- **Database:** SQLite + sqlite-vec (vector search)
- **Messaging:** MQTT via paho-mqtt
- **Serialisation:** Protobuf (nervous system schemas)
- **Frontend:** SvelteKit 5 with Svelte 5 runes, adapter-static
- **Process management:** systemd, cgroup v2
- **LLM providers:** OpenAI, Anthropic, Ollama, GitHub Copilot, Groq, Mistral, xAI, OpenRouter, custom OpenAI-compatible
