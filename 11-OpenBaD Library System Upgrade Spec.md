# **Specification: Phase 11 — The Exocortex (Library System)**

## **1. System Objective**

Establish a structured, persistent Long-Term Storage archive (The Exocortex) that operates distinctly from the existing four-tier Memory hierarchy (STM → Episodic → Semantic → Procedural). The Library holds exhaustive documentation chunked into vector embeddings inside the consolidated `state.db`, while Semantic Memory (`data/memory/semantic.json`) stores abstract facts and pointer references (the "Card Catalog") to the Library. The daemon autonomously maintains, searches, and drafts books via heartbeat-dispatched scheduler tasks.

---

## **2. Storage Architecture**

**Location:** Consolidated into the existing `state.db` (managed by `src/openbad/state/db.py`).

**Technology:** SQLite relational hierarchy (new tables in state.db) + sqlite-vec extension for in-process vector storage (no external services).

### **2.1 Relational Schema (New Migration)**

Create `src/openbad/state/migrations/0006_library.sql` to add the library hierarchy tables. Follow the existing migration runner pattern — `initialize_state_db()` discovers and applies numbered `.sql` files from `src/openbad/state/migrations/` and tracks them in `schema_migrations`.

Tables:

- **libraries**: `library_id TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `description TEXT`, `created_at REAL NOT NULL`.
  - Top-level categories (e.g., "Hardware", "Software", "Research Archive").
- **shelves**: `shelf_id TEXT PRIMARY KEY`, `library_id TEXT NOT NULL REFERENCES libraries`, `name TEXT NOT NULL`, `description TEXT`, `created_at REAL NOT NULL`.
  - Project-level groupings (e.g., "Pendant Project", "Omniscient Forge").
- **sections**: `section_id TEXT PRIMARY KEY`, `shelf_id TEXT NOT NULL REFERENCES shelves`, `name TEXT NOT NULL`, `created_at REAL NOT NULL`.
  - Topic-level subdivisions (e.g., "Firmware", "API Specs").
- **books**: `book_id TEXT PRIMARY KEY`, `section_id TEXT NOT NULL REFERENCES sections`, `title TEXT NOT NULL`, `summary TEXT`, `content TEXT NOT NULL`, `author TEXT NOT NULL DEFAULT 'system'` (values: `'user'` or `'system'`), `created_at REAL NOT NULL`, `updated_at REAL NOT NULL`.
- **book_edges**: `source_book_id TEXT NOT NULL REFERENCES books`, `target_book_id TEXT NOT NULL REFERENCES books`, `relation_type TEXT NOT NULL CHECK(relation_type IN ('supersedes','relies_on','contradicts','references'))`, `PRIMARY KEY (source_book_id, target_book_id)`.

All IDs are UUIDs (text), consistent with the existing `tasks`, `research_nodes`, etc. tables.

### **2.2 Vector Storage (Chunks)**

Add chunk tables to the same `0006_library.sql` migration:

- **book_chunks**: `chunk_id TEXT PRIMARY KEY`, `book_id TEXT NOT NULL REFERENCES books ON DELETE CASCADE`, `chunk_index INTEGER NOT NULL`, `text_content TEXT NOT NULL`, `created_at REAL NOT NULL`.
- **book_chunk_vectors**: Managed by sqlite-vec — a virtual table storing the float vectors, keyed by `chunk_id`. Created via `CREATE VIRTUAL TABLE book_chunk_vectors USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[768])` (dimension depends on chosen embedding model).

**Chunking Strategy:** Implement `src/openbad/library/embedder.py` using Recursive Character Text Splitting (chunk size ~500 tokens, 50-token overlap). Use the existing `chars / 4` token heuristic from `src/openbad/cognitive/context_manager.py` for sizing.

### **2.3 Data Access Layer**

Create `src/openbad/library/store.py`:

- `LibraryStore(conn: sqlite3.Connection)` — CRUD for the relational hierarchy.
- Methods: `create_library()`, `create_shelf()`, `create_section()`, `create_book()`, `update_book()`, `get_book()`, `get_tree()`, `link_books()`, `search_chunks()`.
- All chunk embedding and vector search operations go through this class.
- Pattern: same raw SQL + dataclass conversion used by `TaskStore` and `ResearchQueue`.

---

## **3. Embedding Provider Extension**

### **3.1 Provider Base Class Extension**

**Target:** `src/openbad/cognitive/providers/base.py`

Add an optional `embed()` method to `ProviderAdapter`:

```python
async def embed(self, texts: list[str], model_id: str | None = None) -> list[list[float]]:
    raise NotImplementedError("Embedding not supported by this provider")
