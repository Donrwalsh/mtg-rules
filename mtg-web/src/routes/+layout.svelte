<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';
  import { refreshAdmin } from '$lib/admin.svelte';
  import CardZoom from '$lib/desk/CardZoom.svelte';

  let { children }: { children: Snippet } = $props();

  onMount(refreshAdmin);
</script>

{@render children()}
<CardZoom />
