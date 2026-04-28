<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { get as apiGet, put as apiPut, post as apiPost } from '$lib/api/client';

  // ── Types ──────────────────────────────────────────────── //
  interface Plugin {
    name: string;
    enabled: boolean;
    has_credentials: boolean;
    health: 'healthy' | 'unhealthy' | 'unknown';
  }

  // ── State ──────────────────────────────────────────────── //
  let plugins: Plugin[] = $state([]);
  let loading = $state(true);
  let error = $state('');

  // Modal state
  let configPlugin: Plugin | null = $state(null);
  let configCredentials = $state('');
  let configSaving = $state(false);
  let configMsg = $state('');

  // Test state
  let testPlugin: string | null = $state(null);
  let testResult = $state('');
  let testLoading = $state(false);

  let pollTimer: ReturnType<typeof setInterval> | undefined;

  // ── Data loading ───────────────────────────────────────── //
  async function loadPlugins(): Promise<void> {
    try {
      const resp = await apiGet<{ plugins: Plugin[] }>('/api/transducers');
      plugins = resp.plugins ?? [];
      error = '';
    } catch (err) {
      error = String(err);
    } finally {
      loading = false;
    }
  }

  async function togglePlugin(p: Plugin): Promise<void> {
    try {
      const updated = await apiPut<Plugin>(`/api/transducers/${p.name}`, {
        enabled: !p.enabled,
      });
      plugins = plugins.map(pl => pl.name === p.name ? { ...pl, ...updated } : pl);
    } catch (err) {
      error = String(err);
    }
  }

  function openConfig(p: Plugin): void {
    configPlugin = p;
    configCredentials = '';
    configMsg = '';
  }

  function closeConfig(): void {
    configPlugin = null;
    configCredentials = '';
    configMsg = '';
  }

  async function saveCredentials(): Promise<void> {
    if (!configPlugin) return;
    configSaving = true;
    configMsg = '';
    try {
      let creds: Record<string, string>;
      try {
        creds = JSON.parse(configCredentials);
      } catch {
        configMsg = 'Invalid JSON — enter {"api_key": "..."}';
        configSaving = false;
        return;
      }
      await apiPut(`/api/transducers/${configPlugin.name}`, { credentials: creds });
      configMsg = 'Credentials saved.';
      await loadPlugins();
    } catch (err) {
      configMsg = `Error: ${err}`;
    } finally {
      configSaving = false;
    }
  }

  async function testPlugin_(name: string): Promise<void> {
    testPlugin = name;
    testLoading = true;
    testResult = '';
    try {
      const resp = await apiPost<{ ok: boolean; result?: string; error?: string }>(
        `/api/transducers/${name}/test`,
        { target: 'test', content: `Test from OpenBaD at ${new Date().toLocaleTimeString()}` }
      );
      testResult = resp.ok ? `✓ ${resp.result ?? 'Sent'}` : `✗ ${resp.error ?? 'Failed'}`;
    } catch (err) {
      testResult = `✗ ${err}`;
    } finally {
      testLoading = false;
    }
  }

  function healthDot(status: string): string {
    if (status === 'healthy') return 'dot-green';
    if (status === 'unhealthy') return 'dot-red';
    return 'dot-grey';
  }

  function handleKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') closeConfig();
  }

  onMount(() => {
    loadPlugins();
    pollTimer = setInterval(loadPlugins, 15_000);
  });

  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page-header">
  <h2>Transducers</h2>
  <span class="page-sub">Peripheral integrations — Corsair plugins</span>
</div>

