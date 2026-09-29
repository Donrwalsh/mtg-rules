<script lang="ts">
  import type { Snippet } from 'svelte';
  import { goto } from '$app/navigation';
  import { admin, refreshAdmin } from '$lib/admin.svelte';
  import { logout } from '$lib/api';

  // `center` sits between the wordmark and the nav (the search form, on /).
  let { center }: { center?: Snippet } = $props();

  async function onLogout() {
    await logout();
    await refreshAdmin();
    goto('/');
  }
</script>

<header
  class="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-page px-4 py-3 sm:px-8 sm:py-[18px]"
>
  <a href="/" class="font-mono text-sm font-medium whitespace-nowrap text-gold no-underline">
    mtg/rules
  </a>
  {#if center}
    <div class="order-last w-full desk:order-none desk:w-auto desk:flex-1">
      {@render center()}
    </div>
  {/if}
  <nav class="ml-auto flex items-center gap-5 text-sm" aria-label="Main">
    <!-- /rules arrives with the secondary-pages PR; until then, the first rule. -->
    <a href="/rules/100">Rules</a>
    {#if admin.isAdmin}
      <a href="/history">History</a>
      <a href="/admin/usage">Usage</a>
      <button
        type="button"
        class="cursor-pointer border-0 bg-transparent p-0 text-sm text-gold hover:text-gold-hover"
        onclick={onLogout}>Log out</button
      >
    {/if}
  </nav>
</header>
