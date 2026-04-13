<script lang="ts">
  import { onMount } from 'svelte';
  import { get as apiGet, put as apiPut, post as apiPost } from '$lib/api/client';

  interface SessionPolicyEntry {
    session_id: string;
    label: string;
    allow_task_autonomy: boolean;
    allow_research_autonomy: boolean;
    allow_destructive: boolean;
    allow_endocrine_doctor?: boolean;
  }

  interface SubsystemGate {
    enabled: boolean;
    disabled_reason: string;
    disabled_until: number | null;
  }

  interface DoctorNote {
    ts: number;
    source: string;
    provider: string;
    model: string;
    summary: string;
    doctor_revelation: boolean;
  }

  interface EndocrineStatus {
    levels: Record<string, number>;
    mood_tags: string[];
    subsystems: Record<string, SubsystemGate>;
    severity: Record<string, number>;
    doctor_notes: DoctorNote[];
  }

  let sessions: Record<string, SessionPolicyEntry> = $state({});
  let endocrine: EndocrineStatus = $state({
    levels: {},
    mood_tags: [],
    subsystems: {},
    severity: {},
    doctor_notes: [],
  });
  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');

  async function loadPolicy(): Promise<void> {
    loading = true;
    error = '';
    try {
      const [policy, status] = await Promise.all([
        apiGet<{ sessions: Record<string, SessionPolicyEntry> }>('/api/immune/policy'),
        apiGet<EndocrineStatus>('/api/endocrine/status'),
      ]);
      sessions = policy.sessions ?? {};
      endocrine = status;
    } catch (err) {
      error = String(err);
    } finally {
      loading = false;
    }
  }

  async function savePolicy(): Promise<void> {
    saving = true;
    error = '';
    try {
      const data = await apiPut<{ sessions: Record<string, SessionPolicyEntry> }>('/api/immune/policy', {
        sessions,
      });
      sessions = data.sessions ?? sessions;
    } catch (err) {
      error = String(err);
    } finally {
      saving = false;
    }
  }

  async function toggleSystem(system: string, enabled: boolean): Promise<void> {
    saving = true;
    error = '';
    try {
      endocrine = await apiPost<EndocrineStatus>('/api/endocrine/toggle', {
        system,
        enabled,
        reason: 'manual user toggle from immune screen',
      });
    } catch (err) {
      error = String(err);
    } finally {
      saving = false;
    }
  }

  function fmtTs(ts: number | null): string {
    if (!ts) return 'none';
    return new Date(ts * 1000).toLocaleString();
  }

  onMount(loadPolicy);
</script>

