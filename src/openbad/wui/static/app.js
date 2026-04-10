const els = {
  status: document.getElementById('ws-status'),
  fsm: document.getElementById('fsm-state'),
  hormones: {
    dopamine: document.getElementById('h-dopamine'),
    adrenaline: document.getElementById('h-adrenaline'),
    cortisol: document.getElementById('h-cortisol'),
    endorphin: document.getElementById('h-endorphin'),
  },
  vitals: {
    cpu: document.getElementById('v-cpu'),
    memory: document.getElementById('v-memory'),
    disk: document.getElementById('v-disk'),
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
  anatomy: {
    nervous: document.getElementById('organ-nervous'),
    endocrine: document.getElementById('organ-endocrine'),
    reflex: document.getElementById('organ-reflex'),
    cognitive: document.getElementById('organ-cognitive'),
    immune: document.getElementById('organ-immune'),
    memory: document.getElementById('organ-memory'),
    sensory: document.getElementById('organ-sensory'),
  },
};

let activeSocket = null;
let reconnectTimer = null;
let activeEventSource = null;

function pulseOrgan(name) {
  const node = els.anatomy[name] || els.anatomy.nervous;
  if (!node) {
    return;
  }
  node.classList.add('pulse');
  window.setTimeout(() => node.classList.remove('pulse'), 380);
}

function mapTopicToOrgan(topic) {
  if (topic.startsWith('agent/cognitive/')) return 'cognitive';
  if (topic.startsWith('agent/endocrine/')) return 'endocrine';
  if (topic.startsWith('agent/reflex/')) return 'reflex';
  if (topic.startsWith('agent/immune/')) return 'immune';
  if (topic.startsWith('agent/memory/')) return 'memory';
  if (topic.startsWith('agent/sensory/')) return 'sensory';
  return 'nervous';
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

window.addEventListener('error', (event) => {
  const message = event.message || 'unknown browser error';
  logLine(`browser error: ${message}`);
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason);
  logLine(`browser rejection: ${reason}`);
});

function setOnline(online) {
  els.status.textContent = online ? 'online' : 'offline';
  els.status.classList.toggle('online', online);
  els.status.classList.toggle('offline', !online);
}

function updateFromEvent(topic, payload) {
  if (topic.startsWith('agent/endocrine/')) {
    const hormone = topic.split('/').pop();
    if (els.hormones[hormone]) {
      const level = Number(payload.level ?? 0);
      els.hormones[hormone].textContent = level.toFixed(2);
    }
    return;
  }

  if (topic === 'agent/reflex/state') {
    els.fsm.textContent = payload.current_state || 'UNKNOWN';
    return;
  }

  if (topic === 'agent/telemetry/cpu') {
    els.vitals.cpu.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    return;
  }
  if (topic === 'agent/telemetry/memory') {
    els.vitals.memory.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    return;
  }
  if (topic === 'agent/telemetry/disk') {
    els.vitals.disk.textContent = `${Number(payload.usage_percent || 0).toFixed(1)}%`;
    return;
  }
  if (topic === 'agent/telemetry/network') {
    els.vitals.netTx.textContent = `${payload.bytes_sent || 0}`;
    els.vitals.netRx.textContent = `${payload.bytes_recv || 0}`;
    return;
  }
  if (topic === 'agent/telemetry/tokens') {
    els.vitals.tokens.textContent = `${payload.tokens_used || 0}`;
    els.vitals.tier.textContent = payload.model_tier || '--';
    return;
  }

  if (topic === 'agent/cognitive/health') {
    const configuredProviders = Number(payload.configured_provider_count ?? 0);
    els.inference.provider.textContent = `${configuredProviders}`;
    els.inference.model.textContent = payload.model_id || '--';
    if (payload.provider === 'inactive' || payload.model_id === 'none') {
      els.inference.health.textContent = 'inactive';
    } else {
      els.inference.health.textContent = payload.available ? 'up' : 'down';
    }
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
      const topic = msg.topic || 'unknown/topic';
      pulseOrgan(mapTopicToOrgan(topic));
      updateFromEvent(topic, msg.payload || {});
      logLine(`[${msg.ts}] ${topic}`);
    }
  } catch {
    logLine('malformed transport payload received');
  }
}

function connectWebSocket() {
  if (activeSocket && activeSocket.readyState === WebSocket.OPEN) {
    return;
  }
  if (activeSocket && activeSocket.readyState === WebSocket.CONNECTING) {
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
    setOnline(true);
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
    }, 1000);
  });

  ws.addEventListener('message', (ev) => {
    handleEventMessage(ev.data);
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
    setOnline(true);
    logLine('event stream connected');
  });

  source.addEventListener('error', () => {
    setOnline(false);
    const state = source.readyState;
    logLine(`event stream disconnected; state=${state}; retrying...`);
  });

  source.onmessage = (event) => {
    handleEventMessage(event.data);
  };
}

connectEventStream();
