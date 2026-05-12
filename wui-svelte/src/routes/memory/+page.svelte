<script lang="ts">
  import { onMount } from 'svelte';
  import { get as apiGet, post as apiPost } from '$lib/api/client';
  import Card from '$lib/components/Card.svelte';

  // -- Types ------------------------------------------------------------------

  interface StmUsage {
    tokens_used: number;
    tokens_max: number;
    entry_count: number;
    oldest_entry_age: number;
  }

  interface MemoryStats {
    stm: StmUsage;
    episodic: { entry_count: number };
    semantic: { entry_count: number };
    procedural: { entry_count: number };
    timestamp: number;
  }

  interface MemEntry {
    key: string;
    value: string;
    tier: string;
    entry_id: string;
    created_at: number;
    accessed_at: number;
    access_count: number;
    ttl_seconds: number | null;
    context: string;
    metadata: Record<string, any>;
    // STM extras
    age_seconds?: number;
    ttl_remaining?: number | null;
    // Semantic extras
    has_vector?: boolean;
    // Procedural extras
    skill?: SkillInfo | null;
  }

  interface SkillInfo {
    name: string;
    description: string;
    capabilities: string[];
    code: string;
    confidence: number;
    success_count: number;
    failure_count: number;
  }

  interface RecallResult {
    key: string;
    value: string;
    tier: string;
    score: number;
    metadata: Record<string, any>;
    library_annotations?: string[];
  }

  // -- State ------------------------------------------------------------------

  type Tab = 'overview' | 'stm' | 'episodic' | 'semantic' | 'procedural';
  type SortDir = 'asc' | 'desc';

  let activeTab: Tab = $state('overview');
  let stats: MemoryStats | null = $state(null);
  let stmEntries: MemEntry[] = $state([]);
  let stmUsage: StmUsage | null = $state(null);
  let episodicEntries: MemEntry[] = $state([]);
  let episodicTotal = $state(0);
  let semanticEntries: MemEntry[] = $state([]);
  let semanticTotal = $state(0);
  let proceduralEntries: MemEntry[] = $state([]);
  let proceduralTotal = $state(0);
  let loading = $state(false);
  let error = $state('');
  let expandedKey = $state('');

  // Sorting
  let sortColumn = $state('created_at');
  let sortDir: SortDir = $state('desc');

  // Filtering
  let filterText = $state('');

  // Recall
  let recallQuery = $state('');
  let recallResults: RecallResult[] = $state([]);
  let recalling = $state(false);

  // -- Helpers ----------------------------------------------------------------

  function fmtTime(ts: number): string {
    if (!ts) return '—';
    const now = Date.now() / 1000;
    const diff = now - ts;
    if (diff < 0) return 'future';
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function fmtTimestamp(ts: number): string {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
  }

  function fmtDuration(seconds: number): string {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function truncate(text: string, max: number = 120): string {
    return text.length > max ? text.slice(0, max) + '…' : text;
  }

  function pct(used: number, total: number): number {
    return total > 0 ? Math.round((used / total) * 100) : 0;
  }

  function tierIcon(tier: string): string {
    const icons: Record<string, string> = {
      stm: '⚡', episodic: '📅', semantic: '🧠', procedural: '🔧',
    };
    return icons[tier] ?? '📦';
  }

  // -- Sorting & filtering ----------------------------------------------------

  function sortEntries(entries: MemEntry[]): MemEntry[] {
    let filtered = entries;
    const q = filterText.trim().toLowerCase();
    if (q) {
      filtered = entries.filter(e =>
        e.key.toLowerCase().includes(q) ||
        e.value.toLowerCase().includes(q) ||
        e.context.toLowerCase().includes(q) ||
        e.entry_id.toLowerCase().includes(q)
      );
    }
    return [...filtered].sort((a, b) => {
      let va: any = (a as any)[sortColumn];
      let vb: any = (b as any)[sortColumn];
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
      if (va == null) va = '';
      if (vb == null) vb = '';
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }

  function toggleSort(col: string) {
    if (sortColumn === col) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortColumn = col;
      sortDir = col === 'created_at' || col === 'accessed_at' || col === 'access_count' ? 'desc' : 'asc';
    }
  }

  function sortIndicator(col: string): string {
    if (sortColumn !== col) return '';
    return sortDir === 'asc' ? ' ▲' : ' ▼';
  }

  // -- Data loading -----------------------------------------------------------

  async function loadStats() {
    try {
      stats = await apiGet<MemoryStats>('/api/memory/stats');
    } catch (e: any) {
      error = e.message ?? 'Failed to load stats';
    }
  }

  async function loadStm() {
    loading = true;
    error = '';
    try {
      const data = await apiGet<{ entries: MemEntry[]; usage: StmUsage }>('/api/memory/stm');
      stmEntries = data.entries;
      stmUsage = data.usage;
    } catch (e: any) {
      error = e.message ?? 'Failed to load STM';
    } finally {
      loading = false;
    }
  }

  async function loadEpisodic() {
    loading = true;
    error = '';
    try {
      const data = await apiGet<{ entries: MemEntry[]; total: number }>('/api/memory/episodic?limit=100');
      episodicEntries = data.entries;
      episodicTotal = data.total;
    } catch (e: any) {
      error = e.message ?? 'Failed to load episodic';
    } finally {
      loading = false;
    }
  }

  async function loadSemantic() {
    loading = true;
    error = '';
    try {
      const data = await apiGet<{ entries: MemEntry[]; total: number }>('/api/memory/semantic');
      semanticEntries = data.entries;
      semanticTotal = data.total;
    } catch (e: any) {
      error = e.message ?? 'Failed to load semantic';
    } finally {
      loading = false;
    }
  }

  async function loadProcedural() {
    loading = true;
    error = '';
    try {
      const data = await apiGet<{ entries: MemEntry[]; total: number }>('/api/memory/procedural');
      proceduralEntries = data.entries;
      proceduralTotal = data.total;
    } catch (e: any) {
      error = e.message ?? 'Failed to load procedural';
    } finally {
      loading = false;
    }
  }

  async function handleRecall() {
    const q = recallQuery.trim();
    if (!q) return;
    recalling = true;
    error = '';
    try {
      const data = await apiPost<{ results: RecallResult[] }>('/api/memory/recall', {
        query: q,
        top_k: 10,
      });
      recallResults = data.results;
    } catch (e: any) {
      error = e.message ?? 'Recall failed';
    } finally {
      recalling = false;
    }
  }

  function switchTab(tab: Tab) {
    activeTab = tab;
    expandedKey = '';
    filterText = '';
    sortColumn = 'created_at';
    sortDir = 'desc';
    if (tab === 'overview') loadStats();
    else if (tab === 'stm') loadStm();
    else if (tab === 'episodic') loadEpisodic();
    else if (tab === 'semantic') loadSemantic();
    else if (tab === 'procedural') loadProcedural();
  }

  function toggleExpand(key: string) {
    expandedKey = expandedKey === key ? '' : key;
  }

  onMount(loadStats);
</script>

<div class="page-header">
  <h2>🧠 Memory Inspector</h2>
  <p>Browse and verify all memory tiers</p>
</div>

<!-- Tab Bar -->
<div class="tab-bar">
  <button class:active={activeTab === 'overview'} onclick={() => switchTab('overview')}>Overview</button>
  <button class:active={activeTab === 'stm'} onclick={() => switchTab('stm')}>⚡ STM</button>
  <button class:active={activeTab === 'episodic'} onclick={() => switchTab('episodic')}>📅 Episodic</button>
  <button class:active={activeTab === 'semantic'} onclick={() => switchTab('semantic')}>🧠 Semantic</button>
  <button class:active={activeTab === 'procedural'} onclick={() => switchTab('procedural')}>🔧 Procedural</button>
</div>

{#if error}
  <p class="text-red">{error}</p>
{/if}

<!-- ═══════ OVERVIEW TAB ═══════ -->
{#if activeTab === 'overview'}
  <div class="overview-grid">
    {#if stats}
      <Card label="Tier Counts">
        <div class="stat-row">
          <div class="stat-card">
            <span class="stat-icon">⚡</span>
            <span class="stat-value">{stats.stm.entry_count}</span>
            <span class="stat-label">STM</span>
          </div>
          <div class="stat-card">
            <span class="stat-icon">📅</span>
            <span class="stat-value">{stats.episodic.entry_count}</span>
            <span class="stat-label">Episodic</span>
          </div>
          <div class="stat-card">
            <span class="stat-icon">🧠</span>
            <span class="stat-value">{stats.semantic.entry_count}</span>
            <span class="stat-label">Semantic</span>
          </div>
          <div class="stat-card">
            <span class="stat-icon">🔧</span>
            <span class="stat-value">{stats.procedural.entry_count}</span>
            <span class="stat-label">Procedural</span>
          </div>
        </div>
      </Card>

      <Card label="STM Token Usage">
        <div class="token-bar-container">
          <div class="token-bar">
            <div
              class="token-fill"
              class:warning={pct(stats.stm.tokens_used, stats.stm.tokens_max) > 80}
              style="width: {pct(stats.stm.tokens_used, stats.stm.tokens_max)}%"
            ></div>
          </div>
          <span class="token-label">
            {stats.stm.tokens_used.toLocaleString()} / {stats.stm.tokens_max.toLocaleString()} tokens
            ({pct(stats.stm.tokens_used, stats.stm.tokens_max)}%)
          </span>
        </div>
      </Card>
    {:else}
      <Card label="Loading...">
        <p class="muted">Fetching memory statistics…</p>
      </Card>
    {/if}

    <Card label="Recall Test">
      <p class="muted recall-desc">Test what the LLM would retrieve for a query — validates the full recall pipeline.</p>
      <form class="recall-form" onsubmit={(e) => { e.preventDefault(); handleRecall(); }}>
        <input
          type="text"
          placeholder="Enter recall query..."
          bind:value={recallQuery}
        />
        <button type="submit" disabled={recalling || !recallQuery.trim()}>
          {recalling ? '⏳' : '🔍'} Recall
        </button>
      </form>

      {#if recallResults.length > 0}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="col-rank">#</th>
                <th class="col-tier">Tier</th>
                <th class="col-score">Score</th>
                <th class="col-key">Key</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {#each recallResults as r, i}
                <tr>
                  <td class="mono">{i + 1}</td>
                  <td><span class="badge">{tierIcon(r.tier)} {r.tier}</span></td>
                  <td class="mono">{r.score.toFixed(4)}</td>
                  <td class="cell-key">{r.key}</td>
                  <td class="cell-value">{truncate(r.value, 200)}</td>
                </tr>
                {#if r.library_annotations && r.library_annotations.length > 0}
                  <tr class="annotation-row">
                    <td></td>
                    <td colspan="4">
                      {#each r.library_annotations as ann}
                        <p class="annotation text-blue">{ann}</p>
                      {/each}
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        </div>
      {:else if recallQuery && !recalling}
        <p class="muted">No results — try a different query or add some memories first.</p>
      {/if}
    </Card>
  </div>

<!-- ═══════ STM TAB ═══════ -->
{:else if activeTab === 'stm'}
  <div class="toolbar">
    <span class="count">{stmEntries.length} entries</span>
    {#if stmUsage}
      <span class="muted">
        | {stmUsage.tokens_used.toLocaleString()} / {stmUsage.tokens_max.toLocaleString()} tokens
      </span>
    {/if}
    <div class="toolbar-right">
      <input class="filter-input" type="text" placeholder="Filter…" bind:value={filterText} />
      <button class="secondary" onclick={loadStm}>↻ Refresh</button>
    </div>
  </div>

  <Card label="Short-Term Memory">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if stmEntries.length === 0}
      <p class="muted">STM is empty — no active short-term memories.</p>
    {:else}
      {@const sorted = sortEntries(stmEntries)}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="sortable col-key" onclick={() => toggleSort('key')}>Key{sortIndicator('key')}</th>
              <th>Value</th>
              <th class="col-context">Context</th>
              <th class="sortable col-ts" onclick={() => toggleSort('created_at')}>Created{sortIndicator('created_at')}</th>
              <th class="col-ttl">TTL</th>
            </tr>
          </thead>
          <tbody>
            {#each sorted as entry}
              <tr class:expanded={expandedKey === entry.key} onclick={() => toggleExpand(entry.key)}>
                <td class="cell-key">{entry.key}</td>
                <td class="cell-value">{truncate(entry.value, 80)}</td>
                <td class="cell-context">{entry.context || '—'}</td>
                <td class="cell-ts" title={fmtTimestamp(entry.created_at)}>{fmtTime(entry.created_at)}</td>
                <td class="cell-ttl">
                  {#if entry.ttl_remaining != null}
                    <span class:text-yellow={entry.ttl_remaining < 300}>{fmtDuration(entry.ttl_remaining)}</span>
                  {:else}
                    —
                  {/if}
                </td>
              </tr>
              {#if expandedKey === entry.key}
                <tr class="detail-row">
                  <td colspan="5">
                    <div class="entry-detail">
                      <div class="detail-grid">
                        <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                        <div><strong>Created:</strong> {fmtTimestamp(entry.created_at)}</div>
                        <div><strong>Accessed:</strong> {fmtTimestamp(entry.accessed_at)} ({entry.access_count}×)</div>
                        <div><strong>Context:</strong> {entry.context || '—'}</div>
                      </div>
                      <div class="detail-value">
                        <strong>Value:</strong>
                        <pre>{entry.value}</pre>
                      </div>
                      {#if Object.keys(entry.metadata).length > 0}
                        <div class="detail-meta">
                          <strong>Metadata:</strong>
                          <pre>{JSON.stringify(entry.metadata, null, 2)}</pre>
                        </div>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
      {#if filterText && sortEntries(stmEntries).length === 0}
        <p class="muted">No entries match filter "{filterText}"</p>
      {/if}
    {/if}
  </Card>

<!-- ═══════ EPISODIC TAB ═══════ -->
{:else if activeTab === 'episodic'}
  <div class="toolbar">
    <span class="count">{episodicTotal} entries (showing {sortEntries(episodicEntries).length})</span>
    <div class="toolbar-right">
      <input class="filter-input" type="text" placeholder="Filter…" bind:value={filterText} />
      <button class="secondary" onclick={loadEpisodic}>↻ Refresh</button>
    </div>
  </div>

  <Card label="Episodic Memory">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if episodicEntries.length === 0}
      <p class="muted">No episodic memories recorded yet.</p>
    {:else}
      {@const sorted = sortEntries(episodicEntries)}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="sortable col-key" onclick={() => toggleSort('key')}>Key{sortIndicator('key')}</th>
              <th>Value</th>
              <th class="sortable col-context" onclick={() => toggleSort('context')}>Role{sortIndicator('context')}</th>
              <th class="sortable col-ts" onclick={() => toggleSort('created_at')}>Created{sortIndicator('created_at')}</th>
              <th class="sortable col-count" onclick={() => toggleSort('access_count')}>Reads{sortIndicator('access_count')}</th>
            </tr>
          </thead>
          <tbody>
            {#each sorted as entry}
              <tr class:expanded={expandedKey === entry.key} onclick={() => toggleExpand(entry.key)}>
                <td class="cell-key">{entry.key}</td>
                <td class="cell-value">{truncate(entry.value, 80)}</td>
                <td class="cell-context">{entry.context || '—'}</td>
                <td class="cell-ts" title={fmtTimestamp(entry.created_at)}>{fmtTime(entry.created_at)}</td>
                <td class="cell-count">{entry.access_count}×</td>
              </tr>
              {#if expandedKey === entry.key}
                <tr class="detail-row">
                  <td colspan="5">
                    <div class="entry-detail">
                      <div class="detail-grid">
                        <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                        <div><strong>Created:</strong> {fmtTimestamp(entry.created_at)}</div>
                        <div><strong>Context:</strong> {entry.context || '—'}</div>
                      </div>
                      <div class="detail-value">
                        <strong>Value:</strong>
                        <pre>{entry.value}</pre>
                      </div>
                      {#if Object.keys(entry.metadata).length > 0}
                        <div class="detail-meta">
                          <strong>Metadata:</strong>
                          <pre>{JSON.stringify(entry.metadata, null, 2)}</pre>
                        </div>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
      {#if filterText && sorted.length === 0}
        <p class="muted">No entries match filter "{filterText}"</p>
      {/if}
    {/if}
  </Card>

<!-- ═══════ SEMANTIC TAB ═══════ -->
{:else if activeTab === 'semantic'}
  <div class="toolbar">
    <span class="count">{semanticTotal} entries (showing {sortEntries(semanticEntries).length})</span>
    <div class="toolbar-right">
      <input class="filter-input" type="text" placeholder="Filter…" bind:value={filterText} />
      <button class="secondary" onclick={loadSemantic}>↻ Refresh</button>
    </div>
  </div>

  <Card label="Semantic Memory">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if semanticEntries.length === 0}
      <p class="muted">No semantic memories stored yet.</p>
    {:else}
      {@const sorted = sortEntries(semanticEntries)}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="sortable col-key" onclick={() => toggleSort('key')}>Key{sortIndicator('key')}</th>
              <th>Value</th>
              <th class="sortable col-context" onclick={() => toggleSort('context')}>Role{sortIndicator('context')}</th>
              <th class="col-vec">Vec</th>
              <th class="sortable col-ts" onclick={() => toggleSort('created_at')}>Created{sortIndicator('created_at')}</th>
              <th class="sortable col-count" onclick={() => toggleSort('access_count')}>Reads{sortIndicator('access_count')}</th>
            </tr>
          </thead>
          <tbody>
            {#each sorted as entry}
              <tr class:expanded={expandedKey === entry.key} onclick={() => toggleExpand(entry.key)}>
                <td class="cell-key">{entry.key}</td>
                <td class="cell-value">{truncate(entry.value, 80)}</td>
                <td class="cell-context">{entry.context || '—'}</td>
                <td class="cell-vec">{entry.has_vector ? '✓' : '—'}</td>
                <td class="cell-ts" title={fmtTimestamp(entry.created_at)}>{fmtTime(entry.created_at)}</td>
                <td class="cell-count">{entry.access_count}×</td>
              </tr>
              {#if expandedKey === entry.key}
                <tr class="detail-row">
                  <td colspan="6">
                    <div class="entry-detail">
                      <div class="detail-grid">
                        <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                        <div><strong>Created:</strong> {fmtTimestamp(entry.created_at)}</div>
                        <div><strong>Accessed:</strong> {fmtTimestamp(entry.accessed_at)} ({entry.access_count}×)</div>
                        <div><strong>Has Vector:</strong> {entry.has_vector ? 'Yes' : 'No'}</div>
                      </div>
                      <div class="detail-value">
                        <strong>Value:</strong>
                        <pre>{entry.value}</pre>
                      </div>
                      {#if entry.metadata?.library_refs?.length}
                        <div class="detail-meta">
                          <strong>Library Refs:</strong>
                          <ul>
                            {#each entry.metadata.library_refs as ref}
                              <li>{ref}</li>
                            {/each}
                          </ul>
                        </div>
                      {/if}
                      {#if Object.keys(entry.metadata).length > 0}
                        <div class="detail-meta">
                          <strong>Metadata:</strong>
                          <pre>{JSON.stringify(entry.metadata, null, 2)}</pre>
                        </div>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
      {#if filterText && sorted.length === 0}
        <p class="muted">No entries match filter "{filterText}"</p>
      {/if}
    {/if}
  </Card>

<!-- ═══════ PROCEDURAL TAB ═══════ -->
{:else if activeTab === 'procedural'}
  <div class="toolbar">
    <span class="count">{proceduralTotal} skills (showing {sortEntries(proceduralEntries).length})</span>
    <div class="toolbar-right">
      <input class="filter-input" type="text" placeholder="Filter…" bind:value={filterText} />
      <button class="secondary" onclick={loadProcedural}>↻ Refresh</button>
    </div>
  </div>

  <Card label="Procedural Memory (Skills)">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if proceduralEntries.length === 0}
      <p class="muted">No procedural skills stored yet.</p>
    {:else}
      {@const sorted = sortEntries(proceduralEntries)}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th class="sortable col-key" onclick={() => toggleSort('key')}>Key{sortIndicator('key')}</th>
              <th>Description</th>
              <th class="col-conf">Confidence</th>
              <th class="col-count">✓</th>
              <th class="col-count">✗</th>
              <th class="sortable col-ts" onclick={() => toggleSort('created_at')}>Created{sortIndicator('created_at')}</th>
            </tr>
          </thead>
          <tbody>
            {#each sorted as entry}
              <tr class:expanded={expandedKey === entry.key} onclick={() => toggleExpand(entry.key)}>
                <td class="cell-key">{entry.key}</td>
                <td class="cell-value">{entry.skill?.description ?? truncate(entry.value, 80)}</td>
                <td class="cell-conf">
                  {#if entry.skill}
                    <div class="conf-bar-inline">
                      <div
                        class="conf-fill-inline"
                        class:low={entry.skill.confidence < 0.3}
                        class:mid={entry.skill.confidence >= 0.3 && entry.skill.confidence < 0.7}
                        class:high={entry.skill.confidence >= 0.7}
                        style="width: {entry.skill.confidence * 100}%"
                      ></div>
                    </div>
                    <span class="conf-pct">{(entry.skill.confidence * 100).toFixed(0)}%</span>
                  {:else}
                    —
                  {/if}
                </td>
                <td class="cell-count text-green">{entry.skill?.success_count ?? '—'}</td>
                <td class="cell-count text-red">{entry.skill?.failure_count ?? '—'}</td>
                <td class="cell-ts" title={fmtTimestamp(entry.created_at)}>{fmtTime(entry.created_at)}</td>
              </tr>
              {#if expandedKey === entry.key}
                <tr class="detail-row">
                  <td colspan="6">
                    <div class="entry-detail">
                      {#if entry.skill}
                        <div class="detail-grid">
                          <div><strong>Name:</strong> {entry.skill.name}</div>
                          <div><strong>Confidence:</strong> {(entry.skill.confidence * 100).toFixed(1)}%</div>
                          <div><strong>Success / Fail:</strong> {entry.skill.success_count} / {entry.skill.failure_count}</div>
                        </div>
                        {#if entry.skill.capabilities.length > 0}
                          <div class="caps-row">
                            <strong>Capabilities:</strong>
                            {#each entry.skill.capabilities as cap}
                              <span class="badge">{cap}</span>
                            {/each}
                          </div>
                        {/if}
                        {#if entry.skill.code}
                          <div class="detail-value">
                            <strong>Code:</strong>
                            <pre>{entry.skill.code}</pre>
                          </div>
                        {/if}
                      {:else}
                        <div class="detail-value">
                          <strong>Value:</strong>
                          <pre>{entry.value}</pre>
                        </div>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
      {#if filterText && sorted.length === 0}
        <p class="muted">No entries match filter "{filterText}"</p>
      {/if}
    {/if}
  </Card>
{/if}

<style>
  /* -- Tab Bar -- */
  .tab-bar {
    display: flex;
    gap: 0;
    margin-bottom: 1rem;
    border-bottom: 1px solid var(--bg-surface2);
  }
  .tab-bar button {
    padding: 0.5rem 1rem;
    background: transparent;
    color: var(--text-dim);
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    font-size: 0.9rem;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab-bar button:hover { color: var(--text); }
  .tab-bar button.active {
    color: var(--blue);
    border-bottom-color: var(--blue);
  }

  /* -- Overview -- */
  .overview-grid {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .stat-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
  }
  .stat-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0.75rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface0);
    border: 1px solid var(--bg-surface1);
  }
  .stat-icon { font-size: 1.5rem; }
  .stat-value { font-size: 1.75rem; font-weight: 700; color: var(--text); }
  .stat-label { font-size: 0.8rem; color: var(--text-dim); text-transform: uppercase; }

  /* -- Token bar -- */
  .token-bar-container {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .token-bar {
    height: 1.25rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .token-fill {
    height: 100%;
    background: var(--green);
    transition: width 0.3s;
    border-radius: var(--radius-sm);
  }
  .token-fill.warning { background: var(--yellow); }
  .token-label { font-size: 0.85rem; color: var(--text-sub); }

  /* -- Recall -- */
  .recall-desc { margin: 0 0 0.5rem; font-size: 0.85rem; }
  .recall-form {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .recall-form input {
    flex: 1;
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bg-surface2);
    background: var(--bg-surface0);
    color: var(--text);
    font-size: 0.9rem;
  }
  .recall-form input::placeholder { color: var(--text-dim); }

  /* -- Toolbar -- */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
    flex-wrap: wrap;
  }
  .toolbar-right {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-left: auto;
  }
  .count { font-weight: 600; }

  /* -- Filter -- */
  .filter-input {
    padding: 0.35rem 0.6rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bg-surface2);
    background: var(--bg-surface0);
    color: var(--text);
    font-size: 0.85rem;
    width: 180px;
  }
  .filter-input::placeholder { color: var(--text-dim); }

  /* -- Table -- */
  .table-wrap {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  thead {
    position: sticky;
    top: 0;
    z-index: 1;
  }
  th {
    text-align: left;
    padding: 0.5rem 0.5rem;
    border-bottom: 2px solid var(--bg-surface2);
    background: var(--bg-surface0);
    color: var(--text-dim);
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    white-space: nowrap;
    user-select: none;
  }
  th.sortable {
    cursor: pointer;
  }
  th.sortable:hover {
    color: var(--blue);
  }
  td {
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--bg-surface1);
    vertical-align: top;
    color: var(--text);
  }
  tbody tr {
    cursor: pointer;
    transition: background 0.1s;
  }
  tbody tr:hover {
    background: var(--bg-surface1);
  }
  tbody tr.expanded {
    background: var(--bg-surface0);
  }
  tr.detail-row {
    cursor: default;
  }
  tr.detail-row:hover {
    background: transparent;
  }
  tr.detail-row td {
    padding: 0;
    border-bottom: 2px solid var(--bg-surface2);
  }
  tr.annotation-row td {
    padding: 0.25rem 0.5rem;
    border-bottom: none;
  }

  /* Column widths */
  .col-key { width: 220px; max-width: 280px; }
  .col-ts { width: 100px; white-space: nowrap; }
  .col-count { width: 60px; text-align: center; }
  .col-context { width: 80px; }
  .col-ttl { width: 80px; }
  .col-vec { width: 50px; text-align: center; }
  .col-conf { width: 100px; }
  .col-rank { width: 30px; }
  .col-score { width: 70px; }
  .col-tier { width: 90px; }

  /* Cell styles */
  .cell-key {
    font-weight: 600;
    color: var(--blue);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 280px;
  }
  .cell-value {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 400px;
    color: var(--text-sub);
  }
  .cell-context {
    color: var(--text-dim);
    font-size: 0.8rem;
  }
  .cell-ts {
    color: var(--text-dim);
    font-size: 0.8rem;
    white-space: nowrap;
  }
  .cell-count {
    text-align: center;
    color: var(--text-dim);
  }
  .cell-vec {
    text-align: center;
  }
  .cell-ttl {
    font-size: 0.8rem;
  }
  .cell-conf {
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }
  .mono {
    font-family: monospace;
    font-size: 0.8rem;
  }

  /* -- Confidence bar (inline) -- */
  .conf-bar-inline {
    width: 50px;
    height: 8px;
    background: var(--bg-surface1);
    border-radius: 4px;
    overflow: hidden;
  }
  .conf-fill-inline {
    height: 100%;
    border-radius: 4px;
  }
  .conf-fill-inline.low { background: var(--red); }
  .conf-fill-inline.mid { background: var(--yellow); }
  .conf-fill-inline.high { background: var(--green); }
  .conf-pct {
    font-size: 0.75rem;
    color: var(--text-dim);
    font-weight: 600;
  }

  /* -- Entry detail (expanded row) -- */
  .entry-detail {
    padding: 0.75rem;
    background: var(--bg-surface0);
    border-radius: var(--radius-sm);
    margin: 0.35rem 0.5rem;
    font-size: 0.85rem;
  }
  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.25rem 1rem;
    margin-bottom: 0.5rem;
  }
  .detail-value pre,
  .detail-meta pre {
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 0.85rem;
    background: var(--bg-mantle);
    padding: 0.5rem;
    border-radius: var(--radius-sm);
    margin: 0.25rem 0 0.5rem;
    max-height: 300px;
    overflow-y: auto;
  }
  .detail-value code,
  .detail-grid code {
    font-size: 0.8rem;
    color: var(--text-sub);
  }

  /* -- Procedural extras -- */
  .caps-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    align-items: center;
    margin-bottom: 0.5rem;
  }

  /* -- Badges -- */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.2rem;
    padding: 0.15rem 0.4rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    font-size: 0.75rem;
    white-space: nowrap;
  }

  /* -- Annotation -- */
  .annotation { font-size: 0.8rem; margin: 0; }

  @media (max-width: 768px) {
    .stat-row {
      grid-template-columns: repeat(2, 1fr);
    }
    .col-key { width: auto; max-width: 150px; }
    .cell-value { max-width: 200px; }
    .filter-input { width: 120px; }
  }
</style>
