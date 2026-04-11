<!-- Placeholder shared component -->
<script lang="ts">
  let { label = 'Card', children }: { label?: string; children: import('svelte').Snippet } = $props();
</script>

<article class="card">
  <h3>{label}</h3>
  {@render children()}
</article>
