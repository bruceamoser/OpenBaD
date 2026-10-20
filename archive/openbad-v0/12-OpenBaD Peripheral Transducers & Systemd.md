# **Phase 12: Peripheral Transducers — Corsair Integration**

## **Purpose**

Integrate persistent external communications (Discord, Slack, Gmail, GitHub, Telegram, etc.) into OpenBaD without compromising the autonomic heartbeat loop. Instead of building per-platform transducer microservices from scratch, leverage **Corsair** (Apache 2.0) — a TypeScript integration layer that exposes 40+ platform connectors behind a unified MCP interface.

Corsair runs as a **single Node.js sidecar process** managed by systemd. The cognitive daemon talks to it through the existing `mcp_bridge()` tool for egress, while a thin **MQTT bridge** converts Corsair webhook callbacks into nervous-system events for ingress. The WUI provides an operator control surface for enabling plugins, storing credentials, and monitoring health.

## **Architecture**

```
┌───────────────────────────────────────────────┐
│  Corsair MCP Sidecar  (Node.js)               │
│  openbad-corsair.service                       │
│  ┌───────────────────────────────────────────┐ │
│  │ Plugins: slack(), discord(), gmail(), ... │ │
│  │ Auth / rate-limit / API versioning        │ │
│  │ MCP stdio server (4 tools)                │ │
│  │   corsair_setup                           │ │
│  │   list_operations                         │ │
│  │   get_schema                              │ │
│  │   corsair_run                             │ │
│  └───────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────┐ │
│  │ Webhook listener  (HTTP on 127.0.0.1)     │ │
│  │ POST /webhook/{platform} → stdout JSON    │ │
│  └───────────────────────────────────────────┘ │
└────────────┬──────────────────────────────────┘
             │ stdio / HTTP loopback
┌────────────┴──────────────────────────────────┐
│  OpenBaD Daemon  (Python)                      │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │ transmit_message  (Level 1 skill)        │  │
│  │  → mcp_bridge("corsair", "corsair_run",  │  │
│  │     {plugin, operation, params})          │  │
│  └──────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────┐  │
│  │ Webhook → MQTT bridge                    │  │
│  │  aiohttp route: POST /api/webhooks/corsair│ │
│  │  → publish sensory/external/{plat}/inbound│ │
│  └──────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────┐  │
│  │ Active inference ingress handler         │  │
│  │  subscribe sensory/external/+/inbound    │  │
│  │  → STM + surprise calc → optional task   │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

## **Goals**

1. Install and configure Corsair as a systemd sidecar service with declarative plugin config.
2. Build a `transmit_message` Level 1 skill that routes outbound messages through Corsair's MCP interface.
3. Bridge Corsair webhook events into the MQTT nervous system for autonomic ingress.
4. Wire the active-inference engine to react to external inbound signals.
5. Provide a WUI frontend (`/transducers`) for operators to enable plugins, manage credentials, and monitor health.

---

## **Task 1: Corsair Sidecar Bootstrap**

**Objective:** Install Corsair and run it as a managed systemd service alongside the daemon.

- **1.1: Create Corsair project scaffold**
  - Create `src/openbad/peripherals/corsair/` directory.
  - Add `package.json` with `corsair` and `@corsair-dev/mcp` dependencies.
  - Create `corsair.ts` entry point that reads plugin config from `config/peripherals.yaml` and starts the MCP stdio server.
- **1.2: Plugin configuration file**
  - Create `config/peripherals.yaml` with a `corsair.plugins` list (each entry: plugin name, enabled flag, credential reference).
  - Credentials stored separately in `data/config/peripherals/` (file-per-plugin, `0600` permissions).
- **1.3: Systemd unit file**
  - Create `config/openbad-corsair.service` — runs as the `openbad` user, `After=openbad-broker.service`, `Restart=always`.
  - `ExecStart` invokes `node` on the compiled Corsair entry point.
- **1.4: Install script integration**
  - Extend `scripts/install.sh` to `npm install` the Corsair sidecar and install the systemd unit.

## **Task 2: `transmit_message` Egress Skill**

**Objective:** Give the cognitive engine a single, abstract capability to speak to external platforms.

- **2.1: Create `transmit_message` skill**
  - Add `transmit_message(platform, operation, target, content)` to `src/openbad/skills/server.py` as a `@skill_server.tool()`.
  - Implementation: call `mcp_bridge("corsair", "corsair_run", {plugin: platform, operation: operation, params: {target, content}})`.
  - Return the Corsair response (success/error) to the LLM.
- **2.2: Register in capability catalog**
  - Add `transmit_message` to the Level 1 capability registry in `src/openbad/wui/server.py` so it appears in the toolbelt.
  - Assign `ToolRole.COMMUNICATION` in the proprioception registry.

## **Task 3: Webhook Ingress Bridge**

**Objective:** Route inbound Corsair webhook events into the MQTT nervous system.

- **3.1: Add webhook endpoint**
  - In `src/openbad/wui/server.py`, add `POST /api/webhooks/corsair` handler.
  - Validate HMAC signature (Corsair webhook secret from config).
  - Parse platform and event type from the payload.
- **3.2: Publish to MQTT**
  - Publish parsed event to `sensory/external/{platform}/inbound` as a JSON-encoded MQTT message.
  - Add topic constants to `src/openbad/nervous_system/topics.py`:
    - `EXTERNAL_INBOUND = "sensory/external/{platform}/inbound"`
    - `EXTERNAL_OUTBOUND = "motor/external/{platform}/outbound"`
- **3.3: Health topic**
  - On Corsair sidecar startup, publish retained `{"status": "online"}` to `system/peripherals/corsair/health`.
  - On graceful shutdown, publish `{"status": "offline"}`.
  - Add a proprioception health check that monitors this retained message.

## **Task 4: Autonomic Ingress Routing**

**Objective:** Ensure the daemon reacts to incoming external signals correctly.

- **4.1: Subscribe to external inbound**
  - In `src/openbad/daemon.py`, subscribe to `sensory/external/+/inbound`.
  - On message: push to Short-Term Memory via `CognitiveMemoryStore.write()` with tier `stm`.
- **4.2: Surprise calculation**
  - Add `ExternalSignalPlugin` to `src/openbad/active_inference/engine.py` as an `ObservationPlugin`.
  - Default prediction: 0 external messages per interval.
  - When inbound messages arrive, prediction error triggers surprise → potential task generation.

## **Task 5: WUI Frontend (`/transducers`)**

**Objective:** Operator control panel for peripheral integrations.

- **5.1: Backend API endpoints**
  - `GET /api/transducers` — list available plugins with enabled/disabled status.
  - `PUT /api/transducers/{plugin}` — enable/disable a plugin and save credentials.
  - `GET /api/transducers/{plugin}/health` — return Corsair health for this plugin.
  - `POST /api/transducers/{plugin}/test` — send a test message via `corsair_run`.
- **5.2: Create the Transducers route**
  - Create `wui-svelte/src/routes/transducers/+page.svelte`.
  - Grid layout showing available plugins with enable/disable toggles and status indicators.
- **5.3: Configuration modal**
  - Modal for entering API tokens/keys when enabling a plugin.
  - Tokens sent to the backend for secure storage (never stored in the frontend).
- **5.4: Live health indicators**
  - Subscribe to MQTT `system/peripherals/corsair/health` via the existing WUI WebSocket bridge.
  - Green/red status dots per plugin. "View Logs" button streams `journalctl -u openbad-corsair` output.

---

## **Definition of Done**

Phase 12 is complete when:

1. An operator can enable a Corsair plugin (e.g., Discord) in the WUI by entering credentials.
2. The `transmit_message` skill successfully sends a message to an external platform via Corsair.
3. An inbound webhook event from an external platform appears on the MQTT bus at `sensory/external/{platform}/inbound`.
4. The active-inference engine detects the inbound signal and creates an STM entry (with optional task generation on surprise).
5. Restarting the WUI or the core daemon does not crash the Corsair sidecar (independent systemd lifecycle).
6. All new code has unit tests. Integration tests verify the egress skill and webhook bridge end-to-end.