# Phase 4: Memory Hierarchies & Sleep Consolidation

> **Parent Document:** [overview-spec.md](overview-spec.md)

---

## Conceptual Foundation

### Hierarchical Memory Dynamics

The treatment of memory in early agent frameworks demonstrates a profound misunderstanding of biological information retention. Relying on flat Markdown files or simple rolling context buffers guarantees that memory will degrade with scale and use. As the context window expands, the LLM inevitably suffers from attention dilution, losing the ability to distinguish between transient conversational noise and enduring factual knowledge.

To prevent degradation, a sophisticated agent requires a memory hierarchy that strictly delineates **Short-Term Memory (STM)** from **Long-Term Memory (LTM)**, orchestrated by a centralized memory controller:

- **STM** functions as immediate working memory, maintaining the active context of a specific task. It is bounded, highly volatile, and optimized for parallel attention mechanisms. Once a task concludes, the STM buffer must be flushed to prevent context contamination.

- **LTM** is a vast, persistent repository divided into distinct cognitive tiers:
  - **Episodic memory** captures sequential logs of interactions, preserving exact chronological sequences.
  - **Semantic memory** abstracts facts, concepts, and entity relationships into vector databases and knowledge graphs.
  - **Procedural memory** encodes learned skills, successful workflows, and tool execution patterns into executable routines.

Architectures like ZenBrain map these functions across up to seven distinct layers to ensure that behavioral strategies are separated from basic conversational history.

### Sleep Consolidation — The Bridge Between STM and LTM

The human brain does not learn continuously; it requires offline periods to synthesize and stabilize neural pathways. Digital implementations such as DreamOS and SuperLocalMemory have pioneered "unihemispheric dreaming" and offline consolidation algorithms. The digital sleep cycle operates through three distinct phases:

1. **Slow Wave Sleep (SWS):** Replays execution traces and failure logs from STM. Extracts negative constraints — identifying precise actions that led to errors — and writes them to a restrictive policy index to prevent repeating mistakes.

2. **Rapid Eye Movement (REM):** Analyzes successful task completions, abstracting specific action sequences into generalized, reusable strategies. New strategies are assigned Bayesian confidence scores and merged into procedural memory.

3. **Synaptic Homeostasis and Pruning:** Using the Ebbinghaus forgetting curve and Fisher-Rao Quantization-Aware Distance (FRQAD), the memory controller systematically decays rarely accessed or contradictory data. High-utility memories are compressed into dense hierarchical embeddings; redundant information is purged.

### Neuroplasticity — Self-Improvement Through Tool Generation

Current agents remain static; their capabilities are bounded by tools explicitly provided by humans. A biologically inspired agent must possess the capacity to generate its own tools and optimize its own pathways. Using frameworks akin to the Voyager architecture, the agent can use downtime to write executable code for novel tasks, verify it in a secure sandbox, and permanently store successful scripts in an ever-growing procedural skill library.

