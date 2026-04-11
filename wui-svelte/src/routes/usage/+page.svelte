<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet } from '$lib/api/client';

  interface UsageSummary {
    total_used: number;
    daily_used: number;
    hourly_used: number;
    daily_remaining_pct: number;
    hourly_remaining_pct: number;
    cost_per_action_avg: number;
    request_count: number;
  }

  interface UsageRow {
    provider?: string;
    model?: string;
    system?: string;
    tokens: number;
    request_count: number;
    last_timestamp?: number;
  }

  interface DailyPoint {
    day: string;
    tokens: number;
    request_count: number;
  }

  interface RecentEvent {
    timestamp: number;
    provider: string;
    model: string;
    system: string;
    request_id: string;
    session_id: string;
    tokens: number;
  }

  interface UsageResponse {
    generated_at: string;
    limits: {
      daily_ceiling: number;
      hourly_ceiling: number;
    };
    summary: UsageSummary;
    by_provider_model: UsageRow[];
    by_system: UsageRow[];
    daily_series: DailyPoint[];
    recent_events: RecentEvent[];
  }

  let usage = $state<UsageResponse | null>(null);
  let loading = $state(true);
  let error = $state('');

  const numberFmt = new Intl.NumberFormat();
  const pctFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  const timeFmt = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });

  async function loadUsage(): Promise<void> {
    loading = true;
    error = '';
    try {
      usage = await apiGet<UsageResponse>('/api/usage');
    } catch (e) {
      error = `Failed to load usage: ${e}`;
    } finally {
      loading = false;
    }
  }

  function formatNumber(value: number): string {
    return numberFmt.format(value);
  }

  function formatPercent(value: number): string {
    return `${pctFmt.format(value)}%`;
  }

  function formatTimestamp(value?: number): string {
    if (!value) return 'Never';
    return timeFmt.format(new Date(value * 1000));
  }

  function maxSeriesValue(points: DailyPoint[]): number {
    return Math.max(1, ...points.map((point) => point.tokens));
  }

  onMount(loadUsage);
</script>

<div class="page-header">
  <div>
    <h2>Usage</h2>
    <p>Long-term token accounting across providers, models, and cognitive systems.</p>
  </div>
  <button class="secondary" onclick={loadUsage} disabled={loading}>Refresh</button>
</div>

