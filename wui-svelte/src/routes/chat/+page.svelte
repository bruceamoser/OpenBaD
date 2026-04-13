<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { get as apiGet, post as apiPost } from '$lib/api/client';
  import { resolveOnboardingRedirect } from '$lib/api/onboarding';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  // Configure marked for code-friendly output
  marked.setOptions({ breaks: true, gfm: true });

  function renderMarkdown(text: string): string {
    if (!text) return '';
    const raw = marked.parse(text) as string;
    return DOMPurify.sanitize(raw);
  }

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
    timestamp: string;
    reasoning?: string;
    provider?: string;
    model?: string;
  }

  interface SessionOption {
    key: string;
    session_id: string;
    label: string;
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  let messages: ChatMessage[] = $state([]);
  let inputText = $state('');
  let reasoningEnabled = $state(false);
  let system: 'CHAT' | 'REASONING' = $derived(reasoningEnabled ? 'REASONING' : 'CHAT');
  let streaming = $state(false);
  let tokensUsed = $state(0);
  let tokensMax = $state(8192);
  let sessionId = $state('');
  let sessionOptions: SessionOption[] = $state([]);
  let selectedSessionId = $state('chat-main');
  let assistantName = $state('Assistant');
  let copiedMsgTimestamp = $state<string | null>(null);
  let onboardingHint = $derived($page.url.searchParams.get('onboarding') ?? '');
  let onboardingTransition = $state(false);

  const CHAT_SESSION_STORAGE_KEY = 'openbad.chat.sessionId';

  // Scroll management
  let chatContainer: HTMLDivElement | undefined = $state();
  let autoScroll = $state(true);

  function scrollToBottom(): void {
    if (chatContainer && autoScroll) {
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  }

  function onScroll(): void {
    if (!chatContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = chatContainer;
    autoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  function persistSession(nextSessionId: string): void {
    const storageKey = currentSessionStorageKey();
    sessionId = nextSessionId;
    localStorage.setItem(storageKey, nextSessionId);
    if (onboardingHint) {
      localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    }
  }

  function currentSessionStorageKey(): string {
    const sessionScope = selectedSessionId || 'chat-main';
    return onboardingHint
      ? `${CHAT_SESSION_STORAGE_KEY}.${sessionScope}.${onboardingHint}`
      : `${CHAT_SESSION_STORAGE_KEY}.${sessionScope}`;
  }

  function clearSession(): void {
    sessionId = '';
    localStorage.removeItem(currentSessionStorageKey());
    if (onboardingHint) {
      localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    }
  }

  function extractOnboardingCompletionPayload(text: string): string | null {
    const trimmed = text.trim();
    if (!trimmed) return null;

    const fencedMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
    const candidate = (fencedMatch?.[1] ?? trimmed).trim();

    try {
      const parsed = JSON.parse(candidate) as Record<string, unknown>;
      return typeof parsed.name === 'string' ? candidate : null;
    } catch {
      return null;
    }
  }

  async function resetOnboardingConversation(): Promise<void> {
    onboardingTransition = true;
    clearSession();
    messages = [];
    tokensUsed = 0;
    tokensMax = 8192;
    await tick();
  }

  function ensureOnboardingIntro(): void {
    if (onboardingTransition) return;
    if (messages.length > 0) return;

    if (onboardingHint === 'assistant') {
      messages = [
        {
          role: 'assistant',
          content:
            "Let's establish my identity first. If you already know my name, role, and communication style, give them to me in one message and I'll use them without re-asking. What should I call myself, what role do you want me to play, and how should I communicate with you? You can adjust these values later on the Entity page if we want to refine them.",
          timestamp: new Date().toISOString(),
        },
      ];
      return;
    }

    if (onboardingHint === 'user') {
      messages = [
        {
          role: 'assistant',
          content:
            "Now I'd like to learn about you. What should I call you, what do you work on, and how do you prefer I communicate? You can adjust these values later on the Entity page too.",
          timestamp: new Date().toISOString(),
        },
      ];
    }
  }

  async function finalizeOnboardingInterview(interviewText: string): Promise<void> {
    if (onboardingHint !== 'assistant' && onboardingHint !== 'user') return;

    const completionPath =
      onboardingHint === 'assistant'
        ? '/api/onboarding/assistant/complete'
        : '/api/onboarding/user/complete';

    try {
      await apiPost<{ success: boolean }>(completionPath, {
        interview_text: interviewText,
      });
    } catch {
      return;
    }

    const redirectTo = await resolveOnboardingRedirect(apiGet);
    const currentRoute = `${$page.url.pathname}${$page.url.search}`;

    if (redirectTo && redirectTo !== currentRoute) {
      await resetOnboardingConversation();
      await goto(redirectTo, { replaceState: true });
      onboardingTransition = false;
      ensureOnboardingIntro();
      return;
    }

    if (!redirectTo && currentRoute !== '/chat') {
      await resetOnboardingConversation();
      await goto('/chat', { replaceState: true });
      onboardingTransition = false;
      return;
    }

    onboardingTransition = false;
  }

  $effect(() => {
    ensureOnboardingIntro();
  });

  async function loadHistory(existingSessionId: string): Promise<void> {
    if (!existingSessionId) return;

    try {
      const data = await apiGet<{ messages: ChatMessage[] }>(
        `/api/chat/history?session_id=${encodeURIComponent(existingSessionId)}`,
      );
      messages = data.messages;
      await tick();
      scrollToBottom();
    } catch {
      messages = [];
    }
  }

  async function loadSessions(): Promise<void> {
    try {
      const data = await apiGet<{ sessions: SessionOption[] }>('/api/sessions');
      const sessions = data.sessions ?? [];
      sessionOptions = sessions.length > 0
        ? sessions
        : [{ key: 'chat', session_id: 'chat-main', label: 'Chat' }];

      if (!sessionOptions.some((s) => s.session_id === selectedSessionId)) {
        selectedSessionId = sessionOptions[0].session_id;
      }
    } catch {
      sessionOptions = [{ key: 'chat', session_id: 'chat-main', label: 'Chat' }];
      selectedSessionId = 'chat-main';
    }
  }

  async function switchSession(nextSessionId: string): Promise<void> {
    if (streaming) return;
    selectedSessionId = nextSessionId;
    const storedSessionId = localStorage.getItem(currentSessionStorageKey()) ?? '';
    if (storedSessionId) {
      persistSession(storedSessionId);
      await loadHistory(storedSessionId);
      return;
    }
    // For autonomy sessions (research, tasks, doctor) there is no stored
    // UUID — the session_id is the selectedSessionId itself.
    sessionId = nextSessionId;
    await loadHistory(nextSessionId);
  }

  // ----------------------------------------------------------------
  // Send message
  // ----------------------------------------------------------------

  async function send(): Promise<void> {
    const text = inputText.trim();
    if (!text || streaming) return;

    if (!sessionId) {
      persistSession(crypto.randomUUID());
    }

    const userMsg: ChatMessage = {
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };
    messages = [...messages, userMsg];
    inputText = '';

    await tick();
    scrollToBottom();

    // Placeholder assistant message for streaming
    const assistantMsg: ChatMessage = {
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    };
    messages = [...messages, assistantMsg];
    streaming = true;

    try {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, system, session_id: sessionId }),
      });

      if (!resp.ok) {
        assistantMsg.content = `Error: ${resp.statusText}`;
        messages = [...messages.slice(0, -1), assistantMsg];
        streaming = false;
        return;
      }

      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });

          // Parse SSE data lines
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            const raw = line.slice(6);
            if (raw === '[DONE]') break;
            try {
              const parsed = JSON.parse(raw);
              if (parsed.session_id && parsed.session_id !== sessionId) {
                persistSession(parsed.session_id);
              }
              if (parsed.token) {
                assistantMsg.content += parsed.token;
              }
              if (parsed.reasoning) {
                assistantMsg.reasoning =
                  (assistantMsg.reasoning ?? '') + parsed.reasoning;
              }
              if (parsed.tokens_used !== undefined) {
                tokensUsed = parsed.tokens_used;
              }
              if (parsed.tokens_max !== undefined) {
                tokensMax = parsed.tokens_max;
              }
              if (parsed.done && parsed.provider) {
                assistantMsg.provider = parsed.provider;
              }
              if (parsed.done && parsed.model) {
                assistantMsg.model = parsed.model;
              }
            } catch {
              // non-JSON SSE line, ignore
            }
          }

          messages = [
            ...messages.slice(0, -1),
            { ...assistantMsg },
          ];
          await tick();
          scrollToBottom();
        }
      }
    } catch (e) {
      assistantMsg.content = `Error: ${e}`;
      messages = [...messages.slice(0, -1), assistantMsg];
    } finally {
      streaming = false;
    }

    const rawAssistantContent = assistantMsg.content;
    const onboardingPayload = extractOnboardingCompletionPayload(rawAssistantContent);

    if (onboardingPayload && (onboardingHint === 'assistant' || onboardingHint === 'user')) {
      assistantMsg.content =
        onboardingHint === 'assistant'
          ? 'Assistant identity saved. Moving on to your profile.'
          : 'User profile saved. Onboarding is complete.';
      messages = [...messages.slice(0, -1), { ...assistantMsg }];
      await tick();
      scrollToBottom();
    }

    if (onboardingPayload) {
      await finalizeOnboardingInterview(onboardingPayload);
    }
  }

  async function copyMessage(content: string, timestamp: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(content);
      copiedMsgTimestamp = timestamp;
      setTimeout(() => { copiedMsgTimestamp = null; }, 1500);
    } catch {
      // clipboard not available
    }
  }

  async function newChat(): Promise<void> {
    if (streaming) return;
    clearSession();
    messages = [];
    tokensUsed = 0;
    tokensMax = 8192;
    await tick();
  }

  function handleKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // ----------------------------------------------------------------
  // Init
  // ----------------------------------------------------------------

  onMount(async () => {
    try {
      const redirectTo = await resolveOnboardingRedirect(apiGet);
      const currentRoute = `${$page.url.pathname}${$page.url.search}`;
      if (redirectTo && currentRoute !== redirectTo) {
        await goto(redirectTo, { replaceState: true });
        return;
      }
    } catch (err) {
      console.error('Failed to check onboarding status:', err);
    }

    await loadSessions();

    if (onboardingHint) {
      localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    }

    const storedSessionId = localStorage.getItem(currentSessionStorageKey()) ?? '';
    if (storedSessionId) {
      persistSession(storedSessionId);
      await loadHistory(storedSessionId);
    } else if (selectedSessionId && selectedSessionId !== 'chat-main') {
      // Autonomy sessions (research, tasks, doctor) have no stored UUID.
      sessionId = selectedSessionId;
      await loadHistory(selectedSessionId);
    }
    scrollToBottom();

    try {
      const assistant = await apiGet<{ name?: string }>('/api/entity/assistant');
      if (assistant?.name) assistantName = assistant.name;
    } catch {
      // leave default
    }
  });
