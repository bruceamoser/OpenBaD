const els = {
  status: document.getElementById('ws-status'),
  transportCaption: document.getElementById('transport-caption'),
  viewTitle: document.getElementById('view-title'),
  viewSubtitle: document.getElementById('view-subtitle'),
  navLinks: Array.from(document.querySelectorAll('.nav-link')),
  views: Array.from(document.querySelectorAll('.view-panel')),
  fsm: document.getElementById('fsm-state'),
  hormones: {
    dopamine: document.getElementById('h-dopamine'),
    adrenaline: document.getElementById('h-adrenaline'),
    cortisol: document.getElementById('h-cortisol'),
    endorphin: document.getElementById('h-endorphin'),
  },
  vitals: {
    cpu: document.getElementById('v-cpu'),
    cpuMeta: document.getElementById('v-cpu-meta'),
    memory: document.getElementById('v-memory'),
    memoryMeta: document.getElementById('v-memory-meta'),
    disk: document.getElementById('v-disk'),
    diskMeta: document.getElementById('v-disk-meta'),
    netTx: document.getElementById('v-net-tx'),
    netRx: document.getElementById('v-net-rx'),
    tokens: document.getElementById('v-tokens'),
    tier: document.getElementById('v-tier'),
  },
  inference: {
    provider: document.getElementById('i-provider'),
    model: document.getElementById('i-model'),
    health: document.getElementById('i-health'),
    p50: document.getElementById('i-p50'),
    p99: document.getElementById('i-p99'),
    lastTokens: document.getElementById('i-last-tokens'),
    lastLatency: document.getElementById('i-last-latency'),
  },
  log: document.getElementById('event-log'),
  providers: {
    configPath: document.getElementById('providers-config-path'),
    status: document.getElementById('providers-status'),
    enabled: document.getElementById('providers-enabled'),
    defaultProvider: document.getElementById('default-provider'),
    providerList: document.getElementById('provider-list'),
    addProvider: document.getElementById('add-provider'),
    wizard: document.getElementById('provider-wizard'),
    wizardTitle: document.getElementById('wizard-title'),
    wizardStatus: document.getElementById('wizard-status'),
    closeWizard: document.getElementById('close-provider-wizard'),
    wizardForm: document.getElementById('provider-wizard-form'),
    wizardType: document.getElementById('wizard-provider-type'),
    copilotFields: document.getElementById('wizard-copilot-fields'),
    localFields: document.getElementById('wizard-local-fields'),
    copilotStartAuth: document.getElementById('copilot-start-auth'),
    copilotCompleteAuth: document.getElementById('copilot-complete-auth'),
    copilotAuthPanel: document.getElementById('copilot-auth-panel'),
    copilotUserCode: document.getElementById('copilot-user-code'),
    copilotCopyCode: document.getElementById('copilot-copy-code'),
    copilotOpenGitHub: document.getElementById('copilot-open-github'),
    copilotAuthMessage: document.getElementById('copilot-auth-message'),
    baseUrl: document.getElementById('wizard-base-url'),
    apiKeyEnv: document.getElementById('wizard-api-key-env'),
    timeoutMs: document.getElementById('wizard-timeout-ms'),
    verify: document.getElementById('verify-provider'),
    save: document.getElementById('save-provider'),
    modelSelect: document.getElementById('wizard-model-select'),
  },
  systems: {
    status: document.getElementById('systems-status'),
    saveStatus: document.getElementById('systems-save-status'),
    saveBtn: document.getElementById('save-systems'),
    chainList: document.getElementById('fallback-chain-list'),
    chainAddProvider: document.getElementById('fallback-add-provider'),
    chainAddModel: document.getElementById('fallback-add-model'),
    chainAddBtn: document.getElementById('fallback-add-btn'),
  },
};

const viewMeta = {
  health: {
    title: 'Health',
    subtitle: 'Live runtime telemetry and subsystem health.',
  },
  chat: {
    title: 'Chat',
    subtitle: 'Operator conversation surface for the next integration step.',
  },
  providers: {
    title: 'Providers',
    subtitle: 'Verified providers and model access for the runtime.',
  },
  models: {
    title: 'Models',
    subtitle: 'Reserved for the upcoming model surface.',
  },
};

