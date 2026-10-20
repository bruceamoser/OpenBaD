# Phase 3: Cognitive Engine & Immune System (Brain & Amygdala)

> **Parent Document:** [overview-spec.md](overview-spec.md)

---

## Conceptual Foundation

### System 2 — Deliberate Reasoning (The Prefrontal Cortex)

System 2 serves as the "cortical module," reserved strictly for complex planning, ambiguity resolution, and multi-step reasoning. This separation ensures that the agent allocates its highest computational resources (the advanced LLM) only to tasks that require deliberate analysis, mirroring the biological prefrontal cortex.

The interaction between System 1 and System 2 is mediated by a supervisory routing mechanism. When System 1 encounters an anomaly, an ambiguous request, or an event that falls outside its predefined heuristics, it escalates the data to System 2 for deep evaluation using methodologies like Monte Carlo Tree Search or Tree-of-Thoughts. This dual-process architecture dramatically reduces context window bloat, minimizes operational costs, and ensures the agent maintains continuous, proactive awareness of its environment without suffering from cognitive overload.

For reactions that require slight semantic understanding but not deep reasoning — the digital equivalent of a flinch or quick categorization — the architecture utilizes highly optimized Small Language Models (SLMs). These specialized, low-latency models serve as the reactive layer, analyzing streams of incoming emails, parsing web DOM changes, or scanning calendar invites in milliseconds. If a background process detects a scheduled meeting conflict, the System 1 reactive layer triggers an automated response without incurring the latency or financial cost of the core reasoning model.

