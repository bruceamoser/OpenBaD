# MQTT Topic Namespace

All topics are defined in `src/openbad/nervous_system/topics.py`.

## Telemetry

| Topic | Payload | Description |
|-------|---------|-------------|
| `agent/telemetry/cpu` | `{ usage_percent }` | CPU usage percentage |
| `agent/telemetry/memory` | `{ usage_percent }` | Memory usage percentage |
| `agent/telemetry/disk` | `{ usage_percent }` | Disk usage percentage |
| `agent/telemetry/network` | `{ bytes_sent, bytes_recv }` | Network I/O counters |
| `agent/telemetry/tokens` | `{ system, tokens, ... }` | Token consumption per system |
| `agent/telemetry/sensory_health` | `{ ... }` | Audio/vision sensor status |
| `agent/telemetry/toolbelt` | `{ ... }` | Tool readiness state |
| `agent/telemetry/readiness` | `{ ready }` | Agent startup readiness |

## Reflex Arc

| Topic | Description |
|-------|-------------|
| `agent/reflex/{reflex_id}/trigger` | FSM event trigger |
| `agent/reflex/{reflex_id}/result` | FSM event result |
| `agent/reflex/state` | Current FSM state broadcast |
| `agent/reflex/attention/trigger` | Attention / wake signal |

## Sensory

| Topic | Description |
|-------|-------------|
| `agent/sensory/vision/{source_id}` | Raw vision data |
| `agent/sensory/vision/{source_id}/parsed` | Parsed vision (objects, text) |
| `agent/sensory/audio/{source_id}` | Raw audio |
| `agent/sensory/audio/tts/complete` | TTS generation complete |

## Cognitive

| Topic | Description |
|-------|-------------|
| `agent/cognitive/escalation` | Escalate to System 2 reasoning |
| `agent/cognitive/request` | Reasoning request |
| `agent/cognitive/response` | Reasoning response |
| `agent/cognitive/health` | Model health status |
| `agent/cognitive/context` | Context window state |

## Immune

| Topic | Description |
|-------|-------------|
| `agent/immune/scan` | Scan inbound message |
| `agent/immune/threat` | Threat detected |
| `agent/immune/alert` | Alert sent |
| `agent/immune/quarantine` | Content quarantined |

## Endocrine

| Topic | Description |
|-------|-------------|
| `agent/endocrine/dopamine` | Dopamine adjustment |
| `agent/endocrine/adrenaline` | Adrenaline adjustment |
| `agent/endocrine/cortisol` | Cortisol adjustment |
| `agent/endocrine/endorphin` | Endorphin adjustment |
| `agent/endocrine/state` | Full hormone state snapshot |
| `agent/endocrine/telemetry` | Hormone telemetry |