const wizardProviders = {
  'github-copilot': {
    label: 'GitHub Copilot',
  },
  'local-openai': {
    label: 'Local llama (OpenAI-compatible)',
  },
};

let activeSocket = null;
let reconnectTimer = null;
let activeEventSource = null;
let providerDrafts = [];
let activeTransport = 'offline';
let currentView = 'health';
let systemsData = { systems: {}, fallback_chain: [], providers: [] };
let fallbackCounts = { chat: 0, sleep: 0, reasoning: 0, reactions: 0 };
let wizardState = {
  open: false,
  editIndex: null,
  verifiedProvider: null,
  verifiedModels: [],
  copilotFlow: null,
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function providerLabel(provider) {
  if (provider.name === 'github-copilot') {
    return 'GitHub Copilot';
  }
  if (provider.name === 'custom') {
    return 'Local llama';
  }
  return provider.name || 'Provider';
}

function providerTypeFromDraft(provider) {
  if (provider.name === 'github-copilot') {
    return 'github-copilot';
  }
  return 'local-openai';
}

function setView(name) {
  currentView = name;
  for (const link of els.navLinks) {
    link.classList.toggle('active', link.dataset.viewTarget === name);
  }
  for (const view of els.views) {
    view.classList.toggle('hidden', view.dataset.view !== name);
  }
  els.viewTitle.textContent = viewMeta[name].title;
  els.viewSubtitle.textContent = viewMeta[name].subtitle;

  if (name === 'providers') {
    loadProvidersConfig();
    loadSystemsConfig();
  }
}

function logLine(text) {
  const div = document.createElement('div');
  div.className = 'log-line';
  div.textContent = text;
  els.log.prepend(div);
  while (els.log.children.length > 140) {
    els.log.removeChild(els.log.lastChild);
  }
}

function setOnline(online, transport = activeTransport) {
  activeTransport = online ? transport : 'offline';
  els.status.textContent = online ? transport : 'offline';
  els.status.classList.toggle('online', online);
  els.status.classList.toggle('offline', !online);
  els.transportCaption.textContent = online
    ? `Telemetry arriving over ${transport}`
    : 'Awaiting telemetry transport';
}

function markMetric(metricName) {
  const node = document.querySelector(`.pulse-target[data-metric="${metricName}"]`);
  if (!node) {
    return;
  }
  node.classList.add('flash');
  window.setTimeout(() => node.classList.remove('flash'), 450);
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) {
    return `${value} B`;
  }

  const units = ['KB', 'MB', 'GB', 'TB'];
  let remainder = value / 1024;
  let unitIndex = 0;
  while (remainder >= 1024 && unitIndex < units.length - 1) {
    remainder /= 1024;
    unitIndex += 1;
  }
  return `${remainder.toFixed(remainder >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatTimestamp() {
  return new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function updateFromEvent(topic, payload) {
  if (topic.startsWith('agent/endocrine/')) {
    const hormone = topic.split('/').pop();
    if (els.hormones[hormone]) {
      els.hormones[hormone].textContent = Number(payload.level ?? 0).toFixed(2);
    }
    return;
  }

  if (topic === 'agent/reflex/state') {
    els.fsm.textContent = payload.current_state || 'UNKNOWN';
    return;
  }

  if (topic === 'agent/telemetry/cpu') {
    els.vitals.cpu.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    els.vitals.cpuMeta.textContent = `load ${Number(payload.load_avg_1m || 0).toFixed(2)} at ${formatTimestamp()}`;
    markMetric('cpu');
    return;
  }

  if (topic === 'agent/telemetry/memory') {
    els.vitals.memory.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    els.vitals.memoryMeta.textContent = `${formatBytes(payload.used_bytes || 0)} used at ${formatTimestamp()}`;
    markMetric('memory');
    return;
  }

  if (topic === 'agent/telemetry/disk') {
    els.vitals.disk.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    els.vitals.diskMeta.textContent = `${formatBytes(payload.free_bytes || 0)} free at ${formatTimestamp()}`;
    markMetric('disk');
    return;
  }

  if (topic === 'agent/telemetry/network') {
    els.vitals.netTx.textContent = `TX ${formatBytes(payload.bytes_sent || 0)}`;
    els.vitals.netRx.textContent = `RX ${formatBytes(payload.bytes_recv || 0)}`;
    markMetric('network');
    return;
  }

  if (topic === 'agent/telemetry/tokens') {
    els.vitals.tokens.textContent = `${payload.tokens_used || 0}`;
    els.vitals.tier.textContent = payload.model_tier || '--';
    return;
  }

  if (topic === 'agent/cognitive/health') {
    els.inference.provider.textContent = `${Number(payload.configured_provider_count ?? 0)}`;
    els.inference.model.textContent = payload.model_id || '--';
    els.inference.health.textContent = payload.provider === 'inactive' || payload.model_id === 'none'
      ? 'inactive'
      : (payload.available ? 'up' : 'down');
    els.inference.p50.textContent = `${Number(payload.latency_p50 || 0).toFixed(1)}ms`;
    els.inference.p99.textContent = `${Number(payload.latency_p99 || 0).toFixed(1)}ms`;
    return;
  }

  if (topic === 'agent/cognitive/response') {
    els.inference.lastTokens.textContent = `${payload.tokens_used || 0}`;
    els.inference.lastLatency.textContent = `${Number(payload.latency_ms || 0).toFixed(1)}ms`;
    return;
  }

  if (topic === 'agent/cognitive/fallback') {
    const system = payload.system || '';
    if (system in fallbackCounts) {
      fallbackCounts[system]++;
      const badge = document.querySelector(`.fallback-badge[data-system="${system}"]`);
      if (badge) {
        badge.textContent = String(fallbackCounts[system]);
        badge.classList.remove('green', 'yellow', 'red');
        if (fallbackCounts[system] >= 5) badge.classList.add('red');
        else if (fallbackCounts[system] >= 2) badge.classList.add('yellow');
        else badge.classList.add('green');
      }
    }
  }
}

function handleEventMessage(raw) {
  try {
    const msg = JSON.parse(raw);
    if (msg.type === 'hello') {
      logLine(`[${msg.ts}] ${msg.message}`);
      return;
    }
    if (msg.type === 'event') {
      updateFromEvent(msg.topic || 'unknown/topic', msg.payload || {});
      logLine(`[${msg.ts}] ${msg.topic || 'unknown/topic'}`);
    }
  } catch {
    logLine('malformed transport payload received');
  }
}

function connectWebSocket() {
  if (activeSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(activeSocket.readyState)) {
    return;
  }
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
  activeSocket = ws;

  ws.addEventListener('open', () => {
    setOnline(true, 'websocket');
    logLine('socket connected');
  });

  ws.addEventListener('error', () => {
    logLine('socket error');
  });

  ws.addEventListener('close', (event) => {
    setOnline(false);
    if (activeSocket === ws) {
      activeSocket = null;
    }
    const reason = event.reason ? ` reason=${event.reason}` : '';
    logLine(`socket disconnected; code=${event.code}${reason}; retrying...`);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 1200);
  });

  ws.addEventListener('message', (event) => {
    handleEventMessage(event.data);
  });
}

function connectEventStream() {
  if (!window.EventSource) {
    connectWebSocket();
    return;
  }
  if (activeEventSource) {
    return;
  }

  const source = new EventSource('/events');
  activeEventSource = source;

  source.addEventListener('open', () => {
    setOnline(true, 'event-stream');
    logLine('event stream connected');
  });

  source.addEventListener('error', () => {
    setOnline(false);
    logLine(`event stream disconnected; state=${source.readyState}; waiting for reconnect...`);
    if (source.readyState === EventSource.CLOSED) {
      activeEventSource = null;
      connectWebSocket();
    }
  });

  source.onmessage = (event) => {
    handleEventMessage(event.data);
  };
}

function renderDefaultProviderOptions(selected) {
  els.providers.defaultProvider.innerHTML = '';
  if (providerDrafts.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No providers configured';
    option.selected = true;
    els.providers.defaultProvider.append(option);
    els.providers.defaultProvider.disabled = true;
    return;
  }

  els.providers.defaultProvider.disabled = false;
  for (const provider of providerDrafts) {
    const option = document.createElement('option');
    option.value = provider.name;
    option.textContent = providerLabel(provider);
    option.selected = provider.name === selected;
    els.providers.defaultProvider.append(option);
  }
}

function providerSummaryCard(provider, index) {
  const endpoint = provider.name === 'github-copilot' ? 'Managed by local Copilot auth' : (provider.base_url || 'endpoint not set');
  const envText = provider.api_key_env ? provider.api_key_env : 'none';
  return `
    <article class="provider-card provider-summary-card">
      <div class="provider-card-head">
        <strong>${escapeHtml(providerLabel(provider))}</strong>
        <div class="provider-card-actions">
          <button type="button" class="ghost-button" data-configure-provider="${index}">Configure</button>
          <button type="button" class="ghost-button" data-remove-provider="${index}">Remove</button>
        </div>
      </div>
      <div class="provider-summary-grid mono">
        <div><span>Model</span><strong>${escapeHtml(provider.model || '--')}</strong></div>
        <div><span>Endpoint</span><strong>${escapeHtml(endpoint)}</strong></div>
        <div><span>Auth env</span><strong>${escapeHtml(envText)}</strong></div>
        <div><span>Timeout</span><strong>${provider.timeout_ms || 30000} ms</strong></div>
      </div>
    </article>
  `;
}

function renderProviderList() {
  if (providerDrafts.length === 0) {
    els.providers.providerList.innerHTML = `
      <div class="empty-state">
        <strong>No providers configured</strong>
        <p>Use Add provider to walk through GitHub Copilot or a local OpenAI-compatible llama endpoint.</p>
      </div>
    `;
    return;
  }

  els.providers.providerList.innerHTML = providerDrafts
    .map((provider, index) => providerSummaryCard(provider, index))
    .join('');
}

function openWizard(editIndex = null) {
  wizardState.open = true;
  wizardState.editIndex = editIndex;
  wizardState.verifiedProvider = null;
  wizardState.verifiedModels = [];
  wizardState.copilotFlow = null;
  els.providers.save.disabled = true;
  els.providers.modelSelect.disabled = true;
  els.providers.modelSelect.innerHTML = '<option value="">Verify provider first</option>';
  els.providers.wizard.classList.remove('hidden');
  els.providers.copilotAuthPanel.classList.add('hidden');
  els.providers.copilotUserCode.textContent = '----';
  els.providers.copilotCompleteAuth.disabled = true;
  els.providers.copilotOpenGitHub.disabled = true;
  els.providers.copilotCopyCode.disabled = true;
  els.providers.copilotAuthMessage.textContent = 'No active Copilot authorization yet.';

  if (editIndex === null) {
    els.providers.wizardTitle.textContent = 'Add Provider';
    els.providers.wizardType.value = 'github-copilot';
    els.providers.baseUrl.value = 'http://127.0.0.1:11434';
    els.providers.apiKeyEnv.value = '';
    els.providers.timeoutMs.value = '30000';
    els.providers.wizardStatus.textContent = 'Choose a provider type to begin the setup walkthrough.';
  } else {
    const provider = providerDrafts[editIndex];
    els.providers.wizardTitle.textContent = `Configure ${providerLabel(provider)}`;
    els.providers.wizardType.value = providerTypeFromDraft(provider);
    els.providers.baseUrl.value = provider.base_url || 'http://127.0.0.1:11434';
    els.providers.apiKeyEnv.value = provider.api_key_env || '';
    els.providers.timeoutMs.value = String(provider.timeout_ms || 30000);
    els.providers.wizardStatus.textContent = 'Verify the provider again before saving updated settings.';
  }

  applyWizardType();
}

function closeWizard() {
  wizardState.open = false;
  wizardState.editIndex = null;
  wizardState.verifiedProvider = null;
  wizardState.verifiedModels = [];
  wizardState.copilotFlow = null;
  els.providers.wizard.classList.add('hidden');
}

function applyWizardType() {
  const type = els.providers.wizardType.value;
  const local = type === 'local-openai';
  els.providers.localFields.classList.toggle('hidden', !local);
  els.providers.copilotFields.classList.toggle('hidden', local);
  els.providers.verify.classList.toggle('hidden', !local);
}

async function loadProvidersConfig() {
  els.providers.status.textContent = 'Loading providers...';
  try {
    const response = await fetch('/api/providers');
    if (!response.ok) {
      throw new Error(`load failed (${response.status})`);
    }
    const data = await response.json();
    providerDrafts = Array.isArray(data.providers) ? data.providers : [];
    els.providers.configPath.textContent = data.config_path || 'config path unavailable';
    els.providers.enabled.checked = Boolean(data.enabled);
    renderDefaultProviderOptions(data.default_provider || providerDrafts[0]?.name || '');
    renderProviderList();
    els.providers.status.textContent = providerDrafts.length > 0
      ? 'Providers loaded.'
      : 'No providers configured yet.';
  } catch (error) {
    els.providers.status.textContent = `Unable to load providers: ${error.message}`;
  }
}

async function persistProvidersConfig(statusMessage) {
  const payload = {
    enabled: els.providers.enabled.checked,
    default_provider: els.providers.defaultProvider.value || '',
    providers: providerDrafts,
  };

  const response = await fetch('/api/providers', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`save failed (${response.status})`);
  }

  const data = await response.json();
  providerDrafts = Array.isArray(data.providers) ? data.providers : providerDrafts;
  renderDefaultProviderOptions(data.default_provider || providerDrafts[0]?.name || '');
  if (data.default_provider) {
    els.providers.defaultProvider.value = data.default_provider;
  }
  renderProviderList();
  els.providers.configPath.textContent = data.config_path || els.providers.configPath.textContent;
  els.providers.status.textContent = statusMessage;
}

function verificationPayload() {
  const payload = {
    provider_type: els.providers.wizardType.value,
    timeout_ms: Number(els.providers.timeoutMs.value || 30000),
  };

  if (payload.provider_type === 'local-openai') {
    payload.base_url = els.providers.baseUrl.value.trim();
    payload.api_key_env = els.providers.apiKeyEnv.value.trim();
  }

  return payload;
}

function populateModelChoices(models, preferredModel = '') {
  els.providers.modelSelect.innerHTML = '';
  const options = models.length > 0 ? models : [preferredModel].filter(Boolean);
  if (options.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No models returned';
    els.providers.modelSelect.append(option);
    els.providers.modelSelect.disabled = true;
    return;
  }

  for (const model of options) {
    const option = document.createElement('option');
    option.value = model;
    option.textContent = model;
    option.selected = model === preferredModel || (!preferredModel && model === options[0]);
    els.providers.modelSelect.append(option);
  }
  els.providers.modelSelect.disabled = false;
}

async function verifyWizardProvider() {
  if (els.providers.wizardType.value === 'github-copilot') {
    els.providers.wizardStatus.textContent = 'Use the Copilot sign-in flow below.';
    return;
  }
  els.providers.wizardStatus.textContent = 'Verifying provider access...';
  els.providers.save.disabled = true;
  try {
    const response = await fetch('/api/providers/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(verificationPayload()),
    });
    if (!response.ok) {
      throw new Error(`verify failed (${response.status})`);
    }
    const data = await response.json();
    wizardState.verifiedProvider = data.provider;
    wizardState.verifiedModels = Array.isArray(data.models) ? data.models : [];
    populateModelChoices(wizardState.verifiedModels, data.provider.model || '');
    els.providers.save.disabled = !data.available;
    els.providers.wizardStatus.textContent = data.message;
  } catch (error) {
    wizardState.verifiedProvider = null;
    wizardState.verifiedModels = [];
    populateModelChoices([], '');
    els.providers.save.disabled = true;
    els.providers.wizardStatus.textContent = `Unable to verify provider: ${error.message}`;
  }
}

async function startCopilotAuthorization() {
  els.providers.copilotAuthMessage.textContent = 'Requesting GitHub verification code...';
  els.providers.copilotStartAuth.disabled = true;
  try {
    const response = await fetch('/api/providers/copilot/device-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timeout_ms: Number(els.providers.timeoutMs.value || 30000) }),
    });
    if (!response.ok) {
      throw new Error(`sign-in start failed (${response.status})`);
    }
    const data = await response.json();
    wizardState.copilotFlow = data;
    els.providers.copilotAuthPanel.classList.remove('hidden');
    els.providers.copilotUserCode.textContent = data.user_code || '----';
    els.providers.copilotAuthMessage.textContent = data.message;
    els.providers.copilotCompleteAuth.disabled = false;
    els.providers.copilotOpenGitHub.disabled = false;
    els.providers.copilotCopyCode.disabled = false;
    els.providers.wizardStatus.textContent = 'Step 2: open GitHub, enter the code, then return here.';
  } catch (error) {
    els.providers.copilotAuthMessage.textContent = `Unable to start Copilot sign-in: ${error.message}`;
  } finally {
    els.providers.copilotStartAuth.disabled = false;
  }
}

async function completeCopilotAuthorization() {
  if (!wizardState.copilotFlow) {
    els.providers.copilotAuthMessage.textContent = 'Start GitHub sign-in first.';
    return;
  }

  els.providers.copilotAuthMessage.textContent = 'Checking GitHub authorization state...';
  try {
    const response = await fetch('/api/providers/copilot/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flow_id: wizardState.copilotFlow.flow_id }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.message || `authorization check failed (${response.status})`);
    }
    if (data.pending) {
      els.providers.copilotAuthMessage.textContent = data.message;
      return;
    }

    wizardState.verifiedProvider = data.provider;
    wizardState.verifiedModels = Array.isArray(data.models) ? data.models : [];
    populateModelChoices(wizardState.verifiedModels, data.provider.model || '');
    els.providers.save.disabled = !data.authorized;
    els.providers.copilotAuthMessage.textContent = data.message;
    els.providers.wizardStatus.textContent = data.authorized
      ? 'Copilot verified. Select a model and save the provider.'
      : data.message;
  } catch (error) {
    els.providers.copilotAuthMessage.textContent = `Unable to complete Copilot sign-in: ${error.message}`;
  }
}

async function copyCopilotCode() {
  if (!wizardState.copilotFlow?.user_code) {
    return;
  }
  try {
    await navigator.clipboard.writeText(wizardState.copilotFlow.user_code);
    els.providers.copilotAuthMessage.textContent = 'Verification code copied to clipboard.';
  } catch {
    els.providers.copilotAuthMessage.textContent = 'Clipboard copy failed. Copy the code manually.';
  }
}

function openCopilotVerification() {
  const uri = wizardState.copilotFlow?.verification_uri;
  if (!uri) {
    return;
  }
  window.open(uri, '_blank', 'noopener,noreferrer');
}

async function saveWizardProvider(event) {
  event.preventDefault();
  if (!wizardState.verifiedProvider) {
    els.providers.wizardStatus.textContent = 'Verify the provider before saving it.';
    return;
  }

  const provider = {
    ...wizardState.verifiedProvider,
    model: els.providers.modelSelect.value || wizardState.verifiedProvider.model,
    enabled: true,
  };

  if (wizardState.editIndex === null) {
    providerDrafts.push(provider);
  } else {
    providerDrafts[wizardState.editIndex] = provider;
  }

  if (!els.providers.defaultProvider.value) {
    els.providers.defaultProvider.value = provider.name;
  }

  if (wizardState.editIndex !== null && els.providers.defaultProvider.value === '') {
    els.providers.defaultProvider.value = provider.name;
  }

  renderDefaultProviderOptions(els.providers.defaultProvider.value || provider.name);
  if (!els.providers.defaultProvider.value) {
    els.providers.defaultProvider.value = provider.name;
  }

  try {
    await persistProvidersConfig(`${providerLabel(provider)} saved.`);
    logLine(`[${new Date().toISOString()}] provider saved: ${providerLabel(provider)}`);
    closeWizard();
  } catch (error) {
    els.providers.wizardStatus.textContent = `Unable to save provider: ${error.message}`;
  }
}

async function removeProvider(index) {
  const [removed] = providerDrafts.splice(index, 1);
  const currentDefault = els.providers.defaultProvider.value;
  if (currentDefault === removed.name) {
    renderDefaultProviderOptions(providerDrafts[0]?.name || '');
  }

  try {
    await persistProvidersConfig(`${providerLabel(removed)} removed.`);
  } catch (error) {
    els.providers.status.textContent = `Unable to remove provider: ${error.message}`;
  }
}

// ------------------------------------------------------------------ //
// System Assignments
// ------------------------------------------------------------------ //

async function loadSystemsConfig() {
  if (els.systems.status) els.systems.status.textContent = 'Loading system assignments...';
  try {
    const response = await fetch('/api/systems');
    if (!response.ok) throw new Error(`load failed (${response.status})`);
    systemsData = await response.json();
    renderSystemAssignments();
    renderFallbackChain();
    if (els.systems.status) els.systems.status.textContent = 'System assignments loaded.';
  } catch (error) {
    if (els.systems.status) els.systems.status.textContent = `Unable to load systems: ${error.message}`;
  }
}

function renderSystemAssignments() {
  const providers = systemsData.providers || [];
  const providerNames = [...new Set(providers.map(p => p.name))];

  for (const system of ['chat', 'sleep', 'reasoning', 'reactions']) {
    const assignment = (systemsData.systems || {})[system] || {};
    const providerSelect = document.querySelector(`.system-provider[data-system="${system}"]`);
    const modelInput = document.querySelector(`.system-model[data-system="${system}"]`);
    if (!providerSelect || !modelInput) continue;

    providerSelect.innerHTML = '<option value="">--</option>';
    for (const name of providerNames) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      opt.selected = name === assignment.provider;
      providerSelect.append(opt);
    }

    modelInput.innerHTML = '<option value="">--</option>';
    const matchingModels = providers.filter(p => p.name === assignment.provider).map(p => p.model).filter(Boolean);
    for (const model of matchingModels) {
      const opt = document.createElement('option');
      opt.value = model;
      opt.textContent = model;
      opt.selected = model === assignment.model;
      modelInput.append(opt);
    }
    // If the current model isn't in the list, add it as an option
    if (assignment.model && !matchingModels.includes(assignment.model)) {
      const opt = document.createElement('option');
      opt.value = assignment.model;
      opt.textContent = assignment.model;
      opt.selected = true;
      modelInput.append(opt);
    }
  }

  // Populate fallback chain add-provider dropdown
  if (els.systems.chainAddProvider) {
    els.systems.chainAddProvider.innerHTML = '<option value="">Provider</option>';
    for (const name of providerNames) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      els.systems.chainAddProvider.append(opt);
    }
  }
}

function renderFallbackChain() {
  const list = els.systems.chainList;
  if (!list) return;
  const chain = systemsData.fallback_chain || [];
  if (chain.length === 0) {
    list.innerHTML = '<div class="empty-state"><p>No fallback chain configured.</p></div>';
    return;
  }
  list.innerHTML = chain.map((step, i) => `
    <div class="fallback-step" data-index="${i}">
      <span class="fallback-step-handle" draggable="true">&#x2630;</span>
      <span class="mono">${escapeHtml(step.provider)}/${escapeHtml(step.model)}</span>
      <button type="button" class="ghost-button fallback-remove" data-remove-chain="${i}">&times;</button>
    </div>
  `).join('');

  // Simple drag-and-drop reorder
  list.querySelectorAll('.fallback-step-handle').forEach(handle => {
    handle.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', handle.closest('.fallback-step').dataset.index);
    });
  });
  list.querySelectorAll('.fallback-step').forEach(step => {
    step.addEventListener('dragover', (e) => { e.preventDefault(); });
    step.addEventListener('drop', (e) => {
      e.preventDefault();
      const fromIdx = Number(e.dataTransfer.getData('text/plain'));
      const toIdx = Number(step.dataset.index);
      if (fromIdx !== toIdx) {
        const chain = systemsData.fallback_chain;
        const [moved] = chain.splice(fromIdx, 1);
        chain.splice(toIdx, 0, moved);
        renderFallbackChain();
      }
    });
  });
}

function addFallbackStep() {
  const provider = (els.systems.chainAddProvider?.value || '').trim();
  const model = (els.systems.chainAddModel?.value || '').trim();
  if (!provider) return;
  if (!systemsData.fallback_chain) systemsData.fallback_chain = [];
  systemsData.fallback_chain.push({ provider, model });
  renderFallbackChain();
  if (els.systems.chainAddProvider) els.systems.chainAddProvider.value = '';
  if (els.systems.chainAddModel) els.systems.chainAddModel.value = '';
}

function removeFallbackStep(index) {
  if (systemsData.fallback_chain) {
    systemsData.fallback_chain.splice(index, 1);
    renderFallbackChain();
  }
}

async function saveSystemsConfig() {
  const systems = {};
  for (const system of ['chat', 'sleep', 'reasoning', 'reactions']) {
    const providerSelect = document.querySelector(`.system-provider[data-system="${system}"]`);
    const modelSelect = document.querySelector(`.system-model[data-system="${system}"]`);
    systems[system] = {
      provider: providerSelect?.value || '',
      model: modelSelect?.value || '',
    };
  }
  const payload = {
    systems,
    fallback_chain: systemsData.fallback_chain || [],
  };
  if (els.systems.saveStatus) els.systems.saveStatus.textContent = 'Saving...';
  try {
    const response = await fetch('/api/systems', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `save failed (${response.status})`);
    }
    systemsData = await response.json();
    renderSystemAssignments();
    renderFallbackChain();
    if (els.systems.saveStatus) els.systems.saveStatus.textContent = 'Saved.';
  } catch (error) {
    if (els.systems.saveStatus) els.systems.saveStatus.textContent = `Save failed: ${error.message}`;
  }
}

function bindEvents() {
  for (const link of els.navLinks) {
    link.addEventListener('click', () => setView(link.dataset.viewTarget));
  }

  els.providers.addProvider.addEventListener('click', () => openWizard(null));
  els.providers.closeWizard.addEventListener('click', closeWizard);
  els.providers.wizardType.addEventListener('change', () => {
    wizardState.verifiedProvider = null;
    wizardState.copilotFlow = null;
    applyWizardType();
    els.providers.save.disabled = true;
    populateModelChoices([], '');
    els.providers.copilotAuthPanel.classList.add('hidden');
    els.providers.copilotCompleteAuth.disabled = true;
    els.providers.copilotOpenGitHub.disabled = true;
    els.providers.copilotCopyCode.disabled = true;
  });
  els.providers.verify.addEventListener('click', verifyWizardProvider);
  els.providers.copilotStartAuth.addEventListener('click', startCopilotAuthorization);
  els.providers.copilotCompleteAuth.addEventListener('click', completeCopilotAuthorization);
  els.providers.copilotCopyCode.addEventListener('click', copyCopilotCode);
  els.providers.copilotOpenGitHub.addEventListener('click', openCopilotVerification);
  els.providers.wizardForm.addEventListener('submit', saveWizardProvider);
  els.providers.enabled.addEventListener('change', async () => {
    try {
      await persistProvidersConfig('Cognitive engine setting updated.');
    } catch (error) {
      els.providers.status.textContent = `Unable to update providers: ${error.message}`;
    }
  });
  els.providers.defaultProvider.addEventListener('change', async () => {
    try {
      await persistProvidersConfig('Default provider updated.');
    } catch (error) {
      els.providers.status.textContent = `Unable to update default provider: ${error.message}`;
    }
  });

  els.providers.providerList.addEventListener('click', (event) => {
    const configure = event.target.closest('[data-configure-provider]');
    if (configure) {
      openWizard(Number(configure.dataset.configureProvider));
      return;
    }

    const remove = event.target.closest('[data-remove-provider]');
    if (remove) {
      removeProvider(Number(remove.dataset.removeProvider));
    }
  });

  // System assignment events
  if (els.systems.saveBtn) {
    els.systems.saveBtn.addEventListener('click', saveSystemsConfig);
  }
  if (els.systems.chainAddBtn) {
    els.systems.chainAddBtn.addEventListener('click', addFallbackStep);
  }
  if (els.systems.chainList) {
    els.systems.chainList.addEventListener('click', (event) => {
      const removeBtn = event.target.closest('[data-remove-chain]');
      if (removeBtn) {
        removeFallbackStep(Number(removeBtn.dataset.removeChain));
      }
    });
  }

  // Update model dropdown when system provider changes
  document.querySelectorAll('.system-provider').forEach(select => {
    select.addEventListener('change', () => {
      const system = select.dataset.system;
      const modelSelect = document.querySelector(`.system-model[data-system="${system}"]`);
      if (!modelSelect) return;
      const providerName = select.value;
      const providers = systemsData.providers || [];
      const models = providers.filter(p => p.name === providerName).map(p => p.model).filter(Boolean);
      modelSelect.innerHTML = '<option value="">--</option>';
      for (const model of models) {
        const opt = document.createElement('option');
        opt.value = model;
        opt.textContent = model;
        modelSelect.append(opt);
      }
    });
  });

  document.getElementById('chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
  });
}

window.addEventListener('error', (event) => {
  logLine(`browser error: ${event.message || 'unknown browser error'}`);
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason);
  logLine(`browser rejection: ${reason}`);
});

function connect() {
  connectEventStream();
}

bindEvents();
setView('health');
connect();