# OpenBaD

**Agentic Assistant. Biological as Digital.**

A biologically inspired cognitive architecture for autonomous AI agents — separating fast instincts from slow reasoning, stratifying memory into temporal hierarchies, and introducing intrinsic metabolic and endocrine drives.

---

## Documentation

| Document | Description |
| :--- | :--- |
| [overview-spec.md](overview-spec.md) | Master conceptual specification and architectural rationale |

### Implementation Phases

| Phase | Document | Focus |
| :--- | :--- | :--- |
| 1 | [01-environment-nervous-system.md](01-environment-nervous-system.md) | Event bus, eBPF interoception, FSM reflex arc, proprioception |
| 2 | [02-sensory-integration.md](02-sensory-integration.md) | Vision (Wayland/PipeWire), audio (Vosk/Whisper), TTS |
| 3 | [03-cognitive-engine-immune-system.md](03-cognitive-engine-immune-system.md) | Immune system (Amygdala), identity verification, LLM/SLM reasoning |
| 4 | [04-memory-hierarchies-sleep.md](04-memory-hierarchies-sleep.md) | STM/LTM hierarchy, sleep consolidation (SWS/REM/pruning) |
| 5 | [05-active-inference-endocrine.md](05-active-inference-endocrine.md) | Active Inference curiosity engine, digital endocrine system |

### Phase Dependencies

```
Phase 1 (Nervous System, Interoception, Reflexes)
  ├── Phase 2 (Sensory Integration) ─── requires event bus
  ├── Phase 3 (Cognitive Engine) ────── requires event bus + reflexes
  │     └── Phase 4 (Memory) ────────── requires cognitive traces
  └── Phase 5 (Endocrine) ──────────── modulates all phases
```

## Install and Setup (Linux/WSL)

Use the installer as root:

```bash
sudo ./scripts/install.sh --bootstrap --configure-wsl-systemd
```

Notes:

- `--bootstrap` installs Linux prerequisites (Ubuntu/Debian apt path) and broker deps.
- Full install requires `systemd` (Linux + WSL).
- In WSL, `--configure-wsl-systemd` writes `/etc/wsl.conf` and prompts for WSL restart.
- Use `--skip-services` only for development mode.

Validate setup/config:

```bash
openbad setup --check
```
