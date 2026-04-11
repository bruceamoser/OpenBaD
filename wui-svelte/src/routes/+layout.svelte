<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { wsStatus, fsmState } from '$lib/stores/websocket';
  import { get as apiGet, post as apiPost } from '$lib/api/client';

  let { children }: { children: Snippet } = $props();

  // ----------------------------------------------------------------
  // Nav items
  // ----------------------------------------------------------------

  const NAV_ITEMS = [
    { href: '/',          label: 'Health',    icon: '❤' },
    { href: '/chat',      label: 'Chat',      icon: '💬' },
    { href: '/providers', label: 'Providers', icon: '⚙' },
    { href: '/senses',    label: 'Senses',    icon: '👁' },
    { href: '/toolbelt',  label: 'Toolbelt',  icon: '🔧' },
    { href: '/entity',    label: 'Entity',    icon: '👤' },
  ];

  // ----------------------------------------------------------------
  // Responsive hamburger
  // ----------------------------------------------------------------

  let sidebarOpen = $state(true);

  function toggleSidebar(): void {
    sidebarOpen = !sidebarOpen;
  }

  // ----------------------------------------------------------------
  // Active route
  // ----------------------------------------------------------------

  let pathname = $derived($page.url.pathname);

  function isActive(href: string): boolean {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  }

  // ----------------------------------------------------------------
  // Connection status
  // ----------------------------------------------------------------

  let wsStatusVal = $derived($wsStatus);
  let fsmVal = $derived($fsmState?.state ?? 'IDLE');

  function statusColor(s: string): string {
    if (s === 'connected') return '#22c55e';
    if (s === 'connecting') return '#eab308';
    return '#ef4444';
  }

  // ----------------------------------------------------------------
  // First-run wizard
  // ----------------------------------------------------------------

  let showWizard = $state(false);
  let wizardStep = $state(0);
  const WIZARD_STEPS = [
    'User Profile',
    'Assistant Personality',
    'Provider Setup',
    'Senses Check',
  ];

  // Wizard field state
  let wUser = $state({ name: '', communication_style: 'casual' });
  let wAssistant = $state({
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stability: 0.5,
  });
  let wProvider = $state({ name: '', model: '' });
  let wSenses = $state({
    vision: true,
    hearing: true,
    speech: true,
  });

  async function checkFirstRun(): Promise<void> {
    try {
      const res = await apiGet<{ first_run: boolean }>(
        '/api/setup-status',
      );
      showWizard = res.first_run;
    } catch {
      // API not available; skip wizard
    }
  }

  async function finishWizard(): Promise<void> {
    try {
      await apiPost('/api/setup', {
        user: wUser,
        assistant: wAssistant,
        provider: wProvider,
        senses: wSenses,
      });
    } catch {
      // best effort
    }
    showWizard = false;
  }

  function skipWizard(): void {
    showWizard = false;
  }

  function nextStep(): void {
    if (wizardStep < WIZARD_STEPS.length - 1) {
      wizardStep += 1;
    } else {
      finishWizard();
    }
  }

  function prevStep(): void {
    if (wizardStep > 0) wizardStep -= 1;
  }

  onMount(() => { checkFirstRun(); });
</script>

