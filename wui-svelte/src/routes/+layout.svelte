<script lang="ts">
  import type { Snippet } from 'svelte';

  let { children }: { children: Snippet } = $props();
</script>

<div class="app-shell">
  <nav class="side-nav">
    <div class="brand-mark">OB</div>
    <h1>OpenBaD</h1>
    <ul>
      <li><a href="/">Health</a></li>
      <li><a href="/chat">Chat</a></li>
      <li><a href="/providers">Providers</a></li>
      <li><a href="/senses">Senses</a></li>
      <li><a href="/toolbelt">Toolbelt</a></li>
      <li><a href="/entity">Entity</a></li>
    </ul>
  </nav>
  <main class="workspace">
    {@render children()}
  </main>
</div>
