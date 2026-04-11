<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
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
    api_key?: string;
    has_api_key?: boolean;
    api_key_env: string;
    timeout_ms: number;
    enabled: boolean;
    verified?: boolean;
    models?: string[];
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

  interface SupportedProvider {
    provider_type: string;
    name: string;
    label: string;
    auth: 'api_key' | 'device_code' | 'local';
    base_url: string;
    api_key_env: string;
    default_model: string;
  }

  interface SetupStatus {
    first_run: boolean;
    provider_ready: boolean;
    chat_assignment_ready: boolean;
    configured_provider_count: number;
    missing: string[];
    redirect_to: string;
    supported_providers: SupportedProvider[];
  }

  interface SystemsData {
    systems: Record<string, SystemAssignment>;
    fallback_chain: FallbackEntry[];
    providers: { name: string; model: string }[];
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
  }

  type WizardStep = 'closed' | 'pick-type' | 'copilot-auth' | 'provider-form' | 'done';

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
  let setupStatus = $state<SetupStatus | null>(null);
  let supportedProviders = $state<SupportedProvider[]>([]);

  // Wizard state
  let wizStep: WizardStep = $state('closed');
  let wizMsg = $state('');
  let wizError = $state('');
  let wizBusy = $state(false);
  let selectedProviderType = $state('');

  // Copilot wizard
  let copilotFlowId = $state('');
  let copilotUserCode = $state('');
  let copilotVerifyUri = $state('');
  let copilotModels: string[] = $state([]);

  // Generic provider wizard
  let providerBaseUrl = $state('');
  let providerModel = $state('');
  let providerApiKey = $state('');
  let providerApiKeyEnv = $state('');
  let providerVerified = $state(false);
  let providerModels: string[] = $state([]);
  let providerLatency = $state(0);

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
      setupStatus = await apiGet<SetupStatus>('/api/setup-status');
      supportedProviders = setupStatus.supported_providers ?? [];

      const pData = await apiGet<ProvidersData>('/api/providers');
      providers = pData.providers ?? [];
      defaultProvider = pData.default_provider ?? '';

      const sData = await apiGet<SystemsData>('/api/systems');
      systems = sData.systems ?? {};
      fallbackChain = sData.fallback_chain ?? [];

      // Pre-fetch models for any providers already assigned to systems
      const assignedProviders = new Set(
        Object.values(systems).map(a => a.provider).filter(Boolean)
      );
      for (const pName of assignedProviders) {
        fetchModelsForProvider(pName);
      }

      const autoOpenWizard = $page.url.searchParams.get('wizard') === '1';
      if ((autoOpenWizard || (setupStatus?.first_run ?? false)) && wizStep === 'closed') {
        openWizard();
      }
    } catch (e) {
      statusMsg = `Load failed: ${e}`;
    } finally {
      loading = false;
    }
  }

  onMount(() => { load(); });

  // ----------------------------------------------------------------
  // Persist providers to backend
  // ----------------------------------------------------------------

  async function persistProviders(): Promise<void> {
    try {
      const result = await apiPut<ProvidersData>('/api/providers', {
        enabled: true,
        default_provider: defaultProvider || (providers[0]?.name ?? ''),
        providers,
      });
      providers = result.providers ?? providers;
      defaultProvider = result.default_provider ?? defaultProvider;
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    }
  }

  // ----------------------------------------------------------------
  // Save systems + fallback
  // ----------------------------------------------------------------

  async function saveSystems(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/systems', {
        systems,
        fallback_chain: fallbackChain,
      });
      dirty = false;
      statusMsg = 'Saved.';
      setTimeout(() => { if (statusMsg === 'Saved.') statusMsg = ''; }, 2000);
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  // ----------------------------------------------------------------
  // Remove provider
  // ----------------------------------------------------------------

  async function removeProvider(idx: number): Promise<void> {
    const removed = providers[idx];
    providers = providers.filter((_, i) => i !== idx);
    if (defaultProvider === removed.name && providers.length > 0) {
      defaultProvider = providers[0].name;
    } else if (providers.length === 0) {
      defaultProvider = '';
    }
    await persistProviders();
  }

  // ----------------------------------------------------------------
  // Wizard: pick type
  // ----------------------------------------------------------------

  function openWizard(): void {
    wizStep = 'pick-type';
    wizMsg = '';
    wizError = '';
  }

  function providerSpec(providerType: string): SupportedProvider | undefined {
    return supportedProviders.find((entry) => entry.provider_type === providerType);
  }

  function beginProviderWizard(providerType: string): void {
    selectedProviderType = providerType;
    const spec = providerSpec(providerType);
    providerBaseUrl = spec?.base_url ?? '';
    providerModel = spec?.default_model ?? '';
    providerApiKey = '';
    providerApiKeyEnv = spec?.api_key_env ?? '';
    providerVerified = false;
    providerModels = [];
    providerLatency = 0;
    wizError = '';
    wizMsg = '';

    if (providerType === 'github-copilot') {
      void startCopilotFlow();
      return;
    }

    wizStep = 'provider-form';
  }

  function closeWizard(): void {
    wizStep = 'closed';
    wizMsg = '';
    wizError = '';
    wizBusy = false;
    selectedProviderType = '';
    providerBaseUrl = '';
    providerModel = '';
    providerApiKey = '';
    providerApiKeyEnv = '';
    providerVerified = false;
    providerModels = [];
    providerLatency = 0;
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
      wizStep = 'copilot-auth';
      wizMsg = res.message;
    } catch (e) {
      wizError = `Failed to start Copilot auth: ${e}`;
    } finally {
      wizBusy = false;
    }
  }

  async function completeCopilotAuth(): Promise<void> {
    if (!copilotFlowId) {
      wizError = 'Start GitHub sign-in first.';
      return;
    }
    wizBusy = true;
    wizError = '';
    wizMsg = 'Checking GitHub authorization state…';
    try {
      const res = await apiPost<CopilotCompleteResult>('/api/providers/copilot/complete', {
        flow_id: copilotFlowId,
      });
      if (res.pending) {
        wizMsg = res.message;
        wizBusy = false;
        return;
      }
      if (res.authorized) {
        copilotModels = res.models ?? [];
        if (res.provider) {
          const entry: ProviderEntry = {
            ...res.provider,
            api_key: '',
            verified: true,
            models: copilotModels,
          };
          providers = [...providers, entry];
          if (!defaultProvider) defaultProvider = entry.name;
          await persistProviders();
          await ensureSuggestedAssignments();
        }
        wizStep = 'done';
        wizMsg = res.message;
      } else {
        wizError = res.message || 'Authorization failed.';
      }
    } catch (e) {
      wizError = `Authorization check failed: ${e}`;
    } finally {
      wizBusy = false;
    }
  }

  async function copyCopilotCode(): Promise<void> {
    if (!copilotUserCode) return;
    try {
      await navigator.clipboard.writeText(copilotUserCode);
      wizMsg = 'Code copied to clipboard.';
    } catch {
      wizMsg = 'Clipboard copy failed. Copy the code manually.';
    }
  }

  // ----------------------------------------------------------------
  // Wizard: API-key or local provider verification
  // ----------------------------------------------------------------

  async function verifyProvider(): Promise<void> {
    if (!selectedProviderType) {
      wizError = 'Choose a provider first.';
      return;
    }

    wizBusy = true;
    wizError = '';
    providerVerified = false;
    try {
      const res = await apiPost<VerifyResult>('/api/providers/verify', {
        provider_type: selectedProviderType,
        base_url: providerBaseUrl,
        model: providerModel,
        api_key: providerApiKey,
        api_key_env: providerApiKeyEnv,
        timeout_ms: 30000,
      });
      if (res.available) {
        providerVerified = true;
        providerModels = res.models ?? [];
        providerLatency = res.latency_ms ?? 0;
        wizMsg = `Verified — ${res.models_available} model(s) available (${res.latency_ms?.toFixed(0)}ms)`;
        if (!providerModel && providerModels.length > 0) {
          providerModel = providerModels[0];
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

  async function addVerifiedProvider(): Promise<void> {
    const spec = providerSpec(selectedProviderType);
    if (!spec) {
      wizError = 'Choose a provider first.';
      return;
    }

    const entry: ProviderEntry = {
      name: spec.name,
      base_url: providerBaseUrl,
      model: providerModel,
      api_key: providerApiKey,
      has_api_key: providerApiKey.length > 0,
      api_key_env: providerApiKeyEnv,
      timeout_ms: 30000,
      enabled: true,
      verified: providerVerified,
      models: providerModels,
    };
    providers = [...providers.filter((provider) => provider.name !== entry.name), entry];
    if (!defaultProvider) defaultProvider = entry.name;
    await persistProviders();
    await ensureSuggestedAssignments();
    wizStep = 'done';
    wizMsg = `Added ${spec.label} (${providerModel || 'default model'})`;
  }

  // ----------------------------------------------------------------
  // System assignment helpers
  // ----------------------------------------------------------------

  // Cache of fetched models per provider name
  let providerModelsCache: Record<string, string[]> = $state({});
  let modelsFetching: Record<string, boolean> = $state({});

  async function fetchModelsForProvider(providerName: string): Promise<void> {
    if (!providerName || providerModelsCache[providerName] || modelsFetching[providerName]) return;
    modelsFetching = { ...modelsFetching, [providerName]: true };
    try {
      const res = await apiGet<{ provider: string; models: { model_id: string }[] }>(
        `/api/providers/${encodeURIComponent(providerName)}/models`
      );
      providerModelsCache = {
        ...providerModelsCache,
        [providerName]: res.models.map(m => m.model_id),
      };
    } catch {
      // Fallback to local data if endpoint fails
      const p = providers.find(pp => pp.name === providerName);
      const fallback: string[] = [];
      if (p?.models && p.models.length > 0) fallback.push(...p.models);
      if (p?.model && !fallback.includes(p.model)) fallback.push(p.model);
      providerModelsCache = { ...providerModelsCache, [providerName]: fallback };
    } finally {
      modelsFetching = { ...modelsFetching, [providerName]: false };
    }
  }

  async function setSystemProvider(sys: string, providerName: string): Promise<void> {
    if (!systems[sys]) systems[sys] = { provider: '', model: '' };
    systems[sys].provider = providerName;
    systems[sys].model = '';
    dirty = true;
    if (providerName) {
      await fetchModelsForProvider(providerName);
      // Auto-select first model
      const models = providerModelsCache[providerName] ?? [];
      if (models.length > 0) {
        systems[sys].model = models[0];
      }
    }
    await saveSystems();
  }

  async function setSystemModel(sys: string, model: string): Promise<void> {
    if (!systems[sys]) systems[sys] = { provider: '', model: '' };
    systems[sys].model = model;
    dirty = true;
    await saveSystems();
  }

  function modelsForEntry(entry: ProviderEntry): string[] {
    const models = [...(entry.models ?? [])];
    if (entry.model && !models.includes(entry.model)) {
      models.unshift(entry.model);
    }
    return models.filter(Boolean);
  }

  function assignmentFromProvider(entry: ProviderEntry | undefined): SystemAssignment {
    if (!entry) return { provider: '', model: '' };
    const models = modelsForEntry(entry);
    return {
      provider: entry.name,
      model: models[0] ?? '',
    };
  }

  function modelScore(model: string): number {
    const value = model.toLowerCase();
    if (value.includes('opus') || value.includes('gpt-5') || value.includes('sonnet-4')) return 5;
    if (value.includes('sonnet') || value.includes('gpt-4') || value.includes('large')) return 4;
    if (value.includes('mini') || value.includes('haiku') || value.includes('small') || value.includes('8b') || value.includes('instant')) return 2;
    if (value.includes('tiny') || value.includes('3b') || value.includes('1b')) return 1;
    return 3;
  }

  function buildSuggestedAssignments(): Record<string, SystemAssignment> {
    const enabled = providers.filter((provider) => provider.enabled);
    const primary = enabled.find((provider) => !['ollama', 'custom'].includes(provider.name)) ?? enabled[0];
    const mostCapable = [...enabled].sort((left, right) => {
      return modelScore((modelsForEntry(right)[0] ?? right.model)) - modelScore((modelsForEntry(left)[0] ?? left.model));
    })[0] ?? primary;
    const fastest = [...enabled].sort((left, right) => {
      return modelScore((modelsForEntry(left)[0] ?? left.model)) - modelScore((modelsForEntry(right)[0] ?? right.model));
    })[0] ?? primary;
    const local = enabled.find((provider) => provider.name === 'ollama')
      ?? enabled.find((provider) => provider.name === 'custom' && provider.base_url.includes('localhost'))
      ?? fastest
      ?? primary;

    return {
      chat: assignmentFromProvider(primary),
      reasoning: assignmentFromProvider(mostCapable),
      reactions: assignmentFromProvider(fastest),
      sleep: assignmentFromProvider(local),
    };
  }

  async function ensureSuggestedAssignments(): Promise<void> {
    const suggested = buildSuggestedAssignments();
    let changed = false;

    for (const systemName of COGNITIVE_SYSTEMS) {
      const current = systems[systemName] ?? { provider: '', model: '' };
      if (current.provider && current.model) continue;
      const next = suggested[systemName];
      if (!next?.provider || !next?.model) continue;
      systems[systemName] = next;
      changed = true;
    }

    if (fallbackChain.length === 0) {
      fallbackChain = [suggested.sleep, suggested.reactions, suggested.chat]
        .filter((entry): entry is SystemAssignment => Boolean(entry?.provider && entry?.model))
        .map((entry) => ({ provider: entry.provider, model: entry.model }));
      changed = changed || fallbackChain.length > 0;
    }

    if (changed) {
      dirty = true;
      await saveSystems();
    }
  }

  function modelsForProvider(providerName: string): string[] {
    if (!providerName) return [];
    // Kick off fetch if not cached yet
    if (!providerModelsCache[providerName] && !modelsFetching[providerName]) {
      fetchModelsForProvider(providerName);
    }
    return providerModelsCache[providerName] ?? [];
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

  async function drop(targetIdx: number): Promise<void> {
    if (dragIdx === null || dragIdx === targetIdx) return;
    const item = fallbackChain[dragIdx];
    const updated = [...fallbackChain];
    updated.splice(dragIdx, 1);
    updated.splice(targetIdx, 0, item);
    fallbackChain = updated;
    dragIdx = null;
    dirty = true;
    await saveSystems();
  }

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------

  function healthColor(entry: ProviderEntry): string {
    if (entry.verified) return 'var(--green)';
    return 'var(--yellow)';
  }

  function cortisolColor(level: number): string {
    if (level < 0.3) return 'var(--green)';
    if (level < 0.7) return 'var(--yellow)';
    return 'var(--red)';
  }

  function providerDisplayName(p: ProviderEntry): string {
    if (p.name === 'github-copilot') return 'GitHub Copilot';
    if (p.name === 'openai') return 'OpenAI';
    if (p.name === 'anthropic') return 'Anthropic';
    if (p.name === 'openrouter') return 'OpenRouter';
    if (p.name === 'groq') return 'Groq';
    if (p.name === 'mistral') return 'Mistral';
    if (p.name === 'xai') return 'xAI';
    if (p.name === 'ollama') return 'Ollama';
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
          {#each supportedProviders as provider}
            <button class="wizard-option" onclick={() => beginProviderWizard(provider.provider_type)} disabled={wizBusy}>
              <span class="wizard-option-label">{provider.label}</span>
              <span class="wizard-option-desc">
                {#if provider.auth === 'device_code'}Authenticate with your GitHub account via device code
                {:else if provider.auth === 'local'}Verify a local endpoint and auto-discover models
                {:else}Validate an API key and discover available models{/if}
              </span>
            </button>
          {/each}
        </div>

      {:else if wizStep === 'copilot-auth'}
        <!-- Step 2a: Copilot device-code flow -->
        <h3>Authorize GitHub Copilot</h3>
        <p class="wizard-subtitle">Complete these steps to link your Copilot subscription.</p>
        <div class="copilot-steps">
          <div class="copilot-code-box">
            <span class="copilot-code-label">Verification code</span>
            <span class="copilot-code">{copilotUserCode}</span>
          </div>
          <ol class="copilot-instructions">
            <li>
              Open
              <a href={copilotVerifyUri} target="_blank" rel="noopener noreferrer">{copilotVerifyUri}</a>
            </li>
            <li>Enter the code above</li>
            <li>Authorize the application</li>
            <li>Return here and click the button below</li>
          </ol>
          <div class="copilot-actions">
            <button class="btn-ghost" onclick={copyCopilotCode}>Copy code</button>
            <button class="btn-ghost" onclick={() => window.open(copilotVerifyUri, '_blank', 'noopener,noreferrer')}>
              Open GitHub
            </button>
            <button class="btn-primary" onclick={completeCopilotAuth} disabled={wizBusy}>
              {wizBusy ? 'Checking…' : 'I entered the code'}
            </button>
          </div>
          {#if wizMsg && !wizError}
            <p class="copilot-auth-msg">{wizMsg}</p>
          {/if}
        </div>

      {:else if wizStep === 'provider-form'}
        {@const spec = providerSpec(selectedProviderType)}
        <h3>{spec?.label ?? 'Provider Setup'}</h3>
        <p class="wizard-subtitle">
          {#if spec?.auth === 'local'}Verify a local endpoint and pull available models.
          {:else}Validate the provider and choose a model before saving it.{/if}
        </p>
        <div class="local-form">
          <label for="wiz-base-url">
            Base URL
            <input
              id="wiz-base-url"
              type="url"
              bind:value={providerBaseUrl}
              placeholder={spec?.base_url ?? 'https://api.example.com'}
            />
          </label>
          {#if spec?.auth === 'api_key'}
            <label for="wiz-api-key">
              API Key
              <input
                id="wiz-api-key"
                type="password"
                bind:value={providerApiKey}
                placeholder="Paste API key"
              />
            </label>
          {/if}
          <label for="wiz-model">
            Model
            <input
              id="wiz-model"
              type="text"
              bind:value={providerModel}
              placeholder={spec?.default_model || 'Leave blank to auto-detect'}
            />
            {#if providerModels.length > 0}
              <div class="model-chips">
                {#each providerModels as m}
                  <button
                    class="model-chip"
                    class:selected={providerModel === m}
                    onclick={() => { providerModel = m; }}
                  >{m}</button>
                {/each}
              </div>
            {/if}
          </label>
          <label for="wiz-apikey">
            API Key Env Var <span class="optional">(optional fallback)</span>
            <input
              id="wiz-apikey"
              type="text"
              bind:value={providerApiKeyEnv}
              placeholder={spec?.api_key_env || 'e.g. OPENAI_API_KEY'}
            />
          </label>
          <div class="local-form-actions">
            <button onclick={verifyProvider} disabled={wizBusy || !providerBaseUrl}>
              {wizBusy ? 'Verifying…' : 'Verify Connection'}
            </button>
            {#if providerVerified}
              <button class="btn-primary" onclick={addVerifiedProvider}>
                Add Provider
              </button>
            {/if}
          </div>
          {#if providerVerified}
            <p class="wizard-subtitle">Latency: {providerLatency.toFixed(0)}ms</p>
          {/if}
        </div>

      {:else if wizStep === 'done'}
        <h3>Provider Added</h3>
        <p class="wizard-done-msg">{wizMsg}</p>
        {#if copilotModels.length > 0 || providerModels.length > 0}
          <div class="available-models">
            <span class="available-models-label">Available models:</span>
            <div class="model-chips">
              {#each (copilotModels.length > 0 ? copilotModels : providerModels) as m}
                <span class="model-chip">{m}</span>
              {/each}
            </div>
          </div>
        {/if}
        <div class="wizard-done-actions">
          <button class="btn-primary" onclick={closeWizard}>Done</button>
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
                <span class="provider-status">{p.verified ? 'verified' : 'unverified'}</span>
              </div>
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
          <span class="cortisol-title">Cortisol Level</span>
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
        <p class="hint">Assign a provider and model to each cognitive system.</p>
        <!-- placeholder="provider" -->
        <!-- placeholder="model" -->
        <div class="systems-grid">
          {#each COGNITIVE_SYSTEMS as sys}
            {@const assignment = systems[sys] ?? { provider: '', model: '' }}
            {@const availableModels = modelsForProvider(assignment.provider)}
            <div class="sys-row">
              <div class="sys-label-wrap">
                <span class="sys-icon">
                  {#if sys === 'chat'}💬{:else if sys === 'reasoning'}🧠{:else if sys === 'reactions'}⚡{:else}😴{/if}
                </span>
                <span class="sys-label">{sys}</span>
              </div>
              <select
                value={assignment.provider}
                onchange={(e: Event) => setSystemProvider(sys, (e.target as HTMLSelectElement).value)}
              >
                <option value="">— provider —</option>
                {#each providers as p}
                  <option value={p.name}>{providerDisplayName(p)}</option>
                {/each}
              </select>
              <select
                value={assignment.model}
                onchange={(e: Event) => setSystemModel(sys, (e.target as HTMLSelectElement).value)}
                disabled={!assignment.provider}
              >
                <option value="">— model —</option>
                {#each availableModels as m}
                  <option value={m}>{m}</option>
                {/each}
              </select>
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

  {#if statusMsg}
    <div class="actions-bar">
      <span class="status-msg">{statusMsg}</span>
    </div>
  {/if}
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
  .provider-status { font-size: 0.72rem; color: var(--text-sub); text-transform: uppercase; }
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

  .btn-ghost {
    background: transparent; border: 1px solid var(--border); color: var(--text-sub);
    padding: 0.4rem 0.9rem; border-radius: var(--radius-sm); cursor: pointer;
    font-size: 0.85rem; transition: all 0.15s var(--ease);
  }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }

  /* ---- Cortisol ---- */
  .cortisol-section { display: flex; flex-direction: column; gap: 0.6rem; }
  .cortisol-header { display: flex; align-items: center; gap: 0.5rem; }
  .cortisol-title { font-size: 0.85rem; color: var(--text-sub); }
  .cortisol-val { font-size: 1.8rem; font-weight: 700; }
  .cortisol-bar-bg { height: 8px; background: var(--bg-surface1); border-radius: 4px; overflow: hidden; }
  .cortisol-fill { height: 100%; border-radius: 4px; transition: width 0.4s var(--ease); }

  /* ---- Systems ---- */
  .systems-grid { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.5rem; }
  .sys-row {
    display: flex; gap: 0.75rem; align-items: center;
    padding: 0.5rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  .sys-label-wrap { display: flex; align-items: center; gap: 0.4rem; width: 8rem; flex-shrink: 0; }
  .sys-icon { font-size: 1rem; }
  .sys-label { font-weight: 600; font-size: 0.85rem; text-transform: capitalize; }
  .sys-row select {
    flex: 1; min-width: 0; padding: 0.4rem 0.5rem;
    background: var(--bg-surface0); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 0.85rem;
  }
  .sys-row select:disabled { opacity: 0.4; }
  .sys-row select:focus { border-color: var(--accent); outline: none; }

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

  .hint { font-size: 0.8rem; color: var(--text-dim); margin: 0 0 0.25rem; }

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
  .copilot-actions {
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
  }
  .copilot-auth-msg {
    font-size: 0.85rem; color: var(--text-dim); margin: 0;
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
  @keyframes spin { to { transform: rotate(360deg); } }

  .verify-spinner { display: flex; justify-content: center; padding: 2rem; }
</style>
