# API Reference

All HTTP endpoints served by the WUI (`openbad-wui.service`).

## Provider & Setup

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/providers` | List configured providers + secret status |
| PUT | `/api/providers` | Save provider list + credentials |
| POST | `/api/providers/verify` | Test provider connectivity |
| POST | `/api/providers/copilot/device-code` | Start GitHub Copilot OAuth flow |
| POST | `/api/providers/copilot/complete` | Poll Copilot auth status |
| GET | `/api/setup-status` | First-run wizard state |
| POST | `/api/setup` | Apply initial wizard settings |

## Cognitive Systems

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/systems` | List system assignments (chat, reasoning, reactions, sleep) |
| PUT | `/api/systems` | Update system assignments |
| GET | `/api/providers/{name}/models` | List models for a provider |

## Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat/stream` | Stream chat response (SSE) |
| GET | `/api/chat/history` | Conversation history (query: `session_id`, `limit`) |

## Tasks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks` | List tasks (query: `status`) |
| POST | `/api/tasks` | Create task |
| GET | `/api/tasks/{task_id}` | Task detail |
| PATCH | `/api/tasks/{task_id}` | Update task metadata |
| POST | `/api/tasks/{task_id}/{action}` | pause / resume / complete / cancel |
| GET | `/api/tasks/{task_id}/events` | Task event log |

## Research

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/research` | List research items |
| POST | `/api/research` | Enqueue new research |
| GET | `/api/research/{id}` | Research detail |
| GET | `/api/research/{id}/findings` | Assembled findings |

## Memory

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/memory` | Memory stats |

## Library

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/library/tree` | Full hierarchy (libraries → shelves → sections → books) |
| GET | `/api/library/book/{book_id}` | Book content + edges |
| POST | `/api/library/book` | Create book (body: `section_id`, `title`, `content`) |
| PUT | `/api/library/book/{book_id}` | Update book content |
| POST | `/api/library/search` | Vector similarity search (body: `query`, `top_k`) |
| POST | `/api/library/link` | Create citation edge (body: `source_id`, `target_id`, `relation_type`) |
| POST | `/api/library/library` | Create a new library |
| POST | `/api/library/shelf` | Create a new shelf |
| POST | `/api/library/section` | Create a new section |

## Routines

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/routines` | List all routines |
| POST | `/api/routines` | Create routine |
| GET | `/api/routines/{id}` | Routine detail |
| PATCH | `/api/routines/{id}` | Update routine |
| DELETE | `/api/routines/{id}` | Delete routine |
| POST | `/api/routines/{id}/toggle` | Enable / disable |
| POST | `/api/routines/{id}/run` | Trigger immediate run |
| GET | `/api/routines/{id}/runs` | Run history |

## Services

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/services` | List OpenBaD systemd units + status |
| POST | `/api/services/{unit}/{action}` | start / stop / restart (allowlisted units only) |

## Endocrine

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/endocrine/status` | Hormone levels, mood tags, subsystem gates, doctor notes |
| GET | `/api/endocrine/activity` | Recent hormone adjustments (query: `limit`) |
| POST | `/api/endocrine/toggle` | Enable/disable subsystem gate |

## Usage & Telemetry

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/usage` | Token/request usage snapshot |
| GET | `/api/usage/requests` | Paginated request history (query: `page`, `per_page`) |
| GET | `/api/usage/requests/{request_id}` | Full request detail |
| GET | `/api/version` | Application version |

## Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/events` | System event log (query: `limit`, `level`) |

## Health & Maintenance

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/maintenance/status` | Maintenance crew status |
| POST | `/api/maintenance/run` | Trigger maintenance crew |

## Sleep & Consolidation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sleep/config` | Memory consolidation schedule |
| PUT | `/api/sleep/config` | Update schedule |
| POST | `/api/sleep/trigger` | Force consolidation now |
| POST | `/api/sleep/wake` | Wake from consolidation |

## Insights

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/insights` | Pending proactive insights (query: `limit`) |
| POST | `/api/insights/dismiss` | Dismiss an insight |

## Senses

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/senses` | Sensory configuration |
| PUT | `/api/senses` | Update sensory configuration |

## Toolbelt

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/toolbelt` | Equipped tools |
| PUT | `/api/toolbelt/{role}` | Equip tool for role |
| DELETE | `/api/toolbelt/{role}` | Unequip role |
| GET | `/api/toolbelt/access` | Pending access requests |
| POST | `/api/toolbelt/access/requests/{id}/approve` | Approve tool access |
| POST | `/api/toolbelt/access/requests/{id}/deny` | Deny tool access |

## WebSocket

| Path | Description |
|------|-------------|
| `/ws` | MQTT ↔ WebSocket bridge for real-time telemetry |
