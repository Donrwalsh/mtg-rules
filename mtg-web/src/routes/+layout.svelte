<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { isAdmin, refreshAdmin } from '$lib/admin';
  import { logout } from '$lib/api';

  onMount(refreshAdmin);

  async function onLogout() {
    await logout();
    await refreshAdmin();
    goto('/');
  }
</script>

{#if $isAdmin}
  <nav class="admin-nav">
    <a href="/">Search</a>
    <a href="/history">History</a>
    <a href="/admin/usage">Usage</a>
    <button type="button" on:click={onLogout}>Log out</button>
  </nav>
{/if}

<slot />

<style>
  .admin-nav {
    display: flex;
    gap: 1rem;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid #ddd;
    font-size: 0.9rem;
  }
</style>