```

### **3.2 Ollama Embedding Support**

**Target:** `src/openbad/cognitive/providers/ollama.py`

Extend `OllamaProvider` to implement `embed()` by calling Ollama's `POST /api/embed` endpoint:

```python
async def embed(self, texts: list[str], model_id: str | None = None) -> list[list[float]]:
    model = model_id or self._embedding_model
    async with self._session.post(f"{self._base_url}/api/embed",
        json={"model": model, "input": texts}) as resp:
        data = await resp.json()
        return data["embeddings"]
```

### **3.3 Configuration**

**Target:** `config/cognitive.yaml`

Add an `embedding` section alongside the existing `providers` and `systems` blocks:

```yaml
embedding:
  provider: ollama
  model: nomic-embed-text    # or mxbai-embed-large
  dimensions: 768
```

This keeps embedding routing separate from the `ModelRouter` fallback chains (embeddings have no priority/cortisol semantics).

### **3.4 Semantic Memory Upgrade**

**Target:** `src/openbad/memory/semantic.py`

Replace the default `hash_embedding()` fallback (64-dim deterministic hash vectors) with the Ollama embedding provider. The existing `EmbeddingFn = Callable[[str], list[float]]` type alias and `cosine_similarity()` function remain unchanged — only the default function wired in `MemoryController.__init__()` changes.

---

## **4. FastMCP Skill Integration**

**Target:** `src/openbad/skills/library_tool.py` (new file)

Register library skills on the existing `skill_server` FastMCP instance using `@skill_server.tool()` decorators. Follow the same patterns used in `fs_tool.py`, `web_search.py`, and `memory_tool.py`.

### **4.1 Skills**

```python
@skill_server.tool()
async def search_library(query: str, shelf_id: str | None = None) -> str:
    """Search the Library for documentation matching a query.
    Returns the top 5 matching text chunks with their parent book title and ID."""

@skill_server.tool()
async def read_book(book_id: str) -> str:
    """Read the full content and metadata of a specific Library book."""

@skill_server.tool()
async def draft_book(section_id: str, title: str, content: str) -> str:
    """Create a new book in the Library. Content is automatically chunked and embedded."""

@skill_server.tool()
async def link_books(source_id: str, target_id: str, relation_type: str) -> str:
    """Create a citation edge between two books.
    relation_type must be one of: supersedes, relies_on, contradicts, references."""
```

### **4.2 Registration**

Import the module in `src/openbad/skills/server.py` alongside the other skill imports:

```python
from openbad.skills import library_tool  # noqa: F401 — registers @skill_server.tool() decorators
```

This makes the tools automatically available via `get_openai_tools()` and `call_skill()` — no manual schema or separate registration needed.

### **4.3 Background Embedding**

`draft_book()` must not block the cognitive router while embedding. Strategy:
- Write the book and chunk text synchronously (fast).
- Enqueue embedding generation as an `asyncio.create_task()` that writes vectors to sqlite-vec on completion.
- The book is immediately searchable by title/metadata; full vector search becomes available once embedding completes.

---

## **5. The Memory Bridge (Semantic Pointers)**

### **5.1 Schema Update**

**Target:** `src/openbad/memory/base.py`

Extend the `MemoryEntry.metadata` dict convention. Semantic entries that reference Library books include a `"library_refs"` key:

```python
metadata={"library_refs": ["book-uuid-1", "book-uuid-2"]}
```

No schema migration needed — `metadata` is already a `dict[str, Any]` stored in `semantic.json`.

### **5.2 Context Injection**

**Target:** `src/openbad/memory/controller.py` (the `MemoryController` unified search path)

When `recall()` returns semantic entries with `library_refs` in their metadata, append pointer annotations to the result text:

```
[Knowledge Node: ESP32 I2S Setup. Detail: Handles audio.
 Exhaustive documentation available in Library Book ID: <uuid>]
