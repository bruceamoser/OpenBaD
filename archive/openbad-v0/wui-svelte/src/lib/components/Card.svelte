<script lang="ts">
  let { label = 'Card', children }: { label?: string; children: import('svelte').Snippet } = $props();
</script>

<article class="card">
  <header class="card-header">
    <h3>{label}</h3>
  </header>
  <div class="card-body">
    {@render children()}
  </div>
</article>

<style>
  .card {
    background: var(--bg-surface0);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    overflow: hidden;
    transition: box-shadow 0.2s var(--ease), border-color 0.2s var(--ease);
  }
  .card:hover {
    border-color: var(--bg-surface2);
    box-shadow: 0 4px 16px var(--shadow);
  }
  .card-header {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
    background: var(--bg-mantle);
  }
  .card-header h3 {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-sub);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin: 0;
  }
  .card-body {
    padding: 1rem;
  }
</style>