<!-- First-run wizard overlay -->
{#if showWizard}
  <div class="wizard-overlay">
    <div class="wizard-card">
      <h2>OpenBaD Setup — Step {wizardStep + 1} of {WIZARD_STEPS.length}</h2>
      <h3>{WIZARD_STEPS[wizardStep]}</h3>

      {#if wizardStep === 0}
        <label>Name
          <input type="text" bind:value={wUser.name} />
        </label>
        <label>Communication Style
          <select bind:value={wUser.communication_style}>
            <option value="casual">Casual</option>
            <option value="formal">Formal</option>
            <option value="terse">Terse</option>
          </select>
        </label>
      {:else if wizardStep === 1}
        {#each Object.keys(wAssistant) as trait}
          <label>{trait}
            <input
              type="range" min="0" max="1" step="0.05"
              value={wAssistant[trait as keyof typeof wAssistant]}
              oninput={(e: Event) => {
                (wAssistant as any)[trait] =
                  parseFloat((e.target as HTMLInputElement).value);
              }}
            />
          </label>
        {/each}
      {:else if wizardStep === 2}
        <label>Provider Name
          <input type="text" bind:value={wProvider.name} />
        </label>
        <label>Model
          <input type="text" bind:value={wProvider.model} />
        </label>
      {:else if wizardStep === 3}
        <label>
          <input type="checkbox" bind:checked={wSenses.vision} />
          Vision
        </label>
        <label>
          <input type="checkbox" bind:checked={wSenses.hearing} />
          Hearing
        </label>
        <label>
          <input type="checkbox" bind:checked={wSenses.speech} />
          Speech
        </label>
      {/if}

      <div class="wizard-actions">
        {#if wizardStep > 0}
          <button onclick={prevStep}>Back</button>
        {/if}
        <button onclick={nextStep}>
          {wizardStep === WIZARD_STEPS.length - 1 ? 'Finish' : 'Next'}
        </button>
        <button class="skip-btn" onclick={skipWizard}>Skip</button>
      </div>
    </div>
  </div>
{/if}

<div class="app-shell" class:sidebar-collapsed={!sidebarOpen}>
  <!-- Top bar -->
  <header class="top-bar">
    <button class="hamburger" onclick={toggleSidebar}>☰</button>
    <span class="app-title">OpenBaD</span>
    <div class="top-indicators">
      <span
        class="ws-dot"
        style="background:{statusColor(wsStatusVal)}"
        title="WebSocket: {wsStatusVal}"
      ></span>
      <span class="fsm-chip">{fsmVal}</span>
    </div>
  </header>

  <!-- Sidebar -->
  <nav class="side-nav" class:open={sidebarOpen}>
    <div class="brand-mark">OB</div>
    <ul>
      {#each NAV_ITEMS as item}
        <li class:active={isActive(item.href)}>
          <a href={item.href}>
            <span class="nav-icon">{item.icon}</span>
            <span class="nav-label">{item.label}</span>
          </a>
        </li>
      {/each}
    </ul>
  </nav>

  <main class="workspace">
    {@render children()}
  </main>
</div>

<style>
  .app-shell {
    display: grid;
    grid-template-areas:
      "topbar topbar"
      "sidebar main";
    grid-template-columns: 14rem 1fr;
    grid-template-rows: auto 1fr;
    height: 100vh;
  }
  .app-shell.sidebar-collapsed {
    grid-template-columns: 0 1fr;
  }

  .top-bar {
    grid-area: topbar;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 1rem;
    background: #1e1e2e;
    border-bottom: 1px solid #333;
  }
  .hamburger {
    background: none;
    border: none;
    font-size: 1.3rem;
    cursor: pointer;
    color: inherit;
  }
  .app-title { font-weight: 700; }
  .top-indicators {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .ws-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
  }
  .fsm-chip {
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border: 1px solid #555;
    border-radius: 4px;
  }

  .side-nav {
    grid-area: sidebar;
    background: #181825;
    padding: 1rem 0;
    overflow: hidden;
    transition: width 0.2s ease;
  }
  .side-nav:not(.open) {
    width: 0;
    padding: 0;
  }
  .brand-mark {
    text-align: center;
    font-size: 1.5rem;
    font-weight: 800;
    margin-bottom: 1rem;
  }
  .side-nav ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .side-nav li a {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem;
    text-decoration: none;
    color: inherit;
  }
  .side-nav li.active a {
    background: rgba(59, 130, 246, 0.2);
    border-left: 3px solid #3b82f6;
    font-weight: 600;
  }
  .nav-icon { font-size: 1.1rem; }

  .workspace {
    grid-area: main;
    padding: 1rem 1.5rem;
    overflow-y: auto;
  }

  /* Responsive: collapse sidebar */
  @media (max-width: 768px) {
    .app-shell {
      grid-template-columns: 0 1fr;
    }
    .side-nav {
      position: fixed;
      top: 0;
      left: 0;
      height: 100vh;
      width: 14rem;
      z-index: 100;
      transform: translateX(-100%);
      transition: transform 0.2s ease;
    }
    .side-nav.open {
      transform: translateX(0);
    }
  }

  /* Wizard overlay */
  .wizard-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 200;
  }
  .wizard-card {
    background: #1e1e2e;
    padding: 2rem;
    border-radius: 12px;
    max-width: 30rem;
    width: 90%;
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
  }
  .wizard-card label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .wizard-card input,
  .wizard-card select {
    padding: 0.3rem 0.5rem;
  }
  .wizard-actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .wizard-actions button { padding: 0.4rem 1rem; }
  .skip-btn { opacity: 0.6; margin-left: auto; }
</style>
