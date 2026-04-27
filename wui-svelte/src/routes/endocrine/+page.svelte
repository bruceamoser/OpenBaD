<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { get as apiGet, post as apiPost, put as apiPut } from '$lib/api/client';
  import { endocrineLevels } from '$lib/stores/websocket';

  /* ── Types ─────────────────────────────────────────────── */

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

  interface HormoneConfig {
    increment: number;
    activation_threshold: number;
    escalation_threshold: number | null;
    half_life_seconds: number;
  }

  interface RewardMapping {
    hormone: string;
    amount: number;
  }

  interface EndocrineConfig {
    hormones: Record<string, HormoneConfig>;
    publish_interval_seconds: number;
    significant_change_delta: number;
    reward_mappings: Record<string, RewardMapping[]>;
  }

  /* ── Constants ─────────────────────────────────────────── */

  const HORMONES = [
    { key: 'dopamine',   emoji: '🧠', label: 'Dopamine',   desc: 'Reward & exploration drive' },
    { key: 'adrenaline', emoji: '⚡', label: 'Adrenaline', desc: 'Urgency & threat response' },
    { key: 'cortisol',   emoji: '🔥', label: 'Cortisol',   desc: 'Sustained stress level' },
    { key: 'endorphin',  emoji: '✨', label: 'Endorphin',  desc: 'Recovery & resilience' },
  ];

  const SEVERITY_LABELS: Record<number, string> = { 1: 'Normal', 2: 'Activated', 3: 'Escalated' };
  const SEVERITY_COLORS: Record<number, string> = { 1: 'var(--green)', 2: 'var(--yellow)', 3: 'var(--red)' };

  const OUTCOME_LABELS: Record<string, string> = {
    success: 'Task Success',
    failure: 'Task Failure',
    timeout: 'Task Timeout',
    cancelled: 'Task Cancelled',
  };

  /* ── State ─────────────────────────────────────────────── */

  let status: EndocrineStatus = $state({
    levels: {}, mood_tags: [], subsystems: {}, severity: {}, doctor_notes: [],
  });
  let adjustments: Adjustment[] = $state([]);
  let config: EndocrineConfig | null = $state(null);
  let editConfig: EndocrineConfig | null = $state(null);

  let loading = $state(true);
  let toggling = $state('');
  let saving = $state(false);
  let resetting = $state(false);
  let error = $state('');
  let saveMsg = $state('');
  let activeTab: 'overview' | 'config' | 'activity' = $state('overview');
  let pollTimer: ReturnType<typeof setInterval> | undefined;

  /* ── Live levels from WebSocket ────────────────────────── */

  let liveLevels = $derived({
    dopamine:   $endocrineLevels?.dopamine   ?? status.levels.dopamine   ?? 0,
    adrenaline: $endocrineLevels?.adrenaline ?? status.levels.adrenaline ?? 0,
    cortisol:   $endocrineLevels?.cortisol   ?? status.levels.cortisol   ?? 0,
    endorphin:  $endocrineLevels?.endorphin  ?? status.levels.endorphin  ?? 0,
  } as Record<string, number>);

  /* ── Data loading ──────────────────────────────────────── */

  async function loadStatus(): Promise<void> {
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

  async function loadConfig(): Promise<void> {
    try {
      config = await apiGet<EndocrineConfig>('/api/endocrine/config');
      editConfig = structuredClone(config);
    } catch (err) {
      error = String(err);
    }
  }

  async function saveConfig(): Promise<void> {
    if (!editConfig) return;
    saving = true;
    saveMsg = '';
    error = '';
    try {
      config = await apiPut<EndocrineConfig>('/api/endocrine/config', editConfig);
      editConfig = structuredClone(config);
      saveMsg = 'Configuration saved. Changes take effect on next heartbeat tick.';
      setTimeout(() => { saveMsg = ''; }, 5000);
    } catch (err) {
      error = String(err);
    } finally {
      saving = false;
    }
  }

  function resetConfig(): void {
    if (config) editConfig = structuredClone(config);
  }

  /* ── Actions ───────────────────────────────────────────── */

  async function toggleSystem(system: string, enabled: boolean): Promise<void> {
    toggling = system;
    error = '';
    try {
      status = await apiPost<EndocrineStatus>('/api/endocrine/toggle', {
        system,
        enabled,
        reason: 'manual toggle from endocrine UI',
      });
    } catch (err) {
      error = String(err);
    } finally {
      toggling = '';
    }
  }

  async function resetLevels(): Promise<void> {
    resetting = true;
    error = '';
    try {
      status = await apiPost<EndocrineStatus>('/api/endocrine/reset', {});
      saveMsg = 'Hormone levels and mood tags reset to zero.';
      setTimeout(() => { saveMsg = ''; }, 4000);
    } catch (err) {
      error = String(err);
    } finally {
      resetting = false;
    }
  }

  /* ── Helpers ───────────────────────────────────────────── */

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
    return d > 0 ? `+${d.toFixed(2)}` : d.toFixed(2);
  }

  function fmtHalfLife(s: number): string {
    if (s < 60) return `${s.toFixed(0)}s`;
    if (s < 3600) return `${(s / 60).toFixed(1)}m`;
    return `${(s / 3600).toFixed(1)}h`;
  }

  /* ── Lifecycle ─────────────────────────────────────────── */

  onMount(() => {
    loadStatus();
    loadConfig();
    pollTimer = setInterval(loadStatus, 15000);
  });
  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- TEMPLATE                                                    -->
