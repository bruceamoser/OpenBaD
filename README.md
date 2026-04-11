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
- If `mosquitto.service` already exists, the installer reuses it instead of creating a second broker service.
- System installs generate and persist an identity secret in `/etc/openbad/identity.yaml` on first install.
- Use `--skip-services` only for development mode.

Validate setup/config:

```bash
openbad setup --check
```

Control the installed stack with:

```bash
openbad start
openbad stop
openbad restart
openbad update
openbad health
openbad tui
openbad version
```

Notes:

- `openbad start` starts the managed OpenBaD services and returns immediately.
- `openbad stop` stops all managed services.
- `openbad restart` restarts all managed services.
- `openbad update` pulls latest code, re-runs the install script, and restarts services. Requires `sudo`.
- `openbad health` reports systemd service state, MQTT reachability, and the WUI health endpoint.
- `openbad tui` attaches a terminal UI to the running MQTT-backed stack.

## Web UI

Once running, the Web UI is available at **http://localhost:9200**.

The WUI is a SvelteKit single-page application served by the aiohttp backend.
It provides panels for Providers, Senses, Toolbelt, Entity, Chat, and Health,
plus a first-run setup wizard.

### Building the WUI

If you need to rebuild the SvelteKit frontend (requires Node.js):

```bash
make wui
```

This runs `npm install && npm run build` in `wui-svelte/` and copies the
output to `src/openbad/wui/build/` where the aiohttp server serves it.

### Development Mode (no systemd)

For local development without a full system install:

```bash
# Install in editable mode
pip install -e ".[dev]"

# Set config dir to the repo's config/ (avoids /etc/openbad permission issues)
export OPENBAD_CONFIG_DIR=./config

# Start the WUI server directly
openbad wui --host 127.0.0.1 --port 9200
```

Or run the SvelteKit dev server with hot-reload (proxies API calls to aiohttp):

```bash
# Terminal 1: start the backend
export OPENBAD_CONFIG_DIR=./config
openbad wui

# Terminal 2: start the SvelteKit dev server
make wui-dev
```

## Running Tests

```bash
pytest                   # unit tests (excludes integration)
pytest --run-all         # all tests including integration
ruff check src/ tests/   # lint
ruff format src/ tests/  # auto-format
```

## License

See [LICENSE](LICENSE).
