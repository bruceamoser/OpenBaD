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

  interface CredentialField {
    key: string;
    label: string;
    secret: boolean;
  }

  interface CatalogEntry {
    name: string;
    label: string;
    icon: string;
    description: string;
    credential_fields: CredentialField[];
    installed: boolean;
  }

  // ── State ──────────────────────────────────────────────── //
  let plugins: Plugin[] = $state([]);
  let loading = $state(true);
  let error = $state('');

  // Config modal state
  let configPlugin: Plugin | null = $state(null);
  let configCredentials = $state('');
  let configSaving = $state(false);
  let configMsg = $state('');

  // Test state
  let testPlugin: string | null = $state(null);
  let testResult = $state('');
  let testLoading = $state(false);

  // ── Wizard state ───────────────────────────────────────── //
  let wizardOpen = $state(false);
  let wizardStep: 1 | 2 | 3 | 4 = $state(1);
  let catalog: CatalogEntry[] = $state([]);
  let catalogLoading = $state(false);
  let selectedPlugin: CatalogEntry | null = $state(null);
  let credValues: Record<string, string> = $state({});
  let wizardSaving = $state(false);
  let wizardMsg = $state('');
  let wizardTestResult = $state('');
  let wizardTestLoading = $state(false);
  let wizardEnableOnFinish = $state(true);

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

  // ── Wizard logic ───────────────────────────────────────── //
  async function openWizard(): Promise<void> {
    wizardOpen = true;
    wizardStep = 1;
    selectedPlugin = null;
    credValues = {};
    wizardMsg = '';
    wizardTestResult = '';
    wizardEnableOnFinish = true;
    catalogLoading = true;
    try {
      const resp = await apiGet<{ catalog: CatalogEntry[] }>('/api/transducers/catalog');
      catalog = resp.catalog ?? [];
    } catch (err) {
      wizardMsg = `Failed to load catalog: ${err}`;
    } finally {
      catalogLoading = false;
    }
  }

  function closeWizard(): void {
    wizardOpen = false;
    selectedPlugin = null;
    credValues = {};
    wizardMsg = '';
    wizardTestResult = '';
  }

  function selectPlugin(entry: CatalogEntry): void {
    if (entry.installed) return;
    selectedPlugin = entry;
    credValues = {};
    for (const f of entry.credential_fields) {
      credValues[f.key] = '';
    }
    wizardStep = 2;
    wizardMsg = '';
  }

  function wizardBack(): void {
    wizardMsg = '';
    wizardTestResult = '';
    if (wizardStep === 2) { wizardStep = 1; selectedPlugin = null; }
    else if (wizardStep === 3) { wizardStep = 2; }
    else if (wizardStep === 4) { wizardStep = 3; }
  }

  function wizardToStep3(): void {
    if (!selectedPlugin) return;
    const missing = selectedPlugin.credential_fields.filter(f => !credValues[f.key]?.trim());
    if (missing.length > 0) {
      wizardMsg = `Please fill in: ${missing.map(f => f.label).join(', ')}`;
      return;
    }
    wizardMsg = '';
    wizardStep = 3;
  }

  async function wizardTestConnection(): Promise<void> {
    if (!selectedPlugin) return;
    wizardTestLoading = true;
    wizardTestResult = '';
    wizardMsg = '';
    try {
      // Save the plugin with credentials (not yet enabled)
      await apiPost('/api/transducers', {
        name: selectedPlugin.name,
        credentials: { ...credValues },
        enabled: false,
      });
      // Test
      const resp = await apiPost<{ ok: boolean; result?: string; error?: string }>(
        `/api/transducers/${selectedPlugin.name}/test`,
        { target: 'test', content: 'Wizard setup test from OpenBaD' }
      );
      wizardTestResult = resp.ok ? 'success' : `failed: ${resp.error ?? 'Unknown error'}`;
    } catch (err) {
      const msg = String(err);
      if (msg.includes('409') || msg.includes('Conflict') || msg.includes('already exists')) {
        try {
          await apiPut(`/api/transducers/${selectedPlugin.name}`, {
            credentials: { ...credValues },
          });
          const resp = await apiPost<{ ok: boolean; result?: string; error?: string }>(
            `/api/transducers/${selectedPlugin.name}/test`,
            { target: 'test', content: 'Wizard setup test from OpenBaD' }
          );
          wizardTestResult = resp.ok ? 'success' : `failed: ${resp.error ?? 'Unknown error'}`;
        } catch (retryErr) {
          wizardTestResult = `failed: ${retryErr}`;
        }
      } else {
        wizardTestResult = `failed: ${msg}`;
      }
    } finally {
      wizardTestLoading = false;
    }
  }

  async function wizardFinish(): Promise<void> {
    if (!selectedPlugin) return;
    wizardSaving = true;
    wizardMsg = '';
    try {
      // If the plugin wasn't saved yet (test was skipped), create it now
      try {
        await apiPost('/api/transducers', {
          name: selectedPlugin.name,
          credentials: { ...credValues },
          enabled: wizardEnableOnFinish,
        });
      } catch (err) {
        const msg = String(err);
        if (msg.includes('409') || msg.includes('Conflict') || msg.includes('already exists')) {
          // Already created during test step — just toggle enabled
          if (wizardEnableOnFinish) {
            await apiPut(`/api/transducers/${selectedPlugin.name}`, { enabled: true });
          }
        } else {
          throw err;
        }
      }
      await loadPlugins();
      closeWizard();
    } catch (err) {
      wizardMsg = `Error: ${err}`;
    } finally {
      wizardSaving = false;
    }
  }

  function handleKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      if (wizardOpen) closeWizard();
      else closeConfig();
    }
  }

  const STEP_LABELS = ['Choose Plugin', 'Credentials', 'Test', 'Finish'];

  onMount(() => {
    loadPlugins();
    pollTimer = setInterval(loadPlugins, 15_000);
  });

  onDestroy(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="page-header">
  <div class="header-row">
    <div>
      <h2>Transducers</h2>
      <span class="page-sub">Peripheral integrations — Corsair plugins</span>
    </div>
    <button class="btn btn-primary add-btn" onclick={openWizard}>+ Add Plugin</button>
  </div>
</div>

{#if loading}
  <p class="loading">Loading plugins…</p>
{:else if error}
  <p class="error">{error}</p>
{:else if plugins.length === 0}
  <div class="empty-state">
    <span class="empty-icon">🔌</span>
    <p>No plugins configured yet.</p>
    <button class="btn btn-primary" onclick={openWizard}>Set Up Your First Plugin</button>
  </div>
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
          <button class="btn-sm btn-config" onclick={() => openConfig(p)}>Configure</button>
          <button
            class="btn-sm btn-test"
            onclick={() => testPlugin_(p.name)}
            disabled={!p.enabled || testLoading}
          >
            {testLoading && testPlugin === p.name ? 'Testing…' : 'Test'}
          </button>
        </div>
        {#if testPlugin === p.name && testResult}
          <div class="test-result" class:ok={testResult.startsWith('✓')}>{testResult}</div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<!-- ═══════════════════════════════════════════════════════════════ -->
<!--  Setup Wizard                                                  -->
<!-- ═══════════════════════════════════════════════════════════════ -->
{#if wizardOpen}
  <div class="modal-backdrop" onclick={closeWizard} role="presentation">
    <div class="wizard" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <div class="wizard-header">
        <h3>Add Plugin</h3>
        <button class="modal-close" onclick={closeWizard}>✕</button>
      </div>

      <!-- Step indicator -->
      <div class="steps">
        {#each STEP_LABELS as label, i}
          <div class="step" class:active={wizardStep === i + 1} class:done={wizardStep > i + 1}>
            <span class="step-num">{wizardStep > i + 1 ? '✓' : i + 1}</span>
            <span class="step-label">{label}</span>
          </div>
          {#if i < STEP_LABELS.length - 1}
            <div class="step-line" class:done={wizardStep > i + 1}></div>
          {/if}
        {/each}
      </div>

      <!-- Step 1: Choose plugin -->
      {#if wizardStep === 1}
        <div class="wizard-body">
          {#if catalogLoading}
            <p class="loading">Loading available plugins…</p>
          {:else}
            <p class="wizard-desc">Choose an integration to set up.</p>
            <div class="catalog-grid">
              {#each catalog as entry}
                <button
                  class="catalog-card"
                  class:installed={entry.installed}
                  onclick={() => selectPlugin(entry)}
                  disabled={entry.installed}
                >
                  <span class="catalog-icon">{entry.icon}</span>
                  <div class="catalog-info">
                    <span class="catalog-name">{entry.label}</span>
                    <span class="catalog-desc">{entry.description}</span>
                  </div>
                  {#if entry.installed}
                    <span class="installed-badge">Installed</span>
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        </div>

      <!-- Step 2: Credentials -->
      {:else if wizardStep === 2 && selectedPlugin}
        <div class="wizard-body">
          <p class="wizard-desc">
            Enter credentials for <strong>{selectedPlugin.label}</strong>.
            These are stored server-side with restricted file permissions.
          </p>
          <div class="cred-fields">
            {#each selectedPlugin.credential_fields as field}
              <div class="field-group">
                <label class="field-label" for="cred-{field.key}">{field.label}</label>
                <input
                  id="cred-{field.key}"
                  class="field-input"
                  type={field.secret ? 'password' : 'text'}
                  bind:value={credValues[field.key]}
                  placeholder={field.label}
                  autocomplete="off"
                />
              </div>
            {/each}
          </div>
          {#if wizardMsg}
            <p class="wizard-error">{wizardMsg}</p>
          {/if}
          <div class="wizard-actions">
            <button class="btn btn-secondary" onclick={wizardBack}>Back</button>
            <button class="btn btn-primary" onclick={wizardToStep3}>Next</button>
          </div>
        </div>

      <!-- Step 3: Test connection -->
      {:else if wizardStep === 3 && selectedPlugin}
        <div class="wizard-body">
          <p class="wizard-desc">
            Test the connection to <strong>{selectedPlugin.label}</strong> to verify your credentials.
          </p>
          <div class="test-area">
            {#if wizardTestResult === ''}
              <button
                class="btn btn-primary test-btn"
                onclick={wizardTestConnection}
                disabled={wizardTestLoading}
              >
                {wizardTestLoading ? 'Testing…' : 'Run Connection Test'}
              </button>
            {:else if wizardTestResult === 'success'}
              <div class="test-banner ok"><span>✓</span> Connection successful!</div>
            {:else}
              <div class="test-banner fail"><span>✗</span> {wizardTestResult}</div>
              <button class="btn btn-secondary retry-btn" onclick={() => { wizardTestResult = ''; wizardStep = 2; }}>
                Edit Credentials
              </button>
            {/if}
          </div>
          {#if wizardMsg}
            <p class="wizard-error">{wizardMsg}</p>
          {/if}
          <div class="wizard-actions">
            <button class="btn btn-secondary" onclick={wizardBack}>Back</button>
            <button
              class="btn btn-primary"
              onclick={() => { wizardStep = 4; }}
              disabled={wizardTestResult !== 'success' && wizardTestResult === ''}
            >
              {wizardTestResult === 'success' ? 'Next' : 'Skip Test'}
            </button>
          </div>
        </div>

      <!-- Step 4: Confirm & finish -->
      {:else if wizardStep === 4 && selectedPlugin}
        <div class="wizard-body">
          <div class="finish-summary">
            <span class="finish-icon">{selectedPlugin.icon}</span>
            <h4>{selectedPlugin.label}</h4>
            <p class="wizard-desc">
              {#if wizardTestResult === 'success'}
                Connection verified. Ready to go!
              {:else}
                Plugin configured (test was skipped).
              {/if}
            </p>
          </div>
          <label class="enable-check">
            <input type="checkbox" bind:checked={wizardEnableOnFinish} />
            <span>Enable plugin immediately</span>
          </label>
          {#if wizardMsg}
            <p class="wizard-error">{wizardMsg}</p>
          {/if}
          <div class="wizard-actions">
            <button class="btn btn-secondary" onclick={wizardBack}>Back</button>
            <button class="btn btn-primary" onclick={wizardFinish} disabled={wizardSaving}>
              {wizardSaving ? 'Saving…' : 'Finish Setup'}
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- ═══════════════════════════════════════════════════════════════ -->
<!--  Configure credentials modal (existing plugins)               -->
<!-- ═══════════════════════════════════════════════════════════════ -->
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
        <p class="config-msg" class:ok={configMsg.startsWith('Credentials')}>{configMsg}</p>
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
  .header-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
  }
  .page-header h2 { font-size: 1.5rem; color: var(--text); margin: 0; }
  .page-sub { color: var(--text-dim); font-size: 0.9rem; }
  .add-btn { white-space: nowrap; }

  .loading, .error {
    color: var(--text-dim);
    text-align: center;
    padding: 2rem;
  }
  .error { color: var(--red); }

  .empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-dim);
  }
  .empty-icon { font-size: 3rem; display: block; margin-bottom: 1rem; }
  .empty-state p { margin-bottom: 1.5rem; font-size: 1rem; }

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
  .plugin-card:hover { background: var(--bg-surface2); border-color: var(--blue); }
  .plugin-card.enabled { border-left: 3px solid var(--green); }

  .card-top { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
  .plugin-icon { font-size: 1.6rem; }
  .plugin-meta { flex: 1; }
  .plugin-name { display: block; font-weight: 600; color: var(--text); }
  .plugin-status { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--text-dim); }

  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
  .dot-green { background: var(--green); }
  .dot-red { background: var(--red); }
  .dot-grey { background: var(--text-dim); }

  .toggle-wrap { position: relative; display: inline-block; width: 40px; height: 22px; cursor: pointer; }
  .toggle-wrap input { opacity: 0; width: 0; height: 0; }
  .toggle-track {
    position: absolute; inset: 0; background: var(--bg-surface0);
    border-radius: 11px; transition: background 0.2s;
  }
  .toggle-track::after {
    content: ''; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px;
    background: var(--text-dim); border-radius: 50%; transition: transform 0.2s, background 0.2s;
  }
  .toggle-wrap input:checked + .toggle-track { background: var(--green); }
  .toggle-wrap input:checked + .toggle-track::after { transform: translateX(18px); background: var(--bg-base); }

  .card-actions { display: flex; gap: 0.5rem; }
  .btn-sm {
    padding: 0.35rem 0.75rem; border-radius: var(--radius-sm, 4px);
    border: 1px solid var(--border); background: var(--bg-surface0);
    color: var(--text); cursor: pointer; font-size: 0.8rem; transition: all 0.15s ease;
  }
  .btn-sm:hover:not(:disabled) { background: var(--bg-surface2); }
  .btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-config { color: var(--blue); border-color: var(--blue); }
  .btn-test { color: var(--teal); border-color: var(--teal); }

  .test-result {
    margin-top: 0.5rem; padding: 0.4rem 0.6rem; border-radius: 4px;
    font-size: 0.8rem; color: var(--red); background: rgba(243, 139, 168, 0.1);
  }
  .test-result.ok { color: var(--green); background: rgba(166, 227, 161, 0.1); }

  /* ── Modal / Wizard ── */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 100; padding: 1rem;
  }
  .modal, .wizard {
    background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius, 6px); padding: 1.5rem;
    max-width: 600px; width: 100%; max-height: 85vh; overflow-y: auto;
  }
  .modal { max-width: 500px; }
  .modal-header, .wizard-header {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem;
  }
  .modal-header h3, .wizard-header h3 { margin: 0; color: var(--text); }
  .modal-close { background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 1.2rem; }
  .modal-close:hover { color: var(--text); }
  .modal-desc { color: var(--text-dim); font-size: 0.85rem; margin-bottom: 1rem; }

  /* ── Steps ── */
  .steps { display: flex; align-items: center; margin-bottom: 1.5rem; padding: 0 0.5rem; }
  .step { display: flex; align-items: center; gap: 0.4rem; white-space: nowrap; }
  .step-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 24px; height: 24px; border-radius: 50%;
    font-size: 0.75rem; font-weight: 700;
    background: var(--bg-surface0); color: var(--text-dim);
    border: 2px solid var(--border); flex-shrink: 0;
  }
  .step.active .step-num { background: var(--blue); color: var(--bg-base); border-color: var(--blue); }
  .step.done .step-num { background: var(--green); color: var(--bg-base); border-color: var(--green); }
  .step-label { font-size: 0.75rem; color: var(--text-dim); }
  .step.active .step-label { color: var(--text); font-weight: 600; }
  .step.done .step-label { color: var(--green); }
  .step-line { flex: 1; height: 2px; background: var(--border); margin: 0 0.4rem; min-width: 12px; }
  .step-line.done { background: var(--green); }

  /* ── Wizard body ── */
  .wizard-body { min-height: 180px; }
  .wizard-desc { color: var(--text-dim); font-size: 0.9rem; margin-bottom: 1rem; }
  .wizard-error { color: var(--red); font-size: 0.85rem; margin-top: 0.5rem; }
  .wizard-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.5rem; }

  /* ── Catalog (step 1) ── */
  .catalog-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 0.75rem; }
  .catalog-card {
    display: flex; align-items: center; gap: 0.75rem; padding: 0.85rem 1rem;
    background: var(--bg-surface0); border: 1px solid var(--border);
    border-radius: var(--radius-md, 8px); cursor: pointer; text-align: left;
    transition: all 0.15s ease; color: var(--text);
  }
  .catalog-card:hover:not(:disabled) { border-color: var(--blue); background: var(--bg-surface2); }
  .catalog-card.installed { opacity: 0.5; cursor: not-allowed; }
  .catalog-icon { font-size: 1.5rem; flex-shrink: 0; }
  .catalog-info { flex: 1; min-width: 0; }
  .catalog-name { display: block; font-weight: 600; font-size: 0.9rem; }
  .catalog-desc {
    display: block; font-size: 0.78rem; color: var(--text-dim);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .installed-badge {
    font-size: 0.7rem; color: var(--green); background: rgba(166, 227, 161, 0.15);
    padding: 0.15rem 0.5rem; border-radius: 4px; white-space: nowrap;
  }

  /* ── Credential fields (step 2) ── */
  .cred-fields { display: flex; flex-direction: column; gap: 0.85rem; }
  .field-group { display: flex; flex-direction: column; gap: 0.25rem; }
  .field-label { color: var(--text-sub); font-size: 0.8rem; font-weight: 600; }
  .field-input {
    background: var(--bg-surface0); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text); padding: 0.55rem 0.7rem; font-size: 0.85rem;
  }
  .field-input:focus { outline: none; border-color: var(--blue); }

  /* ── Test area (step 3) ── */
  .test-area { text-align: center; padding: 1rem 0; }
  .test-btn { min-width: 200px; }
  .test-banner {
    display: flex; align-items: center; justify-content: center; gap: 0.5rem;
    padding: 0.75rem 1rem; border-radius: 6px; font-weight: 600; font-size: 0.9rem;
  }
  .test-banner.ok { background: rgba(166, 227, 161, 0.15); color: var(--green); }
  .test-banner.fail { background: rgba(243, 139, 168, 0.1); color: var(--red); }
  .retry-btn { margin-top: 0.75rem; }

  /* ── Finish (step 4) ── */
  .finish-summary { text-align: center; padding: 1rem 0; }
  .finish-icon { font-size: 2.5rem; display: block; margin-bottom: 0.5rem; }
  .finish-summary h4 { margin: 0 0 0.5rem; color: var(--text); font-size: 1.1rem; }
  .enable-check {
    display: flex; align-items: center; gap: 0.5rem;
    color: var(--text); font-size: 0.9rem; cursor: pointer; padding: 0.5rem 0;
  }
  .enable-check input { accent-color: var(--green); }

  /* ── Config modal ── */
  .creds-textarea {
    width: 100%; background: var(--bg-surface0); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text); padding: 0.6rem;
    font-family: monospace; font-size: 0.85rem; resize: vertical;
  }
  .creds-textarea:focus { outline: none; border-color: var(--blue); }
  .config-msg { margin-top: 0.5rem; font-size: 0.85rem; color: var(--red); }
  .config-msg.ok { color: var(--green); }
  .modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1rem; }

  /* ── Shared buttons ── */
  .btn {
    padding: 0.5rem 1rem; border-radius: var(--radius-sm, 4px); border: none;
    cursor: pointer; font-weight: 600; transition: all 0.15s ease;
  }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: var(--bg-surface0); color: var(--text); border: 1px solid var(--border);
  }
  .btn-secondary:hover:not(:disabled) { background: var(--bg-surface2); }
  .btn-primary { background: var(--blue); color: var(--bg-base); }
  .btn-primary:hover:not(:disabled) { opacity: 0.85; }
</style>
