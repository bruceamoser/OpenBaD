Here is the detailed, discrete technical specification for integrating the Level 1 and Level 2 built-in skills into the OpenBaD heartbeat architecture. This spec targets the current state of your src/openbad/ directory and aligns with the separation of the autonomic daemon.py process and the interactive wui/server.py process.

# ---

**Specification: OpenBaD Built-In Capabilities (Phase 9+ Tools)**

## **1\. Level 1: File System Operations (read\_file, write\_file)**

**Objective:** Provide in-process, trusted file manipulation governed by the immune system and disk I/O interoception.

* \[ \] **Task 1.1: Create FS Tool Module**  
  * Create src/openbad/toolbelt/fs\_tool.py.  
  * Implement read\_file(path) and write\_file(path, content) functions.  
* \[ \] **Task 1.2: Immune System Gating**  
  * Update src/openbad/immune\_system/rules\_engine.py to include a FileOperationRule.  
  * Define restricted path patterns (e.g., /etc/, \~/.ssh/, system binaries). If write\_file attempts to target these, trigger src/openbad/immune\_system/interceptor.py to block the action and flag the payload.  
* \[ \] **Task 1.3: Endocrine Throttling Integration**  
  * In fs\_tool.py, import interoception.disk\_network.  
  * Before executing a large read/write, check disk I/O metrics. If the baseline is saturated (high Cortisol state), yield the TaskNode lease back to the heartbeat scheduler with a DEFERRED\_RESOURCES status.

## **2\. Level 1: Command Line Execution (exec\_command)**

**Objective:** Allow shell execution within strict quarantine boundaries.

* \[ \] **Task 2.1: Refactor cli\_tool.py for Asynchronous Task DAG**  
  * Modify src/openbad/toolbelt/cli\_tool.py to ensure it does not run blocking subprocess.run calls directly in the main thread. It must use asyncio.create\_subprocess\_shell yielding back to the daemon.py event loop.  
* \[ \] **Task 2.2: The Quarantine Gate**  
  * Hook cli\_tool.py execution into src/openbad/immune\_system/rules\_engine.py.  
  * If a destructive signature (e.g., rm \-rf, mkfs, chmod \-R 777 /) is detected, do not execute.  
  * Update the SQLite task DAG state to QUARANTINED and use nervous\_system/client.py to publish an alert to agent/immune/alert for the WUI to display.

## **3\. Level 1: Web Information (web\_search, web\_fetch)**

**Objective:** Fast, stateless external data gathering for Active Inference to reduce surprise.

* \[ \] **Task 3.1: Expand Web Search Module**  
  * Open src/openbad/toolbelt/web\_search.py. Ensure both a web\_search(query) and a web\_fetch(url) function exist (fetching raw HTML/Markdown of a single page).  
* \[ \] **Task 3.2: Research Queue Escalation Bridge**  
  * Wrap web\_fetch execution in a try/except block.  
  * If the fetch returns a 404, 403, or times out, catch the exception.  
  * Instead of failing the node, program the worker to suspend the current TaskNode and push a ResearchNode onto src/openbad/active\_inference/insight\_queue.py to investigate the broken link/concept autonomously.

## **4\. Dual-Mode Communication (ask\_user)**

**Objective:** A context-aware tool that behaves synchronously during active chat, but asynchronously via MQTT when the user is disconnected.

* \[ \] **Task 4.1: Track WUI Presence**  
  * Modify src/openbad/wui/bridge.py and server.py to manage a UserSession singleton.  
  * Set UserSession.is\_active \= True when a WebSocket is connected, and track the timestamp of the last inbound message from chat\_pipeline.py.  
  * Expose this presence state to the MQTT broker (e.g., topic system/wui/presence).  
* \[ \] **Task 4.2: Implement ask\_user.py**  
  * Create src/openbad/toolbelt/ask\_user.py.  
  * **Mode A (Active):** If presence is True, publish question to agent/chat/response and await a reply from agent/chat/inbound with a short timeout.  
  * **Mode B (Inactive):** If presence is False, update the SQLite TaskNode status to BLOCKED\_ON\_USER. Publish the payload to agent/escalation. Yield the lease immediately so daemon.py can move to the next task.  
* \[ \] **Task 4.3: Re-Engagement Hook in WUI**  
  * Modify src/openbad/reflex\_arc/chat\_activator.py.  
  * On a new WebSocket connection (user login), query the SQLite database for any TaskNode in the BLOCKED\_ON\_USER state.  
  * Push these pending questions to the WUI chat feed *before* the standard greeting. Once answered, publish to agent/chat/inbound to unblock the daemon.

## **5\. Level 2: Isolated MCP Bridge (browser & External SaaS)**

**Objective:** Keep heavy/complex tools out of the ambient context window and load them only when specifically requested by a task node.

* \[ \] **Task 5.1: Create the MCP Execution Sandbox**  
  * Create a new directory: src/openbad/toolbelt/mcp\_bridge/.  
  * Implement an mcp\_runner.py that can dynamically load an MCP server schema (e.g., standard Playwright browser or GitHub).  
* \[ \] **Task 5.2: Scope Browser Context**  
  * Wrap the existing CDP DOM logic (src/openbad/sensory/vision/cdp\_dom.py) into this transient bridge.  
  * Ensure the browser session spins up *only* when daemon.py executes a TaskNode explicitly tagged for the browser capability, and tears down completely when the task node finishes.  
* \[ \] **Task 5.3: Interoceptive Governor**  
  * Before launching the isolated MCP bridge, query src/openbad/interoception/monitor.py for RAM and Thermal states.  
  * If system limits are breached, refuse the tool execution, defer the task, and trigger the Adrenaline hook (if the task priority is CRITICAL) or the Cortisol hook (to force hibernation/cooldown).

## **6\. Autonomic Memory Migration**

**Objective:** Remove explicit "Memory Search" tool overhead to save tokens and mimic biological reflex.

* \[ \] **Task 6.1: Deprecate Explicit Memory Tool**  
  * Remove memory\_search schemas from the default tool registry passed to the LLM in src/openbad/cognitive/model\_router.py.  
* \[ \] **Task 6.2: Ensure Autonomic Context Injection**  
  * Verify src/openbad/cognitive/event\_loop.py handles context enrichment.  
  * Before sending a prompt payload to model\_router.py, the event loop must natively query src/openbad/memory/semantic.py (and Muninn graph if connected) using the prompt's intent vector, automatically prepending the relevant facts to the context window behind the scenes.