<section class="panel">
  <header class="panel-header">
    <h2>Immune Session Policy</h2>
    <p class="muted">
      Configure session permissions and endocrine doctor controls.
    </p>
  </header>

  {#if loading}
    <p>Loading policy…</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Session</th>
            <th>Session ID</th>
            <th>Task autonomy</th>
            <th>Research autonomy</th>
            <th>Destructive actions</th>
            <th>Endocrine doctor loop</th>
          </tr>
        </thead>
        <tbody>
          {#each Object.entries(sessions) as [key, s]}
            <tr>
              <td>
                <strong>{s.label}</strong>
                <div class="key">{key}</div>
              </td>
              <td>
                <input
                  value={s.session_id}
                  oninput={(e) => {
                    sessions[key].session_id = (e.currentTarget as HTMLInputElement).value;
                  }}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={s.allow_task_autonomy}
                  onchange={(e) => {
                    sessions[key].allow_task_autonomy = (e.currentTarget as HTMLInputElement).checked;
                  }}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={s.allow_research_autonomy}
                  onchange={(e) => {
                    sessions[key].allow_research_autonomy = (e.currentTarget as HTMLInputElement).checked;
                  }}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={s.allow_destructive}
                  onchange={(e) => {
                    sessions[key].allow_destructive = (e.currentTarget as HTMLInputElement).checked;
                  }}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={s.allow_endocrine_doctor ?? false}
                  onchange={(e) => {
                    sessions[key].allow_endocrine_doctor = (e.currentTarget as HTMLInputElement).checked;
                  }}
                />
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="actions">
      <button onclick={savePolicy} disabled={saving}>{saving ? 'Saving…' : 'Save policy'}</button>
      <button class="ghost" onclick={loadPolicy} disabled={saving}>Reload</button>
    </div>

    <h3>System Status</h3>
    <p class="muted">Manual override for chat, tasks, and research subsystem gates.</p>
    <div class="systems-grid">
      {#each Object.entries(endocrine.subsystems) as [name, gate]}
        <div class="system-card">
          <div class="system-head">
            <strong>{name}</strong>
            <span class:ok={gate.enabled} class:bad={!gate.enabled}>{gate.enabled ? 'enabled' : 'disabled'}</span>
          </div>
          <div class="system-meta">
            <div>Reason: {gate.disabled_reason || 'none'}</div>
            <div>Disabled until: {fmtTs(gate.disabled_until)}</div>
          </div>
          <div class="system-actions">
            <button onclick={() => toggleSystem(name, true)} disabled={saving || gate.enabled}>Enable</button>
            <button class="ghost" onclick={() => toggleSystem(name, false)} disabled={saving || !gate.enabled}>Disable</button>
          </div>
        </div>
      {/each}
    </div>

    <h3>Mood Tags</h3>
    <p class="muted">Current endocrine doctor tags.</p>
    <div class="tag-row">
      {#if endocrine.mood_tags.length === 0}
        <span class="muted">No active mood tags.</span>
      {:else}
        {#each endocrine.mood_tags as tag}
          <span class="tag">{tag}</span>
        {/each}
      {/if}
    </div>

    <h3>Doctor Revelations</h3>
    <p class="muted">Recent health decisions flagged as doctor revelations.</p>
    <div class="notes">
      {#if endocrine.doctor_notes.length === 0}
        <div class="muted">No recent doctor revelations.</div>
      {:else}
        {#each endocrine.doctor_notes as note}
          <div class="note">
            <div class="note-head">
              <strong>{new Date(note.ts * 1000).toLocaleString()}</strong>
              <span>{note.provider} {note.model}</span>
            </div>
            <div>{note.summary || 'No summary provided.'}</div>
          </div>
        {/each}
      {/if}
    </div>
  {/if}

  {#if error}
    <p class="error">{error}</p>
  {/if}
</section>

<style>
  .panel { max-width: 1100px; }
  .panel-header { margin-bottom: 1rem; }
  .muted { color: var(--text-dim); }
  .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.7rem; border-bottom: 1px solid var(--line); text-align: left; }
  .key { font-size: 0.8rem; color: var(--text-dim); }
  input:not([type]) {
    width: 100%;
    min-width: 160px;
    padding: 0.45rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel);
    color: var(--text);
  }
  .actions { display: flex; gap: 0.6rem; margin-top: 0.9rem; margin-bottom: 1.2rem; }
  .ghost { background: transparent; border: 1px solid var(--line); }
  .error { color: var(--red); margin-top: 0.8rem; }

  .systems-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.7rem;
    margin-bottom: 1.1rem;
  }
  .system-card {
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.7rem;
    background: var(--panel);
  }
  .system-head { display: flex; justify-content: space-between; margin-bottom: 0.4rem; }
  .ok { color: var(--green); }
  .bad { color: var(--red); }
  .system-meta { color: var(--text-dim); font-size: 0.82rem; display: grid; gap: 0.2rem; }
  .system-actions { display: flex; gap: 0.4rem; margin-top: 0.6rem; }

  .tag-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1.1rem; }
  .tag {
    border: 1px solid var(--line);
    background: var(--bg-surface1);
    border-radius: 999px;
    padding: 0.2rem 0.55rem;
    font-size: 0.8rem;
  }

  .notes { display: grid; gap: 0.5rem; }
  .note {
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.6rem;
    background: var(--panel);
  }
  .note-head {
    display: flex;
    justify-content: space-between;
    gap: 0.6rem;
    margin-bottom: 0.3rem;
    color: var(--text-dim);
    font-size: 0.82rem;
  }
</style>
