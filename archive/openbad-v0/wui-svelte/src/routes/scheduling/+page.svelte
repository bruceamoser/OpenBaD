<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, put as apiPut, post as apiPost } from '$lib/api/client';
  import { resolveOnboardingRedirect } from '$lib/api/onboarding';
  import { heartbeatTick } from '$lib/stores/websocket';

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

  let statusMsg = $state('');
  let sleepSaveMsg = $state('');
  let hbSaveMsg = $state('');
  let telemetrySaveMsg = $state('');
  let hbInterval = $state(60);
  let telemetryInterval = $state(5);
  let telemetryAppliesOnRestart = $state(true);
  let onboardingHint = $derived($page.url.searchParams.get('onboarding') ?? '');
  let sleepConfig = $state<SleepConfig>({
    sleep_window_start: '02:00',
    sleep_window_duration_hours: 3,
    idle_timeout_minutes: 15,
    allow_daytime_naps: true,
    enabled: true,
  });
  let nextScheduledConsolidation = $state<string | null>(null);
  let lastConsolidationSummary = $state<Record<string, unknown> | null>(null);

  async function loadHeartbeatConfig(): Promise<void> {
    try {
      const res = await apiGet<{ interval_seconds: number }>('/api/heartbeat/config');
      hbInterval = res.interval_seconds ?? 60;
    } catch { /* use default */ }
  }

  async function saveHeartbeatConfig(): Promise<void> {
    try {
      await apiPut<{ interval_seconds: number }>('/api/heartbeat/config', { interval_seconds: hbInterval });
      hbSaveMsg = 'Saved';
      setTimeout(() => { hbSaveMsg = ''; }, 2000);
    } catch (e) { hbSaveMsg = `Save failed: ${e}`; }
  }

  async function loadTelemetryConfig(): Promise<void> {
    try {
      const res = await apiGet<{ interval_seconds: number; applies_on_restart?: boolean }>('/api/telemetry/config');
      telemetryInterval = Number(res.interval_seconds ?? 5);
      telemetryAppliesOnRestart = res.applies_on_restart !== false;
    } catch { /* use default */ }
  }

  async function saveTelemetryConfig(): Promise<void> {
    try {
      const payload = { interval_seconds: telemetryInterval };
      const res = await apiPut<{ interval_seconds: number; applies_on_restart?: boolean }>('/api/telemetry/config', payload);
      telemetryInterval = Number(res.interval_seconds ?? telemetryInterval);
      telemetryAppliesOnRestart = res.applies_on_restart !== false;
      telemetrySaveMsg = telemetryAppliesOnRestart ? 'Saved (applies on daemon restart)' : 'Saved';
      setTimeout(() => { telemetrySaveMsg = ''; }, 2500);
    } catch (e) { telemetrySaveMsg = `Save failed: ${e}`; }
  }

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
    } catch (e) { sleepSaveMsg = `Sleep settings unavailable: ${e}`; }
  }

  async function saveSleepConfig(): Promise<void> {
    try {
      const res = await apiPut<SleepConfigResponse>('/api/sleep/config', { sleep: sleepConfig });
      sleepConfig = res.sleep;
      nextScheduledConsolidation = res.next_scheduled_consolidation;
      lastConsolidationSummary = res.last_consolidation_summary;
      sleepSaveMsg = 'Sleep settings saved';

      if (onboardingHint === 'sleep') {
        const redirectTo = await resolveOnboardingRedirect(apiGet);
        const currentRoute = `${$page.url.pathname}${$page.url.search}`;
        if (redirectTo && redirectTo !== currentRoute) {
          await goto(redirectTo, { replaceState: true });
        }
      }
    } catch (e) { sleepSaveMsg = `Save failed: ${e}`; }
  }

  function formatIso(ts: string | null): string {
    if (!ts) return 'Not scheduled';
    const parsed = new Date(ts);
    if (Number.isNaN(parsed.getTime())) return ts;
    return parsed.toLocaleString();
  }

  async function triggerSleep(): Promise<void> {
    try { await apiPost('/api/sleep/trigger'); statusMsg = 'Sleep triggered'; }
    catch (e) { statusMsg = `Failed: ${e}`; }
  }
  async function triggerWake(): Promise<void> {
    try { await apiPost('/api/sleep/wake'); statusMsg = 'Wake triggered'; }
    catch (e) { statusMsg = `Failed: ${e}`; }
  }

  onMount(() => {
    loadSleepConfig();
    loadHeartbeatConfig();
    loadTelemetryConfig();
  });
</script>

<div class="page-header">
  <h2>Scheduling</h2>
  <p>Sleep cycles, heartbeat rhythm, and telemetry sample rates</p>
</div>

