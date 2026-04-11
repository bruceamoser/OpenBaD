<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, put as apiPut } from '$lib/api/client';
  import { endocrineLevels } from '$lib/stores/websocket';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface ProviderEntry {
    name: string;
    model: string;
    verified: boolean;
  }

  interface SystemAssignment {
    provider: string;
    model: string;
  }

  interface FallbackEntry {
    provider: string;
    model: string;
  }

  interface ProvidersData {
    enabled: boolean;
    default_provider: string;
    providers: ProviderEntry[];
  }

  interface SystemsData {
    systems: Record<string, SystemAssignment>;
    default_fallback_chain: FallbackEntry[];
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  const COGNITIVE_SYSTEMS = ['chat', 'reasoning', 'reactions', 'sleep'];

  let providers: ProviderEntry[] = $state([]);
  let systems: Record<string, SystemAssignment> = $state({});
  let fallbackChain: FallbackEntry[] = $state([]);
  let dirty = $state(false);
  let saving = $state(false);
  let statusMsg = $state('');
  let dragIdx: number | null = $state(null);

  // Derived cortisol from WS
  let cortisol = $derived($endocrineLevels?.cortisol ?? 0);

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function load(): Promise<void> {
    try {
      const pData = await apiGet<ProvidersData>('/api/providers');
      providers = pData.providers ?? [];

      const sData = await apiGet<SystemsData>('/api/systems');
      systems = sData.systems ?? {};
      fallbackChain = sData.default_fallback_chain ?? [];
    } catch (e) {
      statusMsg = `Load failed: ${e}`;
    }
  }

  onMount(() => { load(); });

  // ----------------------------------------------------------------
  // Save
  // ----------------------------------------------------------------

  async function save(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/systems', {
        systems,
        default_fallback_chain: fallbackChain,
      });
      dirty = false;
      statusMsg = 'Saved';
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  // ----------------------------------------------------------------
  // System assignment helpers
  // ----------------------------------------------------------------

  function setSystem(sys: string, field: 'provider' | 'model', val: string): void {
    if (!systems[sys]) systems[sys] = { provider: '', model: '' };
    systems[sys][field] = val;
    dirty = true;
  }

  // ----------------------------------------------------------------
  // Fallback chain drag-to-reorder
  // ----------------------------------------------------------------

  function dragStart(idx: number): void {
    dragIdx = idx;
  }

  function dragOver(e: DragEvent): void {
    e.preventDefault();
  }

  function drop(targetIdx: number): void {
    if (dragIdx === null || dragIdx === targetIdx) return;
    const item = fallbackChain[dragIdx];
    const updated = [...fallbackChain];
    updated.splice(dragIdx, 1);
    updated.splice(targetIdx, 0, item);
    fallbackChain = updated;
    dragIdx = null;
    dirty = true;
  }

  // ----------------------------------------------------------------
  // Health indicator helpers
  // ----------------------------------------------------------------

  function healthColor(entry: ProviderEntry): string {
    if (entry.verified) return '#22c55e';      // green
    return '#ef4444';                           // red
  }

  function cortisolColor(level: number): string {
    if (level < 0.3) return '#22c55e';
    if (level < 0.7) return '#eab308';
    return '#ef4444';
  }
</script>

<div class="page-header">
  <h2>Providers</h2>
  <p>Manage LLM providers, system assignments, and fallback chains</p>
</div>

