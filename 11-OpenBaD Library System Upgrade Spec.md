# **Specification: Phase 11 \- The Exocortex (Library System)**

## **1\. System Objective**

To establish a structured, persistent Long-Term Storage archive (The Exocortex) that operates distinctly from the Semantic LTM. The Library holds exhaustive documentation chunked into vector embeddings, while Semantic Memory stores abstract facts and pointer references (the "Card Catalog") to the Library. The daemon autonomously maintains, searches, and drafts books via Level 1 heartbeat tools.

## ---

**2\. Storage Architecture (library.db)**

**Location:** data/library/

**Technology:** SQLite (Relational Hierarchy) \+ LanceDB / sqlite-vec (In-Process Vector Store).

### **2.1 Relational Schema (SQLite)**

Create src/openbad/library/db.py to manage the relational hierarchy.

* **libraries**: id, name, description, created\_at (e.g., "Hardware", "Software").  
* **shelves**: id, library\_id, name, description (e.g., "Pendant Project", "Omniscient Forge").  
* **sections**: id, shelf\_id, name (e.g., "Firmware", "API Specs").  
* **books**: id, section\_id, title, summary, author (User or Daemon), last\_updated\_at.  
* **book\_edges**: source\_book\_id, target\_book\_id, relation\_type (ENUM: supersedes, relies\_on, contradicts, references).

### **2.2 Vector Storage (Chunks)**

* **chunks (Vector Table):** id, book\_id, chunk\_index, text\_content, vector (FLOAT ARRAY).  
* **Chunking Strategy:** Implement src/openbad/library/embedder.py using Recursive Character Text Splitting (chunk size \~500 tokens, 50 token overlap).

## ---

**3\. Cognitive Provider Extension (Embeddings)**

**Target:** src/openbad/cognitive/providers/ollama.py

* **Action:** Extend the existing Ollama provider (or base provider class) to support a native .embed(text: str) \-\> list\[float\] method.  
* **Model:** Configure cognitive.yaml to include a default embedding model (e.g., nomic-embed-text or mxbai-embed-large) strictly for local, cost-free vector generation.

## ---

**4\. Level 1 Skill Integration (library\_tool.py)**

**Target:** src/openbad/toolbelt/library\_tool.py

**Execution:** These run as TaskNodes on the daemon.py heartbeat lease.

* **search\_library(query: str, shelf\_id: Optional\[int\])**:  
  * Embeds the query.  
  * Performs Cosine Similarity search against the vector store.  
  * Returns the top 5 matching text chunks alongside their parent book\_id and title.  
* **read\_book(book\_id: int)**:  
  * Retrieves the full text/summary of a specific book if the STM requires exhaustive context.  
* **draft\_book(section\_id: int, title: str, content: str)**:  
  * Creates a new Book record.  
  * *Crucially:* Automatically chunks content and generates embeddings in the background to avoid blocking the cognitive router.  
* **link\_books(source\_id: int, target\_id: int, relation\_type: str)**:  
  * Allows the LLM to autonomously build the citation graph (e.g., marking an old spec as superseded).

## ---

**5\. The Memory Bridge (Semantic Pointers)**

**Target:** src/openbad/memory/semantic.py & data/memory/semantic.json

* **Schema Update:** Modify the Semantic Node schema to include a library\_refs: list\[int\] array.  
* **Context Injection:** Update src/openbad/cognitive/event\_loop.py. When retrieving semantic context for the working prompt, the loop must append the pointer references.  
  * *Format injected to prompt:* \[Knowledge Node: ESP32 I2S Setup. Detail: Handles audio. Exhaustive documentation available in Library Book ID: 104\].

## ---

**6\. Autonomic Maintenance (Reconciliation Loop)**

**Target:** src/openbad/active\_inference/reconciliation.py (New File)

* **The Trigger:** When ExplorationEngine registers a high-surprise fact update that contradicts an existing semantic node, it checks if that node has library\_refs.  
* **The Task Generation:** If refs exist, it pushes a LibraryReconciliationTask to the SQLite Task DAG.  
* **The Heartbeat Execution:**  
  1. daemon.py picks up the task.  
  2. Loads the old book chunk and the new fact.  
  3. Prompts the System 2 reasoning engine: *"Rewrite this section to incorporate the new fact."*  
  4. Saves the updated text, re-embeds the chunk, and updates the last\_updated\_at timestamp.

## ---

**7\. Web UI (WUI) Surfaces**

**Target:** src/openbad/wui/server.py & wui-svelte/src/routes/library/

### **7.1 Backend API (WUI Server)**

Add new HTTP REST endpoints or WebSocket RPC handlers to server.py to serve the frontend without exposing the raw database:

* GET /api/library/tree (Returns nested JSON of Libraries \-\> Shelves \-\> Sections \-\> Books).  
* GET /api/library/book/{id} (Returns full book text and metadata).  
* POST /api/library/search (Accepts a string, returns vector search results).

### **7.2 Svelte Frontend Components**

Create a new route: wui-svelte/src/routes/library/+page.svelte.

* **Component 1: The Archive Browser (Sidebar)**  
  * A collapsible accordion/tree-view component mapping the hierarchy.  
  * Visual indicators for "Author" (e.g., a robot icon if drafted by the Daemon, a user icon if drafted by the Operator).  
* **Component 2: Semantic Search Bar (Top Nav)**  
  * A search input that pings the POST /api/library/search endpoint.  
  * Displays results not just by exact match, but by semantic relevance, highlighting the specific chunk snippet.  
* **Component 3: Book Viewer / Editor (Main Panel)**  
  * Renders the markdown content of the selected Book.  
  * Shows metadata tags: Last Updated By: OpenBaD Autonomic Loop (2 hours ago).  
  * Shows an "Edges" visualizer or list: *"This book supersedes \[Book ID 42\]"*.  
  * Includes a "Manual Edit" button allowing the user to forcefully overwrite daemon-generated text.

## ---

**Implementation Checklist**

### **Database & Embeddings**

* \[ \] Initialize library.db and implement SQLite relational schema.  
* \[ \] Integrate local vector store (LanceDB or sqlite-vec).  
* \[ \] Update ollama.py provider to support .embed().  
* \[ \] Write text chunking utility in embedder.py.

### **Toolbelt & Memory**

* \[ \] Create library\_tool.py with search, read, draft, and link capabilities.  
* \[ \] Register library\_tool.py as a Level 1 trusted capability.  
* \[ \] Update semantic.json schema to accept library\_refs.  
* \[ \] Modify event\_loop.py to expose Book ID pointers to the LLM context.

### **Autonomic Engine**

* \[ \] Create LibraryReconciliationTask type in the SQLite Task DAG.  
* \[ \] Write the reconciliation prompt/logic triggered by Active Inference updates.

### **Frontend WUI**

* \[ \] Implement /api/library/\* data bridges in server.py.  
* \[ \] Build wui-svelte/src/routes/library/+page.svelte layout.  
* \[ \] Implement the Archive Tree Browser component.  
* \[ \] Implement the Book Viewer and Graph Edges UI.  
* \[ \] Compile Svelte frontend and test integration with the WUI server.