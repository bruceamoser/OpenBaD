<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet } from '$lib/api/client';

  /* ── Types ─────────────────────────────────────────────── */

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
    session_id?: string;
    session_type?: string;
    type_label?: string;
    label?: string;
    tokens: number;
    request_count: number;
    session_count?: number;
    last_timestamp?: number;
  }

  interface DailyPoint {
    day: string;
    tokens: number;
    request_count: number;
  }

  interface UsageResponse {
    generated_at: string;
    limits: { daily_ceiling: number; hourly_ceiling: number };
    summary: UsageSummary;
    by_provider_model: UsageRow[];
    by_system: UsageRow[];
    by_session_type: UsageRow[];
    daily_series: DailyPoint[];
  }

  interface RequestListItem {
    request_id: string;
    timestamp: number;
    provider: string;
    model: string;
    system: string;
    session_id: string;
    tokens: number;
    tool_count: number;
    tool_names: string[];
    input_preview: string;
    output_preview: string;
    type_label?: string;
    label?: string;
  }

  interface RequestsPage {
    items: RequestListItem[];
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
  }

  interface ToolCall {
    name: string;
    args: Record<string, unknown>;
    result: string;
  }

  interface RequestDetail {
    request_id: string;
    timestamp: number;
    provider: string;
    model: string;
    system: string;
    session_id: string;
    tokens: number;
    input_text: string;
    output_text: string;
    tools: ToolCall[];
    type_label?: string;
    label?: string;
  }

  /* ── State ─────────────────────────────────────────────── */

  let activeTab: 'breakdown' | 'requests' = $state('requests');
  let usage: UsageResponse | null = $state(null);
  let requestsPage: RequestsPage | null = $state(null);
  let selectedDetail: RequestDetail | null = $state(null);
  let expandedTools: Set<number> = $state(new Set());
  let loading = $state(true);
  let requestsLoading = $state(false);
  let detailLoading = $state(false);
  let error = $state('');
  let currentPage = $state(1);
  const perPage = 10;

  /* ── Formatters ────────────────────────────────────────── */

  const numFmt = new Intl.NumberFormat();
  const pctFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  const timeFmt = new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  const fullTimeFmt = new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
  });

  function fmt(n: number): string { return numFmt.format(n); }
  function pct(n: number): string { return `${pctFmt.format(n)}%`; }
  function ts(v?: number): string { return v ? timeFmt.format(new Date(v * 1000)) : 'Never'; }
  function fullTs(v: number): string { return fullTimeFmt.format(new Date(v * 1000)); }

  function maxSeries(pts: DailyPoint[]): number {
    return Math.max(1, ...pts.map(p => p.tokens));
  }

  function systemColor(sys: string): string {
    const map: Record<string, string> = {
      chat: 'var(--blue)', task_worker: 'var(--green)', research_worker: 'var(--teal)',
      doctor: 'var(--yellow)', immune: 'var(--red)',
    };
    return map[sys] ?? 'var(--text-dim)';
  }

  /* ── Data loading ──────────────────────────────────────── */

  async function loadUsage(): Promise<void> {
    loading = true;
    error = '';
    try {
      usage = await apiGet<UsageResponse>('/api/usage');
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function loadRequests(page: number = 1): Promise<void> {
    requestsLoading = true;
    error = '';
    currentPage = page;
    try {
      requestsPage = await apiGet<RequestsPage>(`/api/usage/requests?page=${page}&per_page=${perPage}`);
    } catch (e) {
      error = String(e);
    } finally {
      requestsLoading = false;
    }
  }

  async function loadDetail(requestId: string): Promise<void> {
    detailLoading = true;
    expandedTools = new Set();
    try {
      selectedDetail = await apiGet<RequestDetail>(`/api/usage/requests/${requestId}`);
    } catch (e) {
      error = String(e);
      selectedDetail = null;
    } finally {
      detailLoading = false;
    }
  }

  function closeDetail(): void {
    selectedDetail = null;
  }

  function toggleTool(idx: number): void {
    const next = new Set(expandedTools);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    expandedTools = next;
  }

  onMount(() => {
    loadUsage();
    loadRequests(1);
  });
</script>

<div class="page-header">
  <div>
    <h2>Usage</h2>
    <p class="muted">Token accounting and request history.</p>
  </div>
  <button class="secondary" onclick={() => { loadUsage(); loadRequests(currentPage); }} disabled={loading}>Refresh</button>
</div>

{#if error}
  <div class="error-banner">{error}</div>
{/if}

<div class="tab-bar">
  <button class="tab" class:active={activeTab === 'requests'} onclick={() => { activeTab = 'requests'; if (!requestsPage) loadRequests(1); }}>
    Request History
  </button>
  <button class="tab" class:active={activeTab === 'breakdown'} onclick={() => { activeTab = 'breakdown'; if (!usage) loadUsage(); }}>
    Breakdown
  </button>
</div>

<!-- ═══════════════ REQUESTS TAB ═══════════════ -->
{#if activeTab === 'requests'}

  {#if selectedDetail}
    <!-- Detail view -->
    <div class="detail-panel">
      <div class="detail-header">
        <button class="btn-sm ghost" onclick={closeDetail}>← Back to list</button>
        <span class="detail-id">{selectedDetail.request_id}</span>
      </div>

      <div class="detail-meta-grid">
        <div class="meta-item">
          <span class="meta-label">Time</span>
          <span class="meta-value">{fullTs(selectedDetail.timestamp)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">System</span>
          <span class="system-badge" style="--sys-color: {systemColor(selectedDetail.system)}">{selectedDetail.type_label || selectedDetail.system}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Provider / Model</span>
          <span class="meta-value">{selectedDetail.provider} / {selectedDetail.model}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Tokens</span>
          <span class="meta-value token-value">{fmt(selectedDetail.tokens)}</span>
        </div>
      </div>

      <section class="detail-section">
        <h4>Input</h4>
        <pre class="detail-content">{selectedDetail.input_text || '(no input recorded)'}</pre>
      </section>

      {#if selectedDetail.tools.length > 0}
        <section class="detail-section">
          <h4>Tool Calls ({selectedDetail.tools.length})</h4>
          <div class="tools-list">
            {#each selectedDetail.tools as tool, i}
              <div class="tool-item">
                <button class="tool-header" onclick={() => toggleTool(i)}>
                  <span class="tool-name">{tool.name}</span>
                  <span class="tool-toggle">{expandedTools.has(i) ? '▾' : '▸'}</span>
                </button>
                {#if expandedTools.has(i)}
                  <div class="tool-body">
                    {#if tool.args && Object.keys(tool.args).length > 0}
                      <div class="tool-sub">
                        <span class="tool-sub-label">Arguments</span>
                        <pre class="tool-pre">{JSON.stringify(tool.args, null, 2)}</pre>
                      </div>
                    {/if}
                    {#if tool.result}
                      <div class="tool-sub">
                        <span class="tool-sub-label">Result</span>
                        <pre class="tool-pre">{tool.result}</pre>
                      </div>
                    {/if}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <section class="detail-section">
        <h4>Output</h4>
        <pre class="detail-content">{selectedDetail.output_text || '(no output recorded)'}</pre>
      </section>
    </div>

  {:else}
    <!-- Request list -->
    {#if requestsLoading && !requestsPage}
      <p class="muted">Loading requests…</p>
    {:else if requestsPage && requestsPage.items.length === 0}
      <div class="empty-state">
        <p>No request details recorded yet.</p>
        <p class="muted">Details will appear here as the system processes chat, task, and research requests.</p>
      </div>
    {:else if requestsPage}
      <div class="requests-list" class:loading-overlay={requestsLoading}>
        {#each requestsPage.items as item}
          <button class="request-row" onclick={() => loadDetail(item.request_id)}>
            <div class="req-main">
              <div class="req-top">
                <span class="system-badge" style="--sys-color: {systemColor(item.system)}">{item.type_label || item.system}</span>
                <span class="req-model">{item.provider}/{item.model}</span>
                <span class="req-time">{ts(item.timestamp)}</span>
              </div>
              <div class="req-preview">{item.input_preview || '(no input)'}</div>
              {#if item.tool_count > 0}
                <div class="req-tools">
                  {#each item.tool_names.slice(0, 5) as tn}
                    <span class="tool-chip">{tn}</span>
                  {/each}
                  {#if item.tool_names.length > 5}
                    <span class="tool-chip more">+{item.tool_names.length - 5}</span>
                  {/if}
                </div>
              {/if}
            </div>
            <div class="req-tokens">{fmt(item.tokens)}</div>
          </button>
        {/each}
      </div>

      <!-- Pagination -->
      {#if requestsPage.total_pages > 1}
        <div class="pagination">
          <button class="btn-sm ghost" disabled={currentPage <= 1 || requestsLoading} onclick={() => loadRequests(currentPage - 1)}>
            ← Prev
          </button>
          <span class="page-info">Page {requestsPage.page} of {requestsPage.total_pages} ({fmt(requestsPage.total)} total)</span>
          <button class="btn-sm ghost" disabled={currentPage >= requestsPage.total_pages || requestsLoading} onclick={() => loadRequests(currentPage + 1)}>
            Next →
          </button>
        </div>
      {/if}
    {/if}
  {/if}

<!-- ═══════════════ BREAKDOWN TAB ═══════════════ -->
{:else if activeTab === 'breakdown'}

  {#if loading && !usage}
    <p class="muted">Loading usage data…</p>
  {:else if usage}
    <div class="summary-grid">
      <Card label="Total Tokens">
        <div class="metric-value">{fmt(usage.summary.total_used)}</div>
        <div class="metric-sub">{fmt(usage.summary.request_count)} tracked requests</div>
      </Card>
      <Card label="Daily Budget">
        <div class="metric-value">{fmt(usage.summary.daily_used)}</div>
        <div class="metric-sub">{pct(usage.summary.daily_remaining_pct)} remaining of {fmt(usage.limits.daily_ceiling)}</div>
      </Card>
      <Card label="Hourly Budget">
        <div class="metric-value">{fmt(usage.summary.hourly_used)}</div>
        <div class="metric-sub">{pct(usage.summary.hourly_remaining_pct)} remaining of {fmt(usage.limits.hourly_ceiling)}</div>
      </Card>
      <Card label="Average Per Request">
        <div class="metric-value">{fmt(usage.summary.cost_per_action_avg)}</div>
        <div class="metric-sub">tokens per request</div>
      </Card>
    </div>

    <div class="usage-grid">
      <Card label="Provider / Model">
        {#if usage.by_provider_model.length === 0}
          <p class="muted">No usage recorded yet.</p>
        {:else}
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>Provider</th><th>Model</th><th>Tokens</th><th>Requests</th><th>Last Seen</th></tr>
              </thead>
              <tbody>
                {#each usage.by_provider_model as row}
                  <tr>
                    <td>{row.provider}</td>
                    <td class="model-cell">{row.model}</td>
                    <td class="num-cell">{fmt(row.tokens)}</td>
                    <td class="num-cell">{fmt(row.request_count)}</td>
                    <td>{ts(row.last_timestamp)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>

      <Card label="By System">
        {#if usage.by_system.length === 0}
          <p class="muted">No system usage recorded yet.</p>
        {:else}
          <div class="system-list">
            {#each usage.by_system as row}
              <div class="system-row">
                <div>
                  <div class="system-name">{row.system}</div>
                  <div class="system-meta">{fmt(row.request_count)} requests</div>
                </div>
                <div class="system-tokens">{fmt(row.tokens)} tokens</div>
              </div>
            {/each}
          </div>
        {/if}
      </Card>
    </div>

    <Card label="Daily Trend">
      {#if usage.daily_series.length === 0}
        <p class="muted">No historical usage yet.</p>
      {:else}
        <div class="trend-list">
          {#each usage.daily_series as pt}
            <div class="trend-row">
              <div class="trend-meta">
                <span>{pt.day}</span>
                <span>{fmt(pt.request_count)} req</span>
              </div>
              <div class="trend-bar-bg">
                <div class="trend-bar" style="width:{(pt.tokens / maxSeries(usage.daily_series)) * 100}%"></div>
              </div>
              <div class="trend-value">{fmt(pt.tokens)}</div>
            </div>
          {/each}
        </div>
      {/if}
    </Card>
  {/if}
{/if}

<style>
  /* ── Layout ─────────────────── */
  .page-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 1rem; margin-bottom: 1rem;
  }
  .page-header p { margin-top: 0.25rem; }
  .muted { color: var(--text-dim); font-size: 0.85rem; }

  .error-banner {
    padding: 0.8rem 1rem; border-radius: var(--radius-md);
    background: var(--bg-surface1); border: 1px solid rgba(243, 139, 168, 0.35);
    color: var(--red); margin-bottom: 1rem;
  }

  /* ── Tabs ────────────────────── */
  .tab-bar {
    display: flex; gap: 0.25rem; margin-bottom: 1.25rem;
    border-bottom: 1px solid var(--border); padding-bottom: 0;
  }
  .tab {
    padding: 0.5rem 1rem; font-size: 0.85rem; font-weight: 600;
    background: none; border: none; border-bottom: 2px solid transparent;
    color: var(--text-dim); cursor: pointer; transition: color 0.15s, border-color 0.15s;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ── Request list ───────────── */
  .requests-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .loading-overlay { opacity: 0.5; pointer-events: none; }

  .request-row {
    display: flex; justify-content: space-between; align-items: center;
    gap: 1rem; padding: 0.85rem 1rem; border-radius: var(--radius-md);
    background: var(--bg-surface1); border: 1px solid var(--border);
    cursor: pointer; text-align: left; width: 100%;
    transition: border-color 0.15s, background 0.15s;
  }
  .request-row:hover {
    border-color: var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--bg-surface1));
  }

  .req-main { flex: 1; min-width: 0; }
  .req-top { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.3rem; }
  .req-model { font-size: 0.78rem; color: var(--text-dim); }
  .req-time { font-size: 0.78rem; color: var(--text-dim); margin-left: auto; }
  .req-preview {
    font-size: 0.82rem; color: var(--text-sub); white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; max-width: 100%;
  }
  .req-tools { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.35rem; }
  .req-tokens { font-size: 0.9rem; font-weight: 700; color: var(--blue); white-space: nowrap; }

  .system-badge {
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
    font-size: 0.72rem; font-weight: 700; text-transform: capitalize;
    background: color-mix(in srgb, var(--sys-color) 15%, transparent);
    color: var(--sys-color); white-space: nowrap;
  }

  .tool-chip {
    display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px;
    font-size: 0.7rem; background: var(--bg-surface2); color: var(--text-dim);
    font-family: var(--font-mono, monospace);
  }
  .tool-chip.more { font-style: italic; }

  /* ── Pagination ─────────────── */
  .pagination {
    display: flex; justify-content: center; align-items: center;
    gap: 1rem; margin-top: 1rem; padding: 0.5rem 0;
  }
  .page-info { font-size: 0.82rem; color: var(--text-dim); }

  .btn-sm {
    padding: 0.35rem 0.7rem; font-size: 0.82rem; border-radius: var(--radius-sm);
    cursor: pointer; border: none; font-weight: 600;
  }
  .btn-sm:disabled { opacity: 0.4; cursor: not-allowed; }
  .ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .ghost:hover:not(:disabled) { background: var(--bg-surface1); }

  /* ── Detail view ────────────── */
  .detail-panel { max-width: 900px; }
  .detail-header {
    display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;
  }
  .detail-id { font-size: 0.78rem; color: var(--text-dim); font-family: var(--font-mono, monospace); }

  .detail-meta-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem; margin-bottom: 1.25rem; padding: 0.85rem 1rem;
    background: var(--bg-surface1); border-radius: var(--radius-md);
    border: 1px solid var(--border);
  }
  .meta-item { display: flex; flex-direction: column; gap: 0.15rem; }
  .meta-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); }
  .meta-value { font-size: 0.9rem; color: var(--text); }
  .token-value { font-weight: 700; color: var(--blue); }

  .detail-section { margin-bottom: 1.25rem; }
  .detail-section h4 {
    font-size: 0.85rem; font-weight: 700; color: var(--text);
    margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.03em;
  }

  .detail-content {
    padding: 0.85rem 1rem; background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius-md); font-size: 0.82rem; color: var(--text-sub);
    white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow: auto;
    font-family: var(--font-mono, monospace); line-height: 1.5;
  }

  /* ── Tool calls ─────────────── */
  .tools-list { display: flex; flex-direction: column; gap: 0.4rem; }
  .tool-item {
    border: 1px solid var(--border); border-radius: var(--radius-md);
    overflow: hidden;
  }
  .tool-header {
    display: flex; justify-content: space-between; align-items: center;
    width: 100%; padding: 0.55rem 0.85rem; background: var(--bg-surface1);
    border: none; cursor: pointer; text-align: left;
    color: var(--text); font-size: 0.85rem;
  }
  .tool-header:hover { background: var(--bg-surface2); }
  .tool-name { font-weight: 600; font-family: var(--font-mono, monospace); }
  .tool-toggle { font-size: 0.75rem; color: var(--text-dim); }

  .tool-body { padding: 0.75rem 0.85rem; border-top: 1px solid var(--border); }
  .tool-sub { margin-bottom: 0.6rem; }
  .tool-sub:last-child { margin-bottom: 0; }
  .tool-sub-label {
    display: block; font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--text-dim); margin-bottom: 0.25rem;
  }
  .tool-pre {
    padding: 0.5rem 0.7rem; background: var(--bg); border-radius: var(--radius-sm);
    font-size: 0.78rem; color: var(--text-sub); white-space: pre-wrap; word-break: break-word;
    max-height: 250px; overflow: auto; font-family: var(--font-mono, monospace);
    line-height: 1.45;
  }

  .empty-state {
    padding: 2rem; text-align: center; background: var(--bg-surface1);
    border-radius: var(--radius-md); border: 1px solid var(--border);
  }

  /* ── Breakdown tab (summary) ── */
  .summary-grid {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem; margin-bottom: 1rem;
  }
  .usage-grid {
    display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    gap: 1rem; margin-bottom: 1rem;
  }

  .metric-value {
    font-size: 1.9rem; font-weight: 700; line-height: 1.1; color: var(--text);
  }
  .metric-sub { color: var(--text-sub); font-size: 0.85rem; margin-top: 0.45rem; }

  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.7rem 0; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); }
  td { color: var(--text); font-size: 0.9rem; }
  .model-cell { font-family: var(--font-mono, monospace); font-size: 0.82rem; }
  .num-cell { font-variant-numeric: tabular-nums; }

  .system-list { display: flex; flex-direction: column; gap: 0.7rem; }
  .system-row {
    display: flex; justify-content: space-between; align-items: center;
    gap: 1rem; padding: 0.75rem 0.85rem; border-radius: var(--radius-sm);
    background: var(--bg-surface1);
  }
  .system-name { color: var(--text); font-weight: 600; text-transform: capitalize; }
  .system-meta { color: var(--text-sub); font-size: 0.85rem; }
  .system-tokens { color: var(--blue); font-weight: 700; white-space: nowrap; }

  .trend-list { display: flex; flex-direction: column; gap: 0.7rem; }
  .trend-row { display: grid; grid-template-columns: 110px minmax(0, 1fr) 90px; gap: 0.75rem; align-items: center; }
  .trend-meta { display: flex; flex-direction: column; color: var(--text-sub); font-size: 0.85rem; }
  .trend-bar-bg { height: 10px; border-radius: 999px; background: var(--bg-surface1); overflow: hidden; }
  .trend-bar { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--teal), var(--blue)); }
  .trend-value { color: var(--blue); font-weight: 700; white-space: nowrap; }

  @media (max-width: 1100px) {
    .summary-grid, .usage-grid { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 800px) {
    .page-header { flex-direction: column; align-items: stretch; }
    .summary-grid, .usage-grid { grid-template-columns: 1fr; }
    .trend-row { grid-template-columns: 1fr; }
  }
</style>
