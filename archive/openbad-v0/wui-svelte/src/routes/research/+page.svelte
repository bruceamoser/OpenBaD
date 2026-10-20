<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { taskLiveLog } from '$lib/stores/websocket';
  import { get as apiGet, post as apiPost } from '$lib/api/client';

  interface ResearchNode {
    node_id: string;
    title: string;
    description?: string;
    priority: number;
    source_task_id?: string;
    enqueued_at: string | null;
    dequeued_at?: string | null;
    status: string;
  }

  interface SessionMessage {
    role: string;
    content: string;
    timestamp: string;
  }

  let nodes: ResearchNode[] = $state([]);
  let completedNodes: ResearchNode[] = $state([]);
  let sessionMessages: SessionMessage[] = $state([]);
  let createTitle = $state('');
  let createDescription = $state('');
  let createPriority = $state(0);
  let createSourceTaskId = $state('');
  let createStatus = $state('');
  let creating = $state(false);
  let error = $state('');
  let loading = $state(true);
  let expandedId = $state<string | null>(null);

  let sortedNodes = $derived(
    [...nodes].sort((left, right) => {
      if (left.priority !== right.priority) return left.priority - right.priority;
      const leftTs = left.enqueued_at ? new Date(left.enqueued_at).getTime() : 0;
      const rightTs = right.enqueued_at ? new Date(right.enqueued_at).getTime() : 0;
      return rightTs - leftTs;
    })
  );

  function fmtTime(ts: string | null | undefined): string {
    if (!ts) return '—';
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
  }

  async function load(): Promise<void> {
    loading = true;
    error = '';
    try {
      const [pendingRes, completedRes, sessionRes] = await Promise.all([
        apiGet<{ nodes: ResearchNode[]; error?: string }>('/api/research'),
        apiGet<{ nodes: ResearchNode[]; error?: string }>('/api/research/completed?limit=20'),
        apiGet<{ session_id: string; messages: SessionMessage[] }>('/api/chat/history?session_id=research-autonomy&limit=50'),
      ]);
      nodes = pendingRes.nodes ?? [];
      completedNodes = completedRes.nodes ?? [];
      sessionMessages = sessionRes.messages ?? [];
      if (pendingRes.error) error = pendingRes.error;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function createResearchNode(): Promise<void> {
    const title = createTitle.trim();
    if (!title || creating) return;

    creating = true;
    createStatus = '';
    try {
      const created = await apiPost<ResearchNode>('/api/research', {
        title,
        description: createDescription,
        priority: Number(createPriority),
        source_task_id: createSourceTaskId.trim() || null,
      });
      expandedId = created.node_id;
      nodes = [created, ...nodes.filter((n) => n.node_id !== created.node_id)];
      createTitle = '';
      createDescription = '';
      createPriority = 0;
      createSourceTaskId = '';
      createStatus = 'Research project created';
      await load();
    } catch (e) {
      createStatus = `Create failed: ${e}`;
    } finally {
      creating = false;
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
  <span class="count">{sortedNodes.length} node{sortedNodes.length !== 1 ? 's' : ''}</span>
  <button class="secondary" onclick={load}>↻ Refresh</button>
</div>

<Card label="Create Research Project">
  <div class="create-grid">
    <label>
      Title
      <input type="text" placeholder="Research project title" bind:value={createTitle} />
    </label>
    <label>
      Priority
      <input type="number" step="1" bind:value={createPriority} />
    </label>
    <label>
      Source task ID (optional)
      <input type="text" placeholder="task id" bind:value={createSourceTaskId} />
    </label>
    <label class="full-row">
      Description
      <textarea rows="3" placeholder="What should be investigated?" bind:value={createDescription}></textarea>
    </label>
  </div>
  <div class="create-actions">
    <button onclick={createResearchNode} disabled={creating || !createTitle.trim()}>
      {creating ? 'Creating…' : 'Create Research Project'}
    </button>
    {#if createStatus}<span class="status-msg">{createStatus}</span>{/if}
  </div>
</Card>

<Card label="Pending Research">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if sortedNodes.length === 0}
    <p class="empty">Research queue is empty. The agent is not blocked on any external information.</p>
  {:else}
    <div class="node-list">
      {#each sortedNodes as n}
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

<Card label="Completed Research ({completedNodes.length})">
  {#if completedNodes.length === 0}
    <p class="empty">No completed research yet.</p>
  {:else}
    <div class="node-list">
      {#each completedNodes as n}
        <div class="node-row completed-row"
             onclick={() => expandedId = expandedId === n.node_id ? null : n.node_id}
             role="button" tabindex="0"
             onkeydown={(e) => e.key === 'Enter' && (expandedId = expandedId === n.node_id ? null : n.node_id)}>
          <span class="check-icon">✓</span>
          <div class="node-meta">
            <span class="node-title">{n.title}</span>
            <span class="node-sub">Completed {fmtTime(n.dequeued_at)}</span>
          </div>
          <span class="expand-icon">{expandedId === n.node_id ? '▲' : '▼'}</span>
        </div>
        {#if expandedId === n.node_id}
          <div class="node-detail">
            {#if n.description}<p>{n.description}</p>{/if}
            <div class="detail-row"><strong>Node ID:</strong> <code>{n.node_id}</code></div>
            <div class="detail-row"><strong>Enqueued:</strong> {fmtTime(n.enqueued_at)}</div>
            <div class="detail-row"><strong>Completed:</strong> {fmtTime(n.dequeued_at)}</div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</Card>

<Card label="Research Session Log ({sessionMessages.length})">
  {#if sessionMessages.length === 0}
    <p class="empty">No research session messages yet.</p>
  {:else}
    <div class="session-log">
      {#each sessionMessages as msg}
        <div class="session-msg">
          <div class="msg-header">
            <span class="msg-role">{msg.role}</span>
            <span class="msg-time">{fmtTime(msg.timestamp)}</span>
          </div>
          <div class="msg-content">{msg.content}</div>
        </div>
      {/each}
    </div>
  {/if}
</Card>

<style>
  .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  .count { font-size: 0.85rem; color: var(--text-dim); }
  .create-grid {
    display: grid;
    gap: 0.65rem;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-bottom: 0.65rem;
  }
  .create-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    font-size: 0.82rem;
    color: var(--text-sub);
  }
  .create-grid input,
  .create-grid textarea {
    padding: 0.45rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
  }
  .full-row { grid-column: 1 / -1; }
  .create-actions { display: flex; align-items: center; gap: 0.65rem; }
  .status-msg { font-size: 0.82rem; color: var(--text-sub); }
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
  .completed-row { opacity: 0.85; }
  .check-icon { color: var(--green, #4caf50); font-weight: 700; min-width: 1.5rem; text-align: center; }
  .session-log { display: flex; flex-direction: column; gap: 0.5rem; }
  .session-msg {
    background: var(--bg-surface1); border-radius: var(--radius-sm);
    padding: 0.6rem 0.8rem; font-size: 0.83rem;
  }
  .msg-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem; }
  .msg-role { font-weight: 600; text-transform: capitalize; color: var(--teal); font-size: 0.78rem; }
  .msg-time { font-size: 0.75rem; color: var(--text-dim); }
  .msg-content { white-space: pre-wrap; line-height: 1.45; }

  @media (max-width: 900px) {
    .create-grid { grid-template-columns: 1fr; }
  }
</style>
