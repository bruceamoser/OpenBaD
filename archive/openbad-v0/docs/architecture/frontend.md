# Frontend

SvelteKit 5 single-page application using Svelte 5 runes syntax,
built with `adapter-static` and served as static files from the
aiohttp WUI server.

## Build & Deploy

```bash
cd wui-svelte && npm run build
rm -rf src/openbad/wui/build
cp -r wui-svelte/build src/openbad/wui/build
sudo /opt/openbad/venv/bin/pip install --upgrade .
sudo systemctl restart openbad-wui.service
```

The compiled SPA is served from `src/openbad/wui/build/` by the aiohttp
server.  All `/api/*` and `/ws` routes are handled server-side; everything
else falls through to `index.html` for client-side routing.

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Dashboard — overview cards, quick stats |
| `/chat` | Chat interface — streaming SSE, markdown rendering |
| `/tasks` | Task manager — create, schedule, track due dates and recurrence |
| `/research` | Research queue — enqueue queries, view findings |
| `/routines` | Routine scheduler — CRUD, enable/disable, run-now, run history |
| `/memory` | Memory exploration — STM, episodic, semantic, procedural |
| `/library` | Knowledge library — tree browser, book viewer/editor, vector search |
| `/providers` | LLM provider configuration — add/remove/test providers |
| `/services` | System services — start/stop/restart OpenBaD systemd units |
| `/health` | Health dashboard — FSM state, CPU/memory sparklines, hormones, doctor notes, maintenance crew |
| `/endocrine` | Endocrine system — hormone bars, subsystem gates, doctor notes, adjustment log |
| `/immune` | Immune system — threat log, quarantine review |
| `/senses` | Sensory configuration — audio/vision settings |
| `/entity` | Entity profiles — user and assistant identity |
| `/skills` | Available skills/tools — capability browser |
| `/toolbelt` | Belt management — equip/unequip tools, access requests |
| `/usage` | Token usage analytics — request history, cost tracking |
| `/debug` | Debug tools — event logs, MQTT inspector |
| `/mqtt` | Live MQTT topic browser |
| `/transducers` | External interface status |

## Key Libraries

- **SvelteKit 5** with Svelte 5 runes (`$state`, `$derived`, `$effect`)
- **adapter-static** — pre-rendered SPA
- **marked** — Markdown rendering in chat
- **highlight.js** — Code syntax highlighting

## Shared Components

| Component | Path | Purpose |
|-----------|------|---------|
| `Card.svelte` | `$lib/components/` | Reusable panel card |
| `client.ts` | `$lib/api/` | API helper (`get`, `post`, `put`, `patch`, `del`) |
| `stores/` | `$lib/stores/` | WebSocket-backed reactive stores for real-time telemetry |
| `+layout.svelte` | `src/routes/` | Global nav sidebar |

## Real-Time Updates

The frontend connects to `/ws` (WebSocket) which bridges MQTT topics.
Svelte stores subscribe to specific topics and update reactively:

- `$fsmState` — FSM state machine
- `$cpuTelemetry` — CPU usage
- `$memoryTelemetry` — Memory usage
- `$diskTelemetry` — Disk usage
- `$networkTelemetry` — Network I/O
- `$endocrineLevels` — Hormone levels
