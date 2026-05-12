<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, post as apiPost } from '$lib/api/client';

  interface Service {
    unit: string;
    description: string;
    load_state: string;
    active_state: string;
    sub_state: string;
    pid: number | null;
    started_at: string;
    memory_bytes: number | null;
    running: boolean;
  }

  let services: Service[] = $state([]);
  let loading = $state(true);
  let error = $state('');
  let actionMsg = $state('');
  let actioning = $state<string | null>(null);

  function fmtUnit(unit: string): string {
    return unit.replace('openbad-', '').replace('.service', '').replace('.timer', ' (timer)').replace('.path', ' (path)');
  }

  function fmtMemory(bytes: number | null): string {
    if (bytes == null || bytes === 0) return '—';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function stateColor(s: Service): string {
    if (s.active_state === 'active') return 'var(--green)';
    if (s.active_state === 'failed') return 'var(--red)';
    if (s.active_state === 'activating' || s.active_state === 'deactivating') return 'var(--yellow)';
    return 'var(--text-dim)';
  }

  async function load(): Promise<void> {
    loading = true;
    error = '';
    try {
      const res = await apiGet<{ services: Service[] }>('/api/services');
      services = res.services ?? [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function doAction(unit: string, action: string): Promise<void> {
    if (actioning) return;
    actioning = `${unit}:${action}`;
    actionMsg = '';
    try {
      const res = await apiPost<{ result: string; error?: string; service?: Service }>(`/api/services/${unit}/${action}`, {});
      if (res.error) {
        actionMsg = `${action} ${fmtUnit(unit)}: ${res.error}`;
      } else {
        actionMsg = `${action} ${fmtUnit(unit)}: OK`;
        await load();
      }
    } catch (e) {
      actionMsg = `${action} ${fmtUnit(unit)} failed: ${e}`;
    } finally {
      actioning = null;
    }
  }

  onMount(load);
  const refreshInterval = setInterval(load, 10000);
  onDestroy(() => clearInterval(refreshInterval));
</script>

<div class="page-header">
  <h2>Services</h2>
  <p>OpenBaD system daemons, timers, and watchers</p>
</div>

<div class="toolbar">
  <span class="count">{services.filter(s => s.running).length}/{services.length} running</span>
  <button class="secondary" onclick={load}>↻ Refresh</button>
  {#if actionMsg}<span class="action-msg">{actionMsg}</span>{/if}
</div>

<Card label="System Units">
  {#if loading && services.length === 0}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">{error}</p>
  {:else if services.length === 0}
    <p class="empty">No OpenBaD units found.</p>
  {:else}
    <div class="services-grid">
      <div class="grid-header">
        <span>Unit</span>
        <span>State</span>
        <span>PID</span>
        <span>Memory</span>
        <span>Actions</span>
      </div>
      {#each services as s}
        <div class="service-row" class:inactive={!s.running}>
          <div class="unit-info">
            <span class="status-dot" style="color:{stateColor(s)}">●</span>
            <div>
              <span class="unit-name">{fmtUnit(s.unit)}</span>
              {#if s.description}
                <span class="unit-desc">{s.description}</span>
              {/if}
            </div>
          </div>
          <span class="state-badge" style="color:{stateColor(s)}">
            {s.active_state}/{s.sub_state}
          </span>
          <span class="pid">{s.pid ?? '—'}</span>
          <span class="memory">{fmtMemory(s.memory_bytes)}</span>
          <div class="actions">
            {#if s.running}
              <button class="small"
                      disabled={actioning !== null || s.unit === 'openbad-wui.service'}
                      onclick={() => doAction(s.unit, 'restart')}>
                ↻
              </button>
              <button class="small danger"
                      disabled={actioning !== null || s.unit === 'openbad-wui.service'}
                      onclick={() => doAction(s.unit, 'stop')}>
                ■
              </button>
            {:else}
              <button class="small go"
                      disabled={actioning !== null}
                      onclick={() => doAction(s.unit, 'start')}>
                ▶
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</Card>

<style>
  .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  .count { font-size: 0.85rem; color: var(--text-dim); }
  .action-msg { font-size: 0.82rem; color: var(--text-sub); }
  .empty, .muted { color: var(--text-dim); padding: 2rem; text-align: center; }
  .error-msg { color: var(--red); padding: 1rem; }

  .services-grid {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .grid-header {
    display: grid;
    grid-template-columns: 2fr 1fr 0.5fr 0.7fr 0.8fr;
    gap: 0.5rem;
    padding: 0.4rem 0.75rem;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .service-row {
    display: grid;
    grid-template-columns: 2fr 1fr 0.5fr 0.7fr 0.8fr;
    gap: 0.5rem;
    align-items: center;
    padding: 0.55rem 0.75rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    font-size: 0.83rem;
  }
  .service-row.inactive { opacity: 0.65; }
  .service-row:hover { background: var(--bg-surface2); }

  .unit-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    overflow: hidden;
  }
  .status-dot { font-size: 0.7rem; flex-shrink: 0; }
  .unit-name { font-weight: 600; display: block; }
  .unit-desc { font-size: 0.75rem; color: var(--text-dim); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .state-badge { font-size: 0.78rem; font-weight: 600; }
  .pid { color: var(--text-dim); font-variant-numeric: tabular-nums; }
  .memory { color: var(--text-dim); font-variant-numeric: tabular-nums; }

  .actions { display: flex; gap: 0.3rem; }

  button.small {
    font-size: 0.8rem;
    padding: 0.2rem 0.45rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface2);
    color: var(--text);
    border: 1px solid var(--line);
    cursor: pointer;
    line-height: 1;
  }
  button.small:hover { background: var(--bg-surface1); }
  button.small:disabled { opacity: 0.4; cursor: not-allowed; }
  button.small.danger { color: var(--red); }
  button.small.danger:hover { background: rgba(255,100,100,0.1); }
  button.small.go { color: var(--green); }
  button.small.go:hover { background: rgba(100,255,100,0.1); }

  @media (max-width: 900px) {
    .grid-header { display: none; }
    .service-row {
      grid-template-columns: 1fr;
      gap: 0.3rem;
    }
    .actions { justify-content: flex-end; }
  }
</style>
