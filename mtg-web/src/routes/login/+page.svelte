<script lang="ts">
  import { goto } from '$app/navigation';
  import AppHeader from '$lib/AppHeader.svelte';
  import { login, RateLimitedError } from '$lib/api';
  import { refreshAdmin } from '$lib/admin.svelte';

  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      if (await login(password)) {
        await refreshAdmin();
        goto('/');
      } else {
        error = 'Incorrect password.';
      }
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head>
  <title>Admin log in — MTG Rules</title>
</svelte:head>

<AppHeader />

<main class="flex justify-center px-4 py-12 sm:py-24">
  <div
    class="flex w-full max-w-[400px] flex-col gap-[18px] rounded-xl border border-line bg-card px-5 py-6 sm:p-8"
  >
    <div class="flex flex-col gap-1.5">
      <h1 class="m-0 text-[22px] font-semibold">Admin log in</h1>
      <p class="m-0 text-sm leading-normal text-fg-soft">
        Query history and usage are for the site's admin.
      </p>
    </div>
    <form class="flex flex-col gap-2.5" onsubmit={onSubmit}>
      <label for="pw" class="text-sm text-fg-body">Password</label>
      <input
        id="pw"
        type="password"
        bind:value={password}
        autocomplete="current-password"
        aria-invalid={error ? 'true' : undefined}
        aria-describedby={error ? 'login-error' : undefined}
        class={[
          'min-h-[46px] rounded-[10px] border bg-field px-3.5 font-sans text-base text-fg outline-none focus:border-gold',
          error ? 'border-danger-line' : 'border-line-strong'
        ]}
      />
      {#if error}
        <p id="login-error" role="alert" class="m-0 text-sm text-danger">{error}</p>
      {/if}
      <button
        type="submit"
        disabled={busy}
        class="mt-1.5 min-h-[46px] cursor-pointer rounded-lg border-0 bg-gold font-sans text-[15px] font-semibold text-gold-ink disabled:cursor-default disabled:bg-gold-off disabled:text-gold-off-fg"
        >Log in</button
      >
    </form>
  </div>
</main>
