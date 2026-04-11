<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { post as apiPost } from '$lib/api/client';
  import {
    cpuTelemetry,
    endocrineLevels,
    fsmState,
  } from '$lib/stores/websocket';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface Transition {
    from: string;
    to: string;
    ts: string;
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  const FSM_STATES = [
    'IDLE', 'ACTIVE', 'THROTTLED', 'SLEEP', 'EMERGENCY',
  ];

  let transitions: Transition[] = $state([]);
  let cpuHistory: number[] = $state([]);
  let memHistory: number[] = $state([]);
  let statusMsg = $state('');

  // Track FSM transitions
  let prevFsm = '';

  const SPARKLINE_MAX = 300; // ~5 min at 1s interval

  // Derived values from stores
  let currentFsm = $derived($fsmState?.state ?? 'IDLE');
  let cpu = $derived($cpuTelemetry?.cpu_percent ?? 0);
  let mem = $derived($cpuTelemetry?.memory_percent ?? 0);
  let disk = $derived($cpuTelemetry?.disk_percent ?? 0);
  let netTx = $derived($cpuTelemetry?.net_tx_bytes ?? 0);
  let netRx = $derived($cpuTelemetry?.net_rx_bytes ?? 0);
  let dopamine = $derived($endocrineLevels?.dopamine ?? 0);
  let adrenaline = $derived($endocrineLevels?.adrenaline ?? 0);
  let cortisol = $derived($endocrineLevels?.cortisol ?? 0);
  let endorphin = $derived($endocrineLevels?.endorphin ?? 0);

  // ----------------------------------------------------------------
  // FSM transition tracking
  // ----------------------------------------------------------------

  $effect(() => {
    if (currentFsm && currentFsm !== prevFsm && prevFsm) {
      transitions = [
        {
          from: prevFsm,
          to: currentFsm,
          ts: new Date().toISOString(),
        },
        ...transitions,
      ].slice(0, 10);
    }
    prevFsm = currentFsm;
  });

  // ----------------------------------------------------------------
  // Sparkline history
  // ----------------------------------------------------------------

  let historyInterval: ReturnType<typeof setInterval> | undefined;

  onMount(() => {
    historyInterval = setInterval(() => {
      cpuHistory = [...cpuHistory, cpu].slice(-SPARKLINE_MAX);
      memHistory = [...memHistory, mem].slice(-SPARKLINE_MAX);
    }, 1000);
  });

  onDestroy(() => {
    if (historyInterval) clearInterval(historyInterval);
  });

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------

  function fsmColor(state: string): string {
    switch (state) {
      case 'IDLE':      return '#22c55e';
      case 'ACTIVE':    return '#3b82f6';
      case 'THROTTLED': return '#eab308';
      case 'SLEEP':     return '#8b5cf6';
      case 'EMERGENCY': return '#ef4444';
      default:          return '#666';
    }
  }

  function hormoneColor(val: number): string {
    if (val < 0.3) return '#22c55e';
    if (val < 0.7) return '#eab308';
    return '#ef4444';
  }

  function sparklinePath(
    data: number[],
    w: number,
    h: number,
  ): string {
    if (data.length < 2) return '';
    const step = w / (data.length - 1);
    return data
      .map((v, i) => {
        const x = i * step;
        const y = h - (v / 100) * h;
        return `${i === 0 ? 'M' : 'L'}${x},${y}`;
      })
      .join(' ');
  }

  function formatBytes(b: number): string {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  }

  // ----------------------------------------------------------------
  // Sleep controls
  // ----------------------------------------------------------------

  async function triggerSleep(): Promise<void> {
    try {
      await apiPost('/api/sleep/trigger');
      statusMsg = 'Sleep triggered';
    } catch (e) {
      statusMsg = `Sleep failed: ${e}`;
    }
  }

  async function triggerWake(): Promise<void> {
    try {
      await apiPost('/api/sleep/wake');
      statusMsg = 'Wake triggered';
    } catch (e) {
      statusMsg = `Wake failed: ${e}`;
    }
  }
</script>

<h2>Health Dashboard</h2>

<div class="dashboard-grid">
  <!-- FSM State -->
  <Card label="FSM State">
    <div class="fsm-badge" style="background:{fsmColor(currentFsm)}">
      {currentFsm}
    </div>
    {#if transitions.length > 0}
      <h4>Transitions</h4>
      <ul class="transition-log">
        {#each transitions as t}
          <li>
            <span class="ts">{t.ts}</span>
            {t.from} → {t.to}
          </li>
        {/each}
      </ul>
    {/if}
  </Card>

  <!-- Endocrine Gauges -->
  <Card label="Endocrine Levels">
    <div class="gauges">
      {#each [
        { name: 'Dopamine', val: dopamine },
        { name: 'Adrenaline', val: adrenaline },
        { name: 'Cortisol', val: cortisol },
        { name: 'Endorphin', val: endorphin },
      ] as h}
        <div class="gauge">
          <label>{h.name}</label>
          <div class="gauge-bar-bg">
            <div
              class="gauge-bar-fill"
              style="width:{h.val * 100}%; background:{hormoneColor(h.val)}"
            ></div>
          </div>
          <span class="gauge-val">{(h.val * 100).toFixed(0)}%</span>
        </div>
      {/each}
    </div>
  </Card>

  <!-- CPU Sparkline -->
  <Card label="CPU (5 min)">
    <svg class="sparkline" viewBox="0 0 300 60" preserveAspectRatio="none">
      <path d={sparklinePath(cpuHistory, 300, 60)} fill="none" stroke="#3b82f6" stroke-width="1.5" />
    </svg>
    <span class="metric">{cpu.toFixed(1)}%</span>
  </Card>

  <!-- Memory Sparkline -->
  <Card label="Memory (5 min)">
    <svg class="sparkline" viewBox="0 0 300 60" preserveAspectRatio="none">
      <path d={sparklinePath(memHistory, 300, 60)} fill="none" stroke="#a855f7" stroke-width="1.5" />
    </svg>
    <span class="metric">{mem.toFixed(1)}%</span>
  </Card>

  <!-- Disk / Network -->
  <Card label="Disk / Network I/O">
    <dl class="io-summary">
      <dt>Disk</dt>
      <dd>{disk.toFixed(1)}%</dd>
      <dt>Net TX</dt>
      <dd>{formatBytes(netTx)}</dd>
      <dt>Net RX</dt>
      <dd>{formatBytes(netRx)}</dd>
    </dl>
  </Card>

  <!-- Sleep Schedule -->
  <Card label="Sleep Schedule">
    <p class="muted">
      Next sleep window info sourced from sleep orchestrator.
    </p>
    <div class="sleep-controls">
      <button onclick={triggerSleep}>Sleep Now</button>
      <button onclick={triggerWake}>Wake</button>
    </div>
  </Card>
</div>

{#if statusMsg}
  <p class="status">{statusMsg}</p>
{/if}

<style>
  .dashboard-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1rem;
  }
  .fsm-badge {
    display: inline-block;
    padding: 0.4rem 1rem;
    border-radius: 6px;
    color: #000;
    font-weight: 700;
    font-size: 1.1rem;
  }
  .transition-log {
    list-style: none;
    padding: 0;
    max-height: 10rem;
    overflow-y: auto;
  }
  .transition-log li {
    font-size: 0.8rem;
    padding: 0.15rem 0;
  }
  .ts {
    opacity: 0.5;
    margin-right: 0.4rem;
    font-size: 0.7rem;
  }

  .gauges {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .gauge {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .gauge label {
    width: 6rem;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .gauge-bar-bg {
    flex: 1;
    height: 10px;
    background: #333;
    border-radius: 5px;
    overflow: hidden;
  }
  .gauge-bar-fill {
    height: 100%;
    transition: width 0.3s ease;
    border-radius: 5px;
  }
  .gauge-val {
    width: 3rem;
    text-align: right;
    font-size: 0.8rem;
  }

  .sparkline {
    width: 100%;
    height: 60px;
  }
  .metric {
    font-size: 1.1rem;
    font-weight: 600;
  }

  .io-summary {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.3rem 1rem;
  }
  .io-summary dt { font-weight: 600; }
  .io-summary dd { text-align: right; margin: 0; }

  .sleep-controls {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .sleep-controls button { padding: 0.4rem 1rem; }
  .muted { opacity: 0.5; font-size: 0.85rem; }
  .status { font-size: 0.85rem; opacity: 0.8; margin-top: 1rem; }
</style>