```

This keeps the bridge lightweight — the LLM can then call `read_book()` or `search_library()` if it needs the full content.

---

## **6. Autonomic Maintenance (Reconciliation Loop)**

**Target:** `src/openbad/active_inference/reconciliation.py` (new file)

### **6.1 The Trigger**

When `ExplorationEngine.run_cycle()` (in `src/openbad/active_inference/engine.py`) registers a high-surprise observation that updates or contradicts an existing semantic node, the engine checks if that node's `metadata` contains `library_refs`.

### **6.2 Task Generation**

If refs exist, push a system task to the SQLite Task DAG (`TaskStore`) with:
- `kind = "system"`
- `title = "Library Reconciliation: <book_title>"`
- A single `task_node` of type `"reconcile"` referencing the book_id and the new fact.

### **6.3 Heartbeat Execution**

The `SchedulerWorker` (in `src/openbad/autonomy/scheduler_worker.py`) picks up the task during a heartbeat tick:

1. Loads the relevant book chunk from `LibraryStore`.
2. Loads the new semantic fact.
3. Sends a `CognitiveRequest` with `system=REASONING` and `priority=MEDIUM`:
   *"Rewrite this section to incorporate the new fact. Preserve existing accurate content."*
4. Saves the updated text via `LibraryStore.update_book()`.
5. Re-chunks and re-embeds the modified content.
6. Updates `books.updated_at` timestamp.

This integrates with the existing reward evaluation via `_apply_reward()` and endocrine feedback loops.

---

## **7. Web UI (WUI) Surfaces**

### **7.1 Backend API**

**Target:** `src/openbad/wui/library_api.py` (new file)

Follow the `setup_*_routes(app, conn)` pattern established in `research_api.py`:

```python
def setup_library_routes(app: web.Application, conn: sqlite3.Connection) -> None:
    store = LibraryStore(conn)
    app["_library_store"] = store

    app.router.add_get("/api/library/tree", _get_tree)
    app.router.add_get("/api/library/book/{book_id}", _get_book)
    app.router.add_post("/api/library/book", _create_book)
    app.router.add_put("/api/library/book/{book_id}", _update_book)
    app.router.add_post("/api/library/search", _search)
    app.router.add_post("/api/library/link", _link_books)
    app.router.add_post("/api/library/library", _create_library)
    app.router.add_post("/api/library/shelf", _create_shelf)
    app.router.add_post("/api/library/section", _create_section)
```

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/library/tree` | Nested JSON: Libraries → Shelves → Sections → Books (titles + IDs only) |
| `GET` | `/api/library/book/{book_id}` | Full book content, metadata, and edge list |
| `POST` | `/api/library/book` | Create a new book (auto-chunks and embeds) |
| `PUT` | `/api/library/book/{book_id}` | Update book content (re-chunks and re-embeds) |
| `POST` | `/api/library/search` | Vector similarity search; returns top-k chunk snippets with book metadata |
| `POST` | `/api/library/link` | Create a citation edge between two books |
| `POST` | `/api/library/library` | Create a new library |
| `POST` | `/api/library/shelf` | Create a new shelf |
| `POST` | `/api/library/section` | Create a new section |

Wire `setup_library_routes()` into the existing `create_app()` call chain in `src/openbad/wui/server.py`.

### **7.2 Svelte Frontend**

**Target:** `wui-svelte/src/routes/library/+page.svelte` (new route)

