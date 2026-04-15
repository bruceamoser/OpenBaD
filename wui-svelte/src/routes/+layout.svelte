<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { wsStatus, fsmState, connect, disconnect } from '$lib/stores/websocket';
  import { get as apiGet, post as apiPost } from '$lib/api/client';
  import { resolveOnboardingRedirect } from '$lib/api/onboarding';

  let { children }: { children: Snippet } = $props();

  const NAV_ITEMS = [
    { href: '/',          label: 'Health',    icon: '❤' },
    { href: '/chat',      label: 'Chat',      icon: '💬' },
    { href: '/mqtt',      label: 'MQTT',      icon: '📡' },
    { href: '/tasks',     label: 'Tasks',     icon: '📋' },
    { href: '/research',  label: 'Research',  icon: '🔬' },
    { href: '/usage',     label: 'Usage',     icon: '◔' },
    { href: '/providers', label: 'Providers', icon: '⚙' },
    { href: '/senses',    label: 'Senses',    icon: '👁' },
    { href: '/toolbelt',  label: 'Toolbelt',  icon: '🔧' },
    { href: '/immune',    label: 'Immune',    icon: '🛡' },
    { href: '/skills',     label: 'Skills',     icon: '🛠' },
    { href: '/scheduling', label: 'Scheduling', icon: '⏰' },
    { href: '/entity',     label: 'Entity',     icon: '👤' },
    { href: '/debug',      label: 'Debug',      icon: '🐛' },
  ];

  let sidebarOpen = $state(true);
  let appVersion = $state('0.1.0');
  function toggleSidebar(): void { sidebarOpen = !sidebarOpen; }

  type AccessRequest = {
    request_id: string;
    requested_path: string;
    normalized_root: string;
    requester: string;
    reason: string;
    created_at: number;
  };

  let pathname = $derived($page.url.pathname);
  function isActive(href: string): boolean {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  }

  let wsStatusVal = $derived($wsStatus);
  let fsmVal = $derived($fsmState?.current_state?.toUpperCase() ?? 'IDLE');
  let pendingAccessRequests = $state<AccessRequest[]>([]);
  let accessModalOpen = $state(false);
  let accessDecisionBusy = $state(false);
  let accessStatusMsg = $state('');
  let accessPollHandle: ReturnType<typeof setInterval> | null = null;

  function statusColor(s: string): string {
    if (s === 'connected') return 'var(--green)';
    if (s === 'connecting') return 'var(--yellow)';
    return 'var(--red)';
  }

  function fsmColor(s: string): string {
    switch (s) {
      case 'IDLE':      return 'var(--green)';
      case 'ACTIVE':    return 'var(--blue)';
      case 'THROTTLED': return 'var(--yellow)';
      case 'SLEEP':     return 'var(--mauve)';
      case 'EMERGENCY': return 'var(--red)';
      default:          return 'var(--text-dim)';
    }
  }

  // First-run wizard
  let showWizard = $state(false);
  let wizardStep = $state(0);
  const WIZARD_STEPS = ['User Profile','Assistant Personality','Provider Setup','Senses Check'];
  let wUser = $state({ name: '', communication_style: 'casual' });
  let wAssistant = $state({
    openness: 0.5, conscientiousness: 0.5, extraversion: 0.5,
    agreeableness: 0.5, stability: 0.5,
  });
  let wProvider = $state({ name: '', model: '' });
  let wSenses = $state({ vision: true, hearing: true, speech: true });

  async function checkOnboarding(): Promise<void> {
    try {
      showWizard = false;
      const redirectTo = await resolveOnboardingRedirect(apiGet);
      const currentRoute = `${$page.url.pathname}${$page.url.search}`;
      if (redirectTo && currentRoute !== redirectTo) {
        await goto(redirectTo, { replaceState: true });
      }
    } catch { }
  }

  async function loadVersion(): Promise<void> {
    try {
      const res = await apiGet<{ version: string }>('/api/version');
      appVersion = res.version;
    } catch { }
  }

  async function loadPendingAccessRequests(): Promise<void> {
    try {
      const data = await apiGet<{ pending_requests: AccessRequest[] }>('/api/toolbelt/access');
      pendingAccessRequests = data.pending_requests ?? [];
      if (pendingAccessRequests.length > 0) {
        accessModalOpen = true;
      } else if (!accessDecisionBusy) {
        accessModalOpen = false;
        accessStatusMsg = '';
      }
    } catch {
      // keep the current modal state if the fetch fails
    }
  }

  function fmtAccessTime(ts: number): string {
    const date = new Date(ts * 1000);
    return Number.isNaN(date.getTime()) ? String(ts) : date.toLocaleString();
  }

  async function approvePathRequest(requestId: string): Promise<void> {
    accessDecisionBusy = true;
    accessStatusMsg = '';
    try {
      await apiPost(`/api/toolbelt/access/requests/${requestId}/approve`, { approved_by: 'user' });
      accessStatusMsg = 'Path access approved.';
      await loadPendingAccessRequests();
    } catch (e) {
      accessStatusMsg = `Approve failed: ${e}`;
    } finally {
      accessDecisionBusy = false;
    }
  }

  async function denyPathRequest(requestId: string): Promise<void> {
    accessDecisionBusy = true;
    accessStatusMsg = '';
    try {
      await apiPost(`/api/toolbelt/access/requests/${requestId}/deny`, { denied_by: 'user', reason: 'User denied access' });
      accessStatusMsg = 'Path access denied.';
      await loadPendingAccessRequests();
    } catch (e) {
      accessStatusMsg = `Deny failed: ${e}`;
    } finally {
      accessDecisionBusy = false;
    }
  }

  async function finishWizard(): Promise<void> {
    try {
      await apiPost('/api/setup', {
        user: wUser, assistant: wAssistant, provider: wProvider, senses: wSenses,
      });
    } catch { }
    showWizard = false;
  }
  function skipWizard(): void { showWizard = false; }
  function nextStep(): void {
    if (wizardStep < WIZARD_STEPS.length - 1) wizardStep += 1;
    else finishWizard();
  }
  function prevStep(): void { if (wizardStep > 0) wizardStep -= 1; }

  onMount(() => {
    connect();
    checkOnboarding();
    loadVersion();
    loadPendingAccessRequests();
    accessPollHandle = setInterval(() => {
      void loadPendingAccessRequests();
    }, 3000);
  });

  onDestroy(() => {
    if (accessPollHandle) {
      clearInterval(accessPollHandle);
      accessPollHandle = null;
    }
    disconnect();
  });