<!-- ═══════════════════════════════════════════════════════════ -->

<div class="page-header">
  <h2>Endocrine System</h2>
  <span class="page-sub">Hormone levels, tuning, reward signals, subsystem gates, and activity log</span>
</div>

{#if error}
  <div class="banner error-banner">{error}</div>
{/if}
{#if saveMsg}
  <div class="banner success-banner">{saveMsg}</div>
{/if}

<!-- Tab bar -->
<div class="tab-bar">
  <button class="tab" class:active={activeTab === 'overview'} onclick={() => activeTab = 'overview'}>Overview</button>
  <button class="tab" class:active={activeTab === 'config'} onclick={() => activeTab = 'config'}>Configuration</button>
  <button class="tab" class:active={activeTab === 'activity'} onclick={() => activeTab = 'activity'}>Activity Log</button>
</div>

{#if loading && !adjustments.length}
  <p class="muted">Loading…</p>
{:else}

  <!-- ═══════════════ OVERVIEW TAB ═══════════════ -->
  {#if activeTab === 'overview'}

    <!-- Hormone levels -->
    <section class="section">
      <div class="section-head">
        <h3 class="section-heading">Hormone Levels</h3>
        <button class="btn-sm btn-danger" onclick={resetLevels} disabled={resetting}>
          {resetting ? 'Resetting…' : 'Reset All'}
        </button>
      </div>
      <div class="hormones-grid">
        {#each HORMONES as h}
          {@const val = liveLevels[h.key] ?? 0}
          {@const sev = status.severity[h.key] ?? 1}
          {@const cfg = config?.hormones[h.key]}
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
              {#if cfg}
                <div class="threshold-marker activation" style="left:{cfg.activation_threshold * 100}%" title="Activation: {(cfg.activation_threshold * 100).toFixed(0)}%"></div>
                {#if cfg.escalation_threshold != null}
                  <div class="threshold-marker escalation" style="left:{cfg.escalation_threshold * 100}%" title="Escalation: {(cfg.escalation_threshold * 100).toFixed(0)}%"></div>
                {/if}
              {/if}
              <div class="hormone-bar-fill" style="width:{val * 100}%; background:{hormoneColor(val)}"></div>
            </div>
            <div class="hormone-footer">
              <span class="severity-badge" style="color:{SEVERITY_COLORS[sev]}">{SEVERITY_LABELS[sev] ?? 'Unknown'}</span>
              {#if cfg}
                <span class="half-life-label">t½ {fmtHalfLife(cfg.half_life_seconds)}</span>
              {/if}
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

  <!-- ═══════════════ CONFIGURATION TAB ═══════════════ -->
  {:else if activeTab === 'config'}

    {#if editConfig}
      <!-- Per-hormone settings -->
      <section class="section">
        <h3 class="section-heading">Hormone Parameters</h3>
        <p class="section-desc">Configure activation thresholds, decay rates, and increment values for each hormone. Changes take effect on the next heartbeat tick after saving.</p>
        <div class="config-cards">
          {#each HORMONES as h}
            {@const hcfg = editConfig.hormones[h.key]}
            {#if hcfg}
              <div class="config-card">
                <div class="config-card-header">
                  <span class="hormone-emoji">{h.emoji}</span>
                  <span class="config-card-title">{h.label}</span>
                </div>
                <div class="config-fields">
                  <label class="config-field">
                    <span class="field-label">Activation Threshold</span>
                    <span class="field-help">Level at which this hormone is considered "activated" (0–1)</span>
                    <input type="number" min="0" max="1" step="0.05"
                      bind:value={hcfg.activation_threshold} />
                  </label>
                  <label class="config-field">
                    <span class="field-label">Escalation Threshold</span>
                    <span class="field-help">Level for critical/escalated state (0–1, leave empty if N/A)</span>
                    <input type="number" min="0" max="1" step="0.05"
                      value={hcfg.escalation_threshold ?? ''}
                      oninput={(e) => {
                        const v = (e.currentTarget as HTMLInputElement).value;
                        hcfg.escalation_threshold = v === '' ? null : parseFloat(v);
                      }} />
                  </label>
                  <label class="config-field">
                    <span class="field-label">Half-Life</span>
                    <span class="field-help">Seconds for the level to decay by 50%</span>
                    <div class="field-with-unit">
                      <input type="number" min="1" step="10"
                        bind:value={hcfg.half_life_seconds} />
                      <span class="field-unit">{fmtHalfLife(hcfg.half_life_seconds)}</span>
                    </div>
                  </label>
                  <label class="config-field">
                    <span class="field-label">Increment</span>
                    <span class="field-help">Default adjustment step size</span>
                    <input type="number" min="0" max="1" step="0.05"
                      bind:value={hcfg.increment} />
                  </label>
                </div>
              </div>
            {/if}
          {/each}
        </div>
      </section>

      <!-- Reward mappings -->
      <section class="section">
        <h3 class="section-heading">Reward Signal Mappings</h3>
        <p class="section-desc">How task outcomes translate to hormone adjustments. Positive values boost the hormone, negative values reduce it.</p>
        <div class="reward-grid">
          {#each Object.entries(editConfig.reward_mappings) as [outcome, mappings]}
            <div class="reward-card">
              <div class="reward-header">{OUTCOME_LABELS[outcome] ?? outcome}</div>
              {#if mappings.length === 0}
                <p class="muted" style="font-size:0.8rem; margin:0.3rem 0">No hormone adjustments</p>
              {:else}
                <div class="reward-mappings">
                  {#each mappings as mapping}
                    <div class="reward-row">
                      <span class="reward-hormone">{mapping.hormone}</span>
                      <input type="number" min="-1" max="1" step="0.05"
                        class="reward-input"
                        bind:value={mapping.amount} />
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </section>

      <!-- Save / reset buttons -->
      <div class="config-actions">
        <button class="btn btn-primary" onclick={saveConfig} disabled={saving}>
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
        <button class="btn btn-ghost" onclick={resetConfig} disabled={saving}>
          Discard Changes
        </button>
      </div>
    {:else}
      <p class="muted">Loading configuration…</p>
    {/if}

  <!-- ═══════════════ ACTIVITY LOG TAB ═══════════════ -->
  {:else if activeTab === 'activity'}

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
{/if}

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STYLES                                                      -->
<!-- ═══════════════════════════════════════════════════════════ -->

<style>
  /* ── Page header ─────────────── */
  .page-header { margin-bottom: 1rem; }
  .page-sub { font-size: 0.82rem; color: var(--text-dim); }

  /* ── Banners ─────────────────── */
  .banner {
    padding: 0.6rem 1rem;
    border-radius: var(--radius-sm);
    margin-bottom: 0.75rem;
    font-size: 0.85rem;
  }
  .error-banner {
    background: rgba(243, 139, 168, 0.12);
    color: var(--red);
  }
  .success-banner {
    background: rgba(166, 227, 161, 0.12);
    color: var(--green);
  }

  /* ── Tab bar ─────────────────── */
  .tab-bar {
    display: flex;
    gap: 0.25rem;
    margin-bottom: 1.25rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0;
  }
  .tab {
    padding: 0.5rem 1rem;
    font-size: 0.85rem;
    font-weight: 600;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-dim);
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  /* ── Sections ────────────────── */
  .section { margin-bottom: 1.5rem; }
  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.6rem;
  }
  .section-heading {
    font-size: 0.92rem;
    font-weight: 700;
    margin-bottom: 0.6rem;
    color: var(--text);
  }
  .section-head .section-heading { margin-bottom: 0; }
  .section-desc {
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-bottom: 0.85rem;
    line-height: 1.45;
  }
  .muted { color: var(--text-dim); font-size: 0.85rem; }

  /* ── Buttons ─────────────────── */
  .btn {
    padding: 0.5rem 1.25rem;
    border-radius: var(--radius-sm);
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.85; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary {
    background: var(--accent);
    color: var(--text-on-color);
    border-color: var(--accent);
  }
  .btn-ghost {
    background: transparent;
    color: var(--text);
  }
  .btn-sm {
    padding: 0.3rem 0.7rem;
    font-size: 0.75rem;
    font-weight: 600;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    cursor: pointer;
    background: var(--bg-surface2, rgba(255,255,255,0.06));
    color: var(--text);
  }
  .btn-danger {
    color: var(--red);
    border-color: rgba(243, 139, 168, 0.3);
  }
  .btn-sm:hover { opacity: 0.85; }
  .btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }

  /* ── Hormone cards (overview) ── */
  .hormones-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 0.75rem;
  }
  .hormone-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.75rem 1rem;
  }
  .hormone-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
  }
  .hormone-emoji { font-size: 1.3rem; }
  .hormone-meta { flex: 1; display: flex; flex-direction: column; }
  .hormone-name { font-weight: 600; font-size: 0.9rem; }
  .hormone-desc { font-size: 0.72rem; color: var(--text-dim); }
  .hormone-pct { font-size: 1.1rem; font-weight: 700; font-variant-numeric: tabular-nums; }
  .hormone-bar-track {
    position: relative;
    height: 8px;
    background: var(--bg-surface2, rgba(255,255,255,0.06));
    border-radius: 4px;
    overflow: visible;
  }
  .hormone-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.4s ease;
    position: relative;
    z-index: 1;
  }
  .threshold-marker {
    position: absolute;
    top: -3px;
    width: 2px;
    height: 14px;
    border-radius: 1px;
    z-index: 2;
  }
  .threshold-marker.activation { background: var(--yellow); }
  .threshold-marker.escalation { background: var(--red); }
  .hormone-footer {
    margin-top: 0.4rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .severity-badge {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .half-life-label {
    font-size: 0.68rem;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }

  /* ── Mood tags ───────────────── */
  .mood-tags { display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .mood-tag {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.2rem 0.65rem;
    font-size: 0.78rem;
    color: var(--text);
  }

  /* ── Subsystem gates ─────────── */
  .gates-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 0.65rem;
  }
  .gate-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
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
    border-radius: var(--radius-sm);
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

  /* ── Doctor notes ────────────── */
  .doctor-notes { display: flex; flex-direction: column; gap: 0.5rem; }
  .doctor-note {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.6rem 0.85rem;
  }
  .doctor-note.revelation { border-left: 3px solid var(--yellow); }
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

  /* ── Config cards ────────────── */
  .config-cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 0.85rem;
  }
  .config-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.85rem 1rem;
  }
  .config-card-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  .config-card-title {
    font-weight: 700;
    font-size: 0.95rem;
  }
  .config-fields {
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
  }
  .config-field {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .field-label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text);
  }
  .field-help {
    font-size: 0.7rem;
    color: var(--text-dim);
    line-height: 1.3;
  }
  .config-field input {
    width: 100%;
    padding: 0.4rem 0.55rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-base);
    color: var(--text);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .config-field input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .field-with-unit {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .field-with-unit input { flex: 1; }
  .field-unit {
    font-size: 0.75rem;
    color: var(--text-dim);
    white-space: nowrap;
    min-width: 3rem;
  }

  /* ── Reward mappings ─────────── */
  .reward-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 0.75rem;
  }
  .reward-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 0.75rem 0.9rem;
  }
  .reward-header {
    font-weight: 700;
    font-size: 0.85rem;
    margin-bottom: 0.5rem;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid var(--border);
  }
  .reward-mappings { display: flex; flex-direction: column; gap: 0.4rem; }
  .reward-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .reward-hormone {
    font-size: 0.82rem;
    font-weight: 600;
    text-transform: capitalize;
  }
  .reward-input {
    width: 70px;
    padding: 0.3rem 0.4rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-base);
    color: var(--text);
    font-size: 0.82rem;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .reward-input:focus {
    outline: none;
    border-color: var(--accent);
  }

  /* ── Config actions ──────────── */
  .config-actions {
    display: flex;
    gap: 0.6rem;
    margin-top: 0.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
  }

  /* ── Adjustments table ───────── */
  .adjustments-table-wrap { overflow-x: auto; }
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

  /* ── Responsive ──────────────── */
  @media (max-width: 768px) {
    .hormones-grid { grid-template-columns: 1fr; }
    .config-cards { grid-template-columns: 1fr; }
    .reward-grid { grid-template-columns: 1fr; }
    .gates-grid { grid-template-columns: 1fr; }
  }
</style>
