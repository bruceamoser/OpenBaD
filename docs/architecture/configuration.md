# Configuration

All configuration files live in `config/`.

## Application Config

| File | Purpose |
|------|---------|
| `cognitive.yaml` | Provider list and system assignments (which LLM for chat / reasoning / reactions / sleep) |
| `identity.yaml` | User and assistant profiles, personality traits, preferences |
| `senses.yaml` | Audio/vision configuration — microphone source, camera, ASR engine |
| `memory.yaml` | Memory tier config — STM size, semantic search top-k, sleep window |
| `active_inference.yaml` | Exploration budget — base polling interval, novelty threshold |
| `endocrine.yaml` | Baseline hormone levels |
| `fsm.yaml` | FSM state config — quiet hours, throttle thresholds |
| `immune.yaml` | Threat rules and thresholds — suspicion scoring, quarantine TTL |
| `immune_rules.yaml` | Immune rule definitions |
| `peripherals.yaml` | Telegram, webhook, external interface config |
| `permissions.yaml` | Capability allowlist — which skills the user can access |
| `threshold_policies.yaml` | Metrics thresholds — CPU, memory, token limits for alerts |
| `model_routing.yaml` | Dynamic model chains — critical / high / medium / low priority |
| `broker.conf` | Mosquitto MQTT broker configuration |

## Systemd Services

All service files are in `config/` and installed to
`/etc/systemd/system/`.

### Core Services

| Unit | Description |
|------|-------------|
| `openbad.service` | Main daemon — MQTT client, FSM, autonomy loop |
| `openbad-wui.service` | Web UI — aiohttp on port 9200 |
| `openbad-broker.service` | Mosquitto MQTT broker on port 1883 |

### Heartbeat

| Unit | Description |
|------|-------------|
| `openbad-heartbeat.timer` | Fires every 60 seconds (configurable) |
| `openbad-heartbeat.service` | Runs heartbeat dispatch (cli.py) |
| `openbad-heartbeat-watch.path` | Watches for heartbeat config changes |
| `openbad-heartbeat-apply.service` | Applies heartbeat config on change |

### Telemetry

| Unit | Description |
|------|-------------|
| `openbad-telemetry-watch.path` | Watches for telemetry config changes |
| `openbad-telemetry-apply.service` | Applies telemetry config on change |

### Peripherals

| Unit | Description |
|------|-------------|
| `openbad-corsair.service` | External interface module |
| `openbad-searxng.service` | Local SearXNG metasearch engine (optional) |

## System User

The `openbad` system user (uid 997) runs all services.  A sudoers rule
at `/etc/sudoers.d/openbad` grants passwordless `systemctl
start/stop/restart` for `openbad*.service`, `.timer`, and `.path` units.

## File Paths

| Path | Contents |
|------|----------|
| `/opt/openbad/venv/` | Production Python virtual environment |
| `/var/lib/openbad/data/state.db` | SQLite state database |
| `/var/lib/openbad/data/memory/` | Persistent memory files |
| `/etc/openbad/` | Installed configuration (symlinked or copied from `config/`) |
