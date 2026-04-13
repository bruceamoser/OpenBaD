# ---

**Phase 13: Circadian Rhythms, Endocrine Naps, and Temporal Architecture**

## **Purpose**

To establish a bifurcated temporal architecture for the OpenBaD daemon. This phase defines how the agent experiences time and schedules maintenance, strictly separating internal, stress-driven reflexes from external, clock-driven environments.

### **The Temporal Directives**

1. **The Heartbeat (Micro-Scheduling):** Must remain an internal asyncio loop within daemon.py. It provides necessary backpressure, dynamic pacing (Heart Rate Variability via Adrenaline/Cortisol), and SQLite lease awareness without OS-level I/O thrashing.  
2. **Circadian Rhythms (Macro-Scheduling):** Sleep and Wake cycles must be delegated to Linux systemd timers. The OS acts as the "environment," publishing absolute, drift-free chronological events to the MQTT bus to trigger state changes.  
3. **Endocrine Naps (Stress Reflex):** Naps must not be chronologically scheduled. They are an internal emergency reflex triggered by the EndocrineController when token or compute resources are exhausted.

## ---

**Task 1: Circadian CLI Triggers**

**Objective:** Provide a lightweight mechanism for the OS to inject chronological events into the OpenBaD nervous system without booting a full daemon context.

* \[ \] **1.1: Extend cli.py**  
  * Open src/openbad/cli.py.  
  * Create a new command group: openbad circadian.  
* \[ \] **1.2: Add Sleep Trigger**  
  * Implement openbad circadian sleep.  
  * Logic: Initialize NervousSystemClient, publish {"action": "sleep"} to system/circadian/event with QoS 1, and exit gracefully.  
* \[ \] **1.3: Add Wake Trigger**  
  * Implement openbad circadian wake.  
  * Logic: Initialize NervousSystemClient, publish {"action": "wake"} to system/circadian/event with QoS 1, and exit gracefully.

## **Task 2: OS-Level Cron (systemd Timers)**

**Objective:** Create the template configurations that allow the Linux kernel to drive the sleep schedule persistently across reboots.

* \[ \] **2.1: Sleep Services & Timers**  
  * Create config/openbad-sleep.service: A Type=oneshot service that executes the openbad circadian sleep CLI command.  
  * Create config/openbad-sleep.timer: Configured with a default OnCalendar=\*-\*-\* 02:00:00 (daily at 2:00 AM) and Persistent=true to ensure missed sleep (due to downtime) executes immediately on boot.  
* \[ \] **2.2: Wake Services & Timers**  
  * Create config/openbad-wake.service: Executes the openbad circadian wake CLI command.  
  * Create config/openbad-wake.timer: Configured with OnCalendar=\*-\*-\* 06:00:00 (daily at 6:00 AM) and Persistent=true.

## **Task 3: The Circadian FSM Reflex**

**Objective:** Wire the main daemon to react to the OS-level MQTT chron triggers.

* \[ \] **3.1: Subscribe to Environmental Time**  
  * In src/openbad/reflex\_arc/fsm.py, subscribe to the system/circadian/event topic.  
* \[ \] **3.2: Implement Sleep Transition**  
  * Upon receiving the "sleep" payload:  
    1. Call \_fsm.fire("sleep") to transition the agent state to SLEEPING.  
    2. Construct a TaskNode of type SYSTEM\_SLEEP with CRITICAL priority.  
    3. Inject the node into the SQLite Task DAG.  
    4. The active Heartbeat worker will pick up this lease, suspend standard background task execution, and execute src/openbad/memory/sleep/orchestrator.py (SWS, REM, Pruning).  
* \[ \] **3.3: Implement Wake Transition**  
  * Upon receiving the "wake" payload:  
    1. Call \_fsm.fire("wake") to transition the agent state back to AWAKE.  
    2. Publish a cancellation token/interrupt to the Heartbeat worker to terminate the sleep maintenance lease early if it is still running.

## **Task 4: Endocrine Naps**

**Objective:** Create a short-term, stress-based recovery reflex entirely decoupled from the OS clock.

* \[ \] **4.1: Implement the Nap Execution Logic**  
  * Create src/openbad/memory/sleep/nap.py.  
  * Write take\_nap(memory\_controller, endocrine\_controller).  
  * Logic: Extract the last N minutes of logs from stm.py, use a fast local SLM to summarize them into a 3-sentence "Highlight Reel," flush the raw logs from STM, and artificially decay Cortisol and Adrenaline levels back to baseline.  
* \[ \] **4.2: Wire the Endocrine Thresholds**  
  * In src/openbad/endocrine/controller.py, monitor Cortisol and Adrenaline aggregates.  
  * If stress metrics cross the critical threshold (e.g., 0.80), publish {"action": "nap"} to agent/endocrine/nap\_required.  
* \[ \] **4.3: The Nap FSM Reflex**  
  * In src/openbad/reflex\_arc/fsm.py, subscribe to agent/endocrine/nap\_required.  
  * Upon receipt, inject a SYSTEM\_NAP task into the SQLite DAG. The Heartbeat worker will execute nap.py, clear the context window, and immediately resume standard tasks.

## **Task 5: WUI Configuration Surface**

**Objective:** Expose the OS chron schedule to the operator via the web interface.

* \[ \] **5.1: Extend SystemManager**  
  * In src/openbad/wui/system\_manager.py (established in Phase 11), add methods to parse and overwrite the OnCalendar strings in the openbad-sleep.timer and openbad-wake.timer files.  
  * Ensure the manager executes systemctl \--user daemon-reload and systemctl \--user restart \<timer\> after any file modifications.  
* \[ \] **5.2: Create Frontend Controls**  
  * In wui-svelte/src/routes/health/+page.svelte (or a dedicated settings page), add a "Circadian Rhythms" card.  
  * Provide time-picker inputs for "Bedtime" and "Wake Time".  
  * Bind these inputs to the backend API to dynamically rewrite the systemd timer configurations.  
  * Add visual indicators showing the live systemd ActiveState and NextElapseUSec (time until next sleep/wake).

## ---

**Definition of Done**

Phase 12 is complete when:

1. The openbad circadian sleep CLI command successfully pushes the agent into the SLEEPING state and triggers the heavy memory orchestration task.  
2. The systemd timers reliably fire these CLI commands based on the system clock.  
3. The operator can successfully alter the daily sleep schedule from the Svelte WUI without touching terminal files.  
4. A high Cortisol state natively triggers a short SYSTEM\_NAP task that clears the STM buffer without requiring a clock-based schedule.  
5. The core Python Heartbeat loop is definitively relieved of watching the time of day, polling only for task leases and dynamic pacing.