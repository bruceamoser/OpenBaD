<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import {
    get as apiGet,
    put as apiPut,
    del as apiDel,
  } from '$lib/api/client';
  import { toolbeltHealth } from '$lib/stores/websocket';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface ToolEntry {
    name: string;
    role: string;
    health: string;
    equipped: boolean;
  }

  interface ChatCallableTool {
    name: string;
    description: string;
  }

  interface ToolSurfaces {
    runtime_belt: string;
    embedded_tools: string;
  }

  interface AutoSwapEvent {
    ts: string;
    role: string;
    from_tool: string;
    to_tool: string;
    reason: string;
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  const TOOL_ROLES = [
    'CLI', 'WEB_SEARCH', 'MEMORY', 'MEDIA',
    'CODE', 'FILE_SYSTEM', 'COMMUNICATION',
  ];

  let cabinet: ToolEntry[] = $state([]);
  let swapLog: AutoSwapEvent[] = $state([]);
  let chatCallableTools: ChatCallableTool[] = $state([]);
  let toolSurfaces: ToolSurfaces = $state({
    runtime_belt: '',
    embedded_tools: '',
  });
  let statusMsg = $state('');

  // Group cabinet by role
  let grouped = $derived(
    TOOL_ROLES.reduce<Record<string, ToolEntry[]>>((acc, role) => {
      acc[role] = cabinet.filter((t) => t.role === role);
      return acc;
    }, {}),
  );

  // Belt = equipped tools
  let belt = $derived(cabinet.filter((t) => t.equipped));

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function load(): Promise<void> {
    try {
      const data = await apiGet<{
        cabinet: Record<string, ToolEntry[]>;
        belt: Record<string, string | null>;
        swap_log?: AutoSwapEvent[];
        chat_callable_tools?: ChatCallableTool[];
        tool_surfaces?: ToolSurfaces;
      }>('/api/toolbelt');
      cabinet = Object.values(data.cabinet ?? {}).flat();
      swapLog = (data.swap_log ?? []).slice(-20);
      chatCallableTools = data.chat_callable_tools ?? [];
      toolSurfaces = data.tool_surfaces ?? toolSurfaces;
    } catch (e) {
      statusMsg = `Load failed: ${e}`;
    }
  }

  onMount(() => { load(); });

  // ----------------------------------------------------------------
  // Equip / Unequip
  // ----------------------------------------------------------------

  async function equip(role: string, toolName: string): Promise<void> {
    // Optimistic update
    cabinet = cabinet.map((t) => ({
      ...t,
      equipped: t.role === role
        ? t.name === toolName
        : t.equipped,
    }));
    try {
      await apiPut(`/api/toolbelt/${role}`, { tool: toolName });
    } catch (e) {
      statusMsg = `Equip failed: ${e}`;
      await load();
    }
  }

  async function unequip(role: string): Promise<void> {
    cabinet = cabinet.map((t) => ({
      ...t,
      equipped: t.role === role ? false : t.equipped,
    }));
    try {
      await apiDel(`/api/toolbelt/${role}`);
    } catch (e) {
      statusMsg = `Unequip failed: ${e}`;
      await load();
    }
  }

  // ----------------------------------------------------------------
  // Health helpers
  // ----------------------------------------------------------------

  function healthColor(h: string): string {
    if (h === 'AVAILABLE') return '#22c55e';
    if (h === 'DEGRADED') return '#eab308';
    return '#ef4444';
  }
</script>

<div class="page-header">
  <h2>Toolbelt</h2>
  <p>Runtime belt assignments and chat-callable embedded tools are separate surfaces</p>
</div>

<Card label="Tooling Surfaces">
  <div class="surface-grid">
    <div class="surface-card">
      <h4>Runtime Toolbelt</h4>
      <p>{toolSurfaces.runtime_belt}</p>
    </div>
    <div class="surface-card">
      <h4>Embedded Skills / Chat Tools</h4>
      <p>{toolSurfaces.embedded_tools}</p>
    </div>
  </div>
</Card>

<Card label={`Chat-Callable Embedded Tools (${chatCallableTools.length})`}>
  <p class="hint">
    These are the structured function schemas the chat agent can call directly. They are not limited by the currently equipped runtime belt below.
  </p>
  <div class="embedded-tools">
    {#each chatCallableTools as tool}
      <div class="embedded-tool-row">
        <span class="embedded-tool-name">{tool.name}</span>
        <span class="embedded-tool-desc">{tool.description}</span>
      </div>
    {/each}
  </div>
</Card>

<!-- Belt (equipped) -->
<Card label="Equipped Belt">
  {#if belt.length === 0}
    <div class="empty-belt">
      <span class="empty-icon">🔧</span>
      <p>No tools equipped yet. Select tools from the cabinet below.</p>
    </div>
  {:else}
    <div class="belt-grid">
      {#each belt as tool}
        <div class="belt-chip">
          <span class="health-dot" style="background:{healthColor(tool.health)}"></span>
          <div class="chip-info">
            <span class="chip-name">{tool.name}</span>
            <span class="chip-role">{tool.role}</span>
          </div>
          <button class="ghost danger-text" onclick={() => unequip(tool.role)}>✕</button>
        </div>
      {/each}
    </div>
  {/if}
</Card>

<!-- Cabinet grouped by role -->
<div class="cabinet">
  {#each TOOL_ROLES as role}
    {#if (grouped[role] ?? []).length > 0}
      <Card label={role.replace('_', ' ')}>
        <div class="tool-list">
          {#each grouped[role] as tool}
            <div class="tool-row" class:is-equipped={tool.equipped}>
              <span class="health-dot" style="background:{healthColor(tool.health)}"></span>
              <span class="tool-name">{tool.name}</span>
              <span class="health-tag" class:ok={tool.health === 'AVAILABLE'} class:degraded={tool.health === 'DEGRADED'}>
                {tool.health.toLowerCase()}
              </span>
              {#if tool.equipped}
                <span class="equipped-badge">✓ Equipped</span>
              {:else}
                <button class="secondary sm" onclick={() => equip(role, tool.name)}>Equip</button>
              {/if}
            </div>
          {/each}
        </div>
      </Card>
    {/if}
  {/each}
</div>

<!-- Auto-swap event log -->
<Card label="Auto-Swap Log">
  {#if swapLog.length === 0}
    <p class="hint">No auto-swap events yet. Swaps are triggered by health degradation.</p>
  {:else}
    <div class="swap-log">
      {#each swapLog as evt}
        <div class="swap-row">
          <span class="swap-ts">{evt.ts}</span>
          <span class="swap-role">{evt.role}</span>
          <span class="swap-arrow">{evt.from_tool} → {evt.to_tool}</span>
          <span class="swap-reason">{evt.reason}</span>
        </div>
      {/each}
    </div>
  {/if}
</Card>

{#if statusMsg}
  <div class="status-toast">{statusMsg}</div>
{/if}

<style>
  .surface-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
    gap: 0.75rem;
  }

  .surface-card {
    padding: 0.85rem 1rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    border: 1px solid var(--bg-surface2);
  }

  .surface-card h4 {
    margin: 0 0 0.35rem;
    font-size: 0.92rem;
  }

  .surface-card p {
    margin: 0;
    font-size: 0.84rem;
    color: var(--text-dim);
    line-height: 1.45;
  }

  .embedded-tools {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
  }

  .embedded-tool-row {
    display: grid;
    grid-template-columns: minmax(12rem, 16rem) 1fr;
    gap: 0.75rem;
    padding: 0.45rem 0.6rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
  }

  .embedded-tool-name {
    font-weight: 600;
    font-family: var(--font-mono, monospace);
  }

  .embedded-tool-desc {
    color: var(--text-dim);
    font-size: 0.84rem;
  }

  .empty-belt { text-align: center; padding: 1.5rem 0; color: var(--text-dim); }
  .empty-icon { font-size: 2rem; display: block; margin-bottom: 0.5rem; }

  .belt-grid { display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .belt-chip {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
    border: 1px solid var(--bg-surface2);
  }
  .chip-info { display: flex; flex-direction: column; }
  .chip-name { font-weight: 600; font-size: 0.85rem; }
  .chip-role { font-size: 0.7rem; color: var(--text-dim); text-transform: capitalize; }

  .health-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    box-shadow: 0 0 6px currentColor;
  }
  .danger-text { color: var(--red); }

  .cabinet { display: flex; flex-direction: column; gap: 1rem; margin-top: 1rem; }

  .tool-list { display: flex; flex-direction: column; gap: 0.35rem; }
  .tool-row {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.45rem 0.6rem; border-radius: var(--radius-sm);
    transition: background 0.15s var(--ease);
  }
  .tool-row:hover { background: var(--bg-surface1); }
  .tool-row.is-equipped { background: rgba(166, 227, 161, 0.06); }
  .tool-name { flex: 1; font-size: 0.9rem; }
  .health-tag { font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 3px; text-transform: capitalize; }
  .health-tag.ok { color: var(--green); background: rgba(166, 227, 161, 0.1); }
  .health-tag.degraded { color: var(--yellow); background: rgba(249, 226, 175, 0.1); }
  .equipped-badge { font-size: 0.75rem; color: var(--green); font-weight: 600; }
  .sm { font-size: 0.78rem; padding: 0.2rem 0.6rem; }

  .swap-log { max-height: 14rem; overflow-y: auto; display: flex; flex-direction: column; gap: 0.25rem; }
  .swap-row {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.3rem 0.5rem; font-size: 0.82rem;
    border-left: 2px solid var(--bg-surface2);
  }
  .swap-ts { color: var(--text-dim); font-size: 0.72rem; min-width: 5rem; }
  .swap-role { font-weight: 600; min-width: 6rem; }
  .swap-arrow { flex: 1; }
  .swap-reason { color: var(--text-dim); font-style: italic; }
  .hint { font-size: 0.8rem; color: var(--text-dim); }

  .status-toast {
    margin-top: 1rem; padding: 0.5rem 1rem;
    background: var(--bg-surface1); border-radius: var(--radius-sm);
    font-size: 0.85rem; color: var(--text-sub);
  }

  @media (max-width: 860px) {
    .embedded-tool-row {
      grid-template-columns: 1fr;
    }
  }
</style>
