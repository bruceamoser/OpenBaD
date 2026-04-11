<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import Card from '$lib/components/Card.svelte';
  import {
    get as apiGet,
    put as apiPut,
    post as apiPost,
  } from '$lib/api/client';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface UserProfile {
    name: string;
    preferred_name: string;
    communication_style: string;
    expertise_domains: string[];
    learned_summary?: string;
  }

  interface ModulationFactors {
    exploration_budget_multiplier: number;
    max_reasoning_depth_multiplier: number;
    proactive_suggestion_threshold: number;
    challenge_probability: number;
    cortisol_decay_multiplier: number;
  }

  interface AssistantProfile {
    name: string;
    persona_summary: string;
    learning_focus: string;
    openness: number;
    conscientiousness: number;
    extraversion: number;
    agreeableness: number;
    stability: number;
    modulation?: ModulationFactors;
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  let tab: 'user' | 'assistant' = $state('user');

  let user: UserProfile = $state({
    name: '',
    preferred_name: '',
    communication_style: 'casual',
    expertise_domains: [],
    learned_summary: '',
  });

  let assistant: AssistantProfile = $state({
    name: '',
    persona_summary: '',
    learning_focus: '',
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stability: 0.5,
  });

  let userDirty = $state(false);
  let assistantDirty = $state(false);
  let saving = $state(false);
  let statusMsg = $state('');
  let onboardingHint = $derived($page.url.searchParams.get('onboarding') ?? '');

  // Live OCEAN behavior descriptions
  const oceanLabels: Record<string, [string, string]> = {
    openness: ['Conventional', 'Exploratory'],
    conscientiousness: ['Flexible', 'Methodical'],
    extraversion: ['Reserved', 'Proactive'],
    agreeableness: ['Challenging', 'Agreeable'],
    stability: ['Reactive', 'Steady'],
  };

  function oceanDesc(trait: string, val: number): string {
    const [low, high] = oceanLabels[trait] ?? ['Low', 'High'];
    if (val < 0.35) return low;
    if (val > 0.65) return high;
    return 'Balanced';
  }

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function loadUser(): Promise<void> {
    try {
      const d = await apiGet<UserProfile>('/api/entity/user');
      user = d;
    } catch (e) {
      statusMsg = `Load user failed: ${e}`;
    }
  }

  async function loadAssistant(): Promise<void> {
    try {
      const d = await apiGet<AssistantProfile>(
        '/api/entity/assistant',
      );
      assistant = d;
    } catch (e) {
      statusMsg = `Load assistant failed: ${e}`;
    }
  }

  onMount(() => { loadUser(); loadAssistant(); });

  $effect(() => {
    const requestedTab = $page.url.searchParams.get('tab');
    if (requestedTab === 'user' || requestedTab === 'assistant') {
      tab = requestedTab;
    }
  });

  // ----------------------------------------------------------------
  // Save
  // ----------------------------------------------------------------

  async function saveUser(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/entity/user', {
        name: user.name,
        preferred_name: user.preferred_name,
        communication_style: user.communication_style,
        expertise_domains: user.expertise_domains,
      });
      userDirty = false;
      statusMsg = 'User saved';
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  async function saveAssistant(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/entity/assistant', {
        name: assistant.name,
        persona_summary: assistant.persona_summary,
        learning_focus: assistant.learning_focus,
        openness: assistant.openness,
        conscientiousness: assistant.conscientiousness,
        extraversion: assistant.extraversion,
        agreeableness: assistant.agreeableness,
        stability: assistant.stability,
      });
      assistantDirty = false;
      statusMsg = 'Assistant saved';
      await loadAssistant();
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  // ----------------------------------------------------------------
  // Reset to seed
  // ----------------------------------------------------------------

  async function resetUser(): Promise<void> {
    try {
      await apiPost('/api/entity/user/reset');
      await loadUser();
      userDirty = false;
      statusMsg = 'User reset to seed';
    } catch (e) {
      statusMsg = `Reset failed: ${e}`;
    }
  }

  async function resetAssistant(): Promise<void> {
    try {
      await apiPost('/api/entity/assistant/reset');
      await loadAssistant();
      assistantDirty = false;
      statusMsg = 'Assistant reset to seed';
    } catch (e) {
      statusMsg = `Reset failed: ${e}`;
    }
  }

  // ----------------------------------------------------------------
  // Domain helpers
  // ----------------------------------------------------------------

  let domainInput = $state('');

  function addDomain(): void {
    const d = domainInput.trim();
    if (d && !user.expertise_domains.includes(d)) {
      user.expertise_domains = [...user.expertise_domains, d];
      userDirty = true;
    }
    domainInput = '';
  }

  function removeDomain(idx: number): void {
    user.expertise_domains = user.expertise_domains.filter(
      (_, i) => i !== idx,
    );
    userDirty = true;
  }