Use the project's SvelteKit + Svelte 5 runes patterns and Catppuccin Mocha design system (CSS variables from `app.css`). Follow the page structure conventions seen in `research/+page.svelte`:
- `$state()` runes for reactive data
- `$derived()` for computed/sorted views
- `onMount()` for initial data fetch via `apiGet()`/`apiPost()` from `$lib/api/client`
- CSS classes: `.page-header`, `.toolbar`, `.status-badge`, etc.

#### **Component 1: Archive Browser (Left Panel)**

A collapsible tree-view mapping the hierarchy (Library → Shelf → Section → Book). Each book node shows:
- Title
- Author indicator: 🤖 (system-drafted) or 👤 (user-drafted)
- Last updated timestamp (relative, e.g., "2 hours ago")

Clicking a book loads it into the Book Viewer.

#### **Component 2: Semantic Search Bar (Top)**

A search input that calls `POST /api/library/search` on submit. Results display as a ranked list showing:
- Relevance score
- Chunk snippet (highlighted)
- Parent book title (clickable → opens in viewer)

#### **Component 3: Book Viewer / Editor (Main Panel)**

- Renders book markdown content.
- Metadata header: author, created/updated timestamps, section breadcrumb.
- **Edges panel:** Lists citation edges (e.g., "This book supersedes *[Book Title]*") with clickable links.
- **Edit mode:** Toggle button switches to a `<textarea>` for manual content editing. Submit calls `PUT /api/library/book/{id}`.

#### **Navigation**

Add a "Library" entry to the sidebar navigation in `wui-svelte/src/routes/+layout.svelte`, alongside the existing links (Chat, Tasks, Research, etc.).

---

## **Implementation Checklist**

### **Database & Embeddings**

- [ ] Create `src/openbad/state/migrations/0006_library.sql` with all library tables.
- [ ] Add sqlite-vec extension loading to `src/openbad/state/db.py`.
- [ ] Create `src/openbad/library/store.py` — `LibraryStore` data access layer.
- [ ] Create `src/openbad/library/embedder.py` — text chunking utility.
- [ ] Extend `ProviderAdapter` base class with optional `embed()` method.
- [ ] Implement `OllamaProvider.embed()` via `/api/embed` endpoint.
- [ ] Add `embedding` section to `config/cognitive.yaml`.
- [ ] Replace `hash_embedding()` default in `MemoryController` with Ollama provider.

### **Skills & Memory**

- [ ] Create `src/openbad/skills/library_tool.py` with `search_library`, `read_book`, `draft_book`, `link_books`.
- [ ] Import `library_tool` in `src/openbad/skills/server.py` to register decorators.
- [ ] Add `library_refs` convention to semantic memory metadata.
- [ ] Update `MemoryController.recall()` to append Library pointer annotations.

### **Autonomic Engine**

- [ ] Create `src/openbad/active_inference/reconciliation.py` — surprise-triggered library reconciliation.
- [ ] Wire reconciliation into `ExplorationEngine.run_cycle()`.
- [ ] Add `"reconcile"` node type handling in `SchedulerWorker._process_task()`.

### **Frontend WUI**

- [ ] Create `src/openbad/wui/library_api.py` with `setup_library_routes()`.
- [ ] Wire `setup_library_routes()` into `server.py` app creation.
- [ ] Build `wui-svelte/src/routes/library/+page.svelte` with tree browser, search, and book viewer.
- [ ] Add "Library" link to `+layout.svelte` sidebar navigation.
- [ ] Compile Svelte frontend (`npm run build`) and test integration.

### **Tests**

- [ ] `tests/test_library_store.py` — CRUD operations, tree retrieval, edge creation.
- [ ] `tests/test_library_embedder.py` — chunking logic, token-size boundaries.
- [ ] `tests/test_library_tool.py` — skill invocation via `call_skill()`.
- [ ] `tests/test_library_api.py` — HTTP endpoint responses.
- [ ] `tests/test_reconciliation.py` — surprise-triggered task generation.