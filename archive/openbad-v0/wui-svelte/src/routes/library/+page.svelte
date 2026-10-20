<script lang="ts">
  import { onMount } from 'svelte';
  import { get as apiGet, post as apiPost, put as apiPut } from '$lib/api/client';
  import Card from '$lib/components/Card.svelte';

  // -- Types ------------------------------------------------------------------

  interface BookNode {
    book_id: string;
    title: string;
  }

  interface SectionNode {
    section_id: string;
    name: string;
    books: BookNode[];
  }

  interface ShelfNode {
    shelf_id: string;
    name: string;
    sections: SectionNode[];
  }

  interface LibraryNode {
    library_id: string;
    name: string;
    shelves: ShelfNode[];
  }

  interface BookEdge {
    source_book_id: string;
    target_book_id: string;
    relation_type: string;
  }

  interface BookDetail {
    book_id: string;
    section_id: string;
    title: string;
    summary: string;
    content: string;
    author: string;
    created_at: number;
    updated_at: number;
    edges: BookEdge[];
  }

  interface ChunkResult {
    chunk_text: string;
    book_id: string;
    book_title: string;
    score: number;
  }

  // -- State ------------------------------------------------------------------

  let tree: LibraryNode[] = $state([]);
  let selectedBook: BookDetail | null = $state(null);
  let editing = $state(false);
  let editContent = $state('');
  let searchQuery = $state('');
  let searchResults: ChunkResult[] = $state([]);
  let searching = $state(false);
  let loading = $state(true);
  let error = $state('');
  let expandedLibs: Set<string> = $state(new Set());
  let expandedShelves: Set<string> = $state(new Set());
  let expandedSections: Set<string> = $state(new Set());

  // -- Helpers ----------------------------------------------------------------

  function fmtTime(ts: number): string {
    if (!ts) return '—';
    const diff = (Date.now() / 1000) - ts;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function authorIcon(author: string): string {
    return author === 'system' ? '🤖' : '👤';
  }

  // -- Tree -------------------------------------------------------------------

  function toggleLib(id: string) {
    const s = new Set(expandedLibs);
    s.has(id) ? s.delete(id) : s.add(id);
    expandedLibs = s;
  }

  function toggleShelf(id: string) {
    const s = new Set(expandedShelves);
    s.has(id) ? s.delete(id) : s.add(id);
    expandedShelves = s;
  }

  function toggleSection(id: string) {
    const s = new Set(expandedSections);
    s.has(id) ? s.delete(id) : s.add(id);
    expandedSections = s;
  }

  // -- Data loading -----------------------------------------------------------

  async function loadTree() {
    loading = true;
    error = '';
    try {
      const data = await apiGet<{ tree: LibraryNode[] }>('/api/library/tree');
      tree = data.tree;
    } catch (e: any) {
      error = e.message ?? 'Failed to load library tree';
    } finally {
      loading = false;
    }
  }

  async function loadBook(bookId: string) {
    try {
      selectedBook = await apiGet<BookDetail>(`/api/library/book/${bookId}`);
      editing = false;
      editContent = '';
    } catch (e: any) {
      error = e.message ?? 'Failed to load book';
    }
  }

  async function handleSearch() {
    const q = searchQuery.trim();
    if (!q) return;
    searching = true;
    try {
      const data = await apiPost<{ results: ChunkResult[] }>('/api/library/search', {
        query: q,
        top_k: 10,
      });
      searchResults = data.results;
    } catch (e: any) {
      error = e.message ?? 'Search failed';
    } finally {
      searching = false;
    }
  }

  function startEdit() {
    if (!selectedBook) return;
    editContent = selectedBook.content;
    editing = true;
  }

  async function saveEdit() {
    if (!selectedBook) return;
    try {
      await apiPut(`/api/library/book/${selectedBook.book_id}`, {
        content: editContent,
      });
      await loadBook(selectedBook.book_id);
      editing = false;
    } catch (e: any) {
      error = e.message ?? 'Failed to save';
    }
  }

  function cancelEdit() {
    editing = false;
    editContent = '';
  }

  // -- Breadcrumb helper ------------------------------------------------------

  function findBreadcrumb(sectionId: string): string {
    for (const lib of tree) {
      for (const shelf of lib.shelves) {
        for (const sec of shelf.sections) {
          if (sec.section_id === sectionId) {
            return `${lib.name} › ${shelf.name} › ${sec.name}`;
          }
        }
      }
    }
    return '';
  }

  // -- Edge title lookup ------------------------------------------------------

  function findBookTitle(bookId: string): string {
    for (const lib of tree) {
      for (const shelf of lib.shelves) {
        for (const sec of shelf.sections) {
          for (const book of sec.books) {
            if (book.book_id === bookId) return book.title;
          }
        }
      }
    }
    return bookId.slice(0, 8);
  }

  onMount(loadTree);
</script>

<div class="page-header">
  <h2>📚 Library</h2>
  <p>Browse, search, and edit knowledge books</p>
</div>

<!-- Search Bar -->
<div class="search-bar">
  <form onsubmit={(e) => { e.preventDefault(); handleSearch(); }}>
    <input
      type="text"
      placeholder="Search library..."
      bind:value={searchQuery}
    />
    <button type="submit" disabled={searching || !searchQuery.trim()}>
      {searching ? '⏳' : '🔍'} Search
    </button>
  </form>
</div>

<!-- Search Results -->
{#if searchResults.length > 0}
  <Card label="Search Results">
    <div class="search-results">
      {#each searchResults as result}
        <div class="result-item">
          <div class="result-header">
            <span class="score badge">{result.score.toFixed(3)}</span>
            <button class="ghost book-link" onclick={() => loadBook(result.book_id)}>
              {result.book_title}
            </button>
          </div>
          <p class="chunk-snippet">{result.chunk_text}</p>
        </div>
      {/each}
    </div>
  </Card>
{/if}

<div class="library-layout">
  <!-- Archive Tree (Left Panel) -->
  <Card label="Archive">
    <div class="tree-panel">
      {#if loading}
        <p class="muted">Loading...</p>
      {:else if tree.length === 0}
        <p class="muted">No libraries yet</p>
      {:else}
        {#each tree as lib}
          <div class="tree-node">
            <button class="ghost tree-toggle" onclick={() => toggleLib(lib.library_id)}>
              {expandedLibs.has(lib.library_id) ? '▾' : '▸'} {lib.name}
            </button>
            {#if expandedLibs.has(lib.library_id)}
              {#each lib.shelves as shelf}
                <div class="tree-node indent-1">
                  <button class="ghost tree-toggle" onclick={() => toggleShelf(shelf.shelf_id)}>
                    {expandedShelves.has(shelf.shelf_id) ? '▾' : '▸'} {shelf.name}
                  </button>
                  {#if expandedShelves.has(shelf.shelf_id)}
                    {#each shelf.sections as sec}
                      <div class="tree-node indent-2">
                        <button class="ghost tree-toggle" onclick={() => toggleSection(sec.section_id)}>
                          {expandedSections.has(sec.section_id) ? '▾' : '▸'} {sec.name}
                        </button>
                        {#if expandedSections.has(sec.section_id)}
                          {#each sec.books as book}
                            <div class="tree-node indent-3">
                              <button
                                class="ghost book-node"
                                class:active={selectedBook?.book_id === book.book_id}
                                onclick={() => loadBook(book.book_id)}
                              >
                                📄 {book.title}
                              </button>
                            </div>
                          {/each}
                        {/if}
                      </div>
                    {/each}
                  {/if}
                </div>
              {/each}
            {/if}
          </div>
        {/each}
      {/if}
    </div>
  </Card>

  <!-- Book Viewer / Editor (Main Panel) -->
  <Card label="Book Viewer">
    {#if !selectedBook}
      <p class="muted">Select a book from the archive tree</p>
    {:else}
      <div class="book-header">
        <h3>{selectedBook.title}</h3>
        <div class="book-meta">
          <span>{authorIcon(selectedBook.author)} {selectedBook.author}</span>
          <span class="muted">|</span>
          <span class="muted">{fmtTime(selectedBook.updated_at)}</span>
          <span class="muted">|</span>
          <span class="muted">{findBreadcrumb(selectedBook.section_id)}</span>
        </div>
      </div>

      {#if editing}
        <div class="editor">
          <textarea bind:value={editContent} rows="20"></textarea>
          <div class="editor-actions">
            <button onclick={saveEdit}>💾 Save</button>
            <button class="secondary" onclick={cancelEdit}>Cancel</button>
          </div>
        </div>
      {:else}
        <div class="book-content">
          <pre>{selectedBook.content}</pre>
        </div>
        <div class="book-actions">
          <button class="secondary" onclick={startEdit}>✏️ Edit</button>
        </div>
      {/if}

      {#if selectedBook.edges.length > 0}
        <div class="edges-panel">
          <h4>Edges</h4>
          {#each selectedBook.edges as edge}
            <div class="edge-item">
              <span class="edge-type badge">{edge.relation_type}</span>
              {#if edge.source_book_id === selectedBook.book_id}
                <button class="ghost book-link" onclick={() => loadBook(edge.target_book_id)}>
                  {findBookTitle(edge.target_book_id)}
                </button>
              {:else}
                <button class="ghost book-link" onclick={() => loadBook(edge.source_book_id)}>
                  {findBookTitle(edge.source_book_id)}
                </button>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </Card>
</div>

{#if error}
  <p class="text-red">{error}</p>
{/if}

<style>
  .search-bar {
    margin-bottom: 1rem;
  }
  .search-bar form {
    display: flex;
    gap: 0.5rem;
  }
  .search-bar input {
    flex: 1;
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bg-surface2);
    background: var(--bg-surface0);
    color: var(--text);
    font-size: 0.9rem;
  }
  .search-bar input::placeholder {
    color: var(--text-dim);
  }

  .search-results {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .result-item {
    padding: 0.5rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface0);
    border: 1px solid var(--bg-surface1);
  }
  .result-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }
  .score {
    font-size: 0.75rem;
    font-family: monospace;
  }
  .chunk-snippet {
    font-size: 0.85rem;
    color: var(--text-sub);
    white-space: pre-wrap;
    margin: 0;
  }

  .library-layout {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 1rem;
    align-items: start;
  }

  .tree-panel {
    max-height: 70vh;
    overflow-y: auto;
  }
  .tree-node {
    line-height: 1.6;
  }
  .tree-toggle {
    font-size: 0.85rem;
    text-align: left;
    width: 100%;
    padding: 0.15rem 0.25rem;
    border-radius: var(--radius-sm);
  }
  .tree-toggle:hover {
    background: var(--bg-surface1);
  }
  .indent-1 { padding-left: 1rem; }
  .indent-2 { padding-left: 2rem; }
  .indent-3 { padding-left: 3rem; }

  .book-node {
    font-size: 0.85rem;
    text-align: left;
    width: 100%;
    padding: 0.15rem 0.25rem;
    border-radius: var(--radius-sm);
    cursor: pointer;
  }
  .book-node:hover {
    background: var(--bg-surface1);
  }
  .book-node.active {
    background: var(--blue-dim, rgba(137, 180, 250, 0.15));
    color: var(--blue);
  }

  .book-header {
    margin-bottom: 1rem;
  }
  .book-header h3 {
    margin: 0 0 0.25rem;
  }
  .book-meta {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.85rem;
    flex-wrap: wrap;
  }

  .book-content {
    margin-bottom: 1rem;
  }
  .book-content pre {
    white-space: pre-wrap;
    word-break: break-word;
    font-family: inherit;
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text);
    background: var(--bg-surface0);
    padding: 1rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bg-surface1);
  }

  .book-actions {
    margin-bottom: 1rem;
  }

  .editor textarea {
    width: 100%;
    min-height: 300px;
    padding: 0.75rem;
    font-family: monospace;
    font-size: 0.9rem;
    background: var(--bg-surface0);
    color: var(--text);
    border: 1px solid var(--bg-surface2);
    border-radius: var(--radius-sm);
    resize: vertical;
  }
  .editor-actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .edges-panel {
    border-top: 1px solid var(--bg-surface1);
    padding-top: 0.75rem;
    margin-top: 0.75rem;
  }
  .edges-panel h4 {
    margin: 0 0 0.5rem;
    font-size: 0.9rem;
    color: var(--text-sub);
  }
  .edge-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }
  .edge-type {
    font-size: 0.75rem;
    text-transform: uppercase;
  }

  .book-link {
    color: var(--blue);
    text-decoration: underline;
    cursor: pointer;
  }
  .book-link:hover {
    color: var(--text);
  }

  @media (max-width: 768px) {
    .library-layout {
      grid-template-columns: 1fr;
    }
  }
</style>
