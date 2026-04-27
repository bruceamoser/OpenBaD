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

  // Recall
  let recallQuery = $state('');
  let recallResults: RecallResult[] = $state([]);
  let recalling = $state(false);

  // -- Helpers ----------------------------------------------------------------

  function fmtTime(ts: number): string {
    if (!ts) return '—';
    const diff = (Date.now() / 1000) - ts;
    if (diff < 0) return 'future';
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
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
        <div class="recall-results">
          {#each recallResults as r, i}
            <div class="recall-item">
              <div class="recall-header">
                <span class="rank">#{i + 1}</span>
                <span class="badge">{tierIcon(r.tier)} {r.tier}</span>
                <span class="score badge">{r.score.toFixed(4)}</span>
                <span class="recall-key">{r.key}</span>
              </div>
              <p class="recall-value">{truncate(r.value, 200)}</p>
              {#if r.library_annotations && r.library_annotations.length > 0}
                <div class="lib-annotations">
                  {#each r.library_annotations as ann}
                    <p class="annotation text-blue">{ann}</p>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
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
    <button class="secondary" onclick={loadStm}>↻ Refresh</button>
  </div>

  <Card label="Short-Term Memory">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if stmEntries.length === 0}
      <p class="muted">STM is empty — no active short-term memories.</p>
    {:else}
      <div class="entry-list">
        {#each stmEntries as entry}
          <div class="entry-row" class:expanded={expandedKey === entry.key}>
            <button class="ghost entry-toggle" onclick={() => toggleExpand(entry.key)}>
              <span class="entry-key">{entry.key}</span>
              <span class="entry-preview muted">{truncate(entry.value, 80)}</span>
              <div class="entry-badges">
                {#if entry.ttl_remaining != null}
                  <span class="badge" class:text-yellow={entry.ttl_remaining < 300}>
                    TTL: {fmtDuration(entry.ttl_remaining)}
                  </span>
                {/if}
                <span class="badge">{fmtTime(entry.created_at)}</span>
              </div>
            </button>
            {#if expandedKey === entry.key}
              <div class="entry-detail">
                <div class="detail-grid">
                  <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                  <div><strong>Created:</strong> {new Date(entry.created_at * 1000).toLocaleString()}</div>
                  <div><strong>Accessed:</strong> {fmtTime(entry.accessed_at)} ({entry.access_count}×)</div>
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
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </Card>

<!-- ═══════ EPISODIC TAB ═══════ -->
{:else if activeTab === 'episodic'}
  <div class="toolbar">
    <span class="count">{episodicEntries.length} of {episodicTotal} entries</span>
    <button class="secondary" onclick={loadEpisodic}>↻ Refresh</button>
  </div>

  <Card label="Episodic Memory (Recent)">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if episodicEntries.length === 0}
      <p class="muted">No episodic memories recorded yet.</p>
    {:else}
      <div class="entry-list">
        {#each episodicEntries as entry}
          <div class="entry-row" class:expanded={expandedKey === entry.key}>
            <button class="ghost entry-toggle" onclick={() => toggleExpand(entry.key)}>
              <span class="entry-key">{entry.key}</span>
              <span class="entry-preview muted">{truncate(entry.value, 80)}</span>
              <div class="entry-badges">
                <span class="badge">{fmtTime(entry.created_at)}</span>
                <span class="badge">{entry.access_count}×</span>
              </div>
            </button>
            {#if expandedKey === entry.key}
              <div class="entry-detail">
                <div class="detail-grid">
                  <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                  <div><strong>Created:</strong> {new Date(entry.created_at * 1000).toLocaleString()}</div>
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
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </Card>

<!-- ═══════ SEMANTIC TAB ═══════ -->
{:else if activeTab === 'semantic'}
  <div class="toolbar">
    <span class="count">{semanticEntries.length} entries</span>
    <button class="secondary" onclick={loadSemantic}>↻ Refresh</button>
  </div>

  <Card label="Semantic Memory">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if semanticEntries.length === 0}
      <p class="muted">No semantic memories stored yet.</p>
    {:else}
      <div class="entry-list">
        {#each semanticEntries as entry}
          <div class="entry-row" class:expanded={expandedKey === entry.key}>
            <button class="ghost entry-toggle" onclick={() => toggleExpand(entry.key)}>
              <span class="entry-key">{entry.key}</span>
              <span class="entry-preview muted">{truncate(entry.value, 80)}</span>
              <div class="entry-badges">
                {#if entry.has_vector}
                  <span class="badge text-green">🔢 vectorized</span>
                {/if}
                {#if entry.metadata?.library_refs?.length}
                  <span class="badge text-blue">📚 {entry.metadata.library_refs.length} refs</span>
                {/if}
                {#if entry.metadata?.tags?.length}
                  {#each entry.metadata.tags as tag}
                    <span class="badge">{tag}</span>
                  {/each}
                {/if}
                <span class="badge">{fmtTime(entry.created_at)}</span>
              </div>
            </button>
            {#if expandedKey === entry.key}
              <div class="entry-detail">
                <div class="detail-grid">
                  <div><strong>Entry ID:</strong> <code>{entry.entry_id}</code></div>
                  <div><strong>Created:</strong> {new Date(entry.created_at * 1000).toLocaleString()}</div>
                  <div><strong>Accessed:</strong> {fmtTime(entry.accessed_at)} ({entry.access_count}×)</div>
                  <div><strong>Has Vector:</strong> {entry.has_vector ? 'Yes' : 'No'}</div>
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
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </Card>

<!-- ═══════ PROCEDURAL TAB ═══════ -->
{:else if activeTab === 'procedural'}
  <div class="toolbar">
    <span class="count">{proceduralEntries.length} skills</span>
    <button class="secondary" onclick={loadProcedural}>↻ Refresh</button>
  </div>

  <Card label="Procedural Memory (Skills)">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if proceduralEntries.length === 0}
      <p class="muted">No procedural skills stored yet.</p>
    {:else}
      <div class="entry-list">
        {#each proceduralEntries as entry}
          <div class="entry-row" class:expanded={expandedKey === entry.key}>
            <button class="ghost entry-toggle" onclick={() => toggleExpand(entry.key)}>
              <span class="entry-key">{entry.key}</span>
              {#if entry.skill}
                <span class="entry-preview muted">{entry.skill.description}</span>
              {:else}
                <span class="entry-preview muted">{truncate(entry.value, 80)}</span>
              {/if}
              <div class="entry-badges">
                {#if entry.skill}
                  <span class="badge confidence" title="Bayesian confidence">
                    {(entry.skill.confidence * 100).toFixed(0)}%
                  </span>
                  <span class="badge text-green">✓ {entry.skill.success_count}</span>
                  <span class="badge text-red">✗ {entry.skill.failure_count}</span>
                {/if}
              </div>
            </button>
            {#if expandedKey === entry.key}
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
                  <div class="confidence-bar-container">
                    <strong>Confidence:</strong>
                    <div class="confidence-bar">
                      <div
                        class="confidence-fill"
                        class:low={entry.skill.confidence < 0.3}
                        class:mid={entry.skill.confidence >= 0.3 && entry.skill.confidence < 0.7}
                        class:high={entry.skill.confidence >= 0.7}
                        style="width: {entry.skill.confidence * 100}%"
                      ></div>
                    </div>
                  </div>
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
            {/if}
          </div>
        {/each}
      </div>
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
  .tab-bar button:hover {
    color: var(--text);
  }
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
  .recall-results {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .recall-item {
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface0);
    border: 1px solid var(--bg-surface1);
  }
  .recall-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }
  .rank { font-weight: 700; color: var(--text-dim); min-width: 2rem; }
  .score { font-family: monospace; font-size: 0.8rem; }
  .recall-key { font-weight: 600; color: var(--text); }
  .recall-value {
    font-size: 0.85rem;
    color: var(--text-sub);
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .lib-annotations { margin-top: 0.25rem; }
  .annotation { font-size: 0.8rem; margin: 0; }

  /* -- Toolbar -- */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
  }
  .count { font-weight: 600; }

  /* -- Entry list -- */
  .entry-list {
    display: flex;
    flex-direction: column;
    gap: 0;
  }
  .entry-row {
    border-bottom: 1px solid var(--bg-surface1);
  }
  .entry-row:last-child { border-bottom: none; }
  .entry-toggle {
    display: grid;
    grid-template-columns: minmax(120px, auto) 1fr auto;
    gap: 0.5rem;
    align-items: center;
    width: 100%;
    padding: 0.5rem 0.25rem;
    text-align: left;
    font-size: 0.85rem;
    cursor: pointer;
    border-radius: var(--radius-sm);
  }
  .entry-toggle:hover { background: var(--bg-surface1); }
  .entry-key {
    font-weight: 600;
    color: var(--blue);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .entry-preview {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .entry-badges {
    display: flex;
    gap: 0.35rem;
    flex-shrink: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  /* -- Entry detail -- */
  .entry-detail {
    padding: 0.75rem;
    background: var(--bg-surface0);
    border-radius: var(--radius-sm);
    margin-bottom: 0.5rem;
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
  .confidence {
    font-weight: 700;
  }
  .caps-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    align-items: center;
    margin-bottom: 0.5rem;
  }
  .confidence-bar-container {
    margin: 0.5rem 0;
  }
  .confidence-bar {
    height: 0.75rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    overflow: hidden;
    margin-top: 0.25rem;
  }
  .confidence-fill {
    height: 100%;
    border-radius: var(--radius-sm);
    transition: width 0.3s;
  }
  .confidence-fill.low { background: var(--red); }
  .confidence-fill.mid { background: var(--yellow); }
  .confidence-fill.high { background: var(--green); }

  @media (max-width: 768px) {
    .stat-row {
      grid-template-columns: repeat(2, 1fr);
    }
    .entry-toggle {
      grid-template-columns: 1fr;
    }
  }
</style>
