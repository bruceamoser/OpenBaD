# **Phase 12: Peripheral Transducers and Systemd Orchestration**

## **Purpose**

To integrate persistent external communications (e.g., Discord, Slack, Webhooks) into OpenBaD without compromising the autonomic heartbeat loop. Rather than building blocking "Gateway" tools that starve the execution DAG, external integrations will be built as **Peripheral Transducers**—isolated, long-lived background microservices.

These transducers will be dynamically managed by the WUI as Linux systemd user services, ensuring high availability, crash recovery, and strict process isolation. They communicate with the core cognitive daemon exclusively via the MQTT nervous system.

## **Goals**

1. Establish a native OS-level service manager within the WUI to generate and control systemd services dynamically.  
2. Implement a base Transducer architecture for persistent external connections.  
3. Build a universal Level 1 egress tool (transmit\_message) allowing the cognitive engine to route outbound messages via MQTT without knowing specific API protocols.  
4. Create a comprehensive WUI frontend (/transducers) for operators to configure, enable, and monitor active sensory peripherals.

## ---

**Task 1: The WUI Systemd Manager**

**Objective:** Grant the WUI server the ability to securely manage Linux user services for dynamic peripherals.

* \[ \] **1.1: Create SystemManager Module**  
  * Create src/openbad/wui/system\_manager.py.  
  * Implement an asynchronous class utilizing subprocess to execute systemctl \--user commands.  
  * Required methods: enable\_service(name, config), disable\_service(name), get\_status(name), and stream\_logs(name).  
* \[ \] **1.2: Dynamic Unit File Generation**  
  * Implement a template engine within system\_manager.py that writes .service files directly to \~/.config/systemd/user/.  
  * Ensure the templates include After=openbad-broker.service and Restart=always to guarantee resilience.  
* \[ \] **1.3: Expose API Endpoints**  
  * In src/openbad/wui/server.py, expose REST/WebSocket endpoints for the Svelte frontend to interface with the SystemManager (e.g., POST /api/transducers/enable, GET /api/transducers/status).

## **Task 2: Transducer Base Architecture & Implementations**

**Objective:** Create the isolated microservices that translate external protocols into OpenBaD's internal MQTT events.

* \[ \] **2.1: Establish the Peripherals Directory**  
  * Create the src/openbad/peripherals/ namespace.  
  * Create base\_transducer.py. This class must inherit or utilize src/openbad/nervous\_system/client.py to establish a persistent MQTT connection on boot.  
* \[ \] **2.2: Implement Liveness & Health Pings**  
  * Ensure base\_transducer.py publishes a retained {"status": "online"} payload to system/transducers/{platform}/health upon successful external connection.  
  * Establish a graceful shutdown hook that publishes {"status": "offline"} upon exit.  
* \[ \] **2.3: Build the First Transducer (Discord/Slack Example)**  
  * Create src/openbad/peripherals/discord\_transducer.py (or target platform).  
  * **Ingress:** Listen to the external WebSocket. On new message, format to OpenBaD Protobuf schema and publish to sensory/external/discord/inbound.  
  * **Egress:** Subscribe to motor/external/discord/outbound. Upon receiving an MQTT payload, transmit it over the external WebSocket.

## **Task 3: Universal Level 1 Egress Tool**

**Objective:** Give the core daemon a single, abstract capability to speak to the outside world without context window bloat.

* \[ \] **3.1: Create transmit\_message.py**  
  * Create src/openbad/toolbelt/transmit\_message.py.  
  * Define the schema for the cognitive router: transmit\_message(platform: str, target\_id: str, content: str).  
* \[ \] **3.2: Heartbeat Execution Logic**  
  * Ensure this tool operates on a standard heartbeat lease.  
  * The tool logic simply formats the payload and publishes it to the MQTT broker at motor/external/{platform}/outbound. It does not handle HTTP requests or external API limits.  
* \[ \] **3.3: Register as Trusted Capability**  
  * Add transmit\_message to the default Level 1 capability registry so it is always available to the CognitiveEventLoop.

## **Task 4: Autonomic Ingress Routing**

**Objective:** Ensure the core daemon reacts to incoming peripheral signals correctly.

* \[ \] **4.1: Extend Active Inference Hooks**  
  * Modify src/openbad/active\_inference/engine.py or the FSM (src/openbad/reflex\_arc/fsm.py) to subscribe to sensory/external/+/inbound.  
  * When an external payload arrives, push it to Short-Term Memory (stm.py) and trigger a surprise/interest calculation to determine if a reactive TaskNode should be generated.

## **Task 5: WUI Frontend Surface (/transducers)**

**Objective:** Provide a visual catalog and control panel for the operator.

* \[ \] **5.1: Create the Transducers Route**  
  * Generate wui-svelte/src/routes/transducers/+page.svelte.  
* \[ \] **5.2: Build the Catalog Component**  
  * Create a grid UI displaying available/supported peripherals (dynamically pulled from the src/openbad/peripherals/ directory manifests).  
* \[ \] **5.3: Configuration Modal**  
  * Implement a modal that prompts for necessary API tokens/keys when activating a peripheral.  
  * Ensure the submitted configuration is saved securely to data/config/peripherals/ before the systemctl launch command is issued.  
* \[ \] **5.4: Live Status & Log Streaming**  
  * Bind the UI to the MQTT system/transducers/+/health topics to display glowing green/red status dots in real-time.  
  * Add a "View Logs" button that streams the journalctl \--user \-u openbad-peripheral-{name} output directly into a Svelte terminal component for easy debugging.

## **Definition of Done**

Phase 11 is complete when:

1. A user can input a bot token in the WUI, resulting in a live, OS-managed background process.  
2. An external message to that bot appears seamlessly on the OpenBaD internal MQTT bus.  
3. The OpenBaD daemon can use the transmit\_message Level 1 tool to successfully reply to the external platform.  
4. Restarting the WUI or the core daemon does not cause the external transducer process to disconnect or drop messages.