{#if onboardingHint === 'sleep'}
  <div class="onboarding-banner">
    Adjust the sleep schedule here to complete first-run setup before returning to chat.
  </div>
{/if}

<div class="grid">
  <!-- Sleep Schedule -->
  <div class="full-width">
    <Card label="Sleep Schedule">
      <div class="config-section">
        <p class="desc">Configure when the agent enters deep-sleep consolidation and how idle timeouts trigger naps.</p>
        <div class="config-grid three-col">
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
        <div class="toggles">
          <label class="toggle-label">
            <input type="checkbox" bind:checked={sleepConfig.allow_daytime_naps} />
            Allow daytime naps when idle
          </label>
          <label class="toggle-label">
            <input type="checkbox" bind:checked={sleepConfig.enabled} />
            Enable scheduler
          </label>
        </div>
        <div class="metadata">
          <div><strong>Next consolidation:</strong> {formatIso(nextScheduledConsolidation)}</div>
          <div>
            <strong>Last summary:</strong>
            {#if lastConsolidationSummary}
              {JSON.stringify(lastConsolidationSummary)}
            {:else}
              none yet
            {/if}
          </div>
        </div>
        <div class="actions">
          <button class="secondary" onclick={saveSleepConfig}>Save Settings</button>
          <button onclick={triggerSleep}>😴 Sleep Now</button>
          <button class="secondary" onclick={triggerWake}>☀️ Wake</button>
        </div>
        {#if sleepSaveMsg}<p class="status-msg">{sleepSaveMsg}</p>{/if}
        {#if statusMsg}<p class="status-msg">{statusMsg}</p>{/if}
      </div>
    </Card>
  </div>

  <!-- Heartbeat -->
  <Card label="Heartbeat">
    <div class="config-section">
      <p class="desc">How often the scheduler runs a heartbeat cycle and checks for pending tasks.</p>
      {#if $heartbeatTick}
        <div class="hb-last">
          <span class="hb-dot">●</span>
          Last tick: <strong>{new Date($heartbeatTick.ts).toLocaleTimeString()}</strong>
        </div>
      {:else}
        <div class="hb-last desc">No heartbeat received yet</div>
      {/if}
      <div class="config-grid one-col">
        <label>
          Interval (seconds)
          <input type="number" min="5" max="3600" step="1" bind:value={hbInterval} />
        </label>
      </div>
      <input class="range-slider" type="range" min="5" max="600" step="5" bind:value={hbInterval} />
      <div class="actions">
        <button class="secondary" onclick={saveHeartbeatConfig}>Save Interval</button>
        {#if hbSaveMsg}<span class="status-msg">{hbSaveMsg}</span>{/if}
      </div>
    </div>
  </Card>

  <!-- Hardware Telemetry -->
  <Card label="Hardware Telemetry">
    <div class="config-section">
      <p class="desc">How often CPU, memory, disk, and network samples are collected and published.</p>
      <div class="config-grid one-col">
        <label>
          Interval (seconds)
          <input type="number" min="1" max="300" step="1" bind:value={telemetryInterval} />
        </label>
      </div>
      <input class="range-slider" type="range" min="1" max="60" step="1" bind:value={telemetryInterval} />
      {#if telemetryAppliesOnRestart}
        <p class="desc">Changes apply after restarting the OpenBaD daemon service.</p>
      {/if}
      <div class="actions">
        <button class="secondary" onclick={saveTelemetryConfig}>Save Telemetry Interval</button>
        {#if telemetrySaveMsg}<span class="status-msg">{telemetrySaveMsg}</span>{/if}
      </div>
    </div>
  </Card>
</div>

<style>
  .onboarding-banner {
    margin-bottom: 1rem;
    padding: 0.75rem 1rem;
    border: 1px solid color-mix(in srgb, var(--teal) 45%, var(--border));
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--teal) 12%, var(--bg-surface1));
    color: var(--text-sub);
    font-size: 0.9rem;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  .full-width { grid-column: 1 / -1; }
  @media (max-width: 900px) {
    .grid { grid-template-columns: 1fr; }
  }

  .config-section { display: flex; flex-direction: column; gap: 0.75rem; }
  .desc { margin: 0; font-size: 0.8rem; color: var(--text-dim); }

  .config-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
  }
  .config-grid.three-col { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .config-grid.one-col { grid-template-columns: 1fr; }
  @media (max-width: 900px) {
    .config-grid { grid-template-columns: 1fr; }
  }
  .config-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.8rem;
    color: var(--text-sub);
  }
  .config-grid input {
    padding: 0.4rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text);
  }

  .toggles { display: flex; flex-direction: column; gap: 0.35rem; }
  .toggle-label {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.85rem;
  }

  .metadata {
    font-size: 0.8rem;
    color: var(--text-sub);
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .actions { display: flex; gap: 0.5rem; align-items: center; }
  .status-msg { font-size: 0.85rem; color: var(--text-sub); }

  .hb-last { margin-bottom: 0.25rem; font-size: 0.9rem; }
  .hb-dot { color: var(--green); margin-right: 0.25rem; }
  .range-slider { width: 100%; margin: 0.25rem 0; }
</style>
