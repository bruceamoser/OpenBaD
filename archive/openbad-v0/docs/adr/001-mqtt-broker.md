# ADR-001: MQTT Broker Selection

## Status

**Accepted** — 2026-04-09

## Context

OpenBaD requires a central event bus for all inter-module communication (telemetry, reflex triggers, cognitive escalation, endocrine signals). The broker must:

- Handle >50k msgs/sec on commodity hardware
- Support MQTT v5 features: shared subscriptions, message expiry, request-response patterns
- Have a permissive open-source license (no BSL, AGPL, GPL)
- Run as a systemd service with watchdog support
- Be lightweight enough to run alongside the agent on the same host

## Candidates

### NanoMQ (MIT License)

- **License:** MIT — fully permissive, no restrictions
- **MQTT Support:** Full MQTT v3.1.1 and v5.0 compliance
- **Architecture:** Built-in actor model with NNG's asynchronous I/O; multi-threaded with native SMP support
- **Performance:** Claims million-level TPS; up to 10x faster than Mosquitto on multi-core CPUs (per vendor benchmarks)
- **Footprint:** <200KB minimum boot footprint
- **Extras:** Built-in rule engine, message persistence (SQLite), MQTT bridging, WebSocket support, TLS/SSL, HTTP API
- **Ecosystem:** Part of EMQ/LF Edge; 2.5k GitHub stars; 48 contributors; 138 releases
- **Language:** Pure C (C99), highly portable across POSIX platforms

### Mosquitto (EPL 2.0 / EDL 1.0)

- **License:** Eclipse Public License 2.0 / Eclipse Distribution License 1.0 — permissive but dual-licensed
- **MQTT Support:** Full MQTT v3.1.1 and v5.0
- **Architecture:** Single-threaded event loop
- **Performance:** Well-established but single-threaded; bottlenecks on multi-core under high message rates
- **Footprint:** Small but larger than NanoMQ
- **Extras:** Mature plugin ecosystem, widely deployed, extensive documentation
- **Ecosystem:** Eclipse Foundation project; de facto standard MQTT broker

## Decision

**NanoMQ** is selected as the OpenBaD event bus broker.

## Rationale

1. **License:** MIT is unambiguously permissive. Mosquitto's EPL 2.0 is permissive but adds complexity for downstream distribution. Our project convention requires MIT/Apache 2.0/BSD.

2. **Performance:** OpenBaD's nervous system processes high-frequency telemetry (CPU, memory, disk, network at 1-5s intervals) alongside burst traffic from reflex triggers and cognitive escalations. NanoMQ's multi-threaded actor model scales linearly with cores, while Mosquitto's single-threaded loop would become a bottleneck under sustained load.

3. **MQTT v5.0:** Both support v5.0, but NanoMQ's native v5 implementation includes shared subscriptions and request-response patterns needed for the escalation gateway (Issue #19).

4. **Footprint:** NanoMQ's <200KB footprint makes it ideal for running as a sidecar alongside the agent process, especially under cgroup resource constraints.

5. **Extensibility:** NanoMQ's built-in rule engine and SQLite persistence provide a path for message persistence without external dependencies, important for the memory consolidation pipeline (Phase 4).

## Consequences

- Python client library will use `paho-mqtt` (BSD) or `gmqtt` (MIT) to connect to NanoMQ
- NanoMQ is distributed as a Docker image or compiled from source; we provide a systemd unit for deployment
- Future auth/ACL will use NanoMQ's HTTP auth plugin (Phase 3)
- If NanoMQ proves insufficient at scale, Mosquitto is a drop-in replacement at the MQTT protocol level