> **See also:** The cognitive module that produces reasoning traces for consolidation is in [Phase 3 — Task 3.3](03-cognitive-engine-immune-system.md#task-33-route-system-2-reasoning-the-prefrontal-cortex). Endorphin signals that trigger the sleep cycle are defined in [Phase 5](05-active-inference-endocrine.md#task-52-map-endocrine-hooks-to-system-directives).

---

## Technical Implementation

### Task 4.1: Construct Short-Term (STM) and Long-Term Memory (LTM)

Replace flat file memory with a mathematically rigorous, multi-tiered memory architecture using local-first storage.

**Objective:** Implement a structured memory hierarchy with clearly separated STM and LTM tiers, each optimized for its access pattern. STM provides fast, volatile working memory for active tasks. LTM provides persistent, indexed storage across episodic, semantic, and procedural dimensions.

#### Sub-tasks

- [ ] **4.1.1 — Implement Short-Term Memory (STM)**
  - Build a bounded, volatile context buffer for active sessions:
    - Use an in-memory data structure (ring buffer or bounded deque) with a configurable maximum size (default: 32,768 tokens).
    - Each STM entry contains:
      ```
      {
        "entry_id": "uuid",
        "task_id": "current_task_uuid",
        "timestamp": "2026-04-09T15:00:00Z",
        "content_type": "observation" | "action" | "result" | "reasoning_trace",
        "content": "...",
        "token_count": 142,
        "relevance_score": 0.85
      }
      ```
    - When the buffer reaches capacity, evict the lowest-relevance entries first (not strictly FIFO — relevance-weighted eviction).
  - STM is **task-scoped**: each active task gets its own isolated STM buffer. When a task completes, its STM is flushed to the consolidation queue (not directly to LTM).
  - Expose STM read/write through the event bus:
    - Write: `agent/memory/stm/write`
    - Read: Direct function call from cognitive module (low-latency requirement)
    - Flush: `agent/memory/stm/flush` (triggers consolidation pipeline)

- [ ] **4.1.2 — Implement Episodic Long-Term Memory**
  - Deploy a persistent, append-only log of all agent interactions:
    - Use **SQLite** (public domain) as the local storage backend — lightweight, zero-config, and well-suited for chronological log access.
    - Schema:
      ```sql
      CREATE TABLE episodic_memory (
        id INTEGER PRIMARY KEY,
        task_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        event_type TEXT NOT NULL,
        content TEXT NOT NULL,
        content_embedding BLOB,       -- vector for similarity search
        source_topic TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX idx_episodic_task ON episodic_memory(task_id);
      CREATE INDEX idx_episodic_time ON episodic_memory(timestamp);
      ```
    - Episodic memory preserves the **exact chronological sequence** of events. It is never directly edited — only appended to and eventually pruned by the consolidation cycle.

- [ ] **4.1.3 — Implement Semantic Long-Term Memory**
  - Deploy a vector database for fact, concept, and entity relationship storage:
    - **ChromaDB** (Apache 2.0): Embedded vector database, Python-native, supports metadata filtering.
    - Alternative: **Valkey** (BSD 3-Clause) with the vector search module for higher throughput.
  - Semantic memory stores **abstracted knowledge**, not raw logs:
    - Entity-relationship tuples: `(subject, predicate, object, confidence, source_episode_id)`
    - Fact embeddings with metadata tags (domain, last_accessed, access_count, confidence_score)
    - Concept clusters linked by cosine similarity
  - Embedding model:
    - Use a local embedding model via **sentence-transformers** (Apache 2.0): `all-MiniLM-L6-v2` (384-dim, fast) or `all-mpnet-base-v2` (768-dim, higher quality).
    - All embeddings are computed locally — no external API dependency.
  - Support efficient retrieval:
    - Similarity search (cosine distance) for context assembly
    - Metadata-filtered queries (e.g., "all facts about user preferences with confidence > 0.8")
    - Hybrid search combining keyword (BM25) and vector similarity

- [ ] **4.1.4 — Implement Procedural Long-Term Memory (Skill Library)**
  - Build a persistent repository of learned executable routines:
    - Each skill entry represents a successful, tested tool-execution sequence:
      ```
      {
        "skill_id": "uuid",
        "name": "resolve_calendar_conflict",
        "description": "Detect and resolve overlapping calendar events by...",
        "trigger_conditions": ["calendar_conflict_detected"],
        "action_sequence": [
          {"tool": "calendar_api", "action": "get_events", "params": {...}},
          {"tool": "calendar_api", "action": "reschedule", "params": {...}},
          {"tool": "notification", "action": "inform_user", "params": {...}}
        ],
        "confidence_score": 0.78,
        "execution_count": 12,
        "success_rate": 0.92,
        "last_executed": "2026-04-08T10:30:00Z",
        "source_episodes": ["episode_id_1", "episode_id_2"],
        "code_artifact": "skills/resolve_calendar_conflict.py"  # optional
      }
      ```
    - Store skill metadata in SQLite; code artifacts in a `skills/` directory.
    - Skills are versioned — new versions are created on modification, old versions retained for rollback.
  - The cognitive module (Phase 3) can query procedural memory to find relevant pre-built skills before constructing new plans from scratch.

- [ ] **4.1.5 — Build the memory controller**
  - Implement the centralized memory controller that mediates all memory access:
    - Routes writes to the appropriate memory tier.
    - Handles cross-tier queries (e.g., "find all relevant context for this task" → searches STM first, then semantic LTM, then episodic LTM).
    - Enforces access patterns: cognitive module reads from all tiers; reflex arc reads from procedural memory only; immune system reads from threat signature database only.
  - Expose the controller through event bus topics:
    - `agent/memory/query` → cross-tier retrieval
    - `agent/memory/stm/write` → STM writes
    - `agent/memory/ltm/consolidate` → trigger consolidation

#### Suggested File Structure

```
src/
  memory/
    __init__.py
    controller.py           # Central memory access mediator
    stm/
      __init__.py
      buffer.py             # Bounded, relevance-weighted ring buffer
    ltm/
      __init__.py
      episodic.py           # SQLite-backed chronological event log
      semantic.py           # ChromaDB/Valkey vector store wrapper
      procedural.py         # Skill library with versioned code artifacts
      embeddings.py         # Local embedding model (sentence-transformers)
    schemas/
      episodic_schema.sql
      procedural_schema.sql
  skills/                   # Versioned executable skill artifacts
    .gitkeep
```

#### Acceptance Criteria
- STM read latency < 1ms; write latency < 5ms.
- Semantic memory vector search returns top-10 results in < 50ms for a 100K-entry collection.
- Episodic memory supports efficient time-range and task-scoped queries.
- Procedural memory skills are executable by the cognitive module without manual intervention.
- Memory controller correctly routes cross-tier queries, prioritizing STM → Semantic → Episodic.

---

### Task 4.2: Implement the "Sleep" Consolidation Cycle

Build a background worker that consolidates STM into LTM during idle periods, implementing the three biological sleep phases.

**Objective:** When the agent goes idle (no active tasks, user disengaged) or when triggered by an endorphin signal, execute an asynchronous consolidation pipeline that replays STM data, extracts learnings, generates reusable skills, and prunes stale memories.

#### Sub-tasks

- [ ] **4.2.1 — Implement the consolidation trigger and scheduler**
  - The sleep cycle is triggered by:
    1. **Endorphin signal:** Published to `agent/endocrine/endorphin` after high-stress resolution (see Phase 5).
    2. **Idle timeout:** No cognitive escalations or user interactions for a configurable period (default: 15 minutes).
    3. **Manual trigger:** Published to `agent/sleep/trigger` by operator command.
  - The scheduler ensures consolidation does not run during active tasks. If a new escalation arrives during consolidation, the sleep cycle pauses and resumes when the escalation is resolved.

- [ ] **4.2.2 — Implement Slow Wave Sleep (SWS) — Failure Analysis**
  - Replay all STM entries from completed tasks that ended in failure or contained error events.
  - For each failure trace:
    - Use the **reasoning LLM** (via the Phase 3 cognitive model router) to extract a concise **negative constraint**: what went wrong, what action caused it, and what conditions it applies to.
    - **Rationale:** Failure analysis is safety-critical. SLMs produce vague or incorrect root-cause analysis. The consolidation cycle runs during idle time, so the LLM cost is amortized against downtime rather than competing with active user tasks. Batch multiple failure traces into a single LLM call where possible to reduce per-trace cost.
    - Store the constraint in a restrictive policy index:
      ```
      {
        "constraint_id": "uuid",
        "description": "Do not call calendar_api.delete without user confirmation",
        "trigger_pattern": {"tool": "calendar_api", "action": "delete", "missing": "user_confirm"},
        "source_episode": "episode_id",
        "confidence": 0.88,
        "created_at": "2026-04-09T03:00:00Z"
      }
      ```
    - The reflex arc (Phase 1) consults this policy index before allowing tool executions, adding a learned safety layer.
  - Publish SWS progress to `agent/sleep/sws`.

- [ ] **4.2.3 — Implement REM Sleep — Skill Abstraction**
  - Replay all STM entries from completed tasks that ended successfully.
  - For each successful task trace:
    - Use the **reasoning LLM** (via the Phase 3 cognitive model router) to analyze the action sequence and abstract it into a generalized, reusable skill definition.
    - **Rationale:** Skill generation is the highest-quality-bar task in the system. Code generated by SLMs (7B-13B) has an unacceptably low success rate (~20-30%). The reasoning LLM produces significantly better abstractions and executable code. Like SWS, this runs during idle time — the cost is justified because a single well-generated skill saves many future LLM calls.
    - Compare the new skill against existing procedural memory entries:
      - If a similar skill exists (cosine similarity > 0.85 on skill description embedding), merge by updating the confidence score and execution count.
      - If novel, create a new skill entry with an initial confidence score.
    - Generate executable Python code for the skill and **mandatory sandbox-test** it (Voyager pattern):
      - Write the code to a temporary sandbox directory.
      - Execute it with mock inputs in an isolated subprocess (use `bubblewrap` or `nsjail` for Linux sandboxing).
      - If tests pass, store in the `skills/` directory and register in procedural memory.
      - If tests fail, log the failure with the LLM's reasoning trace and discard the code. Do not retry immediately — failed skill traces feed back into the next SWS cycle for negative constraint extraction.
    - **Cost control:** Batch successful traces by similarity before LLM calls. If the daily consolidation token budget is exhausted, defer remaining REM processing to the next cycle.
  - Assign **Bayesian confidence scores** to newly generated skills:
    - Prior: based on the success rate of the source task type.
    - Updated with each subsequent execution (success → increase, failure → decrease).
  - Publish REM progress to `agent/sleep/rem`.

- [ ] **4.2.4 — Implement Synaptic Homeostasis and Pruning**
  - Run a decay and pruning pass over all LTM tiers:
    - **Episodic pruning:**
      - Apply the Ebbinghaus forgetting curve: memories not accessed within a configurable decay window (default: 30 days) have their relevance score reduced.
      - When relevance drops below a threshold (default: 0.1), archive the episodic entry to cold storage (compressed file) and remove from the active database.
    - **Semantic pruning:**
      - Identify contradictory facts (facts with overlapping subjects/predicates but different objects and similar confidence). Flag for review; auto-resolve if one has significantly higher confidence.
      - Merge near-duplicate fact embeddings (cosine similarity > 0.95) into a single entry with combined provenance.
    - **Procedural pruning:**
      - Skills with zero executions in the decay window and confidence below threshold are archived.
      - Skills with consistently low success rates (< 30% over 10+ executions) are demoted and flagged for review.
  - Use **Fisher-Rao Quantization-Aware Distance (FRQAD)** metrics to measure embedding quality degradation during compression. If compression would degrade retrieval quality beyond a threshold, retain the full embedding.
  - Publish pruning statistics to `agent/sleep/pruning`:
    ```
    {
      "episodic_pruned": 142,
      "episodic_archived": 38,
      "semantic_merged": 17,
      "semantic_contradictions_flagged": 3,
      "procedural_archived": 5,
      "total_storage_freed_mb": 12.4
    }
    ```

- [ ] **4.2.5 — Implement consolidation metrics and health monitoring**
  - Track consolidation cycle health over time:
    - Duration of each phase (SWS, REM, Pruning)
    - Number of new constraints extracted, skills generated, memories pruned
    - Memory storage growth rate
    - Skill library accuracy trend
  - Store metrics in episodic memory (meta-level: the agent remembers its own maintenance).
  - Alert (via `agent/immune/alert` at low severity) if consolidation consistently fails or if memory growth is unsustainable.

#### Suggested File Structure

```
src/
  memory/
    consolidation/
      __init__.py
      scheduler.py          # Idle detection, trigger handling, pause/resume
      sws.py                # Slow Wave Sleep: failure analysis → negative constraints
      rem.py                # REM: success abstraction → skill generation
      pruning.py            # Synaptic homeostasis: decay, merge, archive
      sandbox.py            # Isolated code execution for skill testing
      metrics.py            # Consolidation health tracking
    policies/
      negative_constraints.json   # Learned restrictive policies (from SWS)
  skills/                   # Versioned executable skill artifacts
```

#### Acceptance Criteria
- Consolidation cycle completes within 10 minutes for a typical day's worth of STM data (~500 entries). LLM calls account for the majority of this time.
- SWS extracts at least one negative constraint per failed task trace with high specificity (actionable, not generic).
- REM generates testable skill code for ≥ 50% of novel successful task patterns. Generated code must pass sandbox tests before admission to the skill library.
- Consolidation token spend is bounded by a configurable daily budget (separate from active-task budget).
- Pruning reduces episodic memory volume by ≥ 10% per cycle once the system has operated for > 30 days.
- Consolidation never blocks active cognitive processing (pauses on new escalations, LLM calls are cancellable).
- All consolidation phases publish progress events for operator visibility.

> **See also:** Negative constraints from SWS are consumed by the reflex arc's proprioceptive handler in [Phase 1 — Task 1.3](01-environment-nervous-system.md#task-13-configure-the-system-1-reflex-arc-instincts). Skill generation aligns with the neuroplasticity concept. Endorphin triggers originate from [Phase 5](05-active-inference-endocrine.md#task-52-map-endocrine-hooks-to-system-directives).

---

## Phase 4 Deliverables Summary

| Deliverable | Component | Key Technology |
| :--- | :--- | :--- |
| Short-Term Memory buffer | STM | In-memory ring buffer, relevance-weighted eviction |
| Episodic LTM store | LTM | SQLite, append-only log |
| Semantic LTM store | LTM | ChromaDB / Valkey, sentence-transformers embeddings |
| Procedural skill library | LTM | SQLite metadata + versioned Python artifacts |
| Central memory controller | Memory | Cross-tier query routing, access enforcement |
| SWS failure analysis | Consolidation | Reasoning LLM (via cognitive router), negative constraint extraction |
| REM skill abstraction | Consolidation | Reasoning LLM (via cognitive router), Voyager-pattern code generation, sandbox testing |
| Synaptic pruning | Consolidation | Ebbinghaus decay, FRQAD, embedding merge |
| Consolidation scheduler | Consolidation | Idle detection, endorphin trigger subscription |

---

## Dependencies

- **Upstream:** Phase 1 (event bus for memory topics, reflex arc consumes negative constraints), Phase 3 (cognitive module writes reasoning traces to STM, reads from all LTM tiers)
- **Downstream:** Phase 5 (endorphin signals trigger consolidation, dopamine signals reinforce procedural memory)
- **System Requirements:** SQLite (bundled with Python), ChromaDB or Valkey, sentence-transformers, reasoning LLM access (user-provided API key or capable local model via Ollama), sandbox runtime (bubblewrap/nsjail)