</script>

<div class="page-header">
  <h2>Entity</h2>
  <p>Configure user and assistant identity profiles</p>
</div>

{#if onboardingHint === 'user' || onboardingHint === 'assistant'}
  <div class="onboarding-banner">
    {#if onboardingHint === 'assistant'}
      Finish the assistant profile to unlock the guided first-run flow.
    {:else}
      Finish the user profile so OpenBaD can personalize the first-run experience.
    {/if}
  </div>
{/if}

<!-- Tab switcher -->
<div class="tab-bar">
  <button class:active={tab === 'user'} onclick={() => tab = 'user'}>
    <span class="tab-icon">👤</span> User
  </button>
  <button class:active={tab === 'assistant'} onclick={() => tab = 'assistant'}>
    <span class="tab-icon">🤖</span> Assistant
  </button>
</div>

<!-- User tab -->
{#if tab === 'user'}
  <Card label="User Profile">
    <div class="form-grid">
      <div class="form-row-2">
        <label>Name
          <input type="text" bind:value={user.name} oninput={() => userDirty = true} placeholder="Your name" />
        </label>
        <label>Preferred Name
          <input type="text" bind:value={user.preferred_name} oninput={() => userDirty = true} placeholder="How the agent calls you" />
        </label>
      </div>
      <label>Communication Style
        <select bind:value={user.communication_style} onchange={() => userDirty = true}>
          <option value="casual">Casual</option>
          <option value="formal">Formal</option>
          <option value="terse">Terse</option>
        </select>
      </label>
      <div class="domains-section">
        <h4>Expertise Domains</h4>
        <div class="domain-chips">
          {#each user.expertise_domains as domain, i}
            <span class="domain-chip">
              {domain}
              <button class="chip-x" onclick={() => removeDomain(i)}>✕</button>
            </span>
          {/each}
        </div>
        <div class="add-row">
          <input
            type="text"
            bind:value={domainInput}
            placeholder="Add expertise…"
            onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') addDomain(); }}
          />
          <button class="secondary" onclick={addDomain}>Add</button>
        </div>
      </div>
    </div>
  </Card>

  <Card label="Learned Summary">
    <p class="ro-summary">{user.learned_summary || 'No learned data yet. The agent will build a summary over time.'}</p>
  </Card>

  <div class="actions-bar">
    <button onclick={saveUser} disabled={!userDirty || saving}>
      {saving ? 'Saving…' : 'Save Changes'}
    </button>
    <button class="ghost" onclick={resetUser}>Reset to Seed</button>
  </div>
{/if}

<!-- Assistant tab -->
{#if tab === 'assistant'}
  <Card label="Assistant Profile">
    <div class="form-grid">
      <label>Name
        <input type="text" bind:value={assistant.name} oninput={() => assistantDirty = true} placeholder="Agent display name" />
      </label>
      <label>Persona Summary
        <textarea bind:value={assistant.persona_summary} oninput={() => assistantDirty = true} rows="3" placeholder="Describe the assistant's personality…"></textarea>
      </label>
      <label>Learning Focus
        <input type="text" bind:value={assistant.learning_focus} oninput={() => assistantDirty = true} placeholder="e.g. systems programming" />
      </label>
    </div>
  </Card>

  <Card label="OCEAN Personality">
    <div class="ocean-grid">
      {#each Object.keys(oceanLabels) as trait}
        {@const val = assistant[trait as keyof AssistantProfile] as number}
        <div class="ocean-row">
          <div class="ocean-top">
            <span class="ocean-label">{trait}</span>
            <span class="ocean-desc">{oceanDesc(trait, val)}</span>
          </div>
          <div class="ocean-slider-wrap">
            <span class="ocean-pole">{oceanLabels[trait][0]}</span>
            <input
              type="range" min="0" max="1" step="0.05" value={val}
              oninput={(e: Event) => {
                const v = parseFloat((e.target as HTMLInputElement).value);
                (assistant as any)[trait] = v;
                assistantDirty = true;
              }}
            />
            <span class="ocean-pole">{oceanLabels[trait][1]}</span>
          </div>
          <div class="ocean-val">{val.toFixed(2)}</div>
        </div>
      {/each}
    </div>
  </Card>

  <Card label="Modulation Factors">
    {#if assistant.modulation}
      <div class="mod-grid">
        <div class="mod-item">
          <span class="mod-label">Exploration Budget</span>
          <span class="mod-val">{assistant.modulation.exploration_budget_multiplier.toFixed(2)}×</span>
        </div>
        <div class="mod-item">
          <span class="mod-label">Reasoning Depth</span>
          <span class="mod-val">{assistant.modulation.max_reasoning_depth_multiplier.toFixed(2)}×</span>
        </div>
        <div class="mod-item">
          <span class="mod-label">Proactive Threshold</span>
          <span class="mod-val">{assistant.modulation.proactive_suggestion_threshold.toFixed(2)}</span>
        </div>
        <div class="mod-item">
          <span class="mod-label">Challenge Probability</span>
          <span class="mod-val">{assistant.modulation.challenge_probability.toFixed(2)}</span>
        </div>
        <div class="mod-item">
          <span class="mod-label">Cortisol Decay</span>
          <span class="mod-val">{assistant.modulation.cortisol_decay_multiplier.toFixed(2)}×</span>
        </div>
      </div>
    {:else}
      <p class="hint">Save personality to compute modulation factors.</p>
    {/if}
  </Card>

  <div class="actions-bar">
    <button onclick={saveAssistant} disabled={!assistantDirty || saving}>
      {saving ? 'Saving…' : 'Save Changes'}
    </button>
    <button class="ghost" onclick={resetAssistant}>Reset to Seed</button>
  </div>
{/if}

{#if statusMsg}
  <div class="status-toast">{statusMsg}</div>
{/if}

<style>
  .onboarding-banner {
    margin-bottom: 1rem;
    padding: 0.75rem 1rem;
    border: 1px solid color-mix(in srgb, var(--blue) 45%, var(--border));
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--blue) 12%, var(--bg-surface1));
    color: var(--text-sub);
    font-size: 0.9rem;
  }

  .tab-bar { display: flex; gap: 0; margin-bottom: 1.25rem; }
  .tab-bar button {
    display: flex; align-items: center; gap: 0.4rem;
    padding: 0.55rem 1.2rem; border: 1px solid var(--border); background: transparent;
    color: var(--text-sub); cursor: pointer; font-size: 0.9rem; transition: all 0.15s var(--ease);
  }
  .tab-bar button:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
  .tab-bar button:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; border-left: none; }
  .tab-bar button.active {
    background: var(--bg-surface1); color: var(--text); font-weight: 600;
    border-color: var(--blue);
  }
  .tab-icon { font-size: 1rem; }

  .form-grid { display: flex; flex-direction: column; gap: 0.75rem; }
  .form-grid label { display: flex; flex-direction: column; gap: 0.3rem; }
  .form-row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  @media (max-width: 600px) { .form-row-2 { grid-template-columns: 1fr; } }
  textarea { resize: vertical; }

  .domains-section { display: flex; flex-direction: column; gap: 0.5rem; }
  .domains-section h4 { margin: 0; font-size: 0.9rem; color: var(--text-sub); }
  .domain-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
  .domain-chip {
    display: inline-flex; align-items: center; gap: 0.3rem;
    background: var(--bg-surface1); border: 1px solid var(--bg-surface2);
    padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.82rem;
  }
  .chip-x { background: none; border: none; cursor: pointer; color: var(--red); font-size: 0.75rem; padding: 0; }
  .add-row { display: flex; gap: 0.4rem; }
  .add-row input { flex: 1; }
  .ro-summary { color: var(--text-dim); font-style: italic; white-space: pre-wrap; line-height: 1.6; }

  .ocean-grid { display: flex; flex-direction: column; gap: 1rem; }
  .ocean-row { display: flex; flex-direction: column; gap: 0.25rem; }
  .ocean-top { display: flex; justify-content: space-between; align-items: baseline; }
  .ocean-label { font-weight: 600; text-transform: capitalize; font-size: 0.9rem; }
  .ocean-desc {
    font-size: 0.8rem; padding: 0.1rem 0.5rem; border-radius: 999px;
    background: var(--bg-surface1); color: var(--text-sub);
  }
  .ocean-slider-wrap { display: flex; align-items: center; gap: 0.5rem; }
  .ocean-slider-wrap input[type="range"] { flex: 1; }
  .ocean-pole { font-size: 0.72rem; color: var(--text-dim); min-width: 5rem; }
  .ocean-pole:last-child { text-align: right; }
  .ocean-val { font-size: 0.8rem; color: var(--text-dim); text-align: right; }

  .mod-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr)); gap: 0.6rem; }
  .mod-item {
    display: flex; flex-direction: column; gap: 0.2rem;
    padding: 0.6rem 0.75rem; background: var(--bg-surface1); border-radius: var(--radius-sm);
  }
  .mod-label { font-size: 0.8rem; color: var(--text-dim); }
  .mod-val { font-size: 1.1rem; font-weight: 700; }
  .hint { font-size: 0.8rem; color: var(--text-dim); }

  .actions-bar {
    display: flex; gap: 1rem; align-items: center; margin-top: 1.25rem;
    padding-top: 1rem; border-top: 1px solid var(--border);
  }
  .status-toast {
    margin-top: 1rem; padding: 0.5rem 1rem;
    background: var(--bg-surface1); border-radius: var(--radius-sm);
    font-size: 0.85rem; color: var(--text-sub);
  }
</style>
