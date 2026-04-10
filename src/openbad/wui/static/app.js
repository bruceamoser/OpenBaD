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
};

function logLine(text) {
  const div = document.createElement('div');
  div.className = 'log-line';
  div.textContent = text;
  els.log.prepend(div);
  while (els.log.children.length > 140) {
    els.log.removeChild(els.log.lastChild);
  }
}

function setOnline(online) {
  els.status.textContent = online ? 'online' : 'offline';
  els.status.classList.toggle('online', online);
  els.status.classList.toggle('offline', !online);
}

function updateFromEvent(topic, payload) {
  if (topic.startsWith('agent/endocrine/')) {
    const hormone = topic.split('/').pop();
    if (els.hormones[hormone] && typeof payload.level === 'number') {
      els.hormones[hormone].textContent = payload.level.toFixed(2);
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
    els.inference.provider.textContent = payload.provider || '--';
    els.inference.model.textContent = payload.model_id || '--';
    els.inference.health.textContent = payload.available ? 'up' : 'down';
    els.inference.p50.textContent = `${Number(payload.latency_p50 || 0).toFixed(1)}ms`;
    els.inference.p99.textContent = `${Number(payload.latency_p99 || 0).toFixed(1)}ms`;
    return;
  }

  if (topic === 'agent/cognitive/response') {
    els.inference.lastTokens.textContent = `${payload.tokens_used || 0}`;
    els.inference.lastLatency.textContent = `${Number(payload.latency_ms || 0).toFixed(1)}ms`;
  }
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/ws`);

  ws.addEventListener('open', () => {
    setOnline(true);
    logLine('socket connected');
  });

  ws.addEventListener('close', () => {
    setOnline(false);
    logLine('socket disconnected; retrying...');
    setTimeout(connect, 1000);
  });

  ws.addEventListener('message', (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'hello') {
        logLine(`[${msg.ts}] ${msg.message}`);
        return;
      }
      if (msg.type === 'event') {
        const topic = msg.topic || 'unknown/topic';
        updateFromEvent(topic, msg.payload || {});
        logLine(`[${msg.ts}] ${topic}`);
      }
    } catch {
      logLine('malformed socket payload received');
    }
  });
}

connect();
