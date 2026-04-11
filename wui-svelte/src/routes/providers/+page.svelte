<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import { get as apiGet, put as apiPut, post as apiPost } from '$lib/api/client';
  import { endocrineLevels } from '$lib/stores/websocket';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface ProviderEntry {
    name: string;
    base_url: string;
    model: string;
    api_key_env: string;
    timeout_ms: number;
    enabled: boolean;
    verified?: boolean;
  }

  interface SystemAssignment {
    provider: string;
    model: string;
  }

  interface FallbackEntry {
    provider: string;
    model: string;
  }

  interface ProvidersData {
    enabled: boolean;
    default_provider: string;
    providers: ProviderEntry[];
  }

  interface SystemsData {
    systems: Record<string, SystemAssignment>;
    default_fallback_chain: FallbackEntry[];
  }

  interface VerifyResult {
    provider_type: string;
    available: boolean;
    latency_ms: number;
    models_available: number;
    models: string[];
    message: string;
    provider: ProviderEntry;
  }

  interface DeviceCodeResult {
    flow_id: string;
    user_code: string;
    verification_uri: string;
    interval: number;
    expires_in: number;
    message: string;
  }

  interface CopilotCompleteResult {
    authorized: boolean;
    pending: boolean;
    message: string;
    provider?: ProviderEntry;
    models?: string[];
    latency_ms?: number;
    models_available?: number;
    interval?: number;
  }

  type WizardStep = 'closed' | 'pick-type' | 'copilot-auth' | 'local-form' | 'verifying' | 'done';

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  const COGNITIVE_SYSTEMS = ['chat', 'reasoning', 'reactions', 'sleep'];

  let providers: ProviderEntry[] = $state([]);
  let defaultProvider = $state('');
  let systems: Record<string, SystemAssignment> = $state({});
  let fallbackChain: FallbackEntry[] = $state([]);
  let dirty = $state(false);
  let saving = $state(false);
  let statusMsg = $state('');
  let dragIdx: number | null = $state(null);
  let loading = $state(true);

  // Wizard state
  let wizStep: WizardStep = $state('closed');
  let wizMsg = $state('');
  let wizError = $state('');
  let wizBusy = $state(false);

  // Copilot wizard
  let copilotFlowId = $state('');
  let copilotUserCode = $state('');
  let copilotVerifyUri = $state('');
  let copilotInterval = $state(5);
  let copilotPollTimer: ReturnType<typeof setInterval> | null = $state(null);
  let copilotModels: string[] = $state([]);

  // Local OpenAI wizard
  let localBaseUrl = $state('http://localhost:11434/v1');
  let localModel = $state('');
  let localApiKeyEnv = $state('');
  let localVerified = $state(false);
  let localModels: string[] = $state([]);
  let localLatency = $state(0);

  // Derived cortisol from WS
  let cortisol = $derived($endocrineLevels?.cortisol ?? 0);

  // Show wizard prominently if no providers configured
  let hasProviders = $derived(providers.length > 0);

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function load(): Promise<void> {
    loading = true;
    try {
      const pData = await apiGet<ProvidersData>('/api/providers');
      providers = pData.providers ?? [];
      defaultProvider = pData.default_provider ?? '';

      const sData = await apiGet<SystemsData>('/api/systems');
      systems = sData.systems ?? {};
      fallbackChain = sData.default_fallback_chain ?? [];
    } catch (e) {
      statusMsg = `Load failed: ${e}`;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    load();
    return () => { stopCopilotPoll(); };
  });

  // ----------------------------------------------------------------
  // Save providers + systems
  // ----------------------------------------------------------------

  async function saveAll(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/providers', {
        enabled: true,
        default_provider: defaultProvider || (providers[0]?.name ?? ''),
        providers,
      });
      await apiPut('/api/systems', {
        systems,
        default_fallback_chain: fallbackChain,
      });
      dirty = false;
      statusMsg = 'Saved';
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  // ----------------------------------------------------------------
  // Remove provider
  // ----------------------------------------------------------------

  function removeProvider(idx: number): void {
    const removed = providers[idx];
    providers = providers.filter((_, i) => i !== idx);
    if (defaultProvider === removed.name && providers.length > 0) {
      defaultProvider = providers[0].name;
    } else if (providers.length === 0) {
      defaultProvider = '';
    }
    dirty = true;
  }

  // ----------------------------------------------------------------
  // Wizard: pick type
  // ----------------------------------------------------------------

  function openWizard(): void {
    wizStep = 'pick-type';
    wizMsg = '';
    wizError = '';
  }

  function closeWizard(): void {
    stopCopilotPoll();
    wizStep = 'closed';
    wizMsg = '';
    wizError = '';
    wizBusy = false;
    // Reset local form
    localBaseUrl = 'http://localhost:11434/v1';
    localModel = '';
    localApiKeyEnv = '';
    localVerified = false;
    localModels = [];
    localLatency = 0;
    // Reset copilot
    copilotFlowId = '';
    copilotUserCode = '';
    copilotVerifyUri = '';
    copilotModels = [];
  }

  // ----------------------------------------------------------------
  // Wizard: GitHub Copilot device-code flow
  // ----------------------------------------------------------------

  async function startCopilotFlow(): Promise<void> {
    wizBusy = true;
    wizError = '';
    try {
      const res = await apiPost<DeviceCodeResult>('/api/providers/copilot/device-code', {});
      copilotFlowId = res.flow_id;
      copilotUserCode = res.user_code;
      copilotVerifyUri = res.verification_uri;
      copilotInterval = res.interval || 5;
      wizStep = 'copilot-auth';
      wizMsg = res.message;
      startCopilotPoll();
    } catch (e) {
      wizError = `Failed to start Copilot auth: ${e}`;
    } finally {
      wizBusy = false;
    }
  }

  function startCopilotPoll(): void {
    stopCopilotPoll();
    copilotPollTimer = setInterval(pollCopilotComplete, copilotInterval * 1000);
  }

  function stopCopilotPoll(): void {
    if (copilotPollTimer !== null) {
      clearInterval(copilotPollTimer);
      copilotPollTimer = null;
    }
  }

  async function pollCopilotComplete(): Promise<void> {
    if (!copilotFlowId) return;
    try {
      const res = await apiPost<CopilotCompleteResult>('/api/providers/copilot/complete', {
        flow_id: copilotFlowId,
      });
      if (res.authorized) {
        stopCopilotPoll();
        copilotModels = res.models ?? [];
        if (res.provider) {
          providers = [...providers, { ...res.provider, verified: true }];
          if (!defaultProvider) defaultProvider = res.provider.name;
          dirty = true;
        }
        wizStep = 'done';
        wizMsg = res.message;
      } else if (res.pending) {
        wizMsg = res.message;
        if (res.interval) copilotInterval = res.interval;
      } else {
        stopCopilotPoll();
        wizError = res.message;
      }
    } catch (e) {
      stopCopilotPoll();
      wizError = `Polling failed: ${e}`;
    }
  }

  // ----------------------------------------------------------------
  // Wizard: Local OpenAI-compatible
  // ----------------------------------------------------------------

  async function verifyLocal(): Promise<void> {
    wizBusy = true;
    wizError = '';
    localVerified = false;
    try {
      const res = await apiPost<VerifyResult>('/api/providers/verify', {
        provider_type: 'local-openai',
        base_url: localBaseUrl,
        model: localModel,
        api_key_env: localApiKeyEnv,
        timeout_ms: 30000,
      });
      if (res.available) {
        localVerified = true;
        localModels = res.models ?? [];
        localLatency = res.latency_ms ?? 0;
        wizMsg = `Verified — ${res.models_available} model(s) available (${res.latency_ms?.toFixed(0)}ms)`;
        if (!localModel && localModels.length > 0) {
          localModel = localModels[0];
        }
      } else {
        wizError = res.message || 'Verification failed';
      }
    } catch (e) {
      wizError = `Verify failed: ${e}`;
    } finally {
      wizBusy = false;
    }
  }

  function addLocalProvider(): void {
    const entry: ProviderEntry = {
      name: 'custom',
      base_url: localBaseUrl,
      model: localModel,
      api_key_env: localApiKeyEnv,
      timeout_ms: 30000,
      enabled: true,
      verified: localVerified,
    };
    providers = [...providers, entry];
    if (!defaultProvider) defaultProvider = entry.name;
    dirty = true;
    wizStep = 'done';
    wizMsg = `Added local provider (${localModel || 'custom'})`;
  }

  // ----------------------------------------------------------------
  // System assignment helpers
  // ----------------------------------------------------------------

  function setSystem(sys: string, field: 'provider' | 'model', val: string): void {
    if (!systems[sys]) systems[sys] = { provider: '', model: '' };
    systems[sys][field] = val;
    dirty = true;
  }

  // ----------------------------------------------------------------
  // Fallback chain drag-to-reorder
  // ----------------------------------------------------------------

  function dragStart(idx: number): void {
    dragIdx = idx;
  }

  function dragOver(e: DragEvent): void {
    e.preventDefault();
  }

  function drop(targetIdx: number): void {
    if (dragIdx === null || dragIdx === targetIdx) return;
    const item = fallbackChain[dragIdx];
    const updated = [...fallbackChain];
    updated.splice(dragIdx, 1);
    updated.splice(targetIdx, 0, item);
    fallbackChain = updated;
    dragIdx = null;
    dirty = true;
  }

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------

  function healthColor(entry: ProviderEntry): string {
    if (entry.verified) return 'var(--green)';
    return 'var(--red)';
  }

  function cortisolColor(level: number): string {
    if (level < 0.3) return 'var(--green)';
    if (level < 0.7) return 'var(--yellow)';
    return 'var(--red)';
  }

  function providerDisplayName(p: ProviderEntry): string {
    if (p.name === 'github-copilot') return 'GitHub Copilot';
    if (p.name === 'custom') {
      try { return `Custom (${new URL(p.base_url).hostname})`; } catch { return 'Custom'; }
    }
    return p.name;
  }
