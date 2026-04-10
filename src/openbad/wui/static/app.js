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
  wiring: {
    configPath: document.getElementById('wiring-config-path'),
    status: document.getElementById('wiring-status'),
    enabled: document.getElementById('wiring-enabled'),
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
  wiring: {
    title: 'Wiring',
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

  if (name === 'wiring') {
    loadWiringConfig();
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
  els.wiring.defaultProvider.innerHTML = '';
  if (providerDrafts.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No providers configured';
    option.selected = true;
    els.wiring.defaultProvider.append(option);
    els.wiring.defaultProvider.disabled = true;
    return;
  }

  els.wiring.defaultProvider.disabled = false;
  for (const provider of providerDrafts) {
    const option = document.createElement('option');
    option.value = provider.name;
    option.textContent = providerLabel(provider);
    option.selected = provider.name === selected;
    els.wiring.defaultProvider.append(option);
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
    els.wiring.providerList.innerHTML = `
      <div class="empty-state">
        <strong>No providers configured</strong>
        <p>Use Add provider to walk through GitHub Copilot or a local OpenAI-compatible llama endpoint.</p>
      </div>
    `;
    return;
  }

  els.wiring.providerList.innerHTML = providerDrafts
    .map((provider, index) => providerSummaryCard(provider, index))
    .join('');
}

function openWizard(editIndex = null) {
  wizardState.open = true;
  wizardState.editIndex = editIndex;
  wizardState.verifiedProvider = null;
  wizardState.verifiedModels = [];
  wizardState.copilotFlow = null;
  els.wiring.save.disabled = true;
  els.wiring.modelSelect.disabled = true;
  els.wiring.modelSelect.innerHTML = '<option value="">Verify provider first</option>';
  els.wiring.wizard.classList.remove('hidden');
  els.wiring.copilotAuthPanel.classList.add('hidden');
  els.wiring.copilotUserCode.textContent = '----';
  els.wiring.copilotCompleteAuth.disabled = true;
  els.wiring.copilotOpenGitHub.disabled = true;
  els.wiring.copilotCopyCode.disabled = true;
  els.wiring.copilotAuthMessage.textContent = 'No active Copilot authorization yet.';

  if (editIndex === null) {
    els.wiring.wizardTitle.textContent = 'Add Provider';
    els.wiring.wizardType.value = 'github-copilot';
    els.wiring.baseUrl.value = 'http://127.0.0.1:11434';
    els.wiring.apiKeyEnv.value = '';
    els.wiring.timeoutMs.value = '30000';
    els.wiring.wizardStatus.textContent = 'Choose a provider type to begin the setup walkthrough.';
  } else {
    const provider = providerDrafts[editIndex];
    els.wiring.wizardTitle.textContent = `Configure ${providerLabel(provider)}`;
    els.wiring.wizardType.value = providerTypeFromDraft(provider);
    els.wiring.baseUrl.value = provider.base_url || 'http://127.0.0.1:11434';
    els.wiring.apiKeyEnv.value = provider.api_key_env || '';
    els.wiring.timeoutMs.value = String(provider.timeout_ms || 30000);
    els.wiring.wizardStatus.textContent = 'Verify the provider again before saving updated settings.';
  }

  applyWizardType();
}

function closeWizard() {
  wizardState.open = false;
  wizardState.editIndex = null;
  wizardState.verifiedProvider = null;
  wizardState.verifiedModels = [];
  wizardState.copilotFlow = null;
  els.wiring.wizard.classList.add('hidden');
}

function applyWizardType() {
  const type = els.wiring.wizardType.value;
  const local = type === 'local-openai';
  els.wiring.localFields.classList.toggle('hidden', !local);
  els.wiring.copilotFields.classList.toggle('hidden', local);
  els.wiring.verify.classList.toggle('hidden', !local);
}

async function loadWiringConfig() {
  els.wiring.status.textContent = 'Loading provider wiring...';
  try {
    const response = await fetch('/api/wiring/providers');
    if (!response.ok) {
      throw new Error(`load failed (${response.status})`);
    }
    const data = await response.json();
    providerDrafts = Array.isArray(data.providers) ? data.providers : [];
    els.wiring.configPath.textContent = data.config_path || 'config path unavailable';
    els.wiring.enabled.checked = Boolean(data.enabled);
    renderDefaultProviderOptions(data.default_provider || providerDrafts[0]?.name || '');
    renderProviderList();
    els.wiring.status.textContent = providerDrafts.length > 0
      ? 'Provider wiring loaded.'
      : 'No providers configured yet.';
  } catch (error) {
    els.wiring.status.textContent = `Unable to load provider wiring: ${error.message}`;
  }
}

async function persistWiringConfig(statusMessage) {
  const payload = {
    enabled: els.wiring.enabled.checked,
    default_provider: els.wiring.defaultProvider.value || '',
    providers: providerDrafts,
  };

  const response = await fetch('/api/wiring/providers', {
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
    els.wiring.defaultProvider.value = data.default_provider;
  }
  renderProviderList();
  els.wiring.configPath.textContent = data.config_path || els.wiring.configPath.textContent;
  els.wiring.status.textContent = statusMessage;
}

function verificationPayload() {
  const payload = {
    provider_type: els.wiring.wizardType.value,
    timeout_ms: Number(els.wiring.timeoutMs.value || 30000),
  };

  if (payload.provider_type === 'local-openai') {
    payload.base_url = els.wiring.baseUrl.value.trim();
    payload.api_key_env = els.wiring.apiKeyEnv.value.trim();
  }

  return payload;
}

function populateModelChoices(models, preferredModel = '') {
  els.wiring.modelSelect.innerHTML = '';
  const options = models.length > 0 ? models : [preferredModel].filter(Boolean);
  if (options.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No models returned';
    els.wiring.modelSelect.append(option);
    els.wiring.modelSelect.disabled = true;
    return;
  }

  for (const model of options) {
    const option = document.createElement('option');
    option.value = model;
    option.textContent = model;
    option.selected = model === preferredModel || (!preferredModel && model === options[0]);
    els.wiring.modelSelect.append(option);
  }
  els.wiring.modelSelect.disabled = false;
}

async function verifyWizardProvider() {
  if (els.wiring.wizardType.value === 'github-copilot') {
    els.wiring.wizardStatus.textContent = 'Use the Copilot sign-in flow below.';
    return;
  }
  els.wiring.wizardStatus.textContent = 'Verifying provider access...';
  els.wiring.save.disabled = true;
  try {
    const response = await fetch('/api/wiring/providers/verify', {
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
    els.wiring.save.disabled = !data.available;
    els.wiring.wizardStatus.textContent = data.message;
  } catch (error) {
    wizardState.verifiedProvider = null;
    wizardState.verifiedModels = [];
    populateModelChoices([], '');
    els.wiring.save.disabled = true;
    els.wiring.wizardStatus.textContent = `Unable to verify provider: ${error.message}`;
  }
}

async function startCopilotAuthorization() {
  els.wiring.copilotAuthMessage.textContent = 'Requesting GitHub verification code...';
  els.wiring.copilotStartAuth.disabled = true;
  try {
    const response = await fetch('/api/wiring/providers/copilot/device-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timeout_ms: Number(els.wiring.timeoutMs.value || 30000) }),
    });
    if (!response.ok) {
      throw new Error(`sign-in start failed (${response.status})`);
    }
    const data = await response.json();
    wizardState.copilotFlow = data;
    els.wiring.copilotAuthPanel.classList.remove('hidden');
    els.wiring.copilotUserCode.textContent = data.user_code || '----';
    els.wiring.copilotAuthMessage.textContent = data.message;
    els.wiring.copilotCompleteAuth.disabled = false;
    els.wiring.copilotOpenGitHub.disabled = false;
    els.wiring.copilotCopyCode.disabled = false;
    els.wiring.wizardStatus.textContent = 'Step 2: open GitHub, enter the code, then return here.';
  } catch (error) {
    els.wiring.copilotAuthMessage.textContent = `Unable to start Copilot sign-in: ${error.message}`;
  } finally {
    els.wiring.copilotStartAuth.disabled = false;
  }
}

async function completeCopilotAuthorization() {
  if (!wizardState.copilotFlow) {
    els.wiring.copilotAuthMessage.textContent = 'Start GitHub sign-in first.';
    return;
  }

  els.wiring.copilotAuthMessage.textContent = 'Checking GitHub authorization state...';
  try {
    const response = await fetch('/api/wiring/providers/copilot/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flow_id: wizardState.copilotFlow.flow_id }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.message || `authorization check failed (${response.status})`);
    }
    if (data.pending) {
      els.wiring.copilotAuthMessage.textContent = data.message;
      return;
    }

    wizardState.verifiedProvider = data.provider;
    wizardState.verifiedModels = Array.isArray(data.models) ? data.models : [];
    populateModelChoices(wizardState.verifiedModels, data.provider.model || '');
    els.wiring.save.disabled = !data.authorized;
    els.wiring.copilotAuthMessage.textContent = data.message;
    els.wiring.wizardStatus.textContent = data.authorized
      ? 'Copilot verified. Select a model and save the provider.'
      : data.message;
  } catch (error) {
    els.wiring.copilotAuthMessage.textContent = `Unable to complete Copilot sign-in: ${error.message}`;
  }
}

async function copyCopilotCode() {
  if (!wizardState.copilotFlow?.user_code) {
    return;
  }
  try {
    await navigator.clipboard.writeText(wizardState.copilotFlow.user_code);
    els.wiring.copilotAuthMessage.textContent = 'Verification code copied to clipboard.';
  } catch {
    els.wiring.copilotAuthMessage.textContent = 'Clipboard copy failed. Copy the code manually.';
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
    els.wiring.wizardStatus.textContent = 'Verify the provider before saving it.';
    return;
  }

  const provider = {
    ...wizardState.verifiedProvider,
    model: els.wiring.modelSelect.value || wizardState.verifiedProvider.model,
    enabled: true,
  };

  if (wizardState.editIndex === null) {
    providerDrafts.push(provider);
  } else {
    providerDrafts[wizardState.editIndex] = provider;
  }

  if (!els.wiring.defaultProvider.value) {
    els.wiring.defaultProvider.value = provider.name;
  }

  if (wizardState.editIndex !== null && els.wiring.defaultProvider.value === '') {
    els.wiring.defaultProvider.value = provider.name;
  }

  renderDefaultProviderOptions(els.wiring.defaultProvider.value || provider.name);
  if (!els.wiring.defaultProvider.value) {
    els.wiring.defaultProvider.value = provider.name;
  }

  try {
    await persistWiringConfig(`${providerLabel(provider)} saved.`);
    logLine(`[${new Date().toISOString()}] provider saved: ${providerLabel(provider)}`);
    closeWizard();
  } catch (error) {
    els.wiring.wizardStatus.textContent = `Unable to save provider: ${error.message}`;
  }
}

async function removeProvider(index) {
  const [removed] = providerDrafts.splice(index, 1);
  const currentDefault = els.wiring.defaultProvider.value;
  if (currentDefault === removed.name) {
    renderDefaultProviderOptions(providerDrafts[0]?.name || '');
  }

  try {
    await persistWiringConfig(`${providerLabel(removed)} removed.`);
  } catch (error) {
    els.wiring.status.textContent = `Unable to remove provider: ${error.message}`;
  }
}

function bindEvents() {
  for (const link of els.navLinks) {
    link.addEventListener('click', () => setView(link.dataset.viewTarget));
  }

  els.wiring.addProvider.addEventListener('click', () => openWizard(null));
  els.wiring.closeWizard.addEventListener('click', closeWizard);
  els.wiring.wizardType.addEventListener('change', () => {
    wizardState.verifiedProvider = null;
    wizardState.copilotFlow = null;
    applyWizardType();
    els.wiring.save.disabled = true;
    populateModelChoices([], '');
    els.wiring.copilotAuthPanel.classList.add('hidden');
    els.wiring.copilotCompleteAuth.disabled = true;
    els.wiring.copilotOpenGitHub.disabled = true;
    els.wiring.copilotCopyCode.disabled = true;
  });
  els.wiring.verify.addEventListener('click', verifyWizardProvider);
  els.wiring.copilotStartAuth.addEventListener('click', startCopilotAuthorization);
  els.wiring.copilotCompleteAuth.addEventListener('click', completeCopilotAuthorization);
  els.wiring.copilotCopyCode.addEventListener('click', copyCopilotCode);
  els.wiring.copilotOpenGitHub.addEventListener('click', openCopilotVerification);
  els.wiring.wizardForm.addEventListener('submit', saveWizardProvider);
  els.wiring.enabled.addEventListener('change', async () => {
    try {
      await persistWiringConfig('Cognitive engine setting updated.');
    } catch (error) {
      els.wiring.status.textContent = `Unable to update wiring: ${error.message}`;
    }
  });
  els.wiring.defaultProvider.addEventListener('change', async () => {
    try {
      await persistWiringConfig('Default provider updated.');
    } catch (error) {
      els.wiring.status.textContent = `Unable to update default provider: ${error.message}`;
    }
  });

  els.wiring.providerList.addEventListener('click', (event) => {
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