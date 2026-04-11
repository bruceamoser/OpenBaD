<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, put as apiPut, post as apiPost } from '$lib/api/client';
  import {
    cpuTelemetry,
    memoryTelemetry,
    diskTelemetry,
    networkTelemetry,
    endocrineLevels,
    fsmState,
  } from '$lib/stores/websocket';

  interface Transition { from: string; to: string; ts: string; }

  interface SleepConfig {
    sleep_window_start: string;
    sleep_window_duration_hours: number;
    idle_timeout_minutes: number;
    allow_daytime_naps: boolean;
    enabled: boolean;
  }

  interface SleepConfigResponse {
    sleep: SleepConfig;
    next_scheduled_consolidation: string | null;
    last_consolidation_summary: Record<string, unknown> | null;
  }

  const FSM_STATES = ['IDLE', 'ACTIVE', 'THROTTLED', 'SLEEP', 'EMERGENCY'];
  let transitions: Transition[] = $state([]);
  let cpuHistory: number[] = $state([]);
  let memHistory: number[] = $state([]);
  let statusMsg = $state('');
  let sleepSaveMsg = $state('');
  let sleepConfig = $state<SleepConfig>({
    sleep_window_start: '02:00',
    sleep_window_duration_hours: 3,
    idle_timeout_minutes: 15,
    allow_daytime_naps: true,
    enabled: true,
  });
  let nextScheduledConsolidation = $state<string | null>(null);
  let lastConsolidationSummary = $state<Record<string, unknown> | null>(null);

  let prevFsm = '';
  const SPARKLINE_MAX = 300;

  let currentFsm = $derived($fsmState?.current_state?.toUpperCase() ?? 'IDLE');
  let cpu = $derived($cpuTelemetry?.usage_percent ?? 0);
  let mem = $derived($memoryTelemetry?.usage_percent ?? 0);
  let disk = $derived($diskTelemetry?.usage_percent ?? 0);
  let netTx = $derived($networkTelemetry?.bytes_sent ?? 0);
  let netRx = $derived($networkTelemetry?.bytes_recv ?? 0);
  let dopamine = $derived($endocrineLevels?.dopamine ?? 0);
  let adrenaline = $derived($endocrineLevels?.adrenaline ?? 0);
  let cortisol = $derived($endocrineLevels?.cortisol ?? 0);
  let endorphin = $derived($endocrineLevels?.endorphin ?? 0);

  $effect(() => {
    if (currentFsm && currentFsm !== prevFsm && prevFsm) {
      transitions = [{ from: prevFsm, to: currentFsm, ts: new Date().toLocaleTimeString() }, ...transitions].slice(0, 10);
    }
    prevFsm = currentFsm;
  });

  let historyInterval: ReturnType<typeof setInterval> | undefined;

  async function loadSleepConfig(): Promise<void> {
    try {
      const res = await apiGet<SleepConfigResponse>('/api/sleep/config');
      sleepConfig = {
        sleep_window_start: res.sleep?.sleep_window_start || '02:00',
        sleep_window_duration_hours: Number(res.sleep?.sleep_window_duration_hours || 3),
        idle_timeout_minutes: Number(res.sleep?.idle_timeout_minutes || 15),
        allow_daytime_naps: !!res.sleep?.allow_daytime_naps,
        enabled: res.sleep?.enabled !== false,
      };
      nextScheduledConsolidation = res.next_scheduled_consolidation;
      lastConsolidationSummary = res.last_consolidation_summary;
    } catch (e) {
      sleepSaveMsg = `Sleep settings unavailable: ${e}`;
    }
  }

  async function saveSleepConfig(): Promise<void> {
    try {
      const res = await apiPut<SleepConfigResponse>('/api/sleep/config', { sleep: sleepConfig });
      sleepConfig = res.sleep;
      nextScheduledConsolidation = res.next_scheduled_consolidation;
      lastConsolidationSummary = res.last_consolidation_summary;
      sleepSaveMsg = 'Sleep settings saved';
    } catch (e) {
      sleepSaveMsg = `Save failed: ${e}`;
    }
  }

  function formatIso(ts: string | null): string {
    if (!ts) return 'Not scheduled';
    const parsed = new Date(ts);
    if (Number.isNaN(parsed.getTime())) return ts;
    return parsed.toLocaleString();
  }

  onMount(() => {
    loadSleepConfig();
    historyInterval = setInterval(() => {
      cpuHistory = [...cpuHistory, cpu].slice(-SPARKLINE_MAX);
      memHistory = [...memHistory, mem].slice(-SPARKLINE_MAX);
    }, 1000);
  });
  onDestroy(() => { if (historyInterval) clearInterval(historyInterval); });

  function fsmColor(state: string): string {
    switch (state) {
      case 'IDLE':      return 'var(--green)';
      case 'ACTIVE':    return 'var(--blue)';
      case 'THROTTLED': return 'var(--yellow)';
      case 'SLEEP':     return 'var(--mauve)';
      case 'EMERGENCY': return 'var(--red)';
      default:          return 'var(--text-dim)';
    }
  }

  function hormoneColor(val: number): string {
    if (val < 0.3) return 'var(--green)';
    if (val < 0.7) return 'var(--yellow)';
    return 'var(--red)';
  }

  function sparklinePath(data: number[], w: number, h: number): string {
    if (data.length < 2) return '';
    const step = w / (data.length - 1);
    return data.map((v, i) => {
      const x = i * step;
      const y = h - (v / 100) * h;
      return `${i === 0 ? 'M' : 'L'}${x},${y}`;
    }).join(' ');
  }

  function sparklineArea(data: number[], w: number, h: number): string {
    if (data.length < 2) return '';
    const path = sparklinePath(data, w, h);
    const step = w / (data.length - 1);
    return `${path} L${(data.length - 1) * step},${h} L0,${h} Z`;
  }

  function formatBytes(b: number): string {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function triggerSleep(): Promise<void> {
    try { await apiPost('/api/sleep/trigger'); statusMsg = 'Sleep triggered'; }
    catch (e) { statusMsg = `Failed: ${e}`; }
  }
  async function triggerWake(): Promise<void> {
    try { await apiPost('/api/sleep/wake'); statusMsg = 'Wake triggered'; }
    catch (e) { statusMsg = `Failed: ${e}`; }
  }
</script>

<div class="page-header">
  <h2>Health Dashboard</h2>
  <p>Live runtime telemetry and subsystem health</p>
</div>

<div class="grid dashboard-grid">
  <!-- Row 1: FSM + Endocrine -->
  <Card label="FSM State">
    <div class="fsm-section">
      <div class="fsm-state-row">
        <div class="fsm-badge" style="background:{fsmColor(currentFsm)}">
          <span class="fsm-icon">◉</span>
          {currentFsm}
        </div>
        <div class="fsm-states">
          {#each FSM_STATES as s}
            <span class="fsm-mini" class:current={s === currentFsm} style="--c:{fsmColor(s)}">{s}</span>
          {/each}
        </div>
      </div>
      {#if transitions.length > 0}
        <div class="transitions">
          <h4>Recent Transitions</h4>
          <div class="transition-list transition-log">
            {#each transitions as t}
              <div class="transition-item">
                <span class="t-time">{t.ts}</span>
                <span class="t-from">{t.from}</span>
                <span class="t-arrow">→</span>
                <span class="t-to">{t.to}</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  </Card>

  <Card label="Endocrine Levels">
    <div class="hormones">
      {#each [
        { name: 'Dopamine', val: dopamine, emoji: '🧠' },
        { name: 'Adrenaline', val: adrenaline, emoji: '⚡' },
        { name: 'Cortisol', val: cortisol, emoji: '🔥' },
        { name: 'Endorphin', val: endorphin, emoji: '✨' },
      ] as h}
        <div class="hormone">
          <div class="hormone-label">
            <span class="hormone-emoji">{h.emoji}</span>
            <span class="hormone-name">{h.name}</span>
          </div>
          <div class="hormone-bar">
            <div class="hormone-fill gauge-bar-fill" style="width:{h.val * 100}%; background:{hormoneColor(h.val)}"></div>
          </div>
          <span class="hormone-val">{(h.val * 100).toFixed(0)}%</span>
        </div>
      {/each}
    </div>
  </Card>

  <!-- Row 2: CPU + Memory sparklines -->
  <Card label="CPU Usage">
    <div class="sparkline-card">
      <div class="sparkline-metric">
        <span class="metric-value text-blue">{cpu.toFixed(1)}%</span>
        <span class="metric-sub">5 min history</span>
      </div>
      <svg class="sparkline" viewBox="0 0 300 60" preserveAspectRatio="none">
        <path d={sparklineArea(cpuHistory, 300, 60)} fill="rgba(137, 180, 250, 0.1)" />
        <path d={sparklinePath(cpuHistory, 300, 60)} fill="none" stroke="var(--blue)" stroke-width="2" />
      </svg>
    </div>
  </Card>

  <Card label="Memory Usage">
    <div class="sparkline-card">
      <div class="sparkline-metric">
        <span class="metric-value text-mauve">{mem.toFixed(1)}%</span>
        <span class="metric-sub">5 min history</span>
      </div>
      <svg class="sparkline" viewBox="0 0 300 60" preserveAspectRatio="none">
        <path d={sparklineArea(memHistory, 300, 60)} fill="rgba(203, 166, 247, 0.1)" />
        <path d={sparklinePath(memHistory, 300, 60)} fill="none" stroke="var(--mauve)" stroke-width="2" />
      </svg>
    </div>
  </Card>

  <!-- Row 3: I/O + Sleep -->
  <Card label="Disk / Network I/O">
    <div class="io-grid">
      <div class="io-item">
        <span class="io-icon">💾</span>
        <div class="io-detail">
          <span class="io-label">Disk</span>
          <span class="io-value">{disk.toFixed(1)}%</span>
        </div>
        <div class="io-bar">
          <div class="io-fill" style="width:{disk}%; background:var(--teal)"></div>
        </div>
      </div>
      <div class="io-item">
        <span class="io-icon">📤</span>
        <div class="io-detail">
          <span class="io-label">Net TX</span>
          <span class="io-value">{formatBytes(netTx)}</span>
        </div>
      </div>
      <div class="io-item">
        <span class="io-icon">📥</span>
        <div class="io-detail">
          <span class="io-label">Net RX</span>
          <span class="io-value">{formatBytes(netRx)}</span>
        </div>
      </div>
    </div>
  </Card>

  <Card label="Sleep Schedule">
    <div class="sleep-section">
      <p class="muted">Configure scheduled consolidation and idle-aware sleep behavior.</p>
      <div class="sleep-config-grid">
        <label>
          Start
          <input type="time" bind:value={sleepConfig.sleep_window_start} />
        </label>
        <label>
          Duration (hours)
          <input type="number" min="0.5" max="12" step="0.5" bind:value={sleepConfig.sleep_window_duration_hours} />
        </label>
        <label>
          Idle timeout (minutes)
          <input type="number" min="1" max="180" step="1" bind:value={sleepConfig.idle_timeout_minutes} />
        </label>
      </div>
      <label class="sleep-check">
        <input type="checkbox" bind:checked={sleepConfig.allow_daytime_naps} />
        Allow daytime naps when idle
      </label>
      <label class="sleep-check">
        <input type="checkbox" bind:checked={sleepConfig.enabled} />
        Enable scheduler
      </label>
      <div class="sleep-metadata">
        <div><strong>Next scheduled consolidation:</strong> {formatIso(nextScheduledConsolidation)}</div>
        <div>
          <strong>Last summary:</strong>
          {#if lastConsolidationSummary}
            {JSON.stringify(lastConsolidationSummary)}
          {:else}
            none yet
          {/if}
        </div>
      </div>
      <div class="sleep-actions">
        <button class="secondary" onclick={saveSleepConfig}>Save Settings</button>
        <button onclick={triggerSleep}>😴 Sleep Now</button>
        <button class="secondary" onclick={triggerWake}>☀️ Wake</button>
      </div>
      {#if sleepSaveMsg}
        <p class="status-msg">{sleepSaveMsg}</p>
      {/if}
      {#if statusMsg}
        <p class="status-msg">{statusMsg}</p>
      {/if}
    </div>
  </Card>
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  @media (max-width: 900px) {
    .grid { grid-template-columns: 1fr; }
  }

  /* FSM */
  .fsm-section { display: flex; flex-direction: column; gap: 1rem; }
  .fsm-state-row { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  .fsm-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.45rem 1.1rem;
    border-radius: var(--radius-sm);
    color: var(--text-on-color);
    font-weight: 700;
    font-size: 1rem;
  }
  .fsm-icon { font-size: 0.7rem; }
  .fsm-states { display: flex; gap: 0.35rem; flex-wrap: wrap; }
  .fsm-mini {
    font-size: 0.65rem;
    font-weight: 600;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    background: var(--bg-surface1);
    color: var(--text-dim);
    letter-spacing: 0.03em;
  }
  .fsm-mini.current {
    background: color-mix(in srgb, var(--c) 20%, transparent);
    color: var(--c);
  }

  .transitions h4 {
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-bottom: 0.4rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .transition-list { display: flex; flex-direction: column; gap: 0.2rem; }
  .transition-item {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    padding: 0.25rem 0.5rem;
    background: var(--bg-surface1);
    border-radius: var(--radius-sm);
  }
  .t-time { color: var(--text-dim); font-size: 0.7rem; width: 5rem; }
  .t-from { color: var(--text-sub); }
  .t-arrow { color: var(--text-dim); }
  .t-to { color: var(--text); font-weight: 600; }

  /* Hormones */
  .hormones { display: flex; flex-direction: column; gap: 0.75rem; }
  .hormone { display: flex; align-items: center; gap: 0.6rem; }
  .hormone-label { display: flex; align-items: center; gap: 0.4rem; width: 7.5rem; flex-shrink: 0; }
  .hormone-emoji { font-size: 1rem; }
  .hormone-name { font-size: 0.85rem; font-weight: 600; color: var(--text-sub); }
  .hormone-bar {
    flex: 1;
    height: 8px;
    background: var(--bg-surface1);
    border-radius: 4px;
    overflow: hidden;
  }
  .hormone-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.4s var(--ease);
  }
  .hormone-val {
    width: 3rem;
    text-align: right;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text-sub);
  }

  /* Sparklines */
  .sparkline-card { display: flex; flex-direction: column; gap: 0.5rem; }
  .sparkline-metric { display: flex; align-items: baseline; gap: 0.5rem; }
  .metric-value { font-size: 1.5rem; font-weight: 700; }
  .metric-sub { font-size: 0.75rem; color: var(--text-dim); }
  .sparkline { width: 100%; height: 60px; }

  /* I/O */
  .io-grid { display: flex; flex-direction: column; gap: 0.75rem; }
  .io-item { display: flex; align-items: center; gap: 0.6rem; }
  .io-icon { font-size: 1.1rem; flex-shrink: 0; }
  .io-detail { display: flex; flex-direction: column; flex: 1; min-width: 0; }
  .io-label { font-size: 0.75rem; color: var(--text-dim); }
  .io-value { font-size: 0.95rem; font-weight: 600; }
  .io-bar {
    width: 100%;
    height: 4px;
    background: var(--bg-surface1);
    border-radius: 2px;
    overflow: hidden;
   }
  .io-fill { height: 100%; border-radius: 2px; transition: width 0.3s var(--ease); }

  /* Sleep */
  .sleep-section { display: flex; flex-direction: column; gap: 0.75rem; }
  .sleep-config-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
  }
  .sleep-config-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.8rem;
    color: var(--text-sub);
  }
  .sleep-config-grid input {
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
  }
  .sleep-check {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.85rem;
  }
  .sleep-metadata {
    font-size: 0.8rem;
    color: var(--text-sub);
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .sleep-actions { display: flex; gap: 0.5rem; }
  .status-msg { font-size: 0.85rem; color: var(--text-sub); }

  @media (max-width: 900px) {
    .sleep-config-grid { grid-template-columns: 1fr; }
  }
</style>
