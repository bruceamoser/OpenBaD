<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { mqttLiveLog } from '$lib/stores/websocket';
  import { wsStatus } from '$lib/stores/websocket';
  import { get as apiGet } from '$lib/api/client';

  interface MqttMsg { ts: string; topic: string; payload: unknown; }

  let seedMessages: MqttMsg[] = $state([]);
  let filter = $state('');
  let expanded = $state(new Set<number>());

  // Merge seeded + live
  let allMessages = $derived<MqttMsg[]>([...$mqttLiveLog, ...seedMessages].slice(0, 300));
  let filtered = $derived(filter
    ? allMessages.filter(m => m.topic.includes(filter))
    : allMessages);

  function toggleExpand(i: number): void {
    if (expanded.has(i)) {
      expanded.delete(i);
    } else {
      expanded.add(i);
    }
    expanded = new Set(expanded);
  }

  function formatPayload(p: unknown): string {
    if (typeof p === 'string') return p;
    try { return JSON.stringify(p, null, 2); }
    catch { return String(p); }
  }

  function topicColor(topic: string): string {
    if (topic.startsWith('agent/endocrine')) return 'var(--mauve)';
    if (topic.startsWith('agent/task'))      return 'var(--blue)';
    if (topic.startsWith('agent/research'))  return 'var(--teal)';
    if (topic.startsWith('agent/scheduler')) return 'var(--green)';
    if (topic.startsWith('agent/reflex'))    return 'var(--yellow)';
    if (topic.startsWith('agent/immune'))    return 'var(--red)';
    if (topic.startsWith('system/'))         return 'var(--text-dim)';
    return 'var(--text)';
  }

  onMount(async () => {
    try {
      const res = await apiGet<{ messages: MqttMsg[] }>('/api/mqtt/log');
      seedMessages = res.messages ?? [];
    } catch {
      // bridge may not be running — live messages will flow in
    }
  });
</script>

<div class="page-header">
  <h2>MQTT Monitor</h2>
  <p>Live message feed from the nervous system bus</p>
</div>

<div class="toolbar">
  <div class="status-pill" class:connected={$wsStatus === 'connected'}>
    {#if $wsStatus === 'connected'}● Connected{:else}● Disconnected{/if}
  </div>
  <input class="filter-input" placeholder="Filter by topic…" bind:value={filter} />
  <span class="count">{filtered.length} messages</span>
  <button class="secondary" onclick={() => { seedMessages = []; mqttLiveLog.set([]); }}>Clear</button>
</div>

<Card label="Messages">
  {#if filtered.length === 0}
    <p class="empty">No messages yet — waiting for MQTT activity…</p>
  {:else}
    <div class="msg-list">
      {#each filtered as msg, i}
        <div class="msg-row" onclick={() => toggleExpand(i)} role="button" tabindex="0"
             onkeydown={(e) => e.key === 'Enter' && toggleExpand(i)}>
          <span class="ts">{new Date(msg.ts).toLocaleTimeString()}</span>
          <span class="topic-badge" style="color:{topicColor(msg.topic)}">{msg.topic}</span>
          {#if !expanded.has(i)}
            <span class="payload-preview">{String(msg.payload).slice(0, 80)}</span>
          {/if}
          <span class="expand-icon">{expanded.has(i) ? '▲' : '▼'}</span>
        </div>
        {#if expanded.has(i)}
          <div class="payload-full">
            <pre>{formatPayload(msg.payload)}</pre>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</Card>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .status-pill {
    font-size: 0.8rem;
    padding: 0.2rem 0.6rem;
    border-radius: 99px;
    background: color-mix(in srgb, var(--red) 25%, var(--bg-surface1));
    color: var(--red);
  }
  .status-pill.connected {
    background: color-mix(in srgb, var(--green) 20%, var(--bg-surface1));
    color: var(--green);
  }
  .filter-input {
    flex: 1;
    min-width: 180px;
    padding: 0.35rem 0.6rem;
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: 0.85rem;
  }
  .count { font-size: 0.8rem; color: var(--text-dim); margin-left: auto; }
  .empty { color: var(--text-dim); padding: 2rem; text-align: center; }
  .msg-list { display: flex; flex-direction: column; gap: 2px; }
  .msg-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.35rem 0.5rem;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-size: 0.82rem;
    background: var(--bg-surface1);
  }
  .msg-row:hover { background: var(--bg-surface2); }
  .ts { color: var(--text-dim); font-variant-numeric: tabular-nums; min-width: 7ch; }
  .topic-badge { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }
  .payload-preview { color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .expand-icon { margin-left: auto; color: var(--text-dim); font-size: 0.7rem; }
  .payload-full {
    background: var(--bg-base);
    border-left: 3px solid var(--border);
    margin: 2px 0 4px 1rem;
    padding: 0.5rem 0.75rem;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    overflow-x: auto;
  }
  .payload-full pre {
    margin: 0;
    font-size: 0.78rem;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-all;
  }
</style>
