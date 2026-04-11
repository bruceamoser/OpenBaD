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

<h2>Providers</h2>

<!-- Provider list with health indicators -->
<Card label="Registered Providers">
  {#if providers.length === 0}
    <p class="muted">No providers registered.</p>
  {:else}
    <ul class="provider-list">
      {#each providers as p}
        <li>
          <span class="health-dot" style="background:{healthColor(p)}"></span>
          <strong>{p.name}</strong> — {p.model}
          <span class="tag">{p.verified ? 'verified' : 'unverified'}</span>
        </li>
      {/each}
    </ul>
  {/if}
</Card>

<!-- System assignments -->
<Card label="System Assignments">
  <div class="systems-grid">
    {#each COGNITIVE_SYSTEMS as sys}
      <div class="sys-row">
        <label class="sys-label">{sys.toUpperCase()}</label>
        <input
          type="text"
          placeholder="provider"
          value={systems[sys]?.provider ?? ''}
          oninput={(e: Event) => setSystem(sys, 'provider', (e.target as HTMLInputElement).value)}
        />
        <input
          type="text"
          placeholder="model"
          value={systems[sys]?.model ?? ''}
          oninput={(e: Event) => setSystem(sys, 'model', (e.target as HTMLInputElement).value)}
        />
      </div>
    {/each}
  </div>
</Card>

<!-- Fallback chain -->
<Card label="Fallback Chain">
  <p class="hint">Drag to reorder. First entry is tried first.</p>
  <ol class="fallback-chain">
    {#each fallbackChain as entry, i}
      <li
        draggable="true"
        class:dragging={dragIdx === i}
        ondragstart={() => dragStart(i)}
        ondragover={dragOver}
        ondrop={() => drop(i)}
      >
        {entry.provider} / {entry.model}
      </li>
    {/each}
  </ol>
</Card>

<!-- Cortisol indicator -->
<Card label="Cortisol Level">
  <div class="cortisol-bar-wrapper">
    <div
      class="cortisol-bar"
      style="width:{cortisol * 100}%; background:{cortisolColor(cortisol)}"
    ></div>
  </div>
  <span class="cortisol-value">{(cortisol * 100).toFixed(0)}%</span>
</Card>

<!-- Save -->
<div class="actions">
  <button onclick={save} disabled={!dirty || saving}>
    {saving ? 'Saving…' : 'Save'}
  </button>
  {#if statusMsg}
    <span class="status">{statusMsg}</span>
  {/if}
</div>

<style>
  .provider-list {
    list-style: none;
    padding: 0;
  }
  .provider-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0;
  }
  .health-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  .tag {
    font-size: 0.75rem;
    opacity: 0.7;
    margin-left: auto;
  }
  .muted {
    opacity: 0.5;
  }

  .systems-grid {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .sys-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .sys-label {
    width: 8rem;
    font-weight: 600;
  }
  .sys-row input {
    flex: 1;
    min-width: 6rem;
    padding: 0.3rem 0.5rem;
  }

  .fallback-chain {
    padding-left: 1.5rem;
  }
  .fallback-chain li {
    padding: 0.4rem;
    cursor: grab;
    border: 1px dashed transparent;
  }
  .fallback-chain li:hover {
    border-color: #888;
  }
  .fallback-chain li.dragging {
    opacity: 0.4;
  }
  .hint {
    font-size: 0.8rem;
    opacity: 0.6;
    margin: 0 0 0.5rem 0;
  }

  .cortisol-bar-wrapper {
    height: 12px;
    background: #333;
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 0.25rem;
  }
  .cortisol-bar {
    height: 100%;
    transition: width 0.3s ease;
    border-radius: 6px;
  }
  .cortisol-value {
    font-size: 0.85rem;
    opacity: 0.8;
  }

  .actions {
    display: flex;
    gap: 1rem;
    align-items: center;
    margin-top: 1rem;
  }
  .actions button {
    padding: 0.5rem 1.5rem;
  }
  .status {
    font-size: 0.85rem;
    opacity: 0.8;
  }
</style>
