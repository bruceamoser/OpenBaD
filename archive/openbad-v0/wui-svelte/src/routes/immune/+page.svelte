<script lang="ts">
  import { onMount } from 'svelte';
  import { get as apiGet, put as apiPut } from '$lib/api/client';

  interface SessionPolicyEntry {
    session_id: string;
    label: string;
    allow_task_autonomy: boolean;
    allow_research_autonomy: boolean;
    allow_destructive: boolean;
    allow_endocrine_doctor?: boolean;
  }

  let sessions: Record<string, SessionPolicyEntry> = $state({});
  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');

  async function loadPolicy(): Promise<void> {
    loading = true;
    error = '';
    try {
      const policy = await apiGet<{ sessions: Record<string, SessionPolicyEntry> }>('/api/immune/policy');
      sessions = policy.sessions ?? {};
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
</style>
