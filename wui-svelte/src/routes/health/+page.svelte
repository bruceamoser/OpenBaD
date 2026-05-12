<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, post as apiPost } from '$lib/api/client';
  import {
    cpuTelemetry,
    memoryTelemetry,
    diskTelemetry,
    networkTelemetry,
    fsmState,
  } from '$lib/stores/websocket';

  interface Transition { from: string; to: string; ts: string; }

  interface ActivityEvent {
    ts: string;
    level: string;
    source: string;
    message: string;
  }

  interface RecentLLMCall {
    request_id: string;
    timestamp: number;
    provider: string;
    model: string;
    system: string;
    tokens: number;
    type_label?: string;
    input_preview: string;
  }

  interface EndocrineStatus {
    levels: Record<string, number>;
    mood_tags: string[];
    subsystems: Record<string, { enabled: boolean }>;
  }

  interface MaintenanceStatus {
    running: boolean;
    last_run: string | null;
    last_result: string | null;
    last_topic: string | null;
    error: string | null;
    explanation: {
      purpose: string;
      current_topic: string;
      exploration_scope: string;
      cortisol: number;
      dopamine: number;
    };
  }

  const FSM_STATES = ['IDLE', 'ACTIVE', 'RESEARCHING', 'EXECUTING_TASK', 'DIAGNOSING', 'THROTTLED', 'SLEEP', 'EMERGENCY'];
  let transitions: Transition[] = $state([]);
  let cpuHistory: number[] = $state([]);
  let memHistory: number[] = $state([]);

  // Activity panel state
  let recentEvents: ActivityEvent[] = $state([]);
  let recentLLM: RecentLLMCall[] = $state([]);
  let endoStatus: EndocrineStatus | null = $state(null);
  let activityLoading = $state(true);
  let activityInterval: ReturnType<typeof setInterval> | null = null;

  // Maintenance crew state
  let maintenance: MaintenanceStatus | null = $state(null);
  let maintenanceLoading = $state(true);
  let maintenanceTriggering = $state(false);
  let showMaintenanceResult = $state(false);

  let prevFsm = '';
  const SPARKLINE_MAX = 300;

  let currentFsm = $derived($fsmState?.current_state?.toUpperCase() ?? 'IDLE');
  let cpu = $derived($cpuTelemetry?.usage_percent ?? 0);
  let mem = $derived($memoryTelemetry?.usage_percent ?? 0);
  let disk = $derived($diskTelemetry?.usage_percent ?? 0);
  let netTx = $derived($networkTelemetry?.bytes_sent ?? 0);
  let netRx = $derived($networkTelemetry?.bytes_recv ?? 0);

  $effect(() => {
    if (currentFsm && currentFsm !== prevFsm && prevFsm) {
      const prev = untrack(() => transitions);
      transitions = [{ from: prevFsm, to: currentFsm, ts: new Date().toLocaleTimeString() }, ...prev].slice(0, 10);
    }
    prevFsm = currentFsm;
  });

  $effect(() => {
    // Append whenever WebSocket pushes new cpu/mem values
    const c = cpu;
    const m = mem;
    const prevCpu = untrack(() => cpuHistory);
    const prevMem = untrack(() => memHistory);
    cpuHistory = [...prevCpu, c].slice(-SPARKLINE_MAX);
    memHistory = [...prevMem, m].slice(-SPARKLINE_MAX);
  });

  function fsmColor(state: string): string {
    switch (state) {
      case 'IDLE':           return 'var(--green)';
      case 'ACTIVE':         return 'var(--blue)';
      case 'RESEARCHING':    return 'var(--teal)';
      case 'EXECUTING_TASK': return 'var(--sapphire)';
      case 'DIAGNOSING':     return 'var(--peach)';
      case 'THROTTLED':      return 'var(--yellow)';
      case 'SLEEP':          return 'var(--mauve)';
      case 'EMERGENCY':      return 'var(--red)';
      default:               return 'var(--text-dim)';
    }
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
    const highCpu = cpu > 90;
    const highMem = mem > 90;
    const emergency = currentFsm === 'EMERGENCY';
    const busy = ['RESEARCHING', 'EXECUTING_TASK', 'DIAGNOSING'].includes(currentFsm);
    if (emergency) return { label: 'Critical', color: 'var(--red)', icon: '🔴' };
    if (highCpu || highMem || currentFsm === 'THROTTLED') return { label: 'Stressed', color: 'var(--yellow)', icon: '🟡' };
    if (busy) return { label: 'Working', color: 'var(--teal)', icon: '🔵' };
    return { label: 'Healthy', color: 'var(--green)', icon: '🟢' };
  });

  async function loadActivity(): Promise<void> {
    try {
      const [evRes, llmRes, endoRes] = await Promise.all([
        apiGet<{ events: ActivityEvent[] }>('/api/events?limit=10&level=INFO'),
        apiGet<{ items: RecentLLMCall[] }>('/api/usage/requests?per_page=5'),
        apiGet<EndocrineStatus>('/api/endocrine/status'),
      ]);
      // Filter to meaningful events (not aiohttp access logs)
      recentEvents = (evRes.events ?? []).filter(
        (e) => !e.source.startsWith('aiohttp') && !e.message.includes('GET /api/')
      ).slice(0, 8);
      recentLLM = llmRes.items ?? [];
      endoStatus = endoRes;
    } catch {
      // Non-critical — activity panel is informational
    } finally {
      activityLoading = false;
    }
  }

  function relativeTime(ts: number): string {
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function relativeTimeISO(ts: string): string {
    const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function hormoneBar(val: number): string {
    return `${Math.min(100, val * 100).toFixed(0)}%`;
  }

  function hormoneColor(name: string): string {
    const map: Record<string, string> = {
      dopamine: 'var(--blue)', adrenaline: 'var(--red)',
      cortisol: 'var(--yellow)', endorphin: 'var(--green)',
    };
    return map[name] ?? 'var(--text-dim)';
  }

  function systemLabelColor(sys: string): string {
    const map: Record<string, string> = {
      chat: 'var(--blue)', task_worker: 'var(--green)', research_worker: 'var(--teal)',
      doctor: 'var(--peach)', immune: 'var(--red)', maintenance: 'var(--mauve)',
    };
    return map[sys] ?? 'var(--text-sub)';
  }

  async function loadMaintenance(): Promise<void> {
    try {
      maintenance = await apiGet<MaintenanceStatus>('/api/maintenance/status');
    } catch {
      // non-critical
    } finally {
      maintenanceLoading = false;
    }
  }

  async function triggerMaintenance(): Promise<void> {
    maintenanceTriggering = true;
    try {
      await apiPost('/api/maintenance/run');
      // Poll status until done
      const poll = setInterval(async () => {
        await loadMaintenance();
        if (maintenance && !maintenance.running) {
          clearInterval(poll);
          maintenanceTriggering = false;
          showMaintenanceResult = true;
        }
      }, 3000);
    } catch {
      maintenanceTriggering = false;
    }
  }

  onMount(() => {
    loadActivity();
    loadMaintenance();
    activityInterval = setInterval(loadActivity, 15000);
    return () => { if (activityInterval) clearInterval(activityInterval); };
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

  <!-- Activity Panel -->
  <div class="activity-grid">
    <!-- Endocrine Summary -->
    <Card label="Hormones">
      {#if endoStatus}
        <div class="hormone-list">
          {#each Object.entries(endoStatus.levels) as [name, val]}
            <div class="hormone-row">
              <span class="hormone-name">{name}</span>
              <div class="hormone-bar-bg">
                <div class="hormone-bar-fill" style="width:{hormoneBar(val)}; background:{hormoneColor(name)}"></div>
              </div>
              <span class="hormone-val">{(val * 100).toFixed(0)}%</span>
            </div>
          {/each}
        </div>
        {#if endoStatus.mood_tags.length > 0}
          <div class="mood-tags">
            {#each endoStatus.mood_tags as tag}
              <span class="mood-tag">{tag}</span>
            {/each}
          </div>
        {/if}
      {:else}
        <p class="muted">Loading…</p>
      {/if}
    </Card>

    <!-- Recent LLM Activity -->
    <Card label="Recent LLM Calls">
      {#if activityLoading}
        <p class="muted">Loading…</p>
      {:else if recentLLM.length === 0}
        <p class="muted">No recent LLM activity</p>
      {:else}
        <div class="llm-list">
          {#each recentLLM as call}
            <div class="llm-row">
              <div class="llm-meta">
                <span class="llm-system" style="color:{systemLabelColor(call.system)}">{call.type_label || call.system}</span>
                <span class="llm-time">{relativeTime(call.timestamp)}</span>
              </div>
              <div class="llm-preview">{call.input_preview.slice(0, 100)}{call.input_preview.length > 100 ? '…' : ''}</div>
              {#if call.tokens > 0}
                <span class="llm-tokens">{call.tokens} tok</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </Card>
  </div>

  <!-- Recent System Events -->
  <Card label="Recent Activity">
    {#if activityLoading}
      <p class="muted">Loading…</p>
    {:else if recentEvents.length === 0}
      <p class="muted">No recent events</p>
    {:else}
      <div class="event-table">
        {#each recentEvents as ev}
          <div class="ev-row">
            <span class="ev-time">{relativeTimeISO(ev.ts)}</span>
            <span class="ev-source">{ev.source.replace('openbad.', '')}</span>
            <span class="ev-msg">{ev.message.slice(0, 120)}{ev.message.length > 120 ? '…' : ''}</span>
          </div>
        {/each}
      </div>
    {/if}
  </Card>

  <!-- Maintenance Crew Manual Trigger -->
  <Card label="Maintenance Crew">
    {#if maintenanceLoading}
      <p class="muted">Loading…</p>
    {:else if maintenance}
      <div class="maintenance-panel">
        <p class="maintenance-purpose">{maintenance.explanation.purpose}</p>

        <div class="maintenance-info">
          <div class="maint-row">
            <span class="maint-label">Exploration Scope</span>
            <span class="maint-val">{maintenance.explanation.exploration_scope}</span>
          </div>
          <div class="maint-row">
            <span class="maint-label">Cortisol</span>
            <span class="maint-val">{(maintenance.explanation.cortisol * 100).toFixed(0)}%</span>
          </div>
          <div class="maint-row">
            <span class="maint-label">Dopamine</span>
            <span class="maint-val">{(maintenance.explanation.dopamine * 100).toFixed(0)}%</span>
          </div>
          {#if maintenance.last_run}
            <div class="maint-row">
              <span class="maint-label">Last Run</span>
              <span class="maint-val">{relativeTimeISO(maintenance.last_run)}</span>
            </div>
          {/if}
        </div>

        <details class="maint-topic-details">
          <summary>What it will explore</summary>
          <pre class="maint-topic">{maintenance.explanation.current_topic}</pre>
        </details>

        {#if maintenance.running || maintenanceTriggering}
          <div class="maint-running">
            <span class="maint-spinner">⟳</span> Maintenance crew is running…
          </div>
        {:else}
          <button class="maint-btn" onclick={triggerMaintenance}>
            Run Maintenance Crew
          </button>
        {/if}

        {#if maintenance.error}
          <div class="maint-error">Error: {maintenance.error}</div>
        {/if}

        {#if showMaintenanceResult && maintenance.last_result}
          <details class="maint-result-details" open>
            <summary>Last Result</summary>
            <pre class="maint-result">{maintenance.last_result}</pre>
          </details>
        {/if}
      </div>
    {:else}
      <p class="muted">Maintenance status unavailable</p>
    {/if}
  </Card>

  <!-- Doctor Notes -->
  <Card label="Doctor Notes">
    {#if !endoStatus}
      <p class="muted">Loading…</p>
    {:else if endoStatus.doctor_notes.length === 0}
      <p class="muted">No doctor visits recorded</p>
    {:else}
      <div class="doctor-notes">
        {#each endoStatus.doctor_notes as note}
          <div class="doctor-note" class:revelation={note.doctor_revelation}>
            <div class="doctor-note-header">
              <span class="doctor-note-time">{relativeTime(note.ts)}</span>
              <span class="doctor-note-source">{note.source}</span>
              {#if note.provider || note.model}
                <span class="doctor-note-provider">{note.provider ?? ''}/{note.model ?? ''}</span>
              {/if}
              {#if note.doctor_revelation}
                <span class="doctor-revelation-badge">revelation</span>
              {/if}
            </div>
            {#if note.summary}
              <div class="doctor-note-summary">{note.summary}</div>
            {:else}
              <div class="doctor-note-summary muted">(no summary)</div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </Card>

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

  /* Activity Panel */
  .activity-grid {
    display: grid;
    grid-template-columns: 1fr 2fr;
    gap: 1rem;
  }
  @media (max-width: 800px) {
    .activity-grid { grid-template-columns: 1fr; }
  }
  .hormone-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .hormone-row { display: flex; align-items: center; gap: 0.5rem; }
  .hormone-name { font-size: 0.78rem; font-weight: 600; min-width: 5.5rem; text-transform: capitalize; }
  .hormone-bar-bg {
    flex: 1; height: 6px; background: var(--bg-surface2, var(--bg));
    border-radius: 3px; overflow: hidden;
  }
  .hormone-bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s ease; }
  .hormone-val { font-size: 0.75rem; font-variant-numeric: tabular-nums; min-width: 2.5rem; text-align: right; color: var(--text-dim); }
  .mood-tags { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-top: 0.6rem; }
  .mood-tag {
    font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.5rem;
    background: var(--bg-surface2, var(--bg)); border-radius: 999px;
    color: var(--text-sub); text-transform: capitalize;
  }

  .llm-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .llm-row {
    padding: 0.45rem 0.6rem; background: var(--bg-surface1);
    border-radius: var(--radius-sm); border-left: 3px solid var(--border);
  }
  .llm-meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.2rem; }
  .llm-system { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; }
  .llm-time { font-size: 0.7rem; color: var(--text-dim); }
  .llm-preview { font-size: 0.78rem; color: var(--text-sub); line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .llm-tokens { font-size: 0.7rem; color: var(--text-dim); margin-top: 0.15rem; display: inline-block; }

  .event-table { display: flex; flex-direction: column; gap: 2px; }
  .ev-row {
    display: grid; grid-template-columns: 5rem 10rem 1fr;
    gap: 0.5rem; align-items: center;
    padding: 0.35rem 0.6rem; font-size: 0.78rem;
    background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  @media (max-width: 700px) {
    .ev-row { grid-template-columns: 4rem 1fr; }
    .ev-source { display: none; }
  }
  .ev-time { color: var(--text-dim); font-variant-numeric: tabular-nums; }
  .ev-source { color: var(--teal); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ev-msg { color: var(--text-sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--text-dim); text-align: center; padding: 1rem; font-size: 0.82rem; }

  /* Maintenance Crew Panel */
  .maintenance-panel { display: flex; flex-direction: column; gap: 0.75rem; }
  .maintenance-purpose { font-size: 0.82rem; color: var(--text-sub); line-height: 1.4; margin: 0; }
  .maintenance-info { display: flex; flex-direction: column; gap: 0.3rem; }
  .maint-row { display: flex; justify-content: space-between; align-items: center; padding: 0.25rem 0.5rem; background: var(--bg-surface1); border-radius: var(--radius-sm); }
  .maint-label { font-size: 0.78rem; color: var(--text-dim); }
  .maint-val { font-size: 0.78rem; font-weight: 600; color: var(--text); }
  .maint-topic-details { font-size: 0.78rem; color: var(--text-sub); }
  .maint-topic-details summary { cursor: pointer; color: var(--mauve); font-weight: 600; }
  .maint-topic { font-size: 0.75rem; white-space: pre-wrap; word-break: break-word; background: var(--bg-surface1); padding: 0.5rem; border-radius: var(--radius-sm); max-height: 10rem; overflow-y: auto; margin: 0.4rem 0 0; }
  .maint-btn {
    padding: 0.6rem 1.2rem; border: none; border-radius: var(--radius-sm);
    background: var(--mauve); color: var(--bg-base); font-weight: 700;
    font-size: 0.85rem; cursor: pointer; transition: opacity 0.15s;
    align-self: flex-start;
  }
  .maint-btn:hover { opacity: 0.85; }
  .maint-running { display: flex; align-items: center; gap: 0.5rem; font-size: 0.82rem; color: var(--mauve); }
  .maint-spinner { animation: spin 1s linear infinite; display: inline-block; }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  .maint-error { font-size: 0.78rem; color: var(--red); background: rgba(243,139,168,0.1); padding: 0.4rem 0.6rem; border-radius: var(--radius-sm); }
  .maint-result-details { font-size: 0.78rem; color: var(--text-sub); }
  .maint-result-details summary { cursor: pointer; color: var(--green); font-weight: 600; }
  .maint-result { font-size: 0.75rem; white-space: pre-wrap; word-break: break-word; background: var(--bg-surface1); padding: 0.5rem; border-radius: var(--radius-sm); max-height: 15rem; overflow-y: auto; margin: 0.4rem 0 0; }

  /* Doctor Notes Panel */
  .doctor-notes { display: flex; flex-direction: column; gap: 0.5rem; }
  .doctor-note {
    background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius-sm); padding: 0.55rem 0.75rem;
  }
  .doctor-note.revelation { border-left: 3px solid var(--yellow); }
  .doctor-note-header {
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 0.25rem; font-size: 0.72rem; color: var(--text-dim);
  }
  .doctor-note-time { font-variant-numeric: tabular-nums; }
  .doctor-note-source { font-weight: 600; color: var(--text); }
  .doctor-note-provider { font-style: italic; }
  .doctor-revelation-badge {
    font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
    padding: 0.1rem 0.4rem; border-radius: 999px;
    background: color-mix(in srgb, var(--yellow) 20%, transparent);
    color: var(--yellow);
  }
  .doctor-note-summary { font-size: 0.82rem; line-height: 1.4; color: var(--text-sub); }
</style>
