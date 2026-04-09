# Phase 5: Active Inference & Digital Endocrine Regulation (Drive)

> **Parent Document:** [overview-spec.md](overview-spec.md)

---

## Conceptual Foundation

### Intrinsic Motivation and Prediction-Driven Exploration

An agent without intrinsic directives is merely an advanced calculator; it lacks the underlying motivation to initiate action, conduct independent research, or improve its own capabilities. To transcend the reactive limitations of current frameworks, the architecture integrates a **prediction-driven exploration system** inspired by Active Inference principles.

> **A note on scope:** The full Free Energy Principle (FEP) and formal Active Inference (AIF) framework as described by Karl Friston require rigorous variational calculus applied to generative models. That's a research program, not a software feature. What we implement here is a **practical approximation**: a prediction→surprise→explore loop that captures the useful behavioral properties of AIF — proactive curiosity, self-directed learning, surprise-driven prioritization — without claiming mathematical equivalence to the full formalism. If and when the formal theory matures into tractable implementations, the architecture is designed to accommodate a drop-in replacement of the prediction engine.

The core loop is straightforward:

1. The agent maintains a **lightweight world model** — expected states for its monitored data sources.
2. When observations diverge from predictions, the agent registers **surprise** (prediction error).
3. High surprise triggers **exploration** — the agent investigates the surprising source to understand and update its model.
4. This naturally produces **proactive behavior**: the agent generates insights and takeaways without being prompted.

**Data source scanning is handled by a plugin/skill architecture**, not hardcoded integrations. The core prediction engine is source-agnostic. Email, calendar, web history, system logs, and future data sources are each implemented as an **observation plugin** that conforms to a standard interface. Plugins are added, removed, or replaced independently — the exploration engine doesn't need to know the specifics of any particular data source.

### The Digital Endocrine System

In biological systems, high-level reasoning and intrinsic motivation are regulated by the endocrine system, which secretes hormones to alter the organism's physical and cognitive state. A truly autonomous digital agent requires a computational equivalent of this biochemical network to modulate learning rates, context parameters, and hardware utilization dynamically.

The system uses **continuous hormone levels (0.0–1.0)** with configurable decay, but all initial defaults are **deliberately conservative**: high activation thresholds, gentle effect multipliers, and fast decay rates. The goal is a system that barely intervenes at first — then we observe telemetry and tighten the values as we understand real workload patterns. Every threshold and multiplier is YAML-configurable so tuning never requires code changes.

| Computational Hormone | Biological Trigger | Digital Trigger Event | System Effect & Modulation |
| :---- | :---- | :---- | :---- |
| **Dopamine** | Reward prediction error, success | Task completion, novel data discovery | Increases learning rate, reinforces procedural memory pathways |
| **Adrenaline** | Acute stress, fight-or-flight | Critical system alerts, security warnings | Expands context window, maximizes compute allocation, suspends background tasks |
| **Cortisol** | Chronic stress, resource scarcity | Thermal throttling, low battery, API budget limits | Suppresses curiosity, shifts to lightweight SLMs, defers non-critical processing |
| **Endorphins** | Pain relief, homeostasis restoration | Resolution of high-stress events | Triggers sleep consolidation cycle, stabilizes neural weights, resets baseline |

> **Tuning philosophy:** All defaults are set so the system is *under*-reactive rather than *over*-reactive. It is always safer to miss a modulation opportunity than to cause unwanted behavior changes. As telemetry accumulates, we lower thresholds and strengthen effects incrementally.

