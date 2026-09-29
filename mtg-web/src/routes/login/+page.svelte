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

<AppHeader />

<main class="mx-auto flex max-w-sm flex-col gap-4 px-4 py-16">
  <h1 class="m-0 text-2xl font-medium">Log in</h1>
  <form class="flex flex-col gap-3" onsubmit={onSubmit}>
    <label for="pw" class="text-sm text-fg-muted">Admin password</label>
    <input
      id="pw"
      type="password"
      class="min-h-11 rounded-[10px] border border-line-strong bg-field px-4 text-base text-fg outline-none focus:border-gold"
      bind:value={password}
      autocomplete="current-password"
      aria-describedby={error ? 'login-error' : undefined}
    />
    <button
      type="submit"
      class="min-h-11 cursor-pointer rounded-[7px] border-0 bg-gold text-sm font-semibold text-gold-ink disabled:bg-gold-off disabled:text-gold-off-fg"
      disabled={busy}>Log in</button
    >
  </form>
  {#if error}
    <p id="login-error" role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}
</main>
