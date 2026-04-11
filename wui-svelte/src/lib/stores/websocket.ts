/**
 * WebSocket store for real-time telemetry and event streaming.
 *
 * Auto-connects on mount, reconnects with exponential backoff (max 30 s).
 * Incoming messages are deserialized from a JSON envelope with topic + payload.
 * Derived stores expose per-topic typed state.
 */

import { writable, derived, type Readable } from 'svelte/store';

// ------------------------------------------------------------------ //
// Types
// ------------------------------------------------------------------ //

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

export interface Envelope {
  topic: string;
  payload: Record<string, unknown>;
}

export interface VitalsPayload {
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  net_tx_bytes: number;
  net_rx_bytes: number;
}

export interface EndocrinePayload {
  dopamine: number;
  adrenaline: number;
  cortisol: number;
  endorphin: number;
}

export interface FsmPayload {
  state: string;
}

export interface ToolbeltPayload {
  cabinet: Record<string, unknown>;
  belt: Record<string, unknown>;
}

// ------------------------------------------------------------------ //
// Core stores
// ------------------------------------------------------------------ //

export const wsStatus = writable<WsStatus>('disconnected');

/** Raw last-received message per topic. */
const _messages = writable<Record<string, Record<string, unknown>>>({});

// ------------------------------------------------------------------ //
// Derived per-topic stores
// ------------------------------------------------------------------ //

export const cpuTelemetry: Readable<VitalsPayload | null> = derived(
  _messages,
  ($m) => ($m['agent/telemetry/vitals'] as unknown as VitalsPayload) ?? null,
);

export const endocrineLevels: Readable<EndocrinePayload | null> = derived(
  _messages,
  ($m) => ($m['agent/telemetry/endocrine'] as unknown as EndocrinePayload) ?? null,
);

export const fsmState: Readable<FsmPayload | null> = derived(
  _messages,
  ($m) => ($m['agent/fsm/state'] as unknown as FsmPayload) ?? null,
);

export const toolbeltHealth: Readable<ToolbeltPayload | null> = derived(
  _messages,
  ($m) => ($m['agent/telemetry/toolbelt'] as unknown as ToolbeltPayload) ?? null,
);

// ------------------------------------------------------------------ //
// Connection management
// ------------------------------------------------------------------ //

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

let _ws: WebSocket | null = null;
let _attempt = 0;
let _reconnectTimer: ReturnType<typeof setTimeout> | undefined;

function _backoffMs(): number {
  const ms = Math.min(BASE_BACKOFF_MS * 2 ** _attempt, MAX_BACKOFF_MS);
  return ms;
}

function _buildUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws`;
}

/** Process an incoming WebSocket message. */
export function _handleMessage(raw: string): void {
  try {
    const envelope: Envelope = JSON.parse(raw);
    if (typeof envelope.topic !== 'string' || !envelope.payload) return;
    _messages.update((m) => ({ ...m, [envelope.topic]: envelope.payload }));
  } catch {
    // Ignore non-JSON or malformed messages
  }
}

/** Connect to the WebSocket backend. */
export function connect(urlOverride?: string): void {
  if (_ws && (_ws.readyState === WebSocket.CONNECTING || _ws.readyState === WebSocket.OPEN)) {
    return;
  }

  const url = urlOverride ?? _buildUrl();
  wsStatus.set('connecting');

  _ws = new WebSocket(url);

  _ws.onopen = () => {
    _attempt = 0;
    wsStatus.set('connected');
  };

  _ws.onmessage = (event) => {
    _handleMessage(String(event.data));
  };

  _ws.onclose = () => {
    wsStatus.set('disconnected');
    _scheduleReconnect();
  };

  _ws.onerror = () => {
    // onclose will fire after onerror
  };
}

function _scheduleReconnect(): void {
  if (_reconnectTimer !== undefined) return;
  const delay = _backoffMs();
  _attempt += 1;
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = undefined;
    connect();
  }, delay);
}

/** Send a message to the backend. */
export function send(topic: string, payload: Record<string, unknown>): void {
  if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
  _ws.send(JSON.stringify({ topic, payload }));
}

/** Disconnect and stop reconnection attempts. */
export function disconnect(): void {
  if (_reconnectTimer !== undefined) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = undefined;
  }
  _attempt = 0;
  if (_ws) {
    _ws.onclose = null;
    _ws.close();
    _ws = null;
  }
  wsStatus.set('disconnected');
}
