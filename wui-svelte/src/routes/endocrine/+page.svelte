<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { get as apiGet, post as apiPost } from '$lib/api/client';
  import { endocrineLevels } from '$lib/stores/websocket';

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
    payload?: Record<string, unknown>;
  }

  interface EndocrineStatus {
    levels: Record<string, number>;
    mood_tags: string[];
    subsystems: Record<string, SubsystemGate>;
    severity: Record<string, number>;
    doctor_notes: DoctorNote[];
  }

  interface Adjustment {
    id: number;
    ts: number;
    source: string;
    reason: string;
    deltas: Record<string, number>;
    levels: Record<string, number>;
  }

  const HORMONES = [
    { key: 'dopamine', emoji: '🧠', label: 'Dopamine', desc: 'Reward & exploration' },
    { key: 'adrenaline', emoji: '⚡', label: 'Adrenaline', desc: 'Urgency & threat' },
    { key: 'cortisol', emoji: '🔥', label: 'Cortisol', desc: 'Sustained stress' },
    { key: 'endorphin', emoji: '✨', label: 'Endorphin', desc: 'Recovery & resilience' },
  ];

  const SEVERITY_LABELS: Record<number, string> = { 1: 'Normal', 2: 'Activated', 3: 'Escalated' };
  const SEVERITY_COLORS: Record<number, string> = { 1: 'var(--green)', 2: 'var(--yellow)', 3: 'var(--red)' };

  let status: EndocrineStatus = $state({
    levels: {}, mood_tags: [], subsystems: {}, severity: {}, doctor_notes: [],
  });
  let adjustments: Adjustment[] = $state([]);
  let loading = $state(true);
  let toggling = $state('');
  let error = $state('');
  let pollTimer: ReturnType<typeof setInterval> | undefined;

  // Live levels from websocket
  let liveDopamine = $derived($endocrineLevels?.dopamine ?? status.levels.dopamine ?? 0);
  let liveAdrenaline = $derived($endocrineLevels?.adrenaline ?? status.levels.adrenaline ?? 0);
  let liveCortisol = $derived($endocrineLevels?.cortisol ?? status.levels.cortisol ?? 0);
  let liveEndorphin = $derived($endocrineLevels?.endorphin ?? status.levels.endorphin ?? 0);
  let liveMap = $derived({
    dopamine: liveDopamine, adrenaline: liveAdrenaline,
    cortisol: liveCortisol, endorphin: liveEndorphin,
  } as Record<string, number>);

  async function load(): Promise<void> {
    loading = true;
    error = '';
    try {
      const [st, act] = await Promise.all([
        apiGet<EndocrineStatus>('/api/endocrine/status'),
        apiGet<{ adjustments: Adjustment[] }>('/api/endocrine/activity?limit=100'),
      ]);
      status = st;
      adjustments = act.adjustments ?? [];
    } catch (err) {
      error = String(err);
    } finally {
      loading = false;
    }
  }

  async function toggleSystem(system: string, enabled: boolean): Promise<void> {
    toggling = system;
    error = '';
    try {
      status = await apiPost<EndocrineStatus>('/api/endocrine/toggle', {
        system,
        enabled,
        reason: `manual toggle from endocrine UI`,
      });
    } catch (err) {
      error = String(err);
    } finally {
      toggling = '';
    }
  }

  function fmtTs(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }

  function relTime(ts: number): string {
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return `${Math.round(diff)}s ago`;
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
    return `${(diff / 3600).toFixed(1)}h ago`;
  }

  function hormoneColor(val: number): string {
    if (val < 0.3) return 'var(--green)';
    if (val < 0.7) return 'var(--yellow)';
    return 'var(--red)';
  }

  function deltaStr(d: number): string {
    if (d > 0) return `+${d.toFixed(2)}`;
    return d.toFixed(2);
  }

  onMount(() => {
    load();
    pollTimer = setInterval(load, 15000);
  });
  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<div class="page-header">
  <h2>Endocrine System</h2>
  <span class="page-sub">Hormone levels, subsystem gates, doctor activity, and adjustment log</span>
</div>

