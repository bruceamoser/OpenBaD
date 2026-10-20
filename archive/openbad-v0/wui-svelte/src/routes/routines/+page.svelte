<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, post as apiPost, patch as apiPatch, del as apiDel } from '$lib/api/client';

  interface Routine {
    routine_id: string;
    name: string;
    description: string;
    body_md: string;
    recurrence_rule: string | null;
    next_run_at: number | null;
    enabled: boolean;
    created_at: number;
    updated_at: number;
  }

  interface RoutineRun {
    run_id: string;
    routine_id: string;
    started_at: number;
    finished_at: number | null;
    status: string;
    output: string;
    error: string;
    tokens_used: number;
  }

  let routines: Routine[] = $state([]);
  let loading = $state(true);
  let error = $state('');

  // Create form
  let createName = $state('');
  let createDesc = $state('');
  let createBody = $state('');
  let createRecurrence = $state('none');
  let createRecurrenceTime = $state('09:00');
  let createRecurrenceDay = $state('MON');
  let createOneshot = $state('');
  let createOneshotTime = $state('');
  let creating = $state(false);
  let createMsg = $state('');

  // Detail / runs
  let expandedId = $state<string | null>(null);
  let editingId = $state<string | null>(null);
  let editBody = $state('');
  let editName = $state('');
  let editDesc = $state('');
  let runs = $state<Record<string, RoutineRun[]>>({});
  let loadingRuns = $state<Record<string, boolean>>({});

  function fmtTime(ts: number | null | undefined): string {
    if (ts == null) return '—';
    const d = new Date(ts * 1000);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
  }

  function fmtRelative(ts: number | null | undefined): string {
    if (ts == null) return '';
    const diff = ts - Date.now() / 1000;
    if (diff < 0) return 'overdue';
    if (diff < 60) return `in ${Math.round(diff)}s`;
    if (diff < 3600) return `in ${Math.round(diff / 60)}m`;
    if (diff < 86400) return `in ${Math.round(diff / 3600)}h`;
    return `in ${Math.round(diff / 86400)}d`;
  }

  async function load(): Promise<void> {
    loading = true;
    error = '';
    try {
      const res = await apiGet<{ routines: Routine[] }>('/api/routines');
      routines = res.routines ?? [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function createRoutine(): Promise<void> {
    const name = createName.trim();
    const body = createBody.trim();
    if (!name || !body || creating) return;

    creating = true;
    createMsg = '';
    try {
      const payload: Record<string, unknown> = {
        name,
        description: createDesc,
        body_md: body,
      };

      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (createRecurrence === 'daily') {
        payload.recurrence_rule = `daily|${createRecurrenceTime}|${tz}`;
      } else if (createRecurrence === 'weekly') {
        payload.recurrence_rule = `weekly|${createRecurrenceDay}|${createRecurrenceTime}|${tz}`;
      }

      if (createRecurrence === 'none' && createOneshot) {
        const dtStr = createOneshotTime ? `${createOneshot}T${createOneshotTime}` : `${createOneshot}T00:00`;
        const dt = new Date(dtStr);
        if (!Number.isNaN(dt.getTime())) {
          payload.next_run_at = Math.floor(dt.getTime() / 1000);
        }
      }

      await apiPost<Routine>('/api/routines', payload);
      createName = '';
      createDesc = '';
      createBody = '';
      createRecurrence = 'none';
      createRecurrenceTime = '09:00';
      createRecurrenceDay = 'MON';
      createOneshot = '';
      createOneshotTime = '';
      createMsg = 'Routine created';
      await load();
    } catch (e) {
      createMsg = `Failed: ${e}`;
    } finally {
      creating = false;
    }
  }

  async function toggleRoutine(r: Routine): Promise<void> {
    try {
      await apiPost(`/api/routines/${r.routine_id}/toggle`, {});
      await load();
    } catch (e) {
      error = `Toggle failed: ${e}`;
    }
  }

  async function deleteRoutine(r: Routine): Promise<void> {
    if (!confirm(`Delete routine "${r.name}"?`)) return;
    try {
      await apiDel(`/api/routines/${r.routine_id}`);
      await load();
    } catch (e) {
      error = `Delete failed: ${e}`;
    }
  }

  async function triggerRun(r: Routine): Promise<void> {
    try {
      await apiPost(`/api/routines/${r.routine_id}/run`, {});
      createMsg = `Triggered "${r.name}"`;
      setTimeout(() => loadRuns(r.routine_id), 2000);
    } catch (e) {
      error = `Trigger failed: ${e}`;
    }
  }

  async function loadRuns(routineId: string): Promise<void> {
    loadingRuns = { ...loadingRuns, [routineId]: true };
    try {
      const res = await apiGet<{ runs: RoutineRun[] }>(`/api/routines/${routineId}/runs?limit=10`);
      runs = { ...runs, [routineId]: res.runs ?? [] };
    } catch {
      // ignore
    } finally {
      loadingRuns = { ...loadingRuns, [routineId]: false };
    }
  }

  function startEdit(r: Routine): void {
    editingId = r.routine_id;
    editName = r.name;
    editDesc = r.description;
    editBody = r.body_md;
  }

  async function saveEdit(): Promise<void> {
    if (!editingId) return;
    try {
      await apiPatch(`/api/routines/${editingId}`, {
        name: editName,
        description: editDesc,
        body_md: editBody,
      });
      editingId = null;
      await load();
    } catch (e) {
      error = `Save failed: ${e}`;
    }
  }

  function toggleExpand(id: string): void {
    if (expandedId === id) {
      expandedId = null;
    } else {
      expandedId = id;
      if (!runs[id]) loadRuns(id);
    }
  }

  onMount(load);
  const refreshInterval = setInterval(load, 30000);
  onDestroy(() => clearInterval(refreshInterval));
</script>

<div class="page-header">
  <h2>Routines</h2>
  <p>Scheduled automations — markdown instructions executed by the agent on a schedule</p>
</div>

<div class="toolbar">
  <span class="count">{routines.length} routine{routines.length !== 1 ? 's' : ''}</span>
  <button class="secondary" onclick={load}>↻ Refresh</button>
</div>

<Card label="Create Routine">
  <div class="create-grid">
    <label>
      Name
      <input type="text" placeholder="Routine name" bind:value={createName} />
    </label>
    <label>
      Description
      <input type="text" placeholder="Brief description" bind:value={createDesc} />
    </label>
    <label>
      Schedule
      <select bind:value={createRecurrence}>
        <option value="none">One-time / Manual</option>
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
      </select>
    </label>
    {#if createRecurrence !== 'none'}
      <label>
        At Time
        <input type="time" bind:value={createRecurrenceTime} />
      </label>
    {/if}
    {#if createRecurrence === 'weekly'}
      <label>
        Day
        <select bind:value={createRecurrenceDay}>
          <option value="MON">Monday</option>
          <option value="TUE">Tuesday</option>
          <option value="WED">Wednesday</option>
          <option value="THU">Thursday</option>
          <option value="FRI">Friday</option>
          <option value="SAT">Saturday</option>
          <option value="SUN">Sunday</option>
        </select>
      </label>
    {/if}
    {#if createRecurrence === 'none'}
      <label>
        Run Date
        <input type="date" bind:value={createOneshot} />
      </label>
      <label>
        Run Time
        <input type="time" bind:value={createOneshotTime} />
      </label>
    {/if}
    <label class="full-row">
      Instructions (Markdown)
      <textarea rows="8" placeholder="Write the steps the agent should follow...

Example:
# Check backup status
1. Run `get_tasks` to see if any backup tasks failed
2. If any failed, send a telegram message to the user
3. Summarize what you found" bind:value={createBody}></textarea>
    </label>
  </div>
  <div class="create-actions">
    <button onclick={createRoutine} disabled={creating || !createName.trim() || !createBody.trim()}>
      {creating ? 'Creating…' : 'Create Routine'}
    </button>
    {#if createMsg}<span class="status-msg">{createMsg}</span>{/if}
  </div>
</Card>

<Card label="Routines">
  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="error-msg">Error: {error}</p>
  {:else if routines.length === 0}
    <p class="empty">No routines yet. Create one above.</p>
  {:else}
    <div class="routine-list">
      {#each routines as r}
        <div class="routine-row" class:disabled={!r.enabled}>
          <div class="routine-main"
               onclick={() => toggleExpand(r.routine_id)}
               role="button" tabindex="0"
               onkeydown={(e) => e.key === 'Enter' && toggleExpand(r.routine_id)}>
            <span class="status-dot" style="color:{r.enabled ? 'var(--green)' : 'var(--text-dim)'}">●</span>
            <div class="routine-meta">
              <span class="routine-name">{r.name}</span>
              <span class="routine-sub">
                {#if r.recurrence_rule}
                  🔁 {r.recurrence_rule}
                {:else}
                  one-time
                {/if}
                {#if r.next_run_at}
                  · next: {fmtTime(r.next_run_at)} ({fmtRelative(r.next_run_at)})
                {/if}
                {#if r.description}
                  · {r.description}
                {/if}
              </span>
            </div>
            <span class="expand-icon">{expandedId === r.routine_id ? '▲' : '▼'}</span>
          </div>

          <div class="routine-actions">
            <button class="small" onclick={() => toggleRoutine(r)}>
              {r.enabled ? 'Pause' : 'Enable'}
            </button>
            <button class="small" onclick={() => triggerRun(r)}>▶ Run Now</button>
            <button class="small" onclick={() => startEdit(r)}>✏ Edit</button>
            <button class="small danger" onclick={() => deleteRoutine(r)}>✕</button>
          </div>
        </div>

        {#if expandedId === r.routine_id}
          <div class="routine-detail">
            {#if editingId === r.routine_id}
              <div class="edit-form">
                <label>Name <input type="text" bind:value={editName} /></label>
                <label>Description <input type="text" bind:value={editDesc} /></label>
                <label class="full-row">Instructions
                  <textarea rows="10" bind:value={editBody}></textarea>
                </label>
                <div class="edit-actions">
                  <button onclick={saveEdit}>Save</button>
                  <button class="secondary" onclick={() => editingId = null}>Cancel</button>
                </div>
              </div>
            {:else}
              <div class="md-preview">
                <pre>{r.body_md}</pre>
              </div>
            {/if}

            <div class="detail-row"><strong>ID:</strong> <code>{r.routine_id}</code></div>
            <div class="detail-row"><strong>Created:</strong> {fmtTime(r.created_at)}</div>
            <div class="detail-row"><strong>Updated:</strong> {fmtTime(r.updated_at)}</div>

            <h4>Recent Runs</h4>
            {#if loadingRuns[r.routine_id]}
              <p class="muted">Loading runs…</p>
            {:else if !runs[r.routine_id] || runs[r.routine_id].length === 0}
              <p class="muted">No runs yet.</p>
            {:else}
              <div class="runs-list">
                {#each runs[r.routine_id] as run}
                  <div class="run-row" class:run-failed={run.status === 'failed'} class:run-done={run.status === 'done'}>
                    <span class="run-status" style="color:{run.status === 'done' ? 'var(--green)' : run.status === 'failed' ? 'var(--red)' : 'var(--blue)'}">
                      {run.status}
                    </span>
                    <span class="run-time">{fmtTime(run.started_at)}</span>
                    <span class="run-tokens">{run.tokens_used} tokens</span>
                    {#if run.error}
                      <div class="run-error">{run.error}</div>
                    {/if}
                    {#if run.output}
                      <details class="run-output">
                        <summary>Output</summary>
                        <pre>{run.output}</pre>
                      </details>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
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
  .create-grid textarea,
  .create-grid select {
    padding: 0.45rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
    font-family: inherit;
  }
  .create-grid textarea {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.82rem;
    line-height: 1.5;
  }
  .full-row { grid-column: 1 / -1; }
  .create-actions { display: flex; align-items: center; gap: 0.65rem; }
  .status-msg { font-size: 0.82rem; color: var(--text-sub); }

  .empty, .muted { color: var(--text-dim); padding: 1.5rem; text-align: center; }
  .error-msg { color: var(--red); padding: 1rem; }

  .routine-list { display: flex; flex-direction: column; gap: 4px; }

  .routine-row {
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    padding: 0.5rem 0.75rem;
  }
  .routine-row.disabled { opacity: 0.55; }

  .routine-main {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .routine-main:hover { opacity: 0.85; }

  .status-dot { font-size: 0.7rem; }
  .routine-meta { flex: 1; display: flex; flex-direction: column; gap: 0.1rem; overflow: hidden; }
  .routine-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .routine-sub { font-size: 0.78rem; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .expand-icon { color: var(--text-dim); font-size: 0.7rem; }

  .routine-actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.4rem;
    padding-left: 1.3rem;
  }

  button.small {
    font-size: 0.75rem;
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface2);
    color: var(--text);
    border: 1px solid var(--line);
    cursor: pointer;
  }
  button.small:hover { background: var(--bg-surface1); }
  button.small.danger { color: var(--red); }
  button.small.danger:hover { background: rgba(255,100,100,0.1); }

  .routine-detail {
    background: var(--bg-base);
    border-left: 3px solid var(--border);
    margin: 4px 0 6px 1.5rem;
    padding: 0.75rem 1rem;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    font-size: 0.83rem;
  }
  .detail-row { margin-top: 0.25rem; }

  .md-preview {
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    padding: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .md-preview pre {
    white-space: pre-wrap;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.8rem;
    line-height: 1.5;
    margin: 0;
  }

  .edit-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .edit-form label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.82rem;
    color: var(--text-sub);
  }
  .edit-form input,
  .edit-form textarea {
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.82rem;
  }
  .edit-actions { display: flex; gap: 0.5rem; }

  h4 { margin: 1rem 0 0.5rem; color: var(--text-sub); font-size: 0.85rem; }

  .runs-list { display: flex; flex-direction: column; gap: 4px; }
  .run-row {
    padding: 0.4rem 0.6rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
    font-size: 0.8rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
  }
  .run-status { font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }
  .run-time { color: var(--text-dim); }
  .run-tokens { color: var(--text-dim); }
  .run-error {
    width: 100%;
    color: var(--red);
    font-size: 0.78rem;
    margin-top: 0.2rem;
  }
  .run-output {
    width: 100%;
    margin-top: 0.3rem;
  }
  .run-output summary {
    cursor: pointer;
    color: var(--blue);
    font-size: 0.78rem;
  }
  .run-output pre {
    white-space: pre-wrap;
    font-size: 0.78rem;
    margin-top: 0.3rem;
    padding: 0.5rem;
    background: var(--bg-base);
    border-radius: var(--radius-sm);
    max-height: 300px;
    overflow-y: auto;
  }

  @media (max-width: 900px) {
    .create-grid { grid-template-columns: 1fr; }
    .routine-actions { flex-wrap: wrap; }
  }
</style>
