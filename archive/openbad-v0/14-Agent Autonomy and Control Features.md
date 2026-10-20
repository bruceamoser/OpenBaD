# **Phase 14: Curiosity Drive, Endocrine Workload Management, and Executive Override**

## **Purpose**

To transform OpenBaD from a strictly reactive execution engine into an autonomous, self-directed organism. This phase implements the **Curiosity Drive** (allowing the agent to invent its own background research tasks when idle), **Endocrine Provider Management** (simulating biological pain/stress when API providers fail), and the **WUI Task Manager** (giving the human operator an Executive Override to pause or kill cognitive processes).

## ---

**Task 1: The Curiosity Drive (Epistemic Foraging)**

**Objective:** Enable the agent to detect when it is completely idle and securely generate intrinsic tasks to improve its own Long-Term Memory (LTM) or Library without burning unnecessary compute.

* \[ \] **1.1: The Idle Detection Trigger**  
  * Update src/openbad/daemon.py (Heartbeat worker).  
  * Add an idle\_ticks counter. If the heartbeat queries the SQLite Task DAG and finds 0 pending tasks, increment the counter.  
  * If idle\_ticks exceeds the configured threshold (e.g., 5 consecutive heartbeats / 15 minutes) AND the Endocrine state is at baseline (low Cortisol/Adrenaline), publish {"action": "forage"} to the MQTT broker.  
* \[ \] **1.2: FSM Transition**  
  * In src/openbad/reflex\_arc/fsm.py, subscribe to the forage event. Transition state to FORAGING.  
* \[ \] **1.3: Epistemic Foraging Logic**  
  * Create src/openbad/cognitive/foraging.py.  
  * **Memory Sampling:** Query the Semantic Memory Graph or Library for 3–5 random, disconnected nodes/books.  
  * **Synthesis:** Pass these nodes to the primary local model with the System 2 daydream prompt: *"Review these concepts. Identify logical gaps or intersections. Formulate a single, concrete research question or maintenance task to improve our knowledge base."*  
  * **Task Generation:** Parse the LLM output and insert a new TaskNode into the SQLite DAG with a priority of BACKGROUND.  
  * Reset idle\_ticks to 0 and transition the FSM back to AWAKE.

## **Task 2: Endocrine Provider Management (Pain & Exhaustion)**

**Objective:** Replace complex failover routing with a biological response to failure. If a provider is busy, the agent waits. If a provider fails, the agent experiences stress and eventually shuts down the system to prevent infinite error loops.

* \[ \] **2.1: The Busy State (Queueing)**  
  * Update src/openbad/wui/chat\_pipeline.py and daemon.py.  
  * If the daemon picks up a compute-bound task (e.g., Epistemic Foraging), publish an MQTT retained message to system/cognitive/status with the payload {"status": "ENGAGED", "task\_id": X}.  
  * If a user chat message arrives while ENGAGED, do not interrupt the local model. Queue the message in the SQLite DAG with CRITICAL priority. The agent will read it naturally on its next heartbeat lease.  
* \[ \] **2.2: Provider Failure Hooks**  
  * In src/openbad/cognitive/providers/base.py, wrap inference requests in a timeout/try-catch block.  
  * On TimeoutError or 500 Server Error, publish a failure event to agent/endocrine/pain.  
* \[ \] **2.3: Cortisol Escalation and Shutdown**  
  * In src/openbad/endocrine/controller.py, catch the pain event and apply a massive spike to the Cortisol metric.  
  * If Cortisol crosses the critical threshold (e.g., 0.90), publish an agent/endocrine/nap\_required event (triggering Phase 12 Nap logic to clear the context) and mark the specific provider as DEGRADED in memory.  
  * The agent will refuse to attempt tasks requiring that provider until Cortisol decays or the operator intervenes.

## **Task 3: The WUI Task Manager (Executive Override)**

**Objective:** Expose the internal SQLite Task DAG to the operator via a frontend interface, allowing the human to forcibly interrupt, kill, or reprioritize the agent's internal monologue.

* \[ \] **3.1: WUI Backend API Extensions**  
  * In src/openbad/wui/server.py, add endpoints to interact directly with the Task DAG:  
    * GET /api/tasks (Returns active/queued tasks).  
    * POST /api/tasks/{id}/suspend (Sets status to SUSPEND\_REQUESTED).  
    * POST /api/tasks/{id}/abort (Sets status to ABORT\_REQUESTED).  
    * POST /api/tasks/{id}/reprioritize (Changes priority ENUM).  
* \[ \] **3.2: MQTT Interrupt Pulse**  
  * When suspend or abort is called via the API, the WUI Server must publish an urgent MQTT message: system/heartbeat/interrupt with payload {"task\_id": X, "action": "abort"}.  
* \[ \] **3.3: Svelte Frontend Component (/tasks)**  
  * Create wui-svelte/src/routes/tasks/+page.svelte.  
  * Build a list view reading from /api/tasks.  
  * Include contextual details: Task Origin (User vs. Curiosity Drive), Token Usage, Status.  
  * Add interactive Action Buttons (Pause, Kill, Bump Priority) mapped to the new API endpoints. Visually indicate an ABORTING... transition state while waiting for the GPU to release.

## **Task 4: The Heartbeat Interrupt Handler**

**Objective:** Ensure the Python daemon can cleanly catch the Executive Override and drop what it is doing without corrupting the database.

* \[ \] **4.1: Asyncio Task Cancellation**  
  * In src/openbad/daemon.py, ensure the function executing the current task lease is wrapped in an asyncio.Task.  
  * Create an MQTT listener on the system/heartbeat/interrupt topic.  
  * If an interrupt is received matching the currently running task\_id, invoke current\_task.cancel().  
* \[ \] **4.2: Graceful Yielding**  
  * In the worker logic, catch the asyncio.CancelledError.  
  * Check the SQLite database for the requested state (SUSPEND\_REQUESTED vs ABORT\_REQUESTED).  
  * If Abort: Mark task as DISCARDED, clear the STM buffer, and yield the lease.  
  * If Suspend: Save the current context/progress back to the SQLite TaskNode payload, mark as SUSPENDED, and yield the lease.  
  * Immediately proceed to the next heartbeat tick to pick up the pending User Chat or High Priority task.