</script>

<!-- First-run wizard overlay -->
{#if showWizard}
  <div class="wizard-overlay">
    <div class="wizard-card">
      <div class="wizard-header">
        <img class="wizard-logo" src="/logo.png" alt="OpenBaD" />
        <div>
          <h2>OpenBaD Setup</h2>
          <p class="wizard-progress">Step {wizardStep + 1} of {WIZARD_STEPS.length} — {WIZARD_STEPS[wizardStep]}</p>
        </div>
      </div>

      <div class="wizard-steps">
        {#each WIZARD_STEPS as step, i}
          <div class="step-dot" class:done={i < wizardStep} class:current={i === wizardStep}></div>
          {#if i < WIZARD_STEPS.length - 1}
            <div class="step-line" class:done={i < wizardStep}></div>
          {/if}
        {/each}
      </div>

      <div class="wizard-body">
        {#if wizardStep === 0}
          <label>Name <input type="text" bind:value={wUser.name} placeholder="What should I call you?" /></label>
          <label>Communication Style
            <select bind:value={wUser.communication_style}>
              <option value="casual">Casual</option>
              <option value="formal">Formal</option>
              <option value="terse">Terse</option>
            </select>
          </label>
        {:else if wizardStep === 1}
          {#each Object.keys(wAssistant) as trait}
            <label class="trait-label">{trait}
              <div class="trait-row">
                <input type="range" min="0" max="1" step="0.05"
                  value={wAssistant[trait as keyof typeof wAssistant]}
                  oninput={(e: Event) => {
                    (wAssistant as any)[trait] = parseFloat((e.target as HTMLInputElement).value);
                  }} />
                <span class="trait-val">{(wAssistant[trait as keyof typeof wAssistant] * 100).toFixed(0)}%</span>
              </div>
            </label>
          {/each}
        {:else if wizardStep === 2}
          <label>Provider Name <input type="text" bind:value={wProvider.name} placeholder="e.g. ollama, openai" /></label>
          <label>Model <input type="text" bind:value={wProvider.model} placeholder="e.g. llama3, gpt-4o" /></label>
        {:else if wizardStep === 3}
          <div class="sense-checks">
            <label class="check-label"><input type="checkbox" bind:checked={wSenses.vision} /> Vision (screen capture)</label>
            <label class="check-label"><input type="checkbox" bind:checked={wSenses.hearing} /> Hearing (microphone)</label>
            <label class="check-label"><input type="checkbox" bind:checked={wSenses.speech} /> Speech (TTS output)</label>
          </div>
        {/if}
      </div>

      <div class="wizard-actions">
        {#if wizardStep > 0}
          <button class="secondary" onclick={prevStep}>Back</button>
        {/if}
        <button onclick={nextStep}>
          {wizardStep === WIZARD_STEPS.length - 1 ? 'Finish' : 'Next'}
        </button>
        <button class="ghost skip-btn" onclick={skipWizard}>Skip setup</button>
      </div>
    </div>
  </div>
{/if}

{#if accessModalOpen && pendingAccessRequests.length > 0}
  <div class="access-modal-backdrop" role="presentation"></div>
  <div class="access-modal" role="dialog" aria-modal="true" aria-labelledby="access-modal-title">
    <div class="access-modal-card">
      <div class="access-modal-header">
        <h2 id="access-modal-title">System Access Approval Required</h2>
        <p>
          OpenBaD needs permission to access a filesystem path outside the current allowed roots.
          This is a system-level approval, not a chat clarification.
        </p>
      </div>
      <div class="access-modal-list">
        {#each pendingAccessRequests as req}
          <div class="access-modal-item">
            <div class="access-modal-path">{req.normalized_root}</div>
            <div class="access-modal-meta">requested path: {req.requested_path}</div>
            <div class="access-modal-meta">requested by: {req.requester} · {fmtAccessTime(req.created_at)}</div>
            {#if req.reason}
              <div class="access-modal-reason">reason: {req.reason}</div>
            {/if}
            <div class="access-modal-actions">
              <button class="secondary" onclick={() => approvePathRequest(req.request_id)} disabled={accessDecisionBusy}>Approve</button>
              <button class="ghost" onclick={() => denyPathRequest(req.request_id)} disabled={accessDecisionBusy}>Deny</button>
            </div>
          </div>
        {/each}
      </div>
      {#if accessStatusMsg}
        <div class="access-modal-status">{accessStatusMsg}</div>
      {/if}
    </div>
  </div>
{/if}

<div class="app-shell" class:sidebar-collapsed={!sidebarOpen}>
  <!-- Top bar -->
  <header class="top-bar">
    <div class="top-left">
      <button class="hamburger" onclick={toggleSidebar} aria-label="Toggle sidebar">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <rect y="3" width="20" height="2" rx="1" fill="currentColor"/>
          <rect y="9" width="20" height="2" rx="1" fill="currentColor"/>
          <rect y="15" width="20" height="2" rx="1" fill="currentColor"/>
        </svg>
      </button>
    </div>
    <div class="top-right">
      <div class="indicator" title="WebSocket: {wsStatusVal}">
        <span class="ws-dot" style="background:{statusColor(wsStatusVal)}"></span>
        <span class="indicator-label">{wsStatusVal}</span>
      </div>
      <div class="fsm-chip" style="border-color:{fsmColor(fsmVal)}; color:{fsmColor(fsmVal)}">
        {fsmVal}
      </div>
    </div>
  </header>

  <!-- Sidebar -->
  <nav class="side-nav" class:open={sidebarOpen}>
    <div class="nav-brand">
      <img class="app-logo sidebar-logo" src="/logo.png" alt="OpenBaD" />
    </div>
    <ul class="nav-list">
      {#each NAV_ITEMS as item}
        <li class:active={isActive(item.href)}>
          <a href={item.href}>
            <span class="nav-icon">{item.icon}</span>
            <span class="nav-label">{item.label}</span>
          </a>
        </li>
      {/each}
    </ul>
    <div class="nav-footer">
      <div class="nav-divider"></div>
      <span class="nav-version">v{appVersion}</span>
    </div>
  </nav>

  <!-- Backdrop for mobile -->
  {#if sidebarOpen}
    <button class="sidebar-backdrop" onclick={toggleSidebar} aria-label="Close sidebar"></button>
  {/if}

  <main class="workspace">
    {@render children()}
  </main>
</div>

<style>
  /* ============================================================
     App Shell Grid
     ============================================================ */
  .app-shell {
    display: grid;
    grid-template-areas:
      "sidebar topbar"
      "sidebar main";
    grid-template-columns: var(--sidebar-w) 1fr;
    grid-template-rows: var(--topbar-h) 1fr;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
    transition: grid-template-columns 0.25s var(--ease);
  }

  .access-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgb(10 12 16 / 0.7);
    backdrop-filter: blur(4px);
    z-index: 70;
  }

  .access-modal {
    position: fixed;
    inset: 0;
    display: grid;
    place-items: center;
    padding: 1.5rem;
    z-index: 71;
  }

  .access-modal-card {
    width: min(42rem, calc(100vw - 2rem));
    max-height: calc(100vh - 3rem);
    overflow: auto;
    background: var(--bg-surface0);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: 0 24px 80px rgb(0 0 0 / 0.35);
    padding: 1.25rem;
  }

  .access-modal-header h2 {
    margin: 0 0 0.35rem;
  }

  .access-modal-header p {
    margin: 0;
    color: var(--text-dim);
  }

  .access-modal-list {
    display: grid;
    gap: 0.9rem;
    margin-top: 1rem;
  }

  .access-modal-item {
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--bg-surface1);
    padding: 0.9rem;
  }

  .access-modal-path {
    font-weight: 700;
    word-break: break-word;
  }

  .access-modal-meta,
  .access-modal-reason {
    margin-top: 0.35rem;
    color: var(--text-dim);
    font-size: 0.92rem;
    word-break: break-word;
  }

  .access-modal-actions {
    display: flex;
    gap: 0.75rem;
    margin-top: 0.85rem;
  }

  .access-modal-status {
    margin-top: 1rem;
    padding: 0.75rem 0.85rem;
    border-radius: var(--radius-sm);
    background: var(--bg-surface1);
    color: var(--text-sub);
  }
  .app-shell.sidebar-collapsed {
    grid-template-columns: 0 1fr;
  }

  /* ============================================================
     Top Bar
     ============================================================ */
  .top-bar {
    grid-area: topbar;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 1.25rem;
    background: var(--bg-surface0);
    border-bottom: 1px solid var(--border);
    z-index: 10;
  }
  .top-left {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .hamburger {
    background: none;
    border: none;
    padding: 0.35rem;
    border-radius: var(--radius-sm);
    color: var(--text-sub);
    display: flex;
    align-items: center;
    cursor: pointer;
    transition: background 0.15s var(--ease);
  }
  .hamburger:hover {
    background: var(--bg-surface1);
    color: var(--text);
  }
  .app-logo {
    display: block;
    height: auto;
    object-fit: contain;
  }
  .top-right {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .indicator {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .ws-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    box-shadow: 0 0 6px currentColor;
  }
  .indicator-label {
    font-size: 0.75rem;
    color: var(--text-dim);
    text-transform: capitalize;
  }
  .fsm-chip {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 0.2rem 0.65rem;
    border: 1.5px solid;
    border-radius: 999px;
  }

  /* ============================================================
     Sidebar
     ============================================================ */
  .side-nav {
    grid-area: sidebar;
    background: var(--bg-mantle);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: width 0.25s var(--ease);
    border-right: 1px solid var(--border);
    z-index: 20;
  }
  .side-nav:not(.open) {
    width: 0;
    border-right: none;
  }

  .nav-brand {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--border);
  }
  .sidebar-logo {
    width: 10.5rem;
    max-width: 100%;
  }

  .nav-list {
    list-style: none;
    padding: 0.5rem 0;
    flex: 1;
    overflow-y: auto;
  }
  .nav-list li a {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 1.25rem;
    text-decoration: none;
    color: var(--text-sub);
    font-size: 0.9rem;
    font-weight: 500;
    border-left: 3px solid transparent;
    transition: all 0.15s var(--ease);
    white-space: nowrap;
  }
  .nav-list li a:hover {
    background: var(--bg-surface1);
    color: var(--text);
    text-decoration: none;
  }
  .nav-list li.active a {
    background: var(--blue-dim);
    color: var(--blue);
    border-left-color: var(--blue);
    font-weight: 600;
  }
  .nav-icon {
    font-size: 1.1rem;
    width: 1.5rem;
    text-align: center;
    flex-shrink: 0;
  }

  .nav-footer {
    padding: 0.75rem 1.25rem;
    margin-top: auto;
  }
  .nav-divider {
    height: 1px;
    background: var(--border);
    margin-bottom: 0.75rem;
  }
  .nav-version {
    font-size: 0.7rem;
    color: var(--text-dim);
    letter-spacing: 0.05em;
  }

  /* Mobile backdrop */
  .sidebar-backdrop {
    display: none;
  }

  /* ============================================================
     Workspace (main content area)
     ============================================================ */
  .workspace {
    grid-area: main;
    padding: 1.75rem 2rem;
    overflow-y: auto;
    background: var(--bg-base);
  }

  /* ============================================================
     Responsive
     ============================================================ */
  @media (max-width: 768px) {
    .app-shell {
      grid-template-areas:
        "topbar"
        "main";
      grid-template-columns: 1fr;
    }
    .side-nav {
      position: fixed;
      top: 0;
      left: 0;
      height: 100vh;
      width: var(--sidebar-w);
      transform: translateX(-100%);
      transition: transform 0.25s var(--ease);
    }
    .side-nav.open {
      transform: translateX(0);
    }
    .sidebar-backdrop {
      display: block;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.5);
      z-index: 15;
      border: none;
      cursor: default;
    }
    .workspace {
      padding: 1.25rem 1rem;
    }
  }

  /* ============================================================
     Wizard Overlay
     ============================================================ */
  .wizard-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(4px);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 200;
  }
  .wizard-card {
    background: var(--bg-surface0);
    padding: 2rem 2.25rem;
    border-radius: var(--radius-lg);
    max-width: 32rem;
    width: 92%;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
    border: 1px solid var(--border);
  }
  .wizard-header {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .wizard-logo {
    width: 5.5rem;
    height: auto;
    flex-shrink: 0;
    object-fit: contain;
  }
  .wizard-header h2 {
    font-size: 1.2rem;
    margin: 0;
  }
  .wizard-progress {
    font-size: 0.8rem;
    color: var(--text-dim);
    margin: 0;
  }

  .wizard-steps {
    display: flex;
    align-items: center;
    gap: 0;
    padding: 0 0.5rem;
  }
  .step-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--bg-surface2);
    flex-shrink: 0;
    transition: background 0.2s var(--ease);
  }
  .step-dot.done { background: var(--green); }
  .step-dot.current { background: var(--blue); box-shadow: 0 0 8px rgba(137, 180, 250, 0.5); }
  .step-line {
    flex: 1;
    height: 2px;
    background: var(--bg-surface2);
    transition: background 0.2s var(--ease);
  }
  .step-line.done { background: var(--green); }

  .wizard-body {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .wizard-body label {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .trait-label {
    text-transform: capitalize;
  }
  .trait-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .trait-val {
    font-size: 0.8rem;
    color: var(--text-dim);
    width: 3rem;
    text-align: right;
  }
  .sense-checks {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .check-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-direction: row;
    font-size: 0.9rem;
    color: var(--text);
    cursor: pointer;
  }

  .wizard-actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.25rem;
  }
  .skip-btn {
    margin-left: auto;
    font-size: 0.8rem;
  }
</style>
