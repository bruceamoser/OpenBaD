<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { fsmState } from '$lib/stores/websocket';
  import { get as apiGet } from '$lib/api/client';

  interface SystemEvent {
    ts: string;
    level: string;
    source: string;
    message: string;
    exception: string;
    function: string;
    line: number;
  }

  let events: SystemEvent[] = $state([]);
  let loading = $state(true);
  let autoRefresh = $state(true);
  let searchText = $state('');
  let levelFilter = $state('');
  let sourceFilter = $state('');
  let refreshTimer: ReturnType<typeof setInterval> | undefined;

  // Collect unique sources for filter dropdown
  let sources = $derived([...new Set(events.map(e => shortSource(e.source)))].sort());

  async function loadEvents(): Promise<void> {
    try {
      let url = '/api/events?limit=500';
      if (levelFilter) url += `&level=${levelFilter}`;
      if (sourceFilter) url += `&source=openbad.${sourceFilter}`;
      if (searchText) url += `&search=${encodeURIComponent(searchText)}`;
      const res = await apiGet<{ events: SystemEvent[] }>(url);
      events = res.events ?? [];
    } catch {
      // ignore
    } finally {
      loading = false;
    }
  }

  function levelClass(level: string): string {
    switch (level) {
      case 'ERROR': return 'level-error';
      case 'WARNING': return 'level-warning';
      case 'DEBUG': return 'level-debug';
      default: return 'level-info';
    }
  }

  function levelIcon(level: string): string {
    switch (level) {
      case 'ERROR': return '❌';
      case 'WARNING': return '⚠️';
      case 'DEBUG': return '🔍';
      default: return 'ℹ️';
    }
  }

  function shortSource(source: string): string {
    return source.replace(/^openbad\./, '');
  }

  $effect(() => {
    if (autoRefresh) {
      refreshTimer = setInterval(loadEvents, 5000);
    } else {
      if (refreshTimer !== undefined) clearInterval(refreshTimer);
    }
  });

  onMount(() => { loadEvents(); });
  onDestroy(() => { if (refreshTimer !== undefined) clearInterval(refreshTimer); });

  let fsmCurrent = $derived($fsmState?.current_state ?? 'IDLE');
</script>

<div class="page-header">
  <h2>Debug</h2>
  <p>Unified persistent event log with live subsystem state</p>
</div>

<!-- Live state panels -->
<div class="live-panels">
  <div class="live-card">
    <span class="live-label">FSM</span>
    <span class="live-value">{fsmCurrent}</span>
    {#if $fsmState}
      <span class="live-sub">← {$fsmState.previous_state} via {$fsmState.trigger_event}</span>
    {/if}
  </div>
</div>

<!-- Unified Event Log -->
<Card label="System Event Log">
  <div class="log-toolbar">
    <select bind:value={levelFilter} onchange={loadEvents}>
      <option value="">All levels</option>
      <option value="ERROR">Errors</option>
      <option value="WARNING">Warnings</option>
      <option value="INFO">Info</option>
      <option value="DEBUG">Debug</option>
    </select>
    <select bind:value={sourceFilter} onchange={loadEvents}>
      <option value="">All sources</option>
      {#each sources as src}
        <option value={src}>{src}</option>
      {/each}
    </select>
    <input type="text" placeholder="Search…" bind:value={searchText}
      onkeydown={(e) => { if (e.key === 'Enter') loadEvents(); }} />
    <div class="toolbar-spacer"></div>
    <label class="auto-toggle">
      <input type="checkbox" bind:checked={autoRefresh} /> Auto
    </label>
    <button class="secondary" onclick={loadEvents} disabled={loading}>
      {loading ? 'Loading…' : '↻ Refresh'}
    </button>
  </div>

  <div class="log-info">
    Persisted to disk · 7-day retention · {events.length} entries shown
  </div>

  {#if loading && events.length === 0}
    <p class="muted">Loading…</p>
  {:else if events.length === 0}
    <p class="muted">No events match the current filters.</p>
  {:else}
    <div class="event-list">
      {#each events as entry, i (i)}
        <div class="event-row {levelClass(entry.level)}">
          <span class="ev-icon">{levelIcon(entry.level)}</span>
          <span class="ev-time">{entry.ts}</span>
          <span class="ev-source">{shortSource(entry.source)}</span>
          <span class="ev-fn">{entry.function}:{entry.line}</span>
          <span class="ev-msg">{entry.message}</span>
          {#if entry.exception}
            <div class="ev-exc">{entry.exception}</div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</Card>

<style>
  /* Live panels */
  .live-panels {
    display: flex;
    gap: 0.6rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .live-card {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 0.82rem;
    min-width: 0;
  }
  .live-label { font-weight: 600; white-space: nowrap; }
  .live-value { font-weight: 700; color: var(--blue); }
  .live-sub { font-size: 0.72rem; color: var(--text-dim); }

  /* Toolbar */
  .log-toolbar {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
  }
  .log-toolbar select, .log-toolbar input {
    font-size: 0.8rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
  }
  .log-toolbar input { min-width: 10rem; }
  .toolbar-spacer { flex: 1; }
  .auto-toggle {
    font-size: 0.8rem;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    gap: 0.25rem;
    cursor: pointer;
  }
  .log-info {
    font-size: 0.72rem;
    color: var(--text-dim);
    margin-bottom: 0.5rem;
  }
  .muted { color: var(--text-dim); padding: 2rem; text-align: center; }

  /* Event list */
  .event-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 600px;
    overflow-y: auto;
  }
  .event-row {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.35rem 0.5rem;
    border-radius: 2px;
    background: var(--bg-surface1);
    font-size: 0.78rem;
    flex-wrap: wrap;
    border-left: 3px solid transparent;
  }
  .event-row:nth-child(even) { background: var(--bg-base); }
  .event-row.level-error { border-left-color: var(--red); background: color-mix(in srgb, var(--red) 5%, var(--bg-surface1)); }
  .event-row.level-warning { border-left-color: var(--yellow); }
  .event-row.level-info { border-left-color: var(--blue, var(--border)); }
  .event-row.level-debug { border-left-color: var(--text-dim); opacity: 0.7; }
  .ev-icon { font-size: 0.85rem; flex-shrink: 0; }
  .ev-time { font-family: monospace; font-size: 0.7rem; color: var(--text-dim); min-width: 5rem; flex-shrink: 0; }
  .ev-source {
    font-size: 0.72rem; font-weight: 600; color: var(--text-sub);
    padding: 0.05rem 0.35rem; background: var(--bg-surface2, var(--bg));
    border-radius: 999px; flex-shrink: 0;
  }
  .ev-fn { font-family: monospace; font-size: 0.68rem; color: var(--text-dim); flex-shrink: 0; }
  .ev-msg { color: var(--text); flex: 1; word-break: break-word; }
  .ev-exc {
    width: 100%;
    font-size: 0.72rem;
    color: var(--red);
    font-family: monospace;
    padding: 0.2rem 0.4rem;
    margin-top: 0.15rem;
    background: color-mix(in srgb, var(--red) 5%, var(--bg-surface1));
    border-radius: var(--radius-sm);
    white-space: pre-wrap;
  }
</style>