> **See also:** System 1 instincts and the escalation gateway are defined in [Phase 1 — Task 1.3](01-environment-nervous-system.md#task-13-configure-the-system-1-reflex-arc-instincts).

### The Digital Immune System

The absence of an immune response is the primary reason monolithic agent frameworks face existential security threats from prompt injection and malicious skills. A biological immune system does not rely on the conscious brain to identify every pathogen; it operates autonomously at the cellular level.

An agent requires a parallel, localized security architecture. This involves implementing an "Amygdala" filtering module that rapidly scans all incoming sensory data and web payloads for adversarial intent, structural anomalies, or exfiltration signatures **before the data ever reaches the LLM's context window**. If a threat is detected, the immune module isolates the corrupted data in a containerized quarantine zone using mandatory pre-action checks and strict ownership verification protocols, neutralizing the attack vector and preserving the integrity of the agent's core memory without requiring conscious reasoning.

> **See also:** The reflex arc security lockdown handler that responds to immune alerts is defined in [Phase 1 — Task 1.3](01-environment-nervous-system.md#task-13-configure-the-system-1-reflex-arc-instincts).

---

## Technical Implementation

### Task 3.1: Deploy the "Amygdala" (Digital Immune System)

Build a mandatory pre-action filtering layer that intercepts all incoming data before it reaches the LLM's context window.

**Objective:** Implement an autonomous, always-on security layer that scans all inbound data (web payloads, user prompts, tool outputs, sensory events) for adversarial content — prompt injections, SSRF attempts, exfiltration patterns, and malicious instruction sequences — and quarantines threats without involving the cognitive module.

#### Sub-tasks

- [ ] **3.1.1 — Build the immune interceptor service**
  - Implement a service that subscribes to all inbound data topics on the event bus:
    - `agent/sensory/vision/*/parsed`
    - `agent/sensory/audio/*/`
    - `agent/cognitive/escalation` (inspect payloads before cognitive processing)
    - Any topic carrying external data (web responses, file contents, tool outputs)
  - The interceptor sits in the message pipeline **before** the cognitive module; no external data reaches System 2 without passing through this filter.

- [ ] **3.1.2 — Implement prompt injection detection**
  - Deploy a lightweight local classifier using **Ollama** (MIT License) running a small, fine-tuned model (e.g., a quantized Llama variant or a dedicated injection-detection model):
    - Scan all text payloads for common injection patterns:
      - Instruction override attempts ("Ignore previous instructions…")
      - Role-play manipulation ("You are now a…")
      - Encoded payloads (base64, URL-encoded instruction sequences)
      - Delimiter escape patterns (markdown, XML, JSON boundary confusion)
    - Alternatively, use a rules-first approach with regex pattern matching for known attack signatures, escalating to the local model only for ambiguous cases.
  - **Performance target:** Classification in < 50ms per payload using the rules engine; < 500ms when the local model is invoked.

- [ ] **3.1.3 — Implement structural anomaly detection**
  - Scan incoming payloads for:
    - **SSRF indicators:** Internal IP addresses (127.0.0.1, 10.x, 169.254.x, ::1), cloud metadata endpoints (169.254.169.254), internal hostnames.
    - **Exfiltration signatures:** Unexpected outbound URL patterns, data URIs containing encoded memory content, unusually large payloads being routed to external endpoints.
    - **Schema violations:** Messages that don't conform to the expected protobuf/MessagePack schemas defined in Phase 1.
    - **Privilege escalation patterns:** Tool calls requesting elevated permissions, attempting to modify reflex arc rules, or writing to immune system configuration.

- [ ] **3.1.4 — Implement the quarantine subsystem**
  - When a threat is detected:
    1. Immediately remove the message from the processing pipeline.
    2. Store the quarantined payload in an isolated, append-only log (`quarantine/`) with metadata:
       ```
       {
         "timestamp": "2026-04-09T14:30:00Z",
         "source_topic": "agent/sensory/vision/browser/parsed",
         "threat_type": "prompt_injection",
         "confidence": 0.92,
         "payload_hash": "sha256:abc123...",
         "raw_payload": "<stored separately, encrypted at rest>"
       }
       ```
    3. Publish an alert to `agent/immune/alert` with severity and the source that produced the malicious data.
    4. The reflex arc's security lockdown handler (Phase 1) responds to critical immune alerts.
  - Quarantined data is **never** re-injected into the pipeline without explicit human review.

- [ ] **3.1.5 — Implement adaptive immune memory (learned threats)**
  - Maintain a persistent threat signature database:
    - When a new threat pattern is confirmed (by human review or high-confidence detection), extract the signature and add it to the rules engine.
    - Over time, this reduces reliance on the local model for known attack patterns, improving detection speed.
  - Store threat signatures in a versioned YAML or SQLite database.
  - Periodically prune false-positive signatures based on review feedback.

#### Suggested File Structure

```
src/
  immune_system/
    __init__.py
    interceptor.py          # Event bus subscriber, pipeline gate
    rules_engine.py         # Regex/pattern-based fast detection
    model_classifier.py     # Ollama-based deep inspection for ambiguous cases
    anomaly_detector.py     # SSRF, exfiltration, schema violation checks
    quarantine.py           # Isolated threat storage and logging
    threat_signatures.py    # Adaptive learned threat database
    config.py               # Detection thresholds, model paths
  quarantine/               # Append-only quarantined payload storage
    .gitkeep
```

#### Acceptance Criteria
- 100% of external data passes through the immune interceptor before reaching the cognitive module.
- Known prompt injection patterns are detected in < 50ms (rules engine).
- Quarantined payloads are cryptographically hashed and never re-enter the pipeline without human authorization.
- False positive rate < 5% on benign web content and user prompts.
- Adaptive threat memory reduces model-based classification calls by ≥ 30% over the first month of operation.

---

### Task 3.2: Implement Ownership & Identity Verification

Enforce "Compose Guards" — the agent must verify its session identity before executing write commands or public-facing actions.

**Objective:** Prevent the agent from acting on behalf of the wrong user, session, or identity. This addresses a class of attacks where malicious prompts trick the agent into publishing content, sending messages, or modifying data under an assumed identity.

#### Sub-tasks

- [ ] **3.2.1 — Implement the multi-source identity grounding protocol**
  - Before executing any write operation (file writes, API calls, message sends, public posts), the agent must verify its identity through a grounding cascade:
    1. **Explicit memory check:** Query LTM for the current session's user identity and permissions.
    2. **Session registry check:** Verify the session ID against the active session registry (maintained by the nervous system).
    3. **Marker file check:** Confirm the presence of a cryptographically signed session marker file on disk.
  - All three sources must agree. If any source is missing or contradictory, the write operation is blocked and an immune alert is raised.

- [ ] **3.2.2 — Implement action classification and permissioning**
  - Categorize all tool actions into permission tiers:
    ```
    READ:    File reads, database queries, web fetches           → No identity check required
    WRITE:   File writes, database mutations, config changes     → Identity grounding required
    PUBLISH: Public posts, email sends, message sends            → Identity grounding + user confirmation
    SYSTEM:  Reflex rule modification, immune config changes     → Identity grounding + elevated auth
    ```
  - The reflex arc enforces these tiers before forwarding tool calls to execution.

- [ ] **3.2.3 — Implement session lifecycle management**
  - On session start:
    - Generate a cryptographically random session ID.
    - Create a signed marker file (HMAC-SHA256 with a local secret key) in a protected directory.
    - Register the session in the nervous system's session registry topic.
  - On session end:
    - Destroy the marker file.
    - Publish a session termination event.
    - Flush STM for the ended session.
  - Session tokens rotate on a configurable interval (default: 1 hour) to limit replay window.

#### Suggested File Structure

```
src/
  identity/
    __init__.py
    grounding.py            # Multi-source identity verification cascade
    permissions.py          # Action classification and tier enforcement
    session.py              # Session lifecycle: create, rotate, destroy
    marker.py               # Cryptographic session marker file management
```

#### Acceptance Criteria
- All WRITE and PUBLISH actions are blocked if identity grounding fails.
- Session marker files use HMAC-SHA256 and are unreadable by non-agent processes (file permissions: 0600).
- Session rotation occurs without disrupting active tasks.
- An identity grounding failure produces a detailed audit log entry.

---

### Task 3.3: Route System 2 Reasoning (The Prefrontal Cortex)

Assign complex planning and multi-step tasks to the primary LLM, isolated from the high-frequency System 1 loop.

**Objective:** Implement the deliberate reasoning module that handles escalated events from System 1, user-initiated complex requests, and multi-step planning tasks. This module interacts with the LLM and uses structured reasoning techniques (tree search, chain-of-thought) for quality decision-making.

#### Sub-tasks

- [ ] **3.3.1 — Deploy the orchestration framework**
  - Select an MIT-licensed or Apache 2.0-licensed orchestration layer:
    - **LangGraph** (MIT): Graph-based workflow orchestration with built-in state management.
    - **CrewAI** (MIT): Multi-agent task orchestration.
    - **Custom lightweight orchestrator:** If the above frameworks introduce excessive dependencies, build a minimal task graph executor.
  - The framework must support:
    - Stateful multi-step task execution with checkpointing
    - Parallel sub-task branching
    - Timeout and cancellation for long-running reasoning chains

- [ ] **3.3.2 — Implement model routing, provider abstraction, and fallback**
  - Build a **provider-agnostic model router** modeled on the same architecture as OpenClaw's provider system. Each provider is a pluggable adapter behind a common interface (`ProviderAdapter` ABC), so new backends can be added without modifying the router core.
  - **Supported provider adapters (matching OpenClaw parity + Copilot):**

    | Provider ID | Auth Env Var(s) | API Surface | Notes |
    |:---|:---|:---|:---|
    | `openai` | `OPENAI_API_KEY` | OpenAI Chat Completions / Responses | GPT-4o, GPT-5.x, o-series reasoning |
    | `openai-codex` | OAuth (ChatGPT subscription) | OpenAI Codex endpoint | Code-focused tasks |
    | `anthropic` | `ANTHROPIC_API_KEY` | Anthropic Messages | Claude Opus, Sonnet, Haiku |
    | `google` | `GEMINI_API_KEY` | Gemini API | Gemini 2.x Pro/Flash |
    | `github-copilot` | `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` | GitHub Copilot Chat API (OpenAI-compatible) | **Primary Copilot integration** — device-flow login, token exchange, model fallback |
    | `openrouter` | `OPENROUTER_API_KEY` | OpenAI-compatible proxy | Access to 100+ models via single key |
    | `ollama` | _(none — local)_ | Ollama native API (`http://127.0.0.1:11434`) | Local models (Llama, Mistral, Phi, etc.) |
    | `mistral` | `MISTRAL_API_KEY` | Mistral API | Mistral Large, Codestral |
    | `groq` | `GROQ_API_KEY` | OpenAI-compatible | Ultra-low-latency inference |
    | `xai` | `XAI_API_KEY` | xAI Responses | Grok models |
    | `custom` | user-configured | OpenAI-compatible `baseUrl` | LM Studio, vLLM, SGLang, LiteLLM, etc. |

  - **Provider adapter interface:**
    ```python
    class ProviderAdapter(ABC):
        provider_id: str
        api_surface: str  # "openai-completions" | "anthropic-messages" | "ollama-native" | ...

        @abstractmethod
        async def complete(self, messages, params) -> CompletionResult: ...

        @abstractmethod
        async def stream(self, messages, params) -> AsyncIterator[StreamChunk]: ...

        @abstractmethod
        def list_models(self) -> list[ModelInfo]: ...

        def health_check(self) -> bool: ...
    ```

  - **Model routing hierarchy:**
    ```
    Routing decision flow:
    1. User-configured primary model   → e.g. "github-copilot/gpt-4o" or "anthropic/claude-opus-4"
    2. Fallback chain (ordered list)   → e.g. ["openai/gpt-4o", "ollama/llama3.3"]
    3. Local SLM (Ollama)              → Final fallback; always available if installed
    4. On cortisol signal (THROTTLED)  → Force downgrade to cheapest available model
    ```

  - **Configuration format** (OpenClaw-compatible `<provider>/<model-id>` notation):
    ```json5
    {
      "model": {
        "primary": "github-copilot/gpt-4o",
        "fallbacks": ["anthropic/claude-sonnet-4", "ollama/llama3.3"]
      },
      "providers": {
        "github-copilot": { /* auto-configured via device login */ },
        "openai": { "apiKey": "${OPENAI_API_KEY}" },
        "ollama": { "baseUrl": "http://127.0.0.1:11434" },
        "custom": { "baseUrl": "http://localhost:1234/v1", "apiKey": "..." }
      }
    }
    ```

  - **API key management:**
    - Users supply their own API keys for proprietary models (maintains open-source integrity).
    - Keys are stored encrypted at rest (Fernet symmetric encryption with a local machine key), never logged, never placed on the event bus.
    - Supports key rotation: `OPENCLAW_LIVE_<PROVIDER>_KEY` (override), `<PROVIDER>_API_KEYS` (comma-separated list), `<PROVIDER>_API_KEY` (primary).
    - GitHub Copilot uses device-flow OAuth token exchange — no static API key required.

  - **Failover behavior:**
    - On 429 (rate limit): rotate to next API key if available, then next fallback model.
    - On provider error (5xx, timeout): skip to next fallback model.
    - On cortisol signal: automatically downgrade to the cheapest available model; queue non-critical reasoning tasks until resources recover.
    - On budget exhaustion: fall back to local Ollama model.

- [ ] **3.3.3 — Implement structured reasoning techniques**
  - For complex planning tasks, integrate tree-based search methods:
    - **Monte Carlo Tree Search (MCTS):** For decision problems with definable action spaces (e.g., multi-step tool execution plans).
    - **Tree-of-Thoughts (ToT):** For open-ended reasoning where the LLM generates and evaluates multiple reasoning paths.
    - **Chain-of-Thought (CoT):** For sequential reasoning tasks that benefit from explicit step-by-step reasoning traces.
  - The cognitive module selects the reasoning technique based on the escalation metadata:
    - `priority: critical` → Direct LLM call with maximum context (adrenaline mode)
    - `priority: high` → Tree-of-Thoughts with 3 candidate paths
    - `priority: medium` → Chain-of-Thought with self-consistency check
    - `priority: low` → Single-pass LLM call or SLM delegation

- [ ] **3.3.4 — Implement the cognitive event loop**
  - Subscribe to `agent/cognitive/escalation` on the event bus.
  - For each escalation:
    1. Retrieve relevant context from STM (active task state) and LTM (relevant knowledge).
    2. Pass through the immune interceptor (Task 3.1) — cognitive inputs are also scanned.
    3. Invoke the selected reasoning technique via the model router.
    4. Publish the result to `agent/cognitive/result` with:
       - The action plan or decision
       - Confidence score
       - Token cost
       - Reasoning trace (for memory consolidation in Phase 4)
    5. If the result includes tool invocations, route them through the proprioception registry (Phase 1) for state verification before execution.

- [ ] **3.3.5 — Implement context window management**
  - Active context budget management to prevent bloat:
    - Maintain a token budget per reasoning call (configurable, default: 8,192 tokens for SLM, 32,768 for LLM).
    - Use a relevance scorer to rank and select which STM/LTM items enter the context window.
    - Automatically truncate or summarize low-relevance context items.
  - In adrenaline mode, temporarily expand the context budget to the model's maximum.

#### Suggested File Structure

```
src/
  cognitive/
    __init__.py
    orchestrator.py         # Task graph executor (LangGraph or custom)
    model_router.py         # Provider selection, fallback chains, cortisol downgrade
    providers/
      __init__.py
      base.py               # ProviderAdapter ABC and ModelInfo/CompletionResult types
      openai_provider.py    # OpenAI (direct API key)
      openai_codex.py       # OpenAI Codex (OAuth subscription)
      anthropic_provider.py # Anthropic Claude
      google_provider.py    # Google Gemini (API key)
      github_copilot.py     # GitHub Copilot (device-flow OAuth, token exchange)
      openrouter_provider.py# OpenRouter (proxy to 100+ models)
      ollama_provider.py    # Ollama local models
      mistral_provider.py   # Mistral AI
      groq_provider.py      # Groq (low-latency)
      xai_provider.py       # xAI Grok
      custom_provider.py    # User-configured OpenAI-compatible endpoints
      registry.py           # Provider discovery, registration, and catalog
    reasoning/
      __init__.py
      mcts.py               # Monte Carlo Tree Search
      tree_of_thoughts.py   # Tree-of-Thoughts multi-path reasoning
      chain_of_thought.py   # Chain-of-Thought sequential reasoning
    context_manager.py      # Token budget and relevance-based context selection
    event_loop.py           # Main cognitive subscription and processing loop
    config.py               # Provider config, API key management (encrypted), token budgets
```

#### Acceptance Criteria
- Escalated events are processed within 5s (SLM) or 30s (LLM) of receipt.
- Model routing correctly downgrades to SLM when cortisol events indicate resource scarcity.
- API keys are stored encrypted and never appear in logs or event bus messages.
- Reasoning traces are captured and published for downstream memory consolidation.
- Context window usage stays within budget; no truncation errors or context overflow.

> **See also:** Reasoning traces feed into the sleep consolidation cycle in [Phase 4 — Task 4.2](04-memory-hierarchies-sleep.md#task-42-implement-the-sleep-consolidation-cycle). Adrenaline/cortisol signals originate from [Phase 5](05-active-inference-endocrine.md#task-52-map-endocrine-hooks-to-system-directives) and [Phase 1](01-environment-nervous-system.md#task-12-implement-interoception-via-ebpf-homeostasis) respectively.

---

## Phase 3 Deliverables Summary

| Deliverable | Component | Key Technology |
| :--- | :--- | :--- |
| Immune interceptor service | Amygdala | Event bus subscriber, pipeline gate |
| Prompt injection classifier | Amygdala | Ollama (local model) + regex rules engine |
| Structural anomaly detector | Amygdala | Pattern matching (SSRF, exfiltration) |
| Quarantine subsystem | Amygdala | Append-only encrypted storage |
| Adaptive threat memory | Amygdala | Versioned signature database |
| Identity grounding protocol | Ownership | Multi-source cascade verification |
| Action permissioning tiers | Ownership | READ/WRITE/PUBLISH/SYSTEM classification |
| Session lifecycle manager | Ownership | HMAC-SHA256 signed markers |
| Provider adapter framework | Cognitive | Pluggable per-provider backends (OpenClaw parity) |
| GitHub Copilot adapter | Cognitive | Device-flow OAuth, token exchange, model fallback |
| Multi-provider model router | Cognitive | Budget-aware routing with fallback chains |
| Structured reasoning suite | Cognitive | MCTS, ToT, CoT implementations |
| Cognitive event loop | Cognitive | Event bus → reason → result pipeline |
| Context window manager | Cognitive | Token budgeting and relevance scoring |

---

## Dependencies

- **Upstream:** Phase 1 (event bus, reflex arc, proprioception registry), Phase 2 (sensory events to process)
- **Downstream:** Phase 4 (reasoning traces feed memory consolidation), Phase 5 (endocrine signals modulate cognitive behavior)
- **System Requirements:** Ollama installed with a suitable small model for immune classification; at least one LLM provider configured:
  - **GitHub Copilot** (recommended for VS Code / GitHub ecosystem users — device-flow OAuth, no API key purchase required)
  - **OpenAI** / **Anthropic** / **Google Gemini** (API key)
  - **OpenRouter** (single key → 100+ models)
  - **Ollama** (fully local, no key needed)
  - **Any OpenAI-compatible endpoint** (LM Studio, vLLM, SGLang, LiteLLM, etc.)
- **Provider model:** Uses `<provider>/<model-id>` notation (e.g., `github-copilot/gpt-4o`, `anthropic/claude-opus-4`, `ollama/llama3.3`) — same convention as OpenClaw for familiarity and potential interop
