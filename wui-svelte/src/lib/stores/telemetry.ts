/**
 * Telemetry store — connects to the SSE event stream and exposes
 * reactive state for health metrics, hormones, and FSM state.
 */

import { writable } from 'svelte/store';

export const connected = writable(false);
export const fsmState = writable('UNKNOWN');

export interface Vitals {
  cpu: number;
  memory: number;
  disk: number;
  netTx: number;
  netRx: number;
}

export const vitals = writable<Vitals>({
  cpu: 0,
  memory: 0,
  disk: 0,
  netTx: 0,
  netRx: 0,
});

export interface Hormones {
  dopamine: number;
  adrenaline: number;
  cortisol: number;
  endorphin: number;
}

export const hormones = writable<Hormones>({
  dopamine: 0,
  adrenaline: 0,
  cortisol: 0,
  endorphin: 0,
});

let eventSource: EventSource | null = null;

export function connectTelemetry(): void {
  if (eventSource) return;
  eventSource = new EventSource('/events');
  eventSource.onopen = () => connected.set(true);
  eventSource.onerror = () => connected.set(false);
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.topic && data.payload) {
        handleEvent(data.topic, data.payload);
      }
    } catch {
      // ignore non-JSON messages
    }
  };
}

function handleEvent(topic: string, payload: Record<string, unknown>): void {
  if (topic === 'agent/telemetry/vitals') {
    vitals.set(payload as unknown as Vitals);
  } else if (topic === 'agent/telemetry/endocrine') {
    hormones.set(payload as unknown as Hormones);
  } else if (topic === 'agent/fsm/state') {
    fsmState.set(String(payload.state ?? 'UNKNOWN'));
  }
}
