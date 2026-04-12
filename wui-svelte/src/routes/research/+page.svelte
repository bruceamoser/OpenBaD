<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { taskLiveLog } from '$lib/stores/websocket';
  import { get as apiGet } from '$lib/api/client';

  interface ResearchNode {
    node_id: string;
    title: string;
    description?: string;
    priority: number;
    source_task_id?: string;
    enqueued_at: string | null;
    status: string;
  }

  let nodes: ResearchNode[] = $state([]);
  let error = $state('');
  let loading = $state(true);
  let expandedId = $state<string | null>(null);

  function fmtTime(ts: string | null): string {
    if (!ts) return '—';
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
  }

  async function load(): Promise<void> {
    try {
      const res = await apiGet<{ nodes: ResearchNode[]; error?: string }>('/api/research');
      nodes = res.nodes ?? [];
      if (res.error) error = res.error;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  const unsub = taskLiveLog.subscribe((log) => {
    if (log.length > 0 && log[0].topic.startsWith('agent/research')) {
      load();
    }
  });

  onMount(load);
  onDestroy(unsub);
</script>

<div class="page-header">
  <h2>Research Queue</h2>
  <p>Pending information-gathering tasks ranked by priority</p>
</div>

<div class="toolbar">
  <span class="count">{nodes.length} node{nodes.length !== 1 ? 's' : ''}</span>
  <button class="secondary" onclick={load}>↻ Refresh</button>
</div>

<Card label="Pending Research">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if nodes.length === 0}
    <p class="empty">Research queue is empty. The agent is not blocked on any external information.</p>
  {:else}
    <div class="node-list">
      {#each nodes.sort((a, b) => b.priority - a.priority) as n}
        <div class="node-row"
             onclick={() => expandedId = expandedId === n.node_id ? null : n.node_id}
             role="button" tabindex="0"
             onkeydown={(e) => e.key === 'Enter' && (expandedId = expandedId === n.node_id ? null : n.node_id)}>
          <span class="priority-badge" title="Priority score">{n.priority.toFixed(2)}</span>
          <div class="node-meta">
            <span class="node-title">{n.title}</span>
            {#if n.source_task_id}
              <span class="node-sub">from task {n.source_task_id.slice(0, 8)}…</span>
            {/if}
          </div>
          <span class="status-badge">{n.status}</span>
          <span class="expand-icon">{expandedId === n.node_id ? '▲' : '▼'}</span>
        </div>
        {#if expandedId === n.node_id}
          <div class="node-detail">
            {#if n.description}<p>{n.description}</p>{/if}
            <div class="detail-row"><strong>Node ID:</strong> <code>{n.node_id}</code></div>
            <div class="detail-row"><strong>Enqueued:</strong> {fmtTime(n.enqueued_at)}</div>
            {#if n.source_task_id}
              <div class="detail-row"><strong>Source task:</strong> <code>{n.source_task_id}</code></div>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</Card>

<Card label="Priority Formula">
  <div class="formula-card">
    <p class="muted">Each research node's priority is computed as:</p>
    <pre class="formula">priority = base_priority
  + 0.5 × recency_boost   (more recent = higher)
  + 1.0 × parent_urgency  (if parent task is ACTIVE)
  − 0.2 × attempt_count   (penalise retried nodes)</pre>
    <p class="muted">Nodes are dequeued by the scheduler during heartbeat cycles when the agent FSM is IDLE or ACTIVE.</p>
  </div>
</Card>

<style>
  .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  .count { font-size: 0.85rem; color: var(--text-dim); }
  .muted { color: var(--text-dim); text-align: center; padding: 1.5rem; }
  .empty { color: var(--text-dim); padding: 2rem; text-align: center; }
  .error-msg { color: var(--red); padding: 1rem; }
  .node-list { display: flex; flex-direction: column; gap: 2px; }
  .node-row {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    cursor: pointer; font-size: 0.85rem;
  }
  .node-row:hover { background: var(--bg-surface2); }
  .priority-badge {
    min-width: 3.5rem; text-align: right;
    font-variant-numeric: tabular-nums;
    font-weight: 700; color: var(--teal); font-size: 0.82rem;
  }
  .node-meta { flex: 1; display: flex; flex-direction: column; gap: 0.1rem; overflow: hidden; }
  .node-title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .node-sub { font-size: 0.78rem; color: var(--text-dim); }
  .status-badge { font-size: 0.75rem; font-weight: 600; color: var(--yellow); text-transform: uppercase; }
  .expand-icon { color: var(--text-dim); font-size: 0.7rem; }
  .node-detail {
    background: var(--bg-base); border-left: 3px solid var(--border);
    margin: 2px 0 4px 1.5rem; padding: 0.75rem 1rem;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0; font-size: 0.83rem;
  }
  .detail-row { margin-top: 0.25rem; }
  .formula-card { padding: 0.25rem 0; }
  .formula {
    background: var(--bg-base); padding: 0.75rem 1rem;
    border-radius: var(--radius-sm); font-size: 0.82rem;
    color: var(--teal); margin: 0.5rem 0; white-space: pre-wrap;
  }
</style>
