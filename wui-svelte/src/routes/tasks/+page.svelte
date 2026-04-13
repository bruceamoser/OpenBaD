<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { taskLiveLog } from '$lib/stores/websocket';
  import { get as apiGet, post as apiPost } from '$lib/api/client';

  interface Task {
    task_id: string;
    title: string;
    description?: string;
    status: string;
    kind: string;
    horizon: string;
    priority: number;
    owner?: string;
    created_at: string;
    updated_at: string;
  }

  let tasks: Task[] = $state([]);
  let showHeartbeatTasks = $state(false);
  let createTitle = $state('');
  let createDescription = $state('');
  let createOwner = $state('user');
  let createStatus = $state('');
  let creating = $state(false);
  let error = $state('');
  let loading = $state(true);
  let expandedId = $state<string | null>(null);

  let visibleTasks = $derived(
    showHeartbeatTasks
      ? tasks
      : tasks.filter((task) => {
          const title = (task.title ?? '').toLowerCase();
          const owner = (task.owner ?? '').toLowerCase();
          return !(title.includes('heartbeat') || owner === 'heartbeat-timer');
        })
  );

  const STATUS_COLOR: Record<string, string> = {
    pending:          'var(--yellow)',
    running:          'var(--blue)',
    blocked:          'var(--peach)',
    blocked_on_user:  'var(--peach)',
    done:             'var(--green)',
    failed:           'var(--red)',
    cancelled:        'var(--text-dim)',
  };

  function statusColor(s: string): string {
    return STATUS_COLOR[s.toLowerCase()] ?? 'var(--text-dim)';
  }

  function fmtTime(ts: string): string {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
  }

  async function load(): Promise<void> {
    try {
      const res = await apiGet<{ tasks: Task[]; error?: string }>('/api/tasks');
      tasks = res.tasks ?? [];
      if (res.error) error = res.error;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function createTask(): Promise<void> {
    const title = createTitle.trim();
    if (!title || creating) return;

    creating = true;
    createStatus = '';
    try {
      await apiPost<Task>('/api/tasks', {
        title,
        description: createDescription,
        owner: createOwner.trim() || 'user',
      });
      createTitle = '';
      createDescription = '';
      createOwner = 'user';
      createStatus = 'Task created';
      await load();
    } catch (e) {
      createStatus = `Create failed: ${e}`;
    } finally {
      creating = false;
    }
  }

  // Reload when a task event arrives
  const unsub = taskLiveLog.subscribe((log) => {
    if (log.length > 0 && log[0].topic.startsWith('agent/task')) {
      load();
    }
  });

  onMount(load);
  onDestroy(unsub);
</script>

<div class="page-header">
  <h2>Tasks</h2>
  <p>Active and recent task queue</p>
</div>

<div class="toolbar">
  <span class="count">{visibleTasks.length} task{visibleTasks.length !== 1 ? 's' : ''}</span>
  <label class="heartbeat-toggle">
    <input type="checkbox" bind:checked={showHeartbeatTasks} />
    Show heartbeat tasks
  </label>
  <button class="secondary" onclick={load}>↻ Refresh</button>
</div>

<Card label="Create Task">
  <div class="create-grid">
    <label>
      Title
      <input type="text" placeholder="New task title" bind:value={createTitle} />
    </label>
    <label>
      Owner
      <input type="text" placeholder="user" bind:value={createOwner} />
    </label>
    <label class="full-row">
      Description
      <textarea rows="3" placeholder="Task details" bind:value={createDescription}></textarea>
    </label>
  </div>
  <div class="create-actions">
    <button onclick={createTask} disabled={creating || !createTitle.trim()}>
      {creating ? 'Creating…' : 'Create Task'}
    </button>
    {#if createStatus}<span class="status-msg">{createStatus}</span>{/if}
  </div>
</Card>

<Card label="Task Queue">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if visibleTasks.length === 0}
    <p class="empty">No tasks recorded yet.</p>
  {:else}
    <div class="task-list">
      {#each visibleTasks as t}
        <div class="task-row" onclick={() => expandedId = expandedId === t.task_id ? null : t.task_id}
             role="button" tabindex="0"
             onkeydown={(e) => e.key === 'Enter' && (expandedId = expandedId === t.task_id ? null : t.task_id)}>
          <span class="status-dot" style="color:{statusColor(t.status)}">●</span>
          <div class="task-meta">
            <span class="task-title">{t.title}</span>
            <span class="task-sub">{t.kind} · {t.horizon} horizon · priority {t.priority}</span>
          </div>
          <span class="status-badge" style="color:{statusColor(t.status)}">{t.status}</span>
          <span class="expand-icon">{expandedId === t.task_id ? '▲' : '▼'}</span>
        </div>
        {#if expandedId === t.task_id}
          <div class="task-detail">
            {#if t.description}<p>{t.description}</p>{/if}
            <div class="detail-row"><strong>ID:</strong> <code>{t.task_id}</code></div>
            <div class="detail-row"><strong>Created:</strong> {fmtTime(t.created_at)}</div>
            <div class="detail-row"><strong>Updated:</strong> {fmtTime(t.updated_at)}</div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</Card>

{#if $taskLiveLog.filter(e => e.topic.startsWith('agent/task')).length > 0}
  <Card label="Live Task Events">
    <div class="event-list">
      {#each $taskLiveLog.filter(e => e.topic.startsWith('agent/task')).slice(0, 20) as ev}
        <div class="event-row">
          <span class="ts">{new Date(ev.ts).toLocaleTimeString()}</span>
          <span class="topic-badge">{ev.topic}</span>
          <span class="event-payload">{JSON.stringify(ev.payload).slice(0, 80)}</span>
        </div>
      {/each}
    </div>
  </Card>
{/if}

<style>
  .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
  .count { font-size: 0.85rem; color: var(--text-dim); }
  .heartbeat-toggle {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--text-dim);
    font-size: 0.82rem;
  }
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
  .empty, .muted { color: var(--text-dim); padding: 2rem; text-align: center; }
  .error-msg { color: var(--red); padding: 1rem; }
  .task-list { display: flex; flex-direction: column; gap: 2px; }
  .task-row {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    cursor: pointer; font-size: 0.85rem;
  }
  .task-row:hover { background: var(--bg-surface2); }
  .status-dot { font-size: 0.7rem; }
  .task-meta { flex: 1; display: flex; flex-direction: column; gap: 0.1rem; overflow: hidden; }
  .task-title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .task-sub { font-size: 0.78rem; color: var(--text-dim); }
  .status-badge { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
  .expand-icon { color: var(--text-dim); font-size: 0.7rem; }
  .task-detail {
    background: var(--bg-base); border-left: 3px solid var(--border);
    margin: 2px 0 4px 1.5rem; padding: 0.75rem 1rem;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0; font-size: 0.83rem;
  }
  .detail-row { margin-top: 0.25rem; }
  .event-list { display: flex; flex-direction: column; gap: 2px; }
  .event-row { display: flex; gap: 0.6rem; align-items: center; font-size: 0.8rem; padding: 0.25rem 0; }
  .ts { color: var(--text-dim); font-variant-numeric: tabular-nums; min-width: 7ch; }
  .topic-badge { color: var(--blue); font-weight: 600; }
  .event-payload { color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  @media (max-width: 900px) {
    .toolbar { flex-wrap: wrap; }
    .create-grid { grid-template-columns: 1fr; }
  }
</style>