</script>

<div class="chat-layout">
  <!-- Header bar -->
  <div class="chat-header">
    <div class="chat-title">
      <h2>Chat</h2>
      <span class="badge">{system}</span>
    </div>
    <div class="header-controls">
      <div class="control-group">
        <span class="control-label">Session</span>
        <select
          bind:value={selectedSessionId}
          onchange={(e) => switchSession((e.currentTarget as HTMLSelectElement).value)}
          disabled={streaming}
        >
          {#each sessionOptions as s}
            <option value={s.session_id}>{s.label}</option>
          {/each}
        </select>
      </div>
      <label class="cot-toggle">
        <input type="checkbox" bind:checked={reasoningEnabled} />
        <span>Reasoning</span>
      </label>
      <button
        class="new-chat-btn"
        onclick={newChat}
        disabled={streaming}
        title="Start a new conversation"
      >New chat</button>
    </div>
  </div>

  <!-- Context budget -->
  <div class="budget-bar">
    <div
      class="budget-fill"
      style="width:{Math.min(tokensUsed / tokensMax * 100, 100)}%"
    ></div>
    <span class="budget-label">
      {tokensUsed.toLocaleString()} / {tokensMax.toLocaleString()} tokens
    </span>
  </div>

  <!-- Message list -->
  <div
    class="messages"
    bind:this={chatContainer}
    onscroll={onScroll}
  >
    {#if messages.length === 0}
      <div class="empty-state">
        <div class="empty-icon">💬</div>
        <h3>{onboardingHint ? 'Onboarding Chat' : 'Start a conversation'}</h3>
        <p>
          {onboardingHint
            ? 'Answer the assistant in chat to complete the remaining identity setup.'
            : 'Type a message below to begin chatting with OpenBaD.'}
        </p>
      </div>
    {/if}
    {#each messages as msg}
      <div class="msg {msg.role}">
        <div class="avatar">
          {msg.role === 'user' ? '👤' : '🤖'}
        </div>
        <div class="bubble">
          <div class="bubble-header">
            <span class="role-label">{msg.role === 'user' ? 'You' : assistantName}</span>
            <time class="ts">{new Date(msg.timestamp).toLocaleTimeString()}</time>
            {#if msg.role === 'assistant' && (msg.provider || msg.model)}
              <span class="model-tag">{[msg.provider, msg.model].filter(Boolean).join(' · ')}</span>
            {/if}
          </div>
          <div class="content">{@html renderMarkdown(msg.content)}</div>
          {#if msg.reasoning}
            <details class="reasoning">
              <summary>Reasoning trace</summary>
              <pre>{msg.reasoning}</pre>
            </details>
          {/if}
          <div class="bubble-actions">
            <button
              class="copy-btn"
              onclick={() => copyMessage(msg.content, msg.timestamp)}
              title="Copy message"
            >{copiedMsgTimestamp === msg.timestamp ? '✓ Copied' : 'Copy'}</button>
          </div>
        </div>
      </div>
    {/each}
    {#if streaming}
      <div class="streaming-indicator">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
    {/if}
  </div>

  <!-- Input -->
  <div class="input-area">
    <textarea
      bind:value={inputText}
      placeholder="Type a message… (Shift+Enter for newline)"
      onkeydown={handleKeydown}
      rows="2"
    ></textarea>
    <button onclick={send} disabled={streaming || !inputText.trim()}>
      Send
    </button>
  </div>
</div>

<style>
  .chat-layout {
    display: flex;
    flex-direction: column;
    height: calc(100vh - var(--topbar-h) - 3.5rem);
  }

  .chat-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem;
    padding-bottom: 1rem;
  }
  .chat-title {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .chat-title h2 { margin: 0; }
  .header-controls {
    display: flex;
    gap: 1rem;
    align-items: center;
  }
  .control-group {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .control-label {
    font-size: 0.8rem;
    color: var(--text-dim);
  }
  .control-group select {
    width: auto;
    padding: 0.3rem 0.6rem;
    font-size: 0.8rem;
  }
  .cot-toggle {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: var(--text-sub);
    cursor: pointer;
  }
  .new-chat-btn {
    padding: 0.3rem 0.75rem;
    font-size: 0.8rem;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text-sub);
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }
  .new-chat-btn:hover:not(:disabled) {
    border-color: var(--blue);
    color: var(--blue);
  }
  .new-chat-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .budget-bar {
    position: relative;
    height: 18px;
    background: var(--bg-surface1);
    border-radius: 9px;
    overflow: hidden;
    margin-bottom: 0.75rem;
  }
  .budget-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--blue), var(--mauve));
    transition: width 0.3s var(--ease);
    border-radius: 9px;
  }
  .budget-label {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 0.7rem;
    font-weight: 600;
    line-height: 18px;
    color: var(--text);
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 0.5rem 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    flex: 1;
    gap: 0.5rem;
    color: var(--text-dim);
  }
  .empty-icon { font-size: 2.5rem; opacity: 0.5; }
  .empty-state h3 { color: var(--text-sub); font-weight: 600; }
  .empty-state p { font-size: 0.9rem; }

  .msg {
    display: flex;
    gap: 0.6rem;
    max-width: 80%;
  }
  .msg.user { align-self: flex-end; flex-direction: row-reverse; }
  .msg.assistant { align-self: flex-start; }

  .avatar {
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg-surface1);
    font-size: 0.9rem;
    flex-shrink: 0;
  }

  .bubble {
    padding: 0.65rem 0.9rem;
    border-radius: var(--radius-md);
    word-break: break-word;
  }
  .msg.user .bubble {
    background: var(--blue);
    color: #08111f;
    border-bottom-right-radius: 4px;
  }
  .msg.assistant .bubble {
    background: var(--bg-surface1);
    border: 1px solid var(--border);
    color: var(--text);
    border-bottom-left-radius: 4px;
  }
  .bubble-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }
  .role-label {
    font-size: 0.75rem;
    font-weight: 600;
    opacity: 0.7;
  }
  .ts {
    font-size: 0.65rem;
    opacity: 0.5;
  }
  .model-tag {
    font-size: 0.65rem;
    opacity: 0.55;
    background: var(--bg-surface2);
    border-radius: 3px;
    padding: 0.05em 0.4em;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
  }
  .content { margin: 0; font-size: 0.9rem; line-height: 1.6; }
  .content :global(p) { margin: 0.3em 0; }
  .content :global(p:first-child) { margin-top: 0; }
  .content :global(p:last-child) { margin-bottom: 0; }
  .content :global(code) {
    background: var(--bg-surface2);
    color: var(--peach);
    padding: 0.15em 0.35em;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85em;
  }
  .content :global(pre) {
    background: var(--bg-base);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.75rem;
    overflow-x: auto;
    margin: 0.5em 0;
  }
  .content :global(pre code) {
    background: none;
    color: var(--text);
    padding: 0;
    font-size: 0.82em;
  }
  .content :global(a) {
    color: var(--blue);
    text-decoration: underline;
  }
  .content :global(strong) {
    color: var(--text);
    font-weight: 700;
  }
  .content :global(ul), .content :global(ol) {
    padding-left: 1.4em;
    margin: 0.4em 0;
  }
  .content :global(blockquote) {
    border-left: 3px solid var(--mauve);
    margin: 0.4em 0;
    padding: 0.2em 0.8em;
    color: var(--text-sub);
  }
  .msg.user .role-label,
  .msg.user .ts,
  .msg.user .content,
  .msg.user .content :global(p),
  .msg.user .content :global(li),
  .msg.user .content :global(strong),
  .msg.user .content :global(blockquote) {
    color: inherit;
    opacity: 1;
  }
  .msg.user .content :global(a) {
    color: #10233f;
  }
  .msg.user .content :global(code) {
    background: rgba(8, 17, 31, 0.12);
    color: #08111f;
  }

  .reasoning {
    margin-top: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px solid rgba(255,255,255,0.1);
  }
  .reasoning summary {
    font-size: 0.75rem;
    cursor: pointer;
    color: var(--text-dim);
  }
  .reasoning pre {
    white-space: pre-wrap;
    font-size: 0.8rem;
    opacity: 0.7;
    margin: 0.25rem 0 0 0;
    font-family: 'JetBrains Mono', monospace;
  }
  .bubble-actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 0.35rem;
  }
  .copy-btn {
    background: transparent;
    border: none;
    font-size: 0.72rem;
    color: var(--text-dim);
    cursor: pointer;
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    transition: color 0.15s, background 0.15s;
  }
  .copy-btn:hover {
    color: var(--text-sub);
    background: var(--bg-surface2);
  }

  .streaming-indicator {
    display: flex;
    gap: 0.3rem;
    align-self: flex-start;
    padding: 0.75rem 1rem;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-dim);
    animation: bounce 1.4s infinite ease-in-out;
  }
  .dot:nth-child(2) { animation-delay: 0.16s; }
  .dot:nth-child(3) { animation-delay: 0.32s; }
  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
    40% { transform: scale(1); opacity: 1; }
  }

  .input-area {
    display: flex;
    gap: 0.5rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
    margin-top: 0.5rem;
  }
  .input-area textarea { flex: 1; resize: none; }
</style>