{#if error}
  <div class="error-banner">{error}</div>
{:else if loading}
  <div class="loading-state">Loading usage statistics...</div>
{:else if usage}
  <div class="summary-grid">
    <Card label="Total Tokens">
      <div class="metric-value">{formatNumber(usage.summary.total_used)}</div>
      <div class="metric-sub">{formatNumber(usage.summary.request_count)} tracked requests</div>
    </Card>
    <Card label="Daily Budget">
      <div class="metric-value">{formatNumber(usage.summary.daily_used)}</div>
      <div class="metric-sub">
        {formatPercent(usage.summary.daily_remaining_pct)} remaining of {formatNumber(usage.limits.daily_ceiling)}
      </div>
    </Card>
    <Card label="Hourly Budget">
      <div class="metric-value">{formatNumber(usage.summary.hourly_used)}</div>
      <div class="metric-sub">
        {formatPercent(usage.summary.hourly_remaining_pct)} remaining of {formatNumber(usage.limits.hourly_ceiling)}
      </div>
    </Card>
    <Card label="Average Per Request">
      <div class="metric-value">{formatNumber(usage.summary.cost_per_action_avg)}</div>
      <div class="metric-sub">tokens per request</div>
    </Card>
  </div>

  <div class="usage-grid">
    <Card label="Provider / Model">
      {#if usage.by_provider_model.length === 0}
        <p class="empty-copy">No usage recorded yet.</p>
      {:else}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Tokens</th>
                <th>Requests</th>
                <th>Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {#each usage.by_provider_model as row}
                <tr>
                  <td>{row.provider}</td>
                  <td>{row.model}</td>
                  <td>{formatNumber(row.tokens)}</td>
                  <td>{formatNumber(row.request_count)}</td>
                  <td>{formatTimestamp(row.last_timestamp)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </Card>

    <Card label="By System">
      {#if usage.by_system.length === 0}
        <p class="empty-copy">No system usage recorded yet.</p>
      {:else}
        <div class="system-list">
          {#each usage.by_system as row}
            <div class="system-row">
              <div>
                <div class="system-name">{row.system}</div>
                <div class="system-meta">{formatNumber(row.request_count)} requests</div>
              </div>
              <div class="system-tokens">{formatNumber(row.tokens)} tokens</div>
            </div>
          {/each}
        </div>
      {/if}
    </Card>
  </div>

  <div class="usage-grid">
    <Card label="Daily Trend">
      {#if usage.daily_series.length === 0}
        <p class="empty-copy">No historical usage yet.</p>
      {:else}
        <div class="trend-list">
          {#each usage.daily_series as point}
            <div class="trend-row">
              <div class="trend-meta">
                <span>{point.day}</span>
                <span>{formatNumber(point.request_count)} req</span>
              </div>
              <div class="trend-bar-bg">
                <div
                  class="trend-bar"
                  style={`width:${(point.tokens / maxSeriesValue(usage.daily_series)) * 100}%`}
                ></div>
              </div>
              <div class="trend-value">{formatNumber(point.tokens)}</div>
            </div>
          {/each}
        </div>
      {/if}
    </Card>

    <Card label="Recent Activity">
      {#if usage.recent_events.length === 0}
        <p class="empty-copy">No recent events yet.</p>
      {:else}
        <div class="event-list">
          {#each usage.recent_events as event}
            <div class="event-row">
              <div class="event-main">
                <div class="event-title">{event.provider} / {event.model}</div>
                <div class="event-meta">{event.system} · {formatTimestamp(event.timestamp)}</div>
              </div>
              <div class="event-tokens">{formatNumber(event.tokens)}</div>
            </div>
          {/each}
        </div>
      {/if}
    </Card>
  </div>
{/if}

<style>
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .page-header p {
    margin-top: 0.25rem;
  }

  .error-banner,
  .loading-state {
    padding: 1rem 1.25rem;
    border-radius: var(--radius-md);
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    color: var(--text-sub);
  }

  .error-banner {
    border-color: rgba(243, 139, 168, 0.35);
    color: var(--red);
  }

  .summary-grid,
  .usage-grid {
    display: grid;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .summary-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .usage-grid {
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  }

  .metric-value {
    font-size: 1.9rem;
    font-weight: 700;
    line-height: 1.1;
    color: var(--text);
  }

  .metric-sub,
  .empty-copy,
  .system-meta,
  .event-meta,
  .trend-meta {
    color: var(--text-sub);
    font-size: 0.85rem;
  }

  .metric-sub {
    margin-top: 0.45rem;
  }

  .table-wrap {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th,
  td {
    text-align: left;
    padding: 0.7rem 0;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }

  th {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
  }

  td {
    color: var(--text);
    font-size: 0.9rem;
  }

  .system-list,
  .event-list,
  .trend-list {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
  }

  .system-row,
  .event-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 0.85rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
  }

  .system-name,
  .event-title {
    color: var(--text);
    font-weight: 600;
    text-transform: capitalize;
  }

  .system-tokens,
  .event-tokens,
  .trend-value {
    color: var(--blue);
    font-weight: 700;
    white-space: nowrap;
  }

  .trend-row {
    display: grid;
    grid-template-columns: 110px minmax(0, 1fr) 90px;
    gap: 0.75rem;
    align-items: center;
  }

  .trend-meta {
    display: flex;
    flex-direction: column;
  }

  .trend-bar-bg {
    height: 10px;
    border-radius: 999px;
    background: var(--bg-surface1);
    overflow: hidden;
  }

  .trend-bar {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--teal), var(--blue));
  }

  @media (max-width: 1100px) {
    .summary-grid,
    .usage-grid {
      grid-template-columns: 1fr 1fr;
    }
  }

  @media (max-width: 800px) {
    .page-header {
      flex-direction: column;
      align-items: stretch;
    }

    .summary-grid,
    .usage-grid {
      grid-template-columns: 1fr;
    }

    .trend-row {
      grid-template-columns: 1fr;
    }
  }
</style>