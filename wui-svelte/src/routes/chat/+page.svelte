<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { post as apiPost } from '$lib/api/client';

  // ----------------------------------------------------------------
  // Types
  // ----------------------------------------------------------------

  interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
    timestamp: string;
    reasoning?: string;
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  let messages: ChatMessage[] = $state([]);
  let inputText = $state('');
  let system: 'CHAT' | 'REASONING' = $state('CHAT');
  let showCot = $state(false);
  let streaming = $state(false);
  let tokensUsed = $state(0);
  let tokensMax = $state(8192);

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

  // ----------------------------------------------------------------
  // Send message
  // ----------------------------------------------------------------

  async function send(): Promise<void> {
    const text = inputText.trim();
    if (!text || streaming) return;

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
        body: JSON.stringify({ message: text, system }),
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

  onMount(() => {
    scrollToBottom();
  });
</script>

<div class="chat-layout">
  <!-- Header bar -->
  <div class="chat-header">
    <h2>Chat</h2>
    <div class="header-controls">
      <label class="sys-select">
        System
        <select bind:value={system}>
          <option value="CHAT">CHAT</option>
          <option value="REASONING">REASONING</option>
        </select>
      </label>
      <label class="cot-toggle">
        <input type="checkbox" bind:checked={showCot} />
        Chain of Thought
      </label>
    </div>
  </div>

  <!-- Context budget -->
  <div class="budget-bar">
    <div
      class="budget-fill"
      style="width:{Math.min(tokensUsed / tokensMax * 100, 100)}%"
    ></div>
    <span class="budget-label">
      {tokensUsed} / {tokensMax} tokens
    </span>
  </div>

  <!-- Message list -->
  <div
    class="messages"
    bind:this={chatContainer}
    onscroll={onScroll}
  >
    {#each messages as msg}
      <div class="msg {msg.role}">
        <div class="bubble">
          <p class="content">{msg.content}</p>
          {#if showCot && msg.reasoning}
            <details class="reasoning">
              <summary>Reasoning trace</summary>
              <pre>{msg.reasoning}</pre>
            </details>
          {/if}
          <time class="ts">{msg.timestamp}</time>
        </div>
      </div>
    {/each}
    {#if streaming}
      <div class="streaming-indicator">●●●</div>
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
    height: 100%;
    max-height: calc(100vh - 4rem);
  }
  .chat-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding-bottom: 0.5rem;
  }
  .chat-header h2 { margin: 0; }
  .header-controls {
    display: flex;
    gap: 1rem;
    align-items: center;
  }
  .sys-select select { margin-left: 0.3rem; }
  .cot-toggle {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.85rem;
  }

  .budget-bar {
    position: relative;
    height: 14px;
    background: #333;
    border-radius: 7px;
    overflow: hidden;
    margin-bottom: 0.5rem;
  }
  .budget-fill {
    height: 100%;
    background: #3b82f6;
    transition: width 0.3s ease;
  }
  .budget-label {
    position: absolute;
    top: 0;
    left: 0.5rem;
    font-size: 0.7rem;
    line-height: 14px;
    color: #fff;
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 0.5rem 0;
  }
  .msg {
    display: flex;
    margin-bottom: 0.5rem;
  }
  .msg.user { justify-content: flex-end; }
  .msg.assistant { justify-content: flex-start; }
  .bubble {
    max-width: 75%;
    padding: 0.5rem 0.75rem;
    border-radius: 8px;
    word-break: break-word;
  }
  .msg.user .bubble { background: #2563eb; color: #fff; }
  .msg.assistant .bubble { background: #374151; color: #e5e7eb; }
  .content { margin: 0; white-space: pre-wrap; }
  .ts {
    display: block;
    font-size: 0.65rem;
    opacity: 0.5;
    margin-top: 0.25rem;
  }
  .reasoning {
    margin-top: 0.4rem;
    font-size: 0.8rem;
  }
  .reasoning pre {
    white-space: pre-wrap;
    opacity: 0.7;
    margin: 0.25rem 0 0 0;
  }
  .streaming-indicator {
    text-align: center;
    opacity: 0.5;
    animation: pulse 1s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 0.8; }
  }

  .input-area {
    display: flex;
    gap: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px solid #444;
  }
  .input-area textarea {
    flex: 1;
    resize: none;
    padding: 0.5rem;
  }
  .input-area button { padding: 0.5rem 1.5rem; }
</style>
