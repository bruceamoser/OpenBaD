<script lang="ts">
  import { onMount } from 'svelte';
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

<h2>Entity</h2>

<!-- Tab switcher -->
<div class="tab-bar">
  <button
    class:active={tab === 'user'}
    onclick={() => tab = 'user'}
  >User</button>
  <button
    class:active={tab === 'assistant'}
    onclick={() => tab = 'assistant'}
  >Assistant</button>
</div>

<!-- User tab -->
{#if tab === 'user'}
  <Card label="User Profile">
    <div class="form-grid">
      <label>Name
        <input
          type="text"
          bind:value={user.name}
          oninput={() => userDirty = true}
        />
      </label>

      <label>Preferred Name
        <input
          type="text"
          bind:value={user.preferred_name}
          oninput={() => userDirty = true}
        />
      </label>

      <label>Communication Style
        <select
          bind:value={user.communication_style}
          onchange={() => userDirty = true}
        >
          <option value="casual">Casual</option>
          <option value="formal">Formal</option>
          <option value="terse">Terse</option>
        </select>
      </label>

      <fieldset>
        <legend>Expertise Domains</legend>
        {#each user.expertise_domains as domain, i}
          <span class="chip">
            {domain}
            <button
              class="chip-x"
              onclick={() => removeDomain(i)}
            >✕</button>
          </span>
        {/each}
        <div class="add-row">
          <input
            type="text"
            bind:value={domainInput}
            placeholder="Add domain…"
            onkeydown={(e: KeyboardEvent) => {
              if (e.key === 'Enter') addDomain();
            }}
          />
          <button class="small-btn" onclick={addDomain}>Add</button>
        </div>
      </fieldset>
    </div>
  </Card>

  <Card label="Learned Summary (read-only)">
    <p class="ro-summary">
      {user.learned_summary || 'No learned data yet.'}
    </p>
  </Card>

  <div class="actions">
    <button onclick={saveUser} disabled={!userDirty || saving}>
      {saving ? 'Saving…' : 'Save'}
    </button>
    <button class="reset-btn" onclick={resetUser}>
      Reset to Seed
    </button>
  </div>
{/if}

<!-- Assistant tab -->
{#if tab === 'assistant'}
  <Card label="Assistant Profile">
    <div class="form-grid">
      <label>Name
        <input
          type="text"
          bind:value={assistant.name}
          oninput={() => assistantDirty = true}
        />
      </label>

      <label>Persona Summary
        <textarea
          bind:value={assistant.persona_summary}
          oninput={() => assistantDirty = true}
          rows="3"
        ></textarea>
      </label>

      <label>Learning Focus
        <input
          type="text"
          bind:value={assistant.learning_focus}
          oninput={() => assistantDirty = true}
        />
      </label>
    </div>
  </Card>

  <Card label="OCEAN Personality">
    {#each Object.keys(oceanLabels) as trait}
      <div class="ocean-row">
        <label class="ocean-label">{trait}</label>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={assistant[trait as keyof AssistantProfile] as number}
          oninput={(e: Event) => {
            const v = parseFloat((e.target as HTMLInputElement).value);
            (assistant as any)[trait] = v;
            assistantDirty = true;
          }}
        />
        <span class="ocean-val">
          {(assistant[trait as keyof AssistantProfile] as number).toFixed(2)}
        </span>
        <span class="ocean-desc">
          {oceanDesc(trait, assistant[trait as keyof AssistantProfile] as number)}
        </span>
      </div>
    {/each}
  </Card>

  <Card label="Modulation Factors (read-only)">
    {#if assistant.modulation}
      <dl class="mod-factors">
        <dt>Exploration Budget ×</dt>
        <dd>{assistant.modulation.exploration_budget_multiplier.toFixed(2)}</dd>
        <dt>Reasoning Depth ×</dt>
        <dd>{assistant.modulation.max_reasoning_depth_multiplier.toFixed(2)}</dd>
        <dt>Proactive Threshold</dt>
        <dd>{assistant.modulation.proactive_suggestion_threshold.toFixed(2)}</dd>
        <dt>Challenge Probability</dt>
        <dd>{assistant.modulation.challenge_probability.toFixed(2)}</dd>
        <dt>Cortisol Decay ×</dt>
        <dd>{assistant.modulation.cortisol_decay_multiplier.toFixed(2)}</dd>
      </dl>
    {:else}
      <p class="muted">
        Modulation factors unavailable — save to compute.
      </p>
    {/if}
  </Card>

  <div class="actions">
    <button
      onclick={saveAssistant}
      disabled={!assistantDirty || saving}
    >
      {saving ? 'Saving…' : 'Save'}
    </button>
    <button class="reset-btn" onclick={resetAssistant}>
      Reset to Seed
    </button>
  </div>
{/if}

{#if statusMsg}
  <p class="status">{statusMsg}</p>
{/if}

<style>
  .tab-bar {
    display: flex;
    gap: 0;
    margin-bottom: 1rem;
  }
  .tab-bar button {
    padding: 0.5rem 1.2rem;
    border: 1px solid #555;
    background: transparent;
    cursor: pointer;
  }
  .tab-bar button.active {
    background: #333;
    font-weight: 600;
  }
  .tab-bar button:first-child {
    border-radius: 6px 0 0 6px;
  }
  .tab-bar button:last-child {
    border-radius: 0 6px 6px 0;
  }
  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .form-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .form-grid input,
  .form-grid select,
  .form-grid textarea {
    padding: 0.3rem 0.5rem;
  }
  fieldset {
    border: 1px solid #444;
    border-radius: 4px;
    padding: 0.5rem;
  }
  legend { font-weight: 600; }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: #444;
    padding: 0.15rem 0.5rem;
    border-radius: 12px;
    font-size: 0.85rem;
    margin: 0.15rem;
  }
  .chip-x {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0;
  }
  .add-row {
    display: flex;
    gap: 0.3rem;
    margin-top: 0.3rem;
  }
  .add-row input { flex: 1; }
  .small-btn {
    padding: 0.2rem 0.5rem;
    font-size: 0.8rem;
  }
  .ro-summary {
    opacity: 0.7;
    font-style: italic;
    white-space: pre-wrap;
  }

  .ocean-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
    flex-wrap: wrap;
  }
  .ocean-label {
    width: 9rem;
    font-weight: 600;
    text-transform: capitalize;
  }
  .ocean-row input[type="range"] { flex: 1; min-width: 8rem; }
  .ocean-val { width: 3rem; text-align: right; font-size: 0.85rem; }
  .ocean-desc {
    width: 7rem;
    font-size: 0.8rem;
    opacity: 0.7;
  }

  .mod-factors {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.3rem 1rem;
  }
  .mod-factors dt { font-weight: 600; }
  .mod-factors dd { text-align: right; margin: 0; }
  .muted { opacity: 0.5; }

  .actions {
    display: flex;
    gap: 1rem;
    align-items: center;
    margin-top: 1rem;
  }
  .actions button { padding: 0.5rem 1.5rem; }
  .reset-btn { opacity: 0.7; }
  .status { font-size: 0.85rem; opacity: 0.8; margin-top: 0.5rem; }
</style>