<div class="grid">
  <!-- Provider list -->
  <Card label="Registered Providers">
    {#if providers.length === 0}
      <p class="empty">No providers registered yet. Add one via the setup wizard or config.</p>
    {:else}
      <div class="provider-list">
        {#each providers as p}
          <div class="provider-item">
            <span class="health-dot" style="background:{healthColor(p)}"></span>
            <div class="provider-info">
              <span class="provider-name">{p.name}</span>
              <span class="provider-model">{p.model}</span>
            </div>
            <span class="badge" class:verified={p.verified}>{p.verified ? '✓ Verified' : 'Unverified'}</span>
          </div>
        {/each}
      </div>
    {/if}
  </Card>

  <!-- Cortisol -->
  <Card label="Provider Stress (Cortisol)">
    <div class="cortisol-section">
      <div class="cortisol-header">
        <span class="cortisol-emoji">🔥</span>
        <span class="cortisol-val" style="color:{cortisolColor(cortisol)}">{(cortisol * 100).toFixed(0)}%</span>
      </div>
      <div class="cortisol-bar-bg">
        <div class="cortisol-fill" style="width:{cortisol * 100}%; background:{cortisolColor(cortisol)}"></div>
      </div>
      <p class="hint">High cortisol triggers fallback chain escalation.</p>
    </div>
  </Card>

  <!-- System assignments -->
  <div class="full-width">
    <Card label="System Assignments">
      <div class="systems-grid">
        {#each COGNITIVE_SYSTEMS as sys}
          <div class="sys-row">
            <div class="sys-label-wrap">
              <span class="sys-icon">
                {#if sys === 'chat'}💬{:else if sys === 'reasoning'}🧠{:else if sys === 'reactions'}⚡{:else}😴{/if}
              </span>
              <span class="sys-label">{sys}</span>
            </div>
            <input
              type="text"
              placeholder="Provider name"
              value={systems[sys]?.provider ?? ''}
              oninput={(e: Event) => setSystem(sys, 'provider', (e.target as HTMLInputElement).value)}
            />
            <input
              type="text"
              placeholder="Model name"
              value={systems[sys]?.model ?? ''}
              oninput={(e: Event) => setSystem(sys, 'model', (e.target as HTMLInputElement).value)}
            />
          </div>
        {/each}
      </div>
    </Card>
  </div>

  <!-- Fallback chain -->
  <div class="full-width">
    <Card label="Fallback Chain">
      <p class="hint">Drag to reorder. First entry is tried first on provider failure.</p>
      {#if fallbackChain.length === 0}
        <p class="empty">No fallback entries configured.</p>
      {:else}
        <div class="fallback-list">
          {#each fallbackChain as entry, i}
            <div
              role="listitem"
              class="fallback-item"
              class:dragging={dragIdx === i}
              draggable="true"
              ondragstart={() => dragStart(i)}
              ondragover={dragOver}
              ondrop={() => drop(i)}
            >
              <span class="fallback-rank">{i + 1}</span>
              <span class="fallback-grip">⠿</span>
              <span class="fallback-name">{entry.provider}</span>
              <span class="fallback-model">{entry.model}</span>
            </div>
          {/each}
        </div>
      {/if}
    </Card>
  </div>
</div>

<!-- Actions -->
<div class="actions-bar">
  <button onclick={save} disabled={!dirty || saving}>
    {saving ? 'Saving…' : 'Save Changes'}
  </button>
  {#if statusMsg}
    <span class="status-msg">{statusMsg}</span>
  {/if}
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  .full-width { grid-column: 1 / -1; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

  .provider-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .provider-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 0.75rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
  }
  .health-dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
    box-shadow: 0 0 6px currentColor;
  }
  .provider-info { display: flex; flex-direction: column; flex: 1; }
  .provider-name { font-weight: 600; font-size: 0.9rem; }
  .provider-model { font-size: 0.8rem; color: var(--text-dim); }
  .verified { background: rgba(166, 227, 161, 0.15); color: var(--green); }
  .empty { color: var(--text-dim); font-size: 0.9rem; }

  .cortisol-section { display: flex; flex-direction: column; gap: 0.6rem; }
  .cortisol-header { display: flex; align-items: center; gap: 0.5rem; }
  .cortisol-emoji { font-size: 1.3rem; }
  .cortisol-val { font-size: 1.8rem; font-weight: 700; }
  .cortisol-bar-bg {
    height: 8px; background: var(--bg-surface1); border-radius: 4px; overflow: hidden;
  }
  .cortisol-fill { height: 100%; border-radius: 4px; transition: width 0.4s var(--ease); }

  .systems-grid { display: flex; flex-direction: column; gap: 0.6rem; }
  .sys-row {
    display: flex; gap: 0.75rem; align-items: center;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  .sys-label-wrap { display: flex; align-items: center; gap: 0.4rem; width: 8rem; flex-shrink: 0; }
  .sys-icon { font-size: 1rem; }
  .sys-label { font-weight: 600; font-size: 0.85rem; text-transform: capitalize; }
  .sys-row input { flex: 1; min-width: 0; }

  .fallback-list { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 0.5rem; }
  .fallback-item {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
    cursor: grab; border: 1px solid transparent; transition: all 0.15s var(--ease);
  }
  .fallback-item:hover { border-color: var(--bg-surface2); }
  .fallback-item.dragging { opacity: 0.4; }
  .fallback-rank {
    width: 1.5rem; height: 1.5rem; display: flex; align-items: center; justify-content: center;
    background: var(--bg-surface2); border-radius: 50%; font-size: 0.75rem; font-weight: 700; flex-shrink: 0;
  }
  .fallback-grip { color: var(--text-dim); }
  .fallback-name { font-weight: 600; }
  .fallback-model { color: var(--text-dim); font-size: 0.85rem; }

  .hint { font-size: 0.8rem; color: var(--text-dim); }

  .actions-bar {
    display: flex; gap: 1rem; align-items: center; margin-top: 1.25rem;
    padding-top: 1rem; border-top: 1px solid var(--border);
  }
  .status-msg { font-size: 0.85rem; color: var(--text-sub); }
</style>
