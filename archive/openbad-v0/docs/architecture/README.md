# OpenBaD Architecture

This directory contains the architectural reference documentation for
OpenBaD — a biologically-inspired autonomous agent framework.

## Documents

| Document | Description |
|----------|-------------|
| [overview.md](overview.md) | High-level system overview and design philosophy |
| [modules.md](modules.md) | Every `src/openbad/` subsystem and its purpose |
| [data-flows.md](data-flows.md) | Key workflows: heartbeat, chat, research, doctor, immune |
| [api-reference.md](api-reference.md) | All HTTP API endpoints |
| [mqtt-topics.md](mqtt-topics.md) | MQTT topic namespace |
| [database-schema.md](database-schema.md) | SQLite tables and migrations |
| [configuration.md](configuration.md) | Config files and systemd services |
| [frontend.md](frontend.md) | SvelteKit frontend pages and structure |

## Conventions

- Python ≥ 3.11, Linux, systemd, cgroup v2
- MQTT (Mosquitto) as the inter-module communication bus
- SQLite for persistent state (`/var/lib/openbad/state.db`)
- LangChain + CrewAI for agentic workflows
- aiohttp for the HTTP/WebSocket server
- SvelteKit 5 (adapter-static) for the frontend SPA