</script>

<div class="page-header">
  <h2>Providers</h2>
  <p>Manage LLM providers, system assignments, and fallback chains</p>
</div>

<!-- ============================================================ -->
<!-- Setup Wizard (overlay)                                       -->
<!-- ============================================================ -->

{#if wizStep !== 'closed'}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="wizard-overlay" onclick={closeWizard}>
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="wizard-panel" onclick={(e) => e.stopPropagation()}>
      <button class="wizard-close" onclick={closeWizard} aria-label="Close wizard">&times;</button>

      {#if wizStep === 'pick-type'}
        <!-- Step 1: Pick provider type -->
        <h3>Add a Provider</h3>
        <p class="wizard-subtitle">Choose the type of LLM provider to configure.</p>
        <div class="wizard-options">
          <button class="wizard-option" onclick={startCopilotFlow} disabled={wizBusy}>
            <span class="wizard-option-icon">
              <svg viewBox="0 0 24 24" width="32" height="32" fill="currentColor">
                <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
              </svg>
            </span>
            <span class="wizard-option-label">GitHub Copilot</span>
            <span class="wizard-option-desc">Authenticate with your GitHub account via device code</span>
          </button>
          <button class="wizard-option" onclick={() => { wizStep = 'local-form'; wizError = ''; wizMsg = ''; }}>
            <span class="wizard-option-icon">
              <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>
              </svg>
            </span>
            <span class="wizard-option-label">Local / OpenAI-Compatible</span>
            <span class="wizard-option-desc">Ollama, LM Studio, vLLM, or any OpenAI-compatible endpoint</span>
          </button>
        </div>

      {:else if wizStep === 'copilot-auth'}
        <!-- Step 2a: Copilot device-code flow -->
        <h3>Authorize GitHub Copilot</h3>
        <p class="wizard-subtitle">Complete these steps to link your Copilot subscription.</p>
        <div class="copilot-steps">
          <div class="copilot-code-box">
            <span class="copilot-code-label">Your code</span>
            <span class="copilot-code">{copilotUserCode}</span>
          </div>
          <ol class="copilot-instructions">
            <li>
              Open <a href={copilotVerifyUri} target="_blank" rel="noopener noreferrer">{copilotVerifyUri}</a>
            </li>
            <li>Enter the code above</li>
            <li>Authorize the application</li>
          </ol>
          <div class="copilot-status">
            <span class="spinner"></span>
            <span>Waiting for authorization…</span>
          </div>
        </div>

      {:else if wizStep === 'local-form'}
        <!-- Step 2b: Local OpenAI form -->
        <h3>Local / OpenAI-Compatible Provider</h3>
        <p class="wizard-subtitle">Point to any OpenAI-compatible API endpoint.</p>
        <div class="local-form">
          <label for="wiz-base-url">
            Base URL
            <input
              id="wiz-base-url"
              type="url"
              bind:value={localBaseUrl}
              placeholder="http://localhost:11434/v1"
            />
          </label>
          <label for="wiz-model">
            Model
            <input
              id="wiz-model"
              type="text"
              bind:value={localModel}
              placeholder="llama3.2 (leave blank to auto-detect)"
            />
            {#if localModels.length > 0}
              <div class="model-chips">
                {#each localModels as m}
                  <button
                    class="model-chip"
                    class:selected={localModel === m}
                    onclick={() => { localModel = m; }}
                  >{m}</button>
                {/each}
              </div>
            {/if}
          </label>
          <label for="wiz-apikey">
            API Key Env Var <span class="optional">(optional)</span>
            <input
              id="wiz-apikey"
              type="text"
              bind:value={localApiKeyEnv}
              placeholder="e.g. OPENAI_API_KEY"
            />
          </label>
          <div class="local-form-actions">
            <button onclick={verifyLocal} disabled={wizBusy || !localBaseUrl}>
              {wizBusy ? 'Verifying…' : 'Verify Connection'}
            </button>
            {#if localVerified}
              <button class="btn-primary" onclick={addLocalProvider}>
                Add Provider
              </button>
            {/if}
          </div>
        </div>

      {:else if wizStep === 'verifying'}
        <h3>Verifying…</h3>
        <div class="verify-spinner"><span class="spinner"></span></div>

      {:else if wizStep === 'done'}
        <h3>Provider Added</h3>
        <p class="wizard-done-msg">{wizMsg}</p>
        {#if copilotModels.length > 0 || localModels.length > 0}
          <div class="available-models">
            <span class="available-models-label">Available models:</span>
            <div class="model-chips">
              {#each (copilotModels.length > 0 ? copilotModels : localModels) as m}
                <span class="model-chip">{m}</span>
              {/each}
            </div>
          </div>
        {/if}
        <div class="wizard-done-actions">
          <button onclick={closeWizard}>Close</button>
          <button class="btn-primary" onclick={() => { closeWizard(); saveAll(); }}>Save &amp; Close</button>
        </div>
      {/if}

      {#if wizError}
        <div class="wizard-error">{wizError}</div>
      {/if}
      {#if wizMsg && wizStep !== 'done' && wizStep !== 'copilot-auth'}
        <div class="wizard-info">{wizMsg}</div>
      {/if}
    </div>
  </div>
{/if}

<!-- ============================================================ -->
<!-- Main content                                                 -->
<!-- ============================================================ -->

{#if loading}
  <div class="loading-state"><span class="spinner"></span> Loading providers…</div>
{:else}

  {#if !hasProviders}
    <!-- Empty state: prominent setup prompt -->
    <Card label="Get Started">
      <div class="empty-hero">
        <div class="empty-icon">🔌</div>
        <h3>No providers configured</h3>
        <p>Add an LLM provider to power OpenBaD's cognitive systems.</p>
        <button class="btn-primary btn-lg" onclick={openWizard}>
          Setup Provider
        </button>
      </div>
    </Card>
  {/if}

  <div class="grid" class:has-providers={hasProviders}>
    <!-- Provider list -->
    <Card label="Registered Providers">
      {#if providers.length === 0}
        <p class="empty">No providers registered yet.</p>
      {:else}
        <div class="provider-list">
          {#each providers as p, i}
            <div class="provider-item">
              <span class="health-dot" style="background:{healthColor(p)}"></span>
              <div class="provider-info">
                <span class="provider-name">{providerDisplayName(p)}</span>
                <span class="provider-model">{p.model || '(auto)'}</span>
              </div>
              <span class="badge" class:verified={p.verified}>{p.verified ? '✓ Verified' : '? Unverified'}</span>
              <button class="btn-icon btn-remove" onclick={() => removeProvider(i)} aria-label="Remove provider" title="Remove provider">&times;</button>
            </div>
          {/each}
        </div>
      {/if}
      <button class="btn-add" onclick={openWizard}>+ Add Provider</button>
    </Card>

    <!-- Cortisol -->
    <Card label="Provider Stress (Cortisol)">
      <div class="cortisol-section">
        <div class="cortisol-header">
          <span class="cortisol-val" style="color:{cortisolColor(cortisol)}">{(cortisol * 100).toFixed(0)}%</span>
        </div>
        <div class="cortisol-bar-bg">
          <div class="cortisol-fill" style="width:{cortisol * 100}%; background:{cortisolColor(cortisol)}"></div>
        </div>
        <p class="hint">High cortisol triggers fallback chain escalation.</p>
      </div>
    </Card>

    <!-- System assignments -->
    <div class="full-width">
      <Card label="System Assignments">
        <div class="systems-grid">
          {#each COGNITIVE_SYSTEMS as sys}
            <div class="sys-row">
              <div class="sys-label-wrap">
                <span class="sys-icon">
                  {#if sys === 'chat'}💬{:else if sys === 'reasoning'}🧠{:else if sys === 'reactions'}⚡{:else}😴{/if}
                </span>
                <span class="sys-label">{sys}</span>
              </div>
              <input
                type="text"
                placeholder="Provider name"
                value={systems[sys]?.provider ?? ''}
                oninput={(e: Event) => setSystem(sys, 'provider', (e.target as HTMLInputElement).value)}
              />
              <input
                type="text"
                placeholder="Model name"
                value={systems[sys]?.model ?? ''}
                oninput={(e: Event) => setSystem(sys, 'model', (e.target as HTMLInputElement).value)}
              />
            </div>
          {/each}
        </div>
      </Card>
    </div>

    <!-- Fallback chain -->
    <div class="full-width">
      <Card label="Fallback Chain">
        <p class="hint">Drag to reorder. First entry is tried first on provider failure.</p>
        {#if fallbackChain.length === 0}
          <p class="empty">No fallback entries configured.</p>
        {:else}
          <div class="fallback-list">
            {#each fallbackChain as entry, i}
              <div
                role="listitem"
                class="fallback-item"
                class:dragging={dragIdx === i}
                draggable="true"
                ondragstart={() => dragStart(i)}
                ondragover={dragOver}
                ondrop={() => drop(i)}
              >
                <span class="fallback-rank">{i + 1}</span>
                <span class="fallback-grip">⠿</span>
                <span class="fallback-name">{entry.provider}</span>
                <span class="fallback-model">{entry.model}</span>
              </div>
            {/each}
          </div>
        {/if}
      </Card>
    </div>
  </div>

  <!-- Actions -->
  <div class="actions-bar">
    <button class="btn-primary" onclick={saveAll} disabled={!dirty || saving}>
      {saving ? 'Saving…' : 'Save Changes'}
    </button>
    {#if statusMsg}
      <span class="status-msg">{statusMsg}</span>
    {/if}
  </div>
{/if}

<style>
  /* ---- Layout ---- */
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  .grid.has-providers { margin-top: 0; }
  .full-width { grid-column: 1 / -1; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

  .loading-state {
    display: flex; align-items: center; gap: 0.75rem; justify-content: center;
    padding: 3rem; color: var(--text-dim);
  }

  /* ---- Empty hero ---- */
  .empty-hero {
    display: flex; flex-direction: column; align-items: center; text-align: center;
    padding: 2rem 1rem; gap: 0.75rem;
  }
  .empty-icon { font-size: 3rem; }
  .empty-hero h3 { margin: 0; font-size: 1.2rem; }
  .empty-hero p { color: var(--text-dim); margin: 0; }

  /* ---- Provider list ---- */
  .provider-list { display: flex; flex-direction: column; gap: 0.5rem; }
  .provider-item {
    display: flex; align-items: center; gap: 0.75rem;
    padding: 0.6rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  .health-dot {
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
    box-shadow: 0 0 6px currentColor;
  }
  .provider-info { display: flex; flex-direction: column; flex: 1; }
  .provider-name { font-weight: 600; font-size: 0.9rem; }
  .provider-model { font-size: 0.8rem; color: var(--text-dim); }
  .badge {
    font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: var(--radius-sm);
    background: rgba(243, 139, 168, 0.15); color: var(--red); white-space: nowrap;
  }
  .badge.verified { background: rgba(166, 227, 161, 0.15); color: var(--green); }
  .empty { color: var(--text-dim); font-size: 0.9rem; }

  .btn-add {
    margin-top: 0.75rem; background: transparent; border: 1px dashed var(--border);
    color: var(--text-sub); padding: 0.5rem; border-radius: var(--radius-sm);
    cursor: pointer; width: 100%; transition: all 0.15s var(--ease);
  }
  .btn-add:hover { border-color: var(--accent); color: var(--accent); }

  .btn-icon {
    background: transparent; border: none; cursor: pointer; font-size: 1.2rem;
    color: var(--text-dim); padding: 0.2rem 0.4rem; border-radius: var(--radius-sm);
    line-height: 1; transition: all 0.15s var(--ease);
  }
  .btn-remove:hover { color: var(--red); background: rgba(243, 139, 168, 0.1); }

  /* ---- Buttons ---- */
  .btn-primary {
    background: var(--accent); color: var(--bg-base); font-weight: 600;
    border: none; border-radius: var(--radius-sm); padding: 0.5rem 1.25rem;
    cursor: pointer; transition: opacity 0.15s;
  }
  .btn-primary:hover:not(:disabled) { opacity: 0.85; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-lg { padding: 0.75rem 2rem; font-size: 1rem; }

  /* ---- Cortisol ---- */
  .cortisol-section { display: flex; flex-direction: column; gap: 0.6rem; }
  .cortisol-header { display: flex; align-items: center; gap: 0.5rem; }
  .cortisol-val { font-size: 1.8rem; font-weight: 700; }
  .cortisol-bar-bg { height: 8px; background: var(--bg-surface1); border-radius: 4px; overflow: hidden; }
  .cortisol-fill { height: 100%; border-radius: 4px; transition: width 0.4s var(--ease); }

  /* ---- Systems ---- */
  .systems-grid { display: flex; flex-direction: column; gap: 0.6rem; }
  .sys-row {
    display: flex; gap: 0.75rem; align-items: center;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  .sys-label-wrap { display: flex; align-items: center; gap: 0.4rem; width: 8rem; flex-shrink: 0; }
  .sys-icon { font-size: 1rem; }
  .sys-label { font-weight: 600; font-size: 0.85rem; text-transform: capitalize; }
  .sys-row input { flex: 1; min-width: 0; }

  /* ---- Fallback ---- */
  .fallback-list { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 0.5rem; }
  .fallback-item {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
    cursor: grab; border: 1px solid transparent; transition: all 0.15s var(--ease);
  }
  .fallback-item:hover { border-color: var(--bg-surface2); }
  .fallback-item.dragging { opacity: 0.4; }
  .fallback-rank {
    width: 1.5rem; height: 1.5rem; display: flex; align-items: center; justify-content: center;
    background: var(--bg-surface2); border-radius: 50%; font-size: 0.75rem; font-weight: 700; flex-shrink: 0;
  }
  .fallback-grip { color: var(--text-dim); }
  .fallback-name { font-weight: 600; }
  .fallback-model { color: var(--text-dim); font-size: 0.85rem; }

  .hint { font-size: 0.8rem; color: var(--text-dim); }

  /* ---- Actions bar ---- */
  .actions-bar {
    display: flex; gap: 1rem; align-items: center; margin-top: 1.25rem;
    padding-top: 1rem; border-top: 1px solid var(--border);
  }
  .status-msg { font-size: 0.85rem; color: var(--text-sub); }

  /* ---- Wizard overlay ---- */
  .wizard-overlay {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(0, 0, 0, 0.6); backdrop-filter: blur(4px);
    display: flex; align-items: center; justify-content: center;
    padding: 1rem;
  }
  .wizard-panel {
    background: var(--bg-surface0); border: 1px solid var(--border);
    border-radius: var(--radius-md, 12px); width: 100%; max-width: 520px;
    padding: 2rem; position: relative; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  .wizard-close {
    position: absolute; top: 0.75rem; right: 0.75rem;
    background: transparent; border: none; font-size: 1.5rem;
    color: var(--text-dim); cursor: pointer; line-height: 1;
  }
  .wizard-close:hover { color: var(--text); }
  .wizard-panel h3 { margin: 0 0 0.25rem; font-size: 1.15rem; }
  .wizard-subtitle { color: var(--text-dim); margin: 0 0 1.25rem; font-size: 0.9rem; }

  .wizard-error {
    margin-top: 1rem; padding: 0.6rem 0.75rem; border-radius: var(--radius-sm);
    background: rgba(243, 139, 168, 0.12); color: var(--red); font-size: 0.85rem;
  }
  .wizard-info {
    margin-top: 1rem; padding: 0.6rem 0.75rem; border-radius: var(--radius-sm);
    background: rgba(137, 180, 250, 0.12); color: var(--blue); font-size: 0.85rem;
  }

  /* ---- Wizard: type picker ---- */
  .wizard-options { display: flex; flex-direction: column; gap: 0.75rem; }
  .wizard-option {
    display: flex; align-items: center; gap: 1rem;
    padding: 1rem; background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius-sm); cursor: pointer; text-align: left;
    transition: all 0.15s var(--ease); color: var(--text);
  }
  .wizard-option:hover:not(:disabled) { border-color: var(--accent); background: var(--bg-surface2); }
  .wizard-option:disabled { opacity: 0.5; cursor: wait; }
  .wizard-option-icon { flex-shrink: 0; color: var(--text-sub); }
  .wizard-option-label { font-weight: 600; font-size: 0.95rem; display: block; }
  .wizard-option-desc { font-size: 0.8rem; color: var(--text-dim); display: block; margin-top: 0.15rem; }

  /* ---- Wizard: Copilot auth ---- */
  .copilot-steps { display: flex; flex-direction: column; gap: 1.25rem; }
  .copilot-code-box {
    display: flex; flex-direction: column; align-items: center; gap: 0.4rem;
    padding: 1.25rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
    border: 1px solid var(--border);
  }
  .copilot-code-label { font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
  .copilot-code { font-size: 2rem; font-weight: 700; font-family: monospace; letter-spacing: 0.15em; color: var(--accent); }
  .copilot-instructions {
    margin: 0; padding-left: 1.5rem; font-size: 0.9rem; color: var(--text-sub);
    display: flex; flex-direction: column; gap: 0.4rem;
  }
  .copilot-instructions a { color: var(--accent); text-decoration: underline; }
  .copilot-status {
    display: flex; align-items: center; gap: 0.6rem;
    font-size: 0.85rem; color: var(--text-dim);
  }

  /* ---- Wizard: Local form ---- */
  .local-form { display: flex; flex-direction: column; gap: 1rem; }
  .local-form label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; font-weight: 600; }
  .local-form input {
    padding: 0.5rem 0.65rem; background: var(--bg-surface1); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 0.9rem;
  }
  .local-form input:focus { border-color: var(--accent); outline: none; }
  .optional { font-weight: 400; color: var(--text-dim); }
  .local-form-actions { display: flex; gap: 0.75rem; margin-top: 0.5rem; }

  /* ---- Model chips ---- */
  .model-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.35rem; }
  .model-chip {
    font-size: 0.75rem; padding: 0.2rem 0.55rem; border-radius: 999px;
    background: var(--bg-surface2); color: var(--text-sub); border: 1px solid var(--border);
    cursor: pointer; transition: all 0.15s var(--ease);
  }
  .model-chip.selected, .model-chip:hover { border-color: var(--accent); color: var(--accent); background: rgba(137, 180, 250, 0.1); }

  .available-models { margin-top: 1rem; }
  .available-models-label { font-size: 0.8rem; color: var(--text-dim); display: block; margin-bottom: 0.35rem; }

  /* ---- Wizard done ---- */
  .wizard-done-msg { color: var(--green); font-size: 0.95rem; margin: 0.5rem 0 0; }
  .wizard-done-actions { display: flex; gap: 0.75rem; margin-top: 1.25rem; }

  /* ---- Spinner ---- */
  .spinner {
    display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  .verify-spinner { display: flex; justify-content: center; padding: 2rem; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