{#if error}
  <div class="error-banner">{error}</div>
{/if}

{#if loading && !adjustments.length}
  <p class="muted">Loading…</p>
{:else}

  <!-- Hormone levels -->
  <section class="section">
    <h3 class="section-heading">Hormone Levels</h3>
    <div class="hormones-grid">
      {#each HORMONES as h}
        {@const val = liveMap[h.key] ?? 0}
        {@const sev = status.severity[h.key] ?? 1}
        <div class="hormone-card">
          <div class="hormone-top">
            <span class="hormone-emoji">{h.emoji}</span>
            <div class="hormone-meta">
              <span class="hormone-name">{h.label}</span>
              <span class="hormone-desc">{h.desc}</span>
            </div>
            <span class="hormone-pct" style="color:{hormoneColor(val)}">{(val * 100).toFixed(0)}%</span>
          </div>
          <div class="hormone-bar-track">
            <div class="hormone-bar-fill" style="width:{val * 100}%; background:{hormoneColor(val)}"></div>
          </div>
          <div class="hormone-footer">
            <span class="severity-badge" style="color:{SEVERITY_COLORS[sev]}">{SEVERITY_LABELS[sev] ?? 'Unknown'}</span>
          </div>
        </div>
      {/each}
    </div>
  </section>

  <!-- Mood tags -->
  {#if status.mood_tags.length > 0}
    <section class="section">
      <h3 class="section-heading">Mood</h3>
      <div class="mood-tags">
        {#each status.mood_tags as tag}
          <span class="mood-tag">{tag}</span>
        {/each}
      </div>
    </section>
  {/if}

  <!-- Subsystem gates -->
  <section class="section">
    <h3 class="section-heading">Subsystem Gates</h3>
    <div class="gates-grid">
      {#each Object.entries(status.subsystems) as [system, gate]}
        <div class="gate-card" class:disabled={!gate.enabled}>
          <div class="gate-top">
            <span class="gate-name">{system}</span>
            <button
              class="gate-toggle"
              class:on={gate.enabled}
              disabled={toggling === system}
              onclick={() => toggleSystem(system, !gate.enabled)}
            >
              {gate.enabled ? 'Enabled' : 'Disabled'}
            </button>
          </div>
          {#if !gate.enabled}
            <div class="gate-info">
              <span class="gate-reason">{gate.disabled_reason || 'No reason given'}</span>
              {#if gate.disabled_until}
                <span class="gate-until">Until {fmtTs(gate.disabled_until)}</span>
              {/if}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  </section>

  <!-- Doctor notes -->
  <section class="section">
    <h3 class="section-heading">Doctor Notes</h3>
    {#if status.doctor_notes.length === 0}
      <p class="muted">No doctor visits recorded.</p>
    {:else}
      <div class="doctor-notes">
        {#each status.doctor_notes as note}
          <div class="doctor-note" class:revelation={note.doctor_revelation}>
            <div class="note-header">
              <span class="note-time">{relTime(note.ts)}</span>
              <span class="note-source">{note.source}</span>
              {#if note.provider}
                <span class="note-provider">{note.provider}/{note.model}</span>
              {/if}
            </div>
            <div class="note-summary">{note.summary || '(no summary)'}</div>
          </div>
        {/each}
      </div>
    {/if}
  </section>

  <!-- Recent adjustments -->
  <section class="section">
    <h3 class="section-heading">Recent Adjustments</h3>
    {#if adjustments.length === 0}
      <p class="muted">No adjustments recorded.</p>
    {:else}
      <div class="adjustments-table-wrap">
        <table class="adjustments-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Reason</th>
              <th>Dopa</th>
              <th>Adr</th>
              <th>Cort</th>
              <th>Endo</th>
            </tr>
          </thead>
          <tbody>
            {#each adjustments as adj}
              <tr>
                <td class="cell-time" title={fmtTs(adj.ts)}>{relTime(adj.ts)}</td>
                <td class="cell-source">{adj.source}</td>
                <td class="cell-reason">{adj.reason}</td>
                {#each ['dopamine', 'adrenaline', 'cortisol', 'endorphin'] as h}
                  {@const d = adj.deltas[h] ?? 0}
                  <td class="cell-delta" class:pos={d > 0} class:neg={d < 0}>
                    {d !== 0 ? deltaStr(d) : '—'}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>
{/if}

<style>
  .page-header {
    margin-bottom: 1.25rem;
  }
  .page-sub {
    font-size: 0.82rem;
    color: var(--text-dim);
  }
  .error-banner {
    background: rgba(243, 139, 168, 0.12);
    color: var(--red);
    padding: 0.65rem 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
    font-size: 0.85rem;
  }
  .section {
    margin-bottom: 1.5rem;
  }
  .section-heading {
    font-size: 0.92rem;
    font-weight: 700;
    margin-bottom: 0.6rem;
    color: var(--text);
  }
  .muted {
    color: var(--text-dim);
    font-size: 0.85rem;
  }

  /* Hormones */
  .hormones-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.75rem;
  }
  .hormone-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
  }
  .hormone-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
  }
  .hormone-emoji { font-size: 1.3rem; }
  .hormone-meta {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
  .hormone-name { font-weight: 600; font-size: 0.9rem; }
  .hormone-desc { font-size: 0.72rem; color: var(--text-dim); }
  .hormone-pct { font-size: 1.1rem; font-weight: 700; font-variant-numeric: tabular-nums; }
  .hormone-bar-track {
    height: 6px;
    background: var(--bg-surface2, rgba(255,255,255,0.06));
    border-radius: 3px;
    overflow: hidden;
  }
  .hormone-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }
  .hormone-footer {
    margin-top: 0.35rem;
    display: flex;
    justify-content: flex-end;
  }
  .severity-badge {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  /* Mood tags */
  .mood-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .mood-tag {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.2rem 0.65rem;
    font-size: 0.78rem;
    color: var(--text);
  }

  /* Subsystem gates */
  .gates-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 0.65rem;
  }
  .gate-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.65rem 0.9rem;
  }
  .gate-card.disabled {
    border-color: var(--red);
    background: rgba(243, 139, 168, 0.04);
  }
  .gate-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .gate-name {
    font-weight: 600;
    font-size: 0.88rem;
    text-transform: capitalize;
  }
  .gate-toggle {
    font-size: 0.72rem;
    padding: 0.2rem 0.55rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    cursor: pointer;
    background: var(--bg-surface2, rgba(255,255,255,0.06));
    color: var(--text);
    font-weight: 600;
    transition: background 0.15s;
  }
  .gate-toggle.on {
    background: rgba(166, 227, 161, 0.15);
    color: var(--green);
    border-color: var(--green);
  }
  .gate-toggle:hover { opacity: 0.85; }
  .gate-info {
    margin-top: 0.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .gate-reason { font-size: 0.78rem; color: var(--red); }
  .gate-until { font-size: 0.72rem; color: var(--text-dim); }

  /* Doctor notes */
  .doctor-notes {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .doctor-note {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.6rem 0.85rem;
  }
  .doctor-note.revelation {
    border-left: 3px solid var(--yellow);
  }
  .note-header {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.3rem;
    font-size: 0.75rem;
    color: var(--text-dim);
  }
  .note-time { font-variant-numeric: tabular-nums; }
  .note-source { font-weight: 600; color: var(--text); }
  .note-provider { font-style: italic; }
  .note-summary { font-size: 0.85rem; line-height: 1.45; }

  /* Adjustments table */
  .adjustments-table-wrap {
    overflow-x: auto;
  }
  .adjustments-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  .adjustments-table th {
    text-align: left;
    padding: 0.4rem 0.55rem;
    font-weight: 600;
    font-size: 0.75rem;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .adjustments-table td {
    padding: 0.35rem 0.55rem;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .cell-time {
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: var(--text-dim);
  }
  .cell-source { font-weight: 600; white-space: nowrap; }
  .cell-reason { max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
  .cell-delta { text-align: center; font-variant-numeric: tabular-nums; font-weight: 600; }
  .cell-delta.pos { color: var(--red); }
  .cell-delta.neg { color: var(--green); }
</style>
