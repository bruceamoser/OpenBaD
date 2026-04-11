/**
 * Config store — manages provider and system configuration state.
 */

import { writable } from 'svelte/store';
import { get, put } from '$lib/api/client';

export interface ProviderConfig {
  enabled: boolean;
  providers: Array<{ name: string; verified: boolean }>;
}

export const providerConfig = writable<ProviderConfig | null>(null);

export async function loadProviders(): Promise<void> {
  const data = await get<ProviderConfig>('/api/providers');
  providerConfig.set(data);
}

export async function saveProviders(config: ProviderConfig): Promise<void> {
  const data = await put<ProviderConfig>('/api/providers', config);
  providerConfig.set(data);
}
