<script lang="ts">
  import type { Snippet } from 'svelte';
  import { tick } from 'svelte';
  import { afterNavigate, goto } from '$app/navigation';
  import { page } from '$app/state';
  import { admin, refreshAdmin } from '$lib/admin.svelte';
  import { logout } from '$lib/api';

  // `center` sits between the wordmark and the nav (the search form, on /).
  let { center }: { center?: Snippet } = $props();

  let menuOpen = $state(false);
  let menuButton = $state<HTMLButtonElement>();

  const path = $derived(page.url.pathname);
  const isCurrent = (href: string) =>
    href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);

  const links = $derived([
    { href: '/rules', label: 'Rules' },
    ...(admin.isAdmin
      ? [
          { href: '/history', label: 'History' },
          { href: '/admin/usage', label: 'Usage' }
        ]
      : [])
  ]);

  afterNavigate(() => {
    menuOpen = false;
  });

  async function closeMenu() {
    menuOpen = false;
    await tick();
    menuButton?.focus();
  }

  function onWindowKey(event: KeyboardEvent) {
    if (menuOpen && event.key === 'Escape') closeMenu();
  }

  async function onLogout() {
    menuOpen = false;
    await logout();
    await refreshAdmin();
    goto('/');
  }

  const menuRow =
    'flex min-h-[52px] items-center rounded-[10px] px-4 text-[17px] font-medium text-fg no-underline hover:text-fg';
</script>

<svelte:window onkeydown={onWindowKey} />

<header
  class="relative z-30 flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-page px-4 py-2.5 sm:px-8 sm:py-[18px]"
>
  <a href="/" class="font-mono text-sm font-medium whitespace-nowrap text-gold no-underline">
    mtg/rules
  </a>
  {#if center}
    <div class="order-last w-full desk:order-none desk:w-auto desk:flex-1">
      {@render center()}
    </div>
  {/if}

  <!-- Tablet and desktop: inline nav. -->
  <nav class="ml-auto flex items-center gap-5 text-sm max-sm:hidden" aria-label="Main">
    {#each links as link (link.href)}
      <a
        href={link.href}
        aria-current={isCurrent(link.href) ? 'page' : undefined}
        class={[
          'no-underline',
          isCurrent(link.href) && 'border-b-2 border-gold pb-1 text-fg hover:text-fg'
        ]}>{link.label}</a
      >
    {/each}
    {#if admin.isAdmin}
      <button
        type="button"
        class="cursor-pointer border-0 bg-transparent p-0 text-sm text-gold hover:text-gold-hover"
        onclick={onLogout}>Log out</button
      >
    {/if}
  </nav>

  <!-- Phone: menu button and dropdown panel. -->
  <button
    bind:this={menuButton}
    type="button"
    aria-label={menuOpen ? 'Close menu' : 'Menu'}
    aria-expanded={menuOpen}
    aria-controls="site-menu"
    onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
    class="ml-auto flex size-11 cursor-pointer items-center justify-center border-0 bg-transparent text-fg sm:hidden"
  >
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      aria-hidden="true"
    >
      {#if menuOpen}<path d="M6 6l12 12M18 6L6 18" />{:else}<path d="M4 7h16M4 12h16M4 17h16" />{/if}
    </svg>
  </button>

  {#if menuOpen}
    <nav
      id="site-menu"
      aria-label="Main"
      class="absolute top-full right-0 left-0 z-30 flex flex-col gap-0.5 rounded-b-2xl border-b border-line-strong bg-panel px-2.5 pt-2.5 pb-4 sm:hidden"
    >
      <a
        href="/"
        aria-current={isCurrent('/') ? 'page' : undefined}
        class={[menuRow, isCurrent('/') && 'bg-chip']}>Search</a
      >
      <a
        href="/rules"
        aria-current={isCurrent('/rules') ? 'page' : undefined}
        class={[menuRow, isCurrent('/rules') && 'bg-chip']}>Rules</a
      >
      {#if admin.isAdmin}
        <div class="mx-4 my-2 border-t border-line"></div>
        <div class="px-4 pt-1 pb-1.5 font-mono text-[11px] tracking-[0.08em] text-fg-muted">
          ADMIN
        </div>
        <a
          href="/history"
          aria-current={isCurrent('/history') ? 'page' : undefined}
          class={[menuRow, isCurrent('/history') && 'bg-chip']}>History</a
        >
        <a
          href="/admin/usage"
          aria-current={isCurrent('/admin/usage') ? 'page' : undefined}
          class={[menuRow, isCurrent('/admin/usage') && 'bg-chip']}>Usage</a
        >
        <button
          type="button"
          onclick={onLogout}
          class="mt-1 flex min-h-[52px] cursor-pointer items-center rounded-[10px] border-0 bg-transparent px-4 text-left font-sans text-[17px] font-medium text-gold"
          >Log out</button
        >
      {/if}
    </nav>
  {/if}
</header>

{#if menuOpen}
  <!-- Tapping outside the panel closes it; keyboard users have Escape. -->
  <button
    type="button"
    tabindex="-1"
    aria-label="Close menu"
    onclick={closeMenu}
    class="fixed inset-0 z-20 cursor-default border-0 bg-scrim sm:hidden"
  ></button>
{/if}
