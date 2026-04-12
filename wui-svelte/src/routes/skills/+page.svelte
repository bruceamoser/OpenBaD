<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet } from '$lib/api/client';

  interface CapabilityTool {
    name: string;
    signature: string;
    description: string;
  }

  interface Capability {
    id: string;
    label: string;
    icon: string;
    level: number;
    module: string;
    description: string;
    tools: CapabilityTool[];
    gates: string[];
  }

  let capabilities: Capability[] = $state([]);
  let loading = $state(true);
  let selected: Capability | null = $state(null);

  onMount(async () => {
    try {
      const res = await apiGet<{ capabilities: Capability[] }>('/api/capabilities');
      capabilities = res.capabilities ?? [];
    } catch {
      // static fallback: show empty state
    } finally {
      loading = false;
    }
  });

  function closeModal(): void { selected = null; }
  function handleKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') closeModal();
  }

  function levelBadgeColor(level: number): string {
    return level === 1 ? 'var(--green)' : 'var(--mauve)';
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page-header">
  <h2>Skills & Capabilities</h2>
  <p>Built-in tools available to the agent — click a card for details</p>
</div>

{#if loading}
  <p class="muted">Loading…</p>
{:else if capabilities.length === 0}
  <p class="empty">No capabilities registered.</p>
{:else}
  <div class="caps-section">
    <h3 class="section-heading">Level 1 — Core tools</h3>
    <div class="caps-grid">
      {#each capabilities.filter(c => c.level === 1) as cap}
        <button class="cap-card" onclick={() => selected = cap}>
          <span class="cap-icon">{cap.icon}</span>
          <div class="cap-info">
            <span class="cap-label">{cap.label}</span>
            <span class="cap-desc">{cap.description.slice(0, 70)}{cap.description.length > 70 ? '…' : ''}</span>
          </div>
          <span class="cap-level-badge" style="color:{levelBadgeColor(cap.level)}">L{cap.level}</span>
        </button>
      {/each}
    </div>
  </div>

  <div class="caps-section">
    <h3 class="section-heading">Level 2 — MCP extensions</h3>
    <div class="caps-grid">
      {#each capabilities.filter(c => c.level === 2) as cap}
        <button class="cap-card" onclick={() => selected = cap}>
          <span class="cap-icon">{cap.icon}</span>
          <div class="cap-info">
            <span class="cap-label">{cap.label}</span>
            <span class="cap-desc">{cap.description.slice(0, 70)}{cap.description.length > 70 ? '…' : ''}</span>
          </div>
          <span class="cap-level-badge" style="color:{levelBadgeColor(cap.level)}">L{cap.level}</span>
        </button>
      {/each}
    </div>
  </div>
{/if}

{#if selected}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal-backdrop" onclick={closeModal}>
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
         aria-labelledby="modal-title">
      <div class="modal-header">
        <span class="modal-icon">{selected.icon}</span>
        <div class="modal-title-group">
          <h3 id="modal-title">{selected.label}</h3>
          <code class="modal-module">{selected.module}</code>
        </div>
        <span class="modal-level-badge" style="color:{levelBadgeColor(selected.level)}">
          Level {selected.level}
        </span>
        <button class="modal-close" onclick={closeModal} aria-label="Close">✕</button>
      </div>

      <p class="modal-desc">{selected.description}</p>

      <section class="modal-section">
        <h4>Tools</h4>
        <div class="tool-list">
          {#each selected.tools as tool}
            <div class="tool-item">
              <code class="tool-sig">{tool.signature}</code>
              <p class="tool-desc">{tool.description}</p>
            </div>
          {/each}
        </div>
      </section>

      <section class="modal-section">
        <h4>Gates &amp; Restrictions</h4>
        <ul class="gate-list">
          {#each selected.gates as gate}
            <li>{gate}</li>
          {/each}
        </ul>
      </section>
    </div>
  </div>
{/if}

<style>
  .muted { color: var(--text-dim); text-align: center; padding: 2rem; }
  .empty { color: var(--text-dim); text-align: center; padding: 2rem; }
  .caps-section { margin-bottom: 2rem; }
  .section-heading { font-size: 0.9rem; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.75rem; }
  .caps-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.75rem; }
  .cap-card {
    display: flex; align-items: flex-start; gap: 0.75rem;
    padding: 0.85rem 1rem;
    background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius); cursor: pointer;
    text-align: left; transition: background 0.15s, border-color 0.15s;
    width: 100%;
  }
  .cap-card:hover { background: var(--bg-surface2); border-color: var(--blue); }
  .cap-icon { font-size: 1.6rem; flex-shrink: 0; }
  .cap-info { flex: 1; display: flex; flex-direction: column; gap: 0.2rem; overflow: hidden; }
  .cap-label { font-weight: 600; font-size: 0.9rem; }
  .cap-desc { font-size: 0.78rem; color: var(--text-dim); }
  .cap-level-badge { font-size: 0.7rem; font-weight: 700; flex-shrink: 0; }

  /* Modal */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 100; padding: 1rem;
  }
  .modal {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.5rem;
    max-width: 640px; width: 100%;
    max-height: 80vh; overflow-y: auto;
    display: flex; flex-direction: column; gap: 1rem;
  }
  .modal-header { display: flex; align-items: center; gap: 0.75rem; }
  .modal-icon { font-size: 2rem; flex-shrink: 0; }
  .modal-title-group { flex: 1; display: flex; flex-direction: column; gap: 0.15rem; }
  .modal-title-group h3 { margin: 0; font-size: 1.1rem; }
  .modal-module { font-size: 0.72rem; color: var(--text-dim); }
  .modal-level-badge { font-weight: 700; font-size: 0.8rem; }
  .modal-close {
    background: none; border: none;
    color: var(--text-dim); cursor: pointer; font-size: 1rem; padding: 0.25rem;
  }
  .modal-close:hover { color: var(--text); }
  .modal-desc { font-size: 0.88rem; color: var(--text-sub); line-height: 1.5; margin: 0; }
  .modal-section h4 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0 0 0.5rem; }
  .tool-list { display: flex; flex-direction: column; gap: 0.6rem; }
  .tool-item { display: flex; flex-direction: column; gap: 0.15rem; }
  .tool-sig {
    font-size: 0.78rem; padding: 0.3rem 0.5rem;
    background: var(--bg-base); border-radius: var(--radius-sm);
    color: var(--teal); word-break: break-all;
  }
  .tool-desc { font-size: 0.82rem; color: var(--text-sub); margin: 0; }
  .gate-list { margin: 0; padding-left: 1.25rem; display: flex; flex-direction: column; gap: 0.3rem; }
  .gate-list li { font-size: 0.82rem; color: var(--text-sub); }
</style>
