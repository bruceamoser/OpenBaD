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
        cabinet: ToolEntry[];
        swap_log?: AutoSwapEvent[];
      }>('/api/toolbelt');
      cabinet = data.cabinet ?? [];
      swapLog = (data.swap_log ?? []).slice(-20);
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
      await apiPut(`/api/toolbelt/${role}`, { name: toolName });
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

<h2>Toolbelt</h2>

<!-- Belt (equipped) -->
<Card label="Equipped Belt">
  {#if belt.length === 0}
    <p class="muted">No tools equipped.</p>
  {:else}
    <div class="belt-grid">
      {#each belt as tool}
        <div class="belt-card">
          <span
            class="health-dot"
            style="background:{healthColor(tool.health)}"
          ></span>
          <strong>{tool.name}</strong>
          <span class="role-tag">{tool.role}</span>
          <button
            class="small-btn"
            onclick={() => unequip(tool.role)}
          >Unequip</button>
        </div>
      {/each}
    </div>
  {/if}
</Card>

<!-- Cabinet grouped by role -->
{#each TOOL_ROLES as role}
  {#if (grouped[role] ?? []).length > 0}
    <Card label={role}>
      <ul class="tool-list">
        {#each grouped[role] as tool}
          <li class:equipped={tool.equipped}>
            <span
              class="health-dot"
              style="background:{healthColor(tool.health)}"
            ></span>
            <span class="tool-name">{tool.name}</span>
            <span class="health-label">{tool.health}</span>
            {#if tool.equipped}
              <span class="badge">equipped</span>
            {:else}
              <button
                class="small-btn"
                onclick={() => equip(role, tool.name)}
              >Equip</button>
            {/if}
          </li>
        {/each}
      </ul>
    </Card>
  {/if}
{/each}

<!-- Auto-swap event log -->
<Card label="Auto-Swap Log">
  {#if swapLog.length === 0}
    <p class="muted">No auto-swap events yet.</p>
  {:else}
    <ul class="swap-log">
      {#each swapLog as evt}
        <li>
          <span class="ts">{evt.ts}</span>
          <strong>{evt.role}</strong>:
          {evt.from_tool} → {evt.to_tool}
          <em>({evt.reason})</em>
        </li>
      {/each}
    </ul>
  {/if}
</Card>

{#if statusMsg}
  <p class="status">{statusMsg}</p>
{/if}

<style>
  .belt-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .belt-card {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid #555;
    border-radius: 6px;
  }
  .health-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  .role-tag {
    font-size: 0.75rem;
    opacity: 0.6;
  }
  .tool-list {
    list-style: none;
    padding: 0;
  }
  .tool-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0;
  }
  .tool-list li.equipped {
    background: rgba(34, 197, 94, 0.08);
    border-radius: 4px;
    padding-left: 0.4rem;
  }
  .tool-name { flex: 1; }
  .health-label {
    font-size: 0.75rem;
    opacity: 0.6;
  }
  .badge {
    font-size: 0.7rem;
    background: #22c55e;
    color: #000;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
  }
  .small-btn {
    padding: 0.2rem 0.5rem;
    font-size: 0.8rem;
  }
  .swap-log {
    list-style: none;
    padding: 0;
    max-height: 15rem;
    overflow-y: auto;
  }
  .swap-log li {
    padding: 0.25rem 0;
    font-size: 0.85rem;
  }
  .ts {
    opacity: 0.5;
    margin-right: 0.5rem;
    font-size: 0.75rem;
  }
  .muted { opacity: 0.5; }
  .status { font-size: 0.85rem; opacity: 0.8; margin-top: 1rem; }
</style>
