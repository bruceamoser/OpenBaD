<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import {
    cpuTelemetry,
    memoryTelemetry,
    diskTelemetry,
    networkTelemetry,
    endocrineLevels,
    fsmState,
  } from '$lib/stores/websocket';

  interface Transition { from: string; to: string; ts: string; }

  const FSM_STATES = ['IDLE', 'ACTIVE', 'THROTTLED', 'SLEEP', 'EMERGENCY'];
  let transitions: Transition[] = $state([]);
  let cpuHistory: number[] = $state([]);
  let memHistory: number[] = $state([]);

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

  onMount(() => {
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

  let overallHealth = $derived(() => {
    const highCortisol = cortisol > 0.7;
    const highAdrenaline = adrenaline > 0.7;
    const highCpu = cpu > 90;
    const highMem = mem > 90;
    const emergency = currentFsm === 'EMERGENCY';
    if (emergency || (highCortisol && highAdrenaline)) return { label: 'Critical', color: 'var(--red)', icon: '🔴' };
    if (highCortisol || highAdrenaline || highCpu || highMem || currentFsm === 'THROTTLED') return { label: 'Stressed', color: 'var(--yellow)', icon: '🟡' };
    return { label: 'Healthy', color: 'var(--green)', icon: '🟢' };
  });
</script>

<div class="page-header">
  <h2>Health Dashboard</h2>
  <div class="health-summary">
    <span class="health-indicator" style="color:{overallHealth().color}">
      {overallHealth().icon} {overallHealth().label}
    </span>
    <span class="health-sub">Live runtime telemetry and subsystem health</span>
  </div>
</div>

<div class="dashboard">
  <!-- FSM State — compact banner -->
  <div class="fsm-banner">
    <div class="fsm-left">
      <span class="fsm-label">State Machine</span>
      <div class="fsm-badge" style="background:{fsmColor(currentFsm)}">
        <span class="fsm-dot">◉</span> {currentFsm}
      </div>
    </div>
    <div class="fsm-states">
      {#each FSM_STATES as s}
        <span class="fsm-pip" class:active={s === currentFsm} style="--c:{fsmColor(s)}">{s}</span>
      {/each}
    </div>
    {#if transitions.length > 0}
      <div class="fsm-transitions">
        {#each transitions.slice(0, 5) as t}
          <span class="fsm-tx"><span class="tx-time">{t.ts}</span> {t.from} → {t.to}</span>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Endocrine — horizontal cards -->
  <div class="endocrine-panel">
    <h3 class="panel-heading">Endocrine System</h3>
    <div class="hormones-grid">
      {#each [
        { name: 'Dopamine', val: dopamine, emoji: '🧠', desc: 'Reward & exploration drive' },
        { name: 'Adrenaline', val: adrenaline, emoji: '⚡', desc: 'Urgency & threat response' },
        { name: 'Cortisol', val: cortisol, emoji: '🔥', desc: 'Sustained stress level' },
        { name: 'Endorphin', val: endorphin, emoji: '✨', desc: 'Recovery & resilience' },
      ] as h}
        <div class="hormone-card">
          <div class="hormone-top">
            <span class="hormone-emoji">{h.emoji}</span>
            <span class="hormone-name">{h.name}</span>
            <span class="hormone-pct" style="color:{hormoneColor(h.val)}">{(h.val * 100).toFixed(0)}%</span>
          </div>
          <div class="hormone-bar-track">
            <div class="hormone-bar-fill" style="width:{h.val * 100}%; background:{hormoneColor(h.val)}"></div>
          </div>
          <span class="hormone-desc">{h.desc}</span>
        </div>
      {/each}
    </div>
  </div>

  <!-- CPU + Memory sparklines -->
  <div class="resource-row">
    <Card label="CPU">
      <div class="spark-wrapper">
        <div class="spark-header">
          <span class="spark-value text-blue">{cpu.toFixed(1)}%</span>
          <span class="spark-sub">5 min</span>
        </div>
        <svg class="sparkline" viewBox="0 0 300 50" preserveAspectRatio="none">
          <path d={sparklineArea(cpuHistory, 300, 50)} fill="rgba(137,180,250,0.08)" />
          <path d={sparklinePath(cpuHistory, 300, 50)} fill="none" stroke="var(--blue)" stroke-width="1.5" />
        </svg>
      </div>
    </Card>

    <Card label="Memory">
      <div class="spark-wrapper">
        <div class="spark-header">
          <span class="spark-value text-mauve">{mem.toFixed(1)}%</span>
          <span class="spark-sub">5 min</span>
        </div>
        <svg class="sparkline" viewBox="0 0 300 50" preserveAspectRatio="none">
          <path d={sparklineArea(memHistory, 300, 50)} fill="rgba(203,166,247,0.08)" />
          <path d={sparklinePath(memHistory, 300, 50)} fill="none" stroke="var(--mauve)" stroke-width="1.5" />
        </svg>
      </div>
    </Card>
  </div>

  <!-- Disk / Network I/O -->
  <div class="io-row">
    <div class="io-card">
      <span class="io-icon">💾</span>
      <div class="io-info">
        <span class="io-label">Disk</span>
        <span class="io-val">{disk.toFixed(1)}%</span>
      </div>
      <div class="io-bar"><div class="io-fill" style="width:{disk}%; background:var(--teal)"></div></div>
    </div>
    <div class="io-card">
      <span class="io-icon">📤</span>
      <div class="io-info">
        <span class="io-label">Net TX</span>
        <span class="io-val">{formatBytes(netTx)}</span>
      </div>
    </div>
    <div class="io-card">
      <span class="io-icon">📥</span>
      <div class="io-info">
        <span class="io-label">Net RX</span>
        <span class="io-val">{formatBytes(netRx)}</span>
      </div>
    </div>
  </div>
</div>

<style>
  .dashboard {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .health-summary {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 0.15rem;
  }
  .health-indicator {
    font-size: 0.95rem;
    font-weight: 700;
  }
  .health-sub {
    font-size: 0.82rem;
    color: var(--text-dim);
  }

  /* FSM Banner */
  .fsm-banner {
    display: flex;
    align-items: center;
    gap: 1.25rem;
    padding: 0.85rem 1.15rem;
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    flex-wrap: wrap;
  }
  .fsm-left {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .fsm-label {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
  }
  .fsm-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.85rem;
    border-radius: var(--radius-sm);
    color: var(--text-on-color);
    font-weight: 700;
    font-size: 0.85rem;
  }
  .fsm-dot { font-size: 0.6rem; }
  .fsm-states {
    display: flex;
    gap: 0.3rem;
    flex-wrap: wrap;
  }
  .fsm-pip {
    font-size: 0.6rem;
    font-weight: 600;
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
    background: var(--bg-surface2, var(--bg));
    color: var(--text-dim);
    letter-spacing: 0.03em;
  }
  .fsm-pip.active {
    background: color-mix(in srgb, var(--c) 20%, transparent);
    color: var(--c);
  }
  .fsm-transitions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-left: auto;
  }
  .fsm-tx {
    font-size: 0.7rem;
    color: var(--text-dim);
    padding: 0.15rem 0.5rem;
    background: var(--bg-surface2, var(--bg));
    border-radius: var(--radius-sm);
  }
  .tx-time {
    color: var(--text-dim);
    opacity: 0.7;
    margin-right: 0.25rem;
  }

  /* Endocrine Panel */
  .endocrine-panel {
    padding: 1rem 1.15rem;
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .panel-heading {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-dim);
    margin: 0 0 0.75rem;
  }
  .hormones-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
  }
  @media (max-width: 900px) {
    .hormones-grid { grid-template-columns: repeat(2, 1fr); }
  }
  @media (max-width: 500px) {
    .hormones-grid { grid-template-columns: 1fr; }
  }
  .hormone-card {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.65rem 0.75rem;
    background: var(--bg-base);
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
  }
  .hormone-top {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .hormone-emoji { font-size: 1rem; }
  .hormone-name { font-size: 0.82rem; font-weight: 600; flex: 1; }
  .hormone-pct { font-size: 0.9rem; font-weight: 700; font-variant-numeric: tabular-nums; }
  .hormone-bar-track {
    height: 6px;
    background: var(--bg-surface2, var(--bg));
    border-radius: 3px;
    overflow: hidden;
  }
  .hormone-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }
  .hormone-desc {
    font-size: 0.7rem;
    color: var(--text-dim);
    line-height: 1.3;
  }

  /* Resource Sparklines */
  .resource-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }
  @media (max-width: 700px) {
    .resource-row { grid-template-columns: 1fr; }
  }
  .spark-wrapper {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .spark-header {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
  .spark-value { font-size: 1.35rem; font-weight: 700; }
  .spark-sub { font-size: 0.72rem; color: var(--text-dim); }
  .sparkline { width: 100%; height: 50px; }

  /* I/O Row */
  .io-row {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
  }
  @media (max-width: 700px) {
    .io-row { grid-template-columns: 1fr; }
  }
  .io-card {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 1rem;
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .io-icon { font-size: 1.1rem; flex-shrink: 0; }
  .io-info {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }
  .io-label { font-size: 0.72rem; color: var(--text-dim); }
  .io-val { font-size: 0.9rem; font-weight: 600; }
  .io-bar {
    width: 100%;
    height: 4px;
    background: var(--bg-surface2, var(--bg));
    border-radius: 2px;
    overflow: hidden;
  }
  .io-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
  }
</style>
