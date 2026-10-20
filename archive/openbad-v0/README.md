# OpenBaD v0 — Archived

> **Status:** Archived on 2026-10-19. Not maintained. Kept for historical reference only.

This directory contains the original OpenBaD concept and its Python implementation.

## What v0 was

OpenBaD v0 explored a biologically inspired cognitive architecture for autonomous agents:

- A dual-process cognitive core (fast reflexes vs. deliberate reasoning).
- An MQTT/protobuf "nervous system" and a systemd-managed service topology.
- eBPF-based interoception and system awareness.
- Hierarchical memory with sleep-style consolidation.
- Active inference, endocrine drives, and immune-system-style governance.
- Screen/audio sensory integration and peripheral transducers.
- A Python implementation (~268 modules) with a Svelte web UI and a Textual TUI.

## Why it was archived

The project direction changed. The new OpenBaD is a **local-first desktop coding
harness** built in Rust, with a cache-first model runtime optimized for
DeepSeek-style prefix caching. The refined design keeps the useful cognitive
concepts while dropping the heavy distributed-system machinery that did not
serve a desktop coding workflow.

The new design is documented in [`../../docs/architecture/`](../../docs/architecture/).

## What moved here

| Path | Contents |
| --- | --- |
| `overview-spec.md`, `0*.md`, `1*.md` | Original concept and phase specifications |
| `docs/` | Original architecture docs and ADRs |
| `src/`, `tests/` | Python implementation and test suite |
| `config/`, `scripts/`, `data/`, `quarantine/` | Runtime configuration, installer scripts, state, quarantined code |
| `wui-svelte/` | Original Svelte web UI |
| `pyproject.toml`, `Makefile` | Python build and task definitions |
| `github-workflows/release.yml` | Original Python release workflow |
| `ORIGINAL-README.md` | Original repository README |

## Running the old code

The old code is preserved, not supported. If you need to inspect it:

```bash
cd archive/openbad-v0
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Do not expect this code to run on current systems without changes. Its
dependencies, systemd units, and MQTT assumptions are historical.

## What was retained conceptually

- Dual-process routing (cheap reflex path vs. deliberate agent loop).
- Autonomic heartbeat and scheduled maintenance.
- Hierarchical memory with decay and consolidation.
- Capability governance, audit, and "immune" guardrails.
- Interoception/proprioception as local system health awareness.
- Affective/endocrine modulation as an optional, transparent control surface.

## What was dropped

- MQTT, protobuf, systemd, eBPF, and the multi-service topology.
- The Python/LangGraph implementation.
- The Svelte web UI and Textual TUI.
- Biological simulation that did not produce a concrete product behavior.
