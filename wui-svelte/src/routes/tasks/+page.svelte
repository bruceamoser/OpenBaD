<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { taskLiveLog } from '$lib/stores/websocket';
  import { get as apiGet } from '$lib/api/client';

  interface Task {
    task_id: string;
    title: string;
    description?: string;
    status: string;
    kind: string;
    horizon: string;
    priority: number;
    created_at: string;
    updated_at: string;
  }

  let tasks: Task[] = $state([]);
  let error = $state('');
  let loading = $state(true);
  let expandedId = $state<string | null>(null);

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
  <span class="count">{tasks.length} task{tasks.length !== 1 ? 's' : ''}</span>
  <button class="secondary" onclick={load}>↻ Refresh</button>
</div>

<Card label="Task Queue">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if tasks.length === 0}
    <p class="empty">No tasks recorded yet.</p>
  {:else}
    <div class="task-list">
      {#each tasks as t}
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
</style>