{#if loading}
  <p class="loading">Loading plugins…</p>
{:else if error}
  <p class="error">{error}</p>
{:else if plugins.length === 0}
  <p class="empty">No plugins configured. Edit <code>config/peripherals.yaml</code> to add plugins.</p>
{:else}
  <div class="plugin-grid">
    {#each plugins as p}
      <div class="plugin-card" class:enabled={p.enabled}>
        <div class="card-top">
          <span class="plugin-icon">🔌</span>
          <div class="plugin-meta">
            <span class="plugin-name">{p.name}</span>
            <span class="plugin-status">
              <span class="dot {healthDot(p.health)}"></span>
              {p.health}
            </span>
          </div>
          <label class="toggle-wrap">
            <input
              type="checkbox"
              checked={p.enabled}
              onchange={() => togglePlugin(p)}
            />
            <span class="toggle-track"></span>
          </label>
        </div>

        <div class="card-actions">
          <button class="btn-sm btn-config" onclick={() => openConfig(p)}>
            Configure
          </button>
          <button
            class="btn-sm btn-test"
            onclick={() => testPlugin_(p.name)}
            disabled={!p.enabled || testLoading}
          >
            {testLoading && testPlugin === p.name ? 'Testing…' : 'Test'}
          </button>
        </div>

        {#if testPlugin === p.name && testResult}
          <div class="test-result" class:ok={testResult.startsWith('✓')}>
            {testResult}
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<!-- Configuration modal -->
{#if configPlugin}
  <div class="modal-backdrop" onclick={closeConfig} role="presentation">
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3>Configure {configPlugin.name}</h3>
        <button class="modal-close" onclick={closeConfig}>✕</button>
      </div>

      <p class="modal-desc">
        Enter API credentials as JSON. Tokens are stored server-side with restricted permissions (0600).
      </p>

      <label class="field-label" for="creds-input">Credentials JSON</label>
      <textarea
        id="creds-input"
        class="creds-textarea"
        bind:value={configCredentials}
        placeholder={'{"api_key": "your-key-here"}'}
        rows="5"
      ></textarea>

      {#if configMsg}
        <p class="config-msg" class:ok={configMsg.startsWith('Credentials')}>
          {configMsg}
        </p>
      {/if}

      <div class="modal-actions">
        <button class="btn btn-secondary" onclick={closeConfig}>Cancel</button>
        <button class="btn btn-primary" onclick={saveCredentials} disabled={configSaving}>
          {configSaving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .page-header { margin-bottom: 1.5rem; }
  .page-header h2 { font-size: 1.5rem; color: var(--text); margin: 0; }
  .page-sub { color: var(--text-dim); font-size: 0.9rem; }

  .loading, .error, .empty {
    color: var(--text-dim);
    text-align: center;
    padding: 2rem;
  }
  .error { color: var(--red); }

  /* ── Plugin grid ── */
  .plugin-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1rem;
  }

  .plugin-card {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius-md, 8px);
    padding: 1rem;
    transition: all 0.15s ease;
  }
  .plugin-card:hover {
    background: var(--bg-surface2);
    border-color: var(--blue);
  }
  .plugin-card.enabled { border-left: 3px solid var(--green); }

  .card-top {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }

  .plugin-icon { font-size: 1.6rem; }
  .plugin-meta { flex: 1; }
  .plugin-name { display: block; font-weight: 600; color: var(--text); }
  .plugin-status {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--text-dim);
  }

  /* ── Health dots ── */
  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .dot-green { background: var(--green); }
  .dot-red { background: var(--red); }
  .dot-grey { background: var(--text-dim); }

  /* ── Toggle switch ── */
  .toggle-wrap {
    position: relative;
    display: inline-block;
    width: 40px;
    height: 22px;
    cursor: pointer;
  }
  .toggle-wrap input { opacity: 0; width: 0; height: 0; }
  .toggle-track {
    position: absolute;
    inset: 0;
    background: var(--bg-surface0);
    border-radius: 11px;
    transition: background 0.2s;
  }
  .toggle-track::after {
    content: '';
    position: absolute;
    width: 16px;
    height: 16px;
    left: 3px;
    top: 3px;
    background: var(--text-dim);
    border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }
  .toggle-wrap input:checked + .toggle-track {
    background: var(--green);
  }
  .toggle-wrap input:checked + .toggle-track::after {
    transform: translateX(18px);
    background: var(--bg-base);
  }

  /* ── Card actions ── */
  .card-actions {
    display: flex;
    gap: 0.5rem;
  }

  .btn-sm {
    padding: 0.35rem 0.75rem;
    border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--border);
    background: var(--bg-surface0);
    color: var(--text);
    cursor: pointer;
    font-size: 0.8rem;
    transition: all 0.15s ease;
  }
  .btn-sm:hover:not(:disabled) { background: var(--bg-surface2); }
  .btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-config { color: var(--blue); border-color: var(--blue); }
  .btn-test { color: var(--teal); border-color: var(--teal); }

  /* ── Test result ── */
  .test-result {
    margin-top: 0.5rem;
    padding: 0.4rem 0.6rem;
    border-radius: 4px;
    font-size: 0.8rem;
    color: var(--red);
    background: rgba(243, 139, 168, 0.1);
  }
  .test-result.ok {
    color: var(--green);
    background: rgba(166, 227, 161, 0.1);
  }

  /* ── Modal ── */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 1rem;
  }
  .modal {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    border-radius: var(--radius, 6px);
    padding: 1.5rem;
    max-width: 500px;
    width: 100%;
    max-height: 80vh;
    overflow-y: auto;
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1rem;
  }
  .modal-header h3 { margin: 0; color: var(--text); }
  .modal-close {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 1.2rem;
  }
  .modal-close:hover { color: var(--text); }
  .modal-desc {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }

  .field-label {
    display: block;
    color: var(--text-sub);
    font-size: 0.8rem;
    margin-bottom: 0.35rem;
    font-weight: 600;
  }
  .creds-textarea {
    width: 100%;
    background: var(--bg-surface0);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    padding: 0.6rem;
    font-family: monospace;
    font-size: 0.85rem;
    resize: vertical;
  }
  .creds-textarea:focus {
    outline: none;
    border-color: var(--blue);
  }

  .config-msg {
    margin-top: 0.5rem;
    font-size: 0.85rem;
    color: var(--red);
  }
  .config-msg.ok { color: var(--green); }

  .modal-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    margin-top: 1rem;
  }
  .btn {
    padding: 0.5rem 1rem;
    border-radius: var(--radius-sm, 4px);
    border: none;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.15s ease;
  }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: var(--bg-surface0);
    color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover:not(:disabled) { background: var(--bg-surface2); }
  .btn-primary {
    background: var(--blue);
    color: var(--bg-base);
  }
  .btn-primary:hover:not(:disabled) { opacity: 0.85; }
</style>