> **See also:** Cortisol events originate from interoception in [Phase 1 — Task 1.2](01-environment-nervous-system.md#task-12-implement-interoception-via-ebpf-homeostasis). The reflex arc responses to endocrine signals are in [Phase 1 — Task 1.3](01-environment-nervous-system.md#task-13-configure-the-system-1-reflex-arc-instincts). Sleep consolidation triggered by endorphins is in [Phase 4 — Task 4.2](04-memory-hierarchies-sleep.md#task-42-implement-the-sleep-consolidation-cycle). Dopamine reinforcement of procedural memory is in [Phase 4 — Task 4.1](04-memory-hierarchies-sleep.md#task-41-construct-short-term-stm-and-long-term-memory-ltm).

---

## Technical Implementation

### Task 5.1: Implement Prediction-Driven Exploration

Build a prediction→surprise→explore loop with a plugin-based observation system for data sources.

**Objective:** Implement a source-agnostic exploration engine that maintains predictions about monitored data sources, detects surprises, and triggers bounded exploration. Data source integrations (email, calendar, etc.) are delivered as observation plugins conforming to a standard interface, added independently over time.

#### Sub-tasks

- [ ] **5.1.1 — Define the Observation Plugin interface**
  - Design a standard plugin contract that all data source integrations must implement:
    ```python
    class ObservationPlugin(ABC):
        """Base class for all data source observation plugins."""

        @property
        @abstractmethod
        def source_id(self) -> str:
            """Unique identifier for this data source (e.g., 'system_logs')."""

        @abstractmethod
        async def observe(self) -> ObservationResult:
            """
            Fetch the current state of this data source.
            Returns a structured observation with key-value metrics.
            """

        @abstractmethod
        def default_predictions(self) -> dict:
            """
            Return initial predictions for this source before any observations.
            Used to bootstrap the world model on first registration.
            """

        @property
        def poll_interval_seconds(self) -> int:
            """How often to poll this source. Default: 60s."""
            return 60
    ```
  - `ObservationResult` is a simple dataclass:
    ```python
    @dataclass
    class ObservationResult:
        metrics: dict[str, float | int | str]   # Key-value observed state
        timestamp: datetime
        raw_data: Any = None                      # Optional, for exploration drill-down
    ```
  - Plugins are registered at startup via a plugin directory (`src/plugins/observations/`) and can be hot-loaded.

- [ ] **5.1.2 — Ship a built-in system health observation plugin**
  - As the only **bundled** plugin (no external service dependencies):
    ```python
    class SystemHealthPlugin(ObservationPlugin):
        source_id = "system_health"

        async def observe(self) -> ObservationResult:
            return ObservationResult(metrics={
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_io_read_mb": ...,
                "active_processes": len(psutil.pids()),
                "uptime_hours": ...,
            }, timestamp=datetime.now())

        def default_predictions(self) -> dict:
            return {
                "cpu_percent": {"expected": 30, "tolerance": 20},
                "memory_percent": {"expected": 50, "tolerance": 15},
                "active_processes": {"expected": 150, "tolerance": 50},
            }
    ```
  - This gives the exploration engine a working data source out of the box, with no external API keys or services required.
  - Additional plugins (email, calendar, browser history, etc.) are documented as **example plugin templates** in `src/plugins/observations/examples/` but not shipped as active plugins. Users add them by implementing the interface and dropping them in the plugin directory.

- [ ] **5.1.3 — Build the world model**
  - Maintain a prediction store keyed by `source_id`:
    - For each metric in each plugin, track:
      ```python
      @dataclass
      class PredictionEntry:
          source_id: str
          metric_name: str
          expected_value: float
          tolerance: float          # How much deviation is "normal"
          observed_history: list     # Last N observations (ring buffer, default N=20)
          prediction_error: float    # Current divergence (0-1 normalized)
          last_updated: datetime
      ```
    - Predictions self-calibrate: after each observation cycle, `expected_value` is updated as an exponential moving average of recent observations. `tolerance` adjusts based on observed variance. This is simple statistics, not Bayesian inference — it works, it's explainable, and it doesn't require a statistics PhD to debug.
  - Persist the world model to semantic LTM on shutdown and reload on startup.

- [ ] **5.1.4 — Implement the surprise calculator**
  - For each observation, compute normalized prediction error:
    ```python
    error = abs(observed - expected) / max(tolerance, 1e-6)
    prediction_error = min(error, 1.0)  # Clamp to 0-1
    ```
  - Publish prediction errors to `agent/inference/surprise` with source, metric, error magnitude, and details.
  - High surprise (above configurable threshold, default: 0.6) triggers exploration for that source.

- [ ] **5.1.5 — Implement the exploration engine**
  - When surprise exceeds threshold and the agent is not in THROTTLED or EMERGENCY state:
    1. Check the exploration budget (daily token allocation, cooldown timer).
    2. If budget allows, invoke the plugin's `observe()` with more detail (if the plugin supports a `deep_observe()` method) or request the cognitive module (Phase 3) to analyze the surprising observation.
    3. The cognitive module generates a brief analysis: what changed, whether it matters, and what (if anything) the user should know.
    4. Update the world model with the new observation.
  - Exploration is **single-threaded** (one exploration at a time) and **cancellable** (any new cognitive escalation pre-empts exploration).
  - Exploration is suppressed when cortisol is active; amplified (shorter cooldown) after dopamine signals.

- [ ] **5.1.6 — Implement the proactive takeaway generator**
  - After exploration, classify findings:
    ```
    URGENT:      Requires immediate user attention (resource exhaustion, security anomaly)
    INFORMATIVE: Useful context for the user (unusual system behavior, trend shift)
    BACKGROUND:  Logged for consolidation, not surfaced to user
    ```
  - For URGENT: escalate to the reflex arc for immediate notification.
  - For INFORMATIVE: queue for delivery at the next natural interaction point.
  - For BACKGROUND: store in episodic memory for consolidation review (Phase 4).
  - Publish to `agent/inference/takeaway`.

- [ ] **5.1.7 — Implement the exploration budget and rate limiter**
  - Configurable bounds:
    ```yaml
    exploration:
      daily_token_budget: 5000        # Max tokens per day for exploration LLM calls
      cooldown_seconds: 300           # Minimum time between exploration cycles
      max_concurrent: 1               # Only one exploration at a time
      suppressed_in_states: [THROTTLED, EMERGENCY]
    ```
  - The cortisol system can dynamically reduce the budget to zero.
  - Log all exploration actions and outcomes for consolidation review (Phase 4).

- [ ] **5.1.8 — Document example plugin templates**
  - Provide well-commented, non-active example plugins for common future integrations:
    - `examples/email_gmail.py` — Gmail API observation plugin (requires OAuth setup)
    - `examples/calendar_google.py` — Google Calendar observation plugin
    - `examples/browser_history.py` — SQLite-based browser history reader (Chrome/Firefox)
    - `examples/journal_filesystem.py` — File system change monitoring in a watched directory
  - Each example includes setup instructions, required credentials/permissions, and expected observation metrics.

#### Suggested File Structure

```
src/
  active_inference/
    __init__.py
    plugin_interface.py     # ObservationPlugin ABC, ObservationResult dataclass
    plugin_loader.py        # Plugin discovery and hot-loading from plugin directory
    world_model.py          # Prediction store with self-calibrating EMA
    surprise.py             # Prediction-error calculator (simple normalized delta)
    exploration.py          # Exploration engine: trigger, budget, cancellation
    takeaway.py             # Proactive insight classification and routing
    budget.py               # Rate limiting and token budget enforcement
    config.py               # Thresholds, budgets, plugin directory path
    config.yaml             # Declarative configuration
  plugins/
    observations/
      __init__.py
      system_health.py      # Built-in: CPU, memory, disk, process count
      examples/
        email_gmail.py      # Template: Gmail integration
        calendar_google.py  # Template: Google Calendar integration
        browser_history.py  # Template: Browser history reader
        journal_filesystem.py  # Template: Filesystem change monitor
```

#### Acceptance Criteria
- Plugin interface is clean and documented; a new observation source can be added in < 50 lines of code.
- System health plugin works out of the box with zero configuration.
- World model self-calibrates within 20 observation cycles (predictions converge on actual patterns).
- Surprise detection and exploration trigger within 2s of a high-deviation observation.
- Exploration respects budget and state constraints — zero exploration during THROTTLED state.
- Proactive takeaways are generated for URGENT/INFORMATIVE findings; BACKGROUND findings are logged silently.
- Example plugin templates are runnable with appropriate credentials and documented setup steps.

---

### Task 5.2: Map Endocrine Hooks to System Directives

Implement the computational hormones that modulate agent behavior in response to environmental events.

**Objective:** Build the digital endocrine system — a set of event-driven hooks that publish hormone signals to the nervous system, causing downstream behavior changes across all phases. Each hormone maps specific trigger events to specific system modulations. All initial values are conservative (high thresholds, gentle effects, fast decay) and tunable via YAML.

#### Sub-tasks

- [ ] **5.2.1 — Implement the endocrine controller**
  - Build a central service that manages hormone state:
    - Track current hormone levels as continuous values (0.0–1.0), not binary on/off:
      ```python
      class HormoneState:
          dopamine: float = 0.0       # 0 = no reward, 1 = maximum reinforcement
          adrenaline: float = 0.0     # 0 = calm, 1 = maximum alert
          cortisol: float = 0.0       # 0 = no stress, 1 = maximum conservation
          endorphin: float = 0.0      # 0 = no relief, 1 = maximum restoration
      ```
    - Hormones decay over time toward baseline (0.0) using configurable half-life values.
    - **Conservative initial half-lives** (faster decay = hormones clear quickly, minimizing sustained side-effects):
      - Adrenaline: half-life **1 minute** — acute response clears fast
      - Dopamine: half-life **5 minutes** — short reinforcement window
      - Cortisol: half-life **15 minutes** — moderate conservation (not the 30+ min a bold config would use)
      - Endorphin: half-life **10 minutes** — brief restoration window
    - Publish the current hormone state to `agent/endocrine/state` at regular intervals (default: 10s) and on every significant state change (Δ > 0.1 on any hormone).
  - **Conservative increment sizes** — each trigger event adds a small, bounded amount rather than large spikes:
    ```yaml
    # config.yaml — conservative defaults
    hormone_increments:
      dopamine:   0.15    # Gentle reinforcement per success event
      adrenaline: 0.25    # Moderate alert per critical event
      cortisol:   0.15    # Gentle conservation per resource breach
      endorphin:  0.15    # Gentle restoration per resolution event
    ```
  - Hormones are clamped to [0.0, 1.0]. Multiple triggers stack additively but can never exceed 1.0.

- [ ] **5.2.2 — Implement Dopamine hooks**
  - **Trigger events:**
    - Cognitive module reports successful task completion (`agent/cognitive/result` with `success: true`)
    - Curiosity exploration discovers novel, high-value information (surprise reduced by > 0.3 in a single cycle)
    - A newly generated skill (Phase 4 REM) passes sandbox testing
  - **System effects when dopamine > activation threshold (conservative default: 0.5):**
    - Increase the Bayesian confidence score multiplier for newly created procedural memories by `1 + (dopamine_level * 0.25)` — a gentle boost (at max dopamine of 1.0, this is only a 1.25x multiplier, not a dramatic distortion).
    - Strengthen the association weight between the successful tool sequence and the triggering context in semantic memory.
    - Notify the curiosity engine to explore similar domains (positive feedback loop).
  - **Why threshold 0.5:** At the conservative increment of 0.15, it takes 4 successive success events to cross threshold. This prevents a single lucky completion from altering system behavior.
  - Publish dopamine level changes to `agent/endocrine/dopamine`.

- [ ] **5.2.3 — Implement Adrenaline hooks**
  - **Trigger events:**
    - Immune system detects a critical threat (`agent/immune/alert` with `severity: critical`)
    - User flags a task as urgent (user input metadata)
    - System encounters a cascading failure (multiple reflex handlers firing within a short window)
    - Approaching hard deadline (calendar event within configurable threshold, default: 15 minutes)
  - **System effects when adrenaline > activation threshold (conservative default: 0.6):**
    - Override the cognitive model router: force LLM usage regardless of budget (up to a configurable emergency ceiling).
    - Expand the context window to the model's maximum capacity.
    - Suspend all background exploration tasks (curiosity engine paused).
    - Priority-boost the triggering task in the cognitive event loop.
  - **System effects when adrenaline > escalation threshold (conservative default: 0.85):**
    - All above effects, plus: transition the FSM (Phase 1) to EMERGENCY state.
  - **Why thresholds 0.6/0.85:** At increment 0.25, three critical events are needed to reach EMERGENCY. A single alert triggers adrenaline (0.25) but does *not* change routing — it takes sustained multi-event stress to actually alter system behavior.
  - Adrenaline decays rapidly (1 min half-life) once the triggering event stream stops.
  - Publish adrenaline level changes to `agent/endocrine/adrenaline`.

- [ ] **5.2.4 — Implement Cortisol hooks**
  - **Trigger events:**
    - Interoception (Phase 1) publishes threshold breaches:
      - CPU > 90%, memory > 95%, disk I/O latency > 200ms
      - Token budget remaining < 5% of daily allocation
      - GPU thermal throttling detected
    - Sustained high workload without idle periods (no consolidation for > 4 hours)
  - **System effects when cortisol > activation threshold (conservative default: 0.5):**
    - Suppress the curiosity engine: reduce exploration budget by 50% (not to zero — partial suppression first).
    - Prefer SLM routing in the cognitive model router (soft preference, not forced).
  - **System effects when cortisol > escalation threshold (conservative default: 0.8):**
    - All above effects, strengthened: exploration budget to zero, force SLM-only routing.
    - Reduce the STM buffer size to conserve memory.
    - Defer non-critical consolidation phases (archival pruning can wait).
    - Reduce sensory capture rates (vision FPS → minimum, audio → wake-word only).
  - **Why two tiers:** A single resource breach (cortisol 0.15) doesn't change anything. Sustained stress must accumulate before the system shifts to conservation mode — this prevents brief CPU spikes from unnecessarily degrading capability.
  - Cortisol decays moderately (15 min half-life), ensuring the agent remains conservative until resources genuinely recover, but not so long that a brief spike locks the system in conservation for an hour.
  - Publish cortisol level changes to `agent/endocrine/cortisol`.

- [ ] **5.2.5 — Implement Endorphin hooks**
  - **Trigger events:**
    - Resolution of an adrenaline-triggering event (adrenaline drops below 0.1 after being above 0.5)
    - Completion of a large, multi-step task (> 10 cognitive reasoning calls)
    - Successful immune neutralization of a threat (quarantine completed)
  - **System effects when endorphin > activation threshold (conservative default: 0.4):**
    - Trigger the sleep consolidation cycle (Phase 4) if not already running.
    - Gradually restore baseline parameters: expand exploration budget, re-enable LLM routing, restore normal sensory capture rates.
    - Reset prediction error accumulators in the world model to give the agent a "fresh start" for the next exploration cycle.
  - **Why threshold 0.4:** At increment 0.15, three resolution events are needed. A single completed task produces endorphin (0.15) but doesn't trigger consolidation — only sustained resolution does. This prevents premature consolidation mid-workflow.
  - Endorphin signals represent the system returning to homeostasis.
  - Publish endorphin level changes to `agent/endocrine/endorphin`.

- [ ] **5.2.6 — Implement the Language to Hierarchical Rewards (L2HR) mapping**
  - Build a configurable mapping layer that translates natural-language task outcomes into hormone adjustments:
    - The cognitive module's result includes a self-assessed outcome description.
    - The L2HR mapper uses the SLM to classify the outcome:
      ```
      "Successfully resolved the user's question"     → dopamine +0.10
      "Detected and quarantined a prompt injection"    → dopamine +0.10, endorphin +0.10
      "Failed to complete the task after 3 retries"    → dopamine -0.05, cortisol +0.10
      "User escalated urgency"                         → adrenaline +0.20
      ```
    - **Conservative L2HR values:** All L2HR-generated adjustments are deliberately smaller than direct event triggers. L2HR is a secondary, interpretive signal — the primary triggers (direct event hooks in 5.2.2–5.2.5) do the heavy lifting. L2HR adds nuance, it doesn't drive the system.
    - This provides a bridge between fuzzy, natural-language outcomes and the precise numerical hormone adjustments.
  - The mapping is configurable via YAML and learnable over time (consolidation can adjust mappings based on observed patterns).

- [ ] **5.2.7 — Add endocrine telemetry and observability**
  - Log every hormone level change > 0.05 with timestamp, triggering event, old level, and new level.
  - Expose a `/endocrine/status` endpoint (or CLI command) that shows current hormone levels, time since last trigger, decay trajectory, and recent change history.
  - Collect aggregate statistics: how often each hormone crosses its activation threshold per day, average time spent above threshold, number of escalation-tier activations.
  - This telemetry is the basis for tuning — if a hormone almost never fires, its threshold is too high; if it fires constantly, the threshold is too low or the increment is too large.

#### Conservative Default Summary

| Parameter | Dopamine | Adrenaline | Cortisol | Endorphin |
| :--- | :--- | :--- | :--- | :--- |
| Increment per trigger | 0.15 | 0.25 | 0.15 | 0.15 |
| Activation threshold | 0.50 | 0.60 | 0.50 | 0.40 |
| Escalation threshold | — | 0.85 | 0.80 | — |
| Decay half-life | 5 min | 1 min | 15 min | 10 min |
| Baseline | 0.0 | 0.0 | 0.0 | 0.0 |

> **Tuning guidance:** After 1-2 weeks of operation, review telemetry. If hormones rarely cross activation thresholds, lower thresholds by 0.05-0.10 increments. If they fire too often, raise thresholds or reduce increments. Never change more than one parameter at a time per hormone.

#### Suggested File Structure

```
src/
  endocrine/
    __init__.py
    controller.py           # Central hormone state management, decay, publishing
    hooks/
      __init__.py
      dopamine.py           # Reward/reinforcement triggers and effects
      adrenaline.py         # Acute stress triggers and effects
      cortisol.py           # Resource conservation triggers and effects
      endorphin.py          # Restoration triggers and effects
    l2hr.py                 # Language to Hierarchical Rewards mapping
    telemetry.py            # Hormone level logging, status endpoint, aggregate stats
    config.py               # Thresholds, half-lives, increments, budget overrides
    config.yaml             # Declarative hormone trigger/effect mappings
```

#### Acceptance Criteria
- Hormone state updates are published within 100ms of trigger event receipt.
- Dopamine correctly reinforces procedural memory after successful task completion (when above threshold).
- Adrenaline overrides cognitive routing to LLM within 500ms of crossing activation threshold.
- Cortisol suppresses exploration and downgrades model routing within 1s of crossing activation threshold.
- Endorphin triggers consolidation within 30s of crossing activation threshold.
- All hormone levels decay toward baseline at their configured half-life rates.
- L2HR mapping correctly translates ≥ 80% of natural-language outcomes to appropriate hormone adjustments.
- No hormone can exceed 1.0 or drop below 0.0 (clamped).
- All thresholds, increments, and half-lives are configurable via YAML without code changes.
- Telemetry captures all significant level changes and provides actionable summary statistics for tuning.

---

## Phase 5 Deliverables Summary

| Deliverable | Component | Key Technology |
| :--- | :--- | :--- |
| Observation plugin interface + loader | Exploration Engine | ABC plugin contract, hot-loading |
| System health plugin (built-in) | Exploration Engine | psutil, zero-config |
| Example plugin templates (email, calendar, etc.) | Exploration Engine | Gmail API, Google Calendar, SQLite |
| Self-calibrating world model | Exploration Engine | Exponential moving average, normalized prediction error |
| Surprise calculator | Exploration Engine | Simple delta + clamped normalization |
| Budget-bounded exploration engine | Exploration Engine | Token budget, cooldown, state-aware suppression |
| Proactive takeaway generator | Exploration Engine | Urgency classification, event bus routing |
| Endocrine controller | Endocrine System | Continuous hormone state (0.0–1.0), half-life decay |
| Dopamine hooks | Endocrine System | Reward reinforcement of procedural memory |
| Adrenaline hooks | Endocrine System | Emergency compute escalation |
| Cortisol hooks | Endocrine System | Conservation mode, curiosity suppression |
| Endorphin hooks | Endocrine System | Consolidation triggering, homeostasis |
| L2HR mapping | Endocrine System | NL outcome → hormone adjustment bridge |
| Endocrine telemetry | Endocrine System | Level logging, status endpoint, tuning stats |

---

## Dependencies

- **Upstream:** Phase 1 (event bus, interoception cortisol signals, FSM state transitions), Phase 3 (cognitive module results feed dopamine/endorphin), Phase 4 (consolidation triggered by endorphin, procedural memory reinforced by dopamine)
- **Downstream:** Modulates all other phases — endocrine signals affect reflex arc behavior (Phase 1), sensory capture rates (Phase 2), cognitive model routing (Phase 3), and consolidation scheduling (Phase 4)
- **System Requirements:** Ollama with SLM for L2HR mapping, psutil for system health plugin, event bus operational
