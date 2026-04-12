<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { endocrineLevels, fsmState } from '$lib/stores/websocket';
  import { get as apiGet } from '$lib/api/client';

  type Tab = 'all' | 'heartbeat' | 'tasks' | 'research' | 'endocrine' | 'fsm';
  interface LogEntry { ts: string; level: string; logger: string; msg: string; }

  const TABS: { id: Tab; label: string }[] = [
    { id: 'all',       label: 'All' },
    { id: 'heartbeat', label: 'Heartbeat' },
    { id: 'tasks',     label: 'Tasks' },
    { id: 'research',  label: 'Research' },
    { id: 'endocrine', label: 'Endocrine' },
    { id: 'fsm',       label: 'FSM' },
  ];

  const SYSTEM_FILTER: Record<Tab, string> = {
    all:       '',
    heartbeat: 'tasks',
    tasks:     'tasks',
    research:  'tasks',
    endocrine: 'endocrine',
    fsm:       'reflex_arc',
  };

  let activeTab: Tab = $state('all');
  let logs: LogEntry[] = $state([]);
  let loading = $state(true);
  let error = $state('');
  let autoRefresh = $state(true);
  let refreshTimer: ReturnType<typeof setInterval> | undefined;

  function levelColor(level: string): string {
    switch (level.toUpperCase()) {
      case 'DEBUG':    return 'var(--text-dim)';
      case 'INFO':     return 'var(--blue)';
      case 'WARNING':  return 'var(--yellow)';
      case 'ERROR':    return 'var(--red)';
      case 'CRITICAL': return 'var(--red)';
      default:         return 'var(--text)';
    }
  }

  async function loadLogs(): Promise<void> {
    try {
      const system = SYSTEM_FILTER[activeTab];
      const res = await apiGet<{ logs: LogEntry[] }>(`/api/debug/logs?system=${system}&limit=300`);
      logs = res.logs ?? [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  function switchTab(tab: Tab): void {
    activeTab = tab;
    loading = true;
    loadLogs();
  }

  $effect(() => {
    if (autoRefresh) {
      refreshTimer = setInterval(loadLogs, 3000);
    } else {
      if (refreshTimer !== undefined) clearInterval(refreshTimer);
    }
  });

  onMount(() => { loadLogs(); });
  onDestroy(() => { if (refreshTimer !== undefined) clearInterval(refreshTimer); });

  // Endocrine live values
  let dopamine = $derived($endocrineLevels?.dopamine ?? 0);
  let adrenaline = $derived($endocrineLevels?.adrenaline ?? 0);
  let cortisol = $derived($endocrineLevels?.cortisol ?? 0);
  let endorphin = $derived($endocrineLevels?.endorphin ?? 0);
  let fsmCurrent = $derived($fsmState?.current_state ?? 'IDLE');
</script>

<div class="page-header">
  <h2>Debug Logs</h2>
  <p>Live subsystem log viewer from the in-memory ring buffer</p>
</div>

<div class="tabs-row">
  {#each TABS as tab}
    <button class="tab-btn" class:active={activeTab === tab.id}
            onclick={() => switchTab(tab.id)}>{tab.label}</button>
  {/each}
  <div class="tab-spacer"></div>
  <label class="auto-refresh-toggle">
    <input type="checkbox" bind:checked={autoRefresh} />
    Auto-refresh
  </label>
  <button class="secondary" onclick={loadLogs}>↻ Refresh</button>
</div>

{#if activeTab === 'endocrine'}
  <Card label="Live Endocrine Levels">
    <div class="hormones">
      {#each [
        { name: 'Dopamine',   val: dopamine,   emoji: '🧠', color: 'var(--mauve)' },
        { name: 'Adrenaline', val: adrenaline, emoji: '⚡', color: 'var(--yellow)' },
        { name: 'Cortisol',   val: cortisol,   emoji: '🔥', color: 'var(--red)' },
        { name: 'Endorphin',  val: endorphin,  emoji: '✨', color: 'var(--green)' },
      ] as h}
        <div class="hormone-row">
          <span class="h-icon">{h.emoji}</span>
          <span class="h-name">{h.name}</span>
          <div class="h-bar"><div class="h-fill" style="width:{h.val * 100}%; background:{h.color}"></div></div>
          <span class="h-val" style="color:{h.color}">{(h.val * 100).toFixed(1)}%</span>
        </div>
      {/each}
    </div>
  </Card>
{:else if activeTab === 'fsm'}
  <Card label="FSM State">
    <div class="fsm-live">
      <span class="fsm-current">Current: <strong>{fsmCurrent}</strong></span>
      {#if $fsmState}
        <div class="fsm-details">
          <div class="detail-row">Previous: <code>{$fsmState.previous_state}</code></div>
          <div class="detail-row">Last trigger: <code>{$fsmState.trigger_event}</code></div>
        </div>
      {/if}
    </div>
  </Card>
{/if}

<Card label="Log Entries ({logs.length})">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if logs.length === 0}
    <p class="empty">No log entries captured for this subsystem yet.</p>
  {:else}
    <div class="log-list">
      {#each [...logs].reverse() as entry}
        <div class="log-row">
          <span class="log-ts">{new Date(entry.ts).toLocaleTimeString()}</span>
          <span class="log-level" style="color:{levelColor(entry.level)}">{entry.level}</span>
          <span class="log-logger">{entry.logger.replace('openbad.', '')}</span>
          <span class="log-msg">{entry.msg}</span>
        </div>
      {/each}
    </div>
  {/if}
</Card>

<style>
  .tabs-row {
    display: flex; gap: 0.25rem; align-items: center;
    margin-bottom: 1rem; flex-wrap: wrap;
  }
  .tab-btn {
    padding: 0.3rem 0.7rem; font-size: 0.82rem;
    background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius-sm); cursor: pointer; color: var(--text-sub);
    transition: background 0.15s;
  }
  .tab-btn:hover { background: var(--bg-surface2); }
  .tab-btn.active { background: var(--blue); border-color: var(--blue); color: var(--base); }
  .tab-spacer { flex: 1; }
  .auto-refresh-toggle { font-size: 0.82rem; color: var(--text-dim); display: flex; align-items: center; gap: 0.3rem; }
  .muted { color: var(--text-dim); padding: 2rem; text-align: center; }
  .empty { color: var(--text-dim); padding: 2rem; text-align: center; }
  .error-msg { color: var(--red); padding: 1rem; }
  .log-list { display: flex; flex-direction: column; gap: 1px; font-family: monospace; }
  .log-row {
    display: flex; gap: 0.5rem; align-items: baseline;
    padding: 0.2rem 0.4rem;
    background: var(--bg-surface1);
    border-radius: 2px; font-size: 0.78rem;
  }
  .log-row:nth-child(even) { background: var(--bg-base); }
  .log-ts { color: var(--text-dim); font-variant-numeric: tabular-nums; min-width: 7.5ch; flex-shrink: 0; }
  .log-level { font-weight: 700; min-width: 6ch; flex-shrink: 0; }
  .log-logger { color: var(--text-dim); min-width: 14ch; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .log-msg { color: var(--text); flex: 1; word-break: break-word; }
  .hormones { display: flex; flex-direction: column; gap: 0.6rem; }
  .hormone-row { display: flex; align-items: center; gap: 0.5rem; }
  .h-icon { font-size: 1rem; }
  .h-name { min-width: 7rem; font-size: 0.85rem; }
  .h-bar { flex: 1; height: 8px; background: var(--bg-surface2); border-radius: 4px; overflow: hidden; }
  .h-fill { height: 100%; border-radius: 4px; transition: width 0.4s; }
  .h-val { min-width: 4rem; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.82rem; }
  .fsm-live { padding: 0.5rem 0; }
  .fsm-current { font-size: 1.1rem; }
  .fsm-details { margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.85rem; }
  .detail-row { color: var(--text-dim); }
</